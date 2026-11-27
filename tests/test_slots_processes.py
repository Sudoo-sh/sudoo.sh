"""The running slots a job holds (core/slots.py) across processes and restarts, with real processes and the real
SQLite file: an owner that lives is never reclaimed, one that dies is (even when a child of it lives on), the limit
holds when two processes start at once, and a start that cannot be recorded is not started. Single host only: a
database shared across hosts is not covered and not tested."""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from sudoo.config import live, settings
from sudoo.core import jobs, slots
from sudoo.instances import capacity, metrics, store
from sudoo.limits import quotas
from sudoo.storage import storage

SRC = str(Path(slots.__file__).resolve().parents[2])
MADE: list[str] = []


@pytest.fixture(autouse=True)
def clean_up() -> Iterator[None]:
    storage().delete(slots.COLLECTION, slots.KEY)
    yield
    storage().delete(slots.COLLECTION, slots.KEY)
    jobs._HELD.clear()
    for name in MADE:
        import shutil

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


def child(code: str) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": SRC}
    return subprocess.Popen([sys.executable, "-u", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, env=env)  # fmt: skip


def line(proc: subprocess.Popen[str]) -> str:
    assert proc.stdout is not None
    return proc.stdout.readline().strip()


HOLDER = """
import subprocess, sys
from sudoo.core import slots
slots.record_hold("job-{tag}", "{instance}", "admin1")
{grandchild}
print(slots.owner(), flush=True)
sys.stdin.read()
"""


def test_a_live_owner_is_not_reclaimed_and_a_dead_one_is_after_a_later_read() -> None:
    make("sp-a")
    proc = child(HOLDER.format(tag="a", instance="sp-a", grandchild=""))
    try:
        owner = line(proc)
        assert owner and "sp-a" in jobs.starting()
        snap = slots.snapshot()
        assert snap is not None and snap.others["job-a"]["end_seq"] is None  # alive: still running
        assert snap.others["job-a"]["owner"] == owner
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait()
        snap = slots.snapshot()
        assert snap is not None
        ended = snap.others["job-a"]
        assert ended["owner"] == slots.RECLAIMED and ended["end_seq"] is not None
        assert not (Path(settings.manager_dir) / "owners" / f"{owner}.lock").exists()
        assert "sp-a" in jobs.starting() and jobs.unsettled() == ["sp-a"]  # no read since: still counted
        old = slots.next_seq()
        slots.publish_observed(old - 1, {"sp-a": False}, {"sp-a"})  # began before the end was taken? (older number)
        assert "sp-a" in jobs.starting()
        slots.publish_observed(slots.next_seq(), {"sp-a": False}, {"sp-a"})
        assert "sp-a" not in jobs.starting() and jobs.unsettled() == []
        assert "job-a" not in storage().get(slots.COLLECTION, slots.KEY)["held"]  # forgotten once released
    finally:
        proc.kill()


def test_a_child_of_a_dead_owner_does_not_keep_it_alive() -> None:
    make("sp-b")
    code = HOLDER.format(
        tag="b", instance="sp-b",
        grandchild="subprocess.Popen(['sleep', '30'])  # a command the job ran: must not inherit the owner's lock",
    )  # fmt: skip
    proc = child(code)
    try:
        owner = line(proc)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait()
        snap = slots.snapshot()
        assert snap is not None and snap.others["job-b"]["owner"] == slots.RECLAIMED, owner
    finally:
        proc.kill()
        subprocess.run(["pkill", "-f", "sleep 30"], check=False)


def test_a_missing_lock_file_proves_nothing_and_says_so() -> None:
    make("sp-c")
    slots._change(lambda doc: doc["held"].__setitem__(
        "job-c", {"instance": "sp-c", "owner": "ghost", "user": "x", "end_seq": None}))  # fmt: skip
    snap = slots.snapshot()
    assert snap is not None and "ghost" in snap.unknown_owners
    assert "sp-c" in jobs.starting() and jobs.unsettled() == ["sp-c"]  # held, and listed as waiting


def test_an_observation_cannot_replace_a_newer_one() -> None:
    slots.publish_observed(10, {"sp-d": True}, {"sp-d"})
    slots.publish_observed(5, {"sp-d": False}, {"sp-d"})  # a slow pass stored late
    seen = storage().get(slots.COLLECTION, slots.KEY)["observed"]["sp-d"]
    assert seen == {"seq": 10, "running": True}
    slots.publish_observed(11, {}, set())  # the instance is gone
    assert "sp-d" not in storage().get(slots.COLLECTION, slots.KEY)["observed"]


def test_another_process_monitor_observation_counts_as_running_and_releases() -> None:
    make("sp-e")
    slots.publish_observed(slots.next_seq(), {"sp-e": True}, {"sp-e"})  # what a monitor elsewhere published
    assert "sp-e" in capacity.counted_names()
    slots.publish_observed(slots.next_seq(), {"sp-e": False}, {"sp-e"})
    assert "sp-e" not in capacity.counted_names()


RACE = """
import sys, time
from sudoo.core import jobs
from sudoo.instances import capacity

def wait(job):
    time.sleep(60)

for name in sys.argv[1:]:
    def go():
        time.sleep(0.05)  # between the check and the hold: without the shared lock both processes pass the check
        return jobs.start(name, "t", "restart", wait, holds_slot=True)

    job, why = capacity.start_admitted(name, go)
    print("started" if job is not None else "refused", name, flush=True)
print("done", flush=True)
sys.stdin.read()
"""


def test_two_processes_starting_at_once_never_pass_the_limit() -> None:
    names = [f"race-{i}" for i in range(12)]
    for name in names:
        make(name)
    live.change({"max_running_instances": 3})
    env = {**os.environ, "PYTHONPATH": SRC}
    procs = [
        subprocess.Popen([sys.executable, "-u", "-c", RACE, *names[k::2]], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True, env=env)  # fmt: skip
        for k in (0, 1)
    ]
    try:
        results = []
        for proc in procs:
            while (got := line(proc)) != "done":
                results.append(got.split()[0])
                assert got, "a child ended early"
        assert results.count("started") == 3 and results.count("refused") == 9
        held = storage().get(slots.COLLECTION, slots.KEY)["held"]
        assert len(held) == 3 and all(h["end_seq"] is None for h in held.values())
        assert len(jobs.starting()) == 3
    finally:
        for proc in procs:
            proc.kill()


def test_a_start_that_cannot_be_recorded_is_not_started(monkeypatch: pytest.MonkeyPatch) -> None:
    make("sp-f")
    ran: list[int] = []

    def refuse(fn: object) -> None:
        raise slots.Unavailable("storage down")

    monkeypatch.setattr(slots, "_change", refuse)
    with pytest.raises(slots.Unavailable):
        jobs.start("sp-f", "t", "restart", lambda j: ran.append(1), holds_slot=True)
    time.sleep(0.1)
    assert ran == [] and jobs._HELD == {} and not any(j.instance == "sp-f" for j in jobs.JOBS.values())


def test_an_end_that_cannot_be_recorded_keeps_the_hold_and_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    make("sp-g")
    real = slots.record_end
    monkeypatch.setattr(slots, "record_end", lambda job_id: (_ for _ in ()).throw(slots.Unavailable("busy")))
    job = jobs.start("sp-g", "t", "restart", lambda j: None, holds_slot=True)
    for _ in range(400):
        if job.finished_mono is not None:
            break
        time.sleep(0.01)
    slots.publish_observed(
        slots.next_seq(), {"sp-g": False}, {"sp-g"}
    )  # a read after the end ... of which no end exists
    assert job.end_seq is None and "sp-g" in jobs.starting()  # never freed for want of a record
    assert storage().get(slots.COLLECTION, slots.KEY)["held"][job.id]["end_seq"] is None  # others see it held
    monkeypatch.setattr(slots, "record_end", real)
    assert "sp-g" in jobs.starting()  # the retry records the end now ...
    assert job.end_seq is not None
    slots.publish_observed(slots.next_seq(), {"sp-g": False}, {"sp-g"})  # ... and a read after it releases
    assert "sp-g" not in jobs.starting()


def test_a_busy_database_refuses_within_the_wait_not_after_ten_seconds() -> None:
    make("sp-h")
    live.change({"slot_store_wait_seconds": 1})
    blocker = sqlite3.connect(storage().path, isolation_level=None)  # type: ignore[attr-defined]
    blocker.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        with pytest.raises(slots.Unavailable):
            slots.record_hold("job-h", "sp-h", "admin1")
        assert time.monotonic() - started < 4
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


def test_the_admission_lock_is_shared_with_other_processes_and_bounded() -> None:
    live.change({"slot_store_wait_seconds": 1})
    holder = child(
        "import sys\nfrom sudoo.core import slots\nwith slots.ADMISSION:\n    print('in', flush=True)\n    sys.stdin.read()\n"
    )
    try:
        assert line(holder) == "in"
        started = time.monotonic()
        with pytest.raises(slots.Busy), quotas.LOCK:
            pass
        assert time.monotonic() - started < 4
    finally:
        holder.kill()
    with quotas.LOCK, quotas.LOCK:  # free again, and re-entrant
        pass


def test_a_forked_child_gets_its_own_owner() -> None:
    mine = slots.owner()
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:  # the child: must not share the parent's owner or lock
        os.close(read)
        os.write(write, slots.owner().encode())
        os._exit(0)
    os.close(write)
    theirs = os.read(read, 64).decode()
    os.waitpid(pid, 0)
    assert theirs and theirs != mine and slots.owner() == mine


def test_the_threads_of_one_process_share_one_owner() -> None:
    found: list[str] = []
    threads = [threading.Thread(target=lambda: found.append(slots.owner())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(found)) == 1
