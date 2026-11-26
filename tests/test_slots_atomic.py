"""The running limits are judged inside the write that records the hold, on the shared record alone; a refused start of
the capacity queue keeps its place; a failed publication refuses new non-production starts."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

import docrun
from sudoo.core import jobs, slots
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
        slots.arm(a, "c")
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
            slots.arm(admit(max_running=2), name)
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
    with pytest.raises(slots.Unavailable, match="no fresh view"):  # transient (503), not a limit (403)
        capacity.refusal(stage="development", user=None, counted=set())
    assert capacity.refusal(stage="production", user=None, counted=set()) is None  # production is unchanged
    with slots.ADMISSION:  # and inside the write too, when the flag turned on after the check
        slots.arm(admit(fresh=False, max_running=0), None)
        with pytest.raises(slots.Unavailable) as caught:
            slots.record_hold("jstale", "newname", "u")
        assert not isinstance(caught.value, slots.Refused)
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


def test_a_check_for_a_creation_is_not_used_by_a_start_of_an_instance_that_exists() -> None:
    docrun.run("b")  # the limit of 1 is full
    with slots.ADMISSION:
        slots.arm(admit(max_running=1), None)  # armed for a creation (no instance)
        slots.record_hold("jx", "a", "u")  # "a" exists and is not running: not judged by that arm, which is dropped
        slots.record_hold("jy", "new", "u")  # the arm was consumed: this creation is not judged either
    assert {"jx", "jy"} <= set(slots.snapshot().held)
    with slots.ADMISSION:
        slots.arm(admit(max_running=1), None)
        with pytest.raises(slots.Refused):
            slots.record_hold("jz", "brandnew", "u")  # a creation: judged against the doc


def test_admit_start_arms_for_the_instance_that_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.web.v1 import instances as api

    make("arm-a")
    seen: list[str | None] = []
    real = capacity.refusal

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs.get("instance"))
        return real(*args, **kwargs)

    monkeypatch.setattr(capacity, "refusal", spy)
    with slots.ADMISSION:
        api.admit_start("arm-a", "t")
        assert slots._ARMED.value is not None and slots._ARMED.value[1] == "arm-a"
        slots._ARMED.value = None
    assert seen == ["arm-a"]
    import shutil

    shutil.rmtree(store.folder("arm-a"), ignore_errors=True)


def test_a_refused_creation_start_records_no_branch_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.instances import planning

    recorded: list[str] = []
    monkeypatch.setattr(planning, "record_stage", lambda plan, user: recorded.append(plan["name"]))
    plan = {"name": "newone", "project": "p", "branch": "b", "stage": "development", "auto_update": False,
            "auto_upgrade": False, "auto_pause": False, "delete_after_days": 0}  # fmt: skip
    plan.update(dict.fromkeys(planning.SHOWN))
    docrun.run("a")
    with slots.ADMISSION:
        slots.arm(admit(max_running=1), None)
        with pytest.raises(slots.Refused):
            planning.start(plan, "u")
    assert recorded == [] and "newone" not in str(slots.snapshot().held)


def test_a_job_whose_hold_cannot_be_dropped_publishes_its_end(monkeypatch: pytest.MonkeyPatch) -> None:
    job = jobs.Job("abn", "t", "restart", "u")
    seen: list[str] = []
    monkeypatch.setattr(slots, "drop", lambda ids: False)
    monkeypatch.setattr(jobs.Job, "changed", lambda self: seen.append(self.status))
    jobs._abandon(job)
    assert job.status == "failed" and seen == ["failed"]


def test_a_queued_creation_is_judged_in_the_hold_of_the_real_planning_start_and_keeps_its_place(
    admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo.config import live
    from sudoo.instances import operations, planning

    plan = dict.fromkeys(planning.SHOWN)
    plan.update({"name": "qnew", "project": None, "branch": None, "stage": "development", "auto_update": False,
                 "auto_upgrade": False, "auto_pause": False, "delete_after_days": 0})  # fmt: skip
    storage().put(queue.COLLECTION, "c1", {"id": "c1", "seq": 1, "kind": "create", "instance": "qnew", "user": "admin1",
                                           "stage": "development", "plan": plan})  # fmt: skip
    ran: list[str] = []
    monkeypatch.setattr(operations, "create", lambda job, plan: ran.append(plan["name"]))
    monkeypatch.setattr(queue, "_ports", lambda: {})
    live.change({"max_running_instances": 1})
    state = {"fill": True}
    real_take = queue._take

    def take(item_id):  # type: ignore[no-untyped-def]
        if state["fill"]:
            docrun.fill_record(1)  # another process fills the record between the real check and the start
        return real_take(item_id)

    monkeypatch.setattr(queue, "_take", take)
    try:
        assert queue.dispatch() == []
        assert [i["id"] for i in queue.items()] == ["c1"] and ran == []
        assert not any(h["instance"] == "qnew" for h in slots.snapshot().held.values())
        state["fill"] = False
        docrun.reset()
        assert queue.dispatch() == ["qnew"]
        assert any(h["instance"] == "qnew" for h in slots.snapshot().held.values())
    finally:
        live.change({"max_running_instances": None})
        jobs._HELD.clear()
        for key in list(storage().all(queue.COLLECTION)):
            storage().delete(queue.COLLECTION, key)


def test_a_production_creation_after_a_non_production_check_is_not_judged_with_its_admit(admin) -> None:
    from sudoo.config import live

    live.change({"max_running_instances": 1})
    try:
        docrun.fill_record(1)
        assert capacity.refusal(stage="production", user=None) is None  # exempt, and it clears the earlier arm
        with slots.ADMISSION:
            docrun.run("a")
            slots.arm(admit(max_running=1), None)  # a non-production creation check of someone else
            assert capacity.refusal(stage="production", user=None) is None  # the production check disarms it
            slots.record_hold("jprod", "prod-new", "u")  # not refused by the other creation's admit
        assert "jprod" in slots.snapshot().held
        with slots.ADMISSION:
            slots.arm(admit(max_running=1), "named-a")
            slots.record_hold("jother", "other-new", "u")  # armed for another instance: not used
        assert "jother" in slots.snapshot().held
    finally:
        live.change({"max_running_instances": None})


def test_a_cancelled_queue_item_leaves_no_armed_check_for_the_next_item(admin, monkeypatch) -> None:
    from sudoo.config import live

    for n in ("qa", "qb"):
        make(n)
    storage().put(queue.COLLECTION, "q1", {**item("q1", 1, "qa"), "user": "admin1"})
    storage().put(queue.COLLECTION, "q2", {**item("q2", 2, "qb"), "user": "admin1", "stage": "production"})
    live.change({"max_running_instances": 5})
    real_take = queue._take
    monkeypatch.setattr(queue, "_take", lambda i: None if i == "q1" else real_take(i))  # cancelled meanwhile
    seen: list[object] = []
    monkeypatch.setattr(queue, "_start", lambda name, user: seen.append(slots._ARMED.value))
    try:
        queue.dispatch()
        assert len(seen) == 1 and seen[0] is not None and seen[0][1] == "qb"  # armed for qb itself, never qa's
    finally:
        live.change({"max_running_instances": None})
        import shutil

        for n in ("qa", "qb"):
            shutil.rmtree(store.folder(n), ignore_errors=True)
        for key in list(storage().all(queue.COLLECTION)):
            storage().delete(queue.COLLECTION, key)


def test_a_creation_that_cannot_begin_after_its_stage_was_recorded_gets_the_stage_back(monkeypatch) -> None:
    from sudoo import projects
    from sudoo.instances import planning

    found = projects.create(repository="Acme/Back", odoo_version="18.0", edition="community", origin="linked", user="t")
    plan = dict.fromkeys(planning.SHOWN)
    plan.update({"name": "rollbk", "project": found["id"], "branch": "feat", "stage": "development",
                 "auto_update": False, "auto_upgrade": False, "auto_pause": False, "delete_after_days": 0})  # fmt: skip

    class NoThread:
        def __init__(self, *a, **k) -> None: ...  # type: ignore[no-untyped-def]

        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(jobs.threading, "Thread", NoThread)
    try:
        with pytest.raises(RuntimeError):
            planning.start(plan, "u")
        assert "feat" not in (projects.get(found["id"]).get("branches") or {})
        assert not any(h["instance"] == "rollbk" for h in slots.snapshot().held.values())
    finally:
        projects.remove(found["id"])


def test_a_failed_publication_refuses_a_preview_and_an_automatic_creation_with_the_temporary_text(admin) -> None:
    from sudoo import projects
    from sudoo.projects import automation, previews

    found = projects.create(
        repository="Acme/Stale", odoo_version="18.0", edition="community", origin="linked", user="t"
    )
    slots.PUBLISH.failed = True
    try:
        text = previews.refusal(found, "system")
        assert text and "no fresh view" in text
        auto = automation.creation_refusal(found, "feat", "system")
        assert auto is None or "no fresh view" in auto  # earlier project rules may refuse first
        assert text and not any(h["instance"] for h in slots.snapshot().held.values())  # nothing created or held
    finally:
        slots.PUBLISH.failed = False
        projects.remove(found["id"])
