"""The starts nobody waits on (automatic update, configuration repair) and the helper they share: at a running limit
the job is not started and the skip is audit-logged; below it the job starts and holds the slot. Not covered by a
behavioural test here: maintenance, the automatic upgrade rollback, applying project resources and rebuilding staging
(they use the same helper or admit_start; test_start_paths only checks that their files mention the check)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

import docrun  # noqa: E402
from sudoo.config import live
from sudoo.core import audit, jobs, slots
from sudoo.core.jobs import Job
from sudoo.instances import capacity, integrity, metrics, store

MADE: list[str] = []


@pytest.fixture(autouse=True)
def clean_up() -> Iterator[None]:
    yield
    for name in MADE:
        shutil.rmtree(store.folder(name), ignore_errors=True)
    MADE.clear()
    metrics.INSTANCES.clear()
    docrun.reset()
    live.change({"max_running_instances": None})


def make(name: str, **extra: str) -> None:
    MADE.append(name)
    store.folder(name).mkdir(parents=True, exist_ok=True)
    (store.folder(name) / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(name, {"ODOO_VERSION": "18.0", "DB_NAME": name.replace("-", "_"), "ODOO_PORT": "18999",
                           "STAGE": "development", "CREATED_BY": "admin1", **extra})  # fmt: skip


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **kw: Any) -> Job:
        calls.append({"instance": instance, "holds_slot": kw.get("holds_slot"), "kind": kind})
        return Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    return calls


def test_the_helper_refuses_at_the_limit_starts_below_it_and_skips_a_running_instance(started: list[Any]) -> None:
    make("row-a")
    make("row-busy")
    docrun.run("row-busy", True)
    live.change({"max_running_instances": 1})
    job, why = capacity.start_admitted("row-a", lambda: jobs.start("row-a", "t", "restart", lambda j: None))
    assert job is None and "at most 1 instance" in (why or "") and started == []
    docrun.run("row-a", True)  # it runs already: it holds its slot, nothing new is asked
    job, why = capacity.start_admitted("row-a", lambda: jobs.start("row-a", "t", "restart", lambda j: None))
    assert why is None and job is not None, why
    metrics.INSTANCES.clear()
    live.change({"max_running_instances": 2})
    job, why = capacity.start_admitted("row-a", lambda: jobs.start("row-a", "t", "restart", lambda j: None))
    assert job is not None and why is None


def test_a_slot_record_that_fails_refuses_the_start_with_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    make("row-b")

    def fail() -> Job:
        raise slots.Unavailable("could not record it")

    job, why = capacity.start_admitted("row-b", fail)
    assert job is None and why == "could not record it"


def test_an_automatic_update_at_the_limit_is_skipped_and_audited(started: list[Any]) -> None:
    from sudoo import triggers

    make("row-c", AUTO_UPDATE="true")
    make("row-busy2")
    docrun.run("row-busy2", True)
    live.change({"max_running_instances": 1})
    triggers.request_update("row-c", "a push")
    assert started == []
    assert any("running limit" in str(e.get("event")) for e in audit.latest(20, instance="row-c"))
    live.change({"max_running_instances": None})
    triggers.request_update("row-c", "a push")
    assert started and started[-1]["holds_slot"] is True and started[-1]["kind"] == "update"


def test_a_repair_at_the_limit_is_skipped_and_audited(started: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    make("row-d")
    make("row-busy3")
    docrun.run("row-busy3", True)
    live.change({"max_running_instances": 1, "drift_action": "restore"})
    found = [SimpleNamespace(restorable=True)]
    integrity._ATTEMPTED.pop("row-d", None)
    integrity._maybe_repair("row-d", found, ("x",))  # type: ignore[arg-type]
    assert started == []
    assert any("running limit" in str(e.get("event")) for e in audit.latest(20, instance="row-d"))
    live.change({"max_running_instances": None})
    integrity._ATTEMPTED.pop("row-d", None)
    integrity._maybe_repair("row-d", found, ("x",))  # type: ignore[arg-type]
    assert started and started[-1]["holds_slot"] is True and started[-1]["kind"] == "repair"
    live.change({"drift_action": None})


def test_maintenance_step_goes_through_the_real_hold_and_a_refusal_in_its_write_skips_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sudoo import maintenance

    make("row-m1")
    steps: list[dict[str, Any]] = []
    monkeypatch.setattr(maintenance, "affected", lambda item: ["row-m1"])
    monkeypatch.setattr(maintenance, "_step", lambda item_id, step: steps.append(step))
    monkeypatch.setattr(maintenance, "_finish", lambda *a, **k: None)
    monkeypatch.setattr(maintenance, "_wait", lambda job: None)
    monkeypatch.setattr(maintenance, "work", lambda job, name, kind, backup: None)
    monkeypatch.setattr(maintenance, "hold", lambda item, name: None)
    item = {"id": "m-1", "kind": "restart", "title": "x"}
    monkeypatch.setattr(capacity, "admission_refusal", docrun.passes_then_full())
    maintenance._instances_work(item)
    assert steps[-1]["status"] == "skipped" and "running or starting" in steps[-1]["detail"]
    assert not any(h["instance"] == "row-m1" for h in slots.snapshot().held.values())
    monkeypatch.undo()
    docrun.reset()
    monkeypatch.setattr(maintenance, "affected", lambda item: ["row-m1"])
    monkeypatch.setattr(maintenance, "_step", lambda item_id, step: steps.append(step))
    monkeypatch.setattr(maintenance, "_finish", lambda *a, **k: None)
    monkeypatch.setattr(maintenance, "_wait", lambda job: None)
    monkeypatch.setattr(maintenance, "work", lambda job, name, kind, backup: None)
    monkeypatch.setattr(maintenance, "hold", lambda item, name: None)
    maintenance._instances_work(item)
    assert steps[-1]["status"] in ("done", "failed") and steps[-1].get("job")
    assert any(h["instance"] == "row-m1" for h in slots.snapshot().held.values())


def test_automatic_upgrade_cancel_goes_through_the_real_hold_and_a_refusal_in_its_write_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sudoo.instances import odoo
    from sudoo.upgrades import run

    make("row-u1")
    from sudoo import upgrades

    monkeypatch.setattr(upgrades, "all_upgrades", lambda: [{"source": "row-u1"}])
    monkeypatch.setattr(run, "rollback_open", lambda doc, now: True)
    monkeypatch.setattr(run, "WATCH_FAILURES", 1)
    monkeypatch.setattr(run, "cancel", lambda job, name, reason: None)
    monkeypatch.setattr(odoo, "answers", lambda env: False)
    run._DOWN.clear()
    run._TRIED.clear()
    monkeypatch.setattr(capacity, "admission_refusal", docrun.passes_then_full())
    assert run.watch_once(now=1e9) == []
    assert any("skipped" in str(e.get("event")) for e in audit.latest(20, instance="row-u1"))
    assert not any(h["instance"] == "row-u1" for h in slots.snapshot().held.values())
