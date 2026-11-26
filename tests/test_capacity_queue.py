"""The capacity queue (CAPACITY_QUEUE): a request a running-instances limit would refuse waits in line and starts by
itself when there is room. Off by default (then the refusal is the 403 it always was); never for production."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

from sudoo import accounts
from sudoo.config import live
from sudoo.core import jobs
from sudoo.core.jobs import Job
from sudoo.instances import metrics, planning, store
from sudoo.limits import queue, quotas
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
MADE: list[str] = []
STARTED: list[tuple[str, str]] = []


@pytest.fixture(autouse=True)
def clean_up(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Nothing a test makes outlives it: instance folders, running marks, the queue and the settings. Job starts are
    stand-ins that only record, except in a test that asks for `real_jobs` (the real registry, a controlled wake)."""
    STARTED.clear()

    def fake_job(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any) -> Job:
        STARTED.append((instance, action))
        metrics.INSTANCES[instance] = {"running": True}  # a started job counts as running, as the real one does
        return Job(instance, action, kind, user)

    def fake_plan_start(plan: dict[str, Any], user: str) -> Job:
        STARTED.append((str(plan["name"]), "create"))
        metrics.INSTANCES[str(plan["name"])] = {"running": True}
        store.release_ports(plan.get("ports") or {})
        return Job(str(plan["name"]), "Create", "create", user)

    if "real_jobs" not in request.fixturenames:
        monkeypatch.setattr(jobs, "start", fake_job)
    monkeypatch.setattr(planning, "start", fake_plan_start)
    yield
    for name in MADE:
        shutil.rmtree(store.folder(name), ignore_errors=True)
    MADE.clear()
    metrics.INSTANCES.clear()
    for key in list(storage().all(queue.COLLECTION)):
        storage().delete(queue.COLLECTION, key)
    live.change({"max_running_non_production_per_user": None, "max_running_instances": None, "capacity_queue": False,
                 "max_instances": None, "max_instances_per_user": None, "create_cooldown_minutes": None})  # fmt: skip
    for user in ("admin1", "sync1"):
        storage().delete(quotas.CREATIONS, user)
    for user in ("admin1", "sync1"):
        if (accounts.get(user) or {}).get("disabled"):
            accounts.update(user, disabled=False)


@pytest.fixture
def real_jobs(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """The real jobs registry with a wake that waits: a started job stays registered (and counts as starting)."""
    from sudoo.instances import operations

    release = threading.Event()
    monkeypatch.setattr(operations, "wake", lambda job, name: release.wait(20))
    yield release
    release.set()
    for _ in range(100):
        if not jobs.starting():
            break
        time.sleep(0.05)


def make(name: str, stage: str, owner: str, running: bool) -> None:
    MADE.append(name)
    store.folder(name).mkdir(parents=True, exist_ok=True)
    (store.folder(name) / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(name, {"ODOO_VERSION": "18.0", "DB_NAME": name.replace("-", "_"), "ODOO_PORT": "18999",
                           "STAGE": stage, "CREATED_BY": owner})  # fmt: skip
    if running:
        metrics.INSTANCES[name] = {"running": True}


def limit(panel: int | None = None, own: int | None = None, on: bool = True) -> None:
    live.change({"max_running_instances": panel, "max_running_non_production_per_user": own, "capacity_queue": on})


def post_start(client: FlaskClient, headers: dict[str, str], name: str) -> Any:
    return client.post(f"/api/v1/instances/{name}/actions/start", headers=headers)


def test_off_by_default_the_refusal_is_the_403_it_was(admin: Client) -> None:
    client, headers = admin
    make("dev-a", "development", "admin1", True)
    make("dev-b", "development", "admin1", False)
    limit(panel=1, on=False)
    assert post_start(client, headers, "dev-b").status_code == 403
    assert queue.items() == []


def test_a_start_over_the_limit_waits_and_starts_when_room_appears(admin: Client) -> None:
    client, headers = admin
    make("dev-a", "development", "admin1", True)
    make("dev-b", "development", "admin1", False)
    limit(panel=1)
    answer = post_start(client, headers, "dev-b")
    assert answer.status_code == 202
    body = answer.get_json()["data"]
    assert body["position"] == 1 and body["status"] == "Waiting for capacity (position 1)"
    assert answer.headers["Location"].endswith(f"/capacity-queue/{body['id']}")
    assert queue.dispatch() == [] and STARTED == []  # still no room
    metrics.INSTANCES["dev-a"] = {"running": False}
    assert queue.dispatch() == ["dev-b"] and STARTED == [("dev-b", "Start")]
    assert queue.items() == []


def test_the_line_is_first_in_first_out_and_two_free_slots_start_two(admin: Client) -> None:
    client, headers = admin
    make("dev-a", "development", "admin1", True)
    for name in ("dev-b", "dev-c", "dev-d"):
        make(name, "development", "admin1", False)
    limit(panel=1)
    for name in ("dev-b", "dev-c", "dev-d"):
        assert post_start(client, headers, name).status_code == 202
    assert [i["instance"] for i in queue.items()] == ["dev-b", "dev-c", "dev-d"]
    assert post_start(client, headers, "dev-b").get_json()["data"]["position"] == 1  # one item per instance
    assert len(queue.items()) == 3
    limit(panel=3)
    metrics.INSTANCES["dev-b"] = {"running": False}
    started = queue.dispatch()  # room for two more than the one running: b and c, not d
    assert started == ["dev-b", "dev-c"]
    assert [i["instance"] for i in queue.items()] == ["dev-d"]


def test_a_person_at_their_own_limit_does_not_hold_up_the_others() -> None:
    make("dev-a", "development", "admin1", True)
    make("dev-b", "development", "admin1", False)
    make("dev-c", "development", "sync1", False)
    limit(own=1)
    queue.enqueue_start("dev-b", "admin1", "admin1", "development", held("instances.update"))
    queue.enqueue_start("dev-c", "sync1", "sync1", "development", held("instances.update"))
    assert queue.dispatch() == ["dev-c"]
    assert [i["instance"] for i in queue.items()] == ["dev-b"]


def test_the_line_survives_a_restart() -> None:
    make("dev-b", "development", "admin1", False)
    item = queue.enqueue_start("dev-b", "admin1", "admin1", "development")
    reloaded = list(storage().all(queue.COLLECTION).values())  # what a new process reads
    assert [i["id"] for i in reloaded] == [item["id"]] and queue.position(str(item["id"])) == 1


def test_production_is_never_queued(admin: Client) -> None:
    client, headers = admin
    make("dev-a", "development", "admin1", True)
    make("prod-a", "production", "admin1", False)
    limit(panel=1)
    assert post_start(client, headers, "prod-a").status_code == 202
    assert queue.items() == [] and STARTED == [("prod-a", "Start")]  # started at once, not queued


def test_creation_waits_keeps_its_name_and_creates_later(admin: Client) -> None:
    client, headers = admin
    make("dev-a", "development", "admin1", True)
    limit(panel=1)
    payload = {"version": "18.0", "edition": "community", "name": "later", "stage": "development"}
    answer = client.post("/api/v1/instances", json=payload, headers=headers)
    assert answer.status_code == 202, answer.get_data(as_text=True)
    assert answer.get_json()["data"]["kind"] == "create"
    assert queue.names() == {"later"} and STARTED == []
    again = client.post("/api/v1/instances", json=payload, headers=headers)
    assert again.status_code == 202 and "later" in queue.names()
    assert len(queue.names()) == 2  # the waiting name is taken: the second one got another name
    metrics.INSTANCES["dev-a"] = {"running": False}
    assert queue.dispatch() == ["later"] and STARTED == [("later", "create")]


def test_cancel_own_or_by_an_administrator_not_by_others(admin: Client, operator: Client) -> None:
    make("dev-b", "development", "admin1", False)
    make("dev-c", "development", "admin1", False)
    first = queue.enqueue_start("dev-b", "admin1", "admin1", "development")
    second = queue.enqueue_start("dev-c", "admin1", "admin1", "development")
    other, other_headers = operator
    assert other.delete(f"/api/v1/capacity-queue/{first['id']}", headers=other_headers).status_code == 404
    assert other.get("/api/v1/capacity-queue").get_json()["data"] == []  # not theirs: not listed
    client, headers = admin
    assert client.delete(f"/api/v1/capacity-queue/{first['id']}", headers=headers).status_code == 204
    assert [i["id"] for i in queue.items()] == [second["id"]]
    assert client.delete(f"/api/v1/capacity-queue/{first['id']}", headers=headers).status_code == 404


def test_only_an_administrator_moves_one_to_the_front(admin: Client, operator: Client) -> None:
    make("dev-b", "development", "sync1", False)
    make("dev-c", "development", "sync1", False)
    queue.enqueue_start("dev-b", "sync1", "sync1", "development")
    last = queue.enqueue_start("dev-c", "sync1", "sync1", "development")
    other, other_headers = operator
    assert other.post(f"/api/v1/capacity-queue/{last['id']}/front", headers=other_headers).status_code == 403
    client, headers = admin
    assert client.post(f"/api/v1/capacity-queue/{last['id']}/front", headers=headers).status_code == 200
    assert [i["instance"] for i in queue.items()] == ["dev-c", "dev-b"]
    assert len(client.get("/api/v1/capacity-queue").get_json()["data"]) == 2  # an administrator sees all


def test_an_item_for_a_deleted_or_running_instance_is_dropped() -> None:
    make("dev-b", "development", "admin1", True)
    queue.enqueue_start("dev-b", "admin1", "admin1", "development")
    queue.enqueue_start("gone", "admin1", "admin1", "development")
    assert queue.dispatch() == [] and queue.items() == []


def test_memory_and_disk_refusals_are_not_queued(admin: Client) -> None:
    """Only the running-instances limits wait; the plan's other checks answer as before."""
    client, headers = admin
    limit(on=True)
    answer = client.post(
        "/api/v1/instances", json={"version": "99.0", "edition": "community", "name": "x"}, headers=headers
    )
    assert answer.status_code == 400 and queue.items() == []


def held(*permissions: str) -> list[str]:
    return list(permissions)


def test_a_disabled_or_deleted_account_does_not_start_anything() -> None:
    make("dev-a", "development", "sync1", False)
    make("dev-b", "development", "sync1", False)
    queue.enqueue_start("dev-a", "sync1", "sync1", "development", held("instances.update"))
    queue.enqueue_start("dev-b", "ghost", "ghost", "development", held("instances.update"))  # no such account
    accounts.update("sync1", disabled=True)
    assert queue.dispatch() == [] and STARTED == [] and queue.items() == []  # both dropped, not kept for later


def test_a_disabled_or_deleted_charged_person_stops_a_waiting_request_but_nobody_does_not() -> None:
    make("dev-a", "development", "sync1", False)
    make("dev-b", "development", "sync1", False)
    make("dev-c", "development", "sync1", False)
    queue.enqueue_start("dev-a", "sync1", "admin1", "development", held("instances.update"))  # charged to another
    queue.enqueue_start("dev-b", "sync1", "system", "development", held("instances.update"))  # nobody: valid
    queue.enqueue_start("dev-c", "sync1", None, "development", held("instances.update"))  # nobody: valid
    accounts.update("admin1", disabled=True)  # after the request waited, before its job
    assert queue.dispatch() == ["dev-b", "dev-c"] and queue.items() == []
    assert [name for name, _ in STARTED] == ["dev-b", "dev-c"]  # the charged one was dropped, not started
    queue.enqueue_start("dev-a", "sync1", "ghost-person", "development", held("instances.update"))  # deleted
    assert queue.dispatch() == [] and queue.items() == []


def test_a_role_that_lost_the_right_while_waiting_does_not_start() -> None:
    make("dev-a", "development", "sync1", False)
    queue.enqueue_start("dev-a", "sync1", "sync1", "development", held("instances.view"))  # could not update
    assert queue.dispatch() == [] and STARTED == [] and queue.items() == []


def test_a_creation_is_checked_against_the_project_at_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.projects import access

    plan = {"name": "later", "stage": "development", "project": "", "branch": "", "ports": {}}
    queue.enqueue_create(plan, "sync1", "sync1", held("instances.create"))
    monkeypatch.setattr(access, "refusal", lambda *a, **k: (404, "This project does not exist."))
    assert queue.dispatch() == [] and STARTED == [] and queue.items() == []


def test_an_allowed_request_still_starts() -> None:
    make("dev-a", "development", "sync1", False)
    queue.enqueue_start("dev-a", "sync1", "sync1", "development", held("instances.update"))
    assert queue.dispatch() == ["dev-a"]


def test_a_cancelled_or_replayed_item_never_starts_twice() -> None:
    make("dev-a", "development", "sync1", False)
    make("dev-b", "development", "sync1", False)
    one = queue.enqueue_start("dev-a", "sync1", "sync1", "development", held("instances.update"))
    two = queue.enqueue_start("dev-b", "sync1", "sync1", "development", held("instances.update"))
    assert queue.cancel(str(one["id"]), "sync1", admin=False)
    assert queue.dispatch() == ["dev-b"]
    assert queue.dispatch() == [] and STARTED == [("dev-b", "Start")]  # a second round finds nothing
    storage().put(queue.COLLECTION, str(two["id"]), two)  # a stale copy written back: the instance runs already
    assert queue.dispatch() == [] and STARTED == [("dev-b", "Start")] and queue.items() == []


def test_two_dispatchers_with_one_free_slot_start_one(real_jobs: threading.Event) -> None:
    """Real jobs registry: three requests wait, room for one, two dispatchers run at the same moment."""
    limit(panel=1)
    for name in ("dev-a", "dev-b", "dev-c"):
        make(name, "development", "sync1", False)
        queue.enqueue_start(name, "sync1", "sync1", "development", held("instances.update"))
    results: list[list[str]] = []
    gate = threading.Barrier(2)

    def go() -> None:
        gate.wait()
        results.append(queue.dispatch())

    threads = [threading.Thread(target=go) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    started = sorted(name for found in results for name in found)
    assert started == ["dev-a"]  # one slot, one start: the first in line, once
    assert len(jobs.starting()) == 1
    assert [i["instance"] for i in queue.items()] == ["dev-b", "dev-c"]


def test_two_competing_starts_through_the_api_and_the_dispatcher(admin: Client, real_jobs: threading.Event) -> None:
    """An API start and a dispatch at the same moment for one free slot: exactly one gets it."""
    client, headers = admin
    limit(panel=1)
    make("dev-q", "development", "sync1", False)
    queue.enqueue_start("dev-q", "sync1", "sync1", "development", held("instances.update"))
    make("dev-n", "development", "admin1", False)
    codes: list[int] = []
    results: list[list[str]] = []
    gate = threading.Barrier(2)

    def api() -> None:
        gate.wait()
        codes.append(post_start(client, headers, "dev-n").status_code)

    def dispatch() -> None:
        gate.wait()
        results.append(queue.dispatch())

    threads = [threading.Thread(target=api), threading.Thread(target=dispatch)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert len(jobs.starting()) == 1  # never two
    assert codes == [202]  # started, or queued behind the other
    running = set(jobs.starting())
    waiting = {str(item["instance"]) for item in queue.items()}
    assert len(running) == 1 and running | waiting == {"dev-q", "dev-n"}  # one runs, the other still waits


def test_disabling_through_the_api_drops_a_waiting_request_and_enabling_does_not_bring_it_back(
    admin: Client, operator: Client
) -> None:
    """The real Disable call (not a stand-in): a request waiting for a person is dropped at dispatch without any job,
    their open session stays ended after Enable, and the dropped request is not revived."""
    from sudoo.core import audit

    person, person_headers = operator
    make("dev-a", "development", "sync1", True)
    make("dev-b", "development", "sync1", False)
    limit(panel=1)
    queued = post_start(person, person_headers, "dev-b")
    assert queued.status_code == 202 and len(queue.items()) == 1
    admin_client, admin_headers = admin
    assert (
        admin_client.post("/api/v1/users/sync1/disable", json={"reason": "test"}, headers=admin_headers).status_code
        == 200
    )
    metrics.INSTANCES["dev-a"] = {"running": False}  # room appears: it would start if the person were enabled
    assert queue.dispatch() == [] and STARTED == [] and queue.items() == []
    assert any(
        e["event"] == "capacity queue: dropped" and "disabled" in str(e.get("detail")) for e in audit.latest(limit=20)
    )
    assert person.get("/api/v1/account").status_code == 401  # the session ended at once
    assert (
        admin_client.post("/api/v1/users/sync1/enable", json={"reason": "test"}, headers=admin_headers).status_code
        == 200
    )
    assert queue.dispatch() == [] and STARTED == [] and queue.items() == []  # not revived
    assert person.get("/api/v1/account").status_code == 401  # nor the session


def plan_for(name: str) -> dict[str, Any]:
    return {"name": name, "stage": "development", "project": "", "branch": "", "ports": {}}


def test_a_creation_is_checked_against_the_instance_limits_at_its_turn() -> None:
    queue.enqueue_create(plan_for("later"), "admin1", "admin1", held("instances.create"))
    make("dev-a", "development", "admin1", False)  # made while it waited
    live.change({"max_instances_per_user": 1})
    assert queue.dispatch() == [] and STARTED == []
    (item,) = queue.items()
    assert "most one user may have" in str(item["waiting_for"])  # it waits and says what for
    live.change({"max_instances": 1, "max_instances_per_user": None})
    assert queue.dispatch() == [] and "maximum of 1 instances" in str(queue.items()[0]["waiting_for"])
    shutil.rmtree(store.folder("dev-a"))  # room again: the same item starts, nothing was lost
    assert queue.dispatch() == ["later"] and STARTED == [("later", "create")]


def test_a_blocked_creation_does_not_hold_up_another_person_or_itself_count() -> None:
    queue.enqueue_create(plan_for("later"), "admin1", "admin1", held("instances.create"))
    make("dev-a", "development", "admin1", False)
    make("dev-b", "development", "sync1", False)
    queue.enqueue_start("dev-b", "sync1", "sync1", "development", held("instances.update"))
    live.change({"max_instances_per_user": 1})
    assert queue.dispatch() == ["dev-b"] and [i["instance"] for i in queue.items()] == ["later"]
    shutil.rmtree(store.folder("dev-a"))
    assert queue.dispatch() == ["later"]  # alone at its limit of 1: a waiting item does not count itself


def test_the_turn_does_not_charge_the_waiting_time_or_the_count_again() -> None:
    live.change({"create_cooldown_minutes": 60})
    quotas.claim("admin1")  # what asking for the creation does, before it is queued
    charged = dict(storage().get(quotas.CREATIONS, "admin1") or {})
    queue.enqueue_create(plan_for("later"), "admin1", "admin1", held("instances.create"))
    assert queue.dispatch() == ["later"]  # the waiting time that was charged at the request does not block it
    assert dict(storage().get(quotas.CREATIONS, "admin1") or {}) == charged  # and it was recorded once only


def test_a_cancelled_item_stays_cancelled_while_its_reason_is_being_written() -> None:
    item = queue.enqueue_create(plan_for("later"), "admin1", "admin1", held("instances.create"))
    assert queue.cancel(str(item["id"]), "admin1", admin=False)
    queue._note(str(item["id"]), "some reason")  # a dispatcher that read the item before the cancel
    assert queue.items() == [] and storage().get(queue.COLLECTION, str(item["id"])) is None


def test_a_creation_that_fails_to_start_does_not_stop_the_line(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(plan: dict[str, Any], user: str) -> Job:
        if plan["name"] == "bad":
            raise RuntimeError("no ports")
        STARTED.append((str(plan["name"]), "create"))
        return Job(str(plan["name"]), "Create", "create", user)

    monkeypatch.setattr(planning, "start", broken)
    queue.enqueue_create(plan_for("bad"), "admin1", "admin1", held("instances.create"))
    queue.enqueue_create(plan_for("good"), "admin1", "admin1", held("instances.create"))
    assert queue.dispatch() == ["good"] and queue.items() == []  # the failed one is audited and gone, not stuck


def test_a_start_is_refused_for_the_instances_own_owner_not_whoever_asks() -> None:
    from sudoo.instances import capacity

    make("dev-a", "development", "admin1", True)
    make("dev-b", "development", "admin1", False)
    make("prod", "production", "admin1", False)
    limit(own=1)
    assert capacity.start_refusal("dev-b") is not None  # sync1 owns it and runs one already
    assert capacity.start_refusal("prod") is None  # production is never refused


def test_a_creation_goes_from_the_api_through_the_queue_to_its_start(admin: Client) -> None:
    """Asked for, queued (the waiting time charged once), blocked by an instance the person made meanwhile, shown
    with what it waits for, then started when that room is back."""
    client, headers = admin
    live.change({"create_cooldown_minutes": 60})
    make("dev-a", "development", "sync1", True)
    limit(panel=1)
    payload = {"version": "18.0", "edition": "community", "name": "later", "stage": "development"}
    assert client.post("/api/v1/instances", json=payload, headers=headers).status_code == 202
    charged = dict(storage().get(quotas.CREATIONS, "admin1") or {})
    assert charged["count"] == 1 and queue.names() == {"later"}
    metrics.INSTANCES["dev-a"] = {"running": False}  # running room appears, but the person made an instance meanwhile
    make("dev-b", "development", "admin1", False)
    live.change({"max_instances_per_user": 1})
    assert queue.dispatch() == [] and STARTED == []
    shown = client.get("/api/v1/capacity-queue", headers=headers).get_json()["data"]
    assert [i["instance"] for i in shown] == ["later"] and "most one user may have" in shown[0]["waiting_for"]
    shutil.rmtree(store.folder("dev-b"))
    assert queue.dispatch() == ["later"] and STARTED == [("later", "create")]
    assert dict(storage().get(quotas.CREATIONS, "admin1") or {}) == charged  # the turn charged nothing


def test_an_api_creation_and_a_queued_creation_of_one_person_never_both_take_the_last_place(
    admin: Client, real_jobs: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-person limit of one instance: an API creation and the dispatch of a queued creation at the same moment
    (the supported single-process model, both under quotas.LOCK): exactly one of them is created."""
    client, headers = admin
    release = real_jobs

    def register(plan: dict[str, Any], user: str) -> Job:  # a creation job that stays registered, as a real one does
        store.release_ports(plan.get("ports") or {})
        return jobs.start(
            str(plan["name"]), "Create instance", "create", lambda job, p: release.wait(20), plan, user=user
        )

    monkeypatch.setattr(planning, "start", register)
    live.change({"max_instances_per_user": 1})
    queue.enqueue_create(plan_for("later"), "admin1", "admin1", held("instances.create"))
    codes: list[int] = []
    results: list[list[str]] = []
    gate = threading.Barrier(2)
    payload = {"version": "18.0", "edition": "community", "name": "direct", "stage": "development"}

    def api() -> None:
        gate.wait()
        codes.append(client.post("/api/v1/instances", json=payload, headers=headers).status_code)

    def dispatch() -> None:
        gate.wait()
        results.append(queue.dispatch())

    try:
        threads = [threading.Thread(target=api), threading.Thread(target=dispatch)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        created = len([1 for _, user in jobs.pending_creations() if user == "admin1"])
        assert created == 1  # never two, whichever came first
        assert (codes == [202]) != (results == [["later"]])  # exactly one of the two got the place
        assert codes in ([202], [403]) and results in ([["later"]], [[]])
    finally:
        release.set()
        for _ in range(100):
            if not jobs.pending_creations():
                break
            time.sleep(0.05)


def test_a_queued_creation_waits_while_the_persons_api_creation_is_still_running(
    admin: Client, real_jobs: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other order of the race, made certain: the API creation is registered first, so at its turn the queued
    one sees it as an instance already (a creation that is not finished counts) and waits."""
    client, headers = admin

    def register(plan: dict[str, Any], user: str) -> Job:
        store.release_ports(plan.get("ports") or {})
        return jobs.start(
            str(plan["name"]), "Create instance", "create", lambda job, p: real_jobs.wait(20), plan, user=user
        )

    monkeypatch.setattr(planning, "start", register)
    live.change({"max_instances_per_user": 2})
    queue.enqueue_create(plan_for("later"), "admin1", "admin1", held("instances.create"))
    payload = {"version": "18.0", "edition": "community", "name": "direct", "stage": "development"}
    assert client.post("/api/v1/instances", json=payload, headers=headers).status_code == 202
    live.change({"max_instances_per_user": 1})
    assert queue.dispatch() == [] and queue.names() == {"later"}
    real_jobs.set()
    for _ in range(100):
        if not jobs.pending_creations():
            break
        time.sleep(0.05)
    shutil.rmtree(store.folder("direct"), ignore_errors=True)
