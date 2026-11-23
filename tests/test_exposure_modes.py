"""The three ways a panel is reached (EXPOSURE): direct (the default of new installations), proxy and tunnel; and
that a panel whose .env does not name one keeps the behaviour of earlier releases."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from flask.testing import FlaskClient

from fake_acme import BASE, FakeAcme
from sudoo import exposure
from sudoo.config import Settings, exposure_mode, live
from sudoo.exposure.base import Exposure, Target
from sudoo.exposure.direct import Direct
from sudoo.exposure.proxy import Proxy
from sudoo.hub import marketplace
from sudoo.instances import acme, certificates, custom_domains, domain_worker, gateway, gateway_link, panel_address
from sudoo.instances import gateway_service as service
from sudoo.storage import storage
from sudoo.web.security import client_ip_of

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def addresses(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    found = {"public_ipv4": "203.0.113.10"}
    monkeypatch.setattr(marketplace, "public_addresses", lambda: dict(found))
    return found


@pytest.fixture
def direct(monkeypatch: pytest.MonkeyPatch, addresses: dict[str, str]) -> Iterator[Direct]:
    from sudoo.config import settings

    live.change({"exposure": "direct"})
    plugin = Direct(settings)
    monkeypatch.setattr(exposure, "provider", lambda: plugin)
    monkeypatch.setattr(gateway, "refresh_domains", lambda: None)
    yield plugin
    live.change({"exposure": None, "panel_domain": None, "public_host": None})
    storage().delete(panel_address.COLLECTION, panel_address.KEY)


# ---------------------------------------------------------------- settings


def test_new_installations_get_direct_and_earlier_ones_keep_their_exposure() -> None:
    template = (REPO / ".env.example").read_text()
    assert "\nEXPOSURE=direct\n" in template
    assert Settings().exposure == "tunnel"  # no EXPOSURE in .env (tests run without one): as before
    assert exposure_mode("cloudflare-quick") == "tunnel" and exposure_mode("host-ports") == "host-ports"
    compose = (REPO / "compose.yaml").read_text()
    assert "EXPOSURE: ${EXPOSURE:-tunnel}" in compose


def test_custom_domains_and_scheme_follow_the_mode_unless_set() -> None:
    assert (Settings(exposure="direct").custom_domains, Settings(exposure="direct").public_scheme) == ("acme", "http")
    assert (Settings(exposure="proxy").custom_domains, Settings(exposure="proxy").public_scheme) == (
        "external",
        "https",
    )
    assert Settings(exposure="tunnel").custom_domains == "off"
    assert Settings(exposure="cloudflare-quick").custom_domains == "off"
    assert Settings(exposure="direct", custom_domains="off").custom_domains == "off"  # an earlier .env's value stays
    assert Settings(exposure="proxy", public_scheme="http").public_scheme == "http"


@pytest.mark.parametrize(
    ("name", "value"),
    [("panel_domain", "not a name"), ("public_host", "a b"), ("proxy_listen", "localhost"),
     ("proxy_client_ip_header", "X Real")],  # fmt: skip
)
def test_wrong_address_settings_are_refused(name: str, value: str) -> None:
    with pytest.raises(ValueError):
        Settings(**{name: value})


def test_the_settings_page_offers_the_three_modes_and_explains_refusals(
    admin: tuple[FlaskClient, dict[str, str]],
) -> None:
    client, headers = admin
    try:
        refused = client.patch("/api/v1/settings", json={"exposure": "carrier-pigeon"}, headers=headers)
        assert refused.status_code == 400 and "direct, proxy, tunnel" in refused.get_json()["detail"]
        assert client.patch("/api/v1/settings", json={"exposure": "host-ports"}, headers=headers).status_code == 400
        no_name = client.patch("/api/v1/settings", json={"exposure": "proxy"}, headers=headers)
        assert no_name.status_code == 400 and "PANEL_DOMAIN" in no_name.get_json()["detail"]
        changed = client.patch("/api/v1/settings", json={"exposure": "direct"}, headers=headers)
        assert changed.status_code == 200
        row = next(s for s in changed.get_json()["data"] if s["key"] == "exposure")
        assert row["value"] == "direct" and row["applies"] == "restart" and row["pending_restart"]
        assert row["env_value"] == "tunnel"  # .env is never rewritten by the panel
    finally:
        live.change({"exposure": None})


# ---------------------------------------------------------------- plugins


def test_direct_addresses(direct: Direct) -> None:
    assert isinstance(direct, Exposure)
    assert direct.listen_host == "0.0.0.0" and direct.https and direct.client_ip_header is None
    assert direct.address(Target("panel", 9000)) == "https://203.0.113.10"
    assert direct.address(Target("shop", 8070)) == "https://203.0.113.10:8070"
    live.change({"public_host": "server.example.com"})
    assert panel_address.domain() == "server.example.com"  # a name: the panel's name, once it has a certificate
    assert direct.address(Target("panel", 9000)) == "https://server.example.com"  # until then, the server itself
    live.change({"panel_domain": "panel.example.com"})
    assert panel_address.domain() == "panel.example.com"
    assert not panel_address.certificate_ready()


def test_proxy_addresses_and_trusted_header() -> None:
    plugin = Proxy(Settings(exposure="proxy", panel_domain="panel.example.com", public_host="apps.example.com"))
    assert isinstance(plugin, Exposure)
    assert plugin.listen_host == "127.0.0.1" and plugin.https and plugin.client_ip_header == "X-Real-IP"
    assert plugin.address(Target("panel", 9000)) == "https://panel.example.com"
    assert plugin.address(Target("shop", 8070)) == "https://apps.example.com:8070"
    assert Proxy(Settings(exposure="proxy")).address(Target("shop", 8070)) is None


def test_visitor_address_with_each_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exposure, "provider", lambda: Direct(Settings(exposure="direct")))
    assert client_ip_of({"X-Sudoo-Client-IP": "198.51.100.4", "X-Forwarded-For": "127.0.0.1"}, "127.0.0.1") == (
        "198.51.100.4"
    )
    monkeypatch.setattr(
        exposure, "provider", lambda: Proxy(Settings(exposure="proxy", proxy_client_ip_header="X-Forwarded-For"))
    )
    assert client_ip_of({"X-Forwarded-For": "6.6.6.6, 198.51.100.5"}, "127.0.0.1") == "198.51.100.5"
    assert client_ip_of({"X-Sudoo-Client-IP": "6.6.6.6", "X-Forwarded-For": "198.51.100.5"}, "127.0.0.1") == (
        "198.51.100.5"
    )  # outside direct mode nobody's X-Sudoo-Client-IP is believed


# ---------------------------------------------------------------- the panel's own certificate and routes


def test_the_panels_own_certificate_names_the_server(direct: Direct, addresses: dict[str, str]) -> None:
    assert panel_address.ensure_server_certificate()
    pem = panel_address.server_cert_file().read_bytes()
    names, _ = panel_address._names_of(pem)
    assert names == {"203.0.113.10"} and b"PRIVATE KEY" in pem
    assert not panel_address.ensure_server_certificate()  # still good: kept
    addresses["public_ipv6"] = "2001:db8::10"
    assert panel_address.ensure_server_certificate()  # another address: made again
    assert panel_address._names_of(panel_address.server_cert_file().read_bytes())[0] == {"203.0.113.10", "2001:db8::10"}
    assert panel_address.ensure_server_certificate(time.time() + 380 * 86400)  # ends soon: made again


def test_routes_carry_the_panel_to_the_gateway_service(direct: Direct) -> None:
    live.change({"panel_domain": "panel.example.com"})
    routes = gateway_link.domain_routes(set())
    assert routes.panel_port == 9000 and routes.server_names == frozenset({"203.0.113.10"})
    assert routes.hosts["panel.example.com"] == gateway.DomainHost("", False, panel=True)
    assert set(routes.wanted()) == {gateway.HTTP_LISTENER, gateway.HTTPS_LISTENER}
    edge = service.Edge("0.0.0.0", True, None, True)
    parsed = service.parse_routes(json.loads(service.routes_document("service", edge, {}, {}, routes)))
    assert parsed.edge.gateway_tls and parsed.domains == routes
    # a file of an earlier panel (no panel, no gateway_tls) still reads
    earlier = service.parse_routes(
        json.loads(service.routes_document("service", service.Edge(), {}, {}, gateway.DomainRoutes()))
    )
    assert earlier.domains.panel_port is None and not earlier.edge.gateway_tls


def test_the_panels_name_gets_its_certificate_from_the_acme_authority(
    direct: Direct, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAcme(certificates.challenge_answer)
    monkeypatch.setattr(domain_worker, "TRANSPORT", fake)
    monkeypatch.setattr(acme, "DIRECTORIES", {"production": f"{BASE}/directory", "staging": f"{BASE}/directory"})
    monkeypatch.setattr(acme, "POLL_SECONDS", 0.0)
    live.change({"panel_domain": "panel.example.com", "domain_http_probe": False})
    try:
        panel_address.round_()
        assert panel_address.state()["status"] == "active"
        assert panel_address.certificate_ready()
        assert direct.address(Target("panel", 9000)) == "https://panel.example.com"
        assert gateway_link.domain_routes(set()).hosts["panel.example.com"].certificate
        assert not panel_address.due(time.time())  # renewed only when due
    finally:
        live.change({"domain_http_probe": None})
        custom_domains.cert_file("panel.example.com").unlink(missing_ok=True)


def test_the_client_portals_name_is_a_second_name_of_the_panel(direct: Direct, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAcme(certificates.challenge_answer)
    monkeypatch.setattr(domain_worker, "TRANSPORT", fake)
    monkeypatch.setattr(acme, "DIRECTORIES", {"production": f"{BASE}/directory", "staging": f"{BASE}/directory"})
    monkeypatch.setattr(acme, "POLL_SECONDS", 0.0)
    live.change(
        {"panel_domain": "panel.example.com", "portal_domain": "portal.example.com", "domain_http_probe": False}
    )
    try:
        routes = gateway_link.domain_routes(set())
        assert routes.hosts["portal.example.com"] == gateway.DomainHost("", False, panel=True)  # to the web service
        panel_address.round_()
        assert panel_address.state(panel_address.PORTAL_KEY)["status"] == "active"
        assert gateway_link.domain_routes(set()).hosts["portal.example.com"].certificate
        assert panel_address.resource()["portal"]["url"] == "https://portal.example.com"
    finally:
        live.change({"domain_http_probe": None, "portal_domain": None})
        for name in ("panel.example.com", "portal.example.com"):
            custom_domains.cert_file(name).unlink(missing_ok=True)


# ---------------------------------------------------------------- the gateway forwards the panel's visits


async def _panel_echo(request: web.Request) -> web.Response:
    response = web.json_response({"headers": list(request.headers.items())})
    response.set_cookie("sudoo_session", "kept")
    return response


def test_the_gateway_forwards_the_panel_with_its_cookies_and_the_visitors_address() -> None:
    async def main() -> dict[str, Any]:
        panel = TestServer(web.Application())
        panel.app.router.add_get("/api/v1/me", _panel_echo)
        await panel.start_server()
        await gateway._create_session()
        before = gateway.DOMAINS
        gateway.DOMAINS = gateway.DomainRoutes(panel_port=panel.port, server_names=frozenset({"127.0.0.1"}))
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", gateway._to_panel)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            answer = await client.get(
                "/api/v1/me", headers={"Cookie": "sudoo_session=abc", "X-Sudoo-Client-IP": "6.6.6.6"}
            )
            body = await answer.json()
            return {"body": body, "cookie": answer.headers.get("Set-Cookie", "")}
        finally:
            gateway.DOMAINS = before
            await client.close()
            assert gateway._SESSION is not None
            await gateway._SESSION.close()
            await panel.close()

    found = asyncio.run(main())
    headers = found["body"]["headers"]
    assert ["Cookie", "sudoo_session=abc"] in headers
    assert ["X-Sudoo-Client-IP", "127.0.0.1"] in headers and ["X-Sudoo-Client-IP", "6.6.6.6"] not in headers
    assert "sudoo_session=kept" in found["cookie"]


def test_the_server_resource_shows_the_mode(
    admin: tuple[FlaskClient, dict[str, str]], addresses: dict[str, str]
) -> None:
    client, headers = admin
    found = client.get("/api/v1/server", headers=headers).get_json()["data"]["public_address"]
    assert found["mode"] == "tunnel" and found["server_addresses"] == ["203.0.113.10"]
