"""Adding members to a team gives them what the team is given (grants and locks that name it): only someone who may
change that access, and whom the locks allow, may add members; super admins may always. Taking members out stays
open to teams.manage."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from sudoo import accounts, projects, teams
from sudoo.projects import access
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
PASSWORD = "copper-meadow-violin-{}"
TEAM = "release-team"


@pytest.fixture
def project() -> Iterator[str]:
    found = projects.create(
        repository="Acme/Locked", odoo_version="18.0", edition="community", origin="linked", user="t"
    )
    storage().put(teams.COLLECTION, TEAM, {"id": TEAM, "name": "Release team", "members": ["ta-member"]})
    access.forget_restrictions()
    yield str(found["id"])
    projects.remove(found["id"])
    storage().delete(teams.COLLECTION, TEAM)
    access.forget_restrictions()


def _person(app: Flask, name: str, role: str) -> Client:
    if accounts.get(name) is None:
        accounts.create(name, role, PASSWORD.format(name))
    return sign_in(app, name, PASSWORD.format(name))


def _set_access(project_id: str, value: dict[str, Any]) -> None:
    projects.change(project_id, lambda p: {**p, "access": value})
    access.forget_restrictions()


def _join(client: Client, members: list[str]) -> int:
    web, headers = client
    return web.patch(f"/api/v1/teams/{TEAM}", json={"members": members}, headers=headers).status_code


def test_an_admin_the_lock_does_not_allow_cannot_join_the_team_it_allows(
    app: Flask, project: str, admin: Client
) -> None:
    _person(app, "ta-member", "admin")
    other = _person(app, "ta-admin", "admin")
    _set_access(project, {"locks": {"main": {"by": "root", "allow": [{"type": "team", "id": TEAM}]}}})
    assert _join(other, ["ta-member", "ta-admin"]) == 403
    assert "ta-admin" not in (teams.get(TEAM) or {})["members"]
    assert _join(other, []) == 200  # taking members out stays open
    assert _join(admin, ["ta-member", "ta-admin"]) == 402  # a super admin passes (the teams package is not here)


def test_an_admin_hidden_from_a_restricted_project_cannot_join_its_team(
    app: Flask, project: str, admin: Client
) -> None:
    _person(app, "ta-member", "admin")
    other = _person(app, "ta-admin", "admin")
    _set_access(project, {"mode": "restricted", "grants": [{"type": "team", "id": TEAM, "level": "admin"}]})
    assert _join(other, ["ta-member", "ta-admin"]) == 403
    web, headers = other
    shown = web.get(f"/api/v1/teams/{TEAM}", headers=headers).get_json()["data"]
    assert shown["projects"] == []  # the restricted project is not named to someone who cannot see it
    _set_access(project, {"branches": {"main": {"restricted": True, "grants": [{"type": "team", "id": TEAM,
                                                                                   "level": "admin"}]}}})  # fmt: skip
    assert _join(other, ["ta-member", "ta-admin"]) == 403
    _set_access(project, {"grants": [{"type": "team", "id": TEAM, "level": "admin"}]})  # open to admins: fine
    assert _join(other, ["ta-member", "ta-admin"]) == 402
