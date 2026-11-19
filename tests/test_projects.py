"""Projects (projects/): one GitHub repository each; branches with stages; existing instances adopted; staging from
production. GitHub and Docker never run: branches come from a stand-in for `git ls-remote`, jobs are recorded."""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from sudoo import accounts, projects, triggers
from sudoo.config import Settings, live, settings
from sudoo.core import jobs
from sudoo.core.jobs import Job, JobError
from sudoo.core.state import STATE
from sudoo.instances import backups, monitor, operations, store
from sudoo.limits import quotas
from sudoo.projects import access, adopt, automation, branches, staging
from sudoo.repos import github
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
NAMES = ("erp-main", "erp-dev", "erp-old", "shop-main", "plain-one", "erp-stage")
HEADS: dict[str, dict[str, str]] = {}  # repository address -> {branch: sha}


def _instance(name: str, stage: str | None, repo: str | None, branch: str = "main", **env: Any) -> None:
    """An instance; `repo` "Owner/Repo" gives its add-ons folder that GitHub remote, None makes it plain Odoo."""
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    subprocess.run(["git", "init", "-q", str(folder / "addons")], check=True)
    if repo:
        subprocess.run(
            ["git", "-C", str(folder / "addons"), "config", "remote.origin.url", f"https://github.com/{repo}"],
            check=True,
        )
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39500 + 2 * NAMES.index(name),
            "ODOO_PORT": 39501 + 2 * NAMES.index(name),
            "GIT_BRANCH": branch if repo else "",
            "AUTO_UPDATE": "true",
            "AUTO_UPGRADE": "true",
            "AUTO_PAUSE": "true",
            "DELETE_AFTER_DAYS": "30",
            "CREATED_AT": int(time.time() - 3600),
            **({"STAGE": stage} if stage else {}),
            **({} if repo else {"SOURCE": "plain"}),
            **env,
        },
    )


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    HEADS.clear()
    monkeypatch.setattr(
        github,
        "heads",
        lambda address: (dict(HEADS[address]), "main", None) if address in HEADS else ({}, None, "not found"),
    )
    yield
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
        monitor.STATUS.pop(name, None)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    for key in list(branches._heads):
        branches.forget(key)
    shutil.rmtree(settings.backups_dir, ignore_errors=True)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any, tuple[Any, ...]]]:
    """Jobs are recorded (instance, function, arguments) instead of run."""
    calls: list[tuple[str, Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system",
                   **_: Any) -> Job:  # fmt: skip
        calls.append((instance, fn, args))
        return Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    monkeypatch.setattr(quotas, "claim", lambda user: None)
    return calls


def _plan(started: list[Any]) -> dict[str, Any]:
    plan: dict[str, Any] = started[-1][2][0]
    store.release_ports(plan["ports"])
    return plan


@pytest.fixture(scope="module")
def viewer(app: Flask) -> Client:
    accounts.create("projview", "viewer", "maple-orbit-quarry-58")
    return sign_in(app, "projview", "maple-orbit-quarry-58")


@pytest.fixture(scope="module")
def developer(app: Flask) -> Client:
    accounts.create("projdev", "developer", "basalt-hollow-lyric-63")
    return sign_in(app, "projdev", "basalt-hollow-lyric-63")


def _existing_instances() -> None:
    _instance("erp-main", "production", "Acme/ERP", "main")
    _instance("erp-dev", None, "acme/erp", "feature-x")  # made before stages existed; another spelling of the repo
    _instance("erp-old", "development", "Acme/ERP", "main")  # a second instance of main, development
    _instance("shop-main", "staging", "Acme/Shop", "main")
    _instance("plain-one", None, None)


# ---------------------------------------------------------------- existing instances


def test_existing_instances_join_one_project_per_repository(started: list[Any]) -> None:
    _existing_instances()
    envs = {name: store.read_env(name) for name in NAMES if store.exists(name)}
    report = adopt.adopt()
    assert sorted(report["joined"]) == ["erp-dev", "erp-main", "erp-old", "shop-main"]
    assert report["standalone"] == ["plain-one"]
    assert len(report["created"]) == 2
    erp = projects.by_repository("ACME/erp")
    shop = projects.by_repository("acme/shop")
    assert erp and shop and erp["origin"] == "adopted" and erp["repository"] == "Acme/ERP"
    assert projects.instances(erp["id"]) == ["erp-dev", "erp-main", "erp-old"]
    # only PROJECT was added: name, stage, ports, lifetime stay; nothing was started or restarted
    for name, before in envs.items():
        after = store.read_env(name)
        assert {k: v for k, v in after.items() if k != "PROJECT"} == before
    assert "PROJECT" not in store.read_env("plain-one")
    assert started == []
    # the branches got the stage their instances have (the highest when several share a branch)
    assert projects.branch_stage(erp, "main") == "production"
    assert projects.branch_stage(erp, "feature-x") == "development"
    assert projects.branch_stage(shop, "main") == "staging"
    assert store.read_env("erp-old").get("STAGE") == "development"  # its own stage is kept

    # again: nothing changes
    snapshot = storage().all(projects.COLLECTION)
    assert adopt.adopt() == {"joined": [], "created": [], "standalone": ["plain-one"]}
    assert storage().all(projects.COLLECTION) == snapshot


def test_an_instance_whose_project_was_deleted_joins_again(started: list[Any]) -> None:
    _instance("erp-main", "production", "Acme/ERP")
    adopt.adopt()
    first = projects.by_repository("acme/erp")
    assert first
    projects.remove(first["id"])
    report = adopt.adopt()
    assert report["joined"] == ["erp-main"] and projects.project_of("erp-main") == report["created"][0]


# ---------------------------------------------------------------- the API


def test_list_and_read_projects(admin: Client, viewer: Client, started: list[Any]) -> None:
    _existing_instances()
    adopt.adopt()
    erp = projects.by_repository("acme/erp")
    assert erp
    HEADS["https://github.com/Acme/ERP"] = {"main": "a" * 40, "feature-x": "b" * 40, "fresh": "c" * 40}
    client, _ = viewer
    answer = client.get("/api/v1/projects").get_json()
    listed = {p["repository"]["full_name"]: p for p in answer["data"]}
    assert set(listed) == {"Acme/ERP", "Acme/Shop"} and answer["meta"]["standalone"] == ["plain-one"]
    assert listed["Acme/ERP"]["branches"]["production"] == 1 and listed["Acme/ERP"]["instances"]["total"] == 3

    found = client.get(f"/api/v1/projects/{erp['id']}").get_json()
    rows = {b["name"]: b for b in found["data"]["branches_list"]}
    assert [b["name"] for b in found["data"]["branches_list"]][0] == "main"  # production first
    assert rows["main"]["instances"] == ["erp-main", "erp-old"] and rows["main"]["on_github"] is True
    assert rows["fresh"]["stage"] == "development" and rows["fresh"]["instances"] == []
    assert rows["fresh"]["commit"]["sha"] == "ccccccc"
    assert found["meta"]["default_branch"] == "main"
    assert client.get("/api/v1/projects/no-such").status_code == 404
    assert client.get("/api/v1/projects/..%2F").status_code == 404
    instance = client.get("/api/v1/instances/erp-main").get_json()["data"]
    assert instance["project"]["id"] == erp["id"]
    assert client.get("/api/v1/instances/plain-one").get_json()["data"]["project"] is None


def test_link_a_repository(admin: Client, developer: Client) -> None:
    client, headers = admin
    HEADS["https://github.com/Acme/Portal"] = {"main": "d" * 40, "dev": "e" * 40}
    body = {"repository": "https://github.com/Acme/Portal", "odoo_version": "18.0", "edition": "community"}
    dev_client, dev_headers = developer
    assert dev_client.post("/api/v1/projects", json=body, headers=dev_headers).status_code == 403
    answer = client.post("/api/v1/projects", json=body, headers=headers)
    assert answer.status_code == 201, answer.get_json()
    created = answer.get_json()["data"]
    assert (
        created["id"] == "portal" and created["origin"] == "linked" and answer.headers["Location"].endswith("/portal")
    )
    assert sorted(projects.get("portal")["seen"]) == ["dev", "main"]  # type: ignore[index]
    assert client.post("/api/v1/projects", json=body, headers=headers).status_code == 409
    missing = {**body, "repository": "Acme/Nothing"}
    assert client.post("/api/v1/projects", json=missing, headers=headers).status_code == 400
    assert client.post("/api/v1/projects", json={**body, "odoo_version": "7.0"}, headers=headers).status_code == 400


def test_options_follow_the_settings_until_set(admin: Client) -> None:
    client, headers = admin
    project = projects.create(
        repository="Acme/ERP", odoo_version="18.0", edition="community", origin="linked", user="t"
    )
    live.change({"project_max_auto_development": 7, "project_staging_on_push": "keep_data"})
    assert projects.option(project, "max_auto_development") == 7
    change = {"options": {"max_auto_development": 2, "branch_deleted": "delete", "auto_development": False}}
    answer = client.patch(f"/api/v1/projects/{project['id']}", json=change, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    shown = answer.get_json()["data"]
    assert shown["options"]["max_auto_development"] == 2 and shown["options"]["staging_on_push"] == "keep_data"
    assert shown["own_options"] == {"max_auto_development": 2, "branch_deleted": "delete", "auto_development": False}
    reset = {"options": {"max_auto_development": None}}
    shown = client.patch(f"/api/v1/projects/{project['id']}", json=reset, headers=headers).get_json()["data"]
    assert shown["options"]["max_auto_development"] == 7
    for bad in ({"options": {"nope": 1}}, {"options": {"branch_deleted": "burn"}}, {"options": {"staging_lifetime_days": -1}},
                {"name": ""}, {}):  # fmt: skip
        assert client.patch(f"/api/v1/projects/{project['id']}", json=bad, headers=headers).status_code == 400
    renamed = client.patch(f"/api/v1/projects/{project['id']}", json={"name": "Acme ERP"}, headers=headers)
    assert renamed.get_json()["data"]["name"] == "Acme ERP" and renamed.get_json()["data"]["id"] == project["id"]


def test_branch_stage_changes_move_the_instances(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    _instance("erp-main", "development", "Acme/ERP", "main")
    _instance("erp-dev", "development", "Acme/ERP", "feature-x")
    adopt.adopt()
    erp = projects.by_repository("acme/erp")
    assert erp
    HEADS["https://github.com/Acme/ERP"] = {"main": "a" * 40, "feature-x": "b" * 40}
    url = f"/api/v1/projects/{erp['id']}/branches/main/stage"
    assert client.put(url, json={"stage": "production"}, headers=headers).status_code == 400  # not confirmed
    answer = client.put(url, json={"stage": "production", "confirm_production": True}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["data"]["stage"] == "production"
    env = store.read_env("erp-main")
    assert env["STAGE"] == "production" and env["AUTO_PAUSE"] == "false" and env["DELETE_AFTER_DAYS"] == "0"

    # one production branch per project (PROJECT_PRODUCTION_BRANCHES), whatever the server allows
    live.change({"max_production_instances": 0})
    other = f"/api/v1/projects/{erp['id']}/branches/feature-x/stage"
    refused = client.put(other, json={"stage": "production", "confirm_production": True}, headers=headers)
    assert refused.status_code == 409 and "production" in refused.get_json()["detail"]
    live.change({"project_production_branches": 2})
    assert (
        client.put(other, json={"stage": "production", "confirm_production": True}, headers=headers).status_code == 200
    )
    # the server-wide limit still applies
    live.change({"max_production_instances": 1, "project_production_branches": 0})
    assert client.put(other, json={"stage": "staging", "confirm_production": True}, headers=headers).status_code == 200
    back = client.put(other, json={"stage": "production", "confirm_production": True}, headers=headers)
    assert back.status_code == 409

    # leaving production needs the confirmation; a branch with a slash in its name works
    assert client.put(url, json={"stage": "development"}, headers=headers).status_code == 400
    HEADS["https://github.com/Acme/ERP"]["fix/login"] = "c" * 40
    slash = f"/api/v1/projects/{erp['id']}/branches/fix/login/stage"
    assert client.put(slash, json={"stage": "staging"}, headers=headers).status_code == 200
    assert projects.branch_stage(projects.get(erp["id"]) or {}, "fix/login") == "staging"
    assert client.put(slash, json={"stage": "live"}, headers=headers).status_code == 400


def test_an_instance_stage_change_records_the_branch_stage(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    _instance("erp-main", "development", "Acme/ERP", "main")
    _instance("erp-dev", "development", "Acme/ERP", "other")
    adopt.adopt()
    assert client.patch("/api/v1/instances/erp-main", json={"stage": "staging"}, headers=headers).status_code == 200
    erp = projects.by_repository("acme/erp") or {}
    assert projects.branch_stage(erp, "main") == "staging"
    # into production through the instance: the project's limit applies too
    live.change({"max_production_instances": 0})
    body = {"stage": "production"}
    assert client.patch("/api/v1/instances/erp-main", json=body, headers=headers).status_code == 200
    refused = client.patch("/api/v1/instances/erp-dev", json=body, headers=headers)
    assert refused.status_code == 409


def test_create_instances_for_branches(admin: Client, developer: Client, started: list[Any]) -> None:
    client, headers = admin
    project = projects.create(
        repository="Acme/ERP", odoo_version="18.0", edition="community", origin="linked", user="t"
    )
    pid = project["id"]
    HEADS["https://github.com/Acme/ERP"] = {"main": "a" * 40, "feature": "b" * 40, "qa": "c" * 40}
    live.change({"delete_after_days": 12, "project_development_demo_data": True})

    answer = client.post(f"/api/v1/projects/{pid}/branches/feature/instances", json={}, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    plan = _plan(started)
    assert plan["project"] == pid and plan["branch"] == "feature" and plan["stage"] == "development"
    assert plan["demo"] is True and plan["delete_after_days"] == 12 and plan["repo"] == "https://github.com/Acme/ERP"
    projects.change(pid, lambda p: {**p, "options": {"development_lifetime_days": 3, "development_demo_data": False}})
    client.post(f"/api/v1/projects/{pid}/branches/feature/instances", json={"name": "feat two"}, headers=headers)
    plan = _plan(started)
    assert plan["demo"] is False and plan["delete_after_days"] == 3 and plan["name"] == "feat-two"

    assert client.post(f"/api/v1/projects/{pid}/branches/gone/instances", json={}, headers=headers).status_code == 404

    # staging needs production's data: refused while the project has no production instance
    projects.record_branch(pid, "qa", stage="staging")
    refused = client.post(f"/api/v1/projects/{pid}/branches/qa/instances", json={}, headers=headers)
    assert refused.status_code == 409 and "production" in refused.get_json()["detail"]
    _instance("erp-main", "production", "Acme/ERP", "main", PROJECT=pid)
    projects.record_branch(pid, "main", stage="production")
    answer = client.post(f"/api/v1/projects/{pid}/branches/qa/instances", json={}, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    plan = _plan(started)
    assert plan["from_production"] == "erp-main" and plan["stage"] == "staging" and plan["demo"] is False

    # a production branch: no lifetime, no pause, no demo data
    projects.change(pid, lambda p: {**p, "branches": {**p["branches"], "main": {"stage": "production"}}})
    live.change({"max_production_instances": 0, "project_production_branches": 0})
    client.post(f"/api/v1/projects/{pid}/branches/main/instances", json={}, headers=headers)
    plan = _plan(started)
    assert plan["stage"] == "production" and plan["delete_after_days"] == 0 and plan["auto_pause"] is False

    dev_client, dev_headers = developer  # developers cannot create instances
    assert (
        dev_client.post(f"/api/v1/projects/{pid}/branches/feature/instances", json={}, headers=dev_headers).status_code
        == 403
    )


def test_creating_an_instance_of_a_repository_puts_it_in_its_project(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    body = {"version": "18.0", "edition": "community", "repo": "https://github.com/Acme/Shop", "branch": "main"}
    assert client.post("/api/v1/instances", json=body, headers=headers).status_code == 202
    plan = _plan(started)
    shop = projects.by_repository("acme/shop")
    assert shop and plan["project"] == shop["id"] and shop["origin"] == "adopted"
    assert projects.branch_stage(projects.get(shop["id"]) or {}, "main") == "development"
    plain = {"version": "18.0", "edition": "community"}
    assert client.post("/api/v1/instances", json=plain, headers=headers).status_code == 202
    assert _plan(started)["project"] == ""

    # the project allows one production branch: a production instance of another branch is refused
    live.change({"max_production_instances": 0})
    _instance("shop-main", "production", "Acme/Shop", "main", PROJECT=shop["id"])
    other = {**body, "branch": "release", "stage": "production"}
    assert client.post("/api/v1/instances", json=other, headers=headers).status_code == 409


def test_delete_a_project(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    project = projects.create(
        repository="Acme/ERP", odoo_version="18.0", edition="community", origin="linked", user="t"
    )
    _instance("erp-main", "development", "Acme/ERP", "main", PROJECT=project["id"])
    url = f"/api/v1/projects/{project['id']}"
    assert client.delete(url, json={"confirm": "wrong"}, headers=headers).status_code == 400
    assert client.delete(url, json={"confirm": project["id"]}, headers=headers).status_code == 409
    shutil.rmtree(store.folder("erp-main"))
    assert client.delete(url, json={"confirm": project["id"]}, headers=headers).status_code == 204
    assert projects.get(project["id"]) is None


def test_access_is_the_panel_roles_until_projects_have_their_own() -> None:
    admin = {"name": "a", "role": "admin"}
    viewer = {"name": "v", "role": "viewer"}
    project = {"id": "p", "access": {"mode": "panel_roles"}}
    assert access.allowed(admin, "projects.manage", project) and not access.allowed(viewer, "projects.manage", project)
    assert access.allowed(viewer, "instances.view", project)
    assert not access.allowed(admin, "instances.view", {"id": "p", "access": {"mode": "teams"}})  # unknown: nothing
    assert access.visible(viewer, [project, {"id": "q", "access": {"mode": "teams"}}]) == [project]


def test_project_settings_are_changed_in_the_panel(admin: Client) -> None:
    client, headers = admin
    change = {
        "project_branch_deleted": "keep",
        "project_staging_on_push": "keep_data",
        "project_production_branches": 3,
    }
    assert client.patch("/api/v1/settings", json=change, headers=headers).status_code == 200
    assert settings.project_branch_deleted == "keep" and settings.project_production_branches == 3
    assert client.patch("/api/v1/settings", json={"project_branch_deleted": "burn"}, headers=headers).status_code == 400


# ---------------------------------------------------------------- staging from production


def test_rebuild_staging_from_production(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    project = projects.create(
        repository="Acme/ERP", odoo_version="18.0", edition="community", origin="linked", user="t"
    )
    pid = project["id"]
    _instance("erp-main", "production", "Acme/ERP", "main", PROJECT=pid)
    _instance("erp-stage", "staging", "Acme/ERP", "qa", PROJECT=pid)
    _instance("erp-dev", "development", "Acme/ERP", "feature", PROJECT=pid)
    url = f"/api/v1/projects/{pid}/branches/qa/rebuild"
    answer = client.post(url, json={}, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    assert started[-1][:2] == ("erp-stage", staging.rebuild)
    refused = client.post(f"/api/v1/projects/{pid}/branches/feature/rebuild", json={}, headers=headers)
    assert refused.status_code == 409  # development: never replaced by production's data
    _instance("erp-main", "production", "Acme/ERP", "main", PROJECT=pid, ODOO_VERSION="19.0")
    assert "same Odoo version" in (staging.rebuild_refusal("erp-stage") or "")


def test_staging_never_copies_a_production_branch_hidden_from_the_caller(
    app: Flask, admin: Client, started: list[Any]
) -> None:
    """A restricted production branch keeps its data from those it does not name: they may not create or rebuild a
    staging instance from it, and a push to staging no longer copies it automatically while they could open it."""
    if accounts.get("projmaint") is None:
        accounts.create("projmaint", "maintainer", "lilac-harbor-quill-91")
    client, headers = sign_in(app, "projmaint", "lilac-harbor-quill-91")
    pid = _linked(["main", "qa"])
    HEADS["https://github.com/Acme/ERP"] = {"main": "a" * 40, "qa": "c" * 40}
    _instance("erp-main", "production", "Acme/ERP", "main", PROJECT=pid)
    _instance("erp-stage", "staging", "Acme/ERP", "qa", PROJECT=pid)
    projects.record_branch(pid, "main", stage="production")
    projects.record_branch(pid, "qa", stage="staging")
    restricted = {"branches": {"main": {"restricted": True, "grants": [{"type": "user", "id": "admin1",
                                                                         "level": "admin"}]}}}  # fmt: skip
    projects.change(pid, lambda p: {**p, "access": restricted})
    access.forget_restrictions()
    try:
        assert client.get("/api/v1/instances/erp-main").status_code == 404
        assert client.get("/api/v1/instances/erp-stage").status_code == 200
        rebuild = client.post(f"/api/v1/projects/{pid}/branches/qa/rebuild", json={}, headers=headers)
        assert rebuild.status_code == 403, rebuild.get_json()
        create = client.post(f"/api/v1/projects/{pid}/branches/qa/instances", json={}, headers=headers)
        assert create.status_code == 403, create.get_json()
        assert not started

        # nobody asks for an automatic rebuild: it would show production's data to the maintainer, so not done
        assert "projmaint" in access.copy_reveals(projects.get(pid) or {}, "main", "qa")
        assert automation.update_for("erp-stage") is None

        # someone the production branch names may
        a_client, a_headers = admin
        answer = a_client.post(f"/api/v1/projects/{pid}/branches/qa/rebuild", json={}, headers=a_headers)
        assert answer.status_code == 202 and started[-1][:2] == ("erp-stage", staging.rebuild)

        # once production's branch is open to the same people again, the push rebuilds as before
        projects.change(pid, lambda p: {**p, "access": {}})
        access.forget_restrictions()
        assert access.copy_reveals(projects.get(pid) or {}, "main", "qa") == []
        found = automation.update_for("erp-stage")
        assert found is not None and found[2] is staging.rebuild
    finally:
        projects.change(pid, lambda p: {**p, "access": {}})
        access.forget_restrictions()


def test_switching_the_only_production_instance_moves_the_production_branch(
    admin: Client, started: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the default limit of one production branch, the only production instance of a branch can move to
    another branch: the new branch takes the old one's place in production (recorded at once), and the records go
    back when the switch does not happen."""
    from sudoo.web.v1 import instances as instances_api

    client, headers = admin
    pid = _linked(["main", "feature"])
    _instance("erp-main", "production", "Acme/ERP", "main", PROJECT=pid)
    _instance("erp-dev", "development", "Acme/ERP", "main", PROJECT=pid)  # stays on main, not production
    projects.record_branch(pid, "main", stage="production")
    live.change({"project_production_branches": 1})
    url = "/api/v1/instances/erp-main/actions/switch-branch"
    answer = client.post(url, json={"branch": "feature"}, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    fn, args = started[-1][1], started[-1][2]
    assert fn is instances_api._switch_production_branch and args[:2] == ("erp-main", "feature")
    recorded = (projects.get(pid) or {})["branches"]
    assert recorded["feature"]["stage"] == "production" and "stage" not in recorded["main"]

    # the switch fails before the instance reaches the branch: the records are put back
    def failing(job: Any, name: str, branch: str) -> None:
        raise JobError("fetch failed")

    monkeypatch.setattr(operations, "switch_branch", failing)
    with pytest.raises(JobError):
        fn(Job("erp-main", "Switch branch", "update", "t"), *args)
    recorded = (projects.get(pid) or {})["branches"]
    assert recorded["main"]["stage"] == "production" and "stage" not in recorded.get("feature", {})
    assert projects.production_branches(projects.get(pid) or {}) == ["main"]

    # the instance does move: one production branch, the new one
    store.update_env("erp-main", GIT_BRANCH="feature")
    projects.move_production(pid, "main", "feature", "t", keep_before=False)
    assert projects.production_branches(projects.get(pid) or {}) == ["feature"]


def test_switching_a_production_instance_refused_beyond_the_production_branch_limit(
    admin: Client, started: list[Any]
) -> None:
    """When another production instance keeps the earlier branch in production, the new branch is one more
    production branch: refused beyond the project's limit, allowed within it (the earlier branch stays recorded)."""
    client, headers = admin
    pid = _linked(["main", "feature"])
    _instance("erp-main", "production", "Acme/ERP", "main", PROJECT=pid)
    _instance("erp-old", "production", "Acme/ERP", "main", PROJECT=pid)
    _instance("erp-dev", "development", "Acme/ERP", "feature", PROJECT=pid)
    projects.record_branch(pid, "main", stage="production")
    live.change({"project_production_branches": 1})
    url = "/api/v1/instances/erp-main/actions/switch-branch"
    refused = client.post(url, json={"branch": "feature"}, headers=headers)
    assert refused.status_code == 409 and "production" in refused.get_json()["detail"], refused.get_json()
    assert not started and store.read_env("erp-main")["GIT_BRANCH"] == "main"
    assert "stage" not in (projects.get(pid) or {})["branches"].get("feature", {})
    answer = client.post(url, json={"branch": "main"}, headers=headers)  # its own branch: nothing more
    assert answer.status_code == 202 and started[-1][1] is operations.switch_branch
    other = client.post("/api/v1/instances/erp-dev/actions/switch-branch", json={"branch": "qa"}, headers=headers)
    assert other.status_code == 202  # not production: no limit
    live.change({"project_production_branches": 2})
    answer = client.post(url, json={"branch": "feature"}, headers=headers)
    assert answer.status_code == 202 and started[-1][2][:2] == ("erp-main", "feature")
    recorded = (projects.get(pid) or {})["branches"]
    assert recorded["main"]["stage"] == "production" and recorded["feature"]["stage"] == "production"


def test_connecting_a_repository_to_production_keeps_to_the_production_branch_limit(
    admin: Client, started: list[Any]
) -> None:
    """A plain production instance given a repository becomes a production branch of its project."""
    client, headers = admin
    pid = _linked(["main", "feature"])
    _instance("erp-main", "production", "Acme/ERP", "main", PROJECT=pid)
    projects.record_branch(pid, "main", stage="production")
    _instance("plain-one", "production", None)
    live.change({"project_production_branches": 1})
    url = "/api/v1/instances/plain-one/actions/connect-repository"
    body = {"repo": "https://github.com/Acme/ERP", "branch": "feature"}
    refused = client.post(url, json=body, headers=headers)
    assert refused.status_code == 409 and "production" in refused.get_json()["detail"], refused.get_json()
    assert not started
    answer = client.post(url, json={**body, "branch": "main"}, headers=headers)  # production already
    assert answer.status_code == 202, answer.get_json()
    answer = client.post(url, json={**body, "repo": "https://github.com/Acme/New"}, headers=headers)
    assert answer.status_code == 202  # a new project: its first production branch
    store.update_env("plain-one", STAGE="development")
    assert client.post(url, json=body, headers=headers).status_code == 202  # not production: no limit


def test_moving_a_branch_out_of_production_needs_the_delete_permission(app: Flask, admin: Client) -> None:
    if accounts.get("projmaint") is None:
        accounts.create("projmaint", "maintainer", "lilac-harbor-quill-91")
    client, headers = sign_in(app, "projmaint", "lilac-harbor-quill-91")
    pid = _linked(["main"])
    _instance("erp-main", "production", "Acme/ERP", "main", PROJECT=pid)
    projects.record_branch(pid, "main", stage="production")
    url = f"/api/v1/projects/{pid}/branches/main/stage"
    body = {"stage": "staging", "confirm_production": True}
    assert client.put(url, json=body, headers=headers).status_code == 403
    assert store.read_env("erp-main")["STAGE"] == "production"
    a_client, a_headers = admin
    assert a_client.put(url, json=body, headers=a_headers).status_code == 200
    assert store.read_env("erp-main")["STAGE"] == "staging"


def test_the_rebuild_copies_neutralises_and_upgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    project = projects.create(
        repository="Acme/ERP", odoo_version="18.0", edition="community", origin="linked", user="t"
    )
    pid = project["id"]
    _instance("erp-main", "production", "Acme/ERP", "main", PROJECT=pid)
    _instance("erp-stage", "staging", "Acme/ERP", "qa", PROJECT=pid)
    steps: list[str] = []
    copy = {"file": "erp-main__erp_main__20261118-010000__staging-copy.zip", "instance": "erp-main", "db": "erp_main",
            "created": time.time()}  # fmt: skip

    def backup(job: Job, name: str, kind: str = "manual") -> list[Any]:
        steps.append(f"backup {name} {kind} locked={jobs.INSTANCE_LOCKS[name].locked()}")
        return [settings.backups_dir / copy["file"]]

    monkeypatch.setattr(backups, "create", backup)
    monkeypatch.setattr(backups, "all_backups", lambda: [copy])
    monkeypatch.setattr(backups, "restore", lambda job, name, b, db: steps.append(f"restore {b['file']} {name} {db}"))
    monkeypatch.setattr(backups, "neutralize", lambda job, name, db: steps.append(f"neutralize {name} {db}") or "ok")
    monkeypatch.setattr(backups, "installed_modules", lambda name, db: {"acme_sale", "sale"})
    monkeypatch.setattr(staging, "own_modules", lambda addons: ["acme_sale", "acme_unused"])
    monkeypatch.setattr(operations, "_require_branch", lambda job, name, addons: steps.append("branch"))
    monkeypatch.setattr(operations, "_pull", lambda job, addons: steps.append("pull"))
    monkeypatch.setattr(operations, "_adopt_admin", lambda job, name, db: steps.append("admin") or "admin")
    monkeypatch.setattr(operations, "refresh_addons_path", lambda job, name: None)
    monkeypatch.setattr(operations, "_finish_restore", lambda job, name: steps.append("start"))
    job = Job("erp-stage", "Rebuild", "restore", "t")
    commands: list[list[str]] = []
    monkeypatch.setattr(job, "run", lambda cmd, **_: commands.append(cmd) or "")
    monkeypatch.setattr(job, "run_unchecked", lambda cmd, **_: commands.append(cmd) or 0)
    staging.rebuild(job, "erp-stage")
    assert steps == [
        "backup erp-main staging-copy locked=True",  # production is copied while none of its jobs runs
        "branch",
        "pull",
        f"restore {copy['file']} erp-stage erp_stage",
        "neutralize erp-stage erp_stage",
        "admin",
        "start",
    ]
    upgrade = next(cmd for cmd in commands if "-u" in cmd)
    assert upgrade[upgrade.index("-u") + 1] == "acme_sale"  # the repository's installed modules only
    assert STATE.get("erp-stage")["rebuilt_from"] == "erp-main"
    assert not jobs.INSTANCE_LOCKS["erp-main"].locked()

    # only staging is rebuilt
    with pytest.raises(JobError):
        staging.rebuild(Job("erp-main", "Rebuild", "restore", "t"), "erp-main")


def test_neutralising_without_odoo_neutralize_also_stops_payments() -> None:
    sql = backups._NEUTRALIZE_SQL
    assert "UPDATE ir_mail_server SET active = false" in sql and "UPDATE ir_cron SET active = false" in sql
    assert "payment_provider SET state = 'disabled'" in sql and "payment_acquirer" in sql
    assert backups.FILE_RE.match("erp-main__erp_main__20261118-010000__staging-copy.zip")
    assert "staging-copy" in backups._KEEP_ONE


# ---------------------------------------------------------------- what happens on GitHub


SECRET = "s" * 32


@pytest.fixture
def webhook() -> Iterator[tuple[FlaskClient, list[tuple[str, str]]]]:
    from flask import Flask as App

    from sudoo.triggers.github_webhook import GitHubWebhook

    trigger = GitHubWebhook(Settings(github_webhook_secret=SECRET))
    requested: list[tuple[str, str]] = []
    trigger.start(lambda name, reason: requested.append((name, reason)))
    flask_app = App(__name__)
    blueprint = trigger.blueprint()
    assert blueprint is not None
    flask_app.register_blueprint(blueprint)
    yield flask_app.test_client(), requested


def _push(client: FlaskClient, branch: str, repository: str = "Acme/ERP", **flags: bool) -> Any:
    body = json.dumps({"ref": f"refs/heads/{branch}", "repository": {"full_name": repository}, **flags}).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Event": "push"}
    return client.post("/hooks/github", data=body, headers=headers, content_type="application/json").get_json()


def _linked(seen: list[str] | None = None, **options: Any) -> str:
    project = projects.create(
        repository="Acme/ERP", odoo_version="18.0", edition="community", origin="linked", user="t"
    )
    projects.change(project["id"], lambda p: {**p, "seen": seen, "options": options})
    return str(project["id"])


def test_a_new_branch_gets_a_development_instance(webhook: Any, started: list[Any]) -> None:
    client, requested = webhook
    pid = _linked(["main"], max_auto_development=1)
    live.change({"delete_after_days": 9, "project_development_demo_data": False})
    answer = _push(client, "feature-a", created=True)
    plan = _plan(started)
    assert answer["created"] == plan["name"] and plan["project"] == pid and plan["branch"] == "feature-a"
    assert plan["stage"] == "development" and plan["delete_after_days"] == 9 and plan["demo"] is False
    assert plan["created_by"] == "system" and requested == []
    project = projects.get(pid) or {}
    assert project["branches"]["feature-a"]["auto_created"] == plan["name"] and "feature-a" in project["seen"]

    # told twice (the webhook and the branch check): handled once
    assert automation.branch_created("acme/erp", "feature-a") is None and len(started) == 1
    # the project's limit: one instance created this way exists already (it is being made: counted)
    _instance("erp-dev", "development", "Acme/ERP", "feature-a", PROJECT=pid)
    projects.record_branch(pid, "feature-a", auto_created="erp-dev")
    assert _push(client, "feature-b", created=True)["created"] is None and len(started) == 1
    assert "feature-b" in (projects.get(pid) or {})["seen"]
    # a branch someone made staging before pushing it: created by hand
    projects.change(pid, lambda p: {**p, "options": {}})
    projects.record_branch(pid, "release", stage="staging")
    assert automation.branch_created("acme/erp", "release") is None
    # switched off for the project
    projects.change(pid, lambda p: {**p, "options": {"auto_development": False}})
    assert automation.branch_created("acme/erp", "feature-c") is None and len(started) == 1


def test_the_first_look_creates_nothing(started: list[Any]) -> None:
    pid = _linked(None)
    HEADS["https://github.com/Acme/ERP"] = {"main": "a" * 40, "old-1": "b" * 40, "old-2": "c" * 40}
    assert automation.sync(projects.get(pid) or {}) == {"created": [], "deleted": []}
    assert (projects.get(pid) or {})["seen"] == ["main", "old-1", "old-2"] and started == []
    HEADS["https://github.com/Acme/ERP"]["new"] = "d" * 40
    assert automation.sync(projects.get(pid) or {})["created"] == ["new"]
    assert _plan(started)["branch"] == "new"
    # a webhook for a project never looked at: the other branches are known, the new one gets its instance
    other = projects.create(repository="Acme/Shop", odoo_version="18.0", edition="community", origin="adopted",
                            user="t")  # fmt: skip
    HEADS["https://github.com/Acme/Shop"] = {"main": "a" * 40, "hotfix": "e" * 40}
    assert automation.branch_created("acme/shop", "hotfix") is not None
    assert (projects.get(other["id"]) or {})["seen"] == ["hotfix", "main"]


def test_a_deleted_branch_archives_or_deletes_by_the_projects_option(webhook: Any, started: list[Any]) -> None:
    client, requested = webhook
    pid = _linked(["main", "qa", "feature", "keepme"], branch_deleted="delete")
    _instance("erp-main", "production", "Acme/ERP", "main", PROJECT=pid)
    _instance("erp-stage", "staging", "Acme/ERP", "qa", PROJECT=pid)
    _instance("erp-dev", "development", "Acme/ERP", "feature", PROJECT=pid)
    assert _push(client, "feature", deleted=True)["branch_deleted"] == ["erp-dev:delete"]
    assert started[-1][:2] == ("erp-dev", operations.delete) and requested == []
    assert STATE.get("erp-dev")["branch_missing"] is True
    automation.branch_deleted("acme/erp", "qa")
    assert started[-1][:2] == ("erp-stage", automation.archive)  # staging: archived, never deleted for this
    assert automation.branch_deleted("acme/erp", "main") == ["erp-main:keep"]  # production: only marked
    assert len(started) == 2
    # the branch check finds deleted branches too; a branch without instances is forgotten
    HEADS["https://github.com/Acme/ERP"] = {"main": "a" * 40}
    assert automation.sync(projects.get(pid) or {})["deleted"] == ["keepme"]
    assert "keepme" not in (projects.get(pid) or {})["seen"]
    projects.change(pid, lambda p: {**p, "options": {"branch_deleted": "keep"}, "seen": ["main", "feature"]})
    assert automation.branch_deleted("acme/erp", "feature") == ["erp-dev:keep"] and len(started) == 2


def test_archiving_backs_up_and_switches_off(monkeypatch: pytest.MonkeyPatch) -> None:
    done: list[str] = []
    monkeypatch.setattr(operations, "backup", lambda job, name: done.append(f"backup {name}"))
    monkeypatch.setattr(operations, "switch_off", lambda job, name: done.append(f"off {name}"))
    automation.archive(Job("erp-dev", "Archive", "stop", "t"), "erp-dev", "feature")
    assert done == ["backup erp-dev", "off erp-dev"] and STATE.get("erp-dev")["archived"]["branch"] == "feature"


def test_a_push_to_staging_rebuilds_it_from_production(webhook: Any, started: list[Any]) -> None:
    pid = _linked(["main", "qa"])
    _instance("erp-main", "production", "Acme/ERP", "main", PROJECT=pid)
    _instance("erp-stage", "staging", "Acme/ERP", "qa", PROJECT=pid)
    triggers.request_update("erp-stage", "push to acme/erp qa")
    assert started[-1][:2] == ("erp-stage", staging.rebuild)
    projects.change(pid, lambda p: {**p, "options": {"staging_on_push": "keep_data"}})
    triggers.request_update("erp-stage", "push to acme/erp qa")
    assert started[-1][:2] == ("erp-stage", operations.update_code)
    triggers.request_update("erp-main", "push")  # production: the usual (safe) update
    assert started[-1][:2] == ("erp-main", operations.update_code)
