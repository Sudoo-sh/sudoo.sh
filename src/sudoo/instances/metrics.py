"""Metrics of the server and every instance: current figures and one hour of history.

The monitor (monitor.py) measures every 15 seconds and calls `sample()`; the gateway calls `record_request()` for
every request it forwards to Odoo. From both, each interval gets one sample per instance:

    cpu_percent          share of the whole server's processor (all cores), Odoo and its database together
    memory_percent       memory in use, of the containers' limit (the server's memory when they have none)
    requests_per_minute  requests forwarded to Odoo (page loads, RPC calls; not the open-tab background traffic)
    error_rate_percent   share of those answered with a 5xx error
    response_ms_avg/p95  time until Odoo started answering: average, and the time 95 % of requests stayed under

History is kept in memory as fixed-size rings (240 samples: one hour), so it costs a few hundred kilobytes and no
database; it starts empty when the panel restarts. The API returns it in columns ({"t": [...], "cpu_percent":
[...]}), the shape charts draw from directly, and the live "metrics" event carries each new sample.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from sudoo.config import settings

INSTANCE_SERIES = (
    "cpu_percent",
    "memory_percent",
    "memory_bytes",
    "requests_per_minute",
    "error_rate_percent",
    "response_ms_avg",
    "response_ms_p95",
)
SERVER_SERIES = ("cpu_percent", "memory_percent", "disk_percent", "load_1m")


# ---------------------------------------------------------------- requests (from the gateway's thread)


@dataclass
class _Requests:
    count: int = 0
    errors: int = 0
    durations_ms: list[float] = field(default_factory=list)


_LOCK = threading.Lock()
_REQUESTS: defaultdict[str, _Requests] = defaultdict(_Requests)
_MAX_DURATIONS = 5000  # per interval; enough for a precise p95, bounded memory under heavy load


def record_request(name: str, status: int, duration_ms: float) -> None:
    """Count one request forwarded to the instance's Odoo, with its status and time to the first byte."""
    with _LOCK:
        requests = _REQUESTS[name]
        requests.count += 1
        requests.errors += status >= 500
        if len(requests.durations_ms) < _MAX_DURATIONS:
            requests.durations_ms.append(duration_ms)


def _take_requests() -> dict[str, _Requests]:
    """The requests counted since the last call, and start counting again."""
    global _REQUESTS
    with _LOCK:
        taken, _REQUESTS = _REQUESTS, defaultdict(_Requests)
    return taken


def percentile(values: list[float], share: float) -> float | None:
    """The value `share` (0..1) of the values stay under (nearest rank), or None when there are none."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(share * len(ordered)) - 1))]


# ---------------------------------------------------------------- samples (from the monitor's thread)

_HISTORY_SAMPLES = 240  # one hour at one sample every 15 seconds
SERVER: dict[str, Any] = {}
INSTANCES: dict[str, dict[str, Any]] = {}
_SERVER_HISTORY: deque[dict[str, Any]] = deque(maxlen=_HISTORY_SAMPLES)
_INSTANCE_HISTORY: defaultdict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=_HISTORY_SAMPLES))
_LAST_SAMPLE = 0.0


@dataclass(frozen=True, slots=True)
class Usage:
    """What Docker reports for an instance's containers together."""

    running: bool
    cpu_percent: float  # of the whole server
    memory_bytes: int
    memory_limit_bytes: int
    started_at: float | None  # Unix time Odoo's container started, when running
    disk_bytes: int | None  # database and files, when known


def sample(server: dict[str, Any], usage: dict[str, Usage]) -> dict[str, Any]:
    """Record one sample of the server and every instance; return the snapshot (the "metrics" event)."""
    global _LAST_SAMPLE
    now = time.time()
    seconds = max(1.0, now - _LAST_SAMPLE) if _LAST_SAMPLE else float(settings.monitor_interval_seconds)
    _LAST_SAMPLE = now
    requests = _take_requests()

    SERVER.clear()
    SERVER.update(server)
    _SERVER_HISTORY.append({"t": int(now), **{key: server.get(key) for key in SERVER_SERIES}})

    for name in set(INSTANCES) - set(usage):  # deleted
        INSTANCES.pop(name, None)
        _INSTANCE_HISTORY.pop(name, None)
    for name, measured in usage.items():
        counted = requests.get(name, _Requests())
        figures = {
            "running": measured.running,
            "cpu_percent": round(measured.cpu_percent, 2),
            "memory_bytes": measured.memory_bytes,
            "memory_limit_bytes": measured.memory_limit_bytes,
            "memory_percent": _percent(measured.memory_bytes, measured.memory_limit_bytes),
            "requests_per_minute": round(counted.count * 60 / seconds, 1),
            "error_rate_percent": _percent(counted.errors, counted.count),
            "response_ms_avg": (
                round(sum(counted.durations_ms) / len(counted.durations_ms), 1) if counted.durations_ms else None
            ),
            "response_ms_p95": _round(percentile(counted.durations_ms, 0.95)),
            "uptime_seconds": int(now - measured.started_at) if measured.running and measured.started_at else None,
            "disk_bytes": measured.disk_bytes,
        }
        INSTANCES[name] = figures
        _INSTANCE_HISTORY[name].append({"t": int(now), **{key: figures[key] for key in INSTANCE_SERIES}})
    return snapshot()


def _percent(part: float, whole: float) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


# ---------------------------------------------------------------- reading


def snapshot() -> dict[str, Any]:
    """The latest figures: {"at", "interval_seconds", "server": {...}, "instances": {name: {...}}}."""
    return {
        "at": int(_LAST_SAMPLE) or None,
        "interval_seconds": settings.monitor_interval_seconds,
        "server": dict(SERVER),
        "instances": {name: dict(figures) for name, figures in INSTANCES.items()},
    }


def _columns(samples: list[dict[str, Any]], series: tuple[str, ...]) -> dict[str, list[Any]]:
    return {key: [s[key] for s in samples] for key in ("t", *series)}


def _since(history: deque[dict[str, Any]], minutes: int) -> list[dict[str, Any]]:
    oldest = time.time() - minutes * 60
    return [s for s in list(history) if s["t"] >= oldest]


def server_history(minutes: int = 60) -> dict[str, list[Any]]:
    return _columns(_since(_SERVER_HISTORY, minutes), SERVER_SERIES)


def instance_history(name: str, minutes: int = 60) -> dict[str, list[Any]]:
    return _columns(_since(_INSTANCE_HISTORY.get(name, deque()), minutes), INSTANCE_SERIES)


def history(minutes: int = 60) -> dict[str, Any]:
    return {
        "server": server_history(minutes),
        "instances": {name: instance_history(name, minutes) for name in list(_INSTANCE_HISTORY)},
    }
