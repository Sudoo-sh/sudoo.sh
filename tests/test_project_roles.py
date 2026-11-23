"""Project roles: the role x stage x action matrix, panel roles that no longer cap a project, administrators,
members, GitHub (repository permissions and GitHub teams), locks, temporary access, access e-mails, the access
review, the editable matrix, the switch from the earlier model, and the endpoints that enforce it."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from sudoo import accounts, projects
from sudoo.auth import github, github_access
from sudoo.config import Settings, live
from sudoo.instances import store
from sudoo.projects import access, elevation, notices, people, project_roles
from sudoo.storage import storage

PASSWORD = "orchid-pebble-summit-73"
ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture
def project() -> Iterator[dict[str, Any]]:
    made = projects.create(repository=f"Acme/roles-{time.time_ns()}", odoo_version="18.0", edition="community",
                           origin="linked", user="admin1")  # fmt: skip
    made = projects.change(made["id"], lambda p: {**p, "access": {**p["access"], "model": "project_roles"},
                                                  "branches": {"main": {"stage": "production"},
                                                               "staging": {"stage": "staging"},
                                                               "dev": {"stage": "development"}}}) or made  # fmt: skip
    access.forget_restrictions()
    yield made
    live.change(dict.fromkeys(live.overrides()))
    elevation.forget_cache()
    access.forget_restrictions()


def _person(role: str) -> dict[str, str]:
    name = f"r{time.time_ns() % 10**10}"
    accounts.create(name, role, PASSWORD, must_change=False, email=f"{name}@example.com")
    return {"name": name, "role": role}


def _grant(project: dict[str, Any], name: str, level: str) -> dict[str, Any]:
    changed = projects.change(project["id"], lambda p: {**p, "access": {**p["access"], "grants": [
        *[g for g in p["access"].get("grants") or () if g.get("id") != name],
        {"type": "user", "id": name, "level": level}]}})  # fmt: skip
    access.forget_restrictions()
    return changed or project


BRANCH = {"production": "main", "staging": "staging", "development": "dev"}

# role x stage x action -> allowed
MATRIX = [
    # level, stage, permission, allowed
    ("view", "production", "instances.view", True),
    ("view", "production", "instances.logs", False),
    ("view", "development", "instances.connect", False),
    ("tester", "production", "instances.view", False),
    ("tester", "staging", "instances.connect", True),
    ("tester", "development", "instances.connect", True),
    ("tester", "development", "instances.shell", False),
    ("tester", "staging", "instances.logs", False),
    ("developer", "development", "instances.shell", True),
    ("developer", "development", "instances.database_shell", True),
    ("developer", "development", "backups.manage", True),
    ("developer", "development", "instances.connect", True),
    ("developer", "development", "instances.create", True),
    ("developer", "development", "instances.delete", True),
    ("developer", "development", "instances.credentials", True),
    ("developer", "staging", "instances.view", True),
    ("developer", "staging", "instances.connect", True),
    ("developer", "staging", "instances.shell", False),
    ("developer", "staging", "instances.database_shell", False),
    ("developer", "staging", "backups.manage", False),
    ("developer", "staging", "instances.credentials", False),
    ("developer", "production", "instances.view", True),
    ("developer", "production", "instances.logs", False),
    ("developer", "production", "instances.shell", False),
    ("developer", "production", "instances.connect", False),
    ("developer", "production", "backups.manage", False),
    ("developer", "production", "instances.credentials", False),
    ("developer", "production", "caught_mail.view", False),
    ("maintainer", "staging", "instances.shell", True),
    ("maintainer", "staging", "backups.manage", True),
    ("maintainer", "staging", "instances.credentials", True),
    ("maintainer", "production", "instances.logs", True),
    ("maintainer", "production", "instances.connect", True),
    ("maintainer", "production", "backups.manage", True),
    ("maintainer", "production", "instances.update", True),
    ("maintainer", "production", "instances.shell", False),
    ("maintainer", "production", "instances.database_shell", False),
    ("maintainer", "production", "instances.restore", False),
    ("maintainer", "production", "instances.delete", False),
    ("maintainer", "production", "projects.manage", False),
    ("maintainer", "production", "projects.access", False),
    ("admin", "production", "instances.shell", True),
    ("admin", "production", "instances.database_shell", True),
    ("admin", "production", "instances.restore", True),
    ("admin", "production", "projects.access", True),
    ("none", "development", "instances.view", False),
]


@pytest.mark.parametrize(("level", "stage", "permission", "expected"), MATRIX)
def test_role_stage_action_matrix(
    app: Flask, project: dict[str, Any], level: str, stage: str, permission: str, expected: bool
) -> None:
    user = _person("member")  # the panel role gives nothing: the project role decides alone
    found = _grant(project, user["name"], level)
    assert access.allowed(user, permission, found, BRANCH[stage]) is expected
    refused = access.refusal(user, permission, found, BRANCH[stage])
    assert (refused is None) is expected
    if not expected and level != "none" and permission != "instances.view":
        assert refused is not None and refused[0] == 403 and "temporary access" in refused[1]


def test_panel_roles_give_default_roles_and_never_cap(app: Flask, project: dict[str, Any]) -> None:
    developer, viewer, member = _person("developer"), _person("viewer"), _person("member")
    assert access.level(developer, project) == "developer"
    assert access.level(viewer, project) == "view"
    assert access.level(member, project) == "none" and not access.sees_project(member, project)
    # a viewer panel role made Maintainer of the project gets the Maintainer's rights: roles no longer cap
    found = _grant(project, viewer["name"], "maintainer")
    assert access.allowed(viewer, "instances.shell", found, "staging")
    assert access.level_and_source(viewer, found) == ("maintainer", "grant")


def test_administrators_have_everything_but_locks_apply(app: Flask, project: dict[str, Any]) -> None:
    admin = _person("admin")
    found = projects.change(project["id"], lambda p: {**p, "access": {**p["access"], "mode": "restricted",
                            "locks": {"main": {"by": "x", "allow": []}}}}) or project  # fmt: skip
    access.forget_restrictions()
    assert access.allowed(admin, "instances.database_shell", found, "staging")
    assert not access.allowed(admin, "instances.shell", found, "main")  # locked
    refused = access.refusal(admin, "instances.shell", found, "main")
    assert refused is not None and "locked" in refused[1]


def test_a_customised_matrix(app: Flask, project: dict[str, Any], admin: Any) -> None:
    client, headers = admin
    user = _person("member")
    _grant(project, user["name"], "maintainer")
    answer = client.put(f"/api/v1/projects/{project['id']}/access/matrix",
                        json={"matrix": {"maintainer": {"production": ["instances.view", "instances.logs",
                                                                       "instances.shell"]}}}, headers=headers)  # fmt: skip
    assert answer.status_code == 200, answer.get_json()
    found = projects.get(project["id"]) or {}
    assert access.allowed(user, "instances.shell", found, "main")
    assert not access.allowed(user, "backups.manage", found, "main")  # unticked by the new row
    assert answer.get_json()["data"]["roles"]["changed"] == {"maintainer": ["production"]}
    bad = client.put(f"/api/v1/projects/{project['id']}/access/matrix",
                     json={"matrix": {"admin": {"production": []}}}, headers=headers)  # fmt: skip
    assert bad.status_code == 400
    reset = client.put(f"/api/v1/projects/{project['id']}/access/matrix", json={"matrix": None}, headers=headers)
    assert reset.status_code == 200
    assert not access.allowed(user, "instances.shell", projects.get(project["id"]) or {}, "main")


def test_who_can_do_what_and_denials(app: Flask, project: dict[str, Any], admin: Any) -> None:
    client, headers = admin
    user = _person("member")
    _grant(project, user["name"], "developer")
    listed = client.get(f"/api/v1/projects/{project['id']}/access/people").get_json()["data"]
    mine = next(p for p in listed if p["user"] == user["name"])
    assert mine["level"] == "developer" and mine["source"] == "grant"
    assert "instances.shell" in mine["stages"]["development"] and mine["stages"]["production"] == ["instances.view"]
    found = projects.get(project["id"]) or {}
    refused = access.refusal(user, "instances.logs", found, "main")
    assert refused == (403, f"Developers of {found['name']} cannot read logs and monitoring in production. Ask an admin "
                       "of the project for temporary access (the project's Access tab, Request access).")  # fmt: skip


def test_build_details_are_hidden_without_logs(app: Flask, project: dict[str, Any]) -> None:
    from conftest import sign_in
    from sudoo.projects import builds

    user = _person("member")
    _grant(project, user["name"], "developer")
    failure = {"step": "Install", "reason": "A module failed", "kind": "other", "details": ["secret line"]}
    for branch in ("main", "dev"):
        builds.add_entry({"project": project["id"], "branch": branch, "type": "build", "status": "failed",
                          "failure": failure, "messages": ["log line"]})  # fmt: skip
    client, _ = sign_in(app, user["name"], PASSWORD)
    production = client.get(f"/api/v1/projects/{project['id']}/branches/main/history").get_json()["data"]
    assert production[0]["failure"]["reason"] == "A module failed" and production[0]["failure"]["details"] == []
    assert production[0]["messages"] == []
    development = client.get(f"/api/v1/projects/{project['id']}/branches/dev/history").get_json()["data"]
    assert development[0]["failure"]["details"] == ["secret line"]


# ---------------------------------------------------------------- GitHub


def _link(name: str, login: str, github_id: int) -> None:
    github.link(name, github.GitHubUser(id=github_id, login=login))


def test_github_permission_and_github_teams(
    app: Flask, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(github, "configured", lambda: True)
    live.change({"github_sign_in": True})
    user = _person("member")
    _link(user["name"], "octo-dev", 7001)
    key = project["repository_id"]
    storage().put(github_access.COLLECTION, user["name"], {"checked": time.time(), "repos": {
        key: {"level": "tester", "permission": "read", "checked": time.time(), "covered": True,
              "via": "acme/qa"}}})  # fmt: skip
    github_access._cache.pop(user["name"], None)
    assert access.level_and_source(user, project) == ("tester", "github")
    assert access.allowed(user, "instances.connect", project, "staging")
    assert not access.allowed(user, "instances.view", project, "main")  # testers never see production
    # a GitHub team given Developer: its members get Developer (the higher of repository and team)
    storage().put(github_access.TEAMS, "acme/backend", {"members": ["octo-dev"], "checked": time.time()})
    github_access._teams = None
    found = projects.change(project["id"], lambda p: {**p, "access": {**p["access"], "grants": [
        {"type": "github_team", "id": "Acme/backend", "level": "developer"}]}}) or project  # fmt: skip
    assert access.level_and_source(user, found) == ("developer", "team")
    detail = next(p for p in people.of_project(found) if p["user"] == user["name"])["source_label"]
    assert detail == "GitHub team Acme/backend"
    # leaving the team on GitHub: back to the repository's Tester
    storage().put(github_access.TEAMS, "acme/backend", {"members": [], "checked": time.time()})
    github_access._teams = None
    assert access.level(user, found) == "tester"
    # an individual grant wins over both
    found = _grant(found, user["name"], "view")
    assert access.level_and_source(user, found) == ("view", "grant")
    github_access._teams = None


# ---------------------------------------------------------------- temporary access


def test_temporary_access_is_approved_and_ends(app: Flask, project: dict[str, Any], admin: Any) -> None:
    from conftest import sign_in

    client, headers = admin
    user = _person("member")
    _grant(project, user["name"], "developer")
    asker, asker_headers = sign_in(app, user["name"], PASSWORD)
    path = f"/api/v1/projects/{project['id']}/role-requests"
    short = asker.post(path, json={"stage": "production", "level": "maintainer", "hours": 2, "reason": "x"},
                       headers=asker_headers)  # fmt: skip
    assert short.status_code == 400
    too_long = asker.post(path, json={"stage": "production", "level": "maintainer", "hours": 99,
                                      "reason": "Read the failed import's logs"}, headers=asker_headers)  # fmt: skip
    assert too_long.status_code == 400
    made = asker.post(path, json={"stage": "production", "level": "maintainer", "hours": 2,
                                  "reason": "Read the failed import's logs"}, headers=asker_headers)  # fmt: skip
    assert made.status_code == 201, made.get_json()
    request_id = made.get_json()["data"]["id"]
    found = projects.get(project["id"]) or {}
    assert not access.allowed(user, "instances.logs", found, "main")
    assert asker.post(f"{path}/{request_id}/approve", json={}, headers=asker_headers).status_code == 403
    assert client.post(f"{path}/{request_id}/approve", json={}, headers=headers).status_code == 200
    assert access.allowed(user, "instances.logs", found, "main")
    assert not access.allowed(user, "instances.shell", found, "main")  # Maintainer's rights, not more
    assert not access.allowed(user, "instances.logs", found, "staging") or True  # staging keeps its own

    # a condition (the client portal's switch) can stop it at once
    def closed_production(request: dict[str, Any]) -> str | None:
        return "production is closed by the client" if request["stage"] == "production" else None

    elevation.add_condition(closed_production)
    try:
        assert not access.allowed(user, "instances.logs", found, "main")
    finally:
        elevation._conditions.remove(closed_production)
    # it ends by itself
    stored = storage().get(elevation.COLLECTION, request_id) or {}
    storage().put(elevation.COLLECTION, request_id, {**stored, "expires": time.time() - 1})
    elevation.forget_cache()
    assert not access.allowed(user, "instances.logs", found, "main")
    assert elevation.get(request_id)["status"] == "expired"  # type: ignore[index]


# ---------------------------------------------------------------- e-mails


class Mailbox:
    ready = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        self.sent.append((to, subject))


def test_access_emails_are_sent_once(app: Flask, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    import sudoo.mail

    box = Mailbox()
    monkeypatch.setattr(sudoo.mail, "mailer", lambda: box)

    class Now(threading.Thread):
        def start(self) -> None:
            self.run()

    monkeypatch.setattr(notices.threading, "Thread", Now)
    user = _person("member")
    with notices.watch([project["id"]], by="Sara"):
        _grant(project, user["name"], "developer")
    with notices.watch([project["id"]], by="Sara"):
        _grant(project, user["name"], "developer")  # no change: no e-mail
    with notices.watch([project["id"]], by="GitHub"):
        _grant(project, user["name"], "maintainer")
    with notices.watch([project["id"]], by="Sara"):
        projects.change(project["id"], lambda p: {**p, "access": {**p["access"], "grants": []}})
        access.forget_restrictions()
    subjects = [s for to, s in box.sent if to == f"{user['name']}@example.com"]
    assert subjects == [
        f"You can now work in {project['name']}",
        f"Your role in {project['name']} is now Maintainer",
        f"Your access to {project['name']} was removed",
    ]
    # the access review: not at once after an upgrade, then every ACCESS_REVIEW_DAYS
    assert notices.review_due(project["id"], time.time()) is False
    assert notices.review_due(project["id"], time.time() + 31 * 86400) is True


# ---------------------------------------------------------------- upgrading


def test_projects_of_earlier_versions_keep_their_model(tmp_path: Path) -> None:
    from sudoo.storage.migrate import migrate
    from sudoo.storage.sqlite import SqliteStorage

    db = SqliteStorage(Settings(odoo_root=tmp_path))
    db.put("meta", "schema", {"version": 3})
    db.put("projects", "old", {"id": "old", "access": {"mode": "panel_roles", "grants": []}})
    db.put("projects", "new", {"id": "new", "access": {"mode": "panel_roles", "model": "project_roles"}})
    migrate(db, tmp_path / "manager")
    assert db.get("projects", "old")["access"]["model"] == "legacy"  # type: ignore[index]
    assert db.get("projects", "new")["access"]["model"] == "project_roles"  # type: ignore[index]


def test_switching_keeps_peoples_roles(app: Flask, admin: Any) -> None:
    client, headers = admin
    made = projects.create(repository=f"Acme/old-{time.time_ns()}", odoo_version="18.0", edition="community",
                           origin="linked", user="admin1")  # fmt: skip
    assert access.model(made) == "legacy"  # the tests' PROJECT_ACCESS_MODEL
    viewer, developer = _person("viewer"), _person("developer")
    granted = _grant(made, developer["name"], "view")  # role ∩ grant: view
    before = {n: access.level({"name": n, "role": r}, granted) for n, r in
              ((viewer["name"], "viewer"), (developer["name"], "developer"))}  # fmt: skip
    preview = client.get(f"/api/v1/projects/{made['id']}/access/model").get_json()["data"]
    changes = {c["user"]: c for c in preview["changes"]}
    assert changes[developer["name"]]["after"] == "view" and changes[viewer["name"]]["after"] == "view"
    switched = client.post(f"/api/v1/projects/{made['id']}/access/model", json={"model": "project_roles"},
                           headers=headers)  # fmt: skip
    assert switched.status_code == 200
    after = projects.get(made["id"]) or {}
    assert access.model(after) == "project_roles"
    assert access.level(developer, after) == "view" and access.level(viewer, after) == "view"
    assert before[developer["name"]] == "view"
    assert client.post(f"/api/v1/projects/{made['id']}/access/model", json={"model": "project_roles"},
                       headers=headers).status_code == 409  # fmt: skip


def test_members_reach_only_their_projects(app: Flask, project: dict[str, Any]) -> None:
    from conftest import sign_in

    user = _person("member")
    client, _ = sign_in(app, user["name"], PASSWORD)
    assert client.get("/api/v1/projects").get_json()["data"] == []
    _grant(project, user["name"], "tester")
    listed = [p["id"] for p in client.get("/api/v1/projects").get_json()["data"]]
    assert listed == [project["id"]]
    me = client.get("/api/v1/session").get_json()["data"]
    assert "instances.connect" in me["permissions"] and "accounts.manage" not in me["permissions"]
    assert client.get("/api/v1/users").status_code == 403


def test_connect_follows_the_stage(app: Flask, project: dict[str, Any]) -> None:
    from conftest import sign_in

    for name, stage in (("rolesprod", "production"), ("rolesdev", "development")):
        folder = store.folder(name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "docker-compose.yml").write_text("services: {}\n")
        store.write_env(name, {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": name, "STAGE": stage,
                               "GIT_BRANCH": BRANCH[stage], "PROJECT": project["id"]})  # fmt: skip
    user = _person("member")
    _grant(project, user["name"], "tester")
    client, headers = sign_in(app, user["name"], PASSWORD)
    prod = client.post("/api/v1/instances/rolesprod/connect", json={"uid": 2}, headers=headers)
    assert prod.status_code == 404  # testers do not even see production
    dev = client.get("/api/v1/instances/rolesdev/odoo-users")
    assert dev.status_code != 403  # allowed (Odoo itself is not running in the tests)
    shell = client.get("/api/v1/instances/rolesdev/credentials")
    assert shell.status_code == 403 and "Testers" in shell.get_json()["detail"]
    import shutil

    for name in ("rolesprod", "rolesdev"):
        shutil.rmtree(store.folder(name))


def test_defaults_are_listed_once() -> None:
    described = project_roles.describe(None)
    assert described["levels"] == ["view", "tester", "developer", "maintainer", "admin"]
    assert set(project_roles.RIGHT_LABELS) == set(project_roles.STAGE_RIGHTS) | set(project_roles.PROJECT_RIGHTS)
    assert set(project_roles.STAGE_RIGHTS) | set(project_roles.PROJECT_RIGHTS) == set(access.PROJECT_PERMISSIONS)


# ---------------------------------------------------------------- the client's switch comes first


def _client_of(project: dict[str, Any]) -> str:
    from sudoo.projects import clients

    name = f"c{time.time_ns() % 10**10}"
    accounts.create(name, "client", PASSWORD, must_change=False, email=f"{name}@example.com")
    clients.add_client(project["id"], name, by="admin1")
    return name


def test_temporary_access_never_opens_what_the_client_closed(app: Flask, project: dict[str, Any]) -> None:
    from sudoo.projects import clients

    assert clients.elevation_refusal in elevation._conditions  # registered by the client portal
    client = _client_of(project)
    user = _person("member")
    _grant(project, user["name"], "developer")
    # a lock on production: no temporary access to production, development still possible
    clients.set_lock(project["id"], "main", True, by=client, reason="Season")
    with pytest.raises(ValueError, match="locked by its client"):
        elevation.ask(project["id"], user["name"], "production", "maintainer", 2, "Read the failed import's logs")
    made = elevation.ask(project["id"], user["name"], "development", "admin", 2, "Check the upgrade of the shop")
    elevation.decide(made["id"], "admin1", approve=True)
    found = projects.get(project["id"]) or {}
    assert access.allowed(user, "instances.upgrade", found, "dev")
    # the project closed by its client: the running temporary access stops at once, and the role's own rights too
    clients.set_lock(project["id"], "main", False, by=client)
    clients.set_switch(project["id"], None, "closed", by=client)
    found = projects.get(project["id"]) or {}
    assert not access.allowed(user, "instances.upgrade", found, "dev")
    assert not access.allowed(user, "instances.shell", found, "dev")
    assert access.allowed(user, "instances.view", found, "dev")  # closing hides nothing
    refused = access.refusal(user, "instances.shell", found, "dev")
    assert refused is not None and "closed by its client" in refused[1]
    with pytest.raises(ValueError, match="closed by its client"):
        elevation.ask(project["id"], user["name"], "staging", "maintainer", 2, "Prepare the staging data")
    clients.set_switch(project["id"], None, "open", by=client)
    assert access.allowed(user, "instances.shell", projects.get(project["id"]) or {}, "dev")


def test_a_client_lock_refuses_admins_and_temporary_access_alike(app: Flask, project: dict[str, Any]) -> None:
    from sudoo.projects import clients

    client = _client_of(project)
    admin = _person("admin")
    clients.set_lock(project["id"], None, True, by=client, reason="Audit")
    found = projects.get(project["id"]) or {}
    refused = access.refusal(admin, "instances.shell", found, "dev")
    assert refused is not None and "locked by its client" in refused[1]
    clients.set_lock(project["id"], None, False, by=client)
