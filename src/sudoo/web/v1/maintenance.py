"""Maintenance windows (maintenance.py): the maintenance Sudoo.sh planned on this panel, and choosing its time.

The panel's people
    GET  /api/v1/maintenance                       open items and those that ended in the last 30 days, with the
                                                   quick choices and the bounds of each (instances.view); meta: the
                                                   time zone, the default window, whether the caller may choose
    PUT  /api/v1/maintenance/<id>/window           {"preset": tonight|weekend|default} or {"start": ISO time}:
                                                   choose when it runs (maintenance.manage)

The client (in the portal; security.CLIENT_ENDPOINTS)
    GET  /api/v1/client/maintenance                the items that work on its projects (only its own instances)
    PUT  /api/v1/client/maintenance/<id>/window    as above, when MAINTENANCE_CLIENT_CHOICE is on
    POST /api/v1/client/maintenance/<id>/approve   {"project"?}: let it run on its project while the project is
                                                   closed or locked
    Both wait for the client's e-mail confirmation while CLIENT_CONFIRMATIONS is on (202 {"confirmation"}), like the
    portal's other major changes; otherwise 200 at once.

Nothing here comes from the hub: the hub's items arrive signed (command and lease, hub/marketplace.py).
"""

from __future__ import annotations

import time
from typing import Any, NoReturn

from flask import Blueprint, Response, abort, g, session

from sudoo import maintenance, projects
from sudoo.config import settings
from sudoo.projects import access, client_confirm, clients

from .. import validation as check
from ..security import can, require
from .responses import ok

blueprint = Blueprint("maintenance", __name__)


def _refuse(error: maintenance.MaintenanceError) -> NoReturn:
    abort(error.status, str(error))


def _choice(item: dict[str, Any]) -> float:
    data = check.body()
    preset = check.text(data, "preset", max_length=20)
    if preset and preset not in maintenance.PRESETS:
        abort(400, "preset: tonight, weekend or default.")
    try:
        return maintenance.start_of(item, preset or None, data.get("start"))
    except maintenance.MaintenanceError as error:
        _refuse(error)


def _item(item_id: str) -> dict[str, Any]:
    found = maintenance.get(item_id[:64])
    if found is None:
        abort(404, "There is no such maintenance.")
    return found


@blueprint.get("/maintenance")
@require("instances.view")
def list_items() -> Response:
    now = time.time()
    return ok(
        [maintenance.resource(item, now) for item in maintenance.items()],
        meta={**maintenance.settings_view(), "may_choose": can("maintenance.manage")},
    )


@blueprint.put("/maintenance/<item_id>/window")
@require("maintenance.manage")
def choose(item_id: str) -> Response:
    found = _item(item_id)
    start = _choice(found)
    try:
        changed = maintenance.choose(found["id"], start, g.user["name"])
    except maintenance.MaintenanceError as error:
        _refuse(error)
    return ok(maintenance.resource(changed))


# ---------------------------------------------------------------- the client


def _mine(item_id: str) -> tuple[dict[str, Any], list[str]]:
    """An item that works on the caller's projects, and those projects; 404 for anything else."""
    if not access.is_client(g.user):
        abort(404, "There is no such maintenance.")
    me = str(access.person(g.user))
    for item, overlap in maintenance.for_client(me):
        if item["id"] == item_id:
            return item, overlap
    abort(404, "There is no such maintenance.")


@blueprint.get("/client/maintenance")
def client_items() -> Response:
    if not access.is_client(g.user):
        abort(404, "Only clients have a client portal.")
    me = str(access.person(g.user))
    now = time.time()
    shown = []
    for item, overlap in maintenance.for_client(me):
        found = maintenance.resource(item, now, projects_shown=overlap)
        found["projects"] = [{"id": p, "name": (projects.get(p) or {}).get("name") or p} for p in overlap]
        found["held"] = [
            p for p in overlap if clients.any_closed(projects.get(p)) or clients.any_client_lock(projects.get(p))
        ]
        shown.append(found)
    return ok(
        shown,
        meta={
            **maintenance.settings_view(),
            "may_choose": bool(settings.maintenance_client_choice),
            "confirmations": client_confirm.required(),
        },
    )


def _act(item: dict[str, Any], project_id: str, kind: str, action: dict[str, Any]) -> Response:
    me = str(access.person(g.user))
    if not client_confirm.required():
        try:
            run(kind, project_id, action, me)
        except clients.ClientError as error:
            abort(error.status, str(error))
        current = maintenance.get(item["id"]) or item
        return ok({"done": True, "maintenance": maintenance.resource(current, projects_shown=[project_id])})
    project = projects.get(project_id) or {"id": project_id}
    try:
        made = client_confirm.ask(project, me, str(session.get("sid") or ""), kind, action, describe(kind, action))
    except client_confirm.ConfirmError as error:
        abort(error.status, str(error))
    return ok({"done": False, "confirmation": client_confirm.resource(made)}, status=202)


@blueprint.put("/client/maintenance/<item_id>/window")
def client_choose(item_id: str) -> Response:
    found, overlap = _mine(item_id[:64])
    if not settings.maintenance_client_choice:
        abort(403, "Your provider chooses the time of maintenance.")
    if found.get("status") not in maintenance.CHOOSABLE:
        abort(409, "This maintenance can no longer be moved.")
    start = _choice(found)
    lo, hi = maintenance.bounds(found)
    if not lo <= start <= hi:
        abort(422, f"Choose a start between {maintenance.when(lo)} and {maintenance.when(hi)}.")
    action = {"maintenance": found["id"], "start": round(start), "project": overlap[0]}
    return _act(found, overlap[0], "maintenance_window", action)


@blueprint.post("/client/maintenance/<item_id>/approve")
def client_approve(item_id: str) -> Response:
    found, overlap = _mine(item_id[:64])
    if found.get("status") not in maintenance.CHOOSABLE:
        abort(409, "This maintenance has started or ended.")
    project_id = check.text(check.body(), "project", max_length=40) or overlap[0]
    if project_id not in overlap:
        abort(400, "project: one of your projects this maintenance works on.")
    return _act(found, project_id, "maintenance_approve", {"maintenance": found["id"], "project": project_id})


# ---------------------------------------------------------------- confirmed actions (web/v1/clients.py runs them)


def describe(kind: str, action: dict[str, Any]) -> str:
    item = maintenance.get(str(action.get("maintenance"))) or {}
    what = maintenance.title(item) if item else "maintenance"
    if kind == "maintenance_window":
        return f"Run the maintenance ({what}) at {maintenance.when(action.get('start'))}"
    return f"Let the maintenance ({what}) run on {action.get('project')} while it is closed or locked"


def run(kind: str, project_id: str, action: dict[str, Any], by: str) -> Any:
    """Make the client's change (once confirmed). Raises clients.ClientError."""
    try:
        if kind == "maintenance_window":
            if not settings.maintenance_client_choice:
                raise clients.ClientError("Your provider chooses the time of maintenance.", 403)
            return maintenance.choose(str(action["maintenance"]), float(action["start"]), by, "client", project_id)
        return maintenance.approve(str(action["maintenance"]), project_id, by)
    except maintenance.MaintenanceError as error:
        raise clients.ClientError(str(error), error.status) from None
