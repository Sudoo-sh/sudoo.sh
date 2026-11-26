"""The running limits are judged inside the write that records the hold, on the shared record alone; a refused start of
the capacity queue keeps its place; a failed publication refuses new non-production starts."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

import docrun
from sudoo.core import slots
from sudoo.instances import capacity, metrics, store
from sudoo.limits import queue
from sudoo.storage import storage


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    docrun.reset()
    metrics.INSTANCES.clear()
    slots.PUBLISH.failed = False
    slots._ARMED.value = None
    yield
    docrun.reset()
    metrics.INSTANCES.clear()
    slots.PUBLISH.failed = False
    slots._ARMED.value = None


def admit(**over):  # type: ignore[no-untyped-def]
    base = {
        "existing": frozenset({"a", "b", "c"}),
        "production": frozenset(),
        "owned": frozenset({"a", "b", "c"}),
        "user": "u",
        "exempt": False,
        "max_running": 2,
        "user_limit": 0,
        "own_voice": True,
        "fresh": True,
    }
    base.update(over)
    return slots.Admit(**base)


def test_decide_is_pure_and_production_is_exempt_from_the_limits_only() -> None:
    names = {"a", "b"}
    assert slots.decide(names, admit(), "c") == slots.Decision("total", 2, 2)
    assert slots.decide(names, admit(), "a") is None  # already counted: it holds its slot
    assert slots.decide(names, admit(exempt=True), "c") is None
    assert slots.decide(names, admit(exempt=True, fresh=False), "c") is None  # production is not refused as stale
    assert slots.decide(names, admit(fresh=False, max_running=0), "c") == slots.Decision("stale", 0, 0)
    assert slots.decide({"a"}, admit(max_running=0, user_limit=1), "c") == slots.Decision("own", 1, 1)


def test_the_limit_is_judged_inside_the_write_against_the_document() -> None:
    """The pre-check passed with room; another process fills the record before this start writes: refused, no hold."""
    docrun.run("a")
    a = admit(max_running=2)
    with slots.ADMISSION:
        slots.arm(a, None)
        docrun.run("b")  # published by someone else after the check, before the hold
        with pytest.raises(slots.Refused):
            slots.record_hold("j1", "c", "u")
    assert slots.snapshot().held == {}


def test_a_start_with_room_records_its_hold_and_two_racing_starts_leave_one() -> None:
    docrun.run("a")
    results: list[str] = []
    barrier = threading.Barrier(2)

    def go(job: str, name: str) -> None:
        barrier.wait()
        with slots.ADMISSION:  # each passed a pre-check with the room it saw; the write judges the current record
            slots.arm(admit(max_running=2), None)
            try:
                slots.record_hold(job, name, "u")
                results.append("held")
            except slots.Refused:
                results.append("refused")

    threads = [threading.Thread(target=go, args=(f"j{i}", n)) for i, n in enumerate("bc")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["held", "refused"]
    assert len(slots.snapshot().held) == 1


def test_the_strict_count_is_the_record_alone() -> None:
    metrics.INSTANCES["a"] = {"running": True}  # this process's view, never published
    assert capacity.counted_names(strict=False) >= set() and "a" not in slots.counted_in(
        slots.snapshot().held, slots.snapshot().observed, {"a"}
    )


def test_a_failed_publication_refuses_new_non_production_starts_until_one_works(monkeypatch) -> None:
    def broken(fn):  # type: ignore[no-untyped-def]
        raise slots.Unavailable("down")

    seq = slots.next_seq() or 1
    with monkeypatch.context() as m:
        m.setattr(slots, "_change", broken)
        slots.publish_observed(seq, {"a": True}, {"a"})
    assert slots.PUBLISH.failed
    assert not capacity.preload("development", "u", True).fresh
    assert capacity.preload("production", "u", True).exempt  # production is not asked
    text = capacity.refusal(stage="development", user=None, counted=set())
    assert text and "no fresh view" in text
    slots.publish_observed(slots.next_seq() or 1, {"a": True}, {"a"})
    assert not slots.PUBLISH.failed and capacity.preload("development", "u", True).fresh


def test_the_refusal_check_and_the_write_use_the_same_evaluator() -> None:
    """Parity: whatever refusal() answers for a count, decide() answers the same for the same preloaded inputs."""
    for running, limit in ((0, 2), (1, 2), (2, 2), (3, 2)):
        names = {f"n{i}" for i in range(running)}
        a = admit(max_running=limit, existing=frozenset(names | {"x"}), owned=frozenset())
        text = capacity.refusal(counted=names)
        if limit and running >= limit:
            assert slots.decide(names, a, None) is not None
        else:
            assert slots.decide(names, a, None) is None
        assert text is None or "at most" in text


def make(name: str) -> None:
    store.folder(name).mkdir(parents=True, exist_ok=True)


def item(i: str, seq: int, instance: str) -> dict:
    return {"id": i, "seq": seq, "kind": "start", "instance": instance, "user": "u", "stage": "development"}


def test_restore_puts_the_item_back_in_its_place_once_and_overwrites_nothing() -> None:
    first, second = item("q1", 1, "a"), item("q2", 2, "b")
    storage().put(queue.COLLECTION, "q2", second)
    queue._restore(first)
    queue._restore(first)  # idempotent
    assert [i["id"] for i in queue.items()] == ["q1", "q2"]
    asked_again = item("q3", 3, "c")
    storage().put(queue.COLLECTION, "q3", asked_again)
    queue._restore(item("q4", 0, "c"))  # the instance waits again under another item: not overwritten, not doubled
    assert [i["id"] for i in queue.items()] == ["q1", "q2", "q3"]
    for key in list(storage().all(queue.COLLECTION)):
        storage().delete(queue.COLLECTION, key)


@pytest.fixture
def line(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(capacity, "start_refusal", lambda name: None)
    monkeypatch.setattr(queue, "_refused", lambda it: None)
    for n in ("qa", "qb"):
        make(n)
    storage().put(queue.COLLECTION, "q1", item("q1", 1, "qa"))
    storage().put(queue.COLLECTION, "q2", item("q2", 2, "qb"))
    yield
    for key in list(storage().all(queue.COLLECTION)):
        storage().delete(queue.COLLECTION, key)
    import shutil

    for n in ("qa", "qb"):
        shutil.rmtree(store.folder(n), ignore_errors=True)


def test_a_start_refused_in_its_write_keeps_its_place_in_the_line(line, monkeypatch) -> None:
    calls: list[str] = []

    def start(name: str, user: str) -> None:
        calls.append(name)
        if name == "qa":
            raise slots.Refused(slots.Decision("total", 1, 1), admit())

    monkeypatch.setattr(queue, "_start", start)
    assert queue.dispatch() == ["qb"] and calls == ["qa", "qb"]
    assert [i["id"] for i in queue.items()] == ["q1"]  # not lost: the same item, same sequence number
    assert queue.items()[0]["seq"] == 1


def test_an_unrecordable_hold_stops_the_round_and_keeps_every_item(line, monkeypatch) -> None:
    def start(name: str, user: str) -> None:
        raise slots.Unavailable("record down")

    monkeypatch.setattr(queue, "_start", start)
    assert queue.dispatch() == []
    assert [i["id"] for i in queue.items()] == ["q1", "q2"]


def test_a_failure_that_may_have_started_it_is_not_put_back(line, monkeypatch) -> None:
    def start(name: str, user: str) -> None:
        raise RuntimeError("after the hold")

    monkeypatch.setattr(queue, "_start", start)
    queue.dispatch()
    assert queue.items() == []  # ambiguous outcome: not requeued blindly (audited as failed)


def test_two_dispatchers_one_refused_start_leave_one_item_not_two(line, monkeypatch) -> None:
    def start(name: str, user: str) -> None:
        if name == "qa":
            raise slots.Refused(slots.Decision("total", 1, 1), admit())

    monkeypatch.setattr(queue, "_start", start)
    out: list[list[str]] = []
    threads = [threading.Thread(target=lambda: out.append(queue.dispatch())) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(sum(out, [])) == ["qb"]
    assert [i["id"] for i in queue.items()] == ["q1"]


def test_a_check_made_outside_the_admission_lock_arms_nothing() -> None:
    slots.arm(admit(max_running=1), None)  # not holding the lock: no armed check follows
    docrun.run("a")
    slots.record_hold("j9", "c", "u")  # production-like start with no armed check is not refused here
    assert "j9" in slots.snapshot().held


def test_a_check_armed_for_one_instance_is_not_used_for_another() -> None:
    docrun.run("a")
    with slots.ADMISSION:
        slots.arm(admit(max_running=1), "b")
        slots.record_hold("j8", "c", "u")  # armed for b: this start of c is not judged by it
    assert "j8" in slots.snapshot().held
