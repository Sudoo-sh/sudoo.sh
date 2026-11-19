"""Several servers: trust (keys, join codes, signed channel), the list of servers (join, approval, revocation, limits
from the plan), the agent's side (what it answers, as whom) and the channel between them."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from sudoo import entitlements, servers
from sudoo.servers import agent, inventory, links, registry, trust
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(registry, "panel_url", lambda: "https://panel.example.com")
    for key in list(storage().all(registry.COLLECTION)):
        storage().delete(registry.COLLECTION, key)
    inventory.reset()
    for key in list(storage().all(inventory.COLLECTION)):
        storage().delete(inventory.COLLECTION, key)
    agent.leave()
    yield
    servers.PANEL.clear()
    servers._ROLE[0] = "panel"
    inventory.reset()
    for key in list(storage().all(inventory.COLLECTION)):
        storage().delete(inventory.COLLECTION, key)
    for key in list(storage().all(registry.COLLECTION)):
        storage().delete(registry.COLLECTION, key)


def _plan(max_servers: int | None) -> entitlements.Current:
    values = {**entitlements.FREE, "plan": "pro" if max_servers else "business", "max_servers": max_servers}
    return entitlements.Current(values, "hub", linked=True)


def _joined(name: str = "Riyadh") -> tuple[dict[str, Any], Any]:
    """A server that joined (not approved yet), and its agent's key."""
    record, code_text = registry.create(name, "me-central", None, "admin1")
    code = trust.JoinCode.decode(code_text)
    key = trust.new_key()
    public = trust.public_text(key)
    signature = trust.sign(key, trust.join_message(code.server_id, code.secret, public))
    return registry.join(code.server_id, code.secret, public, signature, {"hostname": "box", "cpus": 4}), key


# ---------------------------------------------------------------- trust


def test_fingerprints_and_join_codes_round_trip() -> None:
    key = trust.new_key()
    public = trust.public_text(key)
    found = trust.fingerprint(public)
    assert trust.FINGERPRINT_RE.match(found)
    assert trust.normalize_fingerprint(found.lower().replace("-", " ")) == found
    assert trust.normalize_fingerprint("not one") == ""
    code = trust.JoinCode("https://panel.example.com", "srv-abcdefghij", trust.new_secret(), "a" * 64)
    assert trust.JoinCode.decode(code.encode()) == code
    for broken in ("", "sudoo-join-v1.@@@", code.encode()[:-3] + "xyz", "hello"):
        with pytest.raises(ValueError):
            trust.JoinCode.decode(broken)


def test_frames_refuse_tampering_replay_reordering_and_reflection() -> None:
    panel, agent_key = trust.new_key(), trust.new_key()
    sender = trust.Seal(agent_key, panel.public_key(), "s1", trust.AGENT)
    receiver = trust.Seal(panel, agent_key.public_key(), "s1", trust.PANEL)
    first, second = sender.seal({"t": "report", "n": 1}), sender.seal({"t": "report", "n": 2})
    assert receiver.open(first) == {"t": "report", "n": 1}
    with pytest.raises(trust.ChannelError):
        receiver.open(first)  # replayed
    tampered = json.loads(second)
    tampered["p"] = tampered["p"].replace('"n":2', '"n":3')
    with pytest.raises(trust.ChannelError):
        receiver.open(json.dumps(tampered))
    unsigned = {**json.loads(second), "g": ""}
    with pytest.raises(trust.ChannelError):
        receiver.open(json.dumps(unsigned))
    assert receiver.open(second)["n"] == 2
    third = sender.seal({"n": 3})
    sender.seal({"n": 4})
    with pytest.raises(trust.ChannelError):
        receiver.open(sender.seal({"n": 5}))  # 4 never arrived: out of order
    # a frame the panel sent, sent back to it, is not the agent's
    own = trust.Seal(panel, agent_key.public_key(), "s1", trust.PANEL)
    with pytest.raises(trust.ChannelError):
        trust.Seal(panel, panel.public_key(), "s1", trust.PANEL).open(own.seal({"t": "x"}))
    other_session = trust.Seal(panel, agent_key.public_key(), "s2", trust.PANEL)
    with pytest.raises(trust.ChannelError):
        other_session.open(third)


def test_connection_requests_are_signed_fresh_and_used_once() -> None:
    key = trust.new_key()
    headers, nonce = trust.connect_headers(key, "srv-abcdefghij")
    nonces = trust.NonceCache()
    assert trust.check_connect(headers, key.public_key(), nonces) == ("srv-abcdefghij", nonce)
    with pytest.raises(trust.ChannelError, match="replayed"):
        trust.check_connect(headers, key.public_key(), nonces)
    with pytest.raises(trust.ChannelError, match="signed"):
        trust.check_connect(trust.connect_headers(trust.new_key(), "srv-abcdefghij")[0], key.public_key(), nonces)
    old, _ = trust.connect_headers(key, "srv-abcdefghij", now=time.time() - 600)
    with pytest.raises(trust.ChannelError, match="clock"):
        trust.check_connect(old, key.public_key(), nonces)
    panel = trust.new_key()
    text, session = trust.welcome(panel, "srv-abcdefghij", nonce, {"hello": 1})
    assert trust.check_welcome(text, panel.public_key(), "srv-abcdefghij", nonce) == ({"hello": 1}, session)
    with pytest.raises(trust.ChannelError):
        trust.check_welcome(text, trust.new_key().public_key(), "srv-abcdefghij", nonce)  # not the pinned key
    with pytest.raises(trust.ChannelError):
        trust.check_welcome(text, panel.public_key(), "srv-abcdefghij", "another-nonce-123456")


# ---------------------------------------------------------------- the list of servers


def test_join_needs_the_right_unused_unexpired_code_and_a_proof_of_the_key() -> None:
    record, code_text = registry.create("Dubai", "", {"max_instances": 5}, "admin1")
    assert record["status"] == registry.WAITING and "code_hash" in record
    code = trust.JoinCode.decode(code_text)
    assert code.panel_key_sha256 == trust.key_sha256(registry.panel_public())
    key = trust.new_key()
    public = trust.public_text(key)
    good = trust.sign(key, trust.join_message(code.server_id, code.secret, public))
    with pytest.raises(registry.Refused) as wrong:
        registry.join(code.server_id, trust.new_secret(), public, trust.sign(key, trust.join_message(
            code.server_id, trust.new_secret(), public)), {})  # fmt: skip
    assert wrong.value.status in (400, 403)
    with pytest.raises(registry.Refused, match="not signed"):
        registry.join(code.server_id, code.secret, public, trust.sign(trust.new_key(), b"x"), {})
    with pytest.raises(registry.Refused, match="not valid"):
        registry.join(code.server_id, code.secret, public, good, {}, now=time.time() + 3 * 3600)  # expired
    joined = registry.join(code.server_id, code.secret, public, good, {"hostname": "h", "cpus": 2, "evil": "x"})
    assert joined["status"] == registry.JOINED and joined["fingerprint"] == trust.fingerprint(public)
    assert joined["facts"] == {"hostname": "h", "cpus": 2}
    with pytest.raises(registry.Refused, match="not valid"):
        registry.join(code.server_id, code.secret, public, good, {})  # used


def test_approval_needs_the_fingerprint_the_server_printed() -> None:
    record, key = _joined()
    with pytest.raises(registry.Refused, match="not the fingerprint"):
        registry.approve(record["id"], trust.fingerprint(trust.public_text(trust.new_key())), "admin1")
    assert registry.public_key_of(record["id"]) is None  # not active yet: its channel is refused
    approved = registry.approve(record["id"], record["fingerprint"].replace("-", "").lower(), "admin1")
    assert approved["status"] == registry.ACTIVE and approved["approved_by"] == "admin1"
    assert registry.public_key_of(record["id"]) is not None
    revoked = registry.revoke(record["id"], "admin1")
    assert revoked["status"] == registry.REVOKED and registry.public_key_of(record["id"]) is None
    with pytest.raises(registry.Refused, match="Move or delete"):
        registry.remove(record["id"], has_instances=True)
    registry.remove(record["id"], has_instances=False)
    assert registry.get(record["id"]) is None


def test_the_plan_limits_the_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entitlements, "current", lambda: _plan(2))  # Pro: 2 servers, the main one included
    _joined("One")
    with pytest.raises(registry.Refused) as refused:
        registry.create("Two", "", None, "admin1")
    assert refused.value.status == 403 and "Pro plan allows 2 servers" in refused.value.message
    assert "plans at" in refused.value.message  # where to get more
    monkeypatch.setattr(entitlements, "current", lambda: _plan(None))  # Business: no limit
    registry.create("Two", "", None, "admin1")
    assert entitlements.usage()["servers"] == 2


def test_a_join_beyond_the_plan_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    record, code_text = registry.create("Late", "", None, "admin1")
    monkeypatch.setattr(entitlements, "current", lambda: _plan(1))  # downgraded before the server joined
    code = trust.JoinCode.decode(code_text)
    key = trust.new_key()
    public = trust.public_text(key)
    with pytest.raises(registry.Refused, match="allows 1 server"):
        registry.join(code.server_id, code.secret, public, trust.sign(key, trust.join_message(
            code.server_id, code.secret, public)), {})  # fmt: skip


# ---------------------------------------------------------------- the API


def test_servers_api_adds_approves_and_revokes(admin: Client, operator: Client) -> None:
    client, headers = admin
    assert operator[0].post("/api/v1/servers", json={"name": "X"}, headers=operator[1]).status_code == 403
    added = client.post("/api/v1/servers", json={"name": "Frankfurt 2", "region": "eu-central"}, headers=headers)
    assert added.status_code == 201, added.get_json()
    data = added.get_json()["data"]
    assert data["join_code"].startswith(trust.JOIN_PREFIX)
    assert "sudoo-install.sh | sudo bash -s -- agent --join" in data["install_command"]
    assert data["status"] == "waiting" and "code_hash" not in data
    code = trust.JoinCode.decode(data["join_code"])
    key = trust.new_key()
    public = trust.public_text(key)
    plain = FlaskClient(client.application)
    body = {"server": code.server_id, "secret": code.secret, "public_key": public,
            "signature": trust.sign(key, trust.join_message(code.server_id, code.secret, public)),
            "facts": {"hostname": "fra2"}}  # fmt: skip
    joined = plain.post("/api/v1/servers/join", json=body)  # no session: the new server's agent
    assert joined.status_code == 201, joined.get_json()
    assert joined.get_json()["data"]["panel_key"] == registry.panel_public()
    assert plain.post("/api/v1/servers/join", json=body).status_code == 403  # the code is used up
    listed = operator[0].get("/api/v1/servers").get_json()
    assert [s["id"] for s in listed["data"]] == ["main", code.server_id]
    assert "fingerprint" not in operator[0].get(f"/api/v1/servers/{code.server_id}").get_json()["data"]
    detail = client.get(f"/api/v1/servers/{code.server_id}").get_json()["data"]
    assert detail["fingerprint"] == trust.fingerprint(public) and detail["status"] == "joined"
    wrong = client.post(f"/api/v1/servers/{code.server_id}/approve", json={"fingerprint": "0000-0000-0000-0000"},
                        headers=headers)  # fmt: skip
    assert wrong.status_code == 400
    approved = client.post(f"/api/v1/servers/{code.server_id}/approve", json={"fingerprint": detail["fingerprint"]},
                           headers=headers)  # fmt: skip
    assert approved.get_json()["data"]["status"] == "active"
    assert client.post(f"/api/v1/servers/{code.server_id}/revoke", json={"confirm": "no"},
                       headers=headers).status_code == 400  # fmt: skip
    revoked = client.post(f"/api/v1/servers/{code.server_id}/revoke", json={"confirm": "Frankfurt 2"},
                          headers=headers)  # fmt: skip
    assert revoked.get_json()["data"]["status"] == "revoked"
    assert client.delete(f"/api/v1/servers/{code.server_id}", headers=headers).status_code == 204
    renamed = client.patch("/api/v1/servers/main", json={"name": "Amman", "region": "jo"}, headers=headers)
    assert renamed.get_json()["data"]["name"] == "Amman"
    client.patch("/api/v1/servers/main", json={"name": "Main", "region": ""}, headers=headers)


# ---------------------------------------------------------------- the agent's side


def test_the_agent_answers_only_requests_passed_on_by_the_panel(app: Flask) -> None:
    servers.become_agent()
    try:
        plain = app.test_client()
        assert plain.get("/api/v1/versions").status_code == 404  # nothing from the network
        viewer = {"name": "ada", "role": "super_admin", "permissions": ["instances.view"]}
        answer = agent.answer_call(app, "http", {"method": "GET", "path": "/api/v1/versions", "user": viewer})
        assert answer["status"] == 200
        refused = agent.answer_call(app, "http", {"method": "GET", "path": "/api/v1/audit", "user": viewer})
        assert refused["status"] == 403  # a super admin, but the panel gave only instances.view
        with pytest.raises(PermissionError):
            agent.answer_call(app, "file.read", {"area": "backups", "name": "../manager/vault.key"})
        with pytest.raises(PermissionError):
            agent.answer_call(app, "file.read", {"area": "manager", "name": "x__y__20260101-000000.zip"})
    finally:
        servers._ROLE[0] = "panel"


def test_the_agent_applies_the_panels_decisions() -> None:
    from sudoo import link_deadline
    from sudoo.config import settings
    from sudoo.instances import stages
    from sudoo.projects import access

    servers.become_agent()
    try:
        agent.apply(
            {
                "settings": {"idle_hours": 3.0},
                "entitlements": {"values": {"plan": "pro", "max_production_branches": 5}, "source": "hub",
                                 "linked": True},  # fmt: skip
                "production_instances": ["prod-elsewhere"],
                "production_keys": ["p1/main"],
                "locked": ["locked-one"],
                "link_restricted": True,
            }
        )
        assert settings.idle_hours == 3.0
        assert "prod-elsewhere" in stages.production_instances()
        assert access.instance_lock("locked-one") is not None and access.instance_lock("free-one") is None
        assert link_deadline.restricted()
    finally:
        servers._ROLE[0] = "panel"
        from sudoo.config import live

        live.use({})


# ---------------------------------------------------------------- the channel, end to end in one process


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def panel_channel(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The panel's channel endpoint on a real port, served on the gateway's event loop."""
    from aiohttp import web

    from sudoo.instances import gateway

    if not gateway.loop().is_running():
        threading.Thread(target=gateway._run_loop, daemon=True, name="test-gateway").start()
        while not gateway.loop().is_running():
            time.sleep(0.01)
    port = _free_port()
    application = web.Application()
    links.add_routes(application)
    runner = web.AppRunner(application)

    async def serve() -> None:
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()

    gateway.submit(serve()).result(5)
    monkeypatch.setattr(registry, "panel_url", lambda: f"http://127.0.0.1:{port}")
    yield f"http://127.0.0.1:{port}"
    gateway.submit(runner.cleanup()).result(5)


def _join_through(app: Flask, monkeypatch: pytest.MonkeyPatch, code: str) -> dict[str, Any]:
    plain = app.test_client()

    def post(url: str, body: dict[str, Any], timeout: float = 30) -> tuple[int, dict[str, Any]]:
        answer = plain.post("/" + url.split("://", 1)[1].split("/", 1)[1], json=body)
        return answer.status_code, answer.get_json()

    monkeypatch.setattr(agent, "_post", post)
    return agent.join(code)


def _wait(condition: Any, seconds: float = 10) -> None:
    deadline = time.time() + seconds
    while not condition():
        assert time.time() < deadline, "timed out"
        time.sleep(0.05)


def test_an_agent_joins_waits_for_approval_connects_answers_and_is_revoked(
    app: Flask, panel_channel: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo.instances import gateway

    record, code = registry.create("Edge", "eu", None, "admin1")
    found = _join_through(app, monkeypatch, code)
    assert found["fingerprint"] == registry.get(record["id"])["fingerprint"]

    async def quiet(self: agent.Channel) -> None:  # the same process: the panel's own events are not passed back
        await asyncio.sleep(3600)

    monkeypatch.setattr(agent.Channel, "pass_events", quiet)
    monkeypatch.setattr(agent, "report", lambda: {"t": "report", "facts": {"hostname": "edge"}, "instances": [
        {"name": "edge-one", "resource": {"name": "edge-one", "status": "online"}, "env": {"STAGE": "staging"}}],
        "jobs": [{"id": "abc123", "instance": "edge-one", "steps": [], "started_at": "2026-11-18T10:00:00Z"}],
        "metrics": {}, "capacity": {"comfortable": 4}})  # fmt: skip
    monkeypatch.setattr(agent, "answer_call", lambda _app, op, args: {"op": op, "echo": args.get("x")})
    channel = agent.Channel(app)
    task = gateway.submit(channel.run_forever())
    try:
        _wait(lambda: agent.read_state().get("status") == "joined")  # refused until approved (409)
        assert not links.connected(record["id"])
        registry.approve(record["id"], found["fingerprint"], "admin1")
        _wait(lambda: links.connected(record["id"]), 30)
        _wait(lambda: inventory.remote("edge-one") is not None)
        assert inventory.env("edge-one") == {"STAGE": "staging"} and inventory.server_of("edge-one") == record["id"]
        assert inventory.job_server("abc123") == record["id"]
        assert links.call(record["id"], "ping", {"x": 7}) == {"op": "ping", "echo": 7}
        _wait(lambda: servers.PANEL.get("server", {}).get("id") == record["id"])  # the panel's decisions arrived
        registry.revoke(record["id"], "admin1")
        links.close(record["id"], "revoked")
        _wait(lambda: agent.read_state().get("status") == "revoked")
        _wait(lambda: not links.connected(record["id"]))
        with pytest.raises(links.Offline):
            links.call(record["id"], "ping", {})
    finally:
        task.cancel()
        servers.PANEL.clear()


def test_a_join_code_for_another_panel_key_is_refused(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    record, code_text = registry.create("Spoofed", "", None, "admin1")
    code = trust.JoinCode.decode(code_text)
    forged = trust.JoinCode(code.panel_url, code.server_id, code.secret, "0" * 64).encode()
    with pytest.raises(ValueError, match="not the one in the join code"):
        _join_through(app, monkeypatch, forged)
    assert not agent.read_state().get("server_id")
