"""Audit log: who did what, from where, and whether it was allowed.

Kept by the storage plugin (storage/, the "audit" table of SQLite), newest first when read; the oldest entries are
removed beyond 200,000.

Each entry also names the project and branch it is about, when there is one: those of its instance, or, for changes
to a project itself (whose detail starts with the project's id, then the branch), those named there. Entries written
before this was recorded are found by the instances the project and branch have now. search() filters, sorts and
pages them; export() writes them as CSV or XLSX (core/xlsx.py).
"""

from __future__ import annotations

import csv
import io
import re
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from sudoo.storage import storage

LISTENERS: list[Callable[[dict[str, Any]], None]] = []
"""Called with every new entry (a server's agent passes its entries to the main panel: premium/servers.py)."""

SORTS = ("ts", "user", "event", "instance", "ip", "ok")
EXPORT_MAX_DAYS = 366
COLUMNS = ("time", "user", "event", "allowed", "project", "branch", "instance", "address", "detail")
_PROJECT_DETAIL = re.compile(r"^([a-z0-9][a-z0-9-]{0,39})(?::|$| (?:\(|([^\s:()]+)(?::| \(|$)))")


def _scope(instance: str | None, detail: str | None) -> tuple[str | None, str | None]:
    """(project, branch) an entry is about; never fails (recording an entry must not)."""
    try:
        from sudoo import projects

        if instance:
            project = projects.project_of(instance)
            if not project:  # an instance being created has no .env yet: its creation's plan names the project
                from sudoo.core import jobs

                job = jobs.creating(instance)
                planned = (job.details.get("project") or None) if job is not None else None
                return planned, (job.details.get("branch") or None) if job is not None and planned else None
            return project, (projects.branch_of(instance) or None) if project else None
        found = _PROJECT_DETAIL.match(detail or "")
        if found and projects.get(found.group(1)) is not None:
            return found.group(1), found.group(2)
    except Exception:  # noqa: BLE001 - a missing .env or storage hiccup only leaves the scope out
        return None, None
    return None, None


def record(
    user: str,
    ip: str | None,
    event: str,
    instance: str | None = None,
    ok: bool = True,
    detail: object = None,
) -> None:
    entry: dict[str, Any] = {"ts": round(time.time()), "user": user, "ip": ip, "event": event, "ok": ok}
    if instance:
        entry["instance"] = instance
    if detail:
        entry["detail"] = str(detail)[:300]
    project, branch = _scope(instance, entry.get("detail"))
    if project:
        entry["project"] = project
        if branch:
            entry["branch"] = branch
    storage().log(entry)
    for listener in LISTENERS:
        try:
            listener(entry)
        except Exception as error:  # noqa: BLE001 - recording never fails because of a listener
            print(f"audit listener: {error!r}", flush=True)


def latest(
    limit: int = 200, offset: int = 0, user: str | None = None, instance: str | None = None
) -> list[dict[str, Any]]:
    """The newest entries, newest first, optionally of one user or instance."""
    return storage().logs(limit, offset, user=user, instance=instance)


def with_instances(filters: dict[str, Any]) -> dict[str, Any]:
    """`filters` with the instances its project and branch have now (for entries recorded without a scope)."""
    project = filters.get("project")
    if not project:
        return filters
    from sudoo import projects

    names = projects.instances(project)
    found = {**filters, "project_instances": names}
    if filters.get("branch"):
        found["branch_instances"] = [n for n in names if projects.branch_of(n) == filters["branch"]]
    return found


def search(
    filters: dict[str, Any], sort: str = "ts", descending: bool = True, limit: int = 100, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """One page of the entries matching `filters` and how many match in all.

    filters: user, event, instance, ip (exact), ok (True: allowed, False: refused), since and until (Unix times,
    until excluded), project and branch (ids), text (in any field)."""
    sort = sort if sort in SORTS else "ts"
    filters = with_instances(filters)
    native = getattr(storage(), "search_logs", None)
    if native is not None:
        found: tuple[list[dict[str, Any]], int] = native(filters, sort, descending, limit, offset)
        return found
    matching = [e for e in _scan() if _matches(e, filters)]  # another storage plugin: filtered here
    matching.sort(key=lambda e: (e.get(sort) is None, str(e.get(sort, "")) if sort != "ts" else e["ts"]),
                  reverse=descending)  # fmt: skip
    return matching[offset : offset + limit], len(matching)


def facets() -> dict[str, list[str]]:
    """The users and events in the log (for the filters' choices)."""
    native = getattr(storage(), "audit_facets", None)
    if native is not None:
        found: dict[str, list[str]] = native()
        return found
    entries = list(_scan())
    return {"users": sorted({e["user"] for e in entries}), "events": sorted({e["event"] for e in entries})}


def _scan(page: int = 2000) -> Iterator[dict[str, Any]]:
    offset = 0
    while True:
        batch = storage().logs(page, offset)
        yield from batch
        if len(batch) < page:
            return
        offset += page


def _matches(entry: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key in ("user", "instance", "event", "ip"):
        if filters.get(key) and entry.get(key) != filters[key]:
            return False
    if filters.get("ok") is not None and bool(entry.get("ok")) != bool(filters["ok"]):
        return False
    if filters.get("since") is not None and entry["ts"] < filters["since"]:
        return False
    if filters.get("until") is not None and entry["ts"] >= filters["until"]:
        return False
    for key in ("project", "branch"):
        if filters.get(key):
            own = entry.get(key)
            if own != filters[key] and not (own is None and entry.get("instance") in (filters.get(f"{key}_instances")
                                                                                     or ())):  # fmt: skip
                return False
    if filters.get("text"):
        text = str(filters["text"]).lower()
        if not any(text in str(entry.get(k) or "").lower() for k in ("user", "event", "instance", "ip", "detail")):
            return False
    return True


# --------------------------------------------------------------------------- export


def row(entry: dict[str, Any]) -> list[str]:
    """An entry as a row of an export (COLUMNS)."""
    when = datetime.fromtimestamp(entry["ts"], UTC).strftime("%Y-%m-%d %H:%M:%S")
    return [
        when,
        str(entry.get("user") or ""),
        str(entry.get("event") or ""),
        "yes" if entry.get("ok") else "no",
        str(entry.get("project") or ""),
        str(entry.get("branch") or ""),
        str(entry.get("instance") or ""),
        str(entry.get("ip") or ""),
        str(entry.get("detail") or ""),
    ]


def _safe_cell(value: str) -> str:
    """A text spreadsheet programs will not run as a formula (CSV injection)."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def export_rows(filters: dict[str, Any], sort: str, descending: bool, max_rows: int) -> tuple[list[list[str]], int]:
    """(rows, how many matched) of an export: at most `max_rows`, in pages."""
    rows: list[list[str]] = []
    total = 0
    page = 5000
    while len(rows) < max_rows:
        batch, total = search(filters, sort, descending, min(page, max_rows - len(rows)), len(rows))
        rows += [row(e) for e in batch]
        if len(batch) < page:
            break
    return rows, total


def to_csv(rows: list[list[str]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([_safe_cell(value) for value in row])
    return ("﻿" + out.getvalue()).encode()  # the byte order mark makes spreadsheet programs read UTF-8


def to_xlsx(rows: list[list[str]]) -> bytes:
    from sudoo.core import xlsx

    return xlsx.workbook("Audit log", [list(COLUMNS), *rows], widths=[20, 24, 30, 9, 18, 22, 28, 16, 80])
