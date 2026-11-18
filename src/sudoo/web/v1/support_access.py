"""Support access (support/): letting Sudoo.sh support into the panel for a while.

    GET    /api/v1/support-access/status        everyone signed in: whether support is connected now, and who (the
                                                banner), and whether the caller may end it
    GET    /api/v1/support-access               support_access.manage: whether it can be granted, the choices, the
                                                grants (active first) and the sessions open now
    POST   /api/v1/support-access/grants        {"level", "hours", "scope": {"all", "projects", "instances"},
                                                "ticket"} -> 201
    DELETE /api/v1/support-access/grants/<id>   withdraw a grant -> 204
    POST   /api/v1/support-access/end           "End now": withdraw every grant, end every session -> 204

Granting needs a person (never an API token) with support_access.manage, the panel connected to Sudoo.sh
(MARKETPLACE) and SUPPORT_ACCESS on. Chosen projects and instances must be ones the person sees.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo import projects, support
from sudoo.config import settings
from sudoo.instances import store
from sudoo.projects import access
from sudoo.support import channel

from .. import validation as check
from ..security import client_ip, require
from .responses import created, iso, no_content, ok

blueprint = Blueprint("support_access", __name__)

MAX_CHOSEN = 100


def _person_only() -> None:
    if g.user.get("token_id"):
        abort(403, "Support access is granted by a person in the panel, not with an API token.")


def _session_out(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "grant_id": item["grant_id"],
        "staff_name": item["staff_name"],
        "opened_at": item.get("opened_at"),
        "expires_at": item.get("expires_at"),
    }


def _grant_out(grant: dict[str, Any], open_sessions: list[dict[str, Any]]) -> dict[str, Any]:
    scope = grant.get("scope") or {}
    revoked = grant.get("revoked_at")
    state = "active" if support.is_active(grant) else "withdrawn" if revoked else "expired"
    return {
        "id": grant["id"],
        "level": grant["level"],
        "scope": {
            "all": bool(scope.get("all")),
            "projects": scope.get("projects") or [],
            "instances": scope.get("instances") or [],
        },
        "ticket": grant.get("ticket"),
        "hours": grant.get("hours"),
        "created_by": grant.get("created_by"),
        "created_at": iso(grant.get("created_at")),
        "expires_at": iso(grant.get("expires_at")),
        "revoked_at": iso(revoked),
        "revoked_by": grant.get("revoked_by"),
        "state": state,
        "sessions": [_session_out(s) for s in open_sessions if s["grant_id"] == grant["id"]],
    }


def _available() -> str | None:
    """Why support access cannot be granted now (None: it can)."""
    from sudoo.hub import marketplace

    if not settings.support_access:
        return "off"
    if not marketplace.enabled():
        return "not_connected"
    return None


@blueprint.get("/support-access/status")
def status() -> Response:
    """The banner: open support sessions (staff names, until when). Everyone signed in sees it."""
    if g.user.get("support"):
        abort(403, "Support cannot use this part of the panel.")
    found = channel.sessions()
    return ok(
        {
            "connected": bool(found),
            "sessions": [_session_out(s) for s in found],
            "active_grants": len(support.active()),
            "can_end": access.allowed(g.user, "support_access.manage") and not g.user.get("token_id"),
        }
    )


@blueprint.get("/support-access")
@require("support_access.manage")
def overview() -> Response:
    open_sessions = channel.sessions()
    grants = support.grants()
    active = [g_ for g_ in grants if support.is_active(g_)]
    past = [g_ for g_ in grants if not support.is_active(g_)][:20]
    state = channel.state()
    return ok(
        {
            "enabled": bool(settings.support_access),
            "unavailable": _available(),
            "max_hours": support.max_hours(),
            "durations": support.durations(),
            "levels": list(support.LEVELS),
            "shell_enabled": bool(settings.shell_enabled),
            "grants": [_grant_out(item, open_sessions) for item in active + past],
            "sessions": [_session_out(s) for s in open_sessions],
            "channel": {"last_ok": iso(state["last_ok"]), "error": state["error"]},
        }
    )


def _scope(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("scope")
    if not isinstance(raw, dict):
        abort(400, "Choose what support may reach: all projects, or some projects and instances.")
    every = check.flag(raw, "all")
    project_ids, names = raw.get("projects") or [], raw.get("instances") or []
    if not isinstance(project_ids, list) or not isinstance(names, list):
        abort(400, "The projects and instances must be lists.")
    if len(project_ids) + len(names) > MAX_CHOSEN:
        abort(400, f"Choose at most {MAX_CHOSEN} projects and instances.")
    if every and (project_ids or names):
        abort(400, "Choose all projects, or some projects and instances, not both.")
    if not every and not (project_ids or names):
        abort(400, "Choose at least one project or instance, or all of them.")
    chosen_projects = []
    for project_id in dict.fromkeys(project_ids):
        found = projects.get(project_id) if isinstance(project_id, str) and projects.ID_RE.match(project_id) else None
        if found is None or not access.sees_project(g.user, found):
            abort(400, "One of the chosen projects does not exist.")
        chosen_projects.append({"id": found["id"], "name": str(found.get("name") or found["id"])})
    chosen_instances = []
    for name in dict.fromkeys(names):
        if not isinstance(name, str) or not store.NAME_RE.match(name) or not store.exists(name):
            abort(400, "One of the chosen instances does not exist.")
        if not access.sees_instance(g.user, name):
            abort(400, "One of the chosen instances does not exist.")
        chosen_instances.append(name)
    return {"all": every, "projects": chosen_projects, "instances": chosen_instances}


@blueprint.post("/support-access/grants")
@require("support_access.manage")
def grant() -> Response:
    _person_only()
    unavailable = _available()
    if unavailable == "off":
        abort(409, "Support access is switched off on this panel (Settings, Support access).")
    if unavailable == "not_connected":
        abort(409, "Support access goes through the panel's connection to Sudoo.sh: turn the marketplace on first.")
    data = check.body()
    level = check.one_of(check.text(data, "level", max_length=20), support.LEVELS,
                         "Choose the level: view, admin or admin_shell.")  # fmt: skip
    hours = data.get("hours")
    if not isinstance(hours, int) or isinstance(hours, bool) or hours not in support.durations():
        allowed = ", ".join(str(h) for h in support.durations())
        abort(400, f"Choose how long, in hours: {allowed}.")
    ticket = check.text(data, "ticket", max_length=64) or None
    if ticket and any(c in ticket for c in "<>"):
        abort(400, "The ticket number cannot contain < or >.")
    scope = _scope(data)
    if len(support.active()) >= support.MAX_ACTIVE:
        abort(409, f"At most {support.MAX_ACTIVE} grants can be active at once: withdraw one first.")
    made = support.create(g.user["name"], level, hours, scope, ticket, client_ip())
    return created(_grant_out(made, []), f"/api/v1/support-access/grants/{made['id']}")


@blueprint.delete("/support-access/grants/<grant_id>")
@require("support_access.manage")
def withdraw(grant_id: str) -> Response:
    _person_only()
    if support.get(grant_id) is None:
        abort(404, "There is no such grant.")
    support.revoke(grant_id, g.user["name"], client_ip())
    return no_content()


@blueprint.post("/support-access/end")
@require("support_access.manage")
def end_now() -> Response:
    _person_only()
    support.revoke_all(g.user["name"], client_ip())
    return no_content()
