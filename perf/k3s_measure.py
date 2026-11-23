"""Measure what a single-node k3s costs, idle and with Odoo instances of the Kubernetes engine (local only).

The cluster is one k3s server in a Docker container (or a k3d node), so the container's figures are k3s itself plus
everything it runs. Instances are made through the engine as the panel makes them (namespace, PostgreSQL 16,
`odoo -i base` in a runner Job, Odoo 18), then left idle; each figure is the average of `docker stats` samples taken
every 5 seconds for SAMPLE_SECONDS after SETTLE_SECONDS.

    ODOO_ROOT=/tmp/k3s-measure KUBERNETES_KUBECONFIG=<panel kubeconfig.json> \\
        uv run python perf/k3s_measure.py <container> <out.json> [--settle 120] [--sample 120]

Never point it at a server that runs anything else: it creates and deletes instances k3sm-1..3.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

from sudoo import engines
from sudoo.engines.kubernetes import engine
from sudoo.instances import store

NAMES = ("k3sm-1", "k3sm-2", "k3sm-3")
MIB = 1024 * 1024


class Job:
    def write(self, text: str) -> None:
        print(text, end="", flush=True)

    def step(self, text: str) -> None:
        print(f"step: {text}", flush=True)


def _bytes(text: str) -> float:
    units = {"KiB": 1024, "MiB": MIB, "GiB": 1024 * MIB, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "B": 1}
    for unit, factor in units.items():
        if text.endswith(unit):
            return float(text[: -len(unit)]) * factor
    return float(text)


def sample(container: str, seconds: int) -> dict[str, float]:
    memory, cpu = [], []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        line = subprocess.check_output(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}|{{.CPUPerc}}", container], text=True
        ).strip()
        used, percent = line.split("|")
        memory.append(_bytes(used.split("/")[0].strip()))
        cpu.append(float(percent.rstrip("%")) / 100)
        time.sleep(5)
    return {
        "memory_mib": round(statistics.mean(memory) / MIB),
        "memory_max_mib": round(max(memory) / MIB),
        "cpu_cores": round(statistics.mean(cpu), 2),
        "samples": len(memory),
    }


def create(name: str, index: int) -> None:
    folder = store.folder(name)
    (folder / "config").mkdir(parents=True, exist_ok=True)
    (folder / "addons").mkdir(exist_ok=True)
    db = name.replace("-", "_")
    store.write_env(
        name,
        {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": db, "DB_PASSWORD": f"pw{index}",
         "ODOO_PORT": 29000 + index, "GATEWAY_PORT": 9100 + index, "STAGE": "development", "ENGINE": "kubernetes"},
    )  # fmt: skip
    store.config_file(name).write_text(
        f"[options]\ndb_host = db\ndb_user = odoo\ndb_password = pw{index}\ndbfilter = ^{db}$\nlist_db = False\n"
        "data_dir = /var/lib/odoo\nproxy_mode = True\nhttp_interface = 0.0.0.0\n"
    )
    kube, job = engines.named("kubernetes"), Job()
    kube.apply(job, name)  # type: ignore[arg-type]
    kube.up(job, name, "db", timeout=900)  # type: ignore[arg-type]
    init = subprocess.run(kube.odoo_cmd(name, "-d", db, "-i", "base", "--stop-after-init", "--no-http"), timeout=1800)
    if init.returncode:
        sys.exit(f"odoo -i base failed for {name}")
    kube.up(job, name, "odoo", timeout=900)  # type: ignore[arg-type]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("container")
    parser.add_argument("out")
    parser.add_argument("--settle", type=int, default=120)
    parser.add_argument("--sample", type=int, default=120)
    args = parser.parse_args()
    version = subprocess.check_output(["docker", "exec", args.container, "k3s", "--version"], text=True).split()[2]
    found: dict[str, Any] = {
        "measured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": (
            "Local measurement only: one k3s server node in a Docker container on a development machine "
            f"(k3s {version}, Traefik and servicelb off, metrics-server, CoreDNS and local-path on), measured with "
            f"docker stats every 5 s for {args.sample} s after {args.settle} s of rest. Instances: Odoo 18.0 "
            "Community with only the base module and PostgreSQL 16, idle (no visitors), made by the Kubernetes "
            "engine (one namespace each). Figures are the whole node: k3s, containerd and every pod."
        ),
        "k3s_version": version,
        "results": {},
    }
    try:
        for count, label in ((0, "idle"), (1, "one_instance"), (3, "three_instances")):
            for index, name in enumerate(NAMES[:count]):
                if not store.exists(name):
                    print(f"creating {name}", flush=True)
                    create(name, index)
            time.sleep(args.settle)
            found["results"][label] = {"instances": count, **sample(args.container, args.sample)}
            print(label, found["results"][label], flush=True)
    finally:
        for name in NAMES:
            if store.exists(name):
                engines.named("kubernetes").remove(Job(), name, checked=False)  # type: ignore[arg-type]
        print(engine.KIND, "instances removed", flush=True)
    results = found["results"]
    if "one_instance" in results and "idle" in results:
        found["per_instance_mib"] = round(
            (results.get("three_instances", results["one_instance"])["memory_mib"] - results["idle"]["memory_mib"])
            / (3 if "three_instances" in results else 1)
        )
    with open(args.out, "w") as out:
        json.dump(found, out, indent=2)
        out.write("\n")


if __name__ == "__main__":
    main()
