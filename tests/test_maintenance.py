"""Maintenance windows (maintenance.py, web/v1/maintenance.py): items arrive only signed from the hub (the command
"maintenance" and the lease's claim) and pass the allow-list; choosing the window (presets, bounds, the default window
applied when nobody chooses, the deadline); reminders; running one instance at a time with production last; clients'
closed projects respected unless they approve; reports back to the hub; the client portal's side."""

from __future__ import annotations

import shutil
import time
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from fake_hub import FakeHub
from sudoo import accounts, maintenance, projects
from sudoo.config import live, settings
from sudoo.core import jobs
from sudoo.hub import client, marketplace
from sudoo.instances import store
from sudoo.projects import access, clients
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
PASSWORD = "lantern-orchard-cobalt-{}"
NAMES = ("mw-prod", "mw-stage", "mw-dev", "mw-other")
DAY = 86400
FAIL: set[str] = set()


def _definition(**values: Any) -> dict[str, Any]:
    now = int(time.time())
    return {
        "maintenance_id": str(uuid.uuid4()),
        "kind": "restart",
        "scope": "panel",
        "targets": [],
        "version": None,
        "reason": {"en": "A planned restart of the server.", "ar": "إعادة تشغيل مخطّطة للخادم."},
        "estimated_minutes": 30,
        "earliest_start": now + 3600,
        "deadline": now + 7 * DAY,
        "required": True,
        **values,
    }


@pytest.fixture(autouse=True)
def clean_items() -> Iterator[None]:
    for key in list(storage().all(maintenance.COLLECTION)):
        storage().delete(maintenance.COLLECTION, key)
    live.change({"timezone": "UTC"})
    yield
    for key in list(storage().all(maintenance.COLLECTION)):
        storage().delete(maintenance.COLLECTION, key)
    live.change({"timezone": None, "maintenance_client_choice": None, "client_confirmations": None})


@pytest.fixture
def inbox(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any]]]:
    """E-mail set up, and every e-mail the panel would send: (to, template, values)."""
    import sudoo.mail

    class Ready:
        ready = True

    sent: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(sudoo.mail, "mailer", lambda: Ready())
    monkeypatch.setattr(sudoo.mail, "send", lambda to, key, **values: sent.append((to, key, values)))
    return sent


def _instance(name: str, project_id: str, branch: str, stage: str) -> None:
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(name, {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": name.replace("-", "_"),
                           "GATEWAY_PORT": 39800 + 2 * NAMES.index(name), "ODOO_PORT": 39801 + 2 * NAMES.index(name),
                           "GIT_BRANCH": branch, "CREATED_AT": int(time.time() - 3600), "STAGE": stage,
                           "PROJECT": project_id})  # fmt: skip


def _account(name: str, role: str) -> None:
    if accounts.get(name) is None:
        accounts.create(name, role, PASSWORD.format(name), first_name=name, email=f"{name}@example.com")
        if role == "client":
            accounts.update(name, sign_in_methods=["password", "email_code"])


@pytest.fixture
def project() -> Iterator[tuple[str, str]]:
    """A project with production, staging and development instances (client mw-client), and another project."""
    found = projects.create(repository="Acme/Maint", odoo_version="18.0", edition="community", origin="linked",
                            user="t")  # fmt: skip
    other = projects.create(repository="Acme/MaintOther", odoo_version="18.0", edition="community", origin="linked",
                            user="t")  # fmt: skip
    _instance("mw-prod", found["id"], "main", "production")
    _instance("mw-stage", found["id"], "staging", "staging")
    _instance("mw-dev", found["id"], "dev", "development")
    _instance("mw-other", other["id"], "main", "production")
    _account("mw-client", "client")
    clients.add_client(found["id"], "mw-client", "admin1")
    access.forget_restrictions()
    yield str(found["id"]), str(other["id"])
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
    projects.remove(found["id"])
    projects.remove(other["id"])
    access.forget_restrictions()


def _received(**values: Any) -> dict[str, Any]:
    definition = _definition(**values)
    assert maintenance.receive("cmd-" + definition["maintenance_id"][:8], definition) == ("done", None)
    found = maintenance.get(definition["maintenance_id"])
    assert found is not None
    return found


def _due(found: dict[str, Any]) -> float:
    """Choose a start just after the earliest one allowed; returns it."""
    start = maintenance.bounds(found)[0] + 60
    maintenance.choose(found["id"], start, "admin1")
    return start


# ---------------------------------------------------------------- the allow-list


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "shell", "scope": "panel"},
        {"kind": "restart", "scope": "everything"},
        {"kind": "panel_update", "scope": "instance", "targets": ["x"], "version": "3.4.0"},
        {"kind": "odoo_images", "scope": "panel"},
        {"kind": "panel_update", "version": None},
        {"kind": "panel_update", "version": "3.4.0; rm -rf /"},
        {"kind": "restart", "version": "3.4.0"},
        {"scope": "instance", "targets": []},
        {"scope": "instance", "targets": ["../etc"]},
        {"scope": "instance", "targets": ["a b"]},
        {"targets": ["not-for-the-panel"]},
        {"estimated_minutes": 0},
        {"deadline": 1, "earliest_start": 2},
        {"required": "yes"},
        {"reason": {"en": "", "ar": "سبب"}},
        {"maintenance_id": "not-a-uuid"},
    ],
)
def test_the_allow_list_refuses_anything_else(change: dict[str, Any]) -> None:
    definition = _definition(**change)
    with pytest.raises(maintenance.Refused):
        maintenance.clean(definition)
    status, detail = maintenance.receive("cmd-1", definition)
    assert status == "failed" and detail
    assert storage().all(maintenance.COLLECTION) == {}


def test_valid_definitions_keep_only_their_fields() -> None:
    found = maintenance.clean(_definition(kind="panel_update", version="3.5.0", command="docker rm -f x"))
    assert found["kind"] == "panel_update" and found["version"] == "3.5.0" and "command" not in found
    found = maintenance.clean(_definition(kind="odoo_images", scope="project", targets=["acme", "acme"]))
    assert found["targets"] == ["acme"]


# ---------------------------------------------------------------- arriving from the hub


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHub]:
    fake = FakeHub().start()
    monkeypatch.setattr(client, "hub_url", lambda: fake.url)
    for collection in (client.COLLECTION, marketplace.COLLECTION):
        for key in list(storage().all(collection)):
            storage().delete(collection, key)
    live.change({"marketplace": True})
    yield fake
    live.change({"marketplace": None})
    fake.stop()


def test_a_signed_command_records_the_item_and_reports_go_back(hub: FakeHub, inbox: list[Any]) -> None:
    marketplace.heartbeat()
    me = client.identity().installation_id
    item_id = hub.plan_maintenance(me)
    refused = hub.plan_maintenance(me, kind="shell")
    hub.maintenance.pop(refused)
    marketplace.heartbeat()
    found = maintenance.get(item_id)
    assert found is not None and found["status"] == "proposed"
    assert maintenance.get(refused) is None
    acks = {hub.commands[c]["payload"]["maintenance_id"]: body for c, body in hub.acks}
    assert acks[item_id] == {"status": "done"}
    assert acks[refused]["status"] == "failed" and "shell" in acks[refused]["detail"]
    assert any(key == "maintenance_planned" for _, key, _ in inbox)
    start = maintenance.bounds(found)[0] + 3600  # an administrator chooses a time: the hub hears it, signed
    maintenance.choose(item_id, start, "admin1")
    assert maintenance.flush() == 1
    reported_id, body = hub.maintenance_reports[-1]
    assert reported_id == item_id and body["status"] == "scheduled" and body["window_source"] == "panel"
    assert datetime.fromisoformat(body["window_start"]).timestamp() == round(start)
    assert (maintenance.get(item_id) or {})["outbox"] == []


def test_the_lease_brings_cancellations_and_the_owners_choice(hub: FakeHub) -> None:
    marketplace.heartbeat()
    me = client.identity().installation_id
    first = hub.plan_maintenance(me)
    second = hub.plan_maintenance(me)
    hub.commands.clear()  # only the lease tells this panel
    marketplace.heartbeat()
    assert maintenance.get(first) is not None and maintenance.get(second) is not None
    chosen = int(time.time()) + 2 * DAY
    hub.maintenance[first].update(status="cancelled", updated_at=int(time.time()) + 5)
    hub.maintenance[second].update(status="scheduled", window_start=chosen, window_end=chosen + 1800,
                                   window_source="account", chosen_by="Acme Ltd", updated_at=int(time.time()) + 5)  # fmt: skip
    marketplace.heartbeat()
    assert (maintenance.get(first) or {})["status"] == "cancelled"
    found = maintenance.get(second) or {}
    assert found["status"] == "scheduled" and found["window_start"] == chosen
    assert found["window_source"] == "account" and found["chosen_by"] == "Acme Ltd"
    assert found["outbox"] == []  # the hub knows already


# ---------------------------------------------------------------- choosing the time


def test_presets_and_bounds() -> None:
    now = time.time()
    found = _received(earliest_start=int(now + 3 * DAY), deadline=int(now + 10 * DAY))
    lo, hi = maintenance.bounds(found, now)
    choices = {p["id"]: datetime.fromisoformat(p["start"]) for p in maintenance.presets(found, now)}
    assert set(choices) == {"tonight", "weekend", "default"}
    assert all(lo <= c.timestamp() <= hi for c in choices.values())
    assert choices["tonight"].strftime("%H:%M") == "02:00"
    assert choices["weekend"].strftime("%a") == "Sat"
    assert choices["default"].strftime("%H:%M") == "01:00"
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.choose(found["id"], now + DAY, "admin1", now=now)  # before the earliest start
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.choose(found["id"], now + 11 * DAY, "admin1", now=now)  # after the deadline


def test_the_api_lets_administrators_choose(app: Flask, admin: Client, passwords: dict[str, str]) -> None:
    found = _received()
    web, headers = admin
    listed = web.get("/api/v1/maintenance").get_json()
    assert listed["meta"]["may_choose"] is True and listed["data"][0]["id"] == found["id"]
    answer = web.put(f"/api/v1/maintenance/{found['id']}/window", json={"preset": "default"}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    window = answer.get_json()["data"]["window"]
    assert window["source"] == "panel" and window["chosen_by"]
    bad = web.put(f"/api/v1/maintenance/{found['id']}/window", json={"start": "2000-01-01T02:00"}, headers=headers)
    assert bad.status_code == 422
    developer, dev_headers = sign_in(app, "sync1", passwords["operator"])
    refused = developer.put(f"/api/v1/maintenance/{found['id']}/window", json={"preset": "tonight"},
                            headers=dev_headers)  # fmt: skip
    assert refused.status_code == 403
    assert developer.get("/api/v1/maintenance").status_code == 200


def test_the_default_window_applies_when_nobody_chooses(inbox: list[Any]) -> None:
    found = _received()
    maintenance.tick(now=found["received_at"] + 3600)
    assert (maintenance.get(found["id"]) or {})["status"] == "proposed"
    later = found["received_at"] + settings.maintenance_choose_hours * 3600 + 1
    maintenance.tick(now=later)
    chosen = maintenance.get(found["id"]) or {}
    assert chosen["status"] == "scheduled" and chosen["window_source"] == "default"
    assert datetime.fromtimestamp(chosen["window_start"], settings.zone).strftime("%H:%M") == "01:00"
    assert any(key == "maintenance_reminder" for _, key, _ in inbox)


def test_past_its_deadline_an_optional_item_fails_and_a_required_one_runs_soon() -> None:
    now = time.time()
    optional = _received(required=False, earliest_start=int(now - 3 * DAY), deadline=int(now - DAY))
    required = _received(required=True, earliest_start=int(now - 3 * DAY), deadline=int(now - DAY))
    storage().change(maintenance.COLLECTION, optional["id"], lambda d: {**(d or {}), "outbox": []})
    maintenance.tick(now=now)
    assert (maintenance.get(optional["id"]) or {})["status"] == "failed"
    found = maintenance.get(required["id"]) or {}
    assert found["status"] in ("scheduled", "running") and found["window_start"] <= now + 3600
    maintenance._RUNNING.acquire()  # wait for a run the tick may have started
    maintenance._RUNNING.release()


def test_reminders_are_sent_once(inbox: list[Any]) -> None:
    found = _received()
    start = maintenance.bounds(found)[0] + 2 * DAY
    maintenance.choose(found["id"], start, "admin1")
    inbox.clear()
    maintenance.tick(now=start - 3 * DAY)
    assert inbox == []
    maintenance.tick(now=start - 23 * 3600)
    maintenance.tick(now=start - 22 * 3600)
    assert [key for _, key, _ in inbox].count("maintenance_reminder") == 1
    maintenance.tick(now=start - 1800)
    assert [key for _, key, _ in inbox].count("maintenance_reminder") == 2
    assert "ada@example.com" in {to for to, _, _ in inbox}


# ---------------------------------------------------------------- running


@pytest.fixture
def worked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The instances the jobs worked on, in order; an instance named in FAIL fails."""
    done: list[str] = []

    def work(job: jobs.Job, name: str, kind: str, backup_first: bool) -> None:
        job.step("Working")
        done.append(name)
        if name in FAIL:
            raise jobs.JobError("It broke.")

    monkeypatch.setattr(maintenance, "work", work)
    FAIL.clear()
    return done


def test_instances_run_one_by_one_production_last(project: tuple[str, str], worked: list[str]) -> None:
    project_id, other = project
    found = _received(kind="odoo_images", scope="project", targets=[project_id, other])
    _due(found)
    done = maintenance.run(found["id"]) or {}
    assert worked == ["mw-dev", "mw-stage", "mw-other", "mw-prod"]
    assert done["status"] == "done" and done["detail"] == "4 done."
    assert [s["status"] for s in done["steps"]] == ["done"] * 4
    body = done["outbox"][-1]
    assert body["status"] == "done" and len(body["steps"]) == 4


def test_after_a_failure_production_is_not_touched(project: tuple[str, str], worked: list[str]) -> None:
    project_id, _ = project
    FAIL.add("mw-stage")
    found = _received(kind="restart", scope="project", targets=[project_id])
    _due(found)
    done = maintenance.run(found["id"]) or {}
    assert worked == ["mw-dev", "mw-stage"]
    assert done["status"] == "failed"
    steps = {s["instance"]: s for s in done["steps"]}
    assert steps["mw-prod"]["status"] == "skipped" and "failed" in steps["mw-prod"]["detail"]


def test_a_closed_project_is_skipped_unless_its_client_approves(project: tuple[str, str], worked: list[str]) -> None:
    project_id, _ = project
    clients.set_switch(project_id, None, "closed", "mw-client")
    try:
        found = _received(kind="restart", scope="instance", targets=["mw-prod", "mw-other"])
        _due(found)
        done = maintenance.run(found["id"]) or {}
        assert worked == ["mw-other"]
        assert {s["instance"]: s["status"] for s in done["steps"]} == {"mw-prod": "skipped", "mw-other": "done"}
        again = _received(kind="restart", scope="instance", targets=["mw-prod"])
        maintenance.approve(again["id"], project_id, "mw-client")
        _due(again)
        worked.clear()
        assert (maintenance.run(again["id"]) or {})["status"] == "done" and worked == ["mw-prod"]
    finally:
        clients.set_switch(project_id, None, "open", "mw-client")


def test_the_panel_restart_and_custom_work(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo import plugin_manager

    asked: list[str] = []
    monkeypatch.setattr(plugin_manager, "schedule_restart", lambda reason: asked.append(reason) or True)
    restart = _received()
    _due(restart)
    assert (maintenance.run(restart["id"]) or {})["status"] == "running" and asked
    maintenance.resume()  # the panel came back
    assert (maintenance.get(restart["id"]) or {})["status"] == "done"
    custom = _received(kind="custom", scope="server", targets=["main"])
    start = _due(custom)
    assert (maintenance.run(custom["id"]) or {})["status"] == "running"
    maintenance.tick(now=start + 1700)
    assert (maintenance.get(custom["id"]) or {})["status"] == "running"
    maintenance.tick(now=start + 31 * 60)
    assert (maintenance.get(custom["id"]) or {})["status"] == "done"


def test_a_panel_update_waits_for_the_new_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "sudoo_version", lambda: "3.4.0")
    found = _received(kind="panel_update", version="3.5.0")
    start = _due(found)
    assert (maintenance.run(found["id"], now=start) or {})["status"] == "running"
    request_file = settings.manager_dir / maintenance.REQUEST_FILE
    assert '"3.5.0"' in request_file.read_text()
    maintenance.tick(now=start + 600)
    assert (maintenance.get(found["id"]) or {})["status"] == "running"
    monkeypatch.setattr(client, "sudoo_version", lambda: "3.5.0")
    maintenance.tick(now=start + 1200)
    assert (maintenance.get(found["id"]) or {})["status"] == "done" and not request_file.exists()
    late = _received(kind="panel_update", version="9.0.0")
    begin = _due(late)
    maintenance.run(late["id"], now=begin)
    maintenance.tick(now=begin + settings.maintenance_update_wait_hours * 3600 + 1)
    assert (maintenance.get(late["id"]) or {})["status"] == "failed"


def test_the_gateway_and_the_public_page_say_so(project: tuple[str, str]) -> None:
    project_id, _ = project
    found = _received(kind="restart", scope="instance", targets=["mw-prod"])
    _due(found)
    notices = maintenance.public_notices(project_id)
    assert [n["status"] for n in notices] == ["scheduled"] and "reason" not in notices[0]
    assert not maintenance.running_on("mw-prod")
    storage().change(maintenance.COLLECTION, found["id"], lambda d: {**(d or {}), "status": "running"})
    assert maintenance.running_on("mw-prod") and not maintenance.running_on("mw-other")


# ---------------------------------------------------------------- the client portal


def test_clients_see_their_maintenance_and_choose_when_allowed(app: Flask, project: tuple[str, str],
                                                               inbox: list[Any]) -> None:  # fmt: skip
    project_id, _ = project
    mine = _received(kind="restart", scope="instance", targets=["mw-prod", "mw-other"])
    _received(kind="restart", scope="instance", targets=["mw-other"])  # not its project
    web, headers = sign_in(app, "mw-client", PASSWORD.format("mw-client"))
    listed = web.get("/api/v1/client/maintenance").get_json()
    assert [i["id"] for i in listed["data"]] == [mine["id"]]
    assert [i["name"] for i in listed["data"][0]["instances"]] == ["mw-prod"]  # never another client's instances
    assert listed["meta"]["may_choose"] is False
    refused = web.put(f"/api/v1/client/maintenance/{mine['id']}/window", json={"preset": "tonight"}, headers=headers)
    assert refused.status_code == 403
    live.change({"maintenance_client_choice": True, "client_confirmations": False})
    answer = web.put(f"/api/v1/client/maintenance/{mine['id']}/window", json={"preset": "tonight"}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    found = maintenance.get(mine["id"]) or {}
    assert found["window_source"] == "client" and project_id in found["approvals"]
    assert found["outbox"][-1]["chosen_by"].endswith("(client)")
    live.change({"client_confirmations": True})
    waiting = web.post(f"/api/v1/client/maintenance/{mine['id']}/approve", json={}, headers=headers)
    assert waiting.status_code == 202 and waiting.get_json()["data"]["confirmation"]
    other = web.put(f"/api/v1/client/maintenance/{uuid.uuid4()}/window", json={"preset": "tonight"}, headers=headers)
    assert other.status_code == 404
