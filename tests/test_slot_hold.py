"""A job that stops or starts an instance holds its running slot while it runs and until the monitor has observed the
instance after the job ended (core/jobs.py starting(), metrics observed_at), also when it failed and also for an
instance that ran when the job was asked for. Unknown (an engine that did not answer) is held, not stopped."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

from sudoo.config import live
from sudoo.core import jobs
from sudoo.core.jobs import Job
from sudoo.instances import capacity, metrics, store

Client = tuple[FlaskClient, dict[str, str]]
MADE: list[str] = []


@pytest.fixture(autouse=True)
def clean_up() -> Iterator[None]:
    yield
    jobs._HELD.clear()
    for name in MADE:
        shutil.rmtree(store.folder(name), ignore_errors=True)
    MADE.clear()
    metrics.INSTANCES.clear()
    live.change({"max_running_non_production_per_user": None, "max_running_instances": None})


def make(name: str, stage: str = "development", owner: str = "admin1") -> None:
    MADE.append(name)
    store.folder(name).mkdir(parents=True, exist_ok=True)
    (store.folder(name) / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(name, {"ODOO_VERSION": "18.0", "DB_NAME": name.replace("-", "_"), "ODOO_PORT": "18999",
                           "STAGE": stage, "CREATED_BY": owner})  # fmt: skip


def held_job(name: str, fn: Any, kind: str = "restart") -> Job:
    job = jobs.start(name, "Test", kind, fn, holds_slot=True)
    return job


def wait_ended(job: Job) -> None:
    for _ in range(400):
        if job.finished_mono is not None:
            return
        time.sleep(0.01)
    raise AssertionError("the job did not end")


def observe(name: str, running: bool, at: float | None) -> None:
    metrics.INSTANCES[name] = {"running": running, "observed_at": at}


def test_a_running_instance_keeps_its_slot_while_a_job_takes_it_down_and_up() -> None:
    make("hold-a")
    gate = threading.Event()
    observe("hold-a", True, time.monotonic())
    job = held_job("hold-a", lambda j: gate.wait(10))
    observe("hold-a", False, time.monotonic())  # the monitor sees it down in the middle of the restart
    assert "hold-a" in jobs.starting() and capacity.running_figures("admin1")["own"] == 1
    gate.set()
    wait_ended(job)
    assert "hold-a" in jobs.starting()  # ended, but nothing has been observed since


def test_an_observation_that_started_before_the_job_ended_releases_nothing() -> None:
    make("hold-b")
    job = held_job("hold-b", lambda j: None)
    wait_ended(job)
    observe("hold-b", False, job.finished_mono - 0.5)  # a slow collection: read before the end, stored after it
    assert "hold-b" in jobs.starting() and jobs.unsettled() == ["hold-b"]
    observe("hold-b", True, job.finished_mono + 0.01)
    assert "hold-b" not in jobs.starting()
    assert jobs.unsettled() == []


def test_a_failed_job_is_held_until_the_instance_is_seen_too() -> None:
    make("hold-c")

    def fail(job: Job) -> None:
        raise RuntimeError("boom")

    job = held_job("hold-c", fail)
    wait_ended(job)
    assert job.status == "failed"
    observe("hold-c", True, job.finished_mono - 1)
    assert "hold-c" in jobs.starting()  # it may have been left running: not assumed stopped
    observe("hold-c", True, job.finished_mono + 1)
    assert "hold-c" not in jobs.starting()


def test_an_instance_the_engine_could_not_observe_stays_held() -> None:
    make("hold-d")
    job = held_job("hold-d", lambda j: None)
    wait_ended(job)
    observe("hold-d", False, None)  # unknown: stopped for lack of an answer
    assert "hold-d" in jobs.starting() and jobs.unsettled() == ["hold-d"]
    observe("hold-d", False, job.finished_mono + 1)
    assert "hold-d" not in jobs.starting()


def test_no_observation_yet_and_a_deleted_instance() -> None:
    make("hold-e")
    job = held_job("hold-e", lambda j: None)
    wait_ended(job)
    assert "hold-e" in jobs.starting()  # in the store, not in the monitor's figures yet
    shutil.rmtree(store.folder("hold-e"))
    assert "hold-e" not in jobs.starting()  # deleted: nothing to hold


def test_a_job_without_the_flag_holds_nothing_and_creations_and_wakes_do() -> None:
    make("hold-f")
    plain = jobs.start("hold-f", "Test", "update", lambda j: None)
    wait_ended(plain)
    assert "hold-f" not in jobs.starting() and plain.holds_slot is False
    wake = jobs.start("hold-f", "Test", "wake", lambda j: None)
    wait_ended(wake)
    assert wake.holds_slot is True and "hold-f" in jobs.starting()


def test_the_set_counts_an_instance_once_whatever_holds_it() -> None:
    make("hold-g")
    gate = threading.Event()
    observe("hold-g", True, time.monotonic())
    first = held_job("hold-g", lambda j: gate.wait(10))
    assert capacity.running_figures("admin1")["total"] == 1  # running and held: one slot
    gate.set()
    wait_ended(first)
    assert capacity.running_figures("admin1")["total"] == 1


def test_a_refusal_says_which_instances_wait_for_a_measurement() -> None:
    make("hold-h")
    make("hold-i")
    live.change({"max_running_instances": 1})
    job = held_job("hold-h", lambda j: None)
    wait_ended(job)
    text = capacity.refusal(stage="development", user="admin1") or ""
    assert "runs at most 1 instance" in text and "has not been measured since its job ended" in text
    assert "hold-h" in text
    observe("hold-h", False, job.finished_mono + 1)
    assert "has not been measured" not in (capacity.refusal(stage="development", user="admin1") or "")


def test_sample_stores_the_observation_time_or_none() -> None:
    usage = {
        "obs-a": metrics.Usage(True, 0.0, 0, 0, None, None, 12.5),
        "obs-b": metrics.Usage(False, 0.0, 0, 0, None, None),
    }
    metrics.sample({"memory_total_bytes": 1}, usage)
    assert metrics.INSTANCES["obs-a"]["observed_at"] == 12.5 and metrics.INSTANCES["obs-b"]["observed_at"] is None


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **kw: Any) -> Job:
        calls.append({"instance": instance, "holds_slot": kw.get("holds_slot")})
        return Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    return calls


def test_applying_resources_and_the_actions_pass_the_flag(admin: Client, started: list[dict[str, Any]]) -> None:
    client, headers = admin
    make("hold-j")
    observe("hold-j", True, time.monotonic())
    assert client.post("/api/v1/instances/hold-j/actions/restart", headers=headers).status_code == 202
    assert started[-1] == {"instance": "hold-j", "holds_slot": True}  # also a running instance


def test_import_into_a_stopped_instance_respects_the_limit_and_holds_the_slot(
    admin: Client, started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo.instances import imports

    client, headers = admin
    make("hold-k")
    make("hold-l")
    observe("hold-l", True, time.monotonic())
    live.change({"max_running_non_production_per_user": 1})
    monkeypatch.setattr(imports, "get", lambda upload_id, name, user: {"id": upload_id, "file_name": "a.zip"})
    monkeypatch.setattr(imports, "check_file", lambda meta, version: None)
    answer = client.post("/api/v1/instances/hold-k/imports/u1/restore", json={}, headers=headers)
    assert answer.status_code == 403 and "Pause one of yours first" in answer.get_json()["detail"]
    assert started == []
    answer = client.post("/api/v1/instances/hold-l/imports/u1/restore", json={}, headers=headers)  # running: its slot
    assert answer.status_code == 202 and started == [{"instance": "hold-l", "holds_slot": True}]
