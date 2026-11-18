"""Teams: named groups of accounts that project and branch access is given to (teams.py, projects/access.py).

    GET    /api/v1/teams              every team with its members and the projects that name it
    POST   /api/v1/teams              {"name", "description"?, "members"?: [account names]} -> 201
    GET    /api/v1/teams/<id>         one team
    PATCH  /api/v1/teams/<id>         {"name"?, "description"?, "members"?}
    DELETE /api/v1/teams/<id>         -> 204; the team leaves every grant and lock

Reading needs teams.manage or projects.access (who grants access chooses among the teams); changing needs
teams.manage. Every change is audit-logged with the members added and removed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo import accounts, projects, teams
from sudoo.projects import access

from .. import validation as check
from ..security import can, log, refuse_unless, require
from .responses import PREFIX, created, iso, no_content, ok

blueprint = Blueprint("teams", __name__)


def _used_in(team_id: str) -> list[dict[str, str]]:
    """The projects whose grants or locks name the team: [{"id", "name"}]."""
    found = []
    for project in projects.all_projects():
        current = access.access_of(project)
        lists = [current.get("grants") or ()]
        lists += [v.get("grants") or () for v in (current.get("branches") or {}).values()]
        lists += [v.get("allow") or () for v in (current.get("locks") or {}).values()]
        if any(e.get("type") == "team" and e.get("id") == team_id for entries in lists for e in entries):
            found.append({"id": str(project["id"]), "name": str(project["name"])})
    return found


def team_resource(team: Mapping[str, Any]) -> dict[str, Any]:
    known = accounts.all_users()
    return {
        "id": team["id"],
        "name": team["name"],
        "description": team.get("description") or "",
        "members": [
            {"name": name, "display_name": accounts.display_name(name, known[name]), "role": known[name]["role"]}
            for name in team.get("members") or []
            if name in known
        ],
        "projects": _used_in(str(team["id"])),
        "created_at": iso(team.get("created_at")),
        "created_by": team.get("created_by"),
        "changed_at": iso(team.get("changed_at")),
        "changed_by": team.get("changed_by"),
        "links": {"self": f"{PREFIX}/teams/{team['id']}"},
    }


def _may_read() -> None:
    if not (can("teams.manage") or can("projects.access")):
        refuse_unless("teams.manage", "read teams")


def _team(team_id: str) -> dict[str, Any]:
    found = teams.get(team_id)
    if found is None:
        abort(404, "This team does not exist.")
    return found


def _members(data: Mapping[str, Any]) -> list[str]:
    raw = data.get("members")
    if not isinstance(raw, list) or not all(isinstance(m, str) for m in raw):
        abort(400, 'members: a list of account names, for example ["ada", "omar"].')
    return [str(m) for m in raw]


@blueprint.get("/teams")
def list_teams() -> Response:
    _may_read()
    return ok([team_resource(t) for t in teams.all_teams()])


@blueprint.get("/teams/<team_id>")
def read_team(team_id: str) -> Response:
    _may_read()
    return ok(team_resource(_team(team_id)))


@blueprint.post("/teams")
@require("teams.manage")
def create_team() -> Response:
    data = check.body()
    members = _members(data) if "members" in data else []
    try:
        team = teams.create(
            check.text(data, "name", max_length=teams.NAME_MAX * 2),
            check.text(data, "description", max_length=teams.DESCRIPTION_MAX * 2),
            members,
            accounts.all_users(),
            g.user["name"],
        )
    except teams.TeamError as error:
        abort(error.status, str(error))
    log("team created", detail=f"{team['id']} ({team['name']}): members {', '.join(team['members']) or 'none'}")
    return created(team_resource(team), f"{PREFIX}/teams/{team['id']}")


@blueprint.patch("/teams/<team_id>")
@require("teams.manage")
def change_team(team_id: str) -> Response:
    before = _team(team_id)
    data = check.body()
    values: dict[str, Any] = {}
    if "name" in data:
        values["name"] = check.text(data, "name", max_length=teams.NAME_MAX * 2)
    if "description" in data:
        values["description"] = check.text(data, "description", max_length=teams.DESCRIPTION_MAX * 2)
    if "members" in data:
        values["members"] = _members(data)
    if not values:
        abort(400, "Send at least one of: name, description, members.")
    try:
        team = teams.change(team_id, g.user["name"], accounts.all_users(), **values)
    except teams.TeamError as error:
        abort(error.status, str(error))
    added = sorted(set(team["members"]) - set(before.get("members") or []))
    removed = sorted(set(before.get("members") or []) - set(team["members"]))
    changes = [k for k in ("name", "description") if k in values and values[k] != before.get(k)]
    detail = ", ".join(
        part
        for part in (
            f"{' and '.join(changes)} changed" if changes else "",
            f"added {', '.join(added)}" if added else "",
            f"removed {', '.join(removed)}" if removed else "",
        )
        if part
    )
    log("team changed", detail=f"{team_id}: {detail or 'nothing changed'}"[:300])
    access.forget_restrictions()
    return ok(team_resource(team))


@blueprint.delete("/teams/<team_id>")
@require("teams.manage")
def delete_team(team_id: str) -> Response:
    _team(team_id)
    try:
        team = teams.remove(team_id)
    except teams.TeamError as error:
        abort(error.status, str(error))
    changed = access.forget_team(team_id)
    access.forget_restrictions()
    log(
        "team deleted",
        detail=f"{team_id} ({team['name']})" + (f"; removed from {', '.join(changed)}" if changed else ""),
    )
    return no_content()
