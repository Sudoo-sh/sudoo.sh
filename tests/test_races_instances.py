"""Instances under work at the same moment (RACE-P-005, 006, 007, 012, 017): an instance's .env is never read half
written and two changes to it both stay, the same GitHub push delivered twice updates once, the same action or
creation sent twice (two tabs, a double click) is one job, polling never fetches into a checkout a job is using,
and the kept state of an instance holds every change."""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from sudoo import triggers
from sudoo.config import Settings
from sudoo.core import jobs
from sudoo.core.state import COLLECTION as STATE_COLLECTION
from sudoo.core.state import STATE
from sudoo.instances import integrity, operations, store
from sudoo.storage import storage
from sudoo.triggers import polling
from sudoo.triggers.github_webhook import GitHubWebhook

Client = tuple[FlaskClient, dict[str, str]]
NAME = "race-shop"


def _instance(name: str = NAME, **env: str) -> None:
    store.folder(name).mkdir(parents=True, exist_ok=True)
    (store.folder(name) / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {"ODOO_VERSION": "18.0", "DB_NAME": name.replace("-", "_"), "ODOO_PORT": "18998", "STAGE": "development",
         "CREATED_BY": "admin1", "GIT_BRANCH": "main", "AUTO_UPDATE": "false", "AUTO_UPGRADE": "false",
         "AUTO_PAUSE": "false", "DELETE_AFTER_DAYS": "0", **env},
    )  # fmt: skip


@pytest.fixture(autouse=True)
def instance() -> Iterator[None]:
    _instance()
    yield
    shutil.rmtree(store.folder(NAME), ignore_errors=True)
    STATE.remove(NAME)
    integrity.forget(NAME)


def _together(count: int, fn: Any) -> list[Any]:
    """Run fn(n) for n in range(count) in threads that start at the same moment; their results, in order."""
    gate = threading.Barrier(count)
    results: list[Any] = [None] * count

    def run(n: int) -> None:
        gate.wait()
        results[n] = fn(n)

    threads = [threading.Thread(target=run, args=(n,)) for n in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results


# ---------------------------------------------------------------- RACE-P-005


def test_an_instances_settings_file_is_never_read_half_written() -> None:
    """Every request reads .env (ports, branch, stage); a write used to empty the file before filling it again, so a
    read at that moment saw an instance without ports or a database."""
    stop = threading.Event()
    torn: list[dict[str, str]] = []

    def write() -> None:
        n = 0
        while not stop.is_set():
            n += 1
            store.update_env(NAME, COUNTER=n)

    def read() -> None:
        while not stop.is_set():
            env = store.read_env(NAME)
            if "DB_NAME" not in env:
                torn.append(env)
                stop.set()

    threads = [threading.Thread(target=write), threading.Thread(target=read), threading.Thread(target=read)]
    for thread in threads:
        thread.start()
    stop.wait(3)
    stop.set()
    for thread in threads:
        thread.join(10)
    assert not torn, f"read a half-written .env: {torn[0]}"


def test_two_changes_to_an_instances_settings_at_once_both_stay() -> None:
    """A user's change (the lifetime) and a job's (the branch) read, changed and wrote .env at the same time: the last
    one wrote back what it had read, without the other's change."""
    for round_ in range(10):
        keys = [f"RACE_{round_}_{n}" for n in range(8)]
        _together(len(keys), lambda n, keys=keys: store.update_env(NAME, **{keys[n]: "1"}))
        env = store.read_env(NAME)
        missing = [key for key in keys if env.get(key) != "1"]
        assert not missing, f"round {round_}: lost {missing}"


# ---------------------------------------------------------------- RACE-P-017


def test_changes_to_an_instances_kept_state_at_once_all_reach_the_storage() -> None:
    for round_ in range(10):
        keys = [f"k{round_}_{n}" for n in range(8)]
        _together(len(keys), lambda n, keys=keys: STATE.update(NAME, **{keys[n]: n}))
        stored = storage().get(STATE_COLLECTION, NAME) or {}
        missing = [key for key in keys if key not in stored]
        assert not missing, f"round {round_}: the stored state lost {missing}"


def test_a_removed_instances_state_is_not_written_back_by_a_change_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """A change took its copy of the state, the instance was deleted (its state removed), then the change wrote its
    copy: the state of a deleted instance came back, and a new instance of that name inherited it. Each change is now
    one write onto the stored state; what is still kept in memory (visits) is never written for a removed state."""
    STATE.update(NAME, paused=True)
    STATE.record_visit(NAME)  # a visit recorded before the removal, written later
    done = threading.Thread(target=lambda: STATE.remove(NAME))
    done.start()
    done.join(10)
    STATE.save()
    assert storage().get(STATE_COLLECTION, NAME) is None
    assert STATE.get(NAME) == {}


# ---------------------------------------------------------------- RACE-P-006

SECRET = "y" * 32


@pytest.fixture
def webhook(monkeypatch: pytest.MonkeyPatch) -> tuple[FlaskClient, list[tuple[str, str]]]:
    trigger = GitHubWebhook(Settings(github_webhook_secret=SECRET))
    requested: list[tuple[str, str]] = []

    def update(name: str, reason: str) -> None:
        time.sleep(0.05)  # starting the update takes a moment: a second delivery arrives meanwhile
        requested.append((name, reason))

    trigger.start(update)
    monkeypatch.setattr(trigger, "instances_following", lambda repo, branch: [NAME] if branch == "main" else [])
    app = Flask(__name__)
    blueprint = trigger.blueprint()
    assert blueprint is not None
    app.register_blueprint(blueprint)
    return app.test_client(), requested


def _deliver(client: FlaskClient, payload: dict[str, Any], delivery: str | None) -> Any:
    body = json.dumps(payload).encode()
    headers = {
        "X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
        "X-GitHub-Event": "push",
        **({"X-GitHub-Delivery": delivery} if delivery else {}),
    }
    return client.post("/hooks/github", data=body, headers=headers, content_type="application/json")


def _push(after: str) -> dict[str, Any]:
    return {"ref": "refs/heads/main", "before": "a" * 40, "after": after, "repository": {"full_name": "acme/race"}}


def test_the_same_push_delivered_twice_at_once_updates_once(
    webhook: tuple[FlaskClient, list[tuple[str, str]]],
) -> None:
    client, requested = webhook
    after = hashlib.sha1(str(time.time_ns()).encode()).hexdigest()  # noqa: S324 - a made-up commit id
    delivery = f"d-{time.time_ns()}"
    answers = _together(3, lambda n: _deliver(webhook[0], _push(after), delivery).status_code)
    assert answers == [200, 200, 200]
    assert len(requested) == 1, requested
    _deliver(client, _push(after), f"other-{delivery}")  # the same push again (the App and a webhook both send it)
    assert len(requested) == 1, requested
    newer = hashlib.sha1(after.encode()).hexdigest()  # noqa: S324
    _deliver(client, _push(newer), f"new-{delivery}")  # a new push updates again
    assert len(requested) == 2


def test_a_delivery_that_failed_can_be_delivered_again(webhook: tuple[FlaskClient, list[tuple[str, str]]],
                                                        monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """GitHub sends a delivery again when it failed: a failure must not leave it counted as handled."""
    client, requested = webhook
    after = hashlib.sha1(str(time.time_ns()).encode()).hexdigest()  # noqa: S324
    delivery = f"f-{time.time_ns()}"
    original = GitHubWebhook.push

    def failing(self: GitHubWebhook, payload: dict[str, Any]) -> Any:
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(GitHubWebhook, "push", failing)
    assert _deliver(client, _push(after), delivery).status_code == 500
    monkeypatch.setattr(GitHubWebhook, "push", original)
    assert _deliver(client, _push(after), delivery).status_code == 200
    assert len(requested) == 1


def test_two_update_requests_at_once_for_an_idle_instance_start_one_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """The webhook and polling report the same new commit at the same moment: both found the instance not busy
    (its job registered but not yet running), so two updates queued."""
    store.update_env(NAME, AUTO_UPDATE="true")
    release = threading.Event()
    started: list[str] = []
    real_start = jobs.start

    def recording_start(instance: str, action: str, kind: str, fn: Any, *args: Any, **kwargs: Any) -> jobs.Job:
        time.sleep(0.05)  # registering the job takes a moment
        job = real_start(instance, action, kind, lambda job, *a: release.wait(20), *args, **kwargs)
        started.append(job.id)
        return job

    monkeypatch.setattr(jobs, "start", recording_start)
    monkeypatch.setattr(operations, "update_code", lambda job, name: release.wait(20))
    try:
        _together(3, lambda n: triggers.request_update(NAME, "new code on GitHub"))
        assert len(started) == 1, started
    finally:
        release.set()
        for _ in range(100):
            if not jobs.unfinished(NAME):
                break
            time.sleep(0.05)


# ---------------------------------------------------------------- RACE-P-007


def test_the_same_action_sent_twice_at_once_is_one_job(admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers = admin
    release = threading.Event()
    monkeypatch.setattr(operations, "backup", lambda job, name, kind, comment: release.wait(20))
    try:
        answers = _together(
            2,
            lambda n: client.post(f"/api/v1/instances/{NAME}/actions/backup", json={"comment": "before go-live"},
                                  headers=headers),  # fmt: skip
        )
        assert [a.status_code for a in answers] == [202, 202]
        ids = {a.get_json()["data"]["id"] for a in answers}
        assert len(ids) == 1, ids  # the second request is the first one's job
        other = client.post(f"/api/v1/instances/{NAME}/actions/backup", json={"comment": "another one"},
                            headers=headers)  # fmt: skip
        assert other.status_code == 202 and other.get_json()["data"]["id"] not in ids  # a different request: a job
    finally:
        release.set()
        for _ in range(100):
            if not jobs.unfinished(NAME):
                break
            time.sleep(0.05)


def test_the_same_creation_sent_twice_at_once_makes_one_instance(
    admin: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = admin
    release = threading.Event()
    monkeypatch.setattr(operations, "create", lambda job, plan: release.wait(20))
    body = {"version": "18.0", "edition": "community", "name": "race-twice", "delete_after_days": 3}
    try:
        answers = _together(2, lambda n: client.post("/api/v1/instances", json=body, headers=headers))
        assert [a.status_code for a in answers] == [202, 202], [a.get_json() for a in answers]
        assert len({a.get_json()["data"]["id"] for a in answers}) == 1
        assert sorted(name for name in jobs.being_created() if name.startswith("race-twice")) == ["race-twice"]
    finally:
        release.set()
        for _ in range(100):
            if not jobs.being_created():
                break
            time.sleep(0.05)


# ---------------------------------------------------------------- RACE-P-012


def test_polling_never_fetches_into_an_instance_a_job_is_working_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A poll ran `git fetch` in the checkout while an update job fetched and reset it: git's locks then failed the
    update (a failed build, an e-mail, a rollback)."""
    store.update_env(NAME, AUTO_UPDATE="true")
    fetched: list[str] = []
    monkeypatch.setattr(store, "names", lambda: [NAME])
    monkeypatch.setattr(polling.Polling, "has_new_commits", staticmethod(lambda name: fetched.append(name) or False))
    rounds = iter([None])

    def one_round(seconds: float) -> None:
        if next(rounds, "stop") == "stop":
            raise SystemExit  # ends the loop after one round

    monkeypatch.setattr(polling.time, "sleep", one_round)
    holding, release = threading.Event(), threading.Event()

    def job_running() -> None:
        with jobs.INSTANCE_LOCKS[NAME]:  # what a running job holds
            holding.set()
            release.wait(10)

    thread = threading.Thread(target=job_running)
    thread.start()
    holding.wait(5)
    try:
        with pytest.raises(SystemExit):
            polling.Polling(Settings())._run(lambda name, reason: None)
    finally:
        release.set()
        thread.join(10)
    assert fetched == []
