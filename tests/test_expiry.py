"""The end of an instance's lifetime (background.py, instances/expiry.py, PATCH delete_after_days): stopped first and
deleted after the grace period, e-mails once, extensions within limits, and an extended instance starts again.
Docker never runs: jobs are recorded, not started."""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

import sudoo.mail
from conftest import sign_in
from sudoo import accounts, background
from sudoo.config import live, settings
from sudoo.core import jobs
from sudoo.core.state import STATE
from sudoo.instances import capacity, expiry, monitor, operations, store
from sudoo.projects import notices
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
NAME = "expiring"
DAY = 86400


class Mailbox:
    ready = True

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        self.sent.append({"to": to, "subject": subject, "text": text})


def _instance(created_days_ago: float = 10, days: int = 30, **env: Any) -> None:
    folder = store.folder(NAME)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        NAME,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": "community",
            "DB_NAME": "expiring",
            "GATEWAY_PORT": 39390,
            "ODOO_PORT": 39391,
            "GIT_BRANCH": "main",
            "AUTO_UPDATE": "true",
            "AUTO_UPGRADE": "true",
            "AUTO_PAUSE": "true",
            "DELETE_AFTER_DAYS": str(days),
            "CREATED_AT": int(time.time() - created_days_ago * DAY),
            **env,
        },
    )


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    shutil.rmtree(store.folder(NAME), ignore_errors=True)
    STATE.remove(NAME)
    monitor.STATUS.pop(NAME, None)
    for key in list(storage().all(expiry.COLLECTION)):
        storage().delete(expiry.COLLECTION, key)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any, tuple[Any, ...]]]:
    calls: list[tuple[str, Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system",
                   **_: Any) -> jobs.Job:  # fmt: skip
        calls.append((instance, fn, args))
        return jobs.Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    return calls


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> Mailbox:
    fake = Mailbox()
    monkeypatch.setattr(sudoo.mail, "mailer", lambda: fake)
    return fake


def _person(name: str, role: str = "maintainer", **values: Any) -> str:
    accounts.create(name, role, "saffron-kettle-orbit-73", email=f"{name}@example.com", **values)
    return name


# ---------------------------------------------------------------- stopped first, deleted after the grace period


def test_an_expired_instance_is_stopped_first_and_deleted_after_the_grace_period(
    started: list[tuple[str, Any, tuple[Any, ...]]],
) -> None:
    _instance(created_days_ago=40, days=30)  # expired 10 days ago
    now = time.time()
    assert background.delete_expired_instances(now) == [NAME]
    assert started[-1][1] is operations.stop_expired and store.exists(NAME)
    STATE.update(NAME, expired_at=int(now))  # what stop_expired records
    assert background.delete_expired_instances(now + 6 * DAY) == []  # grace: 7 days
    assert background.delete_expired_instances(now + 7 * DAY) == [NAME]
    assert started[-1][1] is operations.expire


def test_a_grace_period_of_zero_deletes_at_once(started: list[tuple[str, Any, tuple[Any, ...]]]) -> None:
    live.change({"expiry_grace_days": 0})
    _instance(created_days_ago=40, days=30)
    assert background.delete_expired_instances() == [NAME] and started[-1][1] is operations.expire


def test_stopping_records_the_date_and_tells_the_people(monkeypatch: pytest.MonkeyPatch, mailbox: Mailbox) -> None:
    person = _person("expmaker")
    _instance(created_days_ago=40, days=30, CREATED_BY=person)
    stops: list[str] = []
    monkeypatch.setattr(
        operations.engines, "of", lambda name: type("E", (), {"stop": lambda s, j, n: stops.append(n)})()
    )
    monkeypatch.setattr(operations.monitor, "mark_stopped", lambda name: None)
    job = jobs.Job(NAME, "Stopped", "pause", "system")
    operations.stop_expired(job, NAME, 1_800_000_000)
    state = STATE.get(NAME)
    assert stops == [NAME] and state["expired_at"] == 1_800_000_000 and state["switched_off"] and store.exists(NAME)
    assert [m["to"] for m in mailbox.sent] == ["expmaker@example.com"]
    assert "will be deleted" in mailbox.sent[0]["subject"]


# ---------------------------------------------------------------- the warning e-mail


def test_the_warning_is_sent_once_for_each_expiry_date(mailbox: Mailbox) -> None:
    person = _person("warnmaker")
    _instance(created_days_ago=28, days=30, CREATED_BY=person)  # expires in 2 days: inside the 3-day window
    assert expiry.warn_expiring() == [NAME] and len(mailbox.sent) == 1
    assert "expires in 2 days" in mailbox.sent[0]["subject"]
    assert expiry.warn_expiring() == [] and len(mailbox.sent) == 1  # not again
    store.update_env(NAME, DELETE_AFTER_DAYS=32)  # extended: a new date, outside the window
    assert expiry.warn_expiring() == []
    store.update_env(NAME, DELETE_AFTER_DAYS=31)  # a new date inside the window
    assert expiry.warn_expiring() == [NAME] and len(mailbox.sent) == 2


def test_nothing_is_warned_outside_the_window_or_for_production(mailbox: Mailbox) -> None:
    _person("farmaker")
    _instance(created_days_ago=10, days=30, CREATED_BY="farmaker")  # 20 days left
    assert expiry.warn_expiring() == []
    store.update_env(NAME, STAGE="production", DELETE_AFTER_DAYS=0)
    assert expiry.warn_expiring() == [] and mailbox.sent == []


def test_a_person_can_turn_the_e_mails_off_but_not_an_administrator_who_made_it(
    monkeypatch: pytest.MonkeyPatch, mailbox: Mailbox
) -> None:
    maker = _person("optmaker", expiry_email=False)
    boss = _person("optboss", "admin", expiry_email=False)
    other = _person("optother", "admin", expiry_email=False)
    kept = _person("optkept", "admin")
    admins = [(n, accounts.get(n) or {}) for n in (boss, other, kept)]
    monkeypatch.setattr(notices, "admins", lambda project: admins)
    monkeypatch.setattr("sudoo.projects.get", lambda project_id: {"id": project_id})
    _instance(created_days_ago=28, days=30, CREATED_BY=maker, PROJECT="p")
    assert sorted(user for user, _ in expiry.recipients(NAME)) == ["optkept"]
    store.update_env(NAME, CREATED_BY=boss)  # an administrator who made it cannot opt out
    assert sorted(user for user, _ in expiry.recipients(NAME)) == ["optboss", "optkept"]


def test_an_instance_made_by_an_api_token_tells_the_token_creator(mailbox: Mailbox) -> None:
    from sudoo import api_tokens

    person = _person("tokenowner")
    api_tokens.create("expiry-app", "instances", person)
    _instance(created_days_ago=28, days=30, CREATED_BY="api:expiry-app")
    assert [user for user, _ in expiry.recipients(NAME)] == [person]


def test_the_opt_out_is_kept_on_the_account(app: Flask) -> None:
    name = _person("optapi")
    client, headers = sign_in(app, name, "saffron-kettle-orbit-73")
    assert client.get("/api/v1/account").get_json()["data"]["expiry_email"] is None
    assert client.patch("/api/v1/account", json={"expiry_email": False}, headers=headers).status_code == 200
    assert client.get("/api/v1/account").get_json()["data"]["expiry_email"] is False
    assert client.patch("/api/v1/account", json={"expiry_email": "no"}, headers=headers).status_code == 400
    assert client.patch("/api/v1/account", json={"expiry_email": None}, headers=headers).status_code == 200


# ---------------------------------------------------------------- extensions


def _patch(client: FlaskClient, headers: dict[str, str], days: int) -> Any:
    return client.patch(f"/api/v1/instances/{NAME}", json={"delete_after_days": days}, headers=headers)


def test_people_without_the_permission_extend_a_little_and_once(app: Flask) -> None:
    name = _person("extlimited")
    client, headers = sign_in(app, name, "saffron-kettle-orbit-73")
    _instance(created_days_ago=10, days=30)
    assert _patch(client, headers, 61).status_code == 403  # 31 days more: over the 30
    assert _patch(client, headers, 0).status_code == 403  # keep is unlimited
    assert _patch(client, headers, 60).status_code == 200  # +30 from the current end
    assert store.read_env(NAME)["EXTENSIONS"] == "1"
    refused = _patch(client, headers, 61)  # a second extension
    assert refused.status_code == 403 and "extended 1 time already" in refused.get_json()["detail"]


def test_the_permission_removes_the_limits(app: Flask, admin: Client) -> None:
    client, headers = admin
    _instance(created_days_ago=10, days=30)
    assert _patch(client, headers, 400).status_code == 200
    assert _patch(client, headers, 900).status_code == 200
    assert _patch(client, headers, 0).status_code == 200


def test_an_instance_stopped_at_the_end_of_its_lifetime_is_extended_by_an_administrator_only(
    app: Flask, admin: Client, started: list[tuple[str, Any, tuple[Any, ...]]]
) -> None:
    name = _person("extstopped")
    small, small_headers = sign_in(app, name, "saffron-kettle-orbit-73")
    _instance(created_days_ago=31, days=30)
    STATE.update(NAME, switched_off=True, expired_at=int(time.time()) - DAY)
    assert _patch(small, small_headers, 40).status_code == 403
    assert STATE.get(NAME)["expired_at"]
    client, headers = admin
    answer = _patch(client, headers, 60)
    assert answer.status_code == 200 and "meta" not in answer.get_json()
    assert not STATE.get(NAME).get("expired_at") and started[-1][1] is operations.wake


def test_an_extended_instance_stays_stopped_when_the_server_cannot_run_one_more(
    app: Flask, admin: Client, started: list[tuple[str, Any, tuple[Any, ...]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capacity, "refusal", lambda reservation=None: "This server runs at most 2 instances at once.")
    _instance(created_days_ago=31, days=30)
    STATE.update(NAME, switched_off=True, expired_at=int(time.time()) - DAY)
    client, headers = admin
    answer = _patch(client, headers, 60)
    assert answer.status_code == 200
    assert "stays stopped" in answer.get_json()["meta"]["restart"] and started == []
    assert store.read_env(NAME)["DELETE_AFTER_DAYS"] == "60" and not STATE.get(NAME).get("expired_at")
    assert settings.expiry_extend_max_days == 30
