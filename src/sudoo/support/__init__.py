"""Support access: a super admin or administrator lets Sudoo.sh support into this panel for a while.

Grants (storage collection "support_grants")
    A grant is made in Settings › Support access (or the Support access page) by someone with support_access.manage:

        level    view          see instances, their activity and logs (the viewer role)
                 admin         what an administrator does (the admin role), without shells, and never accounts,
                               API tokens, teams, project access and locks, GitHub, the panel's settings or support
                               access itself (EXCLUDED)
                 admin_shell   admin, with shells in instances' containers (recorded, always)
        scope    all projects and instances, or chosen projects and instances (see level_in)
        hours    1, 24 or 72, at most SUPPORT_ACCESS_MAX_HOURS
        ticket   optional: the Sudoo.sh support ticket it is for

    It ends on its own at `expires_at`, or when someone withdraws it (revoke), presses "End now" (revoke_all), or
    SUPPORT_ACCESS is switched off. Nothing reaches the panel while no grant is active.

How support reaches the panel (channel.py)
    No inbound port and no shared secret: while a grant is active the panel keeps asking the Sudoo.sh hub for work
    over its existing signed link (Ed25519 both ways, hub/client.py), and carries out each request against its own
    API as a synthetic user, "Sudoo.sh support (<staff name>)", with exactly the grant's level and scope (user_for).
    The grant is checked again on every request and every permission check (permissions), so withdrawing it takes
    effect at once on the panel, and on the hub with the next call (a second or two).

Scope (level_in, instance_level)
    all             every project and instance, except restricted projects and restricted branches (those only
                    their own grants open: support sees what panel_roles projects show everyone)
    chosen projects those projects, except their restricted branches
    chosen instances those instances, whatever restricts their project or branch
    Locked branches stay locked: support is never allowed by a lock, so it cannot change, open (credentials, logs,
    shells) or unlock a locked branch's instances, whatever the level; unlocking needs projects.access, which support
    never has.

Audit
    Everything support does is in the panel's audit log under its synthetic name (support_entry tells such entries
    apart): each relayed request, sessions starting and ending, shells (always recorded); granting, withdrawing and
    expiry are logged under the admin's name or "system".
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

from sudoo import roles
from sudoo.config import settings
from sudoo.core import audit
from sudoo.storage import storage

COLLECTION = "support_grants"
LEVELS = ("view", "admin", "admin_shell")
DURATIONS = (1, 24, 72)  # hours a grant may last (at most SUPPORT_ACCESS_MAX_HOURS)
MAX_ACTIVE = 20
KEEP = 100  # grants kept for the list (the oldest inactive ones go)
NAME_PREFIX = "Sudoo.sh support ("
EXCLUDED = frozenset(
    {
        "accounts.manage",
        "tokens.manage",
        "teams.manage",
        "projects.access",
        "github.manage",
        "settings.manage",
        "mail.manage",
        "offsite.configure",
        "shells.supervise",
        "support_access.manage",
    }
)
"""What support never has, whatever the level: nothing that would outlive the grant (accounts, tokens, teams,
access rules, deploy keys) or change the panel itself."""

_LOCK = threading.Lock()


# ---------------------------------------------------------------- grants


def _all() -> list[dict[str, Any]]:
    return sorted(storage().all(COLLECTION).values(), key=lambda g: g.get("created_at", 0), reverse=True)


def grants() -> list[dict[str, Any]]:
    """Every grant kept, newest first."""
    return _all()


def get(grant_id: str) -> dict[str, Any] | None:
    if not isinstance(grant_id, str) or not 8 <= len(grant_id) <= 64:
        return None
    return storage().get(COLLECTION, grant_id)


def is_active(grant: Mapping[str, Any] | None, now: float | None = None) -> bool:
    """Not withdrawn, not expired, and support access is switched on."""
    if not grant or not settings.support_access:
        return False
    now = time.time() if now is None else now
    return grant.get("revoked_at") is None and float(grant.get("expires_at") or 0) > now


def active(now: float | None = None) -> list[dict[str, Any]]:
    now = time.time() if now is None else now
    return [g for g in _all() if is_active(g, now)]


def max_hours() -> int:
    return max(1, min(DURATIONS[-1], int(settings.support_access_max_hours)))


def durations() -> list[int]:
    """The lengths a new grant may have now (hours)."""
    return [hours for hours in DURATIONS if hours <= max_hours()]


def create(
    by: str, level: str, hours: int, scope: Mapping[str, Any], ticket: str | None, ip: str | None = None
) -> dict[str, Any]:
    """Store a new grant (the caller checked the values, see web/v1/support_access.py) and tell the hub."""
    now = time.time()
    grant: dict[str, Any] = {
        "id": _uuid(),
        "level": level,
        "scope": {
            "all": bool(scope.get("all")),
            "projects": [dict(p) for p in scope.get("projects") or ()],
            "instances": list(scope.get("instances") or ()),
        },
        "ticket": ticket or None,
        "created_by": by,
        "created_at": now,
        "expires_at": now + hours * 3600,
        "hours": hours,
        "revoked_at": None,
        "revoked_by": None,
    }
    with _LOCK:
        storage().put(COLLECTION, grant["id"], grant)
        _prune()
    audit.record(by, ip, "support access granted", detail=f"{describe_scope(grant)}; {grant['id']}")
    _changed()
    return grant


def _uuid() -> str:
    import uuid

    return str(uuid.uuid4())


def _prune() -> None:
    kept = _all()
    for old in [g for g in kept if not is_active(g)][KEEP:]:
        storage().delete(COLLECTION, old["id"])


def revoke(grant_id: str, by: str, ip: str | None = None, event: str = "support access withdrawn") -> bool:
    """Withdraw a grant now; False when it is not active. Open support shells of it close at once."""
    now = time.time()

    def change(doc: dict[str, Any] | None) -> dict[str, Any] | None:
        if doc is None or not is_active(doc, now):
            return doc
        return {**doc, "revoked_at": now, "revoked_by": by}

    with _LOCK:
        before = storage().get(COLLECTION, grant_id)
        if not is_active(before, now):
            return False
        storage().change(COLLECTION, grant_id, change)
    audit.record(by, ip, event, detail=f"{describe_scope(before or {})}; {grant_id}")
    from . import shells

    shells.end_for_grant(grant_id)
    _changed()
    return True


def revoke_all(by: str, ip: str | None = None) -> list[str]:
    """ "End now": withdraw every active grant (their sessions end, their shells close)."""
    ended = [g["id"] for g in active() if revoke(g["id"], by, ip, "support access ended now")]
    from . import channel

    channel.end_sessions_now()
    return ended


def note_expired(now: float | None = None) -> list[str]:
    """Write grants that ran out to the audit log, once each (called by the channel's loop)."""
    now = time.time() if now is None else now
    noted = []
    for grant in _all():
        expired = grant.get("revoked_at") is None and float(grant.get("expires_at") or 0) <= now
        if expired and not grant.get("expiry_noted"):
            storage().change(COLLECTION, grant["id"], lambda d: {**(d or {}), "expiry_noted": True} if d else d)
            audit.record("system", None, "support access expired", detail=f"{describe_scope(grant)}; {grant['id']}")
            from . import shells

            shells.end_for_grant(grant["id"])
            noted.append(grant["id"])
    return noted


def _changed() -> None:
    from sudoo.core import events

    from . import channel

    channel.wake()
    events.publish("support", "status", {"changed": True})


LEVEL_WORDS = {"view": "view", "admin": "panel admin", "admin_shell": "panel admin + shell"}


def describe_scope(grant: Mapping[str, Any]) -> str:
    """One line for the audit log: level, scope, hours, ticket."""
    scope = grant.get("scope") or {}
    if scope.get("all"):
        where = "all projects and instances"
    else:
        parts = [f"project {p.get('name') or p.get('id')}" for p in scope.get("projects") or ()]
        parts += [f"instance {name}" for name in scope.get("instances") or ()]
        where = ", ".join(parts) or "nothing"
    ticket = f"; ticket {grant['ticket']}" if grant.get("ticket") else ""
    hours = f"; {grant['hours']} h" if grant.get("hours") else ""
    return f"{LEVEL_WORDS.get(str(grant.get('level')), grant.get('level'))}; {where}{hours}{ticket}"


# ---------------------------------------------------------------- the synthetic user


def clean_name(value: object) -> str:
    """The staff member's name as shown: printable, no brackets, at most 80 characters."""
    text = "".join(c for c in str(value or "") if c.isprintable() and c not in "()<>")
    return " ".join(text.split())[:80] or "staff"


def user_for(grant: Mapping[str, Any], staff_name: str, session_id: str) -> dict[str, Any]:
    """The user a support request runs as: "Sudoo.sh support (<staff name>)", the role of its level, and the grant
    (its id is checked again on every permission check)."""
    level = str(grant["level"])
    return {
        "name": f"{NAME_PREFIX}{clean_name(staff_name)})",
        "role": "viewer" if level == "view" else "admin",
        "support": {"grant": str(grant["id"]), "level": level, "session": session_id},
    }


def is_support(user: Mapping[str, Any] | None) -> bool:
    return bool(user and user.get("support"))


def support_entry(name: object) -> bool:
    """Whether an audit entry's user is support's synthetic user."""
    return isinstance(name, str) and name.startswith(NAME_PREFIX)


def grant_of(user: Mapping[str, Any]) -> dict[str, Any] | None:
    """The user's grant while it is active (None: support may do nothing at all)."""
    found = get(str((user.get("support") or {}).get("grant") or ""))
    return found if is_active(found) else None


def permissions(user: Mapping[str, Any]) -> frozenset[str]:
    """What support may do on the panel: the level's role without EXCLUDED (and without shells below admin_shell);
    nothing once the grant ended."""
    grant = grant_of(user)
    if grant is None:
        return frozenset()
    found = roles.permissions("viewer" if grant["level"] == "view" else "admin") - EXCLUDED
    if grant["level"] != "admin_shell":
        found -= roles.SHELL_PERMISSIONS
    return found


def level_in(user: Mapping[str, Any], project: Mapping[str, Any] | None, branch: str | None) -> str:
    """Support's level in a project and branch (projects/access.py levels): "admin" (its role decides) inside the
    scope, "none" outside it; restricted projects (scope all) and restricted branches are outside."""
    from sudoo.projects import access

    grant = grant_of(user)
    if grant is None:
        return "none"
    scope = grant.get("scope") or {}
    if project is None:
        return "admin" if scope.get("all") else "none"
    chosen = str(project.get("id")) in {str(p.get("id")) for p in scope.get("projects") or ()}
    if not chosen and not (scope.get("all") and access.mode(project) == "panel_roles"):
        return "none"
    if branch and access.branch_access(project, branch).get("restricted"):
        return "none"
    return "admin"


def chosen_instance(user: Mapping[str, Any], name: str) -> bool:
    """Whether the grant names this instance itself (it is then open to support whatever restricts its project)."""
    grant = grant_of(user)
    return grant is not None and name in ((grant.get("scope") or {}).get("instances") or ())
