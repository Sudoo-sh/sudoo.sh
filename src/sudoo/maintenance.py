"""Maintenance windows: work Sudoo.sh plans on this panel, at a time the panel's people (or their clients) choose.

Sudoo.sh staff plan maintenance that needs a restart or downtime (an update of the panel to a new version, new Odoo
images, a restart, other work on the server) on hub.sudoo.sh. The hub tells the panel through what it already signs:

    the command "maintenance"   sent with every heartbeat answer until the panel acknowledges it: done when the item
                                is recorded here, failed (with the reason) when the allow-list below refuses it
    the lease's "maintenance"   every open item of this server with its status and the window the account's owner
                                may have chosen on app.sudoo.sh; cancelled items stay listed for a few days

Nothing else from the hub is ever run: an item names one of KINDS and one of SCOPES (ALLOWED pairs only), names its
targets (instances, projects or servers of this panel) and a version for a panel update. The panel only maps them to
its own jobs (instances/operations.py). Any other value refuses the whole item.

An item (storage collection "maintenance", one document per item; the hub's id)
    definition  kind, scope, targets, version, reason {"en", "ar"}, estimated_minutes, earliest_start, deadline (the
                latest start, Unix time), required
    status      proposed (no time yet) -> scheduled -> running -> done | failed; cancelled (by Sudoo.sh, or declined
                by the account's owner when it is optional)
    window      window_start, window_end (start + estimated_minutes), window_source (panel: an administrator;
                client: a client in the portal; account: the owner on app.sudoo.sh; default: chosen by the panel),
                chosen_by, chosen_at
    approvals   {project: {"by", "at"}}: the clients who let it run on their closed or locked project
    reminders   hours before the start already reminded (MAINTENANCE_REMINDER_HOURS)
    steps       [{"name", "instance", "stage", "status", "job", "detail"}]: what the run did, one instance at a time
    outbox      reports for the hub not sent yet (POST /api/v1/maintenance/<id>/report, signed)

Choosing the time
    Anyone with maintenance.manage (super admins and admins) chooses a window in [max(earliest_start, now + MAINTENANCE_LEAD_MINUTES), deadline]:
    tonight (MAINTENANCE_TONIGHT_TIME), the weekend (MAINTENANCE_WEEKEND_DAY at the same time), the default window
    (MAINTENANCE_DEFAULT_WINDOW on MAINTENANCE_DEFAULT_DAYS) or any time. Clients of affected projects may choose too
    when MAINTENANCE_CLIENT_CHOICE is on (confirmed by e-mail like their other major changes). When nobody chose
    within MAINTENANCE_CHOOSE_HOURS of its arrival, or MAINTENANCE_CUTOFF_HOURS before the deadline (whichever comes
    first), the panel picks the first default window that fits. Times are shown in TIMEZONE.

Running it
    One item at a time, at its window. A panel update waits for the new version (sudoo-install update on the server,
    asked for in <manager>/update-request.json) up to MAINTENANCE_UPDATE_WAIT_HOURS; a restart of the panel restarts
    the backend once no job runs (instances keep serving); other work is done by Sudoo.sh during the window
    (custom). Instance work runs through the usual jobs, one instance at a time: development first, then staging,
    production last; a backup first for production and staging before new images (MAINTENANCE_BACKUP_FIRST); Odoo
    must answer afterwards. An instance whose branch its client closed or locked is skipped unless that client
    approved this item; a branch the panel's people locked is skipped too. After a failure no production instance is
    touched. The Cloudflare connectors are never started, stopped or recreated here.

Everything is audited; administrators (and the clients of affected projects) are e-mailed when it is planned, before
it starts (reminders) and when it ends.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from datetime import time as clock
from typing import Any

from sudoo import accounts
from sudoo.config import settings
from sudoo.core import audit
from sudoo.storage import Document, storage

log = logging.getLogger(__name__)

COLLECTION = "maintenance"
COMMAND = "maintenance"
KINDS = ("panel_update", "odoo_images", "restart", "custom")
SCOPES = ("panel", "server", "instance", "project")
ALLOWED: dict[str, tuple[str, ...]] = {
    "panel_update": ("panel",),
    "odoo_images": ("server", "instance", "project"),
    "restart": ("panel", "server", "instance", "project"),
    "custom": ("panel", "server", "instance", "project"),
}
"""The only work the hub may ask for, and where: anything else refuses the whole item."""
STATUSES = ("proposed", "scheduled", "running", "done", "failed", "cancelled")
OPEN = ("proposed", "scheduled", "running")
CHOOSABLE = ("proposed", "scheduled")
SOURCES = ("panel", "client", "account", "default")
PRESETS = ("tonight", "weekend", "default")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$")
MAX_TARGETS = 50
REASON_MAX = 1000
DETAIL_MAX = 1000
FORGET_DAYS = 90
SHOWN_DAYS = 30
WHO = "maintenance"  # the "user" of jobs and audit entries the panel makes by itself
REQUEST_FILE = "update-request.json"
TITLES = {
    "panel_update": ("Update the panel to version {version}", "تحديث اللوحة إلى الإصدار {version}"),
    "odoo_images": ("Update the Odoo images", "تحديث صور Odoo"),
    "restart": ("Restart", "إعادة تشغيل"),
    "custom": ("Maintenance work", "أعمال صيانة"),
}
_LOCK = threading.RLock()
_RUNNING = threading.Lock()  # one item runs at a time
_WAKE = threading.Event()


class Refused(Exception):
    """The hub asked for something this panel never does (the allow-list); the message says what."""


class MaintenanceError(Exception):
    """A choice that is not possible; the message is meant for people."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- the allow-list


def _int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Refused(f"{name} is not a time.")
    return value


def _reason(value: Any, language: str) -> str:
    text = value.get(language) if isinstance(value, Mapping) else None
    if not isinstance(text, str) or not text.strip():
        raise Refused(f"The reason in {'English' if language == 'en' else 'Arabic'} is missing.")
    text = "".join(ch for ch in text.strip() if ch in "\n\t" or ord(ch) >= 32)
    if len(text) > REASON_MAX:
        raise Refused("The reason is too long.")
    return text


def clean(raw: Any) -> dict[str, Any]:
    """The definition of an item from what the hub sent (the command's payload, or an item of the lease), checked
    against the allow-list; raises Refused. Only these keys are kept."""
    if not isinstance(raw, Mapping):
        raise Refused("The maintenance is not described.")
    try:
        item_id = str(uuid.UUID(str(raw.get("maintenance_id") or raw.get("id") or "")))
    except ValueError:
        raise Refused("The maintenance has no valid id.") from None
    kind, scope = raw.get("kind"), raw.get("scope")
    if kind not in KINDS:
        raise Refused(f"This panel does not do maintenance of the kind {str(kind)[:40]!r}.")
    if scope not in ALLOWED[str(kind)]:
        raise Refused(f"This panel does not do {kind} on the scope {str(scope)[:40]!r}.")
    targets = raw.get("targets") or []
    if not isinstance(targets, list) or len(targets) > MAX_TARGETS:
        raise Refused("The targets are not a list of names.")
    if not all(isinstance(t, str) and TARGET_RE.match(t) for t in targets):
        raise Refused("A target is not a valid name.")
    targets = list(dict.fromkeys(targets))
    if (scope == "panel") != (not targets):
        raise Refused("The panel itself takes no targets; every other scope names at least one.")
    version = raw.get("version")
    if kind == "panel_update":
        if not isinstance(version, str) or not VERSION_RE.match(version) or len(version) > 32:
            raise Refused("A panel update names the version to update to.")
    elif version is not None:
        raise Refused("Only a panel update names a version.")
    minutes = raw.get("estimated_minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 5 <= minutes <= 1440:
        raise Refused("The estimated duration must be 5 to 1440 minutes.")
    earliest, deadline = _int(raw.get("earliest_start"), "earliest_start"), _int(raw.get("deadline"), "deadline")
    if deadline < earliest:
        raise Refused("The deadline is before the earliest start.")
    if not isinstance(raw.get("required"), bool):
        raise Refused("Whether the maintenance is required is not said.")
    return {
        "id": item_id,
        "kind": kind,
        "scope": scope,
        "targets": targets,
        "version": version if kind == "panel_update" else None,
        "reason": {"en": _reason(raw.get("reason"), "en"), "ar": _reason(raw.get("reason"), "ar")},
        "estimated_minutes": minutes,
        "earliest_start": earliest,
        "deadline": deadline,
        "required": bool(raw["required"]),
    }


def title(item: Mapping[str, Any], language: str = "en") -> str:
    english, arabic = TITLES.get(str(item.get("kind")), TITLES["custom"])
    return (arabic if language == "ar" else english).format(version=item.get("version") or "")


# ---------------------------------------------------------------- reading


def get(item_id: str) -> Document | None:
    return storage().get(COLLECTION, item_id) if item_id else None


def items(shown_days: int = SHOWN_DAYS, now: float | None = None) -> list[Document]:
    """Open items and those that ended within `shown_days`, soonest first."""
    now = time.time() if now is None else now
    found = [
        doc
        for doc in storage().all(COLLECTION).values()
        if doc.get("status") in OPEN
        or float(doc.get("finished_at") or doc.get("received_at") or 0) > now - shown_days * 86400
    ]
    return sorted(
        found, key=lambda d: (d.get("status") not in OPEN, float(d.get("window_start") or d.get("earliest_start") or 0))
    )


def affected(item: Mapping[str, Any]) -> list[str]:
    """The instances the item works on (none for the panel itself), on every server."""
    from sudoo import projects
    from sudoo.premium import servers

    scope, targets = item.get("scope"), list(item.get("targets") or [])
    if scope == "instance":
        return [name for name in targets if servers.exists(name)]
    if scope == "project":
        return sorted({name for project_id in targets if projects.ID_RE.match(project_id)
                       for name in projects.instances(project_id)})  # fmt: skip
    if scope == "server":
        return sorted(name for name in servers.names() if servers.server_of(name) in targets)
    return []


def _project_of(name: str) -> tuple[str | None, str | None]:
    from sudoo.premium import servers

    env = servers.env(name)
    return (env.get("PROJECT") or None), (env.get("GIT_BRANCH") or None)


def projects_of(item: Mapping[str, Any]) -> list[str]:
    """The projects whose instances the item works on."""
    return sorted({p for p in (_project_of(name)[0] for name in affected(item)) if p})


def _stage(name: str) -> str:
    from sudoo.premium import servers

    return servers.env(name).get("STAGE") or "development"


def running_on(name: str) -> bool:
    """Whether a maintenance is working on instance `name` now (the gateway's page says so)."""
    return any(doc.get("status") == "running" and name in affected(doc) for doc in storage().all(COLLECTION).values())


def public_notices(project_id: str, now: float | None = None) -> list[dict[str, Any]]:
    """What a project's public status page shows: maintenance that is running, or scheduled within 14 days, on its
    instances. Only the kind and the time (the reason is for the panel's people and clients)."""
    from sudoo import projects

    now = time.time() if now is None else now
    names = set(projects.instances(project_id))
    shown = []
    for doc in items(0, now):
        start = float(doc.get("window_start") or 0)
        upcoming = doc.get("status") == "scheduled" and now <= start <= now + 14 * 86400
        if (doc.get("status") == "running" or upcoming) and names & set(affected(doc)):
            shown.append({
                "kind": doc["kind"],
                "title": {"en": title(doc), "ar": title(doc, "ar")},
                "status": doc["status"],
                "window_start": iso(doc.get("window_start")),
                "window_end": iso(doc.get("window_end")),
            })  # fmt: skip
    return shown


# ---------------------------------------------------------------- time


def iso(at: Any) -> str | None:
    if not at:
        return None
    return datetime.fromtimestamp(float(at), settings.zone).isoformat(timespec="seconds")


def when(at: Any) -> str:
    """A time for e-mails: 2026-11-29 02:00 (Africa/Cairo)."""
    if not at:
        return ""
    return f"{datetime.fromtimestamp(float(at), settings.zone):%Y-%m-%d %H:%M} ({settings.timezone})"


def _hhmm(text: str) -> clock:
    hours, minutes = text.strip().split(":")
    return clock(int(hours), int(minutes))


def default_window() -> tuple[clock, int]:
    """(start, length in seconds) of the default daily window; it may cross midnight."""
    first, last = (part.strip() for part in str(settings.maintenance_default_window).split("-"))
    start, end = _hhmm(first), _hhmm(last)
    length = ((end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)) % (24 * 60) or 24 * 60
    return start, length * 60


def _days(text: str) -> set[int]:
    return {DAYS.index(day.strip()) for day in text.split(",") if day.strip() in DAYS}


def next_at(at: clock, after: float, until: float, weekdays: Iterable[int] | None = None) -> float | None:
    """The first time `at` (in TIMEZONE) on one of `weekdays` that is >= after and <= until; None when there is none."""
    allowed = set(range(7) if weekdays is None else weekdays)
    if not allowed or until < after:
        return None
    day = datetime.fromtimestamp(after, settings.zone).date()
    for offset in range(0, 400):
        candidate = datetime.combine(day + timedelta(days=offset), at, tzinfo=settings.zone)
        stamp = candidate.timestamp()
        if stamp > until:
            return None
        if stamp >= after and candidate.weekday() in allowed:
            return stamp
    return None


def bounds(item: Mapping[str, Any], now: float | None = None) -> tuple[float, float]:
    """(earliest, latest) start someone may choose now."""
    now = time.time() if now is None else now
    lead = int(settings.maintenance_lead_minutes) * 60
    return max(float(item["earliest_start"]), now + lead), float(item["deadline"])


def default_start(item: Mapping[str, Any], now: float | None = None) -> float | None:
    """The start of the first default window that fits: inside today's window when the work still fits in it,
    else the next window's start."""
    lo, hi = bounds(item, now)
    start, length = default_window()
    weekdays = _days(str(settings.maintenance_default_days))
    opened = next_at(start, lo - length, hi, weekdays)
    if opened is not None and opened < lo and lo + int(item["estimated_minutes"]) * 60 <= opened + length and lo <= hi:
        return lo
    return next_at(start, lo, hi, weekdays)


def presets(item: Mapping[str, Any], now: float | None = None) -> list[dict[str, Any]]:
    """The quick choices: tonight, the weekend and the default window, each where it fits."""
    lo, hi = bounds(item, now)
    night = _hhmm(str(settings.maintenance_tonight_time))
    weekend = DAYS.index(str(settings.maintenance_weekend_day))
    starts = {
        "tonight": next_at(night, lo, hi),
        "weekend": next_at(night, lo, hi, [weekend]),
        "default": default_start(item, now),
    }
    minutes = int(item["estimated_minutes"])
    return [
        {"id": key, "start": iso(found), "end": iso(found + minutes * 60)}
        for key, found in starts.items()
        if found is not None
    ]


def auto_at(item: Mapping[str, Any]) -> float:
    """When the default window applies if nobody has chosen a time."""
    received = float(item.get("received_at") or 0)
    return min(
        received + int(settings.maintenance_choose_hours) * 3600,
        float(item["deadline"]) - int(settings.maintenance_cutoff_hours) * 3600,
    )


# ---------------------------------------------------------------- receiving from the hub


def _change(item_id: str, fn: Any) -> Document | None:
    with _LOCK:
        return storage().change(COLLECTION, item_id, fn)


def _record(definition: dict[str, Any], command_id: str | None, now: float) -> tuple[Document, bool]:
    """Store a new item (or give back the one already stored); True when it is new."""
    made: list[bool] = []

    def add(current: Document | None) -> Document:
        if current is not None:
            if command_id and not current.get("command_id"):
                return {**current, "command_id": command_id}
            return current
        made.append(True)
        return {
            **definition,
            "status": "proposed",
            "received_at": now,
            "command_id": command_id,
            "window_start": None,
            "window_end": None,
            "window_source": None,
            "chosen_by": None,
            "chosen_at": None,
            "approvals": {},
            "reminders": [],
            "steps": [],
            "detail": None,
            "started_at": None,
            "finished_at": None,
            "outbox": [],
        }

    found = _change(definition["id"], add)
    assert found is not None
    return found, bool(made)


def receive(command_id: str, payload: Any, now: float | None = None) -> tuple[str, str | None]:
    """The hub's signed command "maintenance": record the item. ("done", None), or ("failed", why) when the
    allow-list refuses it (the hub then marks it failed)."""
    now = time.time() if now is None else now
    try:
        definition = clean(payload)
    except Refused as error:
        audit.record(WHO, None, "maintenance refused", ok=False, detail=str(error))
        return "failed", str(error)
    found, new = _record(definition, command_id, now)
    if new:
        _announced(found)
    _WAKE.set()
    return "done", None


def _announced(item: Document) -> None:
    audit.record(WHO, None, "maintenance announced",
                 detail=f"{title(item)} ({item['scope']}: {', '.join(item['targets']) or 'the panel'}), "
                        f"by {when(item['deadline'])}")  # fmt: skip
    _mail(item, "maintenance_planned")


def sync(claimed: Any, now: float | None = None) -> None:
    """The lease's "maintenance" claim (signed with the lease): items the command did not bring yet, cancellations,
    and a window the account's owner chose on app.sudoo.sh."""
    now = time.time() if now is None else now
    for raw in claimed if isinstance(claimed, list) else []:
        try:
            definition = clean(raw)
        except Refused as error:
            log.warning("maintenance: an item of the lease was ignored: %s", error)
            continue
        status = raw.get("status")
        current = get(definition["id"])
        if current is None:
            if status in ("proposed", "scheduled"):
                current, _ = _record(definition, None, now)
                _announced(current)
            else:
                continue
        if status == "cancelled" and current.get("status") in CHOOSABLE:
            _cancel(current, now)
        elif status == "failed" and current.get("status") in CHOOSABLE:
            _finish(current["id"], "failed", "Sudoo.sh marked it as failed.", now, report=False)
        elif raw.get("window_source") == "account" and current.get("status") in CHOOSABLE:
            _adopt_account_window(current, raw, now)


def _adopt_account_window(current: Document, raw: Mapping[str, Any], now: float) -> None:
    start = raw.get("window_start")
    if not isinstance(start, int) or isinstance(start, bool):
        return
    updated = raw.get("updated_at")
    changed_at = float(updated) if isinstance(updated, int) else now
    if current.get("window_start") == start or float(current.get("chosen_at") or 0) >= float(changed_at):
        return
    if any(r.get("status") == "scheduled" for r in current.get("outbox") or []):
        return  # a choice made here is on its way to the hub: it is the newer one
    if not float(current["earliest_start"]) <= start <= float(current["deadline"]):
        return
    who = str(raw.get("chosen_by") or "the account's owner")[:200]
    _set_window(current["id"], start, "account", who, now, report=False)
    audit.record(WHO, None, "maintenance window chosen on app.sudoo.sh", detail=f"{title(current)}: {when(start)}")


def _cancel(current: Document, now: float) -> None:
    def apply(doc: Document | None) -> Document | None:
        if doc is None or doc.get("status") not in CHOOSABLE:
            return doc
        return {**doc, "status": "cancelled", "finished_at": now, "outbox": []}

    _change(current["id"], apply)
    audit.record(WHO, None, "maintenance cancelled", detail=title(current))
    _mail(get(current["id"]) or current, "maintenance_result")


# ---------------------------------------------------------------- choosing the time


def _set_window(item_id: str, start: float, source: str, chosen_by: str | None, now: float,
                report: bool = True) -> Document:  # fmt: skip

    def apply(doc: Document | None) -> Document | None:
        if doc is None or doc.get("status") not in CHOOSABLE:
            return doc
        changed = {
            **doc,
            "status": "scheduled",
            "window_start": round(start),
            "window_end": round(start) + int(doc["estimated_minutes"]) * 60,
            "window_source": source,
            "chosen_by": chosen_by,
            "chosen_at": now,
            "reminders": [],
        }
        if report:
            changed = _queued(changed, _report_body(changed, "scheduled"))
        return changed

    found = _change(item_id, apply)
    if found is None:
        raise MaintenanceError("There is no such maintenance.", 404)
    _WAKE.set()
    return found


def start_of(item: Mapping[str, Any], preset: str | None, start: Any, now: float | None = None) -> float:
    """The start asked for (a preset, or an ISO time); raises MaintenanceError when it is not allowed."""
    now = time.time() if now is None else now
    if preset:
        found = next((p for p in presets(item, now) if p["id"] == preset), None)
        if found is None:
            raise MaintenanceError("This choice does not fit before the deadline: choose a time.", 422)
        return datetime.fromisoformat(found["start"]).timestamp()
    try:
        moment = datetime.fromisoformat(str(start or "").replace("Z", "+00:00"))
    except ValueError:
        raise MaintenanceError("Give a start time, such as 2026-11-29T02:00.", 400) from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=settings.zone)
    return moment.timestamp()


def choose(item_id: str, start: float, by: str, source: str = "panel", project: str | None = None,
           now: float | None = None) -> Document:  # fmt: skip
    """Someone chose when the item runs (an administrator, or a client of `project`); raises MaintenanceError."""
    now = time.time() if now is None else now
    item = get(item_id)
    if item is None:
        raise MaintenanceError("There is no such maintenance.", 404)
    if item.get("status") == "running":
        raise MaintenanceError("This maintenance is running already.")
    if item.get("status") not in CHOOSABLE:
        raise MaintenanceError("This maintenance has ended.")
    lo, hi = bounds(item, now)
    if not lo <= start <= hi:
        raise MaintenanceError(f"Choose a start between {when(lo)} and {when(hi)}.", 422)
    who = accounts.display_name(by, accounts.get(by) or {})
    if source == "client" and project:
        approve(item_id, project, by, now, quiet=True)
    found = _set_window(item_id, start, source, who, now)
    audit.record(by, _ip(), "maintenance window chosen",
                 detail=f"{project + ': ' if project else ''}{title(item)}: {when(start)}")  # fmt: skip
    return found


def approve(item_id: str, project: str, by: str, now: float | None = None, quiet: bool = False) -> Document:
    """A client lets the item run on its project while the project (or a branch of it) is closed or locked."""
    now = time.time() if now is None else now

    def apply(doc: Document | None) -> Document | None:
        if doc is None:
            return None
        approvals = {**(doc.get("approvals") or {}), project: {"by": by, "at": now}}
        return {**doc, "approvals": approvals}

    found = _change(item_id, apply)
    if found is None:
        raise MaintenanceError("There is no such maintenance.", 404)
    if not quiet:
        audit.record(by, _ip(), "maintenance approved by the client", detail=f"{project}: {title(found)}")
    return found


# ---------------------------------------------------------------- reports to the hub


def _report_body(item: Mapping[str, Any], status: str) -> dict[str, Any]:
    body: dict[str, Any] = {"status": status}
    if status == "scheduled":
        source = item.get("window_source")
        body.update(
            window_start=datetime.fromtimestamp(float(item["window_start"]), settings.zone).isoformat(),
            window_end=datetime.fromtimestamp(float(item["window_end"]), settings.zone).isoformat(),
            window_source="default" if source == "default" else "panel",
        )
        if item.get("chosen_by"):
            suffix = " (client)" if source == "client" else ""
            body["chosen_by"] = f"{item['chosen_by']}{suffix}"[:200]
    if item.get("steps"):
        body["steps"] = [{"name": str(s.get("name"))[:200], "status": s.get("status")} for s in item["steps"][:100]]
    if status in ("done", "failed") and item.get("detail"):
        body["detail"] = str(item["detail"])[:DETAIL_MAX]
    return body


def _queued(doc: Document, body: dict[str, Any]) -> Document:
    """`doc` with `body` waiting to be sent; a newer report of the same status replaces the older one."""
    outbox = [r for r in doc.get("outbox") or [] if r.get("status") != body["status"]]
    return {**doc, "outbox": [*outbox, body][-10:]}


def flush() -> int:
    """Send the reports that wait; returns how many the hub took. Reports the hub refuses for good are dropped."""
    from sudoo.hub import HubRefused, HubUnavailable, client, marketplace

    if not marketplace.enabled():
        return 0
    sent = 0
    for doc in storage().all(COLLECTION).values():
        for body in list(doc.get("outbox") or []):
            try:
                client.request("POST", f"/api/v1/maintenance/{doc['id']}/report", body)
            except HubUnavailable as error:
                log.info("maintenance: report not sent yet: %s", error)
                return sent
            except HubRefused as error:
                log.warning("maintenance: the hub refused a report of %s: %s", doc["id"], error.detail)
            else:
                sent += 1
            _drop_report(str(doc["id"]), body)
    return sent


def _drop_report(item_id: str, body: Mapping[str, Any]) -> None:
    def apply(doc: Document | None) -> Document | None:
        if doc is None:
            return None
        outbox = list(doc.get("outbox") or [])
        if body in outbox:
            outbox.remove(dict(body))
        return {**doc, "outbox": outbox}

    _change(item_id, apply)


# ---------------------------------------------------------------- the background loop


def tick(now: float | None = None) -> None:
    """One round: default windows, reminders, starting what is due, finishing what waits, reports."""
    now = time.time() if now is None else now
    for doc in list(storage().all(COLLECTION).values()):
        try:
            _advance(doc, now)
        except Exception:  # noqa: BLE001 - one item never stops the others
            log.exception("maintenance %s", doc.get("id"))
    try:
        flush()
    except Exception:  # noqa: BLE001 - tried again at the next round
        log.exception("maintenance: reports")
    _forget(now)


def _advance(doc: Document, now: float) -> None:
    status = doc.get("status")
    if status == "proposed" and now >= auto_at(doc):
        _apply_default(doc, now)
    elif status == "scheduled":
        _remind(doc, now)
        if now >= float(doc.get("window_start") or 0):
            start_run(str(doc["id"]))
    elif status == "running":
        if doc["kind"] == "custom" and now >= float(doc.get("window_end") or 0):
            _finish(doc["id"], "done", "The maintenance window ended.", now)
        elif doc["kind"] == "panel_update":
            _check_update(doc, now)


def _apply_default(doc: Document, now: float) -> None:
    start = default_start(doc, now)
    if start is None:
        lo, hi = bounds(doc, now)
        if lo <= hi:
            start = lo
        elif doc.get("required"):
            start = now + int(settings.maintenance_lead_minutes) * 60  # past the deadline already: as soon as possible
        else:
            _finish(doc["id"], "failed", "Nobody chose a time and the deadline passed.", now)
            return
    _set_window(doc["id"], start, "default", None, now)
    audit.record(WHO, None, "maintenance window chosen by default", detail=f"{title(doc)}: {when(start)}")
    found = get(doc["id"])
    if found is not None:
        _mail(found, "maintenance_reminder", default=True)


def reminder_hours() -> list[float]:
    return sorted({float(h) for h in str(settings.maintenance_reminder_hours).split(",") if h.strip()}, reverse=True)


def _remind(doc: Document, now: float) -> None:
    start = float(doc.get("window_start") or 0)
    sent = set(doc.get("reminders") or [])
    due = [h for h in reminder_hours() if start - h * 3600 <= now < start and h not in sent]
    if not due:
        return

    def apply(current: Document | None) -> Document | None:
        if current is None:
            return None
        return {**current, "reminders": sorted({*(current.get("reminders") or []), *due})}

    _change(doc["id"], apply)
    _mail(doc, "maintenance_reminder")  # one reminder, however many were due at once


def start_run(item_id: str) -> bool:
    """Run the item in a thread of its own, unless another one runs; True when it started."""
    if not _RUNNING.acquire(blocking=False):
        return False

    def go() -> None:
        try:
            run(item_id)
        finally:
            _RUNNING.release()

    threading.Thread(target=go, daemon=True, name=f"maintenance-{item_id[:8]}").start()
    return True


def _forget(now: float) -> None:
    for key, doc in list(storage().all(COLLECTION).items()):
        if (
            doc.get("status") not in OPEN
            and float(doc.get("finished_at") or doc.get("received_at") or 0) < now - FORGET_DAYS * 86400
        ):
            storage().delete(COLLECTION, key)


def resume(now: float | None = None) -> None:
    """At start-up: an item that was running when the panel stopped. A panel restart it asked for is done; instance
    work cut off by the restart failed (the instances' own jobs say how far they got)."""
    now = time.time() if now is None else now
    for doc in storage().all(COLLECTION).values():
        if doc.get("status") != "running" or doc["kind"] in ("custom", "panel_update"):
            continue
        if doc.get("restart_requested_at"):
            _finish(doc["id"], "done", "The panel restarted.", now, steps_status="done")
        else:
            _finish(doc["id"], "failed", "The panel stopped while the maintenance ran; check the instances.", now)


def run_forever() -> None:
    try:
        resume()
    except Exception:  # noqa: BLE001 - the loop still runs
        log.exception("maintenance: resuming")
    while True:
        try:
            tick()
        except Exception:  # noqa: BLE001 - the loop must survive anything
            log.exception("maintenance")
        _WAKE.wait(60)
        _WAKE.clear()


# ---------------------------------------------------------------- running


def _step(item_id: str, step: dict[str, Any]) -> None:
    def apply(doc: Document | None) -> Document | None:
        if doc is None:
            return None
        steps = [s for s in doc.get("steps") or [] if s.get("key") != step["key"]]
        changed = {**doc, "steps": [*steps, step]}
        return _queued(changed, _report_body(changed, "running"))

    _change(item_id, apply)


def run(item_id: str, now: float | None = None) -> Document | None:
    """Carry out a scheduled item now (the caller holds the one-at-a-time lock)."""
    now = time.time() if now is None else now

    def begin(doc: Document | None) -> Document | None:
        if doc is None or doc.get("status") != "scheduled":
            return doc
        changed = {**doc, "status": "running", "started_at": now, "steps": []}
        return _queued(changed, _report_body(changed, "running"))

    found = _change(item_id, begin)
    if found is None or found.get("status") != "running" or found.get("started_at") != now:
        return found
    audit.record(WHO, None, "maintenance started", detail=title(found))
    kind, scope = found["kind"], found["scope"]
    if kind not in KINDS or scope not in ALLOWED[kind]:  # checked when it arrived; never run anything else
        return _finish(item_id, "failed", "Refused: not in this panel's allow-list.", time.time())
    if kind == "custom":
        _step(item_id, {"key": "custom", "name": "Work done by Sudoo.sh during the window", "status": "running"})
        return get(item_id)
    if kind == "panel_update":
        return _request_update(found)
    if kind == "restart" and scope == "panel":
        return _restart_panel(found)
    return _instances_work(found)


def _request_update(item: Document) -> Document | None:
    version = str(item["version"])
    if _version_reached(version):
        return _finish(item["id"], "done", f"The panel runs version {version} already.", time.time(),
                       steps_status="done")  # fmt: skip
    request_file = settings.manager_dir / REQUEST_FILE
    request_file.write_text(
        json.dumps({"version": version, "maintenance": item["id"], "requested_at": round(time.time())})
    )
    _step(
        item["id"], {"key": "update", "name": f"Waiting for the panel to be updated to {version}", "status": "running"}
    )
    audit.record(WHO, None, "panel update requested", detail=f"version {version} (sudoo-install update)")
    return get(item["id"])


def _version_key(version: str) -> tuple[tuple[int, ...], str]:
    numbers = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", version)
    if not numbers:
        return (0, 0, 0), version
    return tuple(int(part) for part in numbers.groups()[:3]), numbers.group(4)


def _version_reached(version: str) -> bool:
    from sudoo.hub import client

    have, want = _version_key(client.sudoo_version()), _version_key(version)
    return have[0] > want[0] or (have[0] == want[0] and have[1] >= want[1])


def _check_update(doc: Document, now: float) -> None:
    version = str(doc["version"])
    if _version_reached(version):
        (settings.manager_dir / REQUEST_FILE).unlink(missing_ok=True)
        _finish(doc["id"], "done", f"The panel runs version {version}.", now, steps_status="done")
    elif now > float(doc.get("started_at") or now) + float(settings.maintenance_update_wait_hours) * 3600:
        (settings.manager_dir / REQUEST_FILE).unlink(missing_ok=True)
        _finish(doc["id"], "failed",
                f"The panel was not updated to {version} in time. Run sudoo-install update --version {version} on "
                "the server.", now)  # fmt: skip


def _restart_panel(item: Document) -> Document | None:
    from sudoo import plugin_manager

    def mark(doc: Document | None) -> Document | None:
        return None if doc is None else {**doc, "restart_requested_at": time.time()}

    _change(item["id"], mark)
    _step(item["id"], {"key": "panel", "name": "Restarting the panel (instances keep serving)", "status": "running"})
    flush()  # the hub hears "running" before the backend stops
    plugin_manager.schedule_restart(f"maintenance: {title(item)}")
    return get(item["id"])


def order(names: Iterable[str]) -> list[str]:
    """Development first, then staging, production last; by name within a stage."""
    rank = {"development": 0, "staging": 1, "production": 2}
    return sorted(names, key=lambda name: (rank.get(_stage(name), 1), name))


def hold(item: Mapping[str, Any], name: str) -> str | None:
    """Why the item may not touch instance `name` now (None: it may): another server, a branch its client closed or
    locked without approving this item, or a branch the panel's people locked."""
    from sudoo import projects
    from sudoo.instances import store
    from sudoo.projects import access

    if not store.exists(name):
        return "It is on another server, or no longer exists."
    project_id, branch = _project_of(name)
    project = projects.get(project_id) if project_id else None
    held = access.held(project, branch) if project is not None else None
    if held is None:
        return None
    if held.get("client"):
        if project_id in (item.get("approvals") or {}):
            return None
        return "Its client closed or locked it and has not approved this maintenance."
    return "Its branch is locked."


def _instances_work(item: Document) -> Document | None:
    from sudoo.core import jobs

    failed_once = False
    counts = {"done": 0, "failed": 0, "skipped": 0}
    for name in order(affected(item)):
        stage = _stage(name)
        step: dict[str, Any] = {"key": name, "name": name, "instance": name, "stage": stage, "status": "running"}
        why = hold(item, name)
        if why is None and failed_once and stage == "production":
            why = "Not started: an instance before it failed."
        if why is not None:
            _step(item["id"], {**step, "status": "skipped", "detail": why})
            audit.record(WHO, None, "maintenance skipped an instance", instance=name, detail=f"{title(item)}: {why}")
            counts["skipped"] += 1
            continue
        _step(item["id"], step)
        job = jobs.start(name, f"Maintenance: {title(item)}", "update" if item["kind"] == "odoo_images" else "restart",
                         work, name, item["kind"], bool(settings.maintenance_backup_first), user=WHO)  # fmt: skip
        _wait(job)
        ok = job.status == "success"
        failed_once = failed_once or not ok
        counts["done" if ok else "failed"] += 1
        _step(item["id"], {**step, "status": "done" if ok else "failed", "job": job.id,
                           "detail": None if ok else (job.hint or "The job failed.")})  # fmt: skip
    summary = ", ".join(f"{n} {word}" for word, n in counts.items() if n) or "No instance to work on"
    return _finish(item["id"], "failed" if counts["failed"] else "done", f"{summary}.", time.time())


def work(job: Any, name: str, kind: str, backup_first: bool) -> None:
    """The job of one instance: a backup first (production and staging, before new images), the work, and Odoo
    must answer afterwards. The instance's own job steps put it back where they can."""
    from sudoo.instances import backups, operations

    paused = operations._paused(name)
    if kind == "odoo_images":
        if backup_first and _stage(name) in ("production", "staging"):
            job.step("Backing up first")
            saved = backups.create(job, name, "manual", comment="Before maintenance")
            job.note(f"Backed up as {', '.join(p.name for p in saved)}")
        operations.update_images(job, name)
    elif paused:
        job.note("The instance is paused: it stays paused and starts as usual when someone opens it.")
        return
    else:
        operations.restart(job, name)
    if not paused:
        job.step("Checking that Odoo answers")
        operations._wait_until_ready(job, name, strict=True)


def _wait(job: Any, limit_hours: float = 12) -> None:
    deadline = time.time() + limit_hours * 3600
    while job.finished is None and time.time() < deadline:
        time.sleep(1)


def _finish(item_id: str, status: str, detail: str, now: float, report: bool = True,
            steps_status: str | None = None) -> Document | None:  # fmt: skip

    def apply(doc: Document | None) -> Document | None:
        if doc is None or doc.get("status") in ("done", "failed", "cancelled"):
            return doc
        steps = doc.get("steps") or []
        if steps_status:
            steps = [{**s, "status": steps_status} if s.get("status") == "running" else s for s in steps]
        changed = {**doc, "status": status, "detail": detail[:DETAIL_MAX], "finished_at": now, "steps": steps}
        return _queued(changed, _report_body(changed, status)) if report else changed

    found = _change(item_id, apply)
    if found is None or found.get("finished_at") != now:
        return found
    audit.record(WHO, None, f"maintenance {'finished' if status == 'done' else 'failed'}", ok=status == "done",
                 detail=f"{title(found)}: {detail}")  # fmt: skip
    _mail(found, "maintenance_result")
    _WAKE.set()
    return found


# ---------------------------------------------------------------- e-mails


def _admins() -> list[tuple[str, dict[str, Any]]]:
    return [(name, account) for name, account in sorted(accounts.all_users().items())
            if account.get("role") in ("super_admin", "admin") and account.get("email")]  # fmt: skip


def _clients(item: Mapping[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    """(name, account, project) of each client of an affected project, once per client."""
    from sudoo import projects
    from sudoo.projects import clients

    found: dict[str, tuple[str, dict[str, Any], str]] = {}
    for project_id in projects_of(item):
        for entry in clients.entries(projects.get(project_id)):
            name = str(entry.get("id"))
            account = accounts.get(name) or {}
            if account.get("email") and name not in found:
                found[name] = (name, account, project_id)
    return list(found.values())


def _mail(item: Mapping[str, Any], key: str, default: bool = False) -> None:
    """E-mail the administrators and the clients of affected projects; never raises (audit-logged instead)."""
    try:
        from sudoo.mail import mailer

        if not mailer().ready:
            return
        from sudoo.mail.render import panel_url
        from sudoo.projects import clients

        panel = panel_url() or ""
        people = [(name, account, f"{panel}/maintenance" if panel else "") for name, account in _admins()]
        if settings.maintenance_remind_clients:
            portal = clients.portal_url().rstrip("/")
            people += [(name, account, f"{portal}/maintenance" if portal else "")
                       for name, account, _ in _clients(item)]  # fmt: skip
        for name, account, link in people:
            _send(item, key, name, account, link, default)
    except Exception as error:  # noqa: BLE001 - a missing e-mail never stops maintenance
        audit.record(WHO, None, "maintenance e-mail not sent", ok=False, detail=f"{key}: {error}")


def _send(item: Mapping[str, Any], key: str, name: str, account: dict[str, Any], link: str, default: bool) -> None:
    from sudoo.mail import MailError, send

    arabic = accounts.language(account) == "ar"
    language = "ar" if arabic else "en"
    names = affected(item)
    where = (
        ("اللوحة" if arabic else "The panel")
        if not names
        else ", ".join(names[:10]) + (" …" if len(names) > 10 else "")
    )
    start = item.get("window_start")
    hours = max(0.0, (float(start or 0) - time.time()) / 3600)
    values = {
        "name": accounts.display_name(name, account),
        "what": title(item, language),
        "reason": item["reason"]["ar" if arabic else "en"],
        "where": where,
        "window": when(start) if start else ("لم يُحدَّد بعد" if arabic else "Not chosen yet"),
        "duration": str(item["estimated_minutes"]),
        "deadline": when(item["deadline"]),
        "hours": f"{hours:.0f}" if hours >= 1 else "1",
        "chosen": _chosen_words(item, arabic, default),
        "result": _result_words(str(item.get("status")), arabic),
        "detail": str(item.get("detail") or ""),
        "link": link,
    }
    try:
        send(str(account["email"]), f"{key}_ar" if arabic else key, **values)
    except (MailError, KeyError, ValueError) as error:
        audit.record(WHO, None, "maintenance e-mail not sent", ok=False, detail=f"{key}: {name}: {error}")


def _chosen_words(item: Mapping[str, Any], arabic: bool, default: bool) -> str:
    source = item.get("window_source")
    if default or source == "default":
        return (
            "اختارت اللوحة نافذة الصيانة الافتراضية." if arabic else "The panel chose the default maintenance window."
        )
    if not source:
        return ""
    who = str(item.get("chosen_by") or "")
    return f"اختاره {who}." if arabic else f"Chosen by {who}."


def _result_words(status: str, arabic: bool) -> str:
    words = {
        "done": ("done", "تمّت"),
        "failed": ("failed", "فشلت"),
        "cancelled": ("cancelled", "أُلغيت"),
    }.get(status, (status, status))
    return words[1] if arabic else words[0]


def _ip() -> str | None:
    from sudoo.projects import clients

    return clients._ip()


# ---------------------------------------------------------------- what the API shows


def resource(
    item: Mapping[str, Any], now: float | None = None, projects_shown: Iterable[str] | None = None
) -> dict[str, Any]:
    """An item as the panel's people see it (`projects_shown`: a client sees only its own projects' instances)."""
    now = time.time() if now is None else now
    names = affected(item)
    shown_projects = None if projects_shown is None else set(projects_shown)
    if shown_projects is not None:
        names = [n for n in names if _project_of(n)[0] in shown_projects]
    choosable = item.get("status") in CHOOSABLE
    lo, hi = bounds(item, now)
    steps = item.get("steps") or []
    if shown_projects is not None:
        steps = [s for s in steps if s.get("instance") in names]
    return {
        "id": item["id"],
        "kind": item["kind"],
        "scope": item["scope"],
        "targets": list(item.get("targets") or []) if shown_projects is None else [],
        "version": item.get("version"),
        "title": {"en": title(item), "ar": title(item, "ar")},
        "reason": dict(item["reason"]),
        "estimated_minutes": item["estimated_minutes"],
        "earliest_start": iso(item["earliest_start"]),
        "deadline": iso(item["deadline"]),
        "required": bool(item.get("required")),
        "status": item.get("status"),
        "window": (
            None
            if not item.get("window_start")
            else {
                "start": iso(item["window_start"]),
                "end": iso(item.get("window_end")),
                "source": item.get("window_source"),
                "chosen_by": item.get("chosen_by"),
                "chosen_at": iso(item.get("chosen_at")),
            }
        ),
        "choose_from": iso(lo) if choosable and lo <= hi else None,
        "choose_until": iso(hi) if choosable and lo <= hi else None,
        "presets": presets(item, now) if choosable else [],
        "auto_at": iso(auto_at(item)) if item.get("status") == "proposed" else None,
        "default_start": iso(default_start(item, now)) if item.get("status") == "proposed" else None,
        "instances": [
            {"name": n, "stage": _stage(n), "project": _project_of(n)[0], "held": hold(item, n)} for n in names
        ],
        "approvals": (
            sorted(item.get("approvals") or {})
            if shown_projects is None
            else sorted(p for p in item.get("approvals") or {} if p in shown_projects)
        ),
        "steps": [{k: s.get(k) for k in ("name", "instance", "stage", "status", "job", "detail")} for s in steps],
        "detail": item.get("detail"),
        "received_at": iso(item.get("received_at")),
        "started_at": iso(item.get("started_at")),
        "finished_at": iso(item.get("finished_at")),
    }


def for_client(name: str) -> list[tuple[Document, list[str]]]:
    """Items that work on the projects of client `name`, with those projects."""
    from sudoo.projects import clients

    mine = {str(p["id"]) for p in clients.projects_of(name)}
    found = []
    for doc in items():
        overlap = sorted(mine & set(projects_of(doc)))
        if overlap:
            found.append((doc, overlap))
    return found


def settings_view() -> dict[str, Any]:
    start, length = default_window()
    return {
        "timezone": settings.timezone,
        "lead_minutes": settings.maintenance_lead_minutes,
        "default_window": settings.maintenance_default_window,
        "default_days": settings.maintenance_default_days,
        "tonight_time": settings.maintenance_tonight_time,
        "weekend_day": settings.maintenance_weekend_day,
        "reminder_hours": reminder_hours(),
        "client_choice": bool(settings.maintenance_client_choice),
        "default_minutes": length // 60,
        "default_start": f"{start:%H:%M}",
    }
