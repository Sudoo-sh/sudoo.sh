"""Each defect the independent review of the slot hold found, as a test that fails without its fix: a limit check
that cannot read the shared record refuses, a job that never got its thread gives its hold back, a forked child
starts with its own admission lock and no held jobs of its parent, a background caller survives a refused lock."""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Iterator

import pytest

from sudoo.config import live
from sudoo.core import jobs, slots
from sudoo.instances import capacity, metrics, store
from sudoo.limits import queue, quotas
from sudoo.storage import storage

MADE: list[str] = []


@pytest.fixture(autouse=True)
def clean_up() -> Iterator[None]:
    storage().delete(slots.COLLECTION, slots.KEY)
    yield
    storage().delete(slots.COLLECTION, slots.KEY)
    jobs._HELD.clear()
    for name in MADE:
        shutil.rmtree(store.folder(name), ignore_errors=True)
    MADE.clear()
    metrics.INSTANCES.clear()
    metrics.OBSERVED.clear()
    live.change({"max_running_instances": None, "slot_store_wait_seconds": None})


def make(name: str) -> None:
    MADE.append(name)
    store.folder(name).mkdir(parents=True, exist_ok=True)
    (store.folder(name) / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(name, {"ODOO_VERSION": "18.0", "DB_NAME": name.replace("-", "_"), "ODOO_PORT": "18999",
                           "STAGE": "development", "CREATED_BY": "admin1"})  # fmt: skip


def test_a_limit_check_that_cannot_read_the_record_refuses_instead_of_counting_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make("rv-a")
    live.change({"max_running_instances": 1})
    monkeypatch.setattr(slots, "snapshot", lambda: None)  # the record cannot be read; writes would still work
    ran: list[int] = []
    job, why = capacity.start_admitted("rv-a", lambda: jobs.start("rv-a", "t", "restart", lambda j: ran.append(1),
                                                                    holds_slot=True))  # fmt: skip
    assert job is None and "could not read" in (why or "")
    time.sleep(0.1)
    assert ran == [] and jobs._HELD == {}
    with pytest.raises(slots.Unavailable):
        capacity.refusal(stage="development")
    assert isinstance(capacity.counted_names(strict=False), set)  # display still shows what this process knows
    assert capacity.running_figures(strict=False)["total"] == 0


def test_a_start_whose_thread_never_began_gives_its_hold_back(monkeypatch: pytest.MonkeyPatch) -> None:
    make("rv-b")

    def no_thread(self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", no_thread)
    with pytest.raises(RuntimeError):
        jobs.start("rv-b", "t", "restart", lambda j: None, holds_slot=True)
    monkeypatch.undo()
    assert jobs._HELD == {} and slots.snapshot() is not None
    assert storage().get(slots.COLLECTION, slots.KEY)["held"] == {}
    assert "rv-b" not in capacity.counted_names()


def test_a_hold_that_cannot_be_removed_counts_as_ended_and_is_released_by_a_later_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make("rv-c")

    def no_thread(self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", no_thread)
    monkeypatch.setattr(slots, "drop", lambda ids: False)
    with pytest.raises(RuntimeError):
        jobs.start("rv-c", "t", "restart", lambda j: None, holds_slot=True)
    monkeypatch.undo()
    (held,) = jobs._HELD.values()
    assert held.finished_mono is not None and held.end_seq is not None
    assert "rv-c" in capacity.counted_names()  # kept, not leaked: nothing observed it since
    slots.publish_observed(slots.next_seq(), {"rv-c": False}, {"rv-c"})
    assert "rv-c" not in capacity.counted_names() and jobs._HELD == {}


def test_a_forked_child_gets_its_own_admission_lock_and_none_of_its_parents_jobs() -> None:
    make("rv-d")
    jobs._HELD["parent-job"] = jobs.Job("rv-d", "t", "restart", "system")
    live.change({"slot_store_wait_seconds": 1})
    with slots.ADMISSION:  # the parent holds the admission lock while it forks
        read, write = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read)
            try:
                with slots.ADMISSION:  # a child that shared the parent's lock would pass here
                    out = b"in"
            except slots.Busy:
                out = b"busy"
            except BaseException:  # noqa: BLE001
                out = b"other"
            os.write(write, out + (b" held" if jobs._HELD else b" none"))
            os._exit(0)
        os.close(write)
        seen = os.read(read, 64)
        os.waitpid(pid, 0)
    assert seen == b"busy none", seen  # waits for the real holder, and does not count the parent's jobs
    assert "parent-job" in jobs._HELD  # the parent's own list is untouched


def test_the_queue_dispatcher_survives_a_refused_lock_and_a_missing_record(monkeypatch: pytest.MonkeyPatch) -> None:
    def busy(self: object) -> None:
        raise slots.Busy("Another start is being checked.")

    monkeypatch.setattr(slots.AdmissionLock, "__enter__", busy)
    assert queue.dispatch() == []
    monkeypatch.undo()
    make("rv-e")
    queue.enqueue_for_test = None  # noqa: B010 - marker: the real queue API is exercised in test_capacity_queue
    monkeypatch.setattr(slots, "snapshot", lambda: None)
    live.change({"max_running_instances": 1})
    assert queue.dispatch() == []


def test_a_plugin_storage_without_a_bounded_change_refuses_every_start(monkeypatch: pytest.MonkeyPatch) -> None:
    make("rv-f")

    class Plain:
        def __getattr__(self, name: str) -> object:
            if name == "bounded_change":
                raise AttributeError(name)
            return getattr(storage(), name)

    monkeypatch.setattr(slots, "storage", lambda: Plain())
    with pytest.raises(slots.Unavailable, match="bounded"):
        jobs.start("rv-f", "t", "restart", lambda j: None, holds_slot=True)
    assert jobs._HELD == {}


def test_the_reclaim_of_a_hold_that_is_gone_ends_nothing() -> None:
    assert slots.record_reclaimed("no-such-job") is None
    assert quotas.LOCK is slots.ADMISSION
