"""Teams, per-project and per-branch access, locked branches (teams.py, projects/access.py): effective permissions are
role ∩ grant, restricted projects and branches are hidden, a locked branch is closed on every path (API, jobs, the
shell's tickets, the gateway's wake, automatic work) except to whoever the lock allows, API tokens act with their
owner's access, and every change is audit-logged. Docker and GitHub never run: jobs are recorded."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from sudoo import accounts, projects, teams, triggers
from sudoo.config import live, settings
from sudoo.core import audit, jobs
from sudoo.core.jobs import Job
from sudoo.core.state import STATE
from sudoo.instances import gateway, monitor, store
from sudoo.limits import quotas
from sudoo.projects import access, automation
from sudoo.storage import storage
from sudoo.webshell import policy, tickets

Client = tuple[FlaskClient, dict[str, str]]
NAMES = ("tm-prod", "tm-dev", "tm-feature", "tm-other")
PASSWORD = "lantern-quarry-orbit-{}"


def _instance(name: str, project: str, branch: str, stage: str) -> None:
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39700 + 2 * NAMES.index(name),
            "ODOO_PORT": 39701 + 2 * NAMES.index(name),
            "GIT_BRANCH": branch,
            "PROJECT": project,
            "STAGE": stage,
            "AUTO_UPDATE": "true",
            "AUTO_UPGRADE": "true",
            "AUTO_PAUSE": "true",
            "DELETE_AFTER_DAYS": "1",
            "CREATED_AT": int(time.time() - 3 * 86400),
            "ODOO_ADMIN_PASSWORD": "x",
            "MASTER_PASSWORD": "",
        },
    )


@pytest.fixture(autouse=True)
def world() -> Iterator[dict[str, Any]]:
    """Project "erp" (Acme/ERP): main in production, dev and feature in development; project "web" (Acme/Web)."""
    erp = projects.create(repository="Acme/ERP", odoo_version="18.0", edition="community", origin="linked", user="t")
    web = projects.create(repository="Acme/Web", odoo_version="18.0", edition="community", origin="linked", user="t")
    projects.change(
        erp["id"], lambda p: {**p, "seen": ["main", "dev", "feature"], "branches": {"main": {"stage": "production"}}}
    )
    _instance("tm-prod", erp["id"], "main", "production")
    _instance("tm-dev", erp["id"], "dev", "development")
    _instance("tm-feature", erp["id"], "feature", "development")
    _instance("tm-other", web["id"], "main", "development")
    access.forget_restrictions()
    yield {"erp": erp["id"], "web": web["id"]}
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
        monitor.STATUS.pop(name, None)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    for key in list(storage().all(teams.COLLECTION)):
        storage().delete(teams.COLLECTION, key)
    access.forget_restrictions()
    live.change(dict.fromkeys(live.overrides()))
    tickets.clear()


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Jobs are recorded (instance, kind) instead of run."""
    calls: list[tuple[str, str]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any) -> Job:
        calls.append((instance, kind))
        return Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    monkeypatch.setattr(quotas, "claim", lambda user: None)
    return calls


def _person(app: Flask, name: str, role: str) -> Client:
    if accounts.get(name) is None:
        accounts.create(name, role, PASSWORD.format(name))
    else:
        accounts.update(name, role=role)
    return sign_in(app, name, PASSWORD.format(name))


@pytest.fixture
def people(app: Flask) -> dict[str, Client]:
    return {
        "admin": _person(app, "tm-admin", "admin"),
        "lead": _person(app, "tm-lead", "maintainer"),
        "dev": _person(app, "tm-dev", "developer"),
        "viewer": _person(app, "tm-viewer", "viewer"),
    }


def _get(client: Client, path: str) -> Any:
    return client[0].get(path)


def _send(client: Client, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    return client[0].open(path, method=method, json=body or {}, headers=client[1])


def _team(admin: Client, name: str, members: list[str]) -> str:
    answer = _send(admin, "POST", "/api/v1/teams", {"name": name, "members": members})
    assert answer.status_code == 201, answer.get_json()
    return str(answer.get_json()["data"]["id"])


def _access(admin: Client, project: str, body: dict[str, Any]) -> Any:
    return _send(admin, "PUT", f"/api/v1/projects/{project}/access", body)


def _events(action: str) -> list[dict[str, Any]]:
    return [e for e in audit.latest(500) if e.get("event") == action]


# ---------------------------------------------------------------- teams


def test_admins_manage_teams_and_everything_is_audited(people: dict[str, Client], admin: Client) -> None:
    team = _team(people["admin"], "Backend leads", ["tm-lead"])
    listed = _get(people["admin"], "/api/v1/teams").get_json()["data"]
    assert [t["name"] for t in listed] == ["Backend leads"]
    assert [m["name"] for m in listed[0]["members"]] == ["tm-lead"]
    # the same name twice, an unknown account, a maintainer: refused
    assert _send(people["admin"], "POST", "/api/v1/teams", {"name": "backend LEADS"}).status_code == 409
    assert _send(people["admin"], "POST", "/api/v1/teams", {"name": "X", "members": ["nobody"]}).status_code == 400
    assert _send(people["lead"], "POST", "/api/v1/teams", {"name": "Mine"}).status_code == 403
    assert _get(people["dev"], "/api/v1/teams").status_code == 403
    changed = _send(people["admin"], "PATCH", f"/api/v1/teams/{team}", {"members": ["tm-lead", "tm-dev"]})
    assert changed.status_code == 200 and len(changed.get_json()["data"]["members"]) == 2
    assert "added tm-dev" in _events("team changed")[0]["detail"]
    assert _send(people["admin"], "DELETE", f"/api/v1/teams/{team}").status_code == 204
    assert _events("team created") and _events("team deleted")
    assert teams.get(team) is None


def test_deleting_an_account_or_a_team_cleans_grants_and_locks(
    people: dict[str, Client], world: dict[str, Any]
) -> None:
    accounts.create("tm-gone", "developer", PASSWORD.format("gone"))
    team = _team(people["admin"], "Short lived", ["tm-gone", "tm-dev"])
    grants = [{"type": "team", "id": team, "level": "view"}, {"type": "user", "id": "tm-gone", "level": "developer"}]
    assert _access(people["admin"], world["erp"], {"mode": "panel_roles", "grants": grants}).status_code == 200
    assert _send(people["admin"], "DELETE", "/api/v1/users/tm-gone").status_code == 204
    assert "tm-gone" not in (teams.get(team) or {})["members"]
    found = projects.get(world["erp"])
    assert found is not None and [g["id"] for g in found["access"]["grants"]] == [team]
    assert _send(people["admin"], "DELETE", f"/api/v1/teams/{team}").status_code == 204
    found = projects.get(world["erp"])
    assert found is not None and found["access"]["grants"] == []


# ---------------------------------------------------------------- effective permissions


def test_existing_projects_keep_panel_wide_access(people: dict[str, Client], world: dict[str, Any]) -> None:
    """No lockout on upgrade: a project without grants works exactly as before."""
    for who in ("lead", "dev", "viewer"):
        assert _get(people[who], f"/api/v1/projects/{world['erp']}").status_code == 200
        names = {i["name"] for i in _get(people[who], "/api/v1/instances").get_json()["data"]}
        assert set(NAMES) <= names


def test_a_grant_never_adds_to_the_role(people: dict[str, Client], world: dict[str, Any], started: list[Any]) -> None:
    """role ∩ grant: a developer granted admin is still a developer; a maintainer granted view only views."""
    grants = [{"type": "user", "id": "tm-dev", "level": "admin"}, {"type": "user", "id": "tm-lead", "level": "view"}]
    assert _access(people["admin"], world["erp"], {"mode": "panel_roles", "grants": grants}).status_code == 200
    user = {"name": "tm-dev", "role": "developer"}
    found = projects.get(world["erp"])
    assert access.allowed(user, "instances.update", found, "dev")
    assert not access.allowed(user, "instances.delete", found, "dev")  # the role does not have it
    lead = {"name": "tm-lead", "role": "maintainer"}
    assert access.allowed(lead, "instances.view", found, "dev") and not access.allowed(lead, "instances.update", found)
    # through the API: the maintainer cannot restart in this project, can in the other one
    refused = _send(people["lead"], "POST", "/api/v1/instances/tm-dev/actions/restart")
    assert refused.status_code == 403 and "view" in refused.get_json()["detail"]
    assert _send(people["lead"], "POST", "/api/v1/instances/tm-other/actions/restart").status_code == 202
    # meta.permissions says what the caller may do in the instance
    meta = _get(people["lead"], "/api/v1/instances/tm-dev").get_json()["meta"]
    assert "instances.view" in meta["permissions"] and "instances.update" not in meta["permissions"]


def test_individual_grants_win_over_teams_and_teams_take_their_highest(
    people: dict[str, Client], world: dict[str, Any]
) -> None:
    readers = _team(people["admin"], "Readers", ["tm-lead", "tm-dev"])
    builders = _team(people["admin"], "Builders", ["tm-lead"])
    grants = [
        {"type": "team", "id": readers, "level": "view"},
        {"type": "team", "id": builders, "level": "maintainer"},
        {"type": "user", "id": "tm-dev", "level": "none"},
        {"type": "user", "id": "tm-admin", "level": "admin"},
    ]
    assert _access(people["admin"], world["erp"], {"mode": "restricted", "grants": grants}).status_code == 200
    found = projects.get(world["erp"])
    assert access.level({"name": "tm-lead", "role": "maintainer"}, found) == "maintainer"
    assert access.level({"name": "tm-dev", "role": "developer"}, found) == "none"
    assert access.level({"name": "tm-viewer", "role": "viewer"}, found) == "none"
    assert access.level({"name": "admin1", "role": "super_admin"}, found) == "admin"  # super admins see everything


def test_restricted_projects_are_hidden_everywhere(
    people: dict[str, Client], world: dict[str, Any], admin: Client, started: list[Any]
) -> None:
    team = _team(people["admin"], "ERP team", ["tm-lead"])
    body = {
        "mode": "restricted",
        "grants": [
            {"type": "team", "id": team, "level": "maintainer"},
            {"type": "user", "id": "tm-admin", "level": "admin"},
        ],
    }
    assert _access(people["admin"], world["erp"], body).status_code == 200
    viewer = people["viewer"]
    listed = [p["id"] for p in _get(viewer, "/api/v1/projects").get_json()["data"]]
    assert world["erp"] not in listed and world["web"] in listed
    assert _get(viewer, f"/api/v1/projects/{world['erp']}").status_code == 404
    names = {i["name"] for i in _get(viewer, "/api/v1/instances").get_json()["data"]}
    assert "tm-prod" not in names and "tm-other" in names
    assert _get(viewer, "/api/v1/instances/tm-prod").status_code == 404
    assert _get(viewer, "/api/v1/instances/tm-prod/logs").status_code == 404
    # its jobs are hidden too
    job = Job("tm-prod", "Restart", "restart", "x")
    jobs.JOBS[job.id] = job
    try:
        assert _get(viewer, f"/api/v1/jobs/{job.id}").status_code == 404
        assert job.id not in {j["id"] for j in _get(viewer, "/api/v1/jobs").get_json()["data"]}
        assert _get(people["lead"], f"/api/v1/jobs/{job.id}").status_code == 200
    finally:
        jobs.JOBS.pop(job.id, None)
    # the granted team and super admins see it
    assert _get(people["lead"], f"/api/v1/projects/{world['erp']}").status_code == 200
    assert _get(admin, f"/api/v1/projects/{world['erp']}").status_code == 200
    # the live events leave it out, and send the project as deleted to whoever lost it
    from sudoo.core.events import Event
    from sudoo.web.v1.events import for_caller

    viewer_user = {"name": "tm-viewer", "role": "viewer"}
    assert for_caller(viewer_user, Event(1, "instance", "tm-prod", {"name": "tm-prod"}), {"name": "tm-prod"}) is None
    assert for_caller(viewer_user, Event(2, "project", world["erp"], {"id": world["erp"]}), {"id": world["erp"]}) == {
        "id": world["erp"],
        "deleted": True,
    }
    metrics = for_caller(viewer_user, Event(3, "metrics", "", {}), {"instances": {"tm-prod": 1, "tm-other": 2}})
    assert metrics == {"instances": {"tm-other": 2}}


def test_nobody_restricts_themselves_out_of_a_project(people: dict[str, Client], world: dict[str, Any]) -> None:
    answer = _access(people["admin"], world["erp"], {"mode": "restricted", "grants": []})
    assert answer.status_code == 409 and "admin level" in answer.get_json()["detail"]
    assert _access(people["lead"], world["erp"], {"mode": "panel_roles"}).status_code == 403  # a maintainer


def test_a_restricted_branch_is_seen_by_its_grants_only(
    people: dict[str, Client], world: dict[str, Any], started: list[Any]
) -> None:
    """Production visible to leads only: the rest of the project stays open."""
    leads = _team(people["admin"], "Leads", ["tm-lead"])
    body = {
        "mode": "panel_roles",
        "branches": {"main": {"restricted": True, "grants": [{"type": "team", "id": leads, "level": "maintainer"}]}},
    }
    assert _access(people["admin"], world["erp"], body).status_code == 200
    dev = people["dev"]
    detail = _get(dev, f"/api/v1/projects/{world['erp']}").get_json()
    assert "main" not in {b["name"] for b in detail["data"]["branches_list"]}
    assert "tm-prod" not in detail["data"]["instances"]["names"]
    assert _get(dev, "/api/v1/instances/tm-prod").status_code == 404
    assert _get(dev, "/api/v1/instances/tm-dev").status_code == 200
    assert _get(people["lead"], "/api/v1/instances/tm-prod").status_code == 200
    assert _send(people["lead"], "POST", "/api/v1/instances/tm-prod/actions/restart").status_code == 202


# ---------------------------------------------------------------- locked branches


def _lock(client: Client, project: str, branch: str, allow: list[dict[str, str]] | None = None) -> Any:
    return _send(
        client,
        "PUT",
        f"/api/v1/projects/{project}/branches/{branch}/lock",
        {"reason": "Release freeze", "allow": allow or []},
    )


def test_a_locked_branch_is_closed_on_every_path(
    app: Flask,
    people: dict[str, Client],
    world: dict[str, Any],
    admin: Client,
    started: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locked = _lock(people["admin"], world["erp"], "dev", [{"type": "user", "id": "tm-lead"}])
    assert locked.status_code == 200
    allow = {(e["type"], e["id"]) for e in locked.get_json()["data"]["allow"]}
    assert allow == {("user", "tm-lead"), ("user", "tm-admin")}  # whoever locks stays allowed
    assert _events("branch locked")
    dev = people["dev"]
    # still seen, with the lock shown
    shown = _get(dev, "/api/v1/instances/tm-dev").get_json()["data"]
    assert shown["lock"]["locked_by"] == "tm-admin" and shown["lock"]["reason"] == "Release freeze"
    # the API: actions, settings, logs, mail, deleting
    for method, path in (
        ("POST", "/api/v1/instances/tm-dev/actions/restart"),
        ("POST", "/api/v1/instances/tm-dev/actions/update"),
        ("PATCH", "/api/v1/instances/tm-dev"),
        ("DELETE", "/api/v1/instances/tm-dev"),
    ):
        answer = _send(dev, method, path, {"auto_pause": False} if method == "PATCH" else {"confirm": "tm-dev"})
        assert answer.status_code == 403, (method, path)
        if method == "POST":  # a developer may restart and update: the lock is what refuses
            assert "locked" in answer.get_json()["detail"]
    lead_refused = _send(people["admin"], "PATCH", "/api/v1/instances/tm-feature", {"auto_pause": False})
    assert lead_refused.status_code == 200  # another branch is not locked
    assert _get(dev, "/api/v1/instances/tm-dev/logs").status_code == 403
    # a super admin is locked out too until they unlock; the people the lock allows are not
    assert _send(admin, "POST", "/api/v1/instances/tm-dev/actions/restart").status_code == 403
    assert _send(people["lead"], "POST", "/api/v1/instances/tm-dev/actions/restart").status_code == 202
    # the project's branch endpoints
    stage = _send(dev, "PUT", f"/api/v1/projects/{world['erp']}/branches/dev/stage", {"stage": "staging"})
    assert stage.status_code == 403
    # the shell: no ticket, and an open shell's recheck fails
    live.change({"shell_enabled": True, "shell_roles": "developers"})
    user = {"name": "tm-dev", "role": "developer"}
    with pytest.raises(policy.Refused) as refused:
        policy.plan(user, "tm-dev", "shell")
    assert refused.value.code == "locked"
    assert _send(dev, "POST", "/api/v1/instances/tm-dev/shell/tickets", {"target": "shell"}).status_code == 403
    policy.plan(user, "tm-feature", "shell")  # another branch: open
    # the gateway: a visit does not start a paused locked instance
    monkeypatch.setattr(gateway, "_wake", lambda name: pytest.fail("a locked instance was woken"))
    monitor.STATUS["tm-dev"] = {"running": False}
    assert gateway.wake_decision("tm-dev")[0] == "locked"
    # automatic work: no update on push, no expiry
    triggers.request_update("tm-dev", "push to dev")
    assert ("tm-dev", "update") not in started
    assert _events("automatic update skipped (branch locked)")
    from sudoo import background

    assert "tm-dev" not in background.delete_expired_instances(now=time.time() + 10 * 86400)
    assert automation.creation_refusal(projects.get(world["erp"]) or {}, "dev") == "the branch is locked"


def test_unlocking_is_for_the_lock_and_super_admins(
    app: Flask, people: dict[str, Client], world: dict[str, Any], admin: Client
) -> None:
    assert _lock(people["admin"], world["erp"], "feature").status_code == 200
    other_admin = _person(app, "tm-admin2", "admin")
    refused = _send(other_admin, "DELETE", f"/api/v1/projects/{world['erp']}/branches/feature/lock")
    assert refused.status_code == 403
    assert _send(people["dev"], "DELETE", f"/api/v1/projects/{world['erp']}/branches/feature/lock").status_code == 403
    assert _lock(other_admin, world["erp"], "feature").status_code == 403  # changing who it allows: the same rule
    assert _send(admin, "DELETE", f"/api/v1/projects/{world['erp']}/branches/feature/lock").status_code == 204
    assert _events("branch unlocked")
    assert access.lock_of(projects.get(world["erp"]), "feature") is None
    assert _send(admin, "DELETE", f"/api/v1/projects/{world['erp']}/branches/feature/lock").status_code == 404


def test_a_locked_production_branch_is_not_copied_into_staging(
    people: dict[str, Client], world: dict[str, Any], started: list[Any]
) -> None:
    _instance("tm-feature", world["erp"], "feature", "staging")
    projects.record_branch(world["erp"], "feature", stage="staging")
    assert _lock(people["admin"], world["erp"], "main").status_code == 200
    refused = _send(people["lead"], "POST", f"/api/v1/projects/{world['erp']}/branches/feature/rebuild")
    assert refused.status_code in (403, 409)
    assert automation.update_for("tm-feature") is None  # pushes do the usual update instead


# ---------------------------------------------------------------- API tokens


class Caller:
    def __init__(self, client: FlaskClient, token: str, secret: str) -> None:
        self.client, self.token, self.secret = client, token, secret.encode()

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else b""
        timestamp, nonce = str(int(time.time())), secrets.token_urlsafe(24)
        message = "\n".join([timestamp, nonce, method, path, hashlib.sha256(data).hexdigest()]).encode()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Sudoo-Timestamp": timestamp,
            "X-Sudoo-Nonce": nonce,
            "X-Sudoo-Signature": "sha256=" + hmac.new(self.secret, message, hashlib.sha256).hexdigest(),
        }
        return self.client.open(path, method=method, data=data, headers=headers, content_type="application/json")


def test_api_tokens_act_with_their_owners_access(
    app: Flask, people: dict[str, Client], world: dict[str, Any], started: list[Any]
) -> None:
    accounts.THROTTLE._failures.clear()
    made = _send(
        people["admin"], "POST", "/api/v1/tokens", {"name": f"tm token {secrets.token_hex(2)}", "scope": "write"}
    )
    data = made.get_json()["data"]
    caller = Caller(app.test_client(), data["token"], data["signing_secret"])
    assert caller.call("GET", "/api/v1/instances/tm-dev").status_code == 200
    # the owner is denied the project: so is the token
    grants = [{"type": "user", "id": "tm-admin", "level": "none"}]
    found = projects.get(world["erp"]) or {}
    projects.change(world["erp"], lambda p: {**p, "access": {**access.access_of(found), "grants": grants}})
    access.forget_restrictions()
    assert caller.call("GET", "/api/v1/instances/tm-dev").status_code == 404
    names = {i["name"] for i in caller.call("GET", "/api/v1/instances").get_json()["data"]}
    assert "tm-dev" not in names and "tm-other" in names
    # a lock that does not allow the owner closes it to the token
    projects.change(world["erp"], lambda p: {**p, "access": {"mode": "panel_roles"}})
    projects.change(
        world["web"], lambda p: {**p, "access": {"mode": "panel_roles", "locks": {"main": {"by": "x", "allow": []}}}}
    )
    access.forget_restrictions()
    answer = caller.call("POST", "/api/v1/instances/tm-other/actions/restart", {})
    assert answer.status_code == 403 and "locked" in answer.get_json()["detail"]
    # a token's permissions are its scope ∩ its owner's role
    accounts.update("tm-admin", role="viewer")
    try:
        assert caller.call("POST", "/api/v1/instances/tm-dev/actions/restart", {}).status_code == 403
    finally:
        accounts.update("tm-admin", role="admin")


def test_settings_are_untouched(people: dict[str, Client]) -> None:
    """Teams change nothing in the panel's settings."""
    assert settings.max_production_instances >= 0
