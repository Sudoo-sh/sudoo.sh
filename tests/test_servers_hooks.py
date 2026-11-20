"""Several servers, the core's part (premium/servers.py, premium/agent.py): without the premium package the panel is
a one-server panel that answers 402 for another server; in a server's agent the core runs only the requests the main
panel passes on and takes the panel's decisions; premium packages reach an agent bound to the panel it joined."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from fake_hub import FakeHub, PremiumBuild, make_premium_package
from sudoo import entitlements, link_deadline, roles
from sudoo.config import live
from sudoo.core import jobs
from sudoo.hub import client, crypto, marketplace
from sudoo.premium import agent, keys, loader, servers
from sudoo.projects import access
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]


@pytest.fixture
def as_agent() -> Iterator[None]:
    servers.become_agent()
    yield
    servers._ROLE[0] = "panel"
    servers.PANEL.clear()


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, tuple[Any, ...]]]:
    calls: list[tuple[Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any) -> Any:
        calls.append((fn, args))
        return jobs.Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    from sudoo.limits import quotas

    monkeypatch.setattr(quotas, "claim", lambda user: None)
    return calls


def test_one_server_without_the_package(admin: Client, started: list[Any]) -> None:
    web, headers = admin
    plan = web.get("/api/v1/instance-plan?version=18.0&edition=community").get_json()["data"]
    assert plan["servers"] == [{"id": "main", "name": "Main", "region": ""}]
    body = {"version": "18.0", "edition": "community", "delete_after_days": 0}
    refused = web.post("/api/v1/instances", json={**body, "server": "srv-abcdefghij"}, headers=headers)
    assert refused.status_code == 402 and "Business" in refused.get_json()["detail"]
    assert not started
    answer = web.post("/api/v1/instances", json={**body, "server": "main"}, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    from sudoo.instances import store

    store.release_ports(started[-1][1][0]["ports"])
    assert web.get("/api/v1/servers", headers=headers).status_code == 402
    assert web.post("/api/v1/instances/shop/move", json={"server": "x"}, headers=headers).status_code == 402
    assert servers.reference() == {"id": "main", "name": "Main", "region": "", "online": True}
    live.change({"server_name": "Frankfurt 1", "server_region": "eu-central"})
    try:
        assert servers.choices() == [{"id": "main", "name": "Frankfurt 1", "region": "eu-central"}]
    finally:
        live.change({"server_name": None, "server_region": None})
    assert servers.counted() == 1 and entitlements.usage()["servers"] == 1
    assert servers.remote("shop") is None and servers.remote_names() == [] and servers.remote_jobs() == []


def test_servers_and_moves_have_their_permissions() -> None:
    assert {"servers.manage", "instances.move"} <= set(roles.PERMISSIONS)
    assert {"servers.manage", "instances.move"} <= roles.permissions("super_admin")
    assert "servers.manage" not in roles.permissions("maintainer")
    assert "instances.move" in access.PROJECT_PERMISSIONS


def test_an_agent_runs_only_what_the_panel_passes_on(app: Flask, as_agent: None) -> None:
    web = app.test_client()
    assert web.get("/api/v1/instances").status_code == 404  # no session, no token: an agent has no web page
    who = {"name": "admin1", "role": "admin", "permissions": ["instances.view"], "via": "panel"}
    answer = web.get("/api/v1/instances", environ_base={servers.FORWARDED_ENV: who})
    assert answer.status_code == 200, answer.get_json()
    assert web.post("/api/v1/instances", json={}, environ_base={servers.FORWARDED_ENV: who}).status_code == 403
    assert access.role_permissions(who) == {"instances.view"}
    # the panel's decisions: plan, link deadline, locks, production elsewhere
    servers.PANEL.update(
        entitlements={"values": {"plan": "business", "max_servers": None}, "source": "hub", "linked": True},
        link_restricted=True,
        locked=["shop"],
        production_instances=["far-away"],
    )
    found = entitlements.current()
    assert found.values["plan"] == "business" and found.linked and found.source == "hub"
    assert link_deadline.status()["restricted"] is True
    assert access.instance_lock("shop") == {"locked_by": "panel"} and access.instance_lock("other") is None
    from sudoo.instances import stages

    assert "far-away" in stages.production_instances()


def test_a_forwarded_identity_means_nothing_in_the_panel() -> None:
    who = {"name": "x", "role": "viewer", "permissions": ["settings.manage"], "via": "panel"}
    assert "settings.manage" not in access.role_permissions(who)


def test_the_join_code_names_the_panel() -> None:
    body = crypto.b64url(json.dumps({"v": 1, "u": "https://panel.example.com/", "i": "srv-abcdefghij"}).encode())
    assert agent.panel_url_of(f"sudoo-join-v1.{body}") == "https://panel.example.com"
    for broken in ("", "hello", "sudoo-join-v1.@@", "sudoo-join-v1." + crypto.b64url(b'{"u": "ftp://x"}')):
        with pytest.raises(agent.AgentError):
            agent.panel_url_of(broken)


def test_status_of_a_server_that_never_joined(as_agent: None, capsys: pytest.CaptureFixture[str]) -> None:
    agent.main("status", "", True)
    assert json.loads(capsys.readouterr().out) == {"joined": False}
    with pytest.raises(SystemExit, match="no verified package"):
        agent.main("run", "", False)


# ---------------------------------------------------------------- premium packages for an agent


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHub]:
    fake = FakeHub().start()
    monkeypatch.setattr(client, "hub_url", lambda: fake.url)
    monkeypatch.setattr(keys, "SHIPPED_PREMIUM_KEYS", (fake.premium_key.public,))
    monkeypatch.setattr(keys, "SHIPPED_PREMIUM_BINDING_KEYS", ())
    monkeypatch.setattr(keys, "SHIPPED_PREMIUM_BINDING_KEYS", (fake.binding_key.public,))
    _clean()
    live.change({"marketplace": True})
    fake.entitlements = {"plan": "business", "max_servers": None, "premium": ["servers"]}
    fake.premium["servers"] = PremiumBuild("servers", "1.0.0", make_premium_package("servers", "1.0.0"))
    yield fake
    live.change({"marketplace": None})
    fake.stop()
    _clean()


def _clean() -> None:
    for collection in (loader.COLLECTION, loader.STATE_COLLECTION, client.COLLECTION):
        for key in storage().all(collection):
            storage().delete(collection, key)
    shutil.rmtree(loader.premium_dir(), ignore_errors=True)
    loader.agent_identity_file().unlink(missing_ok=True)
    loader.LOADED = set()
    for name in [m for m in sys.modules if m.startswith("sudoo_premium_")]:
        del sys.modules[name]
    sys.path[:] = [p for p in sys.path if "/premium/" not in p]


def _panel_runs_servers() -> None:
    import time

    marketplace.heartbeat()
    job = loader.start_sync("test")
    assert job is not None
    while job.finished is None:
        time.sleep(0.05)
    assert job.status == "success", "".join(job.log)
    loader.LOADED = None
    assert loader.activate() == {"servers"}


def rec_binding_end(rec: dict[str, Any]) -> float:
    key = keys.trusted(role=keys.BINDING)[rec["binding_key_id"]]
    return float(crypto.read_binding(rec["binding"], rec["binding_signature"], key)["exp"])


def test_an_agent_gets_the_panels_packages_bound_to_that_panel(hub: FakeHub, tmp_path: Path) -> None:
    _panel_runs_servers()
    package, headers = loader.build_for_agent("servers", "cp315", "aarch64-linux-musl")
    assert headers["X-Sudoo-Premium-Python-Tag"] == "py3"  # the panel runs a pure build: the agent gets it too
    again, _ = loader.build_for_agent("servers", "cp315", "aarch64-linux-musl")
    assert again == package and hub.premium_requests.count("servers/download") == 2  # the panel's own, and one kept
    panel_id, panel_fingerprint = loader.server_id(), loader.fingerprint()
    copy = tmp_path / "servers.zip"
    shutil.copy(package, copy)
    sent = {k.lower(): v for k, v in headers.items()}

    # now on the agent's server: its own storage, nothing installed
    for key in storage().all(loader.COLLECTION):
        storage().delete(loader.COLLECTION, key)
    shutil.rmtree(loader.premium_dir(), ignore_errors=True)
    servers.become_agent()
    try:
        broken = {**sent, "x-sudoo-signature": sent["x-sudoo-signature"][:-4] + "AAAA"}
        with pytest.raises(loader.PremiumRefused, match="signature"):
            loader.install_for_agent(copy, broken)
        rec = loader.install_for_agent(copy, sent)
        assert rec["id"] == "servers"
        assert loader.agent_identity() == {"server_id": panel_id, "hardware_fingerprint": panel_fingerprint}
        loader.LOADED = None
        assert loader.activate() == {"servers"}  # the panel decides, not a plan of the agent's own
        # the joined panel states its identity over its signed channel: the same one, then another one
        assert loader.confirm_agent_identity(panel_id, panel_fingerprint) and loader.binding_problem(rec) is None
        assert not loader.confirm_agent_identity("other", "e" * 64)
        assert loader.binding_problem(rec) == "it is bound to another panel than the one this server joined"
        assert loader.confirm_agent_identity(panel_id, panel_fingerprint) and loader.binding_problem(rec) is None
        # a clock set back does not keep an ended binding running: never earlier than the panel's latest signed time
        import time

        ends = rec_binding_end(rec)
        loader.agent_time_seen(ends + 60)
        assert loader.binding_problem(rec) is not None and "ended" in str(loader.binding_problem(rec))
        loader.agent_time_seen("not a time")
        loader.agent_time_seen(time.time())  # never moves back
        assert "ended" in str(loader.binding_problem(rec))
        identity = loader.agent_identity()
        identity.pop("clock_floor")
        loader.agent_identity_file().write_text(json.dumps(identity))
        assert loader.binding_problem(rec) is None
        # bound to another panel: refused
        loader.agent_identity_file().write_text(json.dumps({"server_id": "other", "hardware_fingerprint": "f" * 64}))
        assert loader.binding_problem(rec) == "it was installed for another server"
        loader.agent_identity_file().unlink()
        assert loader.binding_problem(rec) == "this server's agent has not joined a panel"
        assert loader.forget_agent_packages() and not loader.records()
    finally:
        servers._ROLE[0] = "panel"


def test_a_joining_agent_learns_a_rotated_binding_key_only_from_its_endorsement(
    hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The agent ships an older binding key (or none): the panel's list of packages carries the hub's premium keys,
    and the agent takes the new binding key from it only because a release key it trusts endorsed it."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from fake_hub import HubKey
    from sudoo.premium import agent

    _panel_runs_servers()
    package, headers = loader.build_for_agent("servers", "cp315", "aarch64-linux-musl")
    body, sent = package.read_bytes(), {k.lower(): v for k, v in headers.items()}
    listing: dict[str, Any] = {"data": [{"id": "servers"}]}

    def answer(url: str, headers: Any, timeout: float = 120) -> tuple[bytes, dict[str, str]]:
        if url.split("?")[0].endswith("/agent-packages"):
            return json.dumps(listing).encode(), {}
        return body, sent

    monkeypatch.setattr(agent, "_get", answer)
    # now on the agent's server: its own storage, the binding key it ships is not the hub's current one
    for key in storage().all(loader.COLLECTION):
        storage().delete(loader.COLLECTION, key)
    shutil.rmtree(loader.premium_dir(), ignore_errors=True)
    storage().delete(keys.STATE_COLLECTION, "keys")
    monkeypatch.setattr(keys, "SHIPPED_PREMIUM_BINDING_KEYS", (HubKey(Ed25519PrivateKey.generate()).public,))
    servers.become_agent()
    try:
        with pytest.raises(agent.AgentError, match="does not trust"):
            agent.fetch_packages("https://panel.example.com", dict)
        # passed on with an endorsement by a key that is not a trusted release key: still refused
        stranger = HubKey(Ed25519PrivateKey.generate())
        forged = [dict(item) for item in hub.premium_keys()]
        statement = (f"sudoo-premium-binding-key-v1\n{hub.binding_key.key_id}\n{hub.binding_key.public}\n"
                     f"{hub.binding_key.not_before}").encode()  # fmt: skip
        forged[1].update(endorsed_by=forged[0]["key_id"], endorsement=stranger.sign(statement))
        listing["keys"] = forged
        with pytest.raises(agent.AgentError, match="does not trust"):
            agent.fetch_packages("https://panel.example.com", dict)
        assert hub.binding_key.key_id not in keys.trusted(role=keys.BINDING)
        # the hub's list as it is: the endorsement verifies with the release key the agent ships
        listing["keys"] = hub.premium_keys()
        assert agent.fetch_packages("https://panel.example.com", dict) == ["servers"]
        assert hub.binding_key.key_id in keys.trusted(role=keys.BINDING)
        assert hub.binding_key.key_id not in keys.trusted(role=keys.RELEASE)  # it never signs a package
    finally:
        servers._ROLE[0] = "panel"
        storage().delete(keys.STATE_COLLECTION, "keys")
