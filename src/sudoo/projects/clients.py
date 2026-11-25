"""The client portal: the clients of the panel's owner, and the access switch each client owns for its projects.

An integrator (the panel's owner and staff) runs Odoo projects for clients. A client has an account with the role
"client" (roles.py), is invited to one or more projects, sees only those (the client portal) and decides whether the
integrator's people may open them: the switch. Everything here builds on projects/access.py, which asks this module
two questions for every check: the level of a client in a project, and whether a closed project or branch lets
someone through.

A project's client part (project["access"]["client"])
    clients     [{"id": account, "backups": bool, "shell": bool, "added_by", "added_at"}]: the project's clients;
                each sees the project (instances.view, instances.logs) and, when given, downloads its backups and
                opens a shell in its Odoo containers
    state       "open" (normal access), "closed" or "timed" (open until `until`, then closed again by itself)
    until       timed: when it closes again (Unix time)
    branches    {branch: {"state", "until", "changed_by", "changed_at"}}: a branch's own switch, which replaces the
                project's for that branch
    allow       [{"type": "user", "id"}]: the people the client names; a closure lets them through
    emergency   whether a super admin may force access for a short time (CLIENT_EMERGENCY_ACCESS for new projects);
                the client turns it off and on
    passes      [{"id", "kind": "request" | "emergency", "users", "branch", "until", "by", "at", "reason",
                "request"}]: who a closure lets through until when, after an approved request or an emergency
    locks       {"*" | branch: {"by", "at", "reason"}}: the client's locks of the whole project ("*") or of a branch
    changed_by, changed_at

The switch
    A closure applies to a branch when the branch's own switch (else the project's) is closed, or timed and its time
    is over. It takes from everyone it does not let through what a lock takes (access.LOCKED_PERMISSIONS: everything
    but seeing the project and its instances), super admins included: unlike a lock, a super admin cannot unlock it.
    It lets through the project's clients, the people the client named, and the people of an active pass (for its
    branch, or for every branch when it names none). Sudoo.sh support is never let through. The panel's own safety
    work goes on (daily backups, monitoring, certificates; production keeps serving) and automatic changes stop, as
    for a locked branch (access.held). A visit still starts a paused instance: it changes nothing.

Client locks
    The client may also lock a branch or the whole project, whatever its switch says. A client lock is as strong as a
    closed switch (nobody in the panel lifts it, super admins included) and narrower in who it lets through: only the
    project's clients and a super admin's emergency access while the client allows it; never the people the client
    named, approved requests or Sudoo.sh support. Automatic deploys and every other automatic change of the branch
    stop (access.held), as for a locked branch.

Confirmation by e-mail (projects/client_confirm.py)
    Every major action of a client (the switch, locks, emergency access, approving a request, the people it lets in)
    waits until the client confirms it with a one-time link or a 6-digit code sent to its e-mail address
    (CLIENT_CONFIRMATIONS, CLIENT_CONFIRM_MINUTES). The functions below make the change; the API calls them only once
    the action is confirmed.

Access requests (storage collection "client_access_requests", one document per request)
    {"id", "project", "branch", "by", "reason", "hours", "status", "at", "decided_by", "decided_at", "users",
    "until", "note"}: staff ask for access to a closed project or branch, with a reason and a duration; each client
    gets an e-mail (client_access_request, or _ar in Arabic) and sees it in the portal. Approving makes a pass for
    the people it names (the person who asked when none are named) and for the hours chosen; refusing ends it. A
    request nobody answered lapses after CLIENT_REQUEST_EXPIRE_HOURS.

Emergency access
    When the client allows it (the default), a super admin may force access to a closed project or branch: they type
    a reason and the name of the branch (or of the project). It makes a pass for them alone, for
    CLIENT_EMERGENCY_MINUTES; each client is e-mailed at once (client_emergency_access, or _ar) and the portal shows
    it at the top. With emergency access turned off, nobody in the panel can force access.

What staff cannot do to a client while one of its projects or branches is closed
    Add a client to that project (a client of their own would be let through), and change, reset, take the second
    factors of, or delete the account of one of its clients (they would take the client's place). The client opens
    it first, or a super admin uses emergency access where it is allowed.

The limit
    Whoever has root on the server, or access to its Docker, can reach the instances' containers, databases and
    files without the panel. The switch is an audited control inside the panel, not a wall against the server's
    administrators; a client that needs stronger isolation runs its projects on its own server.

Everything is in the audit log, scoped to the project (core/audit.py), and the client reads it as its activity log.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Iterable, Mapping
from typing import Any

from sudoo import accounts, roles
from sudoo.config import settings
from sudoo.core import audit, events
from sudoo.storage import Document, storage

STATES = ("open", "closed", "timed")
CLIENT_LEVEL = "client"
REQUESTS = "client_access_requests"
REQUEST_STATUSES = ("pending", "approved", "refused", "lapsed")
PASS_KINDS = ("request", "emergency")
REASON_MIN = 10
REASON_MAX = 300
MAX_CLIENTS = 50
MAX_ALLOW = 50
MAX_PASSES = 100
FORGET_REQUESTS_DAYS = 90  # answered requests stay listed this long
TOPIC = "client"  # live events: a project's client part or its requests changed (key: the project's id)
_BASE = frozenset({"instances.view", "instances.logs"})
_LOCK = threading.RLock()


class ClientError(Exception):
    """A change that is not allowed; the message is meant for people."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- reading


def part(project: Mapping[str, Any] | None) -> dict[str, Any]:
    """The project's client part (empty when it has none)."""
    raw = (project or {}).get("access")
    found = raw.get("client") if isinstance(raw, dict) else None
    return found if isinstance(found, dict) else {}


def entries(project: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [e for e in part(project).get("clients") or () if isinstance(e, dict) and e.get("id")]


def entry(project: Mapping[str, Any] | None, name: str | None) -> dict[str, Any] | None:
    if not name:
        return None
    return next((e for e in entries(project) if e.get("id") == name), None)


def is_client(user: Mapping[str, Any]) -> bool:
    """Whether `user` is a client (a token: its owner; Sudoo.sh support never)."""
    if user.get("support"):
        return False
    if user.get("token_id"):
        owner = accounts.get(str(user.get("owner") or ""))
        return bool(owner) and roles.normalize(str(owner.get("role"))) == roles.CLIENT  # type: ignore[union-attr]
    return user.get("role") == roles.CLIENT


def permissions_of(found: Mapping[str, Any] | None) -> frozenset[str]:
    """What a client entry gives in its project: seeing it, and the backups and the shell when it was given them."""
    if not found:
        return frozenset()
    extra = {"backups.manage"} if found.get("backups") else set()
    if found.get("shell"):
        extra.add("instances.shell")
    return _BASE | extra


def projects_of(name: str) -> list[Document]:
    """The projects `name` is a client of."""
    from sudoo import projects

    return [p for p in projects.all_projects() if entry(p, name) is not None]


def _now() -> float:
    return time.time()


def switch(project: Mapping[str, Any] | None, branch: str | None = None) -> dict[str, Any]:
    """The switch that applies to `branch` (None: the project as a whole): {"state", "until", "scope", "changed_by",
    "changed_at"}; "scope" is "branch" when the branch has its own, else "project"."""
    found = part(project)
    own = (found.get("branches") or {}).get(branch) if branch else None
    chosen, scope = (own, "branch") if isinstance(own, dict) and own.get("state") in STATES else (found, "project")
    state = chosen.get("state") if chosen.get("state") in STATES else "open"
    return {
        "state": state,
        "until": chosen.get("until") if state == "timed" else None,
        "scope": scope,
        "changed_by": chosen.get("changed_by"),
        "changed_at": chosen.get("changed_at"),
    }


def closed(project: Mapping[str, Any] | None, branch: str | None = None, now: float | None = None) -> bool:
    """Whether a closure applies to `branch` (None: the project as a whole)."""
    if project is None:
        return False
    found = switch(project, branch)
    if found["state"] == "closed":
        return True
    if found["state"] == "timed":
        return float(found.get("until") or 0) <= (_now() if now is None else now)
    return False


def closure(project: Mapping[str, Any] | None, branch: str | None = None) -> dict[str, Any] | None:
    """The closure that applies to `branch` (None: the project), or None: {"by", "at", "scope"}."""
    if not closed(project, branch):
        return None
    found = switch(project, branch)
    return {"by": found.get("changed_by"), "at": found.get("changed_at"), "scope": found["scope"]}


def any_closed(project: Mapping[str, Any] | None) -> bool:
    """Whether the project or one of its branches is closed now."""
    if project is None:
        return False
    if any_client_lock(project):
        return True
    return closed(project) or any(closed(project, b) for b in part(project).get("branches") or {})


def client_lock(project: Mapping[str, Any] | None, branch: str | None = None) -> dict[str, Any] | None:
    """The client's lock that applies to `branch` (None: the project as a whole): the project's ("*") first, else the
    branch's own; None when neither."""
    locks = part(project).get("locks") or {}
    found = locks.get("*") or (locks.get(branch) if branch else None)
    if not isinstance(found, dict):
        return None
    return {**found, "scope": "project" if locks.get("*") else "branch"}


def any_client_lock(project: Mapping[str, Any] | None) -> bool:
    return bool(part(project).get("locks"))


def lock_lets_through(user: Mapping[str, Any], project: Mapping[str, Any], branch: str | None = None) -> bool:
    """Whether a client lock lets `user` through: the project's clients, and a super admin's emergency access while
    it lasts. Nobody else."""
    from sudoo.projects import access

    name = access.person(user)
    if name is None:
        return False
    if entry(project, name) is not None:
        return True
    return any(
        p.get("kind") == "emergency"
        and name in (p.get("users") or ())
        and (not p.get("branch") or p.get("branch") == branch)
        for p in active_passes(project)
    )


def active_passes(project: Mapping[str, Any] | None, now: float | None = None) -> list[dict[str, Any]]:
    now = _now() if now is None else now
    return [p for p in part(project).get("passes") or () if isinstance(p, dict) and float(p.get("until") or 0) > now]


def lets_through(user: Mapping[str, Any], project: Mapping[str, Any], branch: str | None = None) -> bool:
    """Whether a closure of `branch` lets `user` through: the project's clients, the people the client named, the
    people of an active pass (for this branch, or for every branch). Never Sudoo.sh support."""
    from sudoo.projects import access

    name = access.person(user)
    if name is None:
        return False
    if entry(project, name) is not None:
        return True
    if any(e.get("type") == "user" and e.get("id") == name for e in part(project).get("allow") or ()):
        return True
    for found in active_passes(project):
        if name in (found.get("users") or ()) and (not found.get("branch") or found.get("branch") == branch):
            return True
    return False


def emergency_allowed(project: Mapping[str, Any] | None) -> bool:
    found = part(project).get("emergency")
    return bool(settings.client_emergency_access) if found is None else bool(found)


def open_choices() -> list[float]:
    """The durations the portal offers for "Open for a time", in hours."""
    return [float(h) for h in str(settings.client_open_hours).split(",") if h.strip()]


def account_refusal(name: str) -> str | None:
    """Why staff may not change, reset or delete the account `name` now (it is a client of a closed project or
    branch: they would take its place), or None."""
    account = accounts.get(name)
    if account is None or roles.normalize(str(account.get("role"))) != roles.CLIENT:
        return None
    found = [p["id"] for p in projects_of(name) if any_closed(p)]
    if not found:
        return None
    return (
        f"{name} is a client of {', '.join(found)}, which it closed: its account cannot be changed, reset or deleted "
        "until the client opens access again."
    )


def describe(
    project: Mapping[str, Any] | None, branch: str | None = None, user: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """The switch of `branch` (None: the project) as the API shows it to staff, or None when the project has no
    client part: {"state": "open" | "closed" | "timed", "until", "scope", "changed_by", "changed_at",
    "emergency_allowed", "clients", "lets_you_through" (with `user`)}."""
    if project is None or not part(project):
        return None
    found = switch(project, branch)
    is_closed = closed(project, branch)
    lock = client_lock(project, branch)
    shown: dict[str, Any] = {
        "state": "closed" if is_closed else found["state"],
        "until": _iso(found.get("until")) if found["state"] == "timed" and not is_closed else None,
        "scope": found["scope"],
        "changed_by": found.get("changed_by"),
        "changed_at": _iso(found.get("changed_at")),
        "emergency_allowed": emergency_allowed(project),
        "clients": len(entries(project)),
        "locked": (
            {
                "by": lock.get("by"),
                "at": _iso(lock.get("at")),
                "reason": lock.get("reason") or "",
                "scope": lock["scope"],
            }
            if lock
            else None
        ),
    }
    if user is not None:
        shown["lets_you_through"] = (not is_closed or lets_through(user, project, branch)) and (
            lock is None or lock_lets_through(user, project, branch)
        )
    return shown


# ---------------------------------------------------------------- changes


def _change(project_id: str, fn: Any) -> Document:
    """Change the project's client part with `fn(part) -> part`; returns the project."""
    from sudoo import projects

    with projects.LOCK:
        current = projects.get(project_id)
        if current is None:
            raise ClientError("This project does not exist.", 404)

        def update(stored: dict[str, Any]) -> dict[str, Any]:
            found_access = dict(stored.get("access") or {})
            found_access["client"] = fn(dict(part(stored)))
            return {**stored, "access": found_access}

        changed = projects.change(project_id, update) or current
    from sudoo.projects import access

    access.forget_restrictions()
    events.publish("project", project_id)
    events.publish(TOPIC, project_id, {"project": project_id})
    for name in projects.instances(project_id):
        events.publish("instance", name)
    return changed


def add_client(project_id: str, name: str, by: str, backups: bool = False, shell: bool = False) -> Document:
    """Make the client account `name` a client of the project, or change what it is given."""
    from sudoo import projects

    account = accounts.get(name)
    if account is None or roles.normalize(str(account.get("role"))) != roles.CLIENT:
        raise ClientError(f"{name} is not a client account.", 400)
    project = projects.get(project_id)
    if project is None:
        raise ClientError("This project does not exist.", 404)
    known = entry(project, name)
    if known is None and any_closed(project):
        raise ClientError("The project or one of its branches is closed by its client: a new client can be added "
                          "once the client opens access again.")  # fmt: skip
    if known is None and len(entries(project)) >= MAX_CLIENTS:
        raise ClientError(f"A project has at most {MAX_CLIENTS} clients.")

    def apply(found: dict[str, Any]) -> dict[str, Any]:
        listed = [e for e in entries({"access": {"client": found}}) if e.get("id") != name]
        before = known or {"added_by": by, "added_at": int(_now())}
        listed.append({**before, "id": name, "backups": bool(backups), "shell": bool(shell)})
        found["clients"] = sorted(listed, key=lambda e: str(e["id"]))
        found.setdefault("state", "open")
        found.setdefault("emergency", bool(settings.client_emergency_access))
        return found

    changed = _change(project_id, apply)
    given = ", ".join(w for w, on in (("backups", backups), ("shell", shell)) if on) or "seeing the project"
    audit.record(by, _ip(), "client added to the project" if known is None else "client's access changed",
                 detail=f"{project_id}: {name} ({given})")  # fmt: skip
    return changed


def remove_client(project_id: str, name: str, by: str) -> Document:
    from sudoo import projects

    project = projects.get(project_id)
    if project is None or entry(project, name) is None:
        raise ClientError(f"{name} is not a client of this project.", 404)
    if any_closed(project) and len(entries(project)) == 1:
        raise ClientError("The project or one of its branches is closed by this client, its only client: the client "
                          "opens access first.")  # fmt: skip

    def apply(found: dict[str, Any]) -> dict[str, Any]:
        found["clients"] = [e for e in entries({"access": {"client": found}}) if e.get("id") != name]
        return found

    changed = _change(project_id, apply)
    audit.record(by, _ip(), "client removed from the project", detail=f"{project_id}: {name}")
    return changed


def set_switch(project_id: str, branch: str | None, state: str, by: str, hours: float | None = None,
               inherit: bool = False) -> Document:  # fmt: skip
    """Set the switch of the project (branch None) or of a branch: open, closed, or timed for `hours`; `inherit`
    takes the branch's own switch away (the project's applies again). Closing ends the passes that would let people
    through it."""
    if state not in STATES:
        raise ClientError("state: open, closed or timed.", 400)
    until: int | None = None
    if state == "timed":
        if hours is None or not 0 < hours <= settings.client_open_max_hours:
            raise ClientError(f"Choose how long to open it: at most {settings.client_open_max_hours} hours.", 400)
        until = int(_now() + hours * 3600)
    now = int(_now())
    values = {"state": state, "until": until, "changed_by": by, "changed_at": now}

    def apply(found: dict[str, Any]) -> dict[str, Any]:
        if branch is None:
            found.update(values)
        else:
            own = dict(found.get("branches") or {})
            if inherit:
                own.pop(branch, None)
            else:
                own[branch] = values
            found["branches"] = own
        if state == "closed":  # closing ends the passes into what closes: all of them, or those that reach the branch
            found["passes"] = [
                p
                for p in found.get("passes") or ()
                if float(p.get("until") or 0) > now and branch is not None and p.get("branch") not in (None, branch)
            ]
        return found

    changed = _change(project_id, apply)
    _access_rules_changed()
    where = f"{project_id} {branch}" if branch else project_id
    if inherit:
        audit.record(by, _ip(), "client access follows the project again", detail=f"{where}: as the project")
    elif state == "timed":
        audit.record(by, _ip(), "client access opened for a time", detail=f"{where}: open for {hours:g} h, closes "
                     f"again at {_when(until)}")  # fmt: skip
    else:
        audit.record(by, _ip(), f"client access {'closed' if state == 'closed' else 'opened'}", detail=where)
    return changed


def _access_rules_changed() -> None:
    """Production access rules (instances/access_rules.py) give no pass while the client has closed access: tell the
    gateway now rather than at its next round."""
    from sudoo.instances import access_rules

    try:
        access_rules.apply()
    except Exception as error:  # noqa: BLE001 - the switch has changed; the gateway catches up at its next round
        print(f"Access rules after a client change: {error!r}", flush=True)


def set_lock(project_id: str, branch: str | None, locked: bool, by: str, reason: str = "") -> Document:
    """Lock or unlock the whole project (branch None) or a branch, as the client."""
    key = branch or "*"
    reason = " ".join(reason.split())[:REASON_MAX]

    def apply(found: dict[str, Any]) -> dict[str, Any]:
        locks = dict(found.get("locks") or {})
        if locked:
            locks[key] = {"by": by, "at": int(_now()), "reason": reason}
        else:
            locks.pop(key, None)
        found["locks"] = locks
        return found

    changed = _change(project_id, apply)
    _access_rules_changed()
    where = _where(project_id, branch)
    audit.record(
        by,
        _ip(),
        "client locked" if locked else "client unlocked",
        detail=f"{where}" + (f": {reason}" if reason and locked else ""),
    )
    return changed


def set_allow(project_id: str, names: Iterable[str], by: str) -> Document:
    """The people the client names: a closure lets them through."""
    chosen = sorted(dict.fromkeys(str(n) for n in names))
    if len(chosen) > MAX_ALLOW:
        raise ClientError(f"Name at most {MAX_ALLOW} people.", 400)
    for name in chosen:
        account = accounts.get(name)
        if account is None:
            raise ClientError(f"There is no account {name}.", 400)

    def apply(found: dict[str, Any]) -> dict[str, Any]:
        found["allow"] = [{"type": "user", "id": n} for n in chosen]
        return found

    changed = _change(project_id, apply)
    audit.record(by, _ip(), "client's named people changed", detail=f"{project_id}: {', '.join(chosen) or 'nobody'}")
    return changed


def set_emergency(project_id: str, allowed: bool, by: str) -> Document:
    def apply(found: dict[str, Any]) -> dict[str, Any]:
        found["emergency"] = bool(allowed)
        return found

    changed = _change(project_id, apply)
    audit.record(by, _ip(), "emergency access allowed" if allowed else "emergency access turned off",
                 detail=project_id)  # fmt: skip
    return changed


def _add_pass(project_id: str, found: dict[str, Any]) -> Document:
    def apply(current: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        kept = [p for p in current.get("passes") or () if float(p.get("until") or 0) > now]
        current["passes"] = [*kept, found][-MAX_PASSES:]
        return current

    return _change(project_id, apply)


def end_pass(project_id: str, pass_id: str, by: str) -> Document:
    from sudoo import projects

    project = projects.get(project_id)
    found = next((p for p in active_passes(project) if p.get("id") == pass_id), None)
    if found is None:
        raise ClientError("This access has ended already.", 404)

    def apply(current: dict[str, Any]) -> dict[str, Any]:
        current["passes"] = [p for p in current.get("passes") or () if p.get("id") != pass_id]
        return current

    changed = _change(project_id, apply)
    audit.record(by, _ip(), "access ended by the client", detail=f"{_where(project_id, found.get('branch'))}: "
                 f"{', '.join(found.get('users') or ())} ({found.get('kind')})")  # fmt: skip
    return changed


# ---------------------------------------------------------------- access requests


def requests(project_id: str | None = None, status: str | None = None) -> list[Document]:
    """The access requests (of a project), newest first, after lapsing those nobody answered in time."""
    lapse()
    found = [r for r in storage().all(REQUESTS).values() if project_id is None or r.get("project") == project_id]
    if status:
        found = [r for r in found if r.get("status") == status]
    return sorted(found, key=lambda r: -float(r.get("at") or 0))


def request_of(request_id: str) -> Document | None:
    lapse()
    return storage().get(REQUESTS, request_id)


def ask(project: Mapping[str, Any], branch: str | None, by: str, reason: str, hours: float) -> Document:
    """A new access request to a closed project or branch; each client is e-mailed."""
    reason = " ".join(reason.split())
    if not closed(project, branch):
        raise ClientError("This is open: there is nothing to ask for.")
    if len(reason) < REASON_MIN:
        raise ClientError(f"Say why you need access (at least {REASON_MIN} characters).", 400)
    if not 0 < hours <= settings.client_request_max_hours:
        raise ClientError(f"Ask for at most {settings.client_request_max_hours} hours.", 400)
    mine = [r for r in requests(str(project["id"]), "pending") if r.get("by") == by and r.get("branch") == branch]
    if mine:
        raise ClientError("You asked already: the client has not answered yet.")
    request_id = secrets.token_urlsafe(9)
    found = {"id": request_id, "project": project["id"], "branch": branch, "by": by, "reason": reason[:REASON_MAX],
             "hours": hours, "status": "pending", "at": int(_now())}  # fmt: skip
    storage().put(REQUESTS, request_id, found)
    audit.record(by, _ip(), "access requested", detail=f"{_where(str(project['id']), branch)}: {hours:g} h; "
                 f"{reason[:200]}")  # fmt: skip
    events.publish(TOPIC, str(project["id"]), {"project": str(project["id"])})
    _tell_clients(project, "client_access_request", by, branch, reason=reason, hours=f"{hours:g}")
    return found


def decide(request_id: str, by: str, approve: bool, hours: float | None = None, users: Iterable[str] | None = None,
           note: str = "") -> Document:  # fmt: skip
    """Approve (a pass for `users`, default the person who asked, for `hours`, default those asked) or refuse."""
    from sudoo import projects

    with _LOCK:
        found = request_of(request_id)
        if found is None:
            raise ClientError("There is no such request.", 404)
        if found.get("status") != "pending":
            raise ClientError(f"This request was {found.get('status')} already.")
        project = projects.get(str(found.get("project")))
        if project is None:
            raise ClientError("This project does not exist.", 404)
        note = " ".join(note.split())[:REASON_MAX]
        now = int(_now())
        decided: dict[str, Any] = {**found, "decided_by": by, "decided_at": now, "note": note or None}
        if approve:
            hours = float(hours or found.get("hours") or settings.client_request_hours)
            if not 0 < hours <= settings.client_request_max_hours:
                raise ClientError(f"Give at most {settings.client_request_max_hours} hours.", 400)
            people = sorted(dict.fromkeys(str(u) for u in (users or [str(found.get("by"))])))
            if not people or any(accounts.get(u) is None for u in people):
                raise ClientError("Name people who have an account.", 400)
            until = int(now + hours * 3600)
            decided |= {"status": "approved", "users": people, "until": until, "hours": hours}
            _add_pass(str(project["id"]), {"id": secrets.token_urlsafe(9), "kind": "request", "users": people,
                                           "branch": found.get("branch"), "until": until, "by": by, "at": now,
                                           "reason": found.get("reason"), "request": request_id})  # fmt: skip
        else:
            decided["status"] = "refused"
        storage().put(REQUESTS, request_id, decided)
    where = _where(str(project["id"]), found.get("branch"))
    if approve:
        audit.record(by, _ip(), "access request approved", detail=f"{where}: {', '.join(decided['users'])} for "
                     f"{decided['hours']:g} h, until {_when(decided['until'])}" + (f"; {note}" if note else ""))  # fmt: skip
    else:
        audit.record(by, _ip(), "access request refused", detail=f"{where}: asked by {found.get('by')}"
                     + (f"; {note}" if note else ""))  # fmt: skip
    events.publish(TOPIC, str(project["id"]), {"project": str(project["id"])})
    return decided


def lapse(now: float | None = None) -> int:
    """Lapse the requests nobody answered within CLIENT_REQUEST_EXPIRE_HOURS, forget old answered ones."""
    now = _now() if now is None else now
    limit = settings.client_request_expire_hours * 3600
    lapsed = 0
    for key, found in list(storage().all(REQUESTS).items()):
        at = float(found.get("at") or 0)
        if found.get("status") == "pending" and now - at > limit:
            storage().put(REQUESTS, key, {**found, "status": "lapsed", "decided_at": int(now)})
            audit.record("system", None, "access request lapsed", detail=f"{_where(str(found.get('project')), found.get('branch'))}: asked by {found.get('by')}, not answered")  # fmt: skip
            lapsed += 1
        elif (
            found.get("status") != "pending"
            and now - float(found.get("decided_at") or at) > FORGET_REQUESTS_DAYS * 86400
        ):
            storage().delete(REQUESTS, key)
    return lapsed


# ---------------------------------------------------------------- emergency access


def emergency(project: Mapping[str, Any], branch: str | None, by: str, reason: str, typed: str) -> dict[str, Any]:
    """A super admin forces access to a closed project or branch (see the module's description); returns the pass."""
    reason = " ".join(reason.split())
    wanted = branch or str(project["id"])
    if not closed(project, branch) and client_lock(project, branch) is None:
        raise ClientError("This is open: no emergency access is needed.")
    if not emergency_allowed(project):
        audit.record(by, _ip(), "emergency access refused (turned off by the client)", ok=False,
                     detail=_where(str(project["id"]), branch))  # fmt: skip
        raise ClientError("The client turned emergency access off for this project: nobody in the panel can force "
                          "access. Ask the client.", 403)  # fmt: skip
    if len(reason) < REASON_MIN:
        raise ClientError(f"Say why (at least {REASON_MIN} characters): the client reads it.", 400)
    if typed.strip() != wanted:
        raise ClientError(f"Type {wanted} to confirm.", 400)
    now = int(_now())
    until = int(now + settings.client_emergency_minutes * 60)
    found = {"id": secrets.token_urlsafe(9), "kind": "emergency", "users": [by], "branch": branch, "until": until,
             "by": by, "at": now, "reason": reason[:REASON_MAX]}  # fmt: skip
    _add_pass(str(project["id"]), found)
    audit.record(by, _ip(), "emergency access used", detail=f"{_where(str(project['id']), branch)}: until "
                 f"{_when(until)}; {reason[:200]}")  # fmt: skip
    _tell_clients(project, "client_emergency_access", by, branch, reason=reason,
                  minutes=str(settings.client_emergency_minutes))  # fmt: skip
    return found


# ---------------------------------------------------------------- keeping time


def sweep(now: float | None = None) -> list[str]:
    """Record what time changed (called every minute by the background work; the checks never wait for it): a timed
    switch whose time is over becomes closed, ended passes go. Returns the projects that changed."""
    from sudoo import projects

    now = _now() if now is None else now
    changed: list[str] = []
    for project in projects.all_projects():
        found = part(project)
        if not found:
            continue
        due = [b for b, v in (found.get("branches") or {}).items() if _over(v, now)]
        whole = _over(found, now)
        ended = [p for p in found.get("passes") or () if float(p.get("until") or 0) <= now]
        if not (due or whole or ended):
            continue

        def apply(current: dict[str, Any], due: list[str] = due, whole: bool = whole) -> dict[str, Any]:
            if whole and _over(current, now):
                current.update(state="closed", until=None)
            branches = dict(current.get("branches") or {})
            for branch in due:
                if _over(branches.get(branch) or {}, now):
                    branches[branch] = {**branches[branch], "state": "closed", "until": None}
            current["branches"] = branches
            current["passes"] = [p for p in current.get("passes") or () if float(p.get("until") or 0) > now]
            return current

        _change(str(project["id"]), apply)
        for branch in ([None] if whole else []) + due:
            audit.record("system", None, "client access closed again (time over)",
                         detail=_where(str(project["id"]), branch))  # fmt: skip
        for item in ended:
            audit.record("system", None, "access ended (time over)", detail=f"{_where(str(project['id']), item.get('branch'))}: {', '.join(item.get('users') or ())} ({item.get('kind')})")  # fmt: skip
        changed.append(str(project["id"]))
    if changed:
        _access_rules_changed()
    lapse(now)
    return changed


def run_forever() -> None:
    while True:
        time.sleep(60)
        try:
            sweep()
        except Exception as error:  # noqa: BLE001 - tried again in a minute
            print(f"client portal: {error!r}", flush=True)


def _over(found: Mapping[str, Any], now: float) -> bool:
    return found.get("state") == "timed" and float(found.get("until") or 0) <= now


# ---------------------------------------------------------------- telling the clients


def _tell_clients(project: Mapping[str, Any], key: str, by: str, branch: str | None, **values: str) -> None:
    """E-mail each client of the project (in its language); a message that cannot be sent is audit-logged and the
    portal shows the request or the emergency all the same."""
    from sudoo.mail import MailError, send

    asker = accounts.display_name(by, accounts.get(by) or {})
    link = f"{portal_url().rstrip('/')}/p/{project['id']}" if portal_url() else ""
    for found in entries(project):
        account = accounts.get(str(found["id"])) or {}
        email = str(account.get("email") or "")
        if not email:
            continue
        arabic = accounts.language(account) == "ar"
        try:
            send(email, f"{key}_ar" if arabic else key, name=accounts.display_name(str(found["id"]), account),
                 person=asker, project=str(project.get("name") or project["id"]), branch=branch or "",
                 where=branch or str(project.get("name") or project["id"]), link=link, **values)  # fmt: skip
        except (MailError, KeyError, ValueError) as error:
            audit.record("system", None, "client notice not e-mailed", ok=False,
                         detail=f"{project['id']}: {found['id']}: {error}")  # fmt: skip


def portal_url() -> str:
    """Where clients open the portal: https://<PORTAL_DOMAIN>, else the panel's own address."""
    if settings.portal_domain:
        return f"https://{settings.portal_domain}"
    from sudoo.mail.render import panel_url

    return panel_url() or ""


# ---------------------------------------------------------------- small helpers


def _where(project_id: str, branch: str | None) -> str:
    return f"{project_id} {branch}" if branch else project_id


def _iso(at: Any) -> str | None:
    from datetime import UTC, datetime

    if not at:
        return None
    return datetime.fromtimestamp(float(at), UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _when(at: float | None) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(float(at or 0), UTC).strftime("%Y-%m-%d %H:%M UTC")


def _ip() -> str | None:
    """The caller's address inside a request, else None (background work)."""
    try:
        from flask import has_request_context

        if has_request_context():
            from sudoo.web.security import client_ip

            return client_ip()
    except Exception:  # noqa: BLE001 - outside the web app
        return None
    return None


# ---------------------------------------------------------------- temporary access (projects/elevation.py)


def elevation_refusal(request: Mapping[str, Any]) -> str | None:
    """Why the client's switch refuses a temporary access request (asked, approved, or while it runs), or None. The
    client decides alone: a project, or a branch of the stage asked for, that the client locked, or a project it
    closed, stays closed to the person whatever role they are given for a while. Branches it closes one by one are
    closed to everyone anyway: projects/access.permissions removes their rights after the role and any temporary
    access."""
    from sudoo import projects

    project = projects.get(str(request.get("project") or ""))
    if project is None:
        return "This project no longer exists."
    user = {"name": str(request.get("user") or ""), "role": "member"}
    for branch in (None, *_stage_branches(project, str(request.get("stage") or ""))):
        if client_lock(project, branch) is not None and not lock_lets_through(user, project, branch):
            where = (
                f"The branch {branch} of {project.get('name') or project['id']}"
                if branch
                else (f"The project {project.get('name') or project['id']}")
            )
            return f"{where} is locked by its client: temporary access cannot open it."
    if closed(project) and not lets_through(user, project):
        return (
            f"The project {project.get('name') or project['id']} is closed by its client: ask the client for access (Request "
            "access), not the project's admins for a role."
        )
    return None


def _stage_branches(project: Mapping[str, Any], stage: str) -> list[str]:
    from sudoo import projects

    known = set(projects.by_branch(str(project["id"]))) | set(project.get("branches") or {})
    return sorted(b for b in known if b and projects.branch_stage(project, b) == stage)


def _register_with_elevation() -> None:
    from . import elevation

    if elevation_refusal not in elevation._conditions:
        elevation.add_condition(elevation_refusal)


_register_with_elevation()
