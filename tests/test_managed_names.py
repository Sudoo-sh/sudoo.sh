"""Managed subdomains (instances/managed_names.py): every instance gets <branch>.<stage>.<company>.<base> from the hub,
served with the panel's own certificates whatever CUSTOM_DOMAINS says; the name follows the branch and stage and is
released with the instance. Against the fake hub's managed subdomains (fake_hub.py) and the stand-in ACME authority
(fake_acme.py)."""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

from fake_acme import BASE, FakeAcme
from fake_hub import FakeHub
from sudoo import exposure
from sudoo.config import live
from sudoo.core.state import STATE
from sudoo.hub import client, marketplace
from sudoo.instances import acme, certificates, custom_domains, domain_worker, gateway, managed_names, store
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
SHOP, DEMO = "shop-main", "shop-demo"


def _instance(name: str, branch: str, stage: str) -> None:
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    (folder / "config").mkdir(exist_ok=True)
    (folder / "config" / "odoo.conf").write_text("[options]\ndb_host = db\n")
    store.write_env(name, {"GATEWAY_PORT": 1, "ODOO_PORT": 2, "STAGE": stage, "GIT_BRANCH": branch,
                           "ODOO_VERSION": "19.0", "EDITION": "community", "DB_NAME": name.replace("-", "_"),
                           "AUTO_UPDATE": "false", "AUTO_UPGRADE": "false", "AUTO_PAUSE": "false",
                           "DELETE_AFTER_DAYS": "0", "CREATED_AT": int(time.time())})  # fmt: skip


def _clean() -> None:
    for name in (SHOP, DEMO):
        custom_domains.forget_instance(name)
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
    for key in list(storage().all(custom_domains.COLLECTION)):
        storage().delete(custom_domains.COLLECTION, key)
    custom_domains.forget_cache()
    for key in list(storage().all(managed_names.STATE_COLLECTION)):
        storage().delete(managed_names.STATE_COLLECTION, key)


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHub]:
    fake = FakeHub().start()
    monkeypatch.setattr(client, "hub_url", lambda: fake.url)
    monkeypatch.setattr(gateway, "refresh_domains", lambda: None)
    monkeypatch.setattr(exposure, "default_address", lambda name: f"https://{name}.example.net")
    for key in list(storage().all(client.COLLECTION)):
        storage().delete(client.COLLECTION, key)
    _clean()
    live.change({"marketplace": True, "public_ipv4": "1.2.3.4", "domain_http_probe": False})
    _instance(SHOP, "main", "production")
    marketplace.heartbeat()  # registers, and tells the hub this server's public address
    yield fake
    _clean()
    live.change({"marketplace": None, "public_ipv4": None, "domain_http_probe": None, "managed_subdomains": None,
                 "managed_subdomain_company": None})  # fmt: skip
    fake.stop()


@pytest.fixture
def authority(monkeypatch: pytest.MonkeyPatch) -> FakeAcme:
    fake = FakeAcme(certificates.challenge_answer)
    monkeypatch.setattr(domain_worker, "TRANSPORT", fake)
    monkeypatch.setattr(acme, "DIRECTORIES", {"production": f"{BASE}/directory", "staging": f"{BASE}/directory"})
    monkeypatch.setattr(acme, "POLL_SECONDS", 0.0)
    return fake


def test_labels_are_made_the_way_the_hub_takes_them() -> None:
    assert managed_names.label("Feature/ABC_x") == "feature-abc-x"
    assert managed_names.label("--main--") == "main"
    assert managed_names.label("xn--pine") == "xn-pine"  # never "--" in places 3 and 4
    assert managed_names.label("..") == "" and managed_names.label("") == ""
    assert len(managed_names.label("a" * 80)) == 63


def test_an_instance_gets_its_name_and_serves_it_with_its_own_certificate(
    hub: FakeHub, authority: FakeAcme, admin: Client
) -> None:
    assert custom_domains.settings.custom_domains == "off"  # managed names need no custom domains
    found = managed_names.sync()
    hostname = "main.production.acme.sudoo.test"
    assert found["enabled"] and found["base_domain"] == "sudoo.test" and found["company"] == "acme"
    assert set(hub.subdomains) == {hostname} and managed_names.on()
    record = custom_domains.get(hostname)
    assert record and record["managed"] and record["instance"] == SHOP and record["status"] == "waiting_dns"
    assert custom_domains.for_instance(SHOP) == []  # never listed among the custom domains
    assert custom_domains.gateway_hosts() == {hostname: {"instance": SHOP, "certificate": False}}
    assert exposure.address(SHOP) == f"https://{SHOP}.example.net"  # not before it has a certificate
    assert domain_worker.round_() == 1
    issued = custom_domains.get(hostname) or {}
    assert issued["status"] == "active" and certificates.present(hostname)
    assert exposure.address(SHOP) == f"https://{hostname}"
    web, _ = admin
    shown = web.get(f"/api/v1/instances/{SHOP}").get_json()["data"]
    assert shown["managed_name"] == {"hostname": hostname, "status": "active", "reason": None, "serving": True,
                                     "url": f"https://{hostname}"}  # fmt: skip
    assert shown["url"] == f"https://{hostname}"
    overview = web.get("/api/v1/server/managed-subdomains").get_json()["data"]
    assert overview["active"] and overview["base_domain"] == "sudoo.test" and overview["held"] == 1
    assert overview["addresses"] == {"public_ipv4": "1.2.3.4"}
    # people cannot remove it: it follows the instance
    with pytest.raises(custom_domains.Refused, match="managed name"):
        custom_domains.remove(hostname)
    # nothing more to do at the next check
    managed_names.sync()
    assert [r for r in hub.requests if r.startswith("POST /api/v1/installations/subdomains")] == [
        "POST /api/v1/installations/subdomains"
    ]


def test_the_name_follows_the_branch_and_stage_and_goes_with_the_instance(hub: FakeHub) -> None:
    managed_names.sync()
    store.update_env(SHOP, GIT_BRANCH="Release/2.0", STAGE="staging")
    managed_names.sync()
    assert set(hub.subdomains) == {"release-2-0.staging.acme.sudoo.test"}
    assert set(managed_names.records()) == {"release-2-0.staging.acme.sudoo.test"}
    # another instance of the same branch and stage: the first one keeps the name
    _instance(DEMO, "release/2.0", "staging")
    managed_names.sync()
    assert managed_names.of_instance(DEMO) is None
    # the instance is deleted: its name is released (a failed release is tried again)
    hub.subdomain_release_failures = 1
    custom_domains.forget_instance(SHOP)
    shutil.rmtree(store.folder(SHOP))
    after = managed_names.sync()
    assert after["pending_release"] == ["release-2-0.staging.acme.sudoo.test"] and hub.subdomains
    managed_names.sync()
    assert "release-2-0.staging.acme.sudoo.test" not in hub.subdomains
    assert managed_names.state()["pending_release"] == []
    assert managed_names.of_instance(DEMO) is not None  # now free for the other instance


def test_nothing_is_asked_while_the_hub_or_the_setting_is_off(hub: FakeHub, admin: Client) -> None:
    hub.subdomains_enabled = False
    found = managed_names.sync()
    assert not found["enabled"] and not managed_names.on() and not hub.subdomains and not managed_names.records()
    hub.subdomains_enabled = True
    live.change({"managed_subdomains": False})
    managed_names.sync()
    assert not hub.subdomains and not managed_names.on()
    live.change({"managed_subdomains": True, "managed_subdomain_company": "Pine-Tree"})
    web, headers = admin
    answer = web.post("/api/v1/server/managed-subdomains/sync", headers=headers)
    assert answer.status_code == 200 and answer.get_json()["data"]["company"] == "pine-tree"
    assert set(hub.subdomains) == {"main.production.pine-tree.sudoo.test"}


def test_a_refusal_is_kept_for_people(hub: FakeHub) -> None:
    hub.subdomain_limit = 0
    found = managed_names.sync()
    assert not managed_names.records() and "used up" in str(found["error"])


def test_a_name_from_the_hub_never_replaces_a_domain_of_this_server(hub: FakeHub) -> None:
    hostname = "main.production.acme.sudoo.test"
    _instance(DEMO, "demo", "development")
    customer = {"domain": hostname, "instance": DEMO, "primary": True, "tls": "acme", "managed": False,
                "status": "active", "added_by": "admin", "added_at": time.time()}  # fmt: skip
    storage().put(custom_domains.COLLECTION, hostname, customer)
    custom_domains.forget_cache()
    found = managed_names.sync()
    assert custom_domains.get(hostname) == customer  # still the customer's, on its own instance
    assert managed_names.of_instance(SHOP) is None
    assert any("already a domain of this server" in problem for problem in found["problems"])


def test_a_name_outside_the_hubs_own_domain_is_refused(hub: FakeHub, monkeypatch: pytest.MonkeyPatch) -> None:
    real = managed_names._hub

    def answer(method: str, path: str, body: object = None) -> object:
        found = real(method, path, body)
        if method == "POST":
            found.data["hostname"] = "shop.customer.example"
        return found

    monkeypatch.setattr(managed_names, "_hub", answer)
    found = managed_names.sync()
    assert custom_domains.get("shop.customer.example") is None and not managed_names.records()
    assert "outside its own domain" in str(found["error"])


def test_hub_calls_for_names_never_wait_for_the_daily_quota() -> None:
    storage().put(client.COLLECTION, client.QUOTA_KEY, {"until": time.time() + 3600})
    try:
        assert client.quota_wait("/api/v1/installations/subdomains") == 0
        assert client.quota_wait("/api/v1/installations/subdomains/main.production.acme.sudoo.test") == 0
        assert client.quota_wait("/api/v1/installations/premium?python_tag=cp315") == 0
        assert client.quota_wait("/api/v1/installations/catalog") > 0
    finally:
        storage().delete(client.COLLECTION, client.QUOTA_KEY)


def test_the_company_label_setting_must_be_a_label() -> None:
    with pytest.raises(ValueError):
        live.change({"managed_subdomain_company": "not a label"})


# ---------------------------------------------------------------- instances of other servers (premium "servers")

FAR = "far-shop"


class _OtherServers:
    """What the servers package answers the core (premium/servers.py), with one more server and its agent."""

    def __init__(self) -> None:
        self.instances: dict[str, tuple[str, dict[str, str]]] = {}  # name -> (server, env facts)
        self.addresses_of: dict[str, dict[str, str]] = {"srv-b": {"public_ipv4": "5.6.7.8"}}
        self.told: list[tuple[str, list[dict[str, str]], dict[str, str]]] = []
        self.certificates: dict[tuple[str, str], str] = {}

    def names(self) -> list[str]:
        return sorted(set(store.names()) | set(self.remote_names()))

    def remote(self, name: str) -> dict[str, Any] | None:
        if store.exists(name) or name not in self.instances:
            return None
        server, env = self.instances[name]
        return {"server": server, "env": env, "resource": {}}

    def server_of(self, name: str) -> str:
        found = self.remote(name)
        return found["server"] if found else "main"

    def remote_names(self) -> list[str]:
        return [n for n in self.instances if not store.exists(n)]

    def addresses(self, server_id: str) -> dict[str, str]:
        return self.addresses_of.get(server_id, {})

    def serve_names(self, server_id: str, certificates: dict[str, str]) -> None:
        self.told.append((server_id, managed_names.for_server(server_id), certificates))

    def certificate(self, server_id: str, domain: str) -> str | None:
        return self.certificates.get((server_id, domain))


@pytest.fixture
def other(monkeypatch: pytest.MonkeyPatch) -> _OtherServers:
    from sudoo.premium import servers

    fake = _OtherServers()
    monkeypatch.setattr(servers, "provider", lambda: fake)
    return fake


def _far_env(branch: str = "main", stage: str = "staging") -> dict[str, str]:
    return {"GIT_BRANCH": branch, "STAGE": stage, "ODOO_VERSION": "19.0"}


def test_an_instance_of_another_server_gets_a_name_pointed_at_that_server(
    hub: FakeHub, other: _OtherServers, authority: FakeAcme
) -> None:
    other.instances[FAR] = ("srv-b", _far_env())
    managed_names.sync()
    hostname = "main.staging.acme.sudoo.test"
    assert hub.subdomains[hostname]["ipv4"] == "5.6.7.8"  # the hub points it at the other server
    assert hub.subdomains["main.production.acme.sudoo.test"]["ipv4"] is None  # this server's: the heartbeat's
    record = custom_domains.get(hostname) or {}
    assert record["server"] == "srv-b" and record["points_to"] == {"ipv4": "5.6.7.8", "ipv6": None}
    assert other.told[-1][0] == "srv-b" and [n["domain"] for n in other.told[-1][1]] == [hostname]
    # the panel neither serves it nor asks for its certificate: the other server does
    assert hostname not in custom_domains.gateway_hosts()
    domain_worker.round_()
    assert not certificates.present(hostname) and (custom_domains.get(hostname) or {})["status"] == "waiting_dns"
    # that server's address changes: the name is asked for again with the new one
    other.addresses_of["srv-b"] = {"public_ipv4": "5.6.7.9", "public_ipv6": "2001:4860::9"}
    managed_names.sync()
    assert hub.subdomains[hostname]["ipv4"] == "5.6.7.9" and hub.subdomains[hostname]["ipv6"] == "2001:4860::9"
    # a server without a public address: no name for its instances, and people see why
    other.instances["near-shop"] = ("srv-c", _far_env("dev", "development"))
    found = managed_names.sync()
    assert managed_names.of_instance("near-shop") is None and "public address" in str(found["error"])
    # deleted there: released, and that server is told
    del other.instances[FAR]
    managed_names.sync()
    assert hostname not in hub.subdomains and managed_names.for_server("srv-b") == []
    assert other.told[-1][:2] == ("srv-b", [])


def test_the_name_follows_a_move_with_its_certificate_both_ways(
    hub: FakeHub, other: _OtherServers, authority: FakeAcme
) -> None:
    from sudoo.core import jobs

    managed_names.sync()
    domain_worker.round_()
    hostname = "main.production.acme.sudoo.test"
    pem = managed_names.certificate_pem(hostname)
    assert pem and certificates.present(hostname)
    # moved to srv-b: the move points the name there before the old copy is deleted, the certificate goes with it
    other.instances[SHOP] = ("srv-b", store.read_env(SHOP))
    job = jobs.Job(SHOP, "Move", "move", "admin1")
    job.status = "running"
    jobs.JOBS[job.id] = job
    try:
        assert managed_names.follow(SHOP, "srv-b") is None
        assert hub.subdomains[hostname]["ipv4"] == "5.6.7.8"
        assert other.told[-1] == ("srv-b", managed_names.for_server("srv-b"), {hostname: pem})
        assert not certificates.present(hostname) and hostname not in custom_domains.gateway_hosts()
        # the old copy is deleted on this server: the name is kept (not released) and a sync changes nothing
        custom_domains.forget_instance(SHOP)
        shutil.rmtree(store.folder(SHOP))
        managed_names.sync()
        assert hostname in hub.subdomains and managed_names.of_instance(SHOP) is not None
    finally:
        job.status = "success"
    assert custom_domains.get(hostname)["server"] == "srv-b"  # type: ignore[index]
    # moved back without the move's help (its agent kept the certificate): the next sync points it here again
    _instance(SHOP, "main", "production")
    other.certificates[("srv-b", hostname)] = pem
    managed_names.sync()
    record = custom_domains.get(hostname) or {}
    assert hub.subdomains[hostname]["ipv4"] is None and managed_names.server_of(record) == "main"
    assert record["status"] == "active" and certificates.present(hostname)  # no new certificate needed
    assert other.told[-1][:2] == ("srv-b", [])  # the old server forgets it
    assert hostname in custom_domains.gateway_hosts()


def test_an_agent_serves_the_names_the_panel_gives_it(hub: FakeHub, authority: FakeAcme) -> None:
    from sudoo.premium import servers

    managed_names.sync()
    domain_worker.round_()
    hostname = "main.production.acme.sudoo.test"
    pem = managed_names.certificate_pem(hostname) or ""
    for key in list(storage().all(custom_domains.COLLECTION)):  # an agent starts without records
        storage().delete(custom_domains.COLLECTION, key)
    custom_domains.forget_cache()
    certificates.remove(hostname)
    servers.become_agent()
    try:
        assert managed_names.sync() == managed_names.state()  # never calls the hub
        managed_names.adopt([{"domain": hostname, "instance": SHOP, "label": "main", "stage": "production"},
                             {"domain": "../x", "instance": SHOP}], {hostname: pem})  # fmt: skip
        record = custom_domains.get(hostname) or {}
        assert set(managed_names.records()) == {hostname} and record["status"] == "active"
        assert custom_domains.gateway_hosts() == {hostname: {"instance": SHOP, "certificate": True}}
        assert managed_names.on()
        # a broken or foreign certificate is not taken: the domain worker gets one
        managed_names.adopt([], None)
        assert not managed_names.records() and not certificates.present(hostname)
        managed_names.adopt([{"domain": hostname, "instance": SHOP}], {hostname: "-----BEGIN " + "PRIVATE KEY-----\nx"})
        assert (custom_domains.get(hostname) or {})["status"] == "waiting_dns"
        assert domain_worker.round_() == 1 and certificates.present(hostname)
        # its instance deleted here: the copy goes, nothing is released by the agent
        requests = len(hub.requests)
        custom_domains.forget_instance(SHOP)
        assert not managed_names.records() and managed_names.state().get("pending_release") in (None, [])
        assert len(hub.requests) == requests
    finally:
        servers._ROLE[0] = "panel"
