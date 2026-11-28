"""API tokens with the developer scope: what such a token may read and do (projects, branches, instances, logs,
jobs, caught e-mails; start, pause, restart, update; development instances only), everything it is refused, and
that its rights stay the scope's ∩ its owner's role and project access. Docker and GitHub never run."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from sudoo import accounts, api_tokens, projects, roles
from sudoo.config import live
from sudoo.core import audit, jobs
from sudoo.core.state import STATE
from sudoo.instances import integrity, odoo, store
from sudoo.limits import quotas
from sudoo.repos import github
from sudoo.storage import storage
from sudoo.web import security
from test_connected_app import Caller, _caller, _instance

Client = tuple[FlaskClient, dict[str, str]]
SHOP = "app-shop"
NEW = {"version": "18.0", "edition": "community", "name": "dev-feature", "repo": "https://github.com/acme/shop"}


@pytest.fixture
def developer(app: Flask, admin: Client) -> Caller:
    return _caller(app, admin, "developer")


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    accounts.THROTTLE.clear()
    monkeypatch.setattr(quotas, "claim", lambda user: None)
    _instance(SHOP)
    yield
    shutil.rmtree(store.folder(SHOP), ignore_errors=True)
    STATE.remove(SHOP)
    integrity.forget(SHOP)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    live.change(dict.fromkeys(live.overrides()))
    accounts.THROTTLE.clear()


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_start(
        instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any
    ) -> jobs.Job:
        calls.append({"instance": instance, "action": action, "user": user, "args": args})
        if args and isinstance(args[0], dict) and "ports" in args[0]:
            store.release_ports(args[0]["ports"])
        return jobs.Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    return calls


def _stage(name: str, stage: str) -> None:
    store.update_env(name, STAGE=stage)


# ---------------------------------------------------------------- the scope


def test_the_scope_is_a_role_only_tokens_have(admin: Client) -> None:
    assert security.SCOPE_ROLES["developer"] == roles.DEVELOPER_TOKEN
    assert "developer" in api_tokens.SCOPES
    assert roles.permissions(roles.DEVELOPER_TOKEN) == {
        "instances.view",
        "instances.logs",
        "instances.update",
        "instances.operate",
        "instances.create",
        "instances.delete",
        "caught_mail.view",
    }
    assert roles.DEVELOPER_TOKEN not in roles.ROLES
    client, headers = admin
    answer = client.post("/api/v1/users", json={"email": "dev@example.com", "role": roles.DEVELOPER_TOKEN},
                         headers=headers)  # fmt: skip
    assert answer.status_code == 400


def test_allowed_reads(developer: Caller, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(odoo, "recent_log", lambda name, lines=300: "INFO odoo started\n")
    assert developer.call("GET", "/api/v1/versions").status_code == 200
    assert SHOP in [i["name"] for i in developer.call("GET", "/api/v1/instances").get_json()["data"]]
    assert developer.call("GET", f"/api/v1/instances/{SHOP}").status_code == 200
    assert "odoo started" in developer.call("GET", f"/api/v1/instances/{SHOP}/logs").get_json()["data"]["text"]
    assert developer.call("GET", "/api/v1/jobs").status_code == 200
    assert developer.call("GET", "/api/v1/instance-quota").status_code == 200
    project = projects.create(repository="Acme/Shop", odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    listed = developer.call("GET", "/api/v1/projects")
    assert listed.status_code == 200 and project["id"] in [p["id"] for p in listed.get_json()["data"]]
    monkeypatch.setattr(github, "heads", lambda address: ({"main": "a" * 40}, "main", None))
    assert developer.call("GET", f"/api/v1/projects/{project['id']}").status_code == 200


@pytest.mark.parametrize("action", ["start", "pause", "restart", "update", "update-submodules"])
def test_allowed_actions(developer: Caller, started: list[dict[str, Any]], action: str) -> None:
    answer = developer.call("POST", f"/api/v1/instances/{SHOP}/actions/{action}", {})
    assert answer.status_code == 202, answer.get_json()
    assert started[-1]["instance"] == SHOP


@pytest.mark.parametrize(
    "action",
    ["backup", "upgrade", "switch-branch", "restore", "rollback", "repair", "update-images", "switch-off",
     "add-submodule", "remove-submodule", "connect-repository"],
)  # fmt: skip
def test_other_actions_are_refused(developer: Caller, started: list[dict[str, Any]], action: str) -> None:
    answer = developer.call("POST", f"/api/v1/instances/{SHOP}/actions/{action}", {})
    assert answer.status_code == 403 and "developer scope" in answer.get_json()["detail"]
    assert not started
    assert audit.latest(1, user=f"api:{developer.name}")[0]["event"] == f"refused: {action} on {SHOP}"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", f"/api/v1/instances/{SHOP}/credentials", None),
        ("PUT", f"/api/v1/instances/{SHOP}/resources", {"limits": {}}),
        ("GET", "/api/v1/settings", None),
        ("PATCH", "/api/v1/settings", {"idle_hours": 1}),
        ("GET", "/api/v1/users", None),
        ("POST", "/api/v1/users", {"email": "x@example.com", "role": "admin"}),
        ("GET", "/api/v1/tokens", None),
        ("POST", "/api/v1/tokens", {"name": "another", "scope": "write"}),
        ("GET", "/api/v1/teams", None),
        ("POST", "/api/v1/projects", {"repository": "acme/new"}),
        ("PATCH", "/api/v1/projects/p1", {"name": "x"}),
        ("PUT", "/api/v1/projects/p1/access", {"mode": "restricted"}),
        ("PUT", "/api/v1/projects/p1/branches/main/stage", {"stage": "production"}),
        ("PUT", "/api/v1/projects/p1/branches/main/lock", {}),
        ("POST", "/api/v1/projects/p1/branches/main/rebuild", {}),
        ("GET", "/api/v1/backups", None),
        ("GET", "/api/v1/offsite", None),
        ("GET", "/api/v1/domains", None),
        ("POST", f"/api/v1/instances/{SHOP}/domains", {"domain": "x.example.com"}),
        ("POST", "/api/v1/shell/tickets", {}),
        ("GET", "/api/v1/audit", None),
        ("POST", "/api/v1/server/restart", {}),
        ("DELETE", f"/api/v1/instances/{SHOP}/mail", None),
    ],
)
def test_everything_else_is_refused(developer: Caller, method: str, path: str, body: dict[str, Any] | None) -> None:
    answer = developer.call(method, path, body)
    assert answer.status_code == 403, path
    assert "developer scope" in answer.get_json()["detail"]


def test_settings_and_stage_of_an_instance_cannot_change(developer: Caller) -> None:
    for body in ({"stage": "production", "confirm_production": True}, {"auto_update": True}):
        answer = developer.call("PATCH", f"/api/v1/instances/{SHOP}", body)
        assert answer.status_code == 403 and "developer scope" in answer.get_json()["detail"]
    assert store.read_env(SHOP).get("STAGE", "development") == "development"


# ---------------------------------------------------------------- development instances only


def test_creates_development_instances_only(developer: Caller, started: list[dict[str, Any]]) -> None:
    answer = developer.call("POST", "/api/v1/instances", NEW)
    assert answer.status_code == 202, answer.get_json()
    assert started[-1]["args"][0]["stage"] == "development"
    for body in ({**NEW, "stage": "staging"}, {**NEW, "stage": "production"},
                 {**NEW, "from_backup": "x__x__20261110-153001__manual.zip"}):  # fmt: skip
        refused = developer.call("POST", "/api/v1/instances", body)
        assert refused.status_code == 403 and "development" in refused.get_json()["detail"]
    assert len(started) == 1


def test_creates_instances_of_development_branches_only(
    developer: Caller, started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = projects.create(repository="Acme/Shop", odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    pid = project["id"]
    monkeypatch.setattr(github, "heads", lambda address: ({"main": "a" * 40, "feature/x": "b" * 40}, "main", None))
    answer = developer.call("POST", f"/api/v1/projects/{pid}/branches/feature/x/instances", {})
    assert answer.status_code == 202, answer.get_json()
    assert started[-1]["args"][0]["stage"] == "development"
    projects.record_branch(pid, "main", stage="production")
    refused = developer.call("POST", f"/api/v1/projects/{pid}/branches/main/instances", {})
    assert refused.status_code == 403 and "development branches" in refused.get_json()["detail"]
    assert len(started) == 1


def test_deletes_development_instances_only(developer: Caller, started: list[dict[str, Any]]) -> None:
    _stage(SHOP, "production")
    refused = developer.call("DELETE", f"/api/v1/instances/{SHOP}", {"confirm": SHOP, "confirm_stage": "production"})
    assert refused.status_code == 403 and not started
    _stage(SHOP, "staging")
    assert developer.call("DELETE", f"/api/v1/instances/{SHOP}", {"confirm": SHOP}).status_code == 403
    _stage(SHOP, "development")
    assert developer.call("DELETE", f"/api/v1/instances/{SHOP}", {"confirm": SHOP}).status_code == 202


# ---------------------------------------------------------------- scope ∩ the owner's role and access


def test_rights_are_the_scope_and_the_owners_role(
    app: Flask, admin: Client, developer: Caller, started: list[dict[str, Any]]
) -> None:
    accounts.create("devowner", "developer", "quartz-meadow-lantern-41")
    token_id = next(t["id"] for t in api_tokens.all_tokens() if t["name"] == developer.name)
    storage().change(api_tokens.TOKENS, token_id, lambda record: record and {**record, "created_by": "devowner"})
    try:
        assert developer.call("POST", f"/api/v1/instances/{SHOP}/actions/restart", {}).status_code == 202
        # a developer account cannot pause, create or delete: neither can its developer token
        assert developer.call("POST", f"/api/v1/instances/{SHOP}/actions/pause", {}).status_code == 403
        assert developer.call("POST", "/api/v1/instances", NEW).status_code == 403
        assert developer.call("DELETE", f"/api/v1/instances/{SHOP}", {"confirm": SHOP}).status_code == 403
    finally:
        accounts.delete("devowner")


def test_project_access_of_the_owner_applies(developer: Caller, monkeypatch: pytest.MonkeyPatch) -> None:
    accounts.create("devowner2", "maintainer", "copper-violet-harbor-27")
    token_id = next(t["id"] for t in api_tokens.all_tokens() if t["name"] == developer.name)
    storage().change(api_tokens.TOKENS, token_id, lambda record: record and {**record, "created_by": "devowner2"})
    try:
        project = projects.create(repository="Acme/Shop", odoo_version="18.0", edition="community",
                                  origin="linked", user="t")  # fmt: skip
        projects.change(project["id"], lambda p: {**p, "access": {"mode": "restricted", "grants": []}})
        listed = developer.call("GET", "/api/v1/projects").get_json()["data"]
        assert project["id"] not in [p["id"] for p in listed]
        assert developer.call("GET", f"/api/v1/projects/{project['id']}").status_code == 404
    finally:
        accounts.delete("devowner2")
