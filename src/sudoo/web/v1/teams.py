"""Teams: named groups of accounts that project and branch access is given to (teams.py, projects/access.py).

    GET    /api/v1/teams              every team with its members and the projects that name it
    POST   /api/v1/teams              {"name", "description"?, "members"?: [account names]} -> 201
    GET    /api/v1/teams/<id>         one team
    PATCH  /api/v1/teams/<id>         {"name"?, "description"?, "members"?}
    DELETE /api/v1/teams/<id>         -> 204; the team leaves every grant and lock

Reading needs teams.manage or projects.access (who grants access chooses among the teams); changing needs
teams.manage. Every change is audit-logged with the members added and removed. Making a team, renaming it and adding
members come with the premium "teams" package (402 without it, with the plans that have it); reading, taking members
out and deleting a team are always possible.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from flask import Blueprint, Response, abort, g

from sudoo import accounts, premium, projects, teams
from sudoo.projects import access

from .. import validation as check
from ..security import PremiumRequired, can, log, refuse_unless, refuse_without_premium, require
from .responses import PREFIX, iso, no_content, ok

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


def _premium_view(name: str) -> Any:
    """The teams package's view `name` (create_team, change_team), or a 402 with the plans that have it."""
    refuse_without_premium("teams", name.replace("_", " "))
    found = premium.hook(premium.ACCESS, "teams")
    if found is None:
        raise PremiumRequired(premium.refusal("teams"))
    return getattr(found, name)


@blueprint.post("/teams")
@require("teams.manage")
def create_team() -> Response:
    return cast(Response, _premium_view("create_team")())


@blueprint.patch("/teams/<team_id>")
@require("teams.manage")
def change_team(team_id: str) -> Response:
    """Taking members out is always possible; any other change comes with the teams package."""
    before = _team(team_id)
    data = check.body()
    if set(data) == {"members"} and set(_members(data)) <= set(before.get("members") or []):
        removed = sorted(set(before.get("members") or []) - set(_members(data)))
        try:
            team = teams.remove_members(team_id, removed, g.user["name"])
        except teams.TeamError as error:
            abort(error.status, str(error))
        log("team changed", detail=f"{team_id}: removed {', '.join(removed) or 'nobody'}"[:300])
        access.forget_restrictions()
        return ok(team_resource(team))
    return cast(Response, _premium_view("change_team")(team_id))


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
