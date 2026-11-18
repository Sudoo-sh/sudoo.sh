"""The web shell (webshell/): what may be opened, one-time tickets, open and past shells, recordings.

    GET    /api/v1/instances/<name>/shell             what the caller may open in this instance, and why not
    POST   /api/v1/instances/<name>/shell/tickets     a one-time ticket for the shell's WebSocket -> 201
           {"target": "shell" | "odoo-shell" | "psql", "as_root": false, "confirm_production": false}
    GET    /api/v1/instances/<name>/shell/socket      the WebSocket itself (shell_socket.py, on the events server)
    GET    /api/v1/shells                             open shells and past ones (?instance=)       shells.supervise
    DELETE /api/v1/shells/<id>                        end an open shell -> 204                     shells.supervise
    GET    /api/v1/shells/<id>/recording              a recording: header and events (asciicast v2) shells.supervise

A ticket is only issued to a person in the panel (session cookie, Origin and X-CSRF-Token, like every change),
never to an API token. It works once, within SHELL_TICKET_SECONDS, from the same session, for this instance.
"""

from __future__ import annotations

from typing import Any, NoReturn

from flask import Blueprint, Response, abort, g, request, session

from sudoo.config import settings
from sudoo.instances import monitor, store
from sudoo.webshell import policy, recordings, sessions, tickets
from sudoo.webshell.policy import Refused

from .. import validation as check
from ..security import client_ip, log, require
from .responses import iso, no_content, ok

blueprint = Blueprint("shell", __name__)

ASLEEP = ("paused", "switched_off", "stopped")


def _refuse(refused: Refused, name: str | None = None) -> NoReturn:
    log("shell refused", instance=name, detail=str(refused), ok=False)
    abort(refused.status, str(refused))


def _options(name: str) -> dict[str, Any]:
    targets: dict[str, Any] = {}
    production = False
    for target in policy.TARGETS:
        try:
            plan = policy.plan(g.user, name, target, confirmed=True)
            production = plan.production
            targets[target] = {"allowed": True, "user": plan.container_user, "recorded": plan.recorded}
        except Refused as refused:
            if refused.code == "missing":
                abort(404, str(refused))
            targets[target] = {"allowed": False, "code": refused.code, "reason": str(refused)}
    try:
        root_plan = policy.plan(g.user, name, "shell", as_root=True, confirmed=True)
        root: dict[str, Any] = {"allowed": True, "recorded": root_plan.recorded}
    except Refused as refused:
        root = {"allowed": False, "code": refused.code, "reason": str(refused)}
    status = monitor.status(name)
    return {
        "enabled": bool(settings.shell_enabled),
        "production": production or targets["shell"].get("code", "").startswith("production"),
        "running": status not in ASLEEP,
        "status": status,
        "targets": targets,
        "root": root,
        "limits": {
            "idle_minutes": settings.shell_idle_minutes,
            "max_minutes": settings.shell_max_minutes,
            "per_user": settings.shell_max_per_user,
            "per_instance": settings.shell_max_per_instance,
        },
        "open": len(sessions.open_sessions(instance=name)),
        "open_mine": len(sessions.open_sessions(user=g.user["name"])),
    }


@blueprint.get("/instances/<name>/shell")
@require("instances.view")
def options(name: str) -> Response:
    if not store.exists(check.instance_name(name)):
        abort(404, "This instance does not exist.")
    return ok(_options(name))


@blueprint.post("/instances/<name>/shell/tickets")
@require("instances.view")
def ticket(name: str) -> Response:
    check.instance_name(name)
    data = check.body()
    target = check.text(data, "target", max_length=20, default="shell")
    as_root = check.flag(data, "as_root")
    confirmed = check.flag(data, "confirm_production")
    try:
        plan = policy.plan(g.user, name, target, as_root=as_root, confirmed=confirmed)
    except Refused as refused:
        _refuse(refused, name if refused.code != "missing" else None)
    if monitor.status(name) in ASLEEP:
        abort(409, "The instance is not running: start it, then open the shell.")
    refusal = sessions.room_for(g.user["name"], name)
    if refusal is not None:
        _refuse(refusal, name)
    sid = session.get("sid")
    if not isinstance(sid, str):
        abort(401, "Your session has ended. Please sign in again.")
    try:
        token, seconds = tickets.issue(
            user=g.user["name"],
            sid=sid,
            instance=name,
            target=target,
            as_root=as_root,
            confirmed=confirmed,
            ip=client_ip(),
        )
    except OverflowError as error:
        abort(429, str(error))
    return ok(
        {
            "ticket": token,
            "expires_in": seconds,
            "socket": f"/api/v1/instances/{name}/shell/socket",
            "protocol": "sudoo.shell.v1",
            "target": plan.target,
            "container": plan.container,
            "container_user": plan.container_user,
            "root": plan.root,
            "production": plan.production,
            "recorded": plan.recorded,
        },
        status=201,
    )


# ---------------------------------------------------------------- supervising (super admins)


def _open_resource(found: sessions.Session) -> dict[str, Any]:
    plan = found.plan
    return {
        "id": found.id,
        "instance": plan.instance,
        "target": plan.target,
        "container_user": plan.container_user,
        "root": plan.root,
        "production": plan.production,
        "user": found.user,
        "ip": found.ip,
        "started_at": iso(found.started),
        "ended_at": None,
        "duration_seconds": None,
        "reason": None,
        "recorded": plan.recorded,
        "recording": False,
        "open": True,
    }


def _past_resource(document: dict[str, Any]) -> dict[str, Any]:
    session_id = str(document.get("id", ""))
    return {
        "id": session_id,
        "instance": document.get("instance"),
        "target": document.get("target"),
        "container_user": document.get("container_user"),
        "root": bool(document.get("root")),
        "production": bool(document.get("production")),
        "user": document.get("user"),
        "ip": document.get("ip"),
        "started_at": iso(document.get("started")),
        "ended_at": iso(document.get("ended")),
        "duration_seconds": document.get("duration"),
        "reason": document.get("reason"),
        "recorded": bool(document.get("recorded")),
        "recording": bool(document.get("recorded"))
        and not document.get("recording_removed")
        and recordings.exists(session_id),
        "open": False,
    }


@blueprint.get("/shells")
@require("shells.supervise")
def list_shells() -> Response:
    instance = request.args.get("instance") or None
    if instance is not None:
        check.instance_name(instance)
    now_open = sessions.open_sessions(instance=instance)
    open_ids = {s.id for s in now_open}
    past = [_past_resource(d) for d in recordings.past(instance) if d.get("id") not in open_ids and d.get("ended")]
    return ok([*(_open_resource(s) for s in now_open), *past])


@blueprint.delete("/shells/<session_id>")
@require("shells.supervise")
def end_shell(session_id: str) -> Response:
    if not session_id.isalnum() or not sessions.end(session_id, "ended"):
        abort(404, "No open shell has this id.")
    log("shell ended by a super admin", detail=session_id)
    return no_content()


@blueprint.get("/shells/<session_id>/recording")
@require("shells.supervise")
def recording(session_id: str) -> Response:
    if not session_id.isalnum() or len(session_id) > 64:
        abort(404, "No recording has this id.")
    document = recordings.get(session_id)
    try:
        header, events = recordings.read(session_id)
    except FileNotFoundError:
        abort(404, "No recording has this id.")
    except Exception:  # noqa: BLE001 - changed on disk or another vault key: say so, show nothing
        abort(409, "This recording cannot be read: it was changed, or was made with another vault key.")
    log("shell recording played", instance=(document or {}).get("instance"), detail=session_id)
    return ok({"session": _past_resource(document) if document else None, "header": header, "events": events})
