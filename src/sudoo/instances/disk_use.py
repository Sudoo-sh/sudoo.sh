"""How much disk each instance uses, as Odoo.sh shows a production database's size: its database (PostgreSQL's
pg_database_size) plus its files (the filestore, measured with du).

Measured by the monitor while the instance runs, once every STORAGE_MEASURE_MINUTES (60 by default: both commands
read the disk, so they are not run on every cycle), and kept in the panel's storage (collection "instance_storage":
{"db", "files", "at"}), so a paused instance still shows its last measurement and a restart of the panel does not
measure everything again.

Each stage has a soft quota (STORAGE_QUOTA_GB_<STAGE>, 0: none): from STORAGE_WARNING_PERCENT of it an instance is
shown as almost full, above it as over its quota, and alerts/watch.py tells the administrators. Nothing is refused or
stopped: quotas are for planning, not enforcement.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from sudoo.config import settings
from sudoo.core.shell import capture
from sudoo.instances import stages, store
from sudoo.storage import storage

COLLECTION = "instance_storage"
GB = 1024**3


def parse_database_size(out: str) -> int | None:
    """The bytes psql printed for SELECT pg_database_size(...) (-tA: the number alone), or None."""
    text = (out or "").strip().splitlines()
    value = text[-1].strip() if text else ""
    return int(value) if value.isdigit() else None


def parse_du(out: str) -> int | None:
    """The bytes `du -sb <folder>` printed ("12345\\t/var/lib/odoo/filestore/db"), or None."""
    first = (out or "").strip().split(maxsplit=1)
    return int(first[0]) if first and first[0].isdigit() else None


def measured(name: str) -> dict[str, Any]:
    """The last measurement kept: {"db", "files", "at"} (each None when never measured)."""
    found = storage().get(COLLECTION, name) or {}
    return {"db": found.get("db"), "files": found.get("files"), "at": found.get("at")}


def due(name: str, now: float | None = None) -> bool:
    at = measured(name)["at"]
    return not at or (now or time.time()) - float(at) >= settings.storage_measure_minutes * 60


def measure(name: str, db_running: bool, odoo_running: bool) -> dict[str, Any]:
    """Measure the instance now (each part only when its container runs; the other keeps its last value), keep and
    return it."""
    from sudoo import engines
    from sudoo.instances import odoo

    engine = engines.of(name)
    found = measured(name)
    database = odoo.databases(name)[0]
    if db_running:
        out = capture(
            engine.exec_cmd(name, "db", "psql", "-U", "odoo", "-d", "postgres", "-tAc",
                            f"SELECT pg_database_size('{database}')")
        )  # fmt: skip
        found["db"] = parse_database_size(out) if out else found["db"]
    if odoo_running:
        out = capture(engine.exec_cmd(name, "odoo", "du", "-sb", f"/var/lib/odoo/filestore/{database}"))
        found["files"] = parse_du(out) if out else 0  # du fails when there is no filestore yet: nothing stored
    found["at"] = time.time()
    storage().put(COLLECTION, name, found)
    return found


def current(name: str, db_running: bool = False, odoo_running: bool = False) -> dict[str, Any]:
    """The last measurement, measured again first when it is due and the containers run."""
    found = measured(name)
    missing = (db_running and found["db"] is None) or (odoo_running and found["files"] is None)
    if (db_running or odoo_running) and (missing or due(name)):
        return measure(name, db_running, odoo_running)
    return found


def forget(name: str) -> None:
    storage().delete(COLLECTION, name)


# ---------------------------------------------------------------- quotas


def quota_bytes(stage: str) -> int:
    """The soft quota of an instance of `stage` (STORAGE_QUOTA_GB_<STAGE>); 0: none."""
    return int(float(getattr(settings, f"storage_quota_gb_{stage}", 0) or 0) * GB)


def level(total: int | None, quota: int) -> str:
    """ "none" (no quota or not measured), "ok", "warning" (from STORAGE_WARNING_PERCENT) or "over" (above it)."""
    if not quota or total is None:
        return "none"
    if total > quota:
        return "over"
    return "warning" if total * 100 >= quota * settings.storage_warning_percent else "ok"


def usage(name: str, stage: str | None = None) -> dict[str, Any]:
    """An instance's storage as the API shows it."""
    stage = stage or stages.of(store.read_env(name))
    found = measured(name)
    parts = [found["db"], found["files"]]
    total = None if all(part is None for part in parts) else sum(part or 0 for part in parts)
    quota = quota_bytes(stage)
    return {
        "database_bytes": found["db"],
        "files_bytes": found["files"],
        "total_bytes": total,
        "measured_at": found["at"],
        "quota_bytes": quota or None,
        "percent": round(100 * total / quota, 1) if quota and total is not None else None,
        "level": level(total, quota),
    }


def totals(names: Iterable[str]) -> dict[str, Any]:
    """The storage of several instances (a project's): by instance, and the sum by stage."""
    by_instance: dict[str, dict[str, Any]] = {}
    by_stage = dict.fromkeys(stages.STAGES, 0)
    for name in names:
        stage = stages.of(store.read_env(name))
        found = usage(name, stage)
        by_instance[name] = found | {"stage": stage}
        by_stage[stage] += found["total_bytes"] or 0
    return {"instances": by_instance, "by_stage": by_stage, "total_bytes": sum(by_stage.values())}
