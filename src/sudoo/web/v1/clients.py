"""The client portal's API (projects/clients.py): staff's side of a project's clients, and the client's own side.

The panel's people (a project's staff)
    GET    /api/v1/projects/<id>/clients                 the project's clients, its switch and its branches', who
                                                         the client let in, active access, requests (instances.view)
    POST   /api/v1/projects/<id>/clients                 {"email", "first_name"?, "last_name"?, "backups"?, "shell"?}:
                                                         an existing client account becomes a client of the project;
                                                         a new address is invited (e-mail) and joins when it accepts
                                                         -> 201 (clients.manage and projects.access; premium, below)
    PATCH  /api/v1/projects/<id>/clients/<name>          {"backups"?, "shell"?} (as POST)
    DELETE /api/v1/projects/<id>/clients/<name>          take a client out -> 204 (clients.manage; always possible,
                                                         except the only client of a closed project)
    GET    /api/v1/projects/<id>/access-requests         the project's access requests
    POST   /api/v1/projects/<id>/access-requests         {"branch"?, "reason", "hours"?}: ask the client to open a
                                                         closed project or branch -> 201 (anyone who sees it)
    POST   /api/v1/projects/<id>/emergency-access        {"branch"?, "reason", "confirm"}: a super admin forces access
                                                         for CLIENT_EMERGENCY_MINUTES, where the client allows it ->
                                                         201 (a signed-in super admin, never an API token)

The client (a client account, in the portal; security.CLIENT_ENDPOINTS)
    GET    /api/v1/client/projects                       its projects, with their switch and what waits for it
    GET    /api/v1/client/projects/<id>                  one project: branches, instances (status, address), the
                                                         switches, who it let in, active access, requests, notices
    PUT    /api/v1/client/projects/<id>/switch           {"branch"?, "state": open|closed|timed, "hours"?, "inherit"?}
    PUT    /api/v1/client/projects/<id>/allow            {"people": [account names]}: who a closure lets through
    PUT    /api/v1/client/projects/<id>/emergency        {"allowed": bool}
    PUT    /api/v1/client/projects/<id>/lock             {"branch"?, "locked": bool, "reason"?}: the client's lock
    POST   /api/v1/client/confirmations/<id>             {"code"} or {"token"}: confirm an action that waits -> 200
    DELETE /api/v1/client/confirmations/<id>             cancel it -> 204
    The switch, the lock, emergency access, the people let in and approving a request answer 202 {"confirmation"}
    while CLIENT_CONFIRMATIONS is on: they wait until the client confirms them by e-mail
    (projects/client_confirm.py); otherwise 200 at once.
    DELETE /api/v1/client/projects/<id>/passes/<pass>    end an approved or emergency access now -> 204
    POST   /api/v1/client/requests/<id>/approve          {"hours"?, "people"?, "note"?}
    POST   /api/v1/client/requests/<id>/refuse           {"note"?}
    GET    /api/v1/client/projects/<id>/backups          the backups of its instances (where it was given them);
                                                         downloaded with GET /api/v1/backups/<file>
    GET    /api/v1/client/projects/<id>/activity         the project's audit entries (limit, offset, kind=access|all)
    GET    /api/v1/client/projects/<id>/activity/export  the same as CSV (core/audit.py), a year at most

Premium: adding a client to a project (POST, and PATCH that gives more) is part of the Business plan, like access
per project: it needs the premium "teams" package and the plan feature client_portal (entitlements.py). Everything
that protects a client works on every plan: the switch, requests, emergency access and its refusal, the activity
log, taking clients out.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from flask import Blueprint, Response, abort, g, request, session

from sudoo import accounts, invitations, projects, roles
from sudoo.config import settings
from sudoo.core import audit
from sudoo.instances import backups, stages, store
from sudoo.mail import MailError, mailer
from sudoo.projects import access, client_confirm, clients
from sudoo.repos import github
from sudoo.storage import Document

from .. import validation as check
from ..security import log, refuse_unless, refuse_without_feature, refuse_without_premium
from .responses import PREFIX, created, iso, no_content, ok

blueprint = Blueprint("clients", __name__)

ACCESS_WORDS = ("client", "access", "emergency", "credentials", "backup", "shell", "restor", "refused", "sign")
"""Audit events the client's "Access" view keeps: switches, requests, emergencies, and opening data."""
ACTIVITY_SCAN = 5000


def _fail(error: clients.ClientError) -> None:
    abort(error.status, str(error))


def _account_resource(name: str) -> dict[str, Any]:
    account = accounts.get(name) or {}
    return {"name": name, "display_name": accounts.display_name(name, account)}


def _request_resource(found: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": found.get("id"),
        "project": found.get("project"),
        "branch": found.get("branch"),
        "by": _account_resource(str(found.get("by"))),
        "reason": found.get("reason"),
        "hours": found.get("hours"),
        "status": found.get("status"),
        "asked_at": iso(found.get("at")),
        "decided_by": found.get("decided_by"),
        "decided_at": iso(found.get("decided_at")),
        "people": [_account_resource(str(u)) for u in found.get("users") or ()],
        "until": iso(found.get("until")),
        "note": found.get("note"),
    }


def _pass_resource(found: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": found.get("id"),
        "kind": found.get("kind"),
        "branch": found.get("branch"),
        "people": [_account_resource(str(u)) for u in found.get("users") or ()],
        "by": found.get("by"),
        "at": iso(found.get("at")),
        "until": iso(found.get("until")),
        "reason": found.get("reason"),
    }


def _branches_of(project: Mapping[str, Any]) -> list[str]:
    known = set(projects.by_branch(project["id"])) | set(project.get("branches") or {})
    known |= set(clients.part(project).get("branches") or {})
    return sorted(b for b in known if b)


def _switches(project: Mapping[str, Any], user: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "project": clients.describe(project, None, user),
        "branches": {b: clients.describe(project, b, user) for b in _branches_of(project)},
    }


# ---------------------------------------------------------------- the panel's people


def _staff_project(project_id: str, permission: str = "instances.view", branch: str | None = None) -> Document:
    if not projects.ID_RE.match(project_id or ""):
        abort(404, "This project does not exist.")
    found = projects.get(project_id)
    if found is None or not access.sees_project(g.user, found):
        abort(404, "This project does not exist.")
    if access.is_client(g.user):
        abort(404, "This project does not exist.")
    refused = access.refusal(g.user, permission, found, branch)
    if refused:
        log(f"refused: {permission} in project {project_id}", ok=False, detail=refused[1])
        abort(*refused)
    return found


@blueprint.get("/projects/<project_id>/clients")
def read_clients(project_id: str) -> Response:
    found = _staff_project(project_id)
    manages = "clients.manage" in access.role_permissions(g.user)
    listed = []
    for item in clients.entries(found):
        account = accounts.get(str(item["id"])) or {}
        listed.append({
            **_account_resource(str(item["id"])),
            "email": account.get("email") if manages else None,
            "backups": bool(item.get("backups")),
            "shell": bool(item.get("shell")),
            "added_by": item.get("added_by"),
            "added_at": iso(item.get("added_at")),
        })  # fmt: skip
    invited = [
        {"id": key, "email": inv.get("email") if manages else None, "expires_at": iso(inv.get("expires"))}
        for key, inv in invitations.all_invitations().items()
        if inv.get("role") == roles.CLIENT and any(p.get("id") == project_id for p in inv.get("projects") or ())
    ]
    allow = [_account_resource(str(e.get("id"))) for e in clients.part(found).get("allow") or ()]
    return ok(
        {
            "clients": listed,
            "invited": invited,
            **_switches(found, g.user),
            "allow": allow,
            "emergency_allowed": clients.emergency_allowed(found),
            "passes": [_pass_resource(p) for p in clients.active_passes(found)],
            "requests": [_request_resource(r) for r in clients.requests(project_id)[:50]],
        },
        meta={
            "may_manage": manages and access.allowed(g.user, "projects.access", found),
            "may_force": _may_force(),
            "emergency_minutes": settings.client_emergency_minutes,
            "request_hours": settings.client_request_hours,
            "request_max_hours": settings.client_request_max_hours,
            "portal_url": clients.portal_url() or None,
        },
    )


def _may_force() -> bool:
    return access.is_super_admin(g.user) and not g.user.get("token_id")


def _gate(project_id: str) -> None:
    """Adding a client is part of the Business plan (see the module's description)."""
    refuse_without_premium("teams", f"add a client to {project_id}")
    refuse_without_feature("client_portal", f"add a client to {project_id}")


@blueprint.post("/projects/<project_id>/clients")
def add_client(project_id: str) -> Response:
    refuse_unless("clients.manage", f"add a client to {project_id}")
    found = _staff_project(project_id, "projects.access")
    data = check.body()
    email = check.text(data, "email", max_length=254).strip()
    names = {key: check.text(data, key, max_length=60).strip() for key in ("first_name", "last_name")}
    given = {"backups": check.flag(data, "backups"), "shell": check.flag(data, "shell")}
    if not email:
        abort(400, "Enter the client's e-mail address.")
    _gate(project_id)
    name = accounts.find(email.lower())
    if name is not None:
        account = accounts.get(name) or {}
        if roles.normalize(str(account.get("role"))) != roles.CLIENT:
            abort(409, "This address belongs to someone of your team, not to a client account.")
        try:
            clients.add_client(project_id, name, g.user["name"], **given)
        except clients.ClientError as error:
            _fail(error)
        return created({"client": name, "invited": False}, f"{PREFIX}/projects/{project_id}/clients")
    if clients.any_closed(found):
        abort(409, "The project or one of its branches is closed by its client: a new client can be added once the "
                   "client opens access again.")  # fmt: skip
    entry = {"id": project_id, **given}
    pending = invitations.pending_for(email)
    if pending is not None:
        invitation = invitations.get(pending) or {}
        if invitation.get("role") != roles.CLIENT:
            abort(409, "This address is invited already as someone of your team.")
        invitations.add_project(pending, entry)
        log("client invitation extended", detail=f"{project_id}: {email}")
        return created({"invitation": pending, "invited": True}, f"{PREFIX}/invitations/{pending}")
    if not mailer().ready:
        abort(400, "E-mail is not set up, so the invitation cannot be sent. Set it up in Settings, E-mail.")
    wait = invitations.SEND_LIMIT.seconds_to_wait(g.user["name"])
    if wait:
        abort(429, f"Too many invitations sent in the last hour. Try again in {max(1, wait // 60)} minutes.")
    try:
        invitation_id, link = invitations.create(
            email, roles.CLIENT, g.user["name"], names["first_name"], names["last_name"], projects=[entry]
        )
    except ValueError as error:
        abort(400, str(error))
    from .invitations import send_invitation

    invitation = invitations.get(invitation_id) or {}
    try:
        send_invitation(link, invitation)
    except MailError as error:
        invitations.revoke(invitation_id)
        log("invitation not sent", detail=f"{email}: {error}", ok=False)
        abort(502, f"The invitation could not be sent: {error}")
    log("client invited", detail=f"{project_id}: {email}")
    return created({"invitation": invitation_id, "invited": True}, f"{PREFIX}/invitations/{invitation_id}")


@blueprint.patch("/projects/<project_id>/clients/<name>")
def change_client(project_id: str, name: str) -> Response:
    refuse_unless("clients.manage", f"change a client of {project_id}")
    found = _staff_project(project_id, "projects.access")
    known = clients.entry(found, name) or abort(404, f"{name} is not a client of this project.")
    data = check.body()
    given = {
        "backups": check.flag(data, "backups", bool(known.get("backups"))),
        "shell": check.flag(data, "shell", bool(known.get("shell"))),
    }
    if any(on and not known.get(key) for key, on in given.items()):  # gives more: as adding
        _gate(project_id)
    try:
        clients.add_client(project_id, name, g.user["name"], **given)
    except clients.ClientError as error:
        _fail(error)
    return ok({"client": name, **given})


@blueprint.delete("/projects/<project_id>/clients/<name>")
def remove_client(project_id: str, name: str) -> Response:
    refuse_unless("clients.manage", f"remove a client of {project_id}")
    _staff_project(project_id)
    try:
        clients.remove_client(project_id, name, g.user["name"])
    except clients.ClientError as error:
        _fail(error)
    return no_content()


def _branch_arg(data: Mapping[str, Any], project: Mapping[str, Any]) -> str | None:
    branch = check.text(data, "branch", max_length=200) or None
    if branch is not None and (not github.valid_branch(branch) or branch not in _branches_of(project)):
        abort(404, "There is no such branch.")
    return branch


def _hours(data: Mapping[str, Any], key: str, default: float | None) -> float | None:
    value = data.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        abort(400, f"{key}: a number of hours.")
    return float(value)


@blueprint.get("/projects/<project_id>/access-requests")
def list_requests(project_id: str) -> Response:
    _staff_project(project_id)
    return ok([_request_resource(r) for r in clients.requests(project_id)])


@blueprint.post("/projects/<project_id>/access-requests")
def ask(project_id: str) -> Response:
    found = _staff_project(project_id)
    data = check.body()
    branch = _branch_arg(data, found)
    if branch is not None and not access.sees_branch(g.user, found, branch):
        abort(404, "There is no such branch.")
    reason = check.text(data, "reason", max_length=clients.REASON_MAX)
    hours = _hours(data, "hours", float(settings.client_request_hours)) or 0.0
    if access.person(g.user) is None:
        abort(403, "Access is asked for by people with an account.")
    try:
        made = clients.ask(found, branch, str(access.person(g.user)), reason, hours)
    except clients.ClientError as error:
        _fail(error)
    return created(_request_resource(made), f"{PREFIX}/projects/{project_id}/access-requests")


@blueprint.post("/projects/<project_id>/emergency-access")
def emergency(project_id: str) -> Response:
    found = _staff_project(project_id)
    if not _may_force():
        log(f"refused: emergency access to {project_id}", ok=False)
        abort(403, "Only a super admin, signed in, may use emergency access.")
    data = check.body()
    branch = _branch_arg(data, found)
    reason = check.text(data, "reason", max_length=clients.REASON_MAX)
    typed = check.text(data, "confirm", max_length=200)
    try:
        made = clients.emergency(found, branch, g.user["name"], reason, typed)
    except clients.ClientError as error:
        _fail(error)
    return created(_pass_resource(made), f"{PREFIX}/projects/{project_id}/clients")


# ---------------------------------------------------------------- the client


def _mine(project_id: str) -> Document:
    """A project the caller is a client of; 404 for anything else (its existence is not told)."""
    if not access.is_client(g.user) or not projects.ID_RE.match(project_id or ""):
        abort(404, "This project does not exist.")
    found = projects.get(project_id)
    if found is None or clients.entry(found, access.person(g.user)) is None:
        log(f"refused: client portal {project_id}", ok=False)
        abort(404, "This project does not exist.")
    return found


def _instance(name: str) -> dict[str, Any]:
    from .resources import instance_resource

    try:
        found = instance_resource(name)
    except (KeyError, OSError, ValueError):
        return {"name": name, "status": "unknown", "stage": None, "url": None, "odoo": None, "branch": None}
    return {
        "name": name,
        "status": found.get("status"),
        "stage": found.get("stage"),
        "url": found.get("url"),
        "odoo": (found.get("odoo") or {}).get("version"),
        "branch": (found.get("git") or {}).get("branch"),
        "current_job": (found.get("current_job") or {}).get("action"),
    }


def _summary(project: Mapping[str, Any]) -> dict[str, Any]:
    pending = clients.requests(str(project["id"]), "pending")
    active = clients.active_passes(project)
    return {
        "id": project["id"],
        "name": project.get("name") or project["id"],
        "switch": clients.describe(project),
        "closed_branches": sorted(b for b in _branches_of(project) if clients.closed(project, b)),
        "pending_requests": len(pending),
        "emergency_active": any(p.get("kind") == "emergency" for p in active),
        "instances": len(projects.instances(str(project["id"]))),
    }


@blueprint.get("/client/projects")
def my_projects() -> Response:
    if not access.is_client(g.user):
        abort(404, "Only clients have a client portal.")
    found = clients.projects_of(str(access.person(g.user)))
    return ok([_summary(p) for p in found], meta={"portal_name": settings.portal_name or None})


def _staff_people(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The people of the panel's owner who see the project: those the client may name."""
    shown = []
    for name, account in sorted(accounts.all_users().items()):
        role = roles.normalize(str(account.get("role")))
        if role == roles.CLIENT:
            continue
        if access.sees_project({"name": name, "role": role}, project):
            shown.append({**_account_resource(name), "role": roles.label(role)})
    return shown


@blueprint.get("/client/projects/<project_id>")
def my_project(project_id: str) -> Response:
    found = _mine(project_id)
    me = clients.entry(found, access.person(g.user)) or {}
    grouped = projects.by_branch(project_id)
    listed = []
    for branch in _branches_of(found):
        names = grouped.get(branch, [])
        listed.append({
            "name": branch,
            "stage": projects.branch_stage(found, branch, names),
            "switch": clients.describe(found, branch),
            "instances": [_instance(n) for n in names],
        })  # fmt: skip
    order = {stages.PRODUCTION: 0, stages.STAGING: 1, stages.DEVELOPMENT: 2}
    listed.sort(key=lambda b: (order.get(str(b["stage"]), 3), str(b["name"])))
    active = clients.active_passes(found)
    since = time.time() - 7 * 86400
    notices = [_pass_resource(p) for p in clients.part(found).get("passes") or ()
               if p.get("kind") == "emergency" and float(p.get("at") or 0) > since]  # fmt: skip
    return ok(
        {
            **_summary(found),
            "branches": listed,
            "allow": [_account_resource(str(e.get("id"))) for e in clients.part(found).get("allow") or ()],
            "people": _staff_people(found),
            "emergency_allowed": clients.emergency_allowed(found),
            "passes": [_pass_resource(p) for p in active],
            "requests": [_request_resource(r) for r in clients.requests(project_id)[:50]],
            "notices": notices,
            "given": {"backups": bool(me.get("backups")), "shell": bool(me.get("shell"))},
            "locked": (clients.describe(found) or {}).get("locked"),
            "confirmations": [
                client_confirm.resource(c) for c in client_confirm.pending(project_id, str(access.person(g.user)))
            ],
        },
        meta={
            "open_hours": clients.open_choices(),
            "open_max_hours": settings.client_open_max_hours,
            "request_max_hours": settings.client_request_max_hours,
            "emergency_minutes": settings.client_emergency_minutes,
            "confirmations": client_confirm.required(),
            "confirm_minutes": settings.client_confirm_minutes,
        },
    )


def _describe_action(kind: str, action: Mapping[str, Any]) -> str:
    """The action in a sentence for the client's e-mail and log."""
    where = f"the branch {action['branch']}" if action.get("branch") else "the whole project"
    if kind == "switch":
        if action.get("inherit"):
            return f"Let {where} follow the project's access again"
        state = action["state"]
        if state == "timed":
            return f"Open {where} for {action['hours']:g} hours"
        return f"{'Close' if state == 'closed' else 'Open'} {where}"
    if kind == "lock":
        return f"{'Lock' if action['locked'] else 'Unlock'} {where}"
    if kind == "emergency":
        return "Allow emergency access" if action["allowed"] else "Turn emergency access off"
    if kind == "allow":
        names = [accounts.display_name(n, accounts.get(n) or {}) for n in action["people"]]
        return "Let in, even while closed: " + (", ".join(names) or "nobody")
    if kind == "approve":
        request_found = clients.request_of(str(action["request"])) or {}
        who = ", ".join(
            accounts.display_name(n, accounts.get(n) or {}) for n in action.get("people") or [request_found.get("by")]
        )
        hours = action.get("hours") or request_found.get("hours")
        return f"Approve the access request of {request_found.get('by')}: {who} for {hours:g} hours"
    if kind.startswith("maintenance_"):
        from . import maintenance

        return maintenance.describe(kind, dict(action))
    return kind


def _run(kind: str, project_id: str, action: Mapping[str, Any], by: str) -> Any:
    """Make the change (once confirmed, or at once without confirmations). Raises clients.ClientError."""
    if kind == "switch":
        return clients.set_switch(project_id, action.get("branch"), str(action["state"]), by, action.get("hours"),
                                  bool(action.get("inherit")))  # fmt: skip
    if kind == "lock":
        return clients.set_lock(
            project_id, action.get("branch"), bool(action["locked"]), by, str(action.get("reason") or "")
        )
    if kind == "emergency":
        return clients.set_emergency(project_id, bool(action["allowed"]), by)
    if kind == "allow":
        return clients.set_allow(project_id, list(action["people"]), by)
    if kind == "approve":
        return clients.decide(str(action["request"]), by, True, action.get("hours"), action.get("people"),
                              str(action.get("note") or ""))  # fmt: skip
    if kind in ("maintenance_window", "maintenance_approve"):
        from . import maintenance

        return maintenance.run(kind, project_id, dict(action), by)
    raise clients.ClientError("Unknown action.", 400)


def _act(project: Mapping[str, Any], kind: str, action: dict[str, Any]) -> Response:
    """Run the action, or (CLIENT_CONFIRMATIONS) ask the client to confirm it by e-mail: 202 with the confirmation."""
    me = str(access.person(g.user))
    project_id = str(project["id"])
    if not client_confirm.required():
        try:
            _run(kind, project_id, action, me)
        except clients.ClientError as error:
            _fail(error)
        return ok({"done": True, **_switches(projects.get(project_id) or project)})
    try:
        made = client_confirm.ask(
            project, me, str(session.get("sid") or ""), kind, action, _describe_action(kind, action)
        )
    except client_confirm.ConfirmError as error:
        abort(error.status, str(error))
    return ok({"done": False, "confirmation": client_confirm.resource(made)}, status=202)


@blueprint.put("/client/projects/<project_id>/switch")
def set_switch(project_id: str) -> Response:
    found = _mine(project_id)
    data = check.body()
    branch = _branch_arg(data, found)
    state = check.one_of(check.text(data, "state", max_length=10), clients.STATES, "state: open, closed or timed.")
    inherit = check.flag(data, "inherit")
    if inherit and branch is None:
        abort(400, "inherit applies to a branch.")
    hours = _hours(data, "hours", None)
    if state == "timed" and (hours is None or not 0 < hours <= settings.client_open_max_hours):
        abort(400, f"Choose how long to open it: at most {settings.client_open_max_hours} hours.")
    return _act(found, "switch", {"branch": branch, "state": state, "hours": hours, "inherit": inherit})


@blueprint.put("/client/projects/<project_id>/lock")
def set_lock(project_id: str) -> Response:
    found = _mine(project_id)
    data = check.body()
    branch = _branch_arg(data, found)
    if not isinstance(data.get("locked"), bool):
        abort(400, "locked: true or false.")
    reason = check.text(data, "reason", max_length=clients.REASON_MAX)
    if not data["locked"] and clients.client_lock(found, branch) is None:
        abort(409, "This is not locked.")
    return _act(found, "lock", {"branch": branch, "locked": bool(data["locked"]), "reason": reason})


@blueprint.put("/client/projects/<project_id>/allow")
def set_allow(project_id: str) -> Response:
    found = _mine(project_id)
    people = check.body().get("people") or []
    if not isinstance(people, list) or not all(isinstance(p, str) for p in people):
        abort(400, "people: a list of account names.")
    known = {p["name"] for p in _staff_people(found)}
    unknown = [p for p in people if p not in known]
    if unknown:
        abort(400, f"Name people of the team who see this project ({', '.join(unknown[:5])} do not).")
    return _act(found, "allow", {"people": sorted(dict.fromkeys(people))})


@blueprint.put("/client/projects/<project_id>/emergency")
def set_emergency(project_id: str) -> Response:
    found = _mine(project_id)
    data = check.body()
    if not isinstance(data.get("allowed"), bool):
        abort(400, "allowed: true or false.")
    return _act(found, "emergency", {"allowed": bool(data["allowed"])})


@blueprint.post("/client/confirmations/<confirmation_id>")
def confirm_action(confirmation_id: str) -> Response:
    """{"code"} or {"token"} (the link's): run the action that waited, in the session that asked."""
    if not access.is_client(g.user):
        abort(404, "There is no such confirmation.")
    data = check.body()
    me = str(access.person(g.user))
    try:
        found = client_confirm.confirm(confirmation_id[:40], me, str(session.get("sid") or ""),
                                       code=check.text(data, "code", max_length=12),
                                       token=check.text(data, "token", max_length=100))  # fmt: skip
    except client_confirm.ConfirmError as error:
        abort(error.status, str(error))
    project = _mine(str(found["project"]))
    try:
        _run(str(found["kind"]), str(project["id"]), found.get("action") or {}, me)
    except clients.ClientError as error:
        _fail(error)
    return ok({"done": True, "confirmation": client_confirm.resource(found), "project": project["id"]})


@blueprint.delete("/client/confirmations/<confirmation_id>")
def cancel_action(confirmation_id: str) -> Response:
    if not access.is_client(g.user):
        abort(404, "There is no such confirmation.")
    try:
        client_confirm.cancel(confirmation_id[:40], str(access.person(g.user)))
    except client_confirm.ConfirmError as error:
        abort(error.status, str(error))
    return no_content()


@blueprint.delete("/client/projects/<project_id>/passes/<pass_id>")
def end_pass(project_id: str, pass_id: str) -> Response:
    _mine(project_id)
    try:
        clients.end_pass(project_id, pass_id[:40], str(access.person(g.user)))
    except clients.ClientError as error:
        _fail(error)
    return no_content()


def _my_request(request_id: str) -> Document:
    found = clients.request_of(request_id[:40]) if request_id else None
    if found is None:
        abort(404, "There is no such request.")
    _mine(str(found.get("project")))
    return found


@blueprint.post("/client/requests/<request_id>/approve")
def approve(request_id: str) -> Response:
    found = _my_request(request_id)
    data = check.body()
    hours = _hours(data, "hours", None)
    people = data.get("people")
    if people is not None:
        if not isinstance(people, list) or not all(isinstance(p, str) for p in people) or not people:
            abort(400, "people: a list of account names.")
        project = projects.get(str(found.get("project"))) or {}
        known = {p["name"] for p in _staff_people(project)}
        if any(p not in known for p in people):
            abort(400, "Name people of the team who see this project.")
    note = check.text(data, "note", max_length=clients.REASON_MAX)
    if hours is not None and not 0 < hours <= settings.client_request_max_hours:
        abort(400, f"Give at most {settings.client_request_max_hours} hours.")
    if found.get("status") != "pending":
        abort(409, f"This request was {found.get('status')} already.")
    project = _mine(str(found.get("project")))
    return _act(project, "approve", {"request": request_id, "hours": hours, "people": people, "note": note})


@blueprint.post("/client/requests/<request_id>/refuse")
def refuse(request_id: str) -> Response:
    _my_request(request_id)
    note = check.text(check.body(), "note", max_length=clients.REASON_MAX)
    try:
        decided = clients.decide(request_id, str(access.person(g.user)), False, note=note)
    except clients.ClientError as error:
        _fail(error)
    return ok(_request_resource(decided))


@blueprint.get("/client/projects/<project_id>/backups")
def my_backups(project_id: str) -> Response:
    found = _mine(project_id)
    if not access.allowed(g.user, "backups.manage", found) and not any(
        access.allowed(g.user, "backups.manage", found, b) for b in _branches_of(found)
    ):
        return ok([], meta={"given": False})
    members = set(projects.instances(project_id))
    shown = [
        {
            "file": b["file"],
            "instance": b["instance"],
            "kind": b["kind"],
            "size_bytes": b["size"],
            "created_at": iso(b["created"]),
        }
        for b in backups.all_backups()
        if b["instance"] in members
        and store.exists(b["instance"])
        and access.allowed_instance(g.user, "backups.manage", b["instance"])
    ]
    return ok(shown, meta={"given": True})


def _activity(project_id: str, kind: str) -> list[dict[str, Any]]:
    found, _ = audit.search({"project": project_id}, "ts", True, ACTIVITY_SCAN, 0)
    me = access.person(g.user)
    shown = []
    for entry in found:
        event = str(entry.get("event") or "")
        if kind == "access" and not any(word in event for word in ACCESS_WORDS):
            continue
        shown.append({**entry, "ip": entry.get("ip") if entry.get("user") == me else None})
    return shown


@blueprint.get("/client/projects/<project_id>/activity")
def my_activity(project_id: str) -> Response:
    _mine(project_id)
    limit = check.whole_number(request.args.get("limit", "100"), "limit", minimum=1, maximum=500)
    offset = check.whole_number(request.args.get("offset", "0"), "offset", minimum=0, maximum=ACTIVITY_SCAN)
    kind = check.one_of(request.args.get("kind", "all"), ("all", "access"), 'kind is "all" or "access".')
    entries = _activity(project_id, kind)
    chosen = [
        {
            "at": iso(e.get("ts")),
            "user": e.get("user"),
            "event": e.get("event"),
            "allowed": bool(e.get("ok")),
            "branch": e.get("branch"),
            "instance": e.get("instance"),
            "detail": e.get("detail"),
            "address": e.get("ip"),
            "emergency": "emergency" in str(e.get("event") or ""),
        }
        for e in entries[offset : offset + limit]
    ]
    return ok(chosen, meta={"total": len(entries), "limit": limit, "offset": offset})


@blueprint.get("/client/projects/<project_id>/activity/export")
def export_activity(project_id: str) -> Response:
    _mine(project_id)
    kind = check.one_of(request.args.get("kind", "all"), ("all", "access"), 'kind is "all" or "access".')
    since = time.time() - audit.EXPORT_MAX_DAYS * 86400
    rows = [audit.row(e) for e in _activity(project_id, kind) if e["ts"] >= since]
    log("activity log exported", detail=f"{project_id}: {len(rows)} entries ({kind})")
    response = Response(audit.to_csv(rows[: int(settings.audit_export_max_rows)]), mimetype="text/csv; charset=utf-8")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    response.headers["Content-Disposition"] = f'attachment; filename="activity-{project_id}-{stamp}.csv"'
    response.headers["Cache-Control"] = "no-store"
    return response
