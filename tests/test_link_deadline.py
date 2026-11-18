"""Linking the panel to a Sudoo.sh account within LINK_DEADLINE_DAYS of its first run (link_deadline.py): the
deadline, who sees the banner, what is limited once it has passed, what keeps working, and that linking clears it.
Docker and GitHub never run: jobs are recorded."""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from sudoo import accounts, link_deadline, projects, triggers
from sudoo.config import live, settings
from sudoo.core import audit, jobs
from sudoo.core.state import STATE
from sudoo.hub import marketplace
from sudoo.instances import integrity, store
from sudoo.limits import quotas
from sudoo.projects import automation
from sudoo.repos import github
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
SHOP = "link-shop"
DAY = 86400
NEW = {"version": "18.0", "edition": "community", "name": "link-new"}


def _instance(name: str, stage: str = "development") -> None:
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(folder / "addons")], check=True)
    subprocess.run(
        ["git", "-C", str(folder / "addons"), "remote", "add", "origin", "https://github.com/acme/link"], check=True
    )
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39601,
            "ODOO_PORT": 39602,
            "GIT_BRANCH": "main",
            "AUTO_UPDATE": "true",
            "AUTO_UPGRADE": "false",
            "AUTO_PAUSE": "false",
            "DELETE_AFTER_DAYS": "7",
            "CREATED_AT": int(time.time()),
            "CREATED_BY": "sam",
            "STAGE": stage,
        },
    )


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    before = storage().get(link_deadline.COLLECTION, link_deadline.FIRST_RUN)
    monkeypatch.setattr(quotas, "claim", lambda user: None)
    monkeypatch.setattr(marketplace, "trusted_now", time.time)
    _instance(SHOP)
    yield
    if before is None:
        storage().delete(link_deadline.COLLECTION, link_deadline.FIRST_RUN)
    else:
        storage().put(link_deadline.COLLECTION, link_deadline.FIRST_RUN, before)
    shutil.rmtree(store.folder(SHOP), ignore_errors=True)
    STATE.remove(SHOP)
    integrity.forget(SHOP)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_start(
        instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any
    ) -> jobs.Job:
        calls.append({"instance": instance, "action": action, "args": args})
        if args and isinstance(args[0], dict) and "ports" in args[0]:
            store.release_ports(args[0]["ports"])
        return jobs.Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    return calls


def _first_run(days_ago: float) -> None:
    storage().put(link_deadline.COLLECTION, link_deadline.FIRST_RUN, {"at": time.time() - days_ago * DAY})


def _link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "marketplace", True)
    monkeypatch.setattr(marketplace, "state", lambda: {"linked": {"account_name": "Acme", "linked_at": 1}})


# ---------------------------------------------------------------- the deadline


def test_the_first_run_is_recorded_once() -> None:
    storage().delete(link_deadline.COLLECTION, link_deadline.FIRST_RUN)
    first = link_deadline.first_run()
    assert abs(first - time.time()) < 5
    live.change({"link_deadline_days": 30})  # a setting never moves the first run
    assert link_deadline.first_run() == first
    _first_run(3)
    assert link_deadline.first_run() < first


def test_deadline_and_days_left() -> None:
    _first_run(0)
    first = link_deadline.first_run()
    found = link_deadline.status(now=first)
    assert found["deadline"] == first + 7 * DAY and found["days_left"] == 7 and not found["restricted"]
    assert link_deadline.status(now=first + 6.5 * DAY)["days_left"] == 1  # the last day still counts as one
    assert link_deadline.status(now=first + 7 * DAY)["days_left"] == 0
    assert link_deadline.status(now=first + 7 * DAY)["restricted"]
    live.change({"link_deadline_days": 10})
    later = link_deadline.status(now=first + 8 * DAY)
    assert later["deadline_days"] == 10 and later["days_left"] == 2 and not later["restricted"]


def test_the_setting_is_documented_and_editable() -> None:
    from sudoo.config import EDITABLE

    assert "link_deadline_days" in EDITABLE and settings.link_deadline_days == 7


# ---------------------------------------------------------------- the banner, by role


def test_banner_for_super_admins_and_admins_only(app: Flask, admin: Client) -> None:
    _first_run(2)
    client, _ = admin
    shown = client.get("/api/v1/plan").get_json()["data"]["link_deadline"]
    assert shown["days_left"] == 5 and shown["linked"] is False and shown["restricted"] is False
    for name, role in (("ldadmin", "admin"), ("ldmaint", "maintainer"), ("lddev", "developer"), ("ldview", "viewer")):
        accounts.create(name, role, "harbor-quartz-lantern-73")
        try:
            other, _ = sign_in(app, name, "harbor-quartz-lantern-73")
            found = other.get("/api/v1/plan").get_json()["data"]["link_deadline"]
            assert (found is not None) == (role == "admin"), role
        finally:
            accounts.delete(name)


# ---------------------------------------------------------------- after the deadline


def test_nothing_is_limited_before_the_deadline(admin: Client, started: list[dict[str, Any]]) -> None:
    _first_run(6.9)
    client, headers = admin
    assert client.post("/api/v1/instances", json=NEW, headers=headers).status_code == 202
    assert client.post(f"/api/v1/instances/{SHOP}/actions/update", json={}, headers=headers).status_code == 202


def test_management_is_limited_after_the_deadline(admin: Client, started: list[dict[str, Any]]) -> None:
    _first_run(8)
    client, headers = admin
    refused = client.post("/api/v1/instances", json=NEW, headers=headers)
    assert refused.status_code == 403 and "not linked" in refused.get_json()["detail"]
    for action in ("update", "update-submodules", "switch-branch", "upgrade", "update-images"):
        answer = client.post(f"/api/v1/instances/{SHOP}/actions/{action}", json={"branch": "dev"}, headers=headers)
        assert answer.status_code == 403 and "not linked" in answer.get_json()["detail"], action
    stage = client.patch(f"/api/v1/instances/{SHOP}", json={"stage": "production"}, headers=headers)
    assert stage.status_code == 403 and store.read_env(SHOP)["STAGE"] == "development"
    assert not started
    assert any("panel not linked" in e["event"] for e in audit.latest(5))


def test_running_instances_keep_working_after_the_deadline(admin: Client, started: list[dict[str, Any]]) -> None:
    _first_run(8)
    client, headers = admin
    for action in ("restart", "start", "pause", "backup"):
        answer = client.post(f"/api/v1/instances/{SHOP}/actions/{action}", json={}, headers=headers)
        assert answer.status_code == 202, (action, answer.get_json())
    assert client.patch(f"/api/v1/instances/{SHOP}", json={"auto_pause": True}, headers=headers).status_code == 200


def test_branches_and_automation_wait_after_the_deadline(
    admin: Client, started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _first_run(8)
    client, headers = admin
    project = projects.create(repository="Acme/Link", odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    pid = project["id"]
    monkeypatch.setattr(github, "heads", lambda address: ({"main": "a" * 40, "dev": "b" * 40}, "main", None))
    answer = client.put(f"/api/v1/projects/{pid}/branches/main/stage",
                        json={"stage": "production", "confirm_production": True}, headers=headers)  # fmt: skip
    assert answer.status_code == 403 and "not linked" in answer.get_json()["detail"]
    created = client.post(f"/api/v1/projects/{pid}/branches/dev/instances", json={}, headers=headers)
    assert created.status_code == 403
    projects.change(pid, lambda p: {**p, "options": {"auto_development": True}})
    assert "not linked" in (automation.creation_refusal(projects.get(pid) or {}, "new-branch") or "")
    triggers.request_update(SHOP, "push")
    assert not started
    assert audit.latest(1)[0]["event"] == "automatic update skipped (panel not linked)"


def test_linking_clears_it_at_once(
    admin: Client, started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _first_run(30)
    assert link_deadline.restricted()
    _link(monkeypatch)
    assert not link_deadline.restricted() and link_deadline.status()["days_left"] == 0
    client, headers = admin
    assert client.post("/api/v1/instances", json=NEW, headers=headers).status_code == 202
    assert client.post(f"/api/v1/instances/{SHOP}/actions/update", json={}, headers=headers).status_code == 202
    assert client.get("/api/v1/plan").get_json()["data"]["link_deadline"]["linked"] is True
    # the marketplace turned off: no longer linked, the limits are back
    monkeypatch.setattr(settings, "marketplace", False)
    assert link_deadline.restricted()
