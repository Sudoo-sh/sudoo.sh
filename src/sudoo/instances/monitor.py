"""Resource usage and status of the server and of every instance, collected every 15 seconds.

Also keeps each Odoo's public address (web.base.url) in step with its connector, whose address changes when the
connector restarts.

After every measurement it publishes live events (core/events.py): "metrics" with the new numbers, and "instance"
for each instance whose status, code or address changed since the last one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Any

from sudoo import exposure
from sudoo.config import settings
from sudoo.core import events, jobs
from sudoo.core.shell import capture, succeeds
from sudoo.core.state import STATE
from sudoo.instances import metrics, odoo, store
from sudoo.repos.gitrepo import git

STATUS: dict[str, dict[str, Any]] = {}  # instance name -> latest measurements
HOST: dict[str, Any] = {"history": deque(maxlen=settings.history_samples)}
HISTORY: defaultdict[str, deque[tuple[int, float, int]]] = defaultdict(
    lambda: deque(maxlen=settings.history_samples)
)  # name -> [(time, cpu %, memory bytes)]
_DISK_CACHE: dict[str, dict[str, Any]] = {}
_KNOWN_ADDRESSES: dict[str, str] = {}
_LAST_SEEN: dict[str, tuple[Any, ...]] = {}  # name -> what clients last heard about it
_previous_cpu: tuple[int, int] | None = None

_UNITS = {
    "b": 1,
    "kb": 1e3,
    "mb": 1e6,
    "gb": 1e9,
    "tb": 1e12,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


def _bytes(text: str | None) -> int:
    match = re.match(r"\s*([\d.]+)\s*([a-zA-Z]+)", text or "")
    return int(float(match.group(1)) * _UNITS.get(match.group(2).lower(), 1)) if match else 0


def _host_cpu_percent() -> float:
    global _previous_cpu
    with open("/proc/stat") as fh:
        values = [int(v) for v in fh.readline().split()[1:]]
    idle, total = values[3] + values[4], sum(values)
    percent = 0.0
    if _previous_cpu and total > _previous_cpu[0]:
        busy = (total - _previous_cpu[0]) - (idle - _previous_cpu[1])
        percent = 100.0 * busy / (total - _previous_cpu[0])
    _previous_cpu = (total, idle)
    return percent


def _host_memory() -> tuple[int, int]:
    info: dict[str, int] = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0]) * 1024
    return info["MemTotal"], info["MemTotal"] - info["MemAvailable"]


def collect() -> None:
    """Measure the server and every instance once."""
    containers: dict[str, dict[str, Any]] = {}
    for line in capture(["docker", "ps", "-a", "--format", "{{json .}}"]).splitlines():
        entry = json.loads(line)
        containers[entry["Names"]] = entry
    usage: dict[str, tuple[float, int, int]] = {}  # container -> (cpu % of one core, memory, memory limit)
    for line in capture(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=60).splitlines():
        entry = json.loads(line)
        used, _, limit = entry["MemUsage"].partition("/")
        usage[entry["Name"]] = (float(entry["CPUPerc"].rstrip("%") or 0), _bytes(used), _bytes(limit))

    cores = os.cpu_count() or 1  # None only when the count cannot be determined
    memory_total, memory_used = _host_memory()
    now = time.time()
    started = _started_at([c for c, entry in containers.items() if entry["State"] == "running"])
    measured_instances: dict[str, metrics.Usage] = {}
    for name in store.names():
        env = store.read_env(name)
        odoo_container = containers.get(store.container(name, "odoo"))
        db_container = containers.get(store.container(name, "db"))
        running = bool(odoo_container and odoo_container["State"] == "running")
        own = (store.container(name, "odoo"), store.container(name, "db"))
        cpu = sum(usage.get(c, (0, 0, 0))[0] for c in own) / cores
        memory = sum(usage.get(c, (0, 0, 0))[1] for c in own)
        limit = min(sum(usage.get(c, (0, 0, 0))[2] for c in own) or memory_total, memory_total)
        addons = store.addons_dir(name)
        STATUS[name] = {
            "running": running,
            "db_running": bool(db_container and db_container["State"] == "running"),
            "healthy": running and odoo.responds(env["ODOO_PORT"]),
            "uptime": odoo_container["Status"] if odoo_container else "",
            "cpu": round(cpu, 2),
            "memory": memory,
            "commit": capture(git(addons, "log", "-1", "--format=%h|%s|%cr|%an")),
            "repo": capture(git(addons, "remote", "get-url", "origin")),
        }
        HISTORY[name].append((int(now), round(cpu, 2), memory))
        disk_bytes = None
        if running:
            stored = disk_usage(name)  # measured every 5 minutes at most
            disk_bytes = (stored.get("db") or 0) + (stored.get("files") or 0) or None
        measured_instances[name] = metrics.Usage(
            running, cpu, memory, limit, started.get(store.container(name, "odoo")), disk_bytes
        )

    disk = shutil.disk_usage(settings.odoo_root)
    cpu = _host_cpu_percent()
    HOST.update(
        cpu=round(cpu, 1),
        cores=cores,
        memory_total=memory_total,
        memory_used=memory_used,
        disk_total=disk.total,
        disk_used=disk.used,
    )
    HOST["history"].append((int(now), round(cpu, 1), memory_used))
    metrics.sample(
        {
            "cpu_percent": round(cpu, 1),
            "cores": cores,
            "load_1m": round(os.getloadavg()[0], 2),
            "memory_used_bytes": memory_used,
            "memory_total_bytes": memory_total,
            "memory_percent": round(100 * memory_used / memory_total, 1),
            "disk_used_bytes": disk.used,
            "disk_total_bytes": disk.total,
            "disk_percent": round(100 * disk.used / disk.total, 1),
            "uptime_seconds": _host_uptime(),
            "instances_running": sum(1 for u in measured_instances.values() if u.running),
            "instances_total": len(measured_instances),
        },
        measured_instances,
    )


def _started_at(names: list[str]) -> dict[str, float]:
    """When each running container started, as Unix time."""
    if not names:
        return {}
    found: dict[str, float] = {}
    for line in capture(["docker", "inspect", "-f", "{{.Name}} {{.State.StartedAt}}", *names]).splitlines():
        name, _, when = line.strip().lstrip("/").partition(" ")
        try:
            found[name] = datetime.fromisoformat(when[:26].rstrip("Z") + "+00:00").timestamp()
        except ValueError:
            continue
    return found


def _host_uptime() -> int:
    with open("/proc/uptime") as fh:
        return int(float(fh.read().split()[0]))


def follow_public_addresses() -> None:
    """Write each instance's current public address into its databases when it changed."""
    for name in store.names():
        if not STATUS.get(name, {}).get("db_running") or jobs.busy(name):
            continue
        url = exposure.address(name)
        changed = url and _KNOWN_ADDRESSES.get(name) != url
        if changed and url and all(succeeds(cmd, timeout=60) for cmd in odoo.public_address_cmds(name, url)):
            _KNOWN_ADDRESSES[name] = url


def mark_running(name: str) -> None:
    """Record right away that an instance answers (instead of waiting for the next measurement)."""
    STATUS.setdefault(name, {}).update(running=True, healthy=True)


def mark_stopped(name: str) -> None:
    STATUS.setdefault(name, {})["running"] = False


def publish_changes() -> None:
    """Publish "metrics", and "instance" for every instance that looks different to a client than last time."""
    events.publish("metrics", "", metrics.snapshot())
    for name in store.names():
        state = STATE.get(name)
        seen = (
            status(name),
            STATUS.get(name, {}).get("commit"),
            exposure.address(name),
            state.get("branch_missing"),
            int(state.get("last_visit") or 0) // 60,
        )
        if _LAST_SEEN.get(name) != seen:
            _LAST_SEEN[name] = seen
            events.publish("instance", name)


def run_forever() -> None:
    """Background loop: measure every MONITOR_INTERVAL_SECONDS."""
    while True:
        try:
            collect()
            publish_changes()
            follow_public_addresses()
        except Exception as exc:  # noqa: BLE001 - the monitor must keep running; the error is logged
            print("monitor error:", repr(exc), flush=True)
        time.sleep(settings.monitor_interval_seconds)


def status(name: str) -> str:
    """What the panel shows: busy, unknown, online, starting, switched_off, paused or stopped."""
    measured, state = STATUS.get(name), STATE.get(name)
    if jobs.busy(name):
        return "busy"
    if measured is None:
        return "unknown"
    if measured["running"]:
        return "online" if measured["healthy"] else "starting"
    if state.get("switched_off"):
        return "switched_off"
    if state.get("paused"):
        return "paused"
    return "stopped"


def disk_usage(name: str) -> dict[str, Any]:
    """{"db": bytes, "files": bytes} of a running instance (None when not running). Cached 5 minutes."""
    cached = _DISK_CACHE.get(name)
    if cached and time.time() - cached["at"] < 300:
        return cached
    measured = STATUS.get(name, {})
    result: dict[str, Any] = {"db": None, "files": None, "at": time.time()}
    db = odoo.databases(name)[0]
    if measured.get("db_running"):
        out = capture(
            [
                "docker",
                "exec",
                store.container(name, "db"),
                "psql",
                "-U",
                "odoo",
                "-d",
                "postgres",
                "-tAc",
                f"SELECT pg_database_size('{db}')",
            ]
        )
        result["db"] = int(out) if out.strip().isdigit() else None
    if measured.get("running"):
        out = capture(["docker", "exec", store.container(name, "odoo"), "du", "-sb", "/var/lib/odoo"])
        result["files"] = int(out.split()[0]) if out else None
    _DISK_CACHE[name] = result
    return result
