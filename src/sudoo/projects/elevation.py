"""Temporary access: a person asks for a higher role in one stage of a project for a few hours, with a reason; an
admin of the project approves or refuses it, and it ends by itself.

    asked      {"id", "project", "user", "stage", "level", "hours", "reason", "status": "pending", "asked"}
    approved   + "decided_by", "decided", "expires" (decided + hours): the rights of `level` in `stage` (project
               roles, project_roles.py) are added to the person's own while it lasts (projects/access.py)
    refused    + "decided_by", "decided", "note"
    withdrawn  by the person, or ended early by an admin

Requests are kept in the panel's storage (collection "access_requests") and in memory for the access checks, which
run on every request. A project's approvers are the people with projects.access in it (super admins and
administrators included). Extra conditions can be added by other parts of the panel through `add_condition`: the
client portal registers one that refuses production access its client switched off; a condition may refuse a request
when it is made, when it is approved, and while it lasts (checked again at each use).
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from sudoo.config import settings
from sudoo.storage import storage

from . import project_roles

COLLECTION = "access_requests"
STATUSES = ("pending", "approved", "refused", "withdrawn", "expired")
KEEP_SECONDS = 90 * 86400  # finished requests stay listed (and audited) for 90 days

Condition = Callable[[Mapping[str, Any]], str | None]
"""(request) -> why it may not apply, or None."""

_conditions: list[Condition] = []
_cache: dict[str, dict[str, Any]] | None = None
_lock = threading.Lock()


def add_condition(condition: Condition) -> None:
    """Register an extra rule (the client portal's switches); it is asked at each step and at each use."""
    _conditions.append(condition)


def refusal(request: Mapping[str, Any]) -> str | None:
    for condition in _conditions:
        reason = condition(request)
        if reason:
            return reason
    return None


def _all() -> dict[str, dict[str, Any]]:
    global _cache
    with _lock:
        if _cache is None:
            _cache = storage().all(COLLECTION)
        return _cache


def _save(request: dict[str, Any]) -> dict[str, Any]:
    storage().put(COLLECTION, str(request["id"]), request)
    with _lock:
        if _cache is not None:
            _cache[str(request["id"])] = request
    from . import access

    access.forget_restrictions()
    return request


def forget_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def of_project(project_id: str) -> list[dict[str, Any]]:
    """The project's requests, newest first (finished ones older than 90 days are deleted)."""
    now = time.time()
    found = []
    for key, request in list(_all().items()):
        if request.get("project") != project_id:
            continue
        if (
            request.get("status") != "pending"
            and float(request.get("decided") or request["asked"]) + KEEP_SECONDS < now
        ):
            storage().delete(COLLECTION, key)
            with _lock:
                if _cache is not None:
                    _cache.pop(key, None)
            continue
        found.append(state(request))
    return sorted(found, key=lambda r: -float(r["asked"]))


def state(request: dict[str, Any]) -> dict[str, Any]:
    """The request with "expired" when its time is over."""
    if request.get("status") == "approved" and float(request.get("expires") or 0) <= time.time():
        return {**request, "status": "expired"}
    return request


def get(request_id: str) -> dict[str, Any] | None:
    found = _all().get(request_id)
    return state(found) if found else None


def ask(project_id: str, user: str, stage: str, level: str, hours: float, reason: str) -> dict[str, Any]:
    """A new request. Raises ValueError saying why it cannot be made."""
    if not settings.access_requests:
        raise ValueError("Temporary access is turned off on this panel (ACCESS_REQUESTS).")
    if stage not in project_roles.STAGES:
        raise ValueError(f"stage: one of {', '.join(project_roles.STAGES)}.")
    if level not in ("tester", "developer", "maintainer", "admin"):
        raise ValueError("level: tester, developer, maintainer or admin.")
    if not 0 < hours <= settings.access_request_max_hours:
        raise ValueError(f"hours: more than 0, at most {settings.access_request_max_hours} (ACCESS_REQUEST_MAX_HOURS).")
    if not 10 <= len(reason.strip()) <= 500:
        raise ValueError("reason: say why you need it, in 10 to 500 characters.")
    if any(
        r["user"] == user and r["project"] == project_id and r["status"] == "pending"
        for r in map(state, _all().values())
    ):
        raise ValueError("You already have a request waiting in this project. Withdraw it first, or wait.")
    request = {
        "id": secrets.token_urlsafe(9),
        "project": project_id,
        "user": user,
        "stage": stage,
        "level": level,
        "hours": round(float(hours), 2),
        "reason": reason.strip(),
        "status": "pending",
        "asked": time.time(),
    }
    blocked = refusal(request)
    if blocked:
        raise ValueError(blocked)
    return _save(request)


def decide(request_id: str, by: str, approve: bool, note: str = "") -> dict[str, Any]:
    request = get(request_id)
    if request is None or request["status"] != "pending":
        raise ValueError("This request is not waiting for a decision any more.")
    if approve:
        blocked = refusal(request)
        if blocked:
            raise ValueError(blocked)
    now = time.time()
    changed = {**request, "status": "approved" if approve else "refused", "decided_by": by, "decided": now,
               "note": note.strip()[:300]}  # fmt: skip
    if approve:
        changed["expires"] = now + float(request["hours"]) * 3600
    return _save(changed)


def end(request_id: str, by: str) -> dict[str, Any]:
    request = get(request_id)
    if request is None or request["status"] not in ("pending", "approved"):
        raise ValueError("This request is not waiting or running.")
    return _save({**request, "status": "withdrawn", "decided_by": request.get("decided_by") or by,
                  "ended_by": by, "decided": request.get("decided") or time.time(), "expires": time.time()})  # fmt: skip


def active(user: str | None, project_id: str) -> list[dict[str, Any]]:
    """The person's approved requests that still run in this project (and that the conditions still allow)."""
    if not user:
        return []
    now = time.time()
    return [
        r
        for r in _all().values()
        if r.get("user") == user
        and r.get("project") == project_id
        and r.get("status") == "approved"
        and float(r.get("expires") or 0) > now
        and refusal(r) is None
    ]


def rights(user: str | None, project: Mapping[str, Any], stage: str | None) -> frozenset[str]:
    """What the person's running requests add in `stage` (None: in any stage) of the project."""
    found: frozenset[str] = frozenset()
    for request in active(user, str(project.get("id"))):
        if stage is None or request["stage"] == stage:
            granted = project_roles.matrix(project)[request["level"]][request["stage"]]
            found |= granted
    return found
