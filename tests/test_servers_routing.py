"""Several servers, the panel's side: requests passed to the right server's agent, placement of new instances,
counting production across servers, moving an instance (and rolling a failed move back), and what an agent does
on its own while the main panel is away. The agents here are fakes answering the panel's calls (links.call)."""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

from sudoo import servers
from sudoo.core import jobs
from sudoo.servers import agent, inventory, links, move, registry, trust
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]


class FakeAgent:
    """A server's agent as the panel sees it: answers calls, remembers them."""

    def __init__(self, server_id: str) -> None:
        self.id = server_id
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.files: dict[str, bytes] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.instances: set[str] = set()
        self.fail_create = False

    def _job(self, instance: str, action: str, notes: list[str] | None = None, ok: bool = True) -> dict[str, Any]:
        job_id = f"j{len(self.jobs) + 1}{self.id[-4:]}"
        self.jobs[job_id] = {"id": job_id, "instance": instance, "action": action, "status": "success" if ok else
                             "failed", "steps": [{"title": action, "status": "done"}], "notes": [
                                 {"text": t, "level": "info"} for t in notes or []], "error": None if ok else "boom",
                             "started_at": "2026-11-18T10:00:00Z"}  # fmt: skip
        return self.jobs[job_id]

    def http(self, args: dict[str, Any]) -> dict[str, Any]:
        method, path = args["method"], args["path"]

        def answer(status: int, data: Any = None) -> dict[str, Any]:
            raw = json.dumps({"data": data}).encode() if data is not None else b""
            return {"status": status, "mimetype": "application/json", "headers": {},
                    "body": base64.b64encode(raw).decode()}  # fmt: skip

        if path.startswith("/api/v1/jobs/"):
            return answer(200, self.jobs[path.rsplit("/", 1)[1]])
        parts = path.split("/")
        name = parts[4] if len(parts) > 4 else ""
        if method == "GET" and len(parts) == 5:
            if name not in self.instances:
                return answer(404)
            return answer(200, {"name": name, "status": "online", "odoo": {"version": "19.0"}, "project": None})
        if method == "GET" and path.endswith("/credentials"):
            return answer(200, {"admin_password": "s3cret", "login": "admin"})
        if method == "POST" and "/actions/" in path:
            action = parts[-1]
            if action == "backup":
                stamp = time.strftime("%Y%m%d-%H%M%S")
                file_name = f"{name}__{name.replace('-', '_')}__{stamp}__manual.zip"
                self.files[file_name] = b"PK" + b"x" * 5000
                return answer(202, self._job(name, action, [f"Backup saved: {file_name}"]))
            if action == "delete":
                self.instances.discard(name)
            return answer(202, self._job(name, action))
        return answer(200, {"echo": path, "user": args["user"]})

    def __call__(self, op: str, args: dict[str, Any]) -> Any:
        self.calls.append((op, args))
        if op == "http":
            return self.http(args)
        if op == "create":
            plan = args["plan"]
            if self.fail_create:
                return self._job(plan["name"], "create", ok=False)
            self.instances.add(plan["name"])
            return self._job(plan["name"], "create")
        if op == "file.stat":
            if args["name"] not in self.files:
                raise links.RemoteError(404, "no such file")
            return {"size": len(self.files[args["name"]])}
        if op == "file.read":
            data = self.files[args["name"]][args["offset"] : args["offset"] + args["length"]]
            return {"data": base64.b64encode(data).decode()}
        if op == "file.write":
            part = self.files.get(args["name"] + ".part", b"")[: args["offset"]]
            part += base64.b64decode(args["data"])
            self.files[args["name"] + ".part"] = part
            if args["last"]:
                self.files[args["name"]] = self.files.pop(args["name"] + ".part")
            return {"written": 1}
        if op == "file.remove":
            self.files.pop(args["name"], None)
            return {"removed": True}
        raise links.RemoteError(404, op)


@pytest.fixture
def fleet(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, FakeAgent]]:
    """Two active, connected servers with fake agents."""
    monkeypatch.setattr(registry, "panel_url", lambda: "https://panel.example.com")
    for key in list(storage().all(registry.COLLECTION)):
        storage().delete(registry.COLLECTION, key)
    inventory.reset()
    for key in list(storage().all(inventory.COLLECTION)):
        storage().delete(inventory.COLLECTION, key)
    agents: dict[str, FakeAgent] = {}
    for name in ("Riyadh", "Frankfurt"):
        record, code_text = registry.create(name, "r", None, "admin1")
        code = trust.JoinCode.decode(code_text)
        key = trust.new_key()
        public = trust.public_text(key)
        registry.join(code.server_id, code.secret, public, trust.sign(key, trust.join_message(
            code.server_id, code.secret, public)), {})  # fmt: skip
        registry.approve(code.server_id, trust.fingerprint(public), "admin1")
        agents[name] = FakeAgent(code.server_id)
    by_id = {a.id: a for a in agents.values()}
    up = set(by_id)
    monkeypatch.setattr(links, "connected", lambda server_id: server_id in up)
    monkeypatch.setattr(links, "online", lambda server_id: server_id == servers.MAIN or server_id in up)

    def call(server_id: str, op: str, args: dict[str, Any], timeout: float | None = None) -> Any:
        if server_id not in up:
            raise links.Offline(server_id)
        return by_id[server_id](op, args)

    monkeypatch.setattr(links, "call", call)
    agents["_up"] = up  # type: ignore[assignment]
    yield agents
    inventory.reset()
    for key in list(storage().all(inventory.COLLECTION)):
        storage().delete(inventory.COLLECTION, key)
    for key in list(storage().all(registry.COLLECTION)):
        storage().delete(registry.COLLECTION, key)


def _report(agent_fake: FakeAgent, *entries: tuple[str, dict[str, str]]) -> None:
    for name, _ in entries:
        agent_fake.instances.add(name)
    inventory.report(agent_fake.id, {"instances": [
        {"name": name, "resource": {"name": name, "status": "online", "odoo": {"version": "19.0"},
                                    "git": {"repository": "https://github.com/acme/shop"}}, "env": env}
        for name, env in entries]})  # fmt: skip


# ---------------------------------------------------------------- routing


def test_requests_about_another_servers_instance_go_to_its_agent(
    fleet: dict[str, FakeAgent], admin: Client, operator: Client
) -> None:
    riyadh = fleet["Riyadh"]
    _report(riyadh, ("far-one", {"STAGE": "staging", "ODOO_VERSION": "19.0"}))
    client, headers = admin
    listed = client.get("/api/v1/instances").get_json()["data"]
    far = next(i for i in listed if i["name"] == "far-one")
    assert far["server"]["id"] == riyadh.id and far["server"]["name"] == "Riyadh"
    shown = client.get("/api/v1/instances/far-one").get_json()["data"]
    assert shown["server"]["id"] == riyadh.id and shown["status"] == "online"
    op, args = riyadh.calls[-1]
    assert op == "http" and args["path"] == "/api/v1/instances/far-one" and args["user"]["name"] == "admin1"
    echoed = operator[0].get("/api/v1/instances/far-one/logs?lines=5").get_json()["data"]
    assert echoed["echo"] == "/api/v1/instances/far-one/logs"
    # a developer: exactly the developer's permissions go with the request, never more
    assert "instances.delete" not in echoed["user"]["permissions"]
    assert "instances.update" in echoed["user"]["permissions"]
    action = client.post("/api/v1/instances/far-one/actions/restart", json={}, headers=headers)
    assert action.status_code == 202 and inventory.job_server(action.get_json()["data"]["id"]) == riyadh.id
    job_id = action.get_json()["data"]["id"]
    assert client.get(f"/api/v1/jobs/{job_id}").get_json()["data"]["instance"] == "far-one"
    assert any(j["id"] == job_id for j in client.get("/api/v1/jobs").get_json()["data"])
    assert fleet["Frankfurt"].calls == []  # nothing went to the other server


def test_an_offline_server_keeps_its_instances_listed_and_refuses_requests(
    fleet: dict[str, FakeAgent], admin: Client
) -> None:
    riyadh = fleet["Riyadh"]
    _report(riyadh, ("far-two", {"STAGE": "production"}))
    fleet["_up"].discard(riyadh.id)  # type: ignore[attr-defined]
    client, _ = admin
    listed = client.get("/api/v1/instances").get_json()["data"]
    assert next(i for i in listed if i["name"] == "far-two")["status"] == "unreachable"
    answer = client.get("/api/v1/instances/far-two")
    assert answer.status_code == 503 and "offline" in answer.get_json()["detail"]
    servers_list = client.get("/api/v1/servers").get_json()["data"]
    assert next(s for s in servers_list if s["id"] == riyadh.id)["online"] is False


def test_creating_on_another_server_passes_the_plan_to_its_agent(fleet: dict[str, FakeAgent], admin: Client) -> None:
    riyadh = fleet["Riyadh"]
    client, headers = admin
    created = client.post(
        "/api/v1/instances",
        json={"version": "19.0", "edition": "community", "name": "remote-new", "server": riyadh.id},
        headers=headers,
    )
    assert created.status_code == 202, created.get_json()
    op, args = riyadh.calls[-1]
    assert op == "create" and args["plan"]["name"] == "remote-new" and "ports" not in args["plan"]
    assert inventory.server_of("remote-new") == riyadh.id
    # the same name is never given twice, whatever server it is on
    again = client.get("/api/v1/instance-plan?name=remote-new&version=19.0&edition=community").get_json()["data"]
    assert again["name"] != "remote-new"
    assert {s["id"] for s in again["servers"]} >= {"main", riyadh.id}
    unknown = client.post("/api/v1/instances", json={"version": "19.0", "edition": "community",
                                                     "server": "srv-nothere000"}, headers=headers)  # fmt: skip
    assert unknown.status_code == 400
    fleet["_up"].discard(riyadh.id)  # type: ignore[attr-defined]
    offline = client.post("/api/v1/instances", json={"version": "19.0", "edition": "community",
                                                     "server": riyadh.id}, headers=headers)  # fmt: skip
    assert offline.status_code == 503


def test_a_full_server_is_refused(fleet: dict[str, FakeAgent], admin: Client) -> None:
    riyadh = fleet["Riyadh"]
    registry.update(riyadh.id, {"capacity": {"max_instances": 1}})
    _report(riyadh, ("only-one", {"STAGE": "development"}))
    client, headers = admin
    full = client.post("/api/v1/instances", json={"version": "19.0", "edition": "community",
                                                  "server": riyadh.id}, headers=headers)  # fmt: skip
    assert full.status_code == 409 and "its limit" in full.get_json()["detail"]
    room = client.get(f"/api/v1/servers/{riyadh.id}").get_json()["data"]["room"]
    assert room["full"] is True and room["instances"] == 1


def test_production_is_counted_on_every_server(
    fleet: dict[str, FakeAgent], admin: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo.config import live

    _report(fleet["Riyadh"], ("prod-far", {"STAGE": "production"}))
    _report(fleet["Frankfurt"], ("dev-far", {"STAGE": "development"}))
    live.change({"max_production_instances": 1})
    try:
        client, headers = admin
        refused = client.patch("/api/v1/instances/dev-far", json={"stage": "production"}, headers=headers)
        assert refused.status_code == 409 and "prod-far" in refused.get_json()["detail"]
        assert not any(op == "http" and a["method"] == "PATCH" for op, a in fleet["Frankfurt"].calls)
    finally:
        live.change({"max_production_instances": None})


def test_updates_from_github_reach_instances_of_other_servers(fleet: dict[str, FakeAgent]) -> None:
    from sudoo import triggers

    riyadh = fleet["Riyadh"]
    _report(riyadh, ("auto-far", {"AUTO_UPDATE": "true"}), ("manual-far", {"AUTO_UPDATE": "false"}))
    triggers.request_update("auto-far", "push")
    triggers.request_update("manual-far", "push")
    paths = [a["path"] for op, a in riyadh.calls if op == "http"]
    assert paths == ["/api/v1/instances/auto-far/actions/update"]


# ---------------------------------------------------------------- moving


def _wait_job(job_id: str, seconds: float = 20) -> jobs.Job:
    deadline = time.time() + seconds
    while jobs.JOBS[job_id].status in ("queued", "running"):
        assert time.time() < deadline
        time.sleep(0.05)
    return jobs.JOBS[job_id]


def test_moving_an_instance_between_servers(
    fleet: dict[str, FakeAgent], admin: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(move, "POLL_SECONDS", 0.01)
    riyadh, frankfurt = fleet["Riyadh"], fleet["Frankfurt"]
    _report(riyadh, ("shop-prod", {"STAGE": "production", "ODOO_VERSION": "19.0", "EDITION": "community",
                                   "DB_NAME": "shop_prod", "GIT_BRANCH": "main", "PROJECT": "acme-shop"}))  # fmt: skip
    client, headers = admin
    unconfirmed = client.post("/api/v1/instances/shop-prod/move", json={"server": frankfurt.id}, headers=headers)
    assert unconfirmed.status_code == 400 and "production" in unconfirmed.get_json()["detail"]
    started = client.post("/api/v1/instances/shop-prod/move", json={"server": frankfurt.id,
                          "confirm_production": True}, headers=headers)  # fmt: skip
    assert started.status_code == 202, started.get_json()
    job = _wait_job(started.get_json()["data"]["id"])
    assert job.status == "success", "".join(job.log)
    actions = [a["path"].rsplit("/", 1)[1] for op, a in riyadh.calls if op == "http" and "/actions/" in a["path"]]
    assert actions == ["switch-off", "backup", "delete"]
    created = next(a for op, a in frankfurt.calls if op == "create")["plan"]
    assert created["name"] == "shop-prod" and created["moved"] and created["admin_password"] == "s3cret"
    assert created["stage"] == "production" and created["db_name"] == "shop_prod"
    assert created["from_backup"] in frankfurt.files and frankfurt.files[created["from_backup"]].startswith(b"PK")


def test_a_failed_move_is_rolled_back(
    fleet: dict[str, FakeAgent], admin: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(move, "POLL_SECONDS", 0.01)
    riyadh, frankfurt = fleet["Riyadh"], fleet["Frankfurt"]
    frankfurt.fail_create = True
    _report(riyadh, ("shop-dev", {"STAGE": "development", "ODOO_VERSION": "19.0"}))
    client, headers = admin
    started = client.post("/api/v1/instances/shop-dev/move", json={"server": frankfurt.id}, headers=headers)
    job = _wait_job(started.get_json()["data"]["id"])
    assert job.status == "failed" and "rolled back" in "".join(job.log)
    actions = [a["path"].rsplit("/", 1)[1] for op, a in riyadh.calls if op == "http" and "/actions/" in a["path"]]
    assert actions == ["switch-off", "backup", "start"]  # started again where it was, never deleted
    assert not [f for f in frankfurt.files if f.endswith(".zip")]  # the copy was removed
    assert "shop-dev" in riyadh.instances


# ---------------------------------------------------------------- on its own


def test_an_agent_keeps_working_without_the_panel_and_waits_for_it(monkeypatch: pytest.MonkeyPatch) -> None:
    servers.become_agent()
    try:
        agent.apply({"wait_minutes": 0.02, "locked": ["kept"], "settings": {}})
        channel = agent.Channel(app=None)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            channel.call_waiting("git_token", {"owner": "acme"})  # the panel is away: it waits, then gives up
        assert 1 <= time.monotonic() - started < 10
        # the panel comes back while a job waits: the call goes through
        answered: list[Any] = []

        async def fake_call(op: str, args: dict[str, Any], timeout: float = 60) -> Any:
            return {"token": "t-123"}

        import asyncio

        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        channel.loop = loop
        monkeypatch.setattr(channel, "call", fake_call)
        agent.apply({"wait_minutes": 1, "locked": ["kept"], "settings": {}})
        waiter = threading.Thread(target=lambda: answered.append(channel.call_waiting("git_token", {"owner": "a"})))
        waiter.start()
        time.sleep(0.2)
        channel.up.set()
        waiter.join(5)
        assert answered == [{"token": "t-123"}]
        loop.call_soon_threadsafe(loop.stop)
        # automatic work uses what the panel pushed last: a locked instance is never deleted automatically
        from sudoo.projects import access

        assert access.instance_lock("kept") is not None
    finally:
        servers._ROLE[0] = "panel"
        servers.PANEL.clear()
