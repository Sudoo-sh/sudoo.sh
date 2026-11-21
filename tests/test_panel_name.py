"""The panel's own managed name (instances/panel_name.py) with EXPOSURE=direct: panel.<company>.<base> from the linked
account's hub, served by the gateway with its own ACME certificate and used as the panel's address unless the owner
gave the panel a name; released when it is no longer wanted or offered, forgotten when the panel logs out. Against
the fake hub (fake_hub.py) and the stand-in ACME authority (fake_acme.py)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask.testing import FlaskClient

from fake_acme import BASE, FakeAcme
from fake_hub import FakeHub
from sudoo import exposure
from sudoo.config import live, settings
from sudoo.exposure.base import Target
from sudoo.exposure.direct import Direct
from sudoo.hub import client, marketplace
from sudoo.instances import (
    acme,
    certificates,
    custom_domains,
    domain_worker,
    gateway,
    gateway_link,
    panel_address,
    panel_name,
)
from sudoo.storage import storage

NAME = "panel.acme.sudoo.test"


def _clean() -> None:
    for collection in (panel_name.COLLECTION, panel_address.COLLECTION):
        for key in list(storage().all(collection)):
            storage().delete(collection, key)
    custom_domains.cert_file(NAME).unlink(missing_ok=True)
    panel_address.server_cert_file().unlink(missing_ok=True)


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHub]:
    fake = FakeHub().start()
    monkeypatch.setattr(client, "hub_url", lambda: fake.url)
    monkeypatch.setattr(gateway, "refresh_domains", lambda: None)
    monkeypatch.setattr(marketplace, "public_addresses", lambda: {"public_ipv4": "203.0.113.10"})
    for key in list(storage().all(client.COLLECTION)):
        storage().delete(client.COLLECTION, key)
    _clean()
    live.change({"marketplace": True, "exposure": "direct", "domain_http_probe": False})
    plugin = Direct(settings)
    monkeypatch.setattr(exposure, "provider", lambda: plugin)
    marketplace.heartbeat()
    marketplace.link("LINKCODE42")
    yield fake
    marketplace._save(linked=None)
    _clean()
    live.change({"marketplace": None, "exposure": None, "domain_http_probe": None, "panel_domain": None,
                 "public_host": None, "panel_managed_name": None, "managed_subdomains": None})  # fmt: skip
    fake.stop()


@pytest.fixture
def authority(monkeypatch: pytest.MonkeyPatch) -> FakeAcme:
    fake = FakeAcme(certificates.challenge_answer)
    monkeypatch.setattr(domain_worker, "TRANSPORT", fake)
    monkeypatch.setattr(acme, "DIRECTORIES", {"production": f"{BASE}/directory", "staging": f"{BASE}/directory"})
    monkeypatch.setattr(acme, "POLL_SECONDS", 0.0)
    return fake


def test_a_linked_panel_gets_its_name_and_serves_it_with_its_own_certificate(
    hub: FakeHub, authority: FakeAcme, admin: tuple[FlaskClient, dict[str, str]]
) -> None:
    found = panel_name.sync()
    assert found["offered"] and found["hostname"] == NAME and found["served"] == NAME
    assert hub.panel_name == {"hostname": NAME, "server_id": client.identity().installation_id}
    assert panel_address.domain() == NAME and panel_address.source() == "managed"
    routes = gateway_link.domain_routes(set())
    assert routes.hosts[NAME] == gateway.DomainHost("", False, panel=True)
    # until its certificate is issued the panel stays at the server's address
    assert exposure.provider().address(Target("panel", 9000)) == "https://203.0.113.10"
    panel_address.round_()
    assert panel_address.certificate_ready()
    assert exposure.provider().address(Target("panel", 9000)) == f"https://{NAME}"
    assert gateway_link.domain_routes(set()).hosts[NAME].certificate
    # nothing more to ask at the next check
    panel_name.sync()
    assert [r for r in hub.requests if r.startswith("PUT ")] == ["PUT /api/v1/installations/panel-name"]
    web, headers = admin
    shown = web.get("/api/v1/server", headers=headers).get_json()["data"]["public_address"]
    assert shown["domain"] == NAME and shown["source"] == "managed" and shown["url"] == f"https://{NAME}"
    assert shown["managed_name"]["hostname"] == NAME and shown["managed_name"]["offered"]


def test_a_name_of_the_owner_wins_and_the_managed_one_is_released(hub: FakeHub) -> None:
    panel_name.sync()
    live.change({"panel_domain": "erp.example.com"})
    assert panel_address.domain() == "erp.example.com" and panel_address.source() == "panel_domain"
    panel_name.sync()
    assert hub.panel_name is None and panel_name.state()["hostname"] is None
    live.change({"panel_domain": None, "public_host": "server.example.com"})
    panel_name.sync()
    assert hub.panel_name is None and panel_address.source() == "public_host"
    live.change({"public_host": None})
    panel_name.sync()
    assert panel_address.domain() == NAME
    live.change({"panel_managed_name": False})
    assert panel_address.domain() == ""  # not used at once, released at the check
    panel_name.sync()
    assert hub.panel_name is None


def test_when_the_hub_stops_offering_it_the_panel_goes_back_to_the_servers_address(hub: FakeHub) -> None:
    panel_name.sync()
    hub.panel_names_offered = False
    found = panel_name.sync()
    assert not found["offered"] and found["hostname"] is None and found["served"] is None
    assert hub.panel_name is None and panel_address.domain() == ""
    assert panel_address.url() == "https://203.0.113.10"
    hub.panel_names_offered = True
    assert panel_name.sync()["hostname"] == NAME


def test_logging_out_forgets_the_name(hub: FakeHub) -> None:
    panel_name.sync()
    marketplace.unlink()
    assert hub.panel_name is None and panel_address.domain() == ""
    assert panel_name.state()["hostname"] is None
    found = panel_name.sync()  # not linked: nothing asked
    assert not found["offered"] and not [r for r in hub.requests[-3:] if r.startswith("PUT ")]


def test_no_name_outside_direct_mode_or_while_the_hub_is_unreachable(
    hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel_name.sync()
    monkeypatch.setattr(client, "hub_url", lambda: "http://127.0.0.1:9")
    found = panel_name.sync()
    assert found["error"] and panel_address.domain() == NAME  # the name held stays in use
    live.change({"exposure": "tunnel"})
    assert panel_address.domain() == "" and not panel_name.wanted()


def test_a_hub_without_panel_names_offers_none(hub: FakeHub, monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.hub import HubRefused

    def older_hub(method: str, path: str, body: object = None) -> object:
        raise HubRefused(404, "Not found.")

    monkeypatch.setattr(marketplace, "_for_panel", older_hub)
    found = panel_name.sync()
    assert not found["offered"] and found["error"] is None and panel_address.domain() == ""
