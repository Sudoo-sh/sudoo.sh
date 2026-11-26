"""The most non-production instances one person runs at once (MAX_RUNNING_NON_PRODUCTION_PER_USER, or the number
set on their account): off by default, never for production, enforced in the API."""

from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest
from flask.testing import FlaskClient

from sudoo import accounts, engines
from sudoo.config import live
from sudoo.core import jobs
from sudoo.core.jobs import Job
from sudoo.instances import backups, capacity, metrics, store

Client = tuple[FlaskClient, dict[str, str]]


MADE: list[str] = []


@pytest.fixture(autouse=True)
def clean_up() -> Iterator[None]:
    """Whatever a test made (instances, an account's number, settings) is gone before the next test file runs."""
    yield
    for name in MADE:
        shutil.rmtree(store.folder(name), ignore_errors=True)
    MADE.clear()
    metrics.INSTANCES.clear()
    live.change({"max_running_non_production_per_user": None, "max_running_instances": None})
    for user in ("admin1", "sync1"):
        if (accounts.get(user) or {}).get("running_limit") is not None:
            accounts.update(user, running_limit=None)


def write_env(name: str, env: dict[str, str]) -> None:
    MADE.append(name)
    store.folder(name).mkdir(parents=True, exist_ok=True)
    (store.folder(name) / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(name, {"ODOO_VERSION": "18.0", "DB_NAME": name.replace("-", "_"), "ODOO_PORT": "18999", **env})


@pytest.fixture
def running() -> Iterator[None]:
    """admin1 created dev-a (development) and test-b (testing), sync1 created dev-c and admin1 also created the
    production instance prod-d: all four run."""
    for name, stage, owner in (
        ("dev-a", "development", "admin1"),
        ("test-b", "testing", "admin1"),
        ("dev-c", "development", "sync1"),
        ("prod-d", "production", "admin1"),
    ):
        write_env(name, {"STAGE": stage, "CREATED_BY": owner})
        metrics.INSTANCES[name] = {"running": True}
    yield
    metrics.INSTANCES.clear()
    live.change({"max_running_non_production_per_user": None, "max_running_instances": None})


def test_off_by_default_nobody_is_refused(running: None) -> None:
    assert capacity.refusal(stage="development", user="admin1") is None
    assert capacity.user_limit("admin1") == 0


def test_the_limit_counts_the_persons_own_non_production_instances(running: None) -> None:
    live.change({"max_running_non_production_per_user": 2})
    assert capacity.running_figures("admin1") == {
        "total": 4,
        "production": 1,
        "own": 2,
    }  # prod-d is not theirs to count
    assert "2 of your 2 running instances" in (capacity.refusal(stage="development", user="admin1") or "")
    assert capacity.refusal(stage="development", user="sync1") is None  # one running: room for another
    assert capacity.refusal(stage="production", user="admin1") is None  # production is never refused


def test_production_is_never_counted_for_the_person(running: None) -> None:
    live.change({"max_running_non_production_per_user": 3})
    assert capacity.refusal(stage="development", user="admin1") is None  # dev-a and test-b: 2 of 3, prod-d not counted


def test_the_panel_wide_limit_counts_production_but_never_refuses_it(running: None) -> None:
    live.change({"max_running_instances": 4})
    assert "at most 4 instances at once" in (capacity.refusal(stage="development", user="sync1") or "")
    assert capacity.refusal(stage="production", user="sync1") is None


def test_the_account_number_beats_the_setting(admin: Client, running: None) -> None:
    client, headers = admin
    live.change({"max_running_non_production_per_user": 1})
    assert capacity.refusal(stage="development", user="sync1") is not None
    assert client.patch("/api/v1/users/sync1", json={"running_limit": 5}, headers=headers).status_code == 200
    assert capacity.refusal(stage="development", user="sync1") is None
    assert client.patch("/api/v1/users/sync1", json={"running_limit": 1}, headers=headers).status_code == 200
    assert capacity.refusal(stage="development", user="sync1") is not None
    assert client.patch("/api/v1/users/sync1", json={"running_limit": 0}, headers=headers).status_code == 200
    assert capacity.refusal(stage="development", user="sync1") is None  # 0: no limit for this person
    answer = client.patch("/api/v1/users/sync1", json={"running_limit": None}, headers=headers)
    assert answer.status_code == 200 and answer.get_json()["data"]["running_limit"] is None
    assert answer.get_json()["data"]["running_limit_default"] == 1
    assert capacity.refusal(stage="development", user="sync1") is not None  # the setting applies again


def test_the_account_number_is_checked(admin: Client) -> None:
    client, headers = admin
    for bad in (-1, 1001, "3", True, 1.5):
        answer = client.patch("/api/v1/users/sync1", json={"running_limit": bad}, headers=headers)
        assert answer.status_code == 400, bad


def test_creating_is_refused_in_the_api_with_403(admin: Client, running: None) -> None:
    client, headers = admin
    live.change({"max_running_non_production_per_user": 2})
    answer = client.post(
        "/api/v1/instances", json={"version": "18.0", "edition": "community", "name": "more"}, headers=headers
    )
    assert answer.status_code == 403 and "Pause one of yours first" in answer.get_json()["detail"]


def test_starting_your_own_stopped_instance_is_refused_but_not_production(admin: Client, running: None) -> None:
    client, headers = admin
    live.change({"max_running_non_production_per_user": 2})
    write_env("dev-e", {"STAGE": "development", "CREATED_BY": "admin1"})
    answer = client.post("/api/v1/instances/dev-e/actions/start", headers=headers)
    assert answer.status_code == 403 and "Pause one of yours first" in answer.get_json()["detail"]
    write_env("prod-f", {"STAGE": "production", "CREATED_BY": "admin1"})
    assert client.post("/api/v1/instances/prod-f/actions/start", headers=headers).status_code != 403


def test_the_quota_answer_has_the_running_figures(admin: Client, running: None) -> None:
    client, _ = admin
    live.change({"max_running_non_production_per_user": 2, "max_running_instances": 9})
    body = client.get("/api/v1/instance-quota").get_json()["data"]
    assert (body["running"], body["running_production"], body["running_own"]) == (4, 1, 2)
    assert (body["max_running"], body["max_running_own"]) == (9, 2)
    live.change({"max_running_non_production_per_user": None, "max_running_instances": None})
    body = client.get("/api/v1/instance-quota").get_json()["data"]
    assert body["max_running"] is None and body["max_running_own"] is None


def test_simultaneous_starts_cannot_pass_the_limit_together(admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """The check and the registration of the start's job happen together (quotas.LOCK): of six starts at once with
    room for two, exactly two are accepted."""
    import threading

    from sudoo.core import jobs
    from sudoo.instances import operations

    client, headers = admin
    release = threading.Event()
    monkeypatch.setattr(operations, "wake", lambda job, name: release.wait(10))
    live.change({"max_running_non_production_per_user": 2})
    names = [f"dev-r{i}" for i in range(6)]
    for name in names:
        write_env(name, {"STAGE": "development", "CREATED_BY": "admin1"})
    codes: list[int] = []
    gate = threading.Barrier(len(names))

    def start(name: str) -> None:
        gate.wait()
        codes.append(client.post(f"/api/v1/instances/{name}/actions/start", headers=headers).status_code)

    try:
        threads = [threading.Thread(target=start, args=(n,)) for n in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        assert sorted(codes) == [202, 202, 403, 403, 403, 403]
        assert len(jobs.starting()) == 2
    finally:
        release.set()
        live.change({"max_running_non_production_per_user": None})


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """No job really runs: the names of the instances a request would have started."""
    names: list[str] = []

    def fake_start(instance: str, action: str, kind: str, fn: object, *args: object, **_: object) -> Job:
        names.append(instance)
        return Job(instance, action, kind, "system")

    monkeypatch.setattr(jobs, "start", fake_start)
    monkeypatch.setattr(backups, "latest", lambda name: {"db": "x"})  # something to restore
    monkeypatch.setattr(backups, "rollback_snapshot", lambda name: {"db": "x"})  # something to roll back to
    monkeypatch.setattr(engines, "offered", lambda: [engines.DOCKER, engines.KUBERNETES])
    return names


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("restart", {}),
        ("upgrade", {"modules": "base"}),
        ("restore", {}),
        ("rollback", {}),
        ("change-engine", {"engine": "kubernetes"}),
    ],
    ids=lambda value: str(value),
)
def test_every_action_that_starts_a_stopped_instance_respects_the_running_limit(
    admin: Client, running: None, started: list[str], action: str, body: dict[str, str]
) -> None:
    client, headers = admin
    live.change({"max_running_non_production_per_user": 2})
    write_env("dev-e", {"STAGE": "development", "CREATED_BY": "admin1"})
    answer = client.post(f"/api/v1/instances/dev-e/actions/{action}", json=body, headers=headers)
    assert answer.status_code == 403 and "Pause one of yours first" in answer.get_json()["detail"]
    assert started == []


def test_restarting_a_running_instance_or_a_stopped_production_one_is_not_refused(
    admin: Client, running: None, started: list[str]
) -> None:
    client, headers = admin
    live.change({"max_running_non_production_per_user": 2})
    assert client.post("/api/v1/instances/dev-a/actions/restart", headers=headers).status_code == 202
    write_env("prod-f", {"STAGE": "production", "CREATED_BY": "admin1"})
    answer = client.post("/api/v1/instances/prod-f/actions/restart", json={"confirm_production": True}, headers=headers)
    assert answer.status_code != 403 and started == ["dev-a", "prod-f"]
