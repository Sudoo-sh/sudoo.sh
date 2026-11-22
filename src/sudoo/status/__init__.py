"""Project status: is production up, how often was it up, and when was it not (the Odoo.sh "Status" page).

Every STATUS_CHECK_INTERVAL_SECONDS the panel checks (checks.py):

    instance:<name>   each production instance of a project: GET /web/health through its own gateway port, as a
                      visitor's request goes (STATUS_CHECK_TIMEOUT_SECONDS); slower than STATUS_SLOW_MS is degraded
    server            the server and the panel on it: the monitor's latest measurement is fresh (its heartbeat);
                      the disk nearly full or memory above the warning is degraded
    backups:<name>    the production instance's backups: the latest one is younger than the daily schedule allows
                      (DAILY_BACKUP_TIME, plus STATUS_BACKUP_GRACE_HOURS)
    offsite:<name>    its off-site copies, when they are on and the off-site package says when the last one worked

What is kept (storage, small documents):

    status_minutes    "<target>|<hour start>": {"m": {"<minute>": [checks, ok, response ms sum, ms count]}}, kept
                      STATUS_MINUTE_HOURS (48 h)
    status_hours      "<target>|<day start>": {"h": {"<hour>": [minutes, minutes up, ms sum, ms count, degraded]}},
                      the same results per hour, kept STATUS_KEEP_DAYS (90 days)
    status_incidents  one document per incident: when a target stopped being up until it was up again, what was
                      seen (kept STATUS_KEEP_DAYS)
    status_meta       per target: its current state, since when, the latest detail and the open incident

A minute is up when every check in it was up or degraded. Uptime is minutes up / minutes checked, except for the
server: there, a minute without any check (the panel was not running) counts as down, from the first check on. Days
are UTC days.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sudoo.config import settings
from sudoo.storage import Document, storage

MINUTES = "status_minutes"
HOURS = "status_hours"
INCIDENTS = "status_incidents"
META = "status_meta"
PAGES = "status_pages"

UP, DEGRADED, DOWN = "up", "degraded", "down"
STATES = (UP, DEGRADED, DOWN)
HOUR, DAY = 3600, 86400
WINDOWS = {"day": DAY, "week": 7 * DAY, "month": 30 * DAY}
SERVER = "server"

_LOCK = threading.RLock()


@dataclass(frozen=True)
class Result:
    """One check of one target."""

    state: str  # up, degraded, down
    detail: str = ""
    ms: float | None = None  # the response time, for an instance


@dataclass(frozen=True)
class Change:
    """What one recorded check changed: the state before and after, and the incident opened or closed."""

    target: str
    before: str | None
    after: str
    incident: Document | None = None


# ---------------------------------------------------------------- keys


def hour_start(at: float) -> int:
    return int(at // HOUR * HOUR)


def day_start(at: float) -> int:
    return int(at // DAY * DAY)


def _minute_key(target: str, at: float) -> str:
    return f"{target}|{hour_start(at)}"


def _hour_key(target: str, at: float) -> str:
    return f"{target}|{day_start(at)}"


# ---------------------------------------------------------------- recording


def _minute_up(entry: list[float] | None) -> bool | None:
    """A minute's state from its [checks, ok, ...]: None when nothing was checked in it."""
    if not entry or not entry[0]:
        return None
    return entry[1] >= entry[0]


def record(target: str, result: Result, now: float | None = None) -> Change:
    """Record one check: its minute, its hour, and the target's state (an incident opens or closes)."""
    now = time.time() if now is None else now
    minute = str(int(now % HOUR // 60))
    ok = result.state in (UP, DEGRADED)
    ms = result.ms if result.ms is not None and result.ms >= 0 else None
    seen: dict[str, bool | None] = {}

    def add_minute(found: Document | None) -> Document:
        doc = found or {"target": target, "hour": hour_start(now), "m": {}}
        entry = list(doc["m"].get(minute) or [0, 0, 0.0, 0])
        seen["before"] = _minute_up(entry)
        entry[0] += 1
        entry[1] += 1 if ok else 0
        if ms is not None:
            entry[2] = round(entry[2] + ms, 1)
            entry[3] += 1
        seen["after"] = _minute_up(entry)
        doc["m"] = {**doc["m"], minute: entry}
        return doc

    def add_hour(found: Document | None) -> Document:
        doc = found or {"target": target, "day": day_start(now), "h": {}}
        hour = str(int(now % DAY // HOUR))
        entry = list(doc["h"].get(hour) or [0, 0, 0.0, 0, 0])
        before, after = seen.get("before"), seen.get("after")
        if before is None:
            entry[0] += 1
            entry[1] += 1 if after else 0
        elif before and not after:
            entry[1] -= 1
        if ms is not None:
            entry[2] = round(entry[2] + ms, 1)
            entry[3] += 1
        if result.state == DEGRADED and before is None:
            entry[4] += 1
        doc["h"] = {**doc["h"], hour: entry}
        return doc

    with _LOCK:
        storage().change(MINUTES, _minute_key(target, now), add_minute)
        storage().change(HOURS, _hour_key(target, now), add_hour)
        return _track(target, result, now)


def _track(target: str, result: Result, now: float) -> Change:
    meta = storage().get(META, target) or {"target": target, "first": now}
    before = meta.get("state")
    incident: Document | None = None
    if result.state != UP and not meta.get("incident"):
        incident = {
            "id": uuid.uuid4().hex[:12],
            "target": target,
            "start": now,
            "end": None,
            "severity": result.state,
            "what": result.detail[:300],
        }
        storage().put(INCIDENTS, incident["id"], incident)
        meta["incident"] = incident["id"]
    elif result.state != UP:
        found = storage().get(INCIDENTS, meta["incident"])
        if found is not None and result.state == DOWN and found.get("severity") != DOWN:
            found.update(severity=DOWN, what=result.detail[:300] or found.get("what", ""))
            storage().put(INCIDENTS, found["id"], found)
    elif meta.get("incident"):
        found = storage().get(INCIDENTS, meta["incident"])
        if found is not None:
            found["end"] = now
            storage().put(INCIDENTS, found["id"], found)
            incident = found
        meta["incident"] = None
    if before != result.state:
        meta["since"] = now
    meta.update(state=result.state, detail=result.detail[:300], at=now, ms=result.ms)
    meta["fails"] = 0 if result.state != DOWN else int(meta.get("fails") or 0) + 1
    storage().put(META, target, meta)
    return Change(target, before, result.state, incident)


def meta(target: str) -> Document | None:
    return storage().get(META, target)


# ---------------------------------------------------------------- reading


def _hours(target: str, start: float, end: float) -> Iterable[tuple[int, list[float]]]:
    """(hour start, [minutes, minutes up, ms sum, ms count, degraded]) of every hour of `target` between start and end."""
    day = day_start(start)
    while day <= end:
        doc = storage().get(HOURS, f"{target}|{day}")
        for hour, entry in (doc or {}).get("h", {}).items():
            at = day + int(hour) * HOUR
            if start - HOUR < at <= end:
                yield at, entry
        day += DAY


def _minutes(target: str, start: float, end: float) -> Iterable[tuple[int, list[float]]]:
    """(minute start, [checks, ok, ms sum, ms count]) of every checked minute of `target` between start and end."""
    hour = hour_start(start)
    while hour <= end:
        doc = storage().get(MINUTES, f"{target}|{hour}")
        for minute, entry in (doc or {}).get("m", {}).items():
            at = hour + int(minute) * 60
            if start <= at <= end:
                yield at, entry
        hour += HOUR


def percent(up: float, total: float) -> float | None:
    """Uptime in %, rounded down to 3 decimals (99.9996 % is not 100 %); None when nothing counts."""
    if total <= 0:
        return None
    share = max(0.0, min(1.0, up / total))
    return int(share * 100_000) / 1000


def uptime(target: str, window: float, now: float | None = None) -> float | None:
    """Uptime of `target` in % over the last `window` seconds: per minute over the last day (STATUS_MINUTE_HOURS
    keeps them), per hour beyond. For the server, minutes without a check since its first one count as down."""
    now = time.time() if now is None else now
    start = now - window
    if window <= settings.status_minute_hours * HOUR:
        rows = list(_minutes(target, start, now))
        checked = len(rows)
        up = sum(1 for _, entry in rows if _minute_up(entry))
    else:
        hours = list(_hours(target, start, now))
        checked = int(sum(entry[0] for _, entry in hours))
        up = int(sum(entry[1] for _, entry in hours))
    if target == SERVER:
        first = (meta(target) or {}).get("first")
        if first is not None:
            expected = int((now - max(start, float(first))) // 60)
            checked = max(checked, expected)
    return percent(up, checked)


def days(target: str, count: int = 30, now: float | None = None) -> list[dict[str, Any]]:
    """The last `count` UTC days of `target`, oldest first: {"day", "uptime", "avg_ms", "degraded"}; uptime None
    for a day without checks."""
    now = time.time() if now is None else now
    today = day_start(now)
    found: list[dict[str, Any]] = []
    for offset in range(count - 1, -1, -1):
        day = today - offset * DAY
        doc = storage().get(HOURS, f"{target}|{day}") or {}
        entries = list(doc.get("h", {}).values())
        checked = sum(e[0] for e in entries)
        up = sum(e[1] for e in entries)
        ms_sum, ms_count = sum(e[2] for e in entries), sum(e[3] for e in entries)
        found.append(
            {
                "day": day,
                "uptime": percent(up, checked),
                "avg_ms": round(ms_sum / ms_count) if ms_count else None,
                "degraded": int(sum(e[4] for e in entries)),
            }
        )
    return found


def hourly_ms(target: str, now: float | None = None) -> list[float | None]:
    """The average response time of each of the last 24 hours (oldest first), None for an hour without one."""
    now = time.time() if now is None else now
    by_hour = dict(_hours(target, now - DAY, now))
    found: list[float | None] = []
    for offset in range(23, -1, -1):
        entry = by_hour.get(hour_start(now) - offset * HOUR)
        found.append(round(entry[2] / entry[3]) if entry and entry[3] else None)
    return found


def incidents(targets: Iterable[str], limit: int = 50, since: float | None = None) -> list[Document]:
    """The incidents of these targets, newest first."""
    wanted = set(targets)
    found = [i for i in storage().all(INCIDENTS).values() if i.get("target") in wanted]
    if since is not None:
        found = [i for i in found if (i.get("end") or time.time()) >= since]
    return sorted(found, key=lambda i: i["start"], reverse=True)[:limit]


def overall(states: Mapping[str, str | None], essential: Iterable[str]) -> str:
    """The banner: "down" when an essential target (a production instance) is down, "degraded" when any target is
    not up, "operational" when all are up, "unknown" before the first check."""
    known = {target: state for target, state in states.items() if state}
    if not known:
        return "unknown"
    if any(known.get(target) == DOWN for target in essential):
        return "down"
    if any(state != UP for state in known.values()):
        return "degraded"
    return "operational"


# ---------------------------------------------------------------- keeping it small


def prune(now: float | None = None) -> int:
    """Remove minutes older than STATUS_MINUTE_HOURS, hours and closed incidents older than STATUS_KEEP_DAYS.
    Returns how many documents went."""
    now = time.time() if now is None else now
    removed = 0
    minutes_until = now - settings.status_minute_hours * HOUR - HOUR
    days_until = now - settings.status_keep_days * DAY - DAY
    for key, doc in storage().all(MINUTES).items():
        if float(doc.get("hour", 0)) < minutes_until:
            removed += storage().delete(MINUTES, key)
    for key, doc in storage().all(HOURS).items():
        if float(doc.get("day", 0)) < days_until:
            removed += storage().delete(HOURS, key)
    for key, doc in storage().all(INCIDENTS).items():
        if doc.get("end") is not None and float(doc["end"]) < days_until:
            removed += storage().delete(INCIDENTS, key)
    return removed


def forget(target: str) -> None:
    """A target that is gone (its instance was deleted): its open incident is closed, its state removed."""
    found = meta(target)
    if found and found.get("incident"):
        incident = storage().get(INCIDENTS, found["incident"])
        if incident is not None and incident.get("end") is None:
            incident["end"] = time.time()
            storage().put(INCIDENTS, incident["id"], incident)
    storage().delete(META, target)


# ---------------------------------------------------------------- extension point


OffsiteLast = Callable[[str], float | None]
offsite_last: OffsiteLast | None = None
"""Set by the off-site copies package: when the last off-site copy of an instance succeeded (None: never). Without
it, no off-site row is shown."""


def use_offsite(provider: OffsiteLast | None) -> None:
    global offsite_last
    offsite_last = provider
