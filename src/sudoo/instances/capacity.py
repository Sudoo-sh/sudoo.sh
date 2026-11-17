"""How many instances this server can run at the same time, from what the monitor measured (metrics.py).

Memory decides: an Odoo that runs short of memory slows down every instance on the server (the system starts
swapping, or the kernel stops processes), while a busy processor only makes them wait their turn.

    reserve       = the larger of CAPACITY_RESERVE_MB and CAPACITY_RESERVE_PERCENT of the server's memory
                    (the operating system, Docker, the page cache)
    panel         = the panel's own memory at its highest over the last hour (backend, web service, connectors)
    usable        = server memory - reserve - panel
    per instance  = the memory of a running instance at its highest over the last hour, averaged over the
                    instances that ran (at least 3 measurements each); CAPACITY_INSTANCE_MEMORY_MB until one has
    comfortable   = floor(usable / per instance), and no more than the processor allows:
                    floor((CAPACITY_WARNING_PERCENT - the panel's average processor use)
                          / the average processor use of a running instance)

`running` counts the instances running now and those a job is creating or starting. Paused instances use only
disk: the disk figures say how many more instances of the average size fit on the free disk.

Nothing here refuses anything unless MAX_RUNNING_INSTANCES is set (`refusal`); the figures are for warnings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sudoo.config import settings
from sudoo.core import jobs
from sudoo.instances import metrics, store

MB = 1024 * 1024
MIN_SAMPLES = 3  # measurements of a running instance before its memory counts (a starting Odoo uses less)


@dataclass(frozen=True, slots=True)
class Rules:
    """The settings the estimate follows."""

    reserve_mb: int = 1024
    reserve_percent: float = 10
    instance_memory_mb: int = 700
    warning_percent: float = 85
    hard_limit: int = 0

    @classmethod
    def current(cls) -> Rules:
        return cls(
            settings.capacity_reserve_mb,
            settings.capacity_reserve_percent,
            settings.capacity_instance_memory_mb,
            settings.capacity_warning_percent,
            settings.max_running_instances,
        )


@dataclass(frozen=True, slots=True)
class Measured:
    """One instance as it ran over the last hour."""

    name: str
    version: str
    samples: int
    average_bytes: float
    peak_bytes: int
    cpu_percent: float  # average, of the whole server


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def warning_text(comfortable: int, running: int) -> str:
    """The warning shown before creating or starting an instance on a full server."""
    return (
        f"This server can run about {_plural(comfortable, 'instance', 'instances')} at once; "
        f"{running} {'is' if running == 1 else 'are'} running. Starting another may slow every instance."
    )


def estimate(
    server: dict[str, Any],
    measured: list[Measured],
    running: int,
    disk_sizes: list[int],
    rules: Rules,
    panel_cpu_average: float | None = None,
) -> dict[str, Any]:
    """The capacity of a server with the figures `server` (metrics.SERVER), from the instances `measured`, with
    `running` instances running or starting and `disk_sizes` the known sizes (database and files) of instances."""
    total = int(server.get("memory_total_bytes") or 0)
    cores = int(server.get("cores") or 1)
    reserve = int(max(rules.reserve_mb * MB, total * rules.reserve_percent / 100))
    panel_now = int(server.get("panel_memory_bytes") or 0)
    panel = int(max(server.get("panel_memory_peak_bytes") or 0, panel_now))
    usable = max(0, total - reserve - panel)

    counted = [m for m in measured if m.samples >= MIN_SAMPLES and m.peak_bytes > 0]
    average: float | None
    peak: float | None
    if counted:
        average = sum(m.average_bytes for m in counted) / len(counted)
        peak = sum(m.peak_bytes for m in counted) / len(counted)
        per_instance, source = peak, "measured"
    else:
        average = peak = None
        per_instance, source = float(rules.instance_memory_mb * MB), "default"
    by_memory = math.floor(usable / per_instance) if per_instance > 0 else 0

    panel_cpu = panel_cpu_average if panel_cpu_average is not None else float(server.get("panel_cpu_percent") or 0)
    cpu_each = sum(m.cpu_percent for m in counted) / len(counted) if counted else None
    by_processor = (
        max(0, math.floor((rules.warning_percent - panel_cpu) / cpu_each)) if cpu_each and cpu_each >= 0.01 else None
    )
    comfortable = by_memory if by_processor is None else min(by_memory, by_processor)
    limited_by = "processor" if by_processor is not None and by_processor < by_memory else "memory"

    versions: dict[str, list[Measured]] = {}
    for m in counted:
        versions.setdefault(m.version, []).append(m)
    by_version = [
        {
            "version": version,
            "instances": len(group),
            "average_bytes": int(sum(m.average_bytes for m in group) / len(group)),
            "peak_bytes": int(sum(m.peak_bytes for m in group) / len(group)),
        }
        for version, group in sorted(versions.items())
    ]

    free_disk = max(0, int(server.get("disk_total_bytes") or 0) - int(server.get("disk_used_bytes") or 0))
    sizes = [size for size in disk_sizes if size > 0]
    disk_average = int(sum(sizes) / len(sizes)) if sizes else None

    memory_percent = server.get("memory_percent")
    memory_high = memory_percent is not None and memory_percent >= rules.warning_percent
    over = running > comfortable
    alert = None
    if memory_high:
        alert = (
            f"Memory is at {memory_percent:g}% of this server (the warning starts at {rules.warning_percent:g}%). "
            "Instances may slow down: pause the ones nobody uses, or give the server more memory."
        )
    elif over:
        alert = (
            f"{_plural(running, 'instance is', 'instances are')} running; this server runs about {comfortable} "
            "comfortably. Every instance may be slower until some of them pause."
        )
    hard_limit = rules.hard_limit or None
    return {
        "comfortable": comfortable,
        "running": running,
        "headroom": comfortable - running,
        "limited_by": limited_by,
        "warning": running >= comfortable,  # starting one more goes beyond the estimate
        "message": warning_text(comfortable, running) if running >= comfortable else None,
        "overloaded": memory_high or over,
        "alert": alert,
        "memory_percent": memory_percent,
        "warning_percent": rules.warning_percent,
        "hard_limit": hard_limit,
        "blocked": bool(hard_limit and running >= hard_limit),
        "memory": {
            "total_bytes": total,
            "reserve_bytes": reserve,
            "panel_bytes": panel,
            "usable_bytes": usable,
            "per_instance_bytes": int(per_instance),
            "per_instance_source": source,
            "instance_average_bytes": None if average is None else int(average),
            "instance_peak_bytes": None if peak is None else int(peak),
            "instance_largest_bytes": max((m.peak_bytes for m in counted), default=None),
            "measured_instances": len(counted),
            "instances": by_memory,
        },
        "processor": {
            "cores": cores,
            "panel_percent": round(panel_cpu, 2),
            "per_instance_percent": None if cpu_each is None else round(cpu_each, 2),
            "instances": by_processor,
        },
        "disk": {
            "free_bytes": free_disk,
            "instance_average_bytes": disk_average,
            "more_instances": free_disk // disk_average if disk_average else None,
        },
        "by_version": by_version,
    }


# ---------------------------------------------------------------- from the monitor's figures

_CACHE: dict[str, Any] = {"key": None, "value": None}


def _version_of(name: str) -> str:
    try:
        return store.read_env(name).get("ODOO_VERSION") or "unknown"
    except OSError:
        return "unknown"


def _running() -> int:
    running = {name for name, figures in metrics.INSTANCES.items() if figures.get("running")}
    return len(running | jobs.starting())


def current() -> dict[str, Any] | None:
    """The estimate from the latest measurements, the panel's footprint by part included; None before the first.
    Built once per measurement, settings and jobs starting instances."""
    server = dict(metrics.SERVER)
    if not server.get("memory_total_bytes"):
        return None
    rules = Rules.current()
    running = _running()
    key = (metrics.version(), rules, running)
    if _CACHE["key"] == key:
        return dict(_CACHE["value"])
    measured = [
        Measured(
            name,
            _version_of(name),
            int(p["samples"]),
            float(p["average_bytes"]),
            int(p["peak_bytes"]),
            float(p["cpu_percent"]),
        )
        for name, p in metrics.running_profiles().items()
    ]
    sizes = [int(f["disk_bytes"]) for f in metrics.INSTANCES.values() if f.get("disk_bytes")]
    value = estimate(server, measured, running, sizes, rules, metrics.server_average("panel_cpu_percent"))
    value["panel"] = {
        "cpu_percent": server.get("panel_cpu_percent"),
        "cpu_peak_percent": server.get("panel_cpu_peak_percent"),
        "memory_bytes": server.get("panel_memory_bytes"),
        "memory_peak_bytes": server.get("panel_memory_peak_bytes"),
        "parts": {part: dict(figures) for part, figures in metrics.PANEL.items()},
    }
    _CACHE.update(key=key, value=value)
    return dict(value)


def snapshot() -> dict[str, Any]:
    """metrics.snapshot() with the estimate: the "metrics" event and GET /api/v1/metrics."""
    return {**metrics.snapshot(), "capacity": current()}


def refusal() -> str | None:
    """Why one more instance may not start (MAX_RUNNING_INSTANCES reached), or None."""
    limit = settings.max_running_instances
    if not limit:
        return None
    running = _running()
    if running < limit:
        return None
    return (
        f"This server runs at most {_plural(limit, 'instance', 'instances')} at once and "
        f"{running} {'is' if running == 1 else 'are'} running or starting. Pause one first."
    )
