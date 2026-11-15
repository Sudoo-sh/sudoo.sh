"""The gateway alone, two versions side by side: each round measures one and then the other (in alternating order),
so a busy machine slows both alike instead of whichever ran later.

    uv run python perf/gateway_ab.py <run folder> <src folder A> <src folder B> [--rounds 3] [--seconds 8]

<run folder> is one seeded by perf/seed.py (its first instance's ODOO_PORT points at the stand-in Odoo). Both
gateways serve that instance, on ports 19290 (A) and 19291 (B), in processes of their own; k6/gateway.js runs
against each. Prints the median of the rounds for every scenario, the stand-in Odoo measured straight as a control,
and each gateway process's peak memory; writes them to <run folder>/gateway_ab.json.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SERVE = """
import asyncio, sys
from aiohttp import web
from sudoo.instances import gateway
async def main() -> None:
    await gateway._create_session()
    app = web.Application(**({} if hasattr(gateway, "_READ_BUFFER") else {"client_max_size": 2 * 1024**3}))
    app[getattr(gateway, "INSTANCE", "instance")] = sys.argv[1]
    app.router.add_route("*", "/{tail:.*}", gateway._handle)
    extra = {"read_bufsize": gateway._READ_BUFFER} if hasattr(gateway, "_READ_BUFFER") else {}
    runner = web.AppRunner(app, access_log=None, **extra)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", int(sys.argv[2])).start()
    print("ready", flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""


def env_of(run: Path, source: Path) -> dict[str, str]:
    values = dict(os.environ, PYTHONPATH=str(source))
    for line in (run / "panel.env").read_text().splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def peak_rss(pid: int, stop: threading.Event, peaks: dict[int, int]) -> None:
    while not stop.is_set():
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    peaks[pid] = max(peaks.get(pid, 0), int(line.split()[1]))
        except OSError:
            return
        time.sleep(0.2)


def k6(run: Path, base: str, out: str, seconds: int) -> dict[str, Any]:
    subprocess.run(
        ["docker", "run", "--rm", "--name", "pperf-k6", "--network", "host", "--user", str(os.getuid()),
         "-v", f"{HERE / 'k6'}:/scripts:ro", "-v", f"{run}:/out", "-e", f"SECONDS={seconds}",
         "grafana/k6:2.4.0", "run", "--quiet", "--no-usage-report", "-e", f"BASE={base}", "-e", f"OUT=/out/{out}",
         "/scripts/gateway.js"],
        check=True, capture_output=True,
    )  # fmt: skip
    result: dict[str, Any] = json.loads((run / out).read_text())
    return result


def median_of(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for name in rounds[0]:
        keys = [k for k in rounds[0][name] if isinstance(rounds[0][name][k], int | float)]
        merged[name] = {k: round(statistics.median(r[name][k] for r in rounds), 2) for k in keys}
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--seconds", type=int, default=8)
    args = parser.parse_args()
    run = args.run.resolve()
    instance = json.loads((run / "secrets.json").read_text())["gateway_instance"]
    stub = subprocess.Popen([sys.executable, str(HERE / "stub_odoo.py"), "--port", "19300"], stdout=subprocess.DEVNULL)
    gateways = {
        label: subprocess.Popen(
            [sys.executable, "-c", SERVE, instance, str(port)],
            env=env_of(run, source.resolve()), stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        for label, source, port in (("a", args.a, 19290), ("b", args.b, 19291))
    }
    stop, peaks = threading.Event(), {}
    try:
        for process in gateways.values():
            assert process.stdout is not None and process.stdout.readline().strip() == "ready"
            threading.Thread(target=peak_rss, args=(process.pid, stop, peaks), daemon=True).start()
        results: dict[str, list[dict[str, Any]]] = {"a": [], "b": [], "direct": []}
        for number in range(args.rounds):
            order = ("a", "b") if number % 2 == 0 else ("b", "a")
            for label in order:
                port = 19290 if label == "a" else 19291
                results[label].append(k6(run, f"http://127.0.0.1:{port}", f"gateway_{label}.json", args.seconds))
            results["direct"].append(k6(run, "http://127.0.0.1:19300", "gateway_direct.json", args.seconds))
        summary = {label: median_of(rounds) for label, rounds in results.items()}
        summary["peak_rss_mb"] = {
            label: round(peaks.get(process.pid, 0) / 1024, 1) for label, process in gateways.items()
        }
    finally:
        stop.set()
        for process in (*gateways.values(), stub):
            process.terminate()
    text = json.dumps(summary, indent=1)
    (run / "gateway_ab.json").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
