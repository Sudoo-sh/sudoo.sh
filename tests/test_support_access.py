"""Support access (support/, web/v1/support_access.py): granting, the choices and their limits, withdrawing, expiry,
"End now"; the support channel against the fake hub (fake_hub.py): grants reported, requests run as the synthetic
support user, answers sent back; a request is carried out only for an active grant and an open session; the level,
the scope, locked branches and blocked paths; support shells are always recorded; the banner; the audit log."""

from __future__ import annotations

import shutil
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from fake_hub import FakeHub
from sudoo import projects, roles, support
from sudoo.config import live
from sudoo.core import audit, jobs
from sudoo.core.jobs import Job
from sudoo.core.state import STATE
from sudoo.hub import client
from sudoo.instances import monitor, store
from sudoo.limits import quotas
from sudoo.projects import access
from sudoo.storage import storage
from sudoo.support import channel
from sudoo.webshell import policy

Client = tuple[FlaskClient, dict[str, str]]
NAMES = ("sa-prod", "sa-dev", "sa-other", "sa-plain")


def _instance(name: str, project: str, branch: str, stage: str) -> None:
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39600 + 2 * NAMES.index(name),
            "ODOO_PORT": 39601 + 2 * NAMES.index(name),
            "GIT_BRANCH": branch,
            "PROJECT": project,
            "STAGE": stage,
            "AUTO_UPDATE": "false",
            "AUTO_UPGRADE": "true",
            "AUTO_PAUSE": "false",
            "DELETE_AFTER_DAYS": "0",
            "CREATED_AT": int(time.time() - 3600),
            "ODOO_ADMIN_PASSWORD": "x",
            "MASTER_PASSWORD": "",
            **({"SOURCE": "plain"} if not project else {}),
        },
    )


@pytest.fixture(autouse=True)
def world() -> Iterator[dict[str, str]]:
    """Project "erp": main in production (sa-prod), dev (sa-dev); project "web": sa-other; sa-plain outside both."""
    erp = projects.create(repository="Acme/Help", odoo_version="18.0", edition="community", origin="linked", user="t")
    web = projects.create(repository="Acme/Site", odoo_version="18.0", edition="community", origin="linked", user="t")
    projects.change(erp["id"], lambda p: {**p, "seen": ["main", "dev"], "branches": {"main": {"stage": "production"}}})
    _instance("sa-prod", erp["id"], "main", "production")
    _instance("sa-dev", erp["id"], "dev", "development")
    _instance("sa-other", web["id"], "main", "development")
    _instance("sa-plain", "", "", "development")
    access.forget_restrictions()
    channel.forget()
    yield {"erp": erp["id"], "web": web["id"]}
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
        monitor.STATUS.pop(name, None)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    for collection in (support.COLLECTION, client.COLLECTION):
        for key in list(storage().all(collection)):
            storage().delete(collection, key)
    access.forget_restrictions()
    channel.forget()
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHub]:
    fake = FakeHub().start()
    monkeypatch.setattr(client, "hub_url", lambda: fake.url)
    live.change({"marketplace": True})
    yield fake
    live.change({"marketplace": None})
    fake.stop()


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Jobs are recorded (instance, kind, user) instead of run."""
    calls: list[tuple[str, str, str]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any) -> Job:
        calls.append((instance, kind, user))
        return Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    monkeypatch.setattr(quotas, "claim", lambda user: None)
    return calls


def _grant(admin: Client, level: str = "admin", hours: int = 1, **scope: Any) -> Any:
    client_, headers = admin
    body = {"level": level, "hours": hours, "scope": scope or {"all": True}, "ticket": "T-1042"}
    return client_.post("/api/v1/support-access/grants", json=body, headers=headers)


def _session(hub: FakeHub, grant_id: str, staff: str = "Sam Support") -> str:
    """The hub opens a session on a grant; the panel hears of it at its next call."""
    session_id = str(uuid.uuid4())
    hub.support_sessions.append({"id": session_id, "grant_id": grant_id, "staff_name": staff,
                                 "opened_at": "2026-11-18T10:00:00Z", "expires_at": "2026-11-18T11:00:00Z"})  # fmt: skip
    channel.exchange(0)
    return session_id


def _request(grant_id: str, session_id: str, method: str = "GET", path: str = "/api/v1/instances", **extra: Any) -> dict[str, Any]:  # fmt: skip
    return {"id": uuid.uuid4().hex, "kind": "http", "grant_id": grant_id, "session_id": session_id,
            "staff_name": "Sam Support", "method": method, "path": path, "query": "", "body": None,
            "issued_at": int(time.time()), "expires_at": int(time.time()) + 30, **extra}  # fmt: skip


def _names(answer: tuple[int, Any]) -> set[str]:
    status, body = answer
    assert status == 200, body
    return {item["name"] for item in body["data"]}


def _events(event: str) -> list[dict[str, Any]]:
    return [e for e in audit.latest(200) if e["event"] == event]


# ---------------------------------------------------------------- granting


def test_granting_needs_the_hub_link_and_checks_the_choices(admin: Client, world: dict[str, str]) -> None:
    refused = _grant(admin)
    assert refused.status_code == 409 and "marketplace" in refused.get_json()["detail"]
    live.change({"marketplace": True, "support_access_max_hours": 24})
    for body, why in (
        ({"level": "root"}, "level"),
        ({"hours": 72}, "how long"),
        ({"scope": {"all": True, "projects": [world["erp"]]}}, "not both"),
        ({"scope": {"all": False}}, "at least one"),
        ({"scope": {"projects": ["nope"]}}, "does not exist"),
        ({"scope": {"instances": ["sa-missing"]}}, "does not exist"),
    ):
        client_, headers = admin
        full = {"level": "admin", "hours": 1, "scope": {"all": True}} | body
        answer = client_.post("/api/v1/support-access/grants", json=full, headers=headers)
        assert answer.status_code == 400 and why in answer.get_json()["detail"], (body, answer.get_json())
    made = _grant(admin, "admin_shell", 24, projects=[world["erp"]], instances=["sa-plain"])
    assert made.status_code == 201, made.get_json()
    grant = made.get_json()["data"]
    assert grant["state"] == "active" and grant["ticket"] == "T-1042"
    assert grant["scope"]["projects"][0]["id"] == world["erp"] and grant["scope"]["instances"] == ["sa-plain"]
    overview = admin[0].get("/api/v1/support-access").get_json()["data"]
    assert overview["durations"] == [1, 24] and overview["max_hours"] == 24 and overview["unavailable"] is None
    assert [g["id"] for g in overview["grants"]] == [grant["id"]]
    granted = _events("support access granted")[0]
    assert granted["user"] == "admin1" and "panel admin + shell" in granted["detail"] and "T-1042" in granted["detail"]

    # withdrawing: at once, logged; switched off: every grant ends
    client_, headers = admin
    assert client_.delete(f"/api/v1/support-access/grants/{grant['id']}", headers=headers).status_code == 204
    assert support.active() == [] and _events("support access withdrawn")
    _grant(admin)
    live.change({"support_access": False})
    assert support.active() == []
    assert _grant(admin).status_code == 409


def test_a_viewer_cannot_grant_and_support_never_manages_support(app: Flask, admin: Client) -> None:
    from conftest import sign_in
    from sudoo import accounts

    if accounts.get("sa-viewer") is None:
        accounts.create("sa-viewer", "viewer", "harbor-sapling-quiet-58")
    viewer = sign_in(app, "sa-viewer", "harbor-sapling-quiet-58")
    assert viewer[0].get("/api/v1/support-access").status_code == 403
    assert _grant(viewer).status_code == 403
    # everyone signed in sees the banner's status, without the button
    assert viewer[0].get("/api/v1/support-access/status").get_json()["data"]["can_end"] is False
    assert admin[0].get("/api/v1/support-access/status").get_json()["data"]["can_end"] is True
    assert "support_access.manage" in roles.permissions("admin")
    assert "support_access.manage" not in roles.permissions("maintainer")


# ---------------------------------------------------------------- the channel


def test_the_channel_reports_grants_and_runs_requests_as_support(
    hub: FakeHub, admin: Client, world: dict[str, str]
) -> None:
    grant = _grant(admin, "view").get_json()["data"]
    channel.exchange(0)
    reported = hub.support_reports[-1]["grants"]
    assert [g["id"] for g in reported] == [grant["id"]] and reported[0]["level"] == "view"
    assert reported[0]["granted_by"] and reported[0]["scope"]["all"] is True

    session_id = _session(hub, grant["id"])
    assert _events("support session started")[0]["user"] == "Sudoo.sh support (Sam Support)"
    status = admin[0].get("/api/v1/support-access/status").get_json()["data"]
    assert status["connected"] is True and status["sessions"][0]["staff_name"] == "Sam Support"

    # a request comes over the channel, is run as support and answered to the hub
    item = _request(grant["id"], session_id)
    hub.support_queue.append(item)
    channel.exchange(0)
    for _ in range(100):
        if item["id"] in hub.support_answers:
            break
        time.sleep(0.05)
    answer = hub.support_answers[item["id"]]
    assert answer["status"] == 200 and {"sa-prod", "sa-dev", "sa-other", "sa-plain"} <= {
        i["name"] for i in answer["body"]["data"]
    }

    # "End now": the grant ends, the session ends here at once and on the hub with the next call
    client_, headers = admin
    assert client_.post("/api/v1/support-access/end", headers=headers).status_code == 204
    assert support.active() == [] and channel.sessions() == []
    channel.exchange(0)
    assert hub.support_reports[-1]["grants"] == [] and session_id in hub.support_reports[-1]["ended_sessions"]
    assert channel.handle(_request(grant["id"], session_id))[0] == 403
    assert _events("support access ended now") and _events("support session ended")


def test_requests_need_an_active_grant_and_an_open_session(hub: FakeHub, admin: Client) -> None:
    grant = _grant(admin).get_json()["data"]
    session_id = _session(hub, grant["id"])
    assert channel.handle(_request(grant["id"], session_id))[0] == 200
    # unknown session, another grant's session, a request that waited too long
    assert channel.handle(_request(grant["id"], str(uuid.uuid4())))[0] == 403
    assert channel.handle(_request(str(uuid.uuid4()), session_id))[0] == 403
    assert channel.handle(_request(grant["id"], session_id, expires_at=int(time.time()) - 1))[0] == 408
    # the grant runs out: nothing more, and the expiry is logged once
    storage().change(support.COLLECTION, grant["id"], lambda d: {**(d or {}), "expires_at": time.time() - 1})
    assert channel.handle(_request(grant["id"], session_id))[0] == 403
    assert support.note_expired() == [grant["id"]] and support.note_expired() == []
    assert _events("support access expired")[0]["user"] == "system"
    # a support user whose grant ended has no permission at all, even if it reached the app
    user = support.user_for(storage().get(support.COLLECTION, grant["id"]) or {}, "Sam", session_id)
    assert support.permissions(user) == frozenset()


def test_levels_paths_scope_and_locks(
    hub: FakeHub, admin: Client, world: dict[str, str], started: list[tuple[str, str, str]]
) -> None:
    # view: reads only
    view = _grant(admin, "view").get_json()["data"]
    seen = _session(hub, view["id"])
    restart = "/api/v1/instances/sa-dev/actions/restart"
    assert channel.handle(_request(view["id"], seen, "POST", restart, body={}))[0] == 403
    assert started == []
    # admin: acts as "Sudoo.sh support (...)", never accounts, tokens, settings, teams, access, its own access
    grant = _grant(admin, "admin", projects=[world["erp"]], instances=["sa-plain"]).get_json()["data"]
    session_id = _session(hub, grant["id"])
    status, body = channel.handle(_request(grant["id"], session_id, "POST", restart, body={}))
    assert status == 202, body
    assert started == [("sa-dev", "restart", "Sudoo.sh support (Sam Support)")]
    for path in ("/api/v1/tokens", "/api/v1/users", "/api/v1/settings", "/api/v1/support-access",
                 "/api/v1/account", "/api/v1/teams", "/api/v1/instances/sa-dev/shell/tickets"):  # fmt: skip
        assert channel.handle(_request(grant["id"], session_id, "GET", path))[0] == 403, path
    assert _events("support request refused")
    assert channel.handle(_request(grant["id"], session_id, "GET", "/api/v1/../healthz"))[0] == 400
    user = support.user_for(storage().get(support.COLLECTION, grant["id"]) or {}, "Sam", session_id)
    allowed = support.permissions(user)
    assert support.EXCLUDED.isdisjoint(allowed) and roles.SHELL_PERMISSIONS.isdisjoint(allowed)
    assert "instances.update" in allowed

    # scope: the chosen project and the chosen instance; nothing else
    assert _names(channel.handle(_request(grant["id"], session_id))) == {"sa-prod", "sa-dev", "sa-plain"}
    assert channel.handle(_request(grant["id"], session_id, "GET", "/api/v1/instances/sa-other"))[0] == 404

    # a locked branch stays locked: seen, but not opened or changed
    projects.change(world["erp"], lambda p: {**p, "access": {"mode": "panel_roles",
                    "locks": {"dev": {"by": "admin1", "allow": [{"type": "user", "id": "admin1"}]}}}})  # fmt: skip
    access.forget_restrictions()
    assert channel.handle(_request(grant["id"], session_id, "GET", "/api/v1/instances/sa-dev"))[0] == 200
    assert channel.handle(_request(grant["id"], session_id, "POST", restart, body={}))[0] == 403
    logs = channel.handle(_request(grant["id"], session_id, "GET", "/api/v1/instances/sa-dev/logs"))
    assert logs[0] == 403 and "locked" in logs[1]["detail"]
    # a project chosen by name stays open to support when it is restricted later (the admin chose it), the lock too
    projects.change(world["erp"], lambda p: {**p, "access": {**p["access"], "mode": "restricted", "grants": []}})
    access.forget_restrictions()
    assert _names(channel.handle(_request(grant["id"], session_id))) == {"sa-prod", "sa-dev", "sa-plain"}
    assert channel.handle(_request(grant["id"], session_id, "POST", restart, body={}))[0] == 403

    # all: restricted projects stay out
    every = _grant(admin, "admin").get_json()["data"]
    everywhere = _session(hub, every["id"])
    assert _names(channel.handle(_request(every["id"], everywhere))) == {"sa-other", "sa-plain"}
    # a restricted branch stays out too, unless the grant names the instance itself
    projects.change(world["erp"], lambda p: {**p, "access": {"mode": "panel_roles",
                    "branches": {"dev": {"restricted": True, "grants": []}}}})  # fmt: skip
    access.forget_restrictions()
    assert _names(channel.handle(_request(every["id"], everywhere))) == {"sa-prod", "sa-other", "sa-plain"}


def test_support_shells_need_admin_shell_and_are_always_recorded(hub: FakeHub, admin: Client) -> None:
    live.change({"shell_recording": "off", "shell_production": "allowed"})
    admin_only = _grant(admin, "admin").get_json()["data"]
    with_shell = _grant(admin, "admin_shell").get_json()["data"]
    for grant in (admin_only, with_shell):
        _session(hub, grant["id"])
    plain = support.user_for(storage().get(support.COLLECTION, admin_only["id"]) or {}, "Sam", "s1")
    with pytest.raises(policy.Refused):
        policy.plan(plain, "sa-dev", "shell")
    shell_user = support.user_for(storage().get(support.COLLECTION, with_shell["id"]) or {}, "Sam", "s2")
    plan = policy.plan(shell_user, "sa-dev", "psql")
    assert plan.recorded is True and plan.root is False
    with pytest.raises(policy.Refused):
        policy.plan(shell_user, "sa-dev", "shell", as_root=True)
    with pytest.raises(policy.Refused) as refused:
        policy.plan(shell_user, "sa-prod", "shell")
    assert refused.value.code == "confirm-production"
    # through the channel: a shell step of a session that is not open is refused
    from sudoo.support import shells

    assert shells.handle(shell_user, {"op": "read", "shell": "abc"})[0] == 404


def test_audit_entries_of_support_are_marked(hub: FakeHub, admin: Client) -> None:
    grant = _grant(admin, "view").get_json()["data"]
    _session(hub, grant["id"])
    entries = admin[0].get("/api/v1/audit?limit=20").get_json()["data"]
    started = next(e for e in entries if e["event"] == "support session started")
    assert started["support"] is True and started["user"].startswith("Sudoo.sh support (")
    assert next(e for e in entries if e["event"] == "support access granted")["support"] is False


def test_a_relayed_shell_keeps_numbered_frames_until_they_are_read() -> None:
    import asyncio
    import json

    from sudoo.support import shells

    relayed = shells.Relayed(id="a1", session_id="s", grant_id="g")
    text = "héllo".encode()
    asyncio.run(relayed.send_bytes(text[:2]))  # a character cut in two arrives whole
    asyncio.run(relayed.send_bytes(text[2:]))
    asyncio.run(relayed.send_str(json.dumps({"type": "closed", "reason": "exit", "detail": ""})))
    found = relayed.read(0, 0.1)
    assert [f["type"] for f in found["frames"]] == ["output", "output", "closed"] and found["closed"] is True
    assert "".join(f["data"] for f in found["frames"] if f["type"] == "output") == "héllo"
    assert relayed.read(found["next"], 0.1)["frames"] == []
