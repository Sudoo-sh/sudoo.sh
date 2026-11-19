"""Stages (instances/stages.py): production, staging and development. Production is never paused or deleted
automatically, is backed up every day and updated safely, needs extra confirmations, and is limited in number;
instances made before stages existed are development instances. Docker never runs: jobs are recorded, not started."""

from __future__ import annotations

import os
import shutil
import time
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from sudoo import accounts, background
from sudoo.config import live, settings
from sudoo.core import audit, jobs
from sudoo.core.state import STATE
from sudoo.instances import backups, monitor, operations, stages, store
from sudoo.limits import quotas

Client = tuple[FlaskClient, dict[str, str]]
NAMES = ("prod", "stage", "dev", "old", "prod-two")


def _instance(name: str, stage: str | None, created_days_ago: float = 60, **env: Any) -> None:
    """An instance created `created_days_ago`, with a 30-day lifetime and the automatic pause on; `stage` None: one
    made before stages existed (no STAGE in its .env)."""
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39300 + 2 * NAMES.index(name),
            "ODOO_PORT": 39301 + 2 * NAMES.index(name),
            "GIT_BRANCH": "main",
            "AUTO_UPDATE": "true",
            "AUTO_UPGRADE": "true",
            "AUTO_PAUSE": "true",
            "DELETE_AFTER_DAYS": "30",
            "CREATED_AT": int(time.time() - created_days_ago * 86400),
            **({"STAGE": stage} if stage else {}),
            **env,
        },
    )


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
        monitor.STATUS.pop(name, None)
    shutil.rmtree(settings.backups_dir, ignore_errors=True)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any, tuple[Any, ...]]]:
    """Jobs are recorded (instance, function, arguments) instead of run."""
    calls: list[tuple[str, Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system",
                   **_: Any) -> jobs.Job:  # fmt: skip
        calls.append((instance, fn, args))
        return jobs.Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    monkeypatch.setattr(quotas, "claim", lambda user: None)
    return calls


@pytest.fixture(scope="module")
def maintainer(app: Flask) -> Client:
    accounts.create("stagemaint", "maintainer", "lilac-ferry-anchor-91")
    return sign_in(app, "stagemaint", "lilac-ferry-anchor-91")


@pytest.fixture(scope="module")
def developer(app: Flask) -> Client:
    accounts.create("stagedev", "developer", "pewter-orchid-sable-26")
    return sign_in(app, "stagedev", "pewter-orchid-sable-26")


# ---------------------------------------------------------------- existing instances


def test_an_instance_made_before_stages_is_development_and_behaves_as_before(admin: Client, started: list[Any]) -> None:
    _instance("old", None)
    env = store.read_env("old")
    assert stages.of(env) == "development" and "STAGE" not in env
    assert stages.pauses_when_idle(env) and operations.expires_at(env) is not None
    assert stages.safe_updates(env) is True
    live.change({"safe_updates": False})
    assert stages.safe_updates(env) is False  # SAFE_UPDATES applies to it, as before
    client, _ = admin
    assert client.get("/api/v1/instances/old").get_json()["data"]["stage"] == "development"
    assert background.delete_expired_instances() == ["old"]  # its lifetime is over: deleted as before


# ---------------------------------------------------------------- automatic pause, deletion and backups


def test_production_is_never_paused_automatically(started: list[Any]) -> None:
    for name, stage in (("prod", "production"), ("stage", "staging"), ("dev", "development")):
        _instance(name, stage)
        monitor.STATUS[name] = {"running": True}
        STATE.update(name, last_visit=time.time() - 30 * 3600)
    assert background.pause_idle_instances() == ["dev", "stage"]
    assert all(instance != "prod" for instance, *_ in started)


def test_production_is_never_deleted_automatically(started: list[Any]) -> None:
    _instance("prod", "production")  # 60 days old with a 30-day lifetime in its .env
    _instance("dev", "development")
    assert operations.expires_at(store.read_env("prod")) is None
    assert background.delete_expired_instances() == ["dev"]


def test_production_is_backed_up_every_day_whatever_the_settings(started: list[Any]) -> None:
    _instance("prod", "production")
    _instance("dev", "development")
    _instance("stage", "staging")
    live.change({"daily_backups": False})
    tried: dict[str, float] = {}
    assert background.daily_backups(tried) == ["prod"]  # not used, DAILY_BACKUPS off: backed up anyway
    assert background.daily_backups(tried) == []  # once per slot
    assert started[-1][1] is operations.daily_backup

    # a daily backup from before the slot: due; one taken since the slot: nothing to do
    slot = backups.daily_slot()
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    older = settings.backups_dir / "prod__prod__20261116-000000__daily.zip"
    with zipfile.ZipFile(older, "w") as archive:
        archive.writestr("dump.sql", "-- empty\n")
    os.utime(older, (slot - 3600, slot - 3600))
    assert stages.daily_backup_due("prod", store.read_env("prod"), None, slot)
    older.rename(settings.backups_dir / "prod__prod__20261117-000000__daily.zip")
    os.utime(settings.backups_dir / "prod__prod__20261117-000000__daily.zip", (slot + 60, slot + 60))
    assert not stages.daily_backup_due("prod", store.read_env("prod"), None, slot)


def test_staging_daily_backups_can_be_turned_off(started: list[Any]) -> None:
    _instance("stage", "staging")
    _instance("dev", "development")
    used = time.time() - 2 * 86400
    for name in ("stage", "dev"):
        STATE.update(name, used_since=used)
    slot = backups.daily_slot()
    assert stages.daily_backup_due("stage", store.read_env("stage"), used, slot)
    live.change({"staging_daily_backups": False})
    assert not stages.daily_backup_due("stage", store.read_env("stage"), used, slot)
    assert stages.daily_backup_due("dev", store.read_env("dev"), used, slot)
    assert background.daily_backups({}) == ["dev"]


# ---------------------------------------------------------------- creating


def _create(client: FlaskClient, headers: dict[str, str], **body: Any) -> Any:
    return client.post("/api/v1/instances", json={"version": "18.0", "edition": "community", **body}, headers=headers)


def _plan(started: list[Any]) -> dict[str, Any]:
    plan: dict[str, Any] = started[-1][2][0]
    store.release_ports(plan["ports"])
    return plan


def test_create_with_a_stage_and_its_defaults(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    live.change({"production_auto_update": False, "staging_delete_after_days": 120, "delete_after_days": 30})

    assert _create(client, headers, name="dev").status_code == 202
    plan = _plan(started)
    assert plan["stage"] == "development" and plan["delete_after_days"] == 30 and plan["auto_pause"] is True

    assert _create(client, headers, name="stage", stage="staging").status_code == 202
    plan = _plan(started)
    assert plan["stage"] == "staging" and plan["delete_after_days"] == 120

    assert _create(client, headers, name="prod", stage="production").status_code == 202
    plan = _plan(started)
    assert plan["stage"] == "production" and plan["delete_after_days"] == 0
    assert plan["auto_pause"] is False and plan["auto_update"] is False

    assert _create(client, headers, name="prod", stage="production", delete_after_days=30).status_code == 400
    assert _create(client, headers, name="prod", stage="production", auto_pause=True).status_code == 400
    assert _create(client, headers, name="prod", stage="live").status_code == 400

    defaults = client.get("/api/v1/instance-plan?name=x&stage=production").get_json()["data"]
    assert defaults["defaults"]["delete_after_days"] == 0
    assert defaults["stage_defaults"]["staging"]["delete_after_days"] == 120
    assert defaults["production"]["limit"] == 1


def test_creation_writes_the_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """operations._create records STAGE in .env (the steps that run Docker are skipped)."""
    written: dict[str, Any] = {}
    monkeypatch.setattr(
        store, "write_env", lambda name, env: written.update(env) or (_ for _ in ()).throw(StopIteration)
    )
    for step in ("_download_images",):
        monkeypatch.setattr(operations, step, lambda *args: None)
    job = jobs.Job("prod", "Create instance", "create", "admin1")
    monkeypatch.setattr(job, "run", lambda *args, **kwargs: None)
    store.folder("prod").mkdir(parents=True, exist_ok=True)
    plan = {"name": "prod", "db_name": "prod", "version": "18.0", "edition": "community", "repo": "", "branch": "",
            "install_modules": False, "demo": False, "ports": {}, "auto_update": False, "auto_upgrade": False,
            "auto_pause": False, "delete_after_days": 0, "stage": "production"}  # fmt: skip
    with pytest.raises(StopIteration):
        operations._create(job, plan)
    assert written["STAGE"] == "production"


def test_the_number_of_production_instances_is_limited(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    _instance("prod", "production")
    _instance("dev", "development")
    answer = _create(client, headers, name="prod-two", stage="production")
    assert answer.status_code == 409 and "prod" in answer.get_json()["detail"]
    assert client.patch("/api/v1/instances/dev", json={"stage": "production"}, headers=headers).status_code == 409
    assert stages.of(store.read_env("dev")) == "development"

    live.change({"max_production_instances": 2})
    assert _create(client, headers, name="prod-two", stage="production").status_code == 202
    _plan(started)
    live.change({"max_production_instances": 0})  # no limit
    assert stages.production_refusal() is None
    assert client.patch("/api/v1/instances/dev", json={"stage": "production"}, headers=headers).status_code == 200


def test_the_limit_comes_from_one_function(admin: Client, started: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Later the server's licence gives the limit: stages.production_limit() is the only place it is read."""
    client, headers = admin
    _instance("prod", "production")
    monkeypatch.setattr(stages, "production_limit", lambda: 5)
    assert _create(client, headers, name="prod-two", stage="production").status_code == 202
    _plan(started)


# ---------------------------------------------------------------- changing the stage


def test_promote_and_demote(admin: Client, developer: Client) -> None:
    client, headers = admin
    _instance("dev", "development", created_days_ago=1)
    url = "/api/v1/instances/dev"
    d_client, d_headers = developer
    assert d_client.patch(url, json={"stage": "production"}, headers=d_headers).status_code == 403  # instances.operate

    answer = client.patch(url, json={"stage": "production"}, headers=headers)
    assert answer.status_code == 200
    data = answer.get_json()["data"]
    assert data["stage"] == "production" and data["expires_at"] is None and data["settings"]["auto_pause"] is False
    env = store.read_env("dev")
    assert env["STAGE"] == "production" and env["DELETE_AFTER_DAYS"] == "0" and env["AUTO_PAUSE"] == "false"
    assert audit.latest(5, instance="dev")[1]["event"] == "instance stage changed"

    # production refuses a lifetime and the automatic pause
    assert client.patch(url, json={"delete_after_days": 30}, headers=headers).status_code == 409
    assert client.patch(url, json={"auto_pause": True}, headers=headers).status_code == 409
    assert client.patch(url, json={"delete_after_days": 0}, headers=headers).status_code == 200
    assert client.patch(url, json={"stage": "production"}, headers=headers).status_code == 200  # already
    assert client.patch(url, json={"auto_update": False}, headers=headers).status_code == 200

    # leaving production needs a confirmation
    assert client.patch(url, json={"stage": "staging"}, headers=headers).status_code == 400
    answer = client.patch(url, json={"stage": "staging", "confirm_production": True}, headers=headers)
    assert answer.status_code == 200 and answer.get_json()["data"]["stage"] == "staging"
    logged = audit.latest(5, instance="dev")
    assert any(e["event"] == "instance stage changed" and "production -> staging" in e["detail"] for e in logged)
    assert client.patch(url, json={"stage": "development"}, headers=headers).status_code == 200


# ---------------------------------------------------------------- confirmations


def test_pause_switch_off_and_restore_of_production_need_a_confirmation(admin: Client, developer: Client,
                                                                     started: list[Any]) -> None:  # fmt: skip
    client, headers = admin
    _instance("prod", "production")
    _instance("dev", "development")
    for action in ("pause", "switch-off"):
        url = f"/api/v1/instances/prod/actions/{action}"
        assert client.post(url, json={}, headers=headers).status_code == 400
        assert client.post(url, json={"confirm_production": True}, headers=headers).status_code == 202
        d_client, d_headers = developer
        assert d_client.post(url, json={"confirm_production": True}, headers=d_headers).status_code == 403
        assert client.post(f"/api/v1/instances/dev/actions/{action}", json={}, headers=headers).status_code == 202
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    restore = "/api/v1/instances/prod/actions/restore"
    assert client.post(restore, json={}, headers=headers).status_code == 400
    assert started[-1][0] == "dev"


def test_deleting_production_needs_its_name_and_the_word_production(admin: Client, maintainer: Client,
                                                                    started: list[Any]) -> None:  # fmt: skip
    client, headers = admin
    _instance("prod", "production")
    url = "/api/v1/instances/prod"
    assert client.delete(url, json={"confirm": "prod"}, headers=headers).status_code == 400
    assert client.delete(url, json={"confirm": "prod", "confirm_stage": "staging"}, headers=headers).status_code == 400
    m_client, m_headers = maintainer
    both = {"confirm": "prod", "confirm_stage": "production"}
    assert m_client.delete(url, json=both, headers=m_headers).status_code == 403  # instances.delete
    assert client.delete(url, json=both, headers=headers).status_code == 202
    assert started[-1][1] is operations.delete


def test_leaving_production_needs_the_delete_permission(admin: Client, maintainer: Client,
                                                        started: list[Any]) -> None:  # fmt: skip
    """Out of production an instance can expire: a maintainer (no instances.delete) can neither move it out with a
    short lifetime in one request (its expiry is worked out with the new stage) nor move it out at all."""
    _instance("prod", "production", created_days_ago=30, DELETE_AFTER_DAYS="0")
    url = "/api/v1/instances/prod"
    m_client, m_headers = maintainer
    before = store.read_env("prod")
    for body in (
        {"stage": "staging", "confirm_production": True, "delete_after_days": 1},
        {"stage": "development", "confirm_production": True, "delete_after_days": 7},
        {"stage": "staging", "confirm_production": True},
    ):
        assert m_client.patch(url, json=body, headers=m_headers).status_code == 403
        assert store.read_env("prod") == before
    assert operations.expires_at(store.read_env("prod")) is None
    background.delete_expired_instances()
    assert all(call[0] != "prod" for call in started)

    client, headers = admin  # with instances.delete it may
    body = {"stage": "staging", "confirm_production": True, "delete_after_days": 60}
    assert client.patch(url, json=body, headers=headers).status_code == 200
    assert store.read_env("prod")["STAGE"] == "staging"


def test_the_resource_lists_the_stage(admin: Client) -> None:
    client, _ = admin
    _instance("prod", "production")
    _instance("stage", "staging")
    found = {i["name"]: i["stage"] for i in client.get("/api/v1/instances").get_json()["data"]}
    assert found["prod"] == "production" and found["stage"] == "staging"
