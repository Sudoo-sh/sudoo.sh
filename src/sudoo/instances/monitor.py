"""Resource usage and status of the server and of every instance, collected every METRICS_INTERVAL_SECONDS.

Container figures come from one long-running `docker stats` stream (Docker sends new figures about every second),
so a measurement only reads the latest ones instead of starting Docker's slow one-off sampling each time. The git
details of an instance (commit, repository) are read at most once a minute, and right after a job on it ends.

Also keeps each Odoo's public address (web.base.url) in step with its connector, whose address changes when the
connector restarts, and has integrity.py compare the instances with the panel's configuration.

After every measurement it publishes live events (core/events.py): "metrics" with the new numbers, and "instance"
for each instance whose status, code or address changed since the last one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

from sudoo import engines, exposure
from sudoo.config import settings
from sudoo.core import events, jobs
from sudoo.core.shell import capture, succeeds
from sudoo.core.state import STATE
from sudoo.instances import capacity, disk_use, integrity, metrics, odoo, store
from sudoo.repos.gitrepo import git, origin_url

STATUS: dict[str, dict[str, Any]] = {}  # instance name -> latest measurements
HOST: dict[str, Any] = {"history": deque(maxlen=settings.history_samples)}
HISTORY: defaultdict[str, deque[tuple[int, float, int]]] = defaultdict(
    lambda: deque(maxlen=settings.history_samples)
)  # name -> [(time, cpu %, memory bytes)]
_KNOWN_ADDRESSES: dict[str, str] = {}
_LAST_SEEN: dict[str, tuple[Any, ...]] = {}  # name -> what clients last heard about it
_STARTED: dict[str, float] = {}  # container id -> Unix time it started
_GIT: dict[str, tuple[float, str, str]] = {}  # instance -> (read at, commit line, repository)
_WAS_BUSY: set[str] = set()
_GIT_SECONDS = 60


_JSON = json.JSONDecoder()


def _stats_line(line: str) -> tuple[str, tuple[float, int, int]] | None:
    """One line of `docker stats --format '{{json .}}'`: the container's name and figures, or None.

    The lines carry terminal control codes around the JSON (`ESC[H` before it, ` ESC[K` after it), so only the
    object itself is read."""
    start = line.find("{")
    if start == -1:
        return None
    try:
        entry, _ = _JSON.raw_decode(line, start)
        used, _, limit = entry["MemUsage"].partition("/")
        return entry["Name"], (float(entry["CPUPerc"].rstrip("%") or 0), _bytes(used), _bytes(limit))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


class _DockerStats:
    """The latest figures of every running container, from a `docker stats` stream that runs in the background."""

    def __init__(self) -> None:
        self.latest: dict[str, tuple[float, int, int]] = {}  # container -> (cpu % of one core, memory, limit)
        self.updated = 0.0
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            threading.Thread(target=self._run_forever, daemon=True, name="docker-stats").start()

    def _run_forever(self) -> None:
        while True:
            try:
                with subprocess.Popen(
                    ["docker", "stats", "--format", "{{json .}}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ) as proc:
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        parsed = _stats_line(line)
                        if parsed:
                            with self._lock:
                                self.latest[parsed[0]] = parsed[1]
                                self.updated = time.time()
            except OSError as exc:
                print("docker stats stream:", repr(exc), flush=True)
            time.sleep(5)  # Docker restarted or is not reachable yet

    def figures(self) -> dict[str, tuple[float, int, int]] | None:
        """The latest figures, or None when the stream has not delivered for a while."""
        with self._lock:
            return dict(self.latest) if time.time() - self.updated < 15 else None


STATS = _DockerStats()


def _container_figures() -> dict[str, tuple[float, int, int]]:
    STATS.start()
    figures = STATS.figures()
    if figures is not None:
        return figures
    usage: dict[str, tuple[float, int, int]] = {}  # until the stream delivers: one sampling
    for line in capture(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=60).splitlines():
        entry = json.loads(line)
        used, _, limit = entry["MemUsage"].partition("/")
        usage[entry["Name"]] = (float(entry["CPUPerc"].rstrip("%") or 0), _bytes(used), _bytes(limit))
    return usage


def _git_details(name: str, refresh: bool) -> tuple[str, str]:
    """(last commit "sha|subject|age|author", repository) of an instance, read at most once a minute."""
    cached = _GIT.get(name)
    if cached and not refresh and time.time() - cached[0] < _GIT_SECONDS:
        return cached[1], cached[2]
    addons = store.addons_dir(name)
    found = (
        capture(git(addons, "log", "-1", "--format=%h|%s|%cr|%an")),
        origin_url(addons),
    )
    _GIT[name] = (time.time(), *found)
    return found


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


# The server's own /proc files, mounted read-only by compose.yaml at /host/proc. Inside a container, /proc/meminfo,
# /proc/stat and /proc/uptime describe the kernel's whole machine: on a server that is itself a container (LXC,
# OpenVZ, some VPS offers) that is the provider's host (16 GB instead of the server's 3 GB, all its processors,
# its uptime). Files bound from the server's side are the server's view (with LXCFS, the server's own figures);
# on an ordinary server both are the same. Without the mounts (an older compose.yaml, development) /proc is read.
HOST_PROC = Path("/host/proc")


def _proc(name: str) -> Path:
    mounted = HOST_PROC / name
    return mounted if mounted.is_file() else Path("/proc") / name


def _host_cpu_percent() -> float:
    global _previous_cpu
    with open(_proc("stat")) as fh:
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
    with open(_proc("meminfo")) as fh:
        for line in fh:
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0]) * 1024
    return info["MemTotal"], info["MemTotal"] - info["MemAvailable"]


PANEL_CONTAINERS = {  # compose.yaml's
    "backend": "sudoo-panel",
    "web": "sudoo-web",
    "gateway": "sudoo-gateway",
    "mail": "sudoo-mail",
}


def _own_memory() -> int:
    """Resident memory of this process (the backend), for a panel not running in its container (development)."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def panel_footprint(usage: dict[str, tuple[float, int, int]], cores: int) -> dict[str, dict[str, Any]]:
    """What the panel itself uses, by part: its backend, web service and gateway containers, and the Cloudflare
    connectors (one for the panel and one per instance; they run whether the instances run or not). Each part:
    {"cpu_percent" (of the whole server), "memory_bytes", "containers"}."""
    parts: dict[str, dict[str, Any]] = {}
    for part, container in PANEL_CONTAINERS.items():
        if container in usage:
            cpu, memory, _ = usage[container]
            parts[part] = {"cpu_percent": round(cpu / cores, 2), "memory_bytes": memory, "containers": 1}
    if "backend" not in parts:  # not in its container: this process is the backend
        parts["backend"] = {"cpu_percent": 0.0, "memory_bytes": _own_memory(), "containers": 0}
    connectors = [figures for name, figures in usage.items() if name.startswith(settings.connector_prefix)]
    if connectors:
        parts["connectors"] = {
            "cpu_percent": round(sum(c[0] for c in connectors) / cores, 2),
            "memory_bytes": sum(c[1] for c in connectors),
            "containers": len(connectors),
        }
    return parts


def collect() -> None:
    """Measure the server and every instance once."""
    containers: dict[str, dict[str, Any]] = {}
    for line in capture(["docker", "ps", "-a", "--format", "{{json .}}"]).splitlines():
        entry = json.loads(line)
        containers[entry["Names"]] = entry
    usage = _container_figures()  # container -> (cpu % of one core, memory, memory limit)

    cores = os.cpu_count() or 1  # None only when the count cannot be determined
    memory_total, memory_used = _host_memory()
    now = time.time()
    running_now = {entry["ID"]: c for c, entry in containers.items() if entry["State"] == "running"}
    unknown = [c for cid, c in running_now.items() if cid not in _STARTED]
    for container, when in _started_at(unknown).items():
        _STARTED[containers[container]["ID"]] = when
    started = {c: _STARTED[cid] for cid, c in running_now.items() if cid in _STARTED}
    measured_instances: dict[str, metrics.Usage] = {}
    names = store.names()
    observed = _observed_elsewhere(names)  # instances of other engines than Docker (Kubernetes)
    for name in names:
        env = store.read_env(name)
        odoo_container = containers.get(store.container(name, "odoo"))
        db_container = containers.get(store.container(name, "db"))
        running = bool(odoo_container and odoo_container["State"] == "running")
        own = (store.container(name, "odoo"), store.container(name, "db"))
        cpu = sum(usage.get(c, (0, 0, 0))[0] for c in own) / cores
        memory = sum(usage.get(c, (0, 0, 0))[1] for c in own)
        limit = min(sum(usage.get(c, (0, 0, 0))[2] for c in own) or memory_total, memory_total)
        db_running = bool(db_container and db_container["State"] == "running")
        uptime = odoo_container["Status"] if odoo_container else ""
        odoo_started = started.get(store.container(name, "odoo"))
        other = observed.get(name)
        if engines.kind_of(env) != engines.DOCKER:
            running, db_running = bool(other and other.running), bool(other and other.db_running)
            cpu = (other.cpu if other else 0.0) / cores
            memory = other.memory if other else 0
            limit = min((other.limit if other else 0) or memory_total, memory_total)
            uptime, odoo_started = (other.uptime, other.started) if other else ("", None)
        busy = jobs.busy(name)
        commit, repo = _git_details(name, refresh=name in _WAS_BUSY and not busy)  # re-read after a job
        _WAS_BUSY.discard(name)
        if busy:
            _WAS_BUSY.add(name)
        STATUS[name] = {
            "running": running,
            "db_running": db_running,
            "healthy": running and odoo.answers(env),
            "uptime": uptime,
            "cpu": round(cpu, 2),
            "memory": memory,
            "commit": commit,
            "repo": repo,
        }
        HISTORY[name].append((int(now), round(cpu, 2), memory))
        stored = disk_usage(name)  # measured every STORAGE_MEASURE_MINUTES while it runs, kept while it is paused
        disk_bytes = (stored.get("db") or 0) + (stored.get("files") or 0) or None
        measured_instances[name] = metrics.Usage(running, cpu, memory, limit, odoo_started, disk_bytes)

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
    panel = panel_footprint(usage, cores)
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
            "panel_cpu_percent": round(sum(p["cpu_percent"] for p in panel.values()), 2),
            "panel_memory_bytes": sum(p["memory_bytes"] for p in panel.values()),
        },
        measured_instances,
        panel,
    )


def _observed_elsewhere(names: list[str]) -> dict[str, engines.Observation]:
    """Figures of the instances whose engine measures them itself (engines/base.py observe); a cluster that does not
    answer leaves its instances unmeasured this time (shown stopped), and is logged."""
    by_kind: dict[str, list[str]] = {}
    for name in names:
        kind = engines.kind_of(store.read_env(name))
        if kind != engines.DOCKER:
            by_kind.setdefault(kind, []).append(name)
    found: dict[str, engines.Observation] = {}
    for kind, members in by_kind.items():
        try:
            found.update(engines.named(kind).observe(members))
        except Exception as exc:  # noqa: BLE001 - the monitor keeps measuring the others
            print(f"monitor: {kind} instances not measured: {exc!r}", flush=True)
    return found


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
    """Seconds since the server started: from the start of its first process (/host/proc/1/stat, the server's
    init, in clock ticks after the kernel's boot) when it is mounted, so a server that is a container counts from
    its own start, not from its host's boot; else /proc/uptime."""
    try:
        with open(HOST_PROC / "1" / "stat") as fh:
            started_ticks = int(fh.read().rsplit(")", 1)[1].split()[19])
        with open(_proc("stat")) as fh:
            boot = next(int(line.split()[1]) for line in fh if line.startswith("btime "))
        return max(0, int(time.time() - (boot + started_ticks / os.sysconf("SC_CLK_TCK"))))
    except (OSError, ValueError, IndexError, StopIteration):
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
    events.publish("metrics", "", capacity.snapshot())
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
            integrity.check_all()  # every INTEGRITY_CHECK_INTERVAL seconds: changes made outside the panel
        except Exception as exc:  # noqa: BLE001 - the monitor must keep running; the error is logged
            print("monitor error:", repr(exc), flush=True)
        time.sleep(settings.metrics_interval_seconds)


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
    """{"db": bytes, "files": bytes, "at"} of an instance: the last measurement (disk_use.py), measured again when
    it is due (STORAGE_MEASURE_MINUTES) and its containers run."""
    measured = STATUS.get(name, {})
    return disk_use.current(name, bool(measured.get("db_running")), bool(measured.get("running")))
