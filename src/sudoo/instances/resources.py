"""An instance's resources: its priority when the server is busy, and the limits an admin may set.

Unlimited by default. No instance has a processor or memory cap, and Odoo runs with its own defaults (threaded
mode), unless an admin sets a limit: as a default of a stage (settings <stage>_<knob>, Settings > Resources) or for
one instance (kept by the panel, storage collection "instance_resources"; the instance's own value wins).

Priority under load, from the instance's stage (settings priority_*), written on both containers (Odoo and its
database) every time the panel starts or recreates them:

    cpu_shares       processor weight (production 4096, staging 2048, development 1024; Docker's default is 1024):
                     a container gets processor time in proportion to its weight, but only when the processor is
                     busy; an idle server gives every container all it asks for
    mem_reservation  soft memory limit (production 1g for Odoo, 512m for its database; none for the others): when
                     the server runs short of memory, the kernel takes memory back from containers above their
                     reservation first; it reserves nothing in advance and caps nothing
    oom_score_adj    out-of-memory score (production 0, staging 300, development 600): when memory runs out, the
                     kernel stops the process with the highest score first, so development goes first and
                     production last. -1000 (never stopped) is refused: the out-of-memory killer stays on

The limits, each written only when set (0 is "not set"):

    odoo_workers, odoo_cron_threads             odoo.conf workers, max_cron_threads (0 workers: threaded mode)
    odoo_limit_memory_soft_mb / _hard_mb        odoo.conf limit_memory_soft / limit_memory_hard (bytes there)
    odoo_limit_time_cpu / _real                 odoo.conf limit_time_cpu / limit_time_real (seconds)
    odoo_memory_limit, odoo_cpu_limit           compose mem_limit, cpus of the Odoo container
    db_memory_limit, db_cpu_limit               the same for the database container

With workers, Odoo serves WebSockets (/websocket) on its gevent port (8072) instead of its HTTP port: the panel then
publishes that port on 127.0.0.1 (ODOO_GEVENT_PORT in the instance's .env, reserved the first time) and the gateway
sends WebSocket traffic there (ODOO_WORKERS in .env: the workers the containers were last started with).

Changes apply when the instance next starts, or at once through the "Apply resources" job (operations.py).
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

from sudoo.config import MIN_CONTAINER_MEMORY, RESOURCE_KNOBS, memory_bytes, settings
from sudoo.instances import stages, store
from sudoo.storage import storage

COLLECTION = "instance_resources"
GEVENT_PORT = 8072  # where Odoo serves WebSockets when it runs with workers

KNOBS = RESOURCE_KNOBS
_INTEGER = {
    "odoo_workers": 256,
    "odoo_cron_threads": 64,
    "odoo_limit_memory_soft_mb": 1048576,
    "odoo_limit_memory_hard_mb": 1048576,
    "odoo_limit_time_cpu": 86400,
    "odoo_limit_time_real": 86400,
}
_CORES = ("odoo_cpu_limit", "db_cpu_limit")
_MEMORY = ("odoo_memory_limit", "db_memory_limit")

CONF_OPTIONS = {
    "odoo_workers": "workers",
    "odoo_cron_threads": "max_cron_threads",
    "odoo_limit_memory_soft_mb": "limit_memory_soft",
    "odoo_limit_memory_hard_mb": "limit_memory_hard",
    "odoo_limit_time_cpu": "limit_time_cpu",
    "odoo_limit_time_real": "limit_time_real",
}
"""Knob -> the odoo.conf option it writes."""

COMPOSE_KEYS = ("cpu_shares", "mem_reservation", "oom_score_adj", "mem_limit", "cpus")
"""The keys of a service in the compose file that come from here."""

RANK = {stages.PRODUCTION: 1, stages.STAGING: 2, stages.DEVELOPMENT: 3}
ODOO_DEFAULTS = {  # what Odoo uses when odoo.conf does not say (for the estimates and the page)
    "odoo_cron_threads": 2,
    "odoo_limit_memory_soft_mb": 2048,
    "odoo_limit_memory_hard_mb": 2560,
    "odoo_limit_time_cpu": 60,
    "odoo_limit_time_real": 120,
}


class Invalid(ValueError):
    """A limit that cannot be applied (the message says which and why)."""


# ---------------------------------------------------------------- values


def cores() -> int:
    from sudoo.instances import metrics

    return int(metrics.SERVER.get("cores") or os.cpu_count() or 1)


def stage_defaults(stage: str) -> dict[str, Any]:
    """The default limits of `stage` (settings <stage>_<knob>)."""
    return {knob: getattr(settings, f"{stage}_{knob}") for knob in KNOBS}


def overrides(name: str) -> dict[str, Any]:
    """The limits set for this instance alone: {knob: value}, only those set."""
    found = storage().get(COLLECTION, name) or {}
    limits = found.get("limits") or {}
    return {knob: limits[knob] for knob in KNOBS if knob in limits}


def save_overrides(name: str, limits: Mapping[str, Any], user: str) -> None:
    storage().put(
        COLLECTION, name, {"limits": {k: limits[k] for k in KNOBS if k in limits}, "by": user, "at": time.time()}
    )


def forget(name: str) -> None:
    """Remove the instance's own limits (when it is deleted)."""
    storage().delete(COLLECTION, name)


def stage_of(name: str) -> str:
    return stages.of(store.read_env(name))


def effective(name: str, stage: str | None = None) -> dict[str, Any]:
    """The limits that apply to the instance: its own, else its stage's default."""
    return stage_defaults(stage or stage_of(name)) | overrides(name)


def priority(stage: str) -> dict[str, Any]:
    """The weights of `stage` under contention (settings priority_*)."""
    return {
        "rank": RANK.get(stage, 3),
        "cpu_weight": int(getattr(settings, f"priority_cpu_weight_{stage}")),
        "oom_score_adj": int(getattr(settings, f"priority_oom_score_{stage}")),
        "memory_reservation": str(getattr(settings, f"priority_memory_reservation_{stage}")),
        "db_memory_reservation": str(getattr(settings, f"priority_db_memory_reservation_{stage}")),
    }


def _cpus(value: float, available: int) -> float:
    return round(min(float(value), float(available)), 2)


def compose_keys(stage: str, limits: Mapping[str, Any], available: int | None = None) -> dict[str, dict[str, Any]]:
    """The resource keys of the Odoo and database services: the stage's weights always, a limit only when set.

    A reservation above the container's memory limit is lowered to it (Docker refuses it otherwise), and a processor
    limit above the server's cores is lowered to them (Docker refuses that too)."""
    weights = priority(stage)
    available = available or cores()
    found: dict[str, dict[str, Any]] = {}
    for service, reservation in (("odoo", weights["memory_reservation"]), ("db", weights["db_memory_reservation"])):
        keys: dict[str, Any] = {"cpu_shares": weights["cpu_weight"], "oom_score_adj": weights["oom_score_adj"]}
        limit = str(limits.get(f"{service}_memory_limit") or "0")
        if memory_bytes(reservation):
            keys["mem_reservation"] = limit if memory_bytes(limit) and memory_bytes(reservation) > memory_bytes(limit) else reservation  # fmt: skip
        if memory_bytes(limit):
            keys["mem_limit"] = limit
        cpus = float(limits.get(f"{service}_cpu_limit") or 0)
        if cpus > 0:
            keys["cpus"] = _cpus(cpus, available)
        found[service] = keys
    return found


def conf_options(limits: Mapping[str, Any]) -> dict[str, str | None]:
    """odoo.conf options from the limits: {option: value}, None for an option to leave out (Odoo's default)."""
    options: dict[str, str | None] = {}
    for knob, option in CONF_OPTIONS.items():
        value = int(limits.get(knob) or 0)
        if knob.startswith("odoo_limit_memory"):
            options[option] = str(value * 1024 * 1024) if value else None
        else:
            options[option] = str(value) if value else None
    return options


def workers(limits: Mapping[str, Any]) -> int:
    return int(limits.get("odoo_workers") or 0)


# ---------------------------------------------------------------- files


def write_conf(name: str, limits: Mapping[str, Any] | None = None) -> bool:
    """Write the limits into the instance's odoo.conf (a hand-edited odoo.conf is replaced by the panel's own
    first). Returns whether anything changed."""
    from sudoo.instances import integrity, odoo

    limits = effective(name) if limits is None else limits
    wanted = conf_options(limits)
    with integrity.writing(name, integrity.CONF):
        integrity.rewrite_if_changed(name)
        conf = odoo.read_config(name)
        if not conf.has_section("options"):
            return False
        changed = False
        for option, value in wanted.items():
            current = conf.get("options", option, fallback=None)
            if value is None and current is not None:
                conf.remove_option("options", option)
                changed = True
            elif value is not None and current != value:
                conf.set("options", option, value)
                changed = True
        if changed:
            with open(store.config_file(name), "w") as fh:
                conf.write(fh)
    return changed


def websocket_port(name: str, limits: Mapping[str, Any]) -> int | None:
    """The port Odoo's gevent server is published on when the instance runs with workers (reserved the first time
    in its .env, ODOO_GEVENT_PORT), else None."""
    if not workers(limits):
        return None
    env = store.read_env(name)
    if env.get("ODOO_GEVENT_PORT", "").isdigit():
        return int(env["ODOO_GEVENT_PORT"])
    ports = store.reserve_ports({}, only={"ODOO_GEVENT_PORT": settings.odoo_port_start})
    try:
        store.update_env(name, **ports)
    finally:
        store.release_ports(ports)
    return ports["ODOO_GEVENT_PORT"]


# ---------------------------------------------------------------- checks


def clean(data: Mapping[str, Any], available: int | None = None) -> dict[str, Any]:
    """The instance's own limits from a request: {knob: value}; null or "" removes one (the stage's default
    applies). Raises Invalid for a value that cannot be applied."""
    available = available or cores()
    unknown = sorted(set(data) - set(KNOBS))
    if unknown:
        raise Invalid(f"Unknown limits: {', '.join(unknown)}. Known: {', '.join(KNOBS)}.")
    found: dict[str, Any] = {}
    for knob, value in data.items():
        if value is None or value == "":
            continue
        if knob in _INTEGER:
            if isinstance(value, bool) or not isinstance(value, int | str) or not str(value).isdigit():
                raise Invalid(f"{knob}: a whole number from 0 is expected.")
            number = int(value)
            if number > _INTEGER[knob]:
                raise Invalid(f"{knob}: at most {_INTEGER[knob]}.")
            found[knob] = number
        elif knob in _CORES:
            try:
                cpus = float(value)
            except (TypeError, ValueError):
                raise Invalid(f"{knob}: a number of processor cores is expected (0.5, 2).") from None
            if isinstance(value, bool) or cpus < 0 or cpus != cpus:
                raise Invalid(f"{knob}: a number of processor cores from 0 is expected.")
            if cpus > available:
                raise Invalid(f"{knob}: this server has {available} cores; Docker refuses more.")
            found[knob] = round(cpus, 2)
        else:
            text = str(value).strip().lower()
            try:
                size = memory_bytes(text) if text[-1:] in "bkmg" or text == "0" else -1
            except ValueError:
                size = -1
            if size < 0:
                raise Invalid(f"{knob}: a quantity of memory in Docker units is expected (512m, 2g), or 0.")
            if size and size < MIN_CONTAINER_MEMORY:
                raise Invalid(f"{knob}: at least 128m, or 0 for no limit.")
            found[knob] = text
    soft = found.get("odoo_limit_memory_soft_mb")
    hard = found.get("odoo_limit_memory_hard_mb")
    if soft and hard and soft > hard:
        raise Invalid("The soft memory limit per worker is above the hard one: Odoo would stop requests first.")
    return found


def _reserved_by(stage: str) -> int:
    weights = priority(stage)
    return memory_bytes(weights["memory_reservation"]) + memory_bytes(weights["db_memory_reservation"])


def reservations(changes: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The memory reserved (mem_reservation) by all instances, by stage, with `changes` ({name: stage}) applied."""
    by_stage = dict.fromkeys(stages.STAGES, 0)
    names = set(store.names()) | set(changes or {})
    for name in names:
        stage = (changes or {}).get(name) or stage_of(name)
        by_stage[stage] += _reserved_by(stage)
    return {"total_bytes": sum(by_stage.values()), "by_stage": by_stage}


def _server_memory() -> tuple[int, int]:
    """(total, usable) memory of the server, from the latest measurement (0 before the first)."""
    from sudoo.instances import capacity

    found = capacity.current()
    if not found:
        return 0, 0
    return int(found["memory"]["total_bytes"]), int(found["memory"]["usable_bytes"])


def _mb(value: int) -> str:
    return f"{value / 1024**3:.1f} GB" if value >= 1024**3 else f"{value // 1024**2} MB"


def _warning(code: str, text: str, **values: Any) -> dict[str, Any]:
    return {"code": code, "text": text, "values": values}


def warnings(name: str, limits: Mapping[str, Any], stage: str | None = None) -> list[dict[str, Any]]:
    """What may not work well with these limits, each {"code", "text", "values"}; nothing here refuses anything."""
    stage = stage or stage_of(name)
    found: list[dict[str, Any]] = []
    total, usable = _server_memory()
    reserved = reservations({name: stage})["total_bytes"]
    if total and reserved > total:
        found.append(_warning(
            "reserved_over_total",
            f"The memory reserved for instances ({_mb(reserved)}) is more than this server has ({_mb(total)}): "
            "under memory pressure the reservations cannot all be honoured. Lower the reservations "
            "(Settings, Resources) or give the server more memory.",
            reserved_bytes=reserved, total_bytes=total,
        ))  # fmt: skip
    elif usable and reserved > usable:
        found.append(_warning(
            "reserved_over_usable",
            f"The memory reserved for instances ({_mb(reserved)}) is more than this server can spare for them "
            f"({_mb(usable)}).",
            reserved_bytes=reserved, usable_bytes=usable,
        ))  # fmt: skip
    count = workers(limits)
    container = memory_bytes(str(limits.get("odoo_memory_limit") or "0"))
    soft = int(limits.get("odoo_limit_memory_soft_mb") or ODOO_DEFAULTS["odoo_limit_memory_soft_mb"]) * 1024**2
    cron = int(limits.get("odoo_cron_threads") or ODOO_DEFAULTS["odoo_cron_threads"])
    if container and count and container < (count + cron) * soft // 2:
        found.append(_warning(
            "workers_memory",
            f"{count} workers and {cron} cron workers may need more memory than the Odoo container's limit "
            f"({_mb(container)}): Odoo may be stopped by the kernel. Raise the limit or lower the workers.",
            workers=count, cron=cron, limit_bytes=container,
        ))  # fmt: skip
    if total and container and container > total:
        found.append(_warning(
            "limit_over_total",
            f"The Odoo container's memory limit ({_mb(container)}) is more than this server has ({_mb(total)}).",
            limit_bytes=container, total_bytes=total,
        ))  # fmt: skip
    cpu, real = int(limits.get("odoo_limit_time_cpu") or 0), int(limits.get("odoo_limit_time_real") or 0)
    if cpu and real and cpu > real:
        found.append(_warning(
            "time_cpu_over_real",
            "The processor time per request is above the total time per request: the total time ends it first.",
        ))  # fmt: skip
    suggested = 2 * cores() + 1
    if count and count > suggested:
        found.append(_warning(
            "workers_over_cores",
            f"{count} workers on {cores()} cores: Odoo suggests at most cores × 2 + 1 ({suggested}) for the whole "
            "server.",
            workers=count, cores=cores(), suggested=suggested,
        ))  # fmt: skip
    return found


# ---------------------------------------------------------------- applied or not


def pending(name: str) -> bool:
    """Whether the containers or odoo.conf differ from what the current limits and weights would write: the
    instance needs a restart (or "Apply resources") to use them."""
    from sudoo.instances import integrity, odoo

    found = integrity.record(name)
    if not found:
        return False
    stage = stage_of(name)
    limits = effective(name, stage)
    wanted = compose_keys(stage, limits)
    services = found.get("spec", {}).get("services", {})
    for service, keys in wanted.items():
        applied = services.get(service, {})
        if {k: applied.get(k) for k in COMPOSE_KEYS} != {k: keys.get(k) for k in COMPOSE_KEYS}:
            return True
    published = any(str(p).endswith(f":{GEVENT_PORT}") for p in services.get("odoo", {}).get("ports", []))
    if published != bool(workers(limits)):
        return True
    conf = odoo.read_config(name)
    return any(conf.get("options", option, fallback=None) != value for option, value in conf_options(limits).items())


def resource(name: str) -> dict[str, Any]:
    """GET /api/v1/instances/<name>/resources."""
    stage = stage_of(name)
    defaults = stage_defaults(stage)
    own = overrides(name)
    limits = defaults | own
    total, usable = _server_memory()
    return {
        "stage": stage,
        "priority": priority(stage),
        "limits": limits,
        "own": own,
        "stage_defaults": defaults,
        "odoo_defaults": ODOO_DEFAULTS,
        "unlimited": not any(memory_bytes(str(limits[k])) for k in _MEMORY) and not any(limits[k] for k in _CORES),
        "pending": pending(name),
        "warnings": warnings(name, limits, stage),
        "server": {
            "cores": cores(),
            "memory_bytes": total or None,
            "usable_bytes": usable or None,
            "reserved_bytes": reservations()["total_bytes"],
        },
    }
