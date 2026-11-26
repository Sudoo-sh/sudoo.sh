"""A job that stops or starts an instance holds its running slot while it runs and until the monitor has observed the
instance after the job ended (core/jobs.py starting(), metrics.OBSERVED), also when it failed and also for an
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
from sudoo.core import jobs, slots
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
    metrics.OBSERVED.clear()
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
        if job.finished_mono is not None and (job.end_seq is not None or not job.holds_slot):
            return
        time.sleep(0.01)
    raise AssertionError("the job did not end")


def observe(name: str, running: bool, at: int | None) -> None:
    metrics.INSTANCES[name] = {"running": running}
    if at is None:
        metrics.OBSERVED.pop(name, None)
    else:
        metrics.OBSERVED[name] = at


def test_a_running_instance_keeps_its_slot_while_a_job_takes_it_down_and_up() -> None:
    make("hold-a")
    gate = threading.Event()
    observe("hold-a", True, slots.next_seq())
    job = held_job("hold-a", lambda j: gate.wait(10))
    observe("hold-a", False, slots.next_seq())  # the monitor sees it down in the middle of the restart
    assert "hold-a" in jobs.starting() and capacity.running_figures("admin1")["own"] == 1
    gate.set()
    wait_ended(job)
    assert "hold-a" in jobs.starting()  # ended, but nothing has been observed since


def test_an_observation_that_started_before_the_job_ended_releases_nothing() -> None:
    make("hold-b")
    job = held_job("hold-b", lambda j: None)
    wait_ended(job)
    observe("hold-b", False, job.end_seq - 1)  # a slow collection: read before the end, stored after it
    assert "hold-b" in jobs.starting() and jobs.unsettled() == ["hold-b"]
    observe("hold-b", True, slots.next_seq())
    assert "hold-b" not in jobs.starting()
    assert jobs.unsettled() == []


def test_a_failed_job_is_held_until_the_instance_is_seen_too() -> None:
    make("hold-c")

    def fail(job: Job) -> None:
        raise RuntimeError("boom")

    job = held_job("hold-c", fail)
    wait_ended(job)
    assert job.status == "failed"
    observe("hold-c", True, job.end_seq - 1)
    assert "hold-c" in jobs.starting()  # it may have been left running: not assumed stopped
    observe("hold-c", True, slots.next_seq())
    assert "hold-c" not in jobs.starting()


def test_an_instance_the_engine_could_not_observe_stays_held() -> None:
    make("hold-d")
    job = held_job("hold-d", lambda j: None)
    wait_ended(job)
    observe("hold-d", False, None)  # unknown: stopped for lack of an answer
    assert "hold-d" in jobs.starting() and jobs.unsettled() == ["hold-d"]
    observe("hold-d", False, slots.next_seq())
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
    observe("hold-g", True, slots.next_seq())
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
    assert "runs at most 1 instance" in text and "1 instance still counts as running" in text
    assert "hold-h" not in text and "hold-i" not in text  # a count only: the names may be other people's
    observe("hold-h", False, slots.next_seq())
    assert "still count" not in (capacity.refusal(stage="development", user="admin1") or "")


def test_sample_keeps_the_observation_time_private_and_forgets_deleted_names() -> None:
    usage = {
        "obs-a": metrics.Usage(True, 0.0, 0, 0, None, None, 12.5),
        "obs-b": metrics.Usage(False, 0.0, 0, 0, None, None),
    }
    snapshot = metrics.sample({"memory_total_bytes": 1}, usage)
    assert metrics.OBSERVED == {"obs-a": 12.5}  # None: not observed this time
    assert "observed_at" not in metrics.INSTANCES["obs-a"] and "observed_at" not in str(snapshot)
    assert "observed_at" not in str(capacity.snapshot()) and "OBSERVED" not in str(capacity.snapshot())
    from sudoo.web.v1 import resources

    assert "observed_at" not in resources._usage("obs-a")
    metrics.sample({"memory_total_bytes": 1}, {"obs-b": usage["obs-b"]})  # obs-a deleted
    assert metrics.OBSERVED == {}


def test_deleting_and_recreating_a_name_overcounts_until_the_next_read_never_undercounts() -> None:
    make("hold-m")
    job = held_job("hold-m", lambda j: None)
    wait_ended(job)
    observe("hold-m", True, slots.next_seq())  # observed after the end: released
    assert "hold-m" not in jobs.starting()
    job2 = held_job("hold-m", lambda j: None)
    wait_ended(job2)
    shutil.rmtree(store.folder("hold-m"))
    make("hold-m")  # deleted and made again before anything asked: still the old job's hold
    observe("hold-m", False, job2.end_seq - 1)  # an old figure of the previous instance
    assert "hold-m" in jobs.starting()  # one slot too many, until a read that began after the end
    observe("hold-m", True, slots.next_seq())
    assert "hold-m" not in jobs.starting()


def test_a_delete_that_failed_after_the_folder_went_is_not_held() -> None:
    make("hold-n")
    job = held_job("hold-n", lambda j: None)
    wait_ended(job)
    shutil.rmtree(store.folder("hold-n"))  # what a half-finished delete leaves: no folder, maybe a container
    assert "hold-n" not in jobs.starting()  # released, the monitor's own running figure still counts a container


def test_two_overlapping_jobs_on_one_name_release_on_the_later_end() -> None:
    make("hold-o")
    gate = threading.Event()
    first = held_job("hold-o", lambda j: None)
    wait_ended(first)
    second = held_job("hold-o", lambda j: gate.wait(10))
    observe("hold-o", True, slots.next_seq())  # after the first ended, while the second still runs
    assert "hold-o" in jobs.starting()
    gate.set()
    wait_ended(second)
    assert "hold-o" in jobs.starting()  # the earlier observation is older than the second end
    observe("hold-o", True, slots.next_seq())
    assert "hold-o" not in jobs.starting()


def test_the_unsettled_figure_reads_the_instances_once_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("hold-p", "hold-q", "hold-r"):
        make(name)
        wait_ended(held_job(name, lambda j: None))
    calls = []
    real = store.names
    monkeypatch.setattr(store, "names", lambda: calls.append(1) or real())
    assert len(jobs.starting()) == 3 and len(calls) == 1


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
    observe("hold-j", True, slots.next_seq())
    assert client.post("/api/v1/instances/hold-j/actions/restart", headers=headers).status_code == 202
    assert started[-1] == {"instance": "hold-j", "holds_slot": True}  # also a running instance


def test_import_into_a_stopped_instance_respects_the_limit_and_holds_the_slot(
    admin: Client, started: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo.instances import imports

    client, headers = admin
    make("hold-k")
    make("hold-l")
    observe("hold-l", True, slots.next_seq())
    live.change({"max_running_non_production_per_user": 1})
    monkeypatch.setattr(imports, "get", lambda upload_id, name, user: {"id": upload_id, "file_name": "a.zip"})
    monkeypatch.setattr(imports, "check_file", lambda meta, version: None)
    answer = client.post("/api/v1/instances/hold-k/imports/u1/restore", json={}, headers=headers)
    assert answer.status_code == 403 and "Pause one of yours first" in answer.get_json()["detail"]
    assert started == []
    answer = client.post("/api/v1/instances/hold-l/imports/u1/restore", json={}, headers=headers)  # running: its slot
    assert answer.status_code == 202 and started == [{"instance": "hold-l", "holds_slot": True}]


def _collect_with_stubs(monkeypatch: pytest.MonkeyPatch, kinds: dict[str, str], answers: dict, state: str = "running"):
    """Run the real monitor.collect with every outside read replaced; return (times, what metrics.sample got)."""
    import json as _json

    from sudoo import engines
    from sudoo.instances import monitor

    times: dict[str, float] = {}
    got: dict[str, metrics.Usage] = {}

    def fake_capture(cmd, *a, **k):
        times["ps"] = slots.next_seq()
        rows = [{"Names": store.container(n, "odoo"), "ID": f"id-{n}", "State": state, "Status": "Up"} for n in kinds]
        return "\n".join(_json.dumps(r) for r in rows)

    def fake_elsewhere(names):
        times["elsewhere"] = slots.next_seq()
        return answers

    monkeypatch.setattr(monitor, "capture", fake_capture)
    monkeypatch.setattr(monitor, "_container_figures", lambda: {})
    monkeypatch.setattr(monitor, "_host_memory", lambda: (1000, 100))
    monkeypatch.setattr(monitor, "_host_cpu_percent", lambda: 0.0)
    monkeypatch.setattr(monitor, "_host_uptime", lambda: 1)
    monkeypatch.setattr(monitor, "_started_at", lambda unknown: {})
    monkeypatch.setattr(monitor, "_git_details", lambda *a, **k: ("", ""))
    monkeypatch.setattr(monitor, "disk_usage", lambda name: {})
    monkeypatch.setattr(monitor, "panel_footprint", lambda usage, cores: {})
    monkeypatch.setattr(monitor.odoo, "answers", lambda env: False)
    monkeypatch.setattr(monitor, "_observed_elsewhere", fake_elsewhere)
    monkeypatch.setattr(monitor.store, "names", lambda: list(kinds))
    monkeypatch.setattr(
        monitor.store, "read_env", lambda n: {} if kinds[n] == engines.DOCKER else {engines.ENV_KEY: kinds[n]}
    )
    monkeypatch.setattr(monitor.metrics, "sample", lambda host, usage, panel=None: got.update(usage))
    before = slots.next_seq()
    monitor.collect()
    times["before"], times["after"] = before, slots.next_seq()
    return times, got


def test_collect_stamps_the_read_time_of_the_state_it_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo import engines
    from sudoo.instances import monitor

    answer = engines.Observation(running=True, db_running=True, cpu=0.0, memory=0, limit=0, uptime="", started=None)
    times, got = _collect_with_stubs(
        monkeypatch,
        {"col-d": engines.DOCKER, "col-k": engines.KUBERNETES, "col-x": engines.KUBERNETES},
        {"col-k": answer},
    )
    # Docker: the number was taken before `docker ps` ran, so a state shown is from then or later
    assert times["before"] < got["col-d"].observed_seq < times["ps"] < times["after"]
    # another engine with an answer: the same number, taken before that read
    assert times["before"] < got["col-k"].observed_seq < times["elsewhere"] < times["after"]
    assert got["col-k"].running is True
    # another engine without an answer: no time (stays held), reported as stopped for lack of an answer
    assert got["col-x"].observed_seq is None and got["col-x"].running is False
    # the cached figures only give start times; `running` comes from the `docker ps` read of this pass
    assert monitor._STARTED is not None


def test_collect_that_fails_before_reading_leaves_the_observations_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.instances import monitor

    observe("col-f", True, 1.0)
    monkeypatch.setattr(monitor, "capture", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("docker down")))
    with pytest.raises(RuntimeError):
        monitor.collect()
    assert metrics.OBSERVED == {"col-f": 1.0}  # no read finished: nothing was stamped
