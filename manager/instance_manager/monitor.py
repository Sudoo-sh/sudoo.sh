"""Resource usage and status of the server and of every instance, collected every 15 seconds.

Also keeps each Odoo's public address (web.base.url) in step with its connector, whose address changes when the
connector restarts.
"""
import json
import os
import re
import shutil
import time
from collections import defaultdict, deque

from . import instances, jobs, odoo, settings, tunnels
from .gitrepo import git
from .shell import capture, succeeds
from .state import STATE

STATUS = {}                 # instance name -> latest measurements
HOST = {"history": deque(maxlen=settings.HISTORY_SAMPLES)}
HISTORY = defaultdict(lambda: deque(maxlen=settings.HISTORY_SAMPLES))   # name -> [(time, cpu %, memory bytes)]
_DISK_CACHE = {}
_KNOWN_ADDRESSES = {}
_previous_cpu = None

_UNITS = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1024 ** 2,
          "gib": 1024 ** 3, "tib": 1024 ** 4}


def _bytes(text):
    match = re.match(r"\s*([\d.]+)\s*([a-zA-Z]+)", text or "")
    return int(float(match.group(1)) * _UNITS.get(match.group(2).lower(), 1)) if match else 0


def _host_cpu_percent():
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


def _host_memory():
    info = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0]) * 1024
    return info["MemTotal"], info["MemTotal"] - info["MemAvailable"]


def collect():
    """Measure the server and every instance once."""
    containers = {}
    for line in capture(["docker", "ps", "-a", "--format", "{{json .}}"]).splitlines():
        entry = json.loads(line)
        containers[entry["Names"]] = entry
    usage = {}
    for line in capture(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=60).splitlines():
        entry = json.loads(line)
        usage[entry["Name"]] = (float(entry["CPUPerc"].rstrip("%") or 0), _bytes(entry["MemUsage"].split("/")[0]))

    cores = os.cpu_count()
    now = time.time()
    for name in instances.names():
        env = instances.read_env(name)
        odoo_container = containers.get(instances.container(name, "odoo"))
        db_container = containers.get(instances.container(name, "db"))
        running = bool(odoo_container and odoo_container["State"] == "running")
        own = (instances.container(name, "odoo"), instances.container(name, "db"))
        cpu = sum(usage.get(c, (0, 0))[0] for c in own) / cores
        memory = sum(usage.get(c, (0, 0))[1] for c in own)
        addons = instances.addons_dir(name)
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

    memory_total, memory_used = _host_memory()
    disk = shutil.disk_usage(settings.ROOT)
    cpu = _host_cpu_percent()
    HOST.update(cpu=round(cpu, 1), cores=cores, memory_total=memory_total, memory_used=memory_used,
                disk_total=disk.total, disk_used=disk.used)
    HOST["history"].append((int(now), round(cpu, 1), memory_used))


def follow_public_addresses():
    """Write each instance's current public address into its databases when it changed."""
    for name in instances.names():
        if not STATUS.get(name, {}).get("db_running") or jobs.busy(name):
            continue
        url = tunnels.address(name)
        if url and _KNOWN_ADDRESSES.get(name) != url:
            if all(succeeds(cmd, timeout=60) for cmd in odoo.public_address_cmds(name, url)):
                _KNOWN_ADDRESSES[name] = url


def mark_running(name):
    """Record right away that an instance answers (instead of waiting for the next measurement)."""
    STATUS.setdefault(name, {}).update(running=True, healthy=True)


def mark_stopped(name):
    STATUS.setdefault(name, {})["running"] = False


def run_forever():
    """Background loop: measure every MONITOR_INTERVAL_SECONDS."""
    while True:
        try:
            collect()
            follow_public_addresses()
        except Exception as exc:  # noqa: BLE001 - the monitor must keep running; the error is logged
            print("monitor error:", repr(exc), flush=True)
        time.sleep(settings.MONITOR_INTERVAL_SECONDS)


def status(name):
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


def disk_usage(name):
    """{"db": bytes, "files": bytes} of a running instance (None when not running). Cached 5 minutes."""
    cached = _DISK_CACHE.get(name)
    if cached and time.time() - cached["at"] < 300:
        return cached
    measured = STATUS.get(name, {})
    result = {"db": None, "files": None, "at": time.time()}
    db = odoo.databases(name)[0]
    if measured.get("db_running"):
        out = capture(["docker", "exec", instances.container(name, "db"), "psql", "-U", "odoo", "-d", "postgres",
                       "-tAc", f"SELECT pg_database_size('{db}')"])
        result["db"] = int(out) if out.strip().isdigit() else None
    if measured.get("running"):
        out = capture(["docker", "exec", instances.container(name, "odoo"), "du", "-sb", "/var/lib/odoo"])
        result["files"] = int(out.split()[0]) if out else None
    _DISK_CACHE[name] = result
    return result
