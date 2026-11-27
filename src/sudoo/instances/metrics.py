"""Metrics of the server and every instance: current figures and one hour of history.

The monitor (monitor.py) measures every 15 seconds and calls `sample()`; the gateway calls `record_request()` for
every request it forwards to Odoo (in the gateway service, the panel collects them every few seconds and adds them
with `add_requests()`, gateway_link.py). From both, each interval gets one sample per instance:

    cpu_percent          share of the whole server's processor (all cores), Odoo and its database together
    memory_percent       memory in use, of the containers' limit (the server's memory when they have none)
    requests_per_minute  requests forwarded to Odoo (page loads, RPC calls; not the open-tab background traffic)
    error_rate_percent   share of those answered with a 5xx error
    response_ms_avg/p95  time until Odoo started answering: average, and the time 95 % of requests stayed under

The server's figures include the panel's own footprint (`panel_*`: its backend, web service and gateway, and the
Cloudflare connectors), now and at most over the history, which capacity.py turns into how many instances the server can run.

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
SERVER_SERIES = (
    "cpu_percent",
    "memory_percent",
    "disk_percent",
    "load_1m",
    "instances_running",
    "panel_cpu_percent",
    "panel_memory_bytes",
)


# ---------------------------------------------------------------- requests (from the gateway's thread)


@dataclass
class _Requests:
    count: int = 0
    errors: int = 0
    durations_ms: list[float] = field(default_factory=list)


_LOCK = threading.Lock()
_REQUESTS: defaultdict[str, _Requests] = defaultdict(_Requests)
MAX_DURATIONS = 5000  # per interval; enough for a precise p95, bounded memory under heavy load


def record_request(name: str, status: int, duration_ms: float) -> None:
    """Count one request forwarded to the instance's Odoo, with its status and time to the first byte."""
    with _LOCK:
        requests = _REQUESTS[name]
        requests.count += 1
        requests.errors += status >= 500
        if len(requests.durations_ms) < MAX_DURATIONS:
            requests.durations_ms.append(duration_ms)


def add_requests(name: str, count: int, errors: int, durations_ms: list[float]) -> None:
    """Add requests counted elsewhere: by the gateway service, which the panel collects them from
    (gateway_link.py)."""
    with _LOCK:
        requests = _REQUESTS[name]
        requests.count += count
        requests.errors += errors
        requests.durations_ms.extend(durations_ms[: max(0, MAX_DURATIONS - len(requests.durations_ms))])


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

_HISTORY_SAMPLES = 1800  # one hour at the shortest interval (2 seconds); older samples are left out by time
SERVER: dict[str, Any] = {}
PANEL: dict[str, Any] = {}  # the panel's footprint by part (monitor.py): {"backend": {...}, "web": {...}, ...}
INSTANCES: dict[str, dict[str, Any]] = {}
# name -> the core/slots.py number taken before the monitor started to read that instance's state (a slot is released
# only by a read that began after its job ended). Private to this process: never in INSTANCES, which clients see.
OBSERVED: dict[str, int] = {}
_SERVER_HISTORY: deque[dict[str, Any]] = deque(maxlen=_HISTORY_SAMPLES)
_INSTANCE_HISTORY: defaultdict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=_HISTORY_SAMPLES))
_LAST_SAMPLE = 0.0
_SAMPLES = 0  # samples taken: the version of everything read here (answers built from them are cached per version)


@dataclass(frozen=True, slots=True)
class Usage:
    """What Docker reports for an instance's containers together."""

    running: bool
    cpu_percent: float  # of the whole server
    memory_bytes: int
    memory_limit_bytes: int
    started_at: float | None  # Unix time Odoo's container started, when running
    disk_bytes: int | None  # database and files, when known
    observed_seq: int | None = (
        None  # core/slots.py number taken before the state was read; None: not observed this time
    )


def sample(server: dict[str, Any], usage: dict[str, Usage], panel: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record one sample of the server and every instance; return the snapshot (the "metrics" event).

    `panel` is the panel's footprint by part; the server's `panel_cpu_percent` and `panel_memory_bytes` are its
    totals, and their highest values over the history are added as `panel_cpu_peak_percent` and
    `panel_memory_peak_bytes`."""
    global _LAST_SAMPLE, _SAMPLES
    now = time.time()
    seconds = max(1.0, now - _LAST_SAMPLE) if _LAST_SAMPLE else float(settings.metrics_interval_seconds)
    _LAST_SAMPLE = now
    _SAMPLES += 1
    requests = _take_requests()

    SERVER.clear()
    SERVER.update(server)
    _SERVER_HISTORY.append({"t": int(now), **{key: server.get(key) for key in SERVER_SERIES}})
    PANEL.clear()
    PANEL.update(panel or {})
    if server.get("panel_memory_bytes") is not None:
        SERVER["panel_memory_peak_bytes"] = _peak("panel_memory_bytes")
        SERVER["panel_cpu_peak_percent"] = _peak("panel_cpu_percent")

    for name in set(INSTANCES) - set(usage):  # deleted
        INSTANCES.pop(name, None)
        OBSERVED.pop(name, None)
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
        if measured.observed_seq is None:  # the engine could not be asked: "running" is False for lack of an answer
            OBSERVED.pop(name, None)
        else:
            OBSERVED[name] = measured.observed_seq  # the state is from this number or later, never earlier
        _INSTANCE_HISTORY[name].append({"t": int(now), **{key: figures[key] for key in INSTANCE_SERIES}})
    return snapshot()


def _peak(key: str) -> Any:
    """The highest value of a server figure over the history kept."""
    return max((s[key] for s in _SERVER_HISTORY if s.get(key) is not None), default=None)


def _percent(part: float, whole: float) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


# ---------------------------------------------------------------- reading


def version() -> int:
    """Changes with every sample: answers built from the figures are valid until the next one."""
    return _SAMPLES


def snapshot() -> dict[str, Any]:
    """The latest figures: {"at", "interval_seconds", "server": {...}, "instances": {name: {...}}}."""
    return {
        "at": int(_LAST_SAMPLE) or None,
        "interval_seconds": settings.metrics_interval_seconds,
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


def running_profiles(minutes: int = 60) -> dict[str, dict[str, float | int]]:
    """How much each instance used while it ran (samples with memory in use), over the last `minutes`:
    {name: {"samples", "average_bytes", "peak_bytes", "cpu_percent"}} (cpu_percent: the average)."""
    profiles: dict[str, dict[str, float | int]] = {}
    for name in list(_INSTANCE_HISTORY):
        ran = [s for s in _since(_INSTANCE_HISTORY.get(name, deque()), minutes) if s.get("memory_bytes")]
        if ran:
            memory = [s["memory_bytes"] for s in ran]
            profiles[name] = {
                "samples": len(ran),
                "average_bytes": sum(memory) / len(memory),
                "peak_bytes": max(memory),
                "cpu_percent": sum(s.get("cpu_percent") or 0 for s in ran) / len(ran),
            }
    return profiles


def server_average(key: str, minutes: int = 60) -> float | None:
    """The average of a server figure over the last `minutes`."""
    values = [s[key] for s in _since(_SERVER_HISTORY, minutes) if s.get(key) is not None]
    return sum(values) / len(values) if values else None


def history(minutes: int = 60) -> dict[str, Any]:
    return {
        "server": server_history(minutes),
        "instances": {name: instance_history(name, minutes) for name in list(_INSTANCE_HISTORY)},
    }
