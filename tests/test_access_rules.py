"""Production access rules (beta): addresses and ranges, the country table, what the back office is, exceptions,
the panel's passes, failing open or closed, the visitor's address in each exposure mode, the confirmed change
through the API, and the gateway applying it all (instances/access_gate.py, access_rules.py, geoip.py)."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import shutil
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any
from unittest import mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
from flask import Flask
from flask.testing import FlaskClient

from sudoo import exposure
from sudoo.config import live, settings
from sudoo.core import audit
from sudoo.core.state import STATE
from sudoo.instances import access_confirm, access_gate, access_rules, gateway, geoip, store
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
KEY = b"k" * 32
ROWS = [
    ["1.0.0.0", "1.0.0.255", "AU"],
    ["5.0.0.0", "5.255.255.255", "JO"],
    ["8.8.8.0", "8.8.8.255", "US"],
    ["10.0.0.0", "10.255.255.255", "ZZ"],
    ["82.0.0.0", "82.255.255.255", "PS"],
    ["2a00::", "2a00:ffff:ffff:ffff:ffff:ffff:ffff:ffff", "DE"],
    ["2a01::", "2a01:00ff:ffff:ffff:ffff:ffff:ffff:ffff", "PS"],
]


def _policy(**values: Any) -> access_gate.Policy:
    raw = {
        "mode": "countries",
        "scope": "back_office",
        "countries": ["PS"],
        "networks": [],
        "exception_paths": [],
        "exception_networks": [],
        "version": 1,
        **values,
    }
    return access_gate.parse_policy(raw)


@pytest.fixture
def geo() -> Iterator[None]:
    """A small country table in the gateway's folder."""
    geoip.install(geoip.build(ROWS, minimum_rows=1), "test", "2026-11")
    geoip.forget()
    yield
    shutil.rmtree(geoip.folder(), ignore_errors=True)
    geoip.forget()


@pytest.fixture
def no_geo() -> Iterator[None]:
    shutil.rmtree(geoip.folder(), ignore_errors=True)
    geoip.forget()
    yield
    geoip.forget()


# ---------------------------------------------------------------- addresses and ranges


def test_addresses_and_ranges_v4_and_v6() -> None:
    office = access_gate.parse_network("203.0.113.0/24")
    vpn = access_gate.parse_network("2001:db8:aa::/48")
    single = access_gate.parse_network("198.51.100.7")
    networks = (office, vpn, single)
    assert access_gate.in_any(access_gate.parse_ip("203.0.113.200"), networks)
    assert not access_gate.in_any(access_gate.parse_ip("203.0.114.1"), networks)
    assert access_gate.in_any(access_gate.parse_ip("2001:db8:aa:1::5"), networks)
    assert not access_gate.in_any(access_gate.parse_ip("2001:db8:ab::5"), networks)
    assert access_gate.in_any(access_gate.parse_ip("198.51.100.7"), networks)
    assert not access_gate.in_any(access_gate.parse_ip("198.51.100.8"), networks)
    # an IPv4 visitor reported as IPv4-mapped IPv6 is the same visitor
    assert access_gate.in_any(access_gate.parse_ip("::ffff:203.0.113.9"), networks)
    assert access_gate.parse_network("::ffff:203.0.113.0/120") == office
    assert access_gate.parse_ip("[2001:db8:aa::1]") == ipaddress.ip_address("2001:db8:aa::1")
    assert access_gate.parse_ip("not an address") is None
    assert not access_gate.in_any(None, networks)


@pytest.mark.parametrize("bad", ["203.0.113.5/24", "300.1.1.1", "2001:db8::/129", "office", "", "1.2.3.4/33"])
def test_ranges_that_are_not_valid_are_refused(bad: str) -> None:
    with pytest.raises(access_rules.RuleError):
        access_rules.clean({"enabled": True, "mode": "ips", "networks": [{"value": bad, "label": "x"}]})


def test_a_range_of_every_address_is_refused() -> None:
    with pytest.raises(access_rules.RuleError, match="every address"):
        access_rules.clean({"enabled": True, "mode": "ips", "networks": ["0.0.0.0/0"]})


def test_a_rule_set_is_cleaned() -> None:
    rules = access_rules.clean(
        {
            "enabled": True,
            "mode": "either",
            "scope": "site",
            "countries": ["ps", "JO", "PS"],
            "networks": [{"value": " 203.0.113.0/24 ", "label": "  Main   office "}, "203.0.113.0/24"],
            "exceptions": {"presets": ["webhooks", "payments"], "paths": ["/Custom/Hook/", "/custom/hook/"]},
        }
    )
    assert rules["countries"] == ["JO", "PS"]
    assert rules["networks"] == [{"value": "203.0.113.0/24", "label": "Main office"}]
    assert rules["exceptions"]["presets"] == ["payments", "webhooks"]
    assert rules["exceptions"]["paths"] == ["/custom/hook/"]
    with pytest.raises(access_rules.RuleError, match="country"):
        access_rules.clean({"enabled": True, "mode": "countries", "countries": []})
    with pytest.raises(access_rules.RuleError, match="whole site"):
        access_rules.clean({"exceptions": {"paths": ["/./"]}})
    with pytest.raises(access_rules.RuleError):
        access_rules.clean({"exceptions": {"presets": ["everything"]}})
    assert access_rules.clean({"enabled": False, "mode": "countries"})["enabled"] is False


# ---------------------------------------------------------------- countries


def test_the_country_table_finds_countries_v4_and_v6(geo: None) -> None:
    assert geoip.ready()
    assert geoip.lookup("82.1.2.3") == "PS"
    assert geoip.lookup("5.255.255.255") == "JO"
    assert geoip.lookup("1.0.0.0") == "AU"
    assert geoip.lookup("1.0.1.0") is None  # a gap
    assert geoip.lookup("10.1.1.1") is None  # ZZ: not a country
    assert geoip.lookup("0.0.0.1") is None
    assert geoip.lookup("255.255.255.255") is None
    assert geoip.lookup("2a00:1::1") == "DE"
    assert geoip.lookup("2a01:ff::1") == "PS"
    assert geoip.lookup("2a01:100::1") is None
    assert geoip.lookup("::ffff:82.0.0.1") == "PS"
    assert geoip.lookup("garbage") is None
    status = geoip.status()
    assert status["ready"] and status["month"] == "2026-11" and "DB-IP" in status["attribution"]


def test_a_table_that_does_not_match_its_checksum_is_not_used(geo: None) -> None:
    table = geoip.folder() / geoip.TABLE_FILE
    data = bytearray(table.read_bytes())
    data[-1] ^= 1
    table.write_bytes(bytes(data))
    manifest = geoip.folder() / geoip.MANIFEST_FILE
    manifest.write_text(manifest.read_text())  # a new manifest time: read again
    geoip.forget()
    assert not geoip.ready()
    assert geoip.lookup("82.1.2.3") is None
    assert "checksum" in (geoip.status()["problem"] or "")


def test_a_truncated_download_is_refused() -> None:
    with pytest.raises(geoip.GeoError, match="usable rows"):
        geoip.build(ROWS)


def test_the_download_takes_this_month_or_the_one_before(monkeypatch: pytest.MonkeyPatch, no_geo: None) -> None:
    import gzip
    from datetime import UTC, datetime

    asked: list[str] = []
    csv = "\n".join(",".join(row) for row in ROWS).encode()

    def fetch(url: str) -> bytes:
        asked.append(url)
        if "2026-12" in url:
            raise OSError("404 not published yet")
        return gzip.compress(csv)

    monkeypatch.setattr(geoip, "_fetch", fetch)
    monkeypatch.setattr(geoip, "MIN_ROWS", 1)
    manifest = geoip.download(datetime(2026, 12, 1, tzinfo=UTC))
    assert [u.rsplit("-", 2)[-2:] for u in asked] == [["2026", "12.csv.gz"], ["2026", "11.csv.gz"]]
    assert manifest["month"] == "2026-11"
    geoip.forget()
    assert geoip.lookup("82.0.0.9") == "PS"
    shutil.rmtree(geoip.folder(), ignore_errors=True)


# ---------------------------------------------------------------- the back office


@pytest.mark.parametrize(
    "path",
    [
        "/web",
        "/web/login",
        "/web/login?redirect=/odoo",
        "/web/database/manager",
        "/web/database/selector",
        "/web/dataset/call_kw/res.partner/read",
        "/web/session/authenticate",
        "/odoo",
        "/odoo/action-123",
        "/json/2/res.partner/search_read",
        "/xmlrpc/2/object",
        "/xmlrpc/db",
        "/jsonrpc",
        "/websocket",
        "/longpolling/poll",
        "/report/pdf/account.report_invoice/7",
        "/doc",
        "/__sudoo/connect",
        "/WEB/LOGIN",
        "//web//login",
        "/shop/../web/login",
        "/web%2Flogin",
        "/web%252Flogin",
        "/web\\login",
        "/./odoo",
    ],
)
def test_back_office_paths(path: str) -> None:
    assert access_gate.classify(path) == "back_office"


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/shop",
        "/shop/cart",
        "/my",
        "/contactus",
        "/website/info",
        "/payment/stripe/webhook",
        "/web/assets/1/web.assets_frontend.min.js",
        "/web/image/product.template/1/image_512",
        "/web/image",
        "/web/content/42",
        "/web/static/img/logo.png",
        "/web/binary/company_logo",
        "/web/webclient/translations/abc",
        "/web/health",
        "/webshop",
        "/odoo-rocks",
        "/json-feed",
        "/__sudoo/status",
        "/documentation",
    ],
)
def test_public_paths(path: str) -> None:
    assert access_gate.classify(path) == "public"


# ---------------------------------------------------------------- deciding


def _decide(policy: access_gate.Policy, ip: str, path: str = "/odoo", **values: Any) -> access_gate.Decision:
    address = access_gate.parse_ip(ip)
    values.setdefault("country", geoip.lookup(address))
    values.setdefault("geo_ready", True)
    return access_gate.decide(policy, address, path, **values)


def test_countries_ips_and_either(geo: None) -> None:
    countries = _policy(countries=["PS"])
    assert _decide(countries, "82.1.1.1").allowed
    assert not _decide(countries, "5.1.1.1").allowed
    ips = _policy(mode="ips", networks=["203.0.113.0/24"])
    assert _decide(ips, "203.0.113.4").allowed
    assert not _decide(ips, "82.1.1.1").allowed  # the country does not count in address mode
    either = _policy(mode="either", countries=["JO"], networks=["2001:db8::/32"])
    assert _decide(either, "5.5.5.5").allowed
    assert _decide(either, "2001:db8::1").allowed
    assert not _decide(either, "82.1.1.1").allowed
    assert not _decide(either, "garbage").allowed  # no address: nobody's rule
    # the public site stays open with "back office only", and is closed with "whole site"
    assert _decide(countries, "5.1.1.1", "/shop").allowed
    assert not _decide(_policy(scope="site"), "5.1.1.1", "/shop").allowed


def test_exceptions_paths_presets_and_addresses(geo: None) -> None:
    rules = access_rules.clean(
        {
            "enabled": True,
            "mode": "countries",
            "scope": "site",
            "countries": ["PS"],
            "exceptions": {
                "presets": ["payments", "webhooks"],
                "paths": ["/custom/hook/"],
                "networks": [{"value": "8.8.8.0/24", "label": "Monitor"}],
            },
        }
    )
    policy = access_gate.parse_policy(access_rules.gateway_policy({**rules, "version": 1}))
    assert _decide(policy, "5.1.1.1", "/payment/stripe/webhook").why == "exception"
    assert _decide(policy, "5.1.1.1", "/web/hook/2f6c1d0e").allowed
    assert _decide(policy, "5.1.1.1", "/custom/hook/x").allowed
    assert not _decide(policy, "5.1.1.1", "/custom/hooks").allowed  # "/custom/hook/" matches below it only
    assert _decide(policy, "8.8.8.8", "/odoo").why == "exception"
    assert not _decide(policy, "5.1.1.1", "/shop").allowed
    assert not _decide(policy, "5.1.1.1", "/payment").allowed  # the preset is "/payment/" and below
    assert not _decide(policy, "5.1.1.1", "/web/hook/../login").allowed  # resolved before matching


def test_fail_open_and_fail_closed_without_the_country_table(no_geo: None) -> None:
    policy = _policy(countries=["PS"])
    opened = _decide(policy, "5.1.1.1", geo_ready=geoip.ready(), country=None, geo_missing="open")
    assert opened.allowed and opened.why == "geo_missing"
    assert not _decide(policy, "5.1.1.1", geo_ready=False, country=None, geo_missing="closed").allowed
    # an address rule still decides on its own, and fail-open never opens address mode
    either = _policy(mode="either", networks=["203.0.113.0/24"])
    assert _decide(either, "203.0.113.1", geo_ready=False, country=None, geo_missing="closed").allowed
    ips = _policy(mode="ips", networks=["203.0.113.0/24"])
    assert not _decide(ips, "5.1.1.1", geo_ready=False, country=None, geo_missing="open").allowed


# ---------------------------------------------------------------- passes


def _verify(token: str, **values: Any) -> tuple[dict[str, Any] | None, str]:
    args: dict[str, Any] = {"kind": "t", "instance": "prod", "host": "erp.example.com", "version": 3, **values}
    return access_gate.verify(KEY, token, **args)


def test_a_pass_is_valid_only_for_its_instance_host_version_and_time() -> None:
    now = 1_000_000.0
    token = access_gate.issue(KEY, "prod", "ERP.example.com:443", 3, "omar", now=now)
    assert _verify(token, now=now + 1)[0] is not None
    assert _verify(token, now=now + access_gate.PASS_TOKEN_SECONDS + 1) == (None, "expired")
    assert _verify(token, now=now, host="other.example.com") == (None, "wrong host")
    assert _verify(token, now=now, instance="staging") == (None, "other instance")
    assert _verify(token, now=now, version=4) == (None, "rules changed")
    assert _verify(token, now=now, kind="c") == (None, "wrong kind")
    body, signature = token.split(".")
    forged = json.loads(access_gate._unb64(body))
    forged["i"] = "other"
    assert (
        _verify(access_gate._b64(json.dumps(forged).encode()) + "." + signature, now=now, instance="other")[1]
        == "forged"
    )
    assert (
        access_gate.verify(b"x" * 32, token, kind="t", instance="prod", host="erp.example.com", version=3, now=now)[1]
        == "forged"
    )
    assert _verify("nonsense", now=now) == (None, "malformed")


def test_a_pass_link_works_once() -> None:
    assert access_gate.use_nonce("n-1", time.time() + 60)
    assert not access_gate.use_nonce("n-1", time.time() + 60)


# ---------------------------------------------------------------- the visitor's address, per exposure mode


def _request(
    path: str, headers: dict[str, str], peer: str = "198.51.100.20", host: str = "erp.example.com"
) -> web.Request:
    transport = mock.Mock()
    transport.get_extra_info.side_effect = lambda key, default=None: (peer, 4242) if key == "peername" else default
    return make_mocked_request("GET", path, headers={"Host": host, **headers}, transport=transport)


def _edges() -> dict[str, str | None]:
    from sudoo.exposure.cloudflare import CloudflareQuickTunnel
    from sudoo.exposure.direct import Direct
    from sudoo.exposure.ports import HostPorts
    from sudoo.exposure.proxy import Proxy

    return {
        "direct": Direct.client_ip_header,
        "host-ports": HostPorts.client_ip_header,
        "proxy": Proxy(settings).client_ip_header,
        "tunnel": CloudflareQuickTunnel.client_ip_header,
    }


def test_the_visitors_address_comes_from_what_each_exposure_trusts() -> None:
    edges = _edges()
    forged = {"X-Forwarded-For": "203.0.113.5", "CF-Connecting-IP": "203.0.113.5", "X-Real-IP": "203.0.113.5"}
    # direct and host-ports: the connection's address, whatever the visitor wrote
    assert edges["direct"] is None and edges["host-ports"] is None
    assert access_gate.visitor_ip(_request("/", forged), edges["direct"]) == "198.51.100.20"
    # proxy: the header the owner's proxy sets (X-Real-IP by default), the connection being the proxy itself
    assert edges["proxy"] == settings.proxy_client_ip_header == "X-Real-IP"
    assert (
        access_gate.visitor_ip(_request("/", {"X-Real-IP": "203.0.113.5"}, "127.0.0.1"), edges["proxy"])
        == "203.0.113.5"
    )
    # with X-Forwarded-For as the proxy's header, the last address is the one the proxy added
    chain = {"X-Forwarded-For": "6.6.6.6, 203.0.113.5"}
    assert access_gate.visitor_ip(_request("/", chain, "127.0.0.1"), "X-Forwarded-For") == "203.0.113.5"
    # tunnel: Cloudflare's header from the connector, never X-Forwarded-For
    assert edges["tunnel"] == "CF-Connecting-IP"
    tunnel = {"CF-Connecting-IP": "203.0.113.5", "X-Forwarded-For": "6.6.6.6"}
    assert access_gate.visitor_ip(_request("/", tunnel, "127.0.0.1"), edges["tunnel"]) == "203.0.113.5"


@pytest.mark.parametrize(
    ("mode", "headers", "peer", "allowed"),
    [
        ("direct", {"CF-Connecting-IP": "203.0.113.5", "X-Forwarded-For": "203.0.113.5"}, "198.51.100.20", False),
        ("direct", {}, "203.0.113.5", True),
        ("proxy", {"X-Real-IP": "203.0.113.5"}, "127.0.0.1", True),
        ("proxy", {"CF-Connecting-IP": "203.0.113.5"}, "127.0.0.1", False),
        ("tunnel", {"CF-Connecting-IP": "203.0.113.5"}, "127.0.0.1", True),
        ("tunnel", {"X-Real-IP": "203.0.113.5"}, "127.0.0.1", False),
    ],
)
def test_rules_use_the_trusted_address_in_each_mode(
    mode: str, headers: dict[str, str], peer: str, allowed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    section = {
        "format": 1,
        "pass_key": KEY.hex(),
        "instances": {"prod": {**_raw_policy(mode="ips", networks=["203.0.113.0/24"]), "scope": "site"}},
    }
    monkeypatch.setattr(access_gate, "CONFIG", access_gate.parse_section(section))
    answer = access_gate.check(_request("/odoo", headers, peer), "prod", _edges()[mode], True)
    assert (answer is None) is allowed
    if not allowed:
        assert answer is not None and answer.status == 403


def _raw_policy(**values: Any) -> dict[str, Any]:
    return {
        "mode": "countries", "scope": "back_office", "countries": ["PS"], "networks": [], "exception_paths": [],
        "exception_networks": [], "version": 1, **values,
    }  # fmt: skip


# ---------------------------------------------------------------- the gateway applying the rules

NAME = "ar-prod"
RECEIVED: dict[str, Any] = {}


async def _odoo_echo(request: web.Request) -> web.Response:
    RECEIVED["cookie"] = request.headers.get("Cookie", "")
    RECEIVED["path"] = request.path
    return web.Response(text="odoo")


Scenario = Callable[[TestClient[web.Request, web.Application]], Awaitable[None]]


def _through_gateway(scenario: Scenario, section: dict[str, Any]) -> None:
    async def main() -> None:
        odoo_app = web.Application()
        odoo_app.router.add_route("*", "/{tail:.*}", _odoo_echo)
        odoo = TestServer(odoo_app)
        await odoo.start_server()
        folder = store.folder(NAME)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "docker-compose.yml").write_text("services: {}\n")
        store.write_env(NAME, {"ODOO_PORT": odoo.port, "GATEWAY_PORT": 1, "STAGE": "production"})
        access_gate.load(section)
        await gateway._create_session()
        app = web.Application()
        app[gateway.INSTANCE] = NAME
        app.router.add_route("*", "/{tail:.*}", gateway._handle)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            await scenario(client)
        finally:
            await client.close()
            assert gateway._SESSION is not None
            await gateway._SESSION.close()
            await odoo.close()
            shutil.rmtree(folder)
            STATE.remove(NAME)
            access_gate.load(None)

    asyncio.run(main())


def test_the_gateway_blocks_lets_through_and_honours_a_pass(monkeypatch: pytest.MonkeyPatch, geo: None) -> None:
    monkeypatch.setattr(exposure, "provider", lambda: type("Edge", (), {"https": False, "client_ip_header": None})())
    section = {"format": 1, "pass_key": KEY.hex(), "instances": {NAME: _raw_policy(countries=["PS"], version=5)}}
    used: list[dict[str, Any]] = []
    monkeypatch.setattr(access_gate, "SINK", used.append)

    async def scenario(client: TestClient[web.Request, web.Application]) -> None:
        # the test client connects from 127.0.0.1: in no country
        blocked = await client.get("/odoo", headers={"Accept": "text/html", "Accept-Language": "ar,en;q=0.5"})
        page = await blocked.text()
        assert blocked.status == 403 and 'dir="rtl"' in page and "<svg" in page and "127.0.0.1" not in page
        api = await client.post("/json/2/res.partner/search", headers={"Accept": "application/json"})
        assert api.status == 403 and (await api.json())["error"] == "access_denied"
        assert (await client.get("/shop")).status == 200  # back office only: the shop stays open
        host = f"127.0.0.1:{client.port}"
        token = access_gate.issue(KEY, NAME, host, 5, "omar")
        opened = await client.get(
            f"/__sudoo/pass?token={token}&next=/odoo/action-1", allow_redirects=False
        )  # fmt: skip
        assert opened.status == 303 and opened.headers["Location"] == "/odoo/action-1"
        assert access_gate.PASS_COOKIE in opened.cookies and used[-1]["ok"] and used[-1]["user"] == "omar"
        inside = await client.get("/odoo")
        assert inside.status == 200 and await inside.text() == "odoo"
        assert access_gate.PASS_COOKIE not in RECEIVED["cookie"]  # never passed to Odoo
        again = await client.get(f"/__sudoo/pass?token={token}&next=//evil.example", allow_redirects=False)
        assert again.headers["Location"] == "/odoo" and not used[-1]["ok"] and used[-1]["why"] == "used already"
        client.session.cookie_jar.clear()
        assert (await client.get("/odoo")).status == 403
        # rules saved again (a new version) end the passes given before
        old = access_gate.issue(KEY, NAME, host, 4, "omar")
        await client.get(f"/__sudoo/pass?token={old}&next=/odoo", allow_redirects=False)
        assert (await client.get("/odoo")).status == 403 and used[-1]["why"] == "rules changed"

    _through_gateway(scenario, section)


def test_instances_without_rules_are_not_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exposure, "provider", lambda: type("Edge", (), {"https": False, "client_ip_header": None})())

    async def scenario(client: TestClient[web.Request, web.Application]) -> None:
        assert (await client.get("/odoo")).status == 200
        assert (await client.get("/web/login")).status == 200

    _through_gateway(scenario, {"format": 1, "pass_key": KEY.hex(), "instances": {}})


def test_routes_json_carries_the_rules_and_refuses_bad_ones() -> None:
    from sudoo.instances import gateway_service

    base = {
        "format": 1, "mode": "service", "edge": {"listen_host": "127.0.0.1"}, "settings": {},
        "instances": {"prod": {"gateway_port": 18070, "odoo_port": 18071}},
    }  # fmt: skip
    good = {"format": 1, "pass_key": KEY.hex(), "instances": {"prod": _raw_policy()}}
    routes = gateway_service.parse_routes({**base, "access": good})
    assert access_gate.parse_section(routes.access).policies["prod"].countries == {"PS"}
    with pytest.raises(ValueError):
        gateway_service.parse_routes({**base, "access": {**good, "instances": {"prod": _raw_policy(mode="all")}}})
    with pytest.raises(ValueError):
        gateway_service.parse_routes({**base, "access": {**good, "pass_key": "abcd"}})
    assert gateway_service.parse_routes(base).access is None  # an earlier panel's file: no rules


# ---------------------------------------------------------------- the API: preview, code, change, passes

PROD, DEV = "arprod", "ardev"


def _instance(name: str, stage: str) -> None:
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name, {"ODOO_VERSION": "18.0", "STAGE": stage, "CREATED_AT": int(time.time()), "ODOO_PORT": "18999"}
    )


class FakeMailer:
    ready = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        self.sent.append((to, subject, text))


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> FakeMailer:
    import sudoo.mail

    fake = FakeMailer()
    monkeypatch.setattr(sudoo.mail, "mailer", lambda: fake)
    return fake


@pytest.fixture
def beta(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str, list[str]]]]:
    from sudoo.web.v1 import access_rules as endpoint

    endpoint._WRONG.clear()
    live.change({"access_rules_beta": True})
    monkeypatch.setattr(exposure, "address", lambda name: f"https://{name}.example.com")
    monkeypatch.setattr("sudoo.auth.codes.RESEND_SECONDS", 0)
    told: list[tuple[str, str, list[str]]] = []
    monkeypatch.setattr(access_rules, "notify", lambda name, by, lines: told.append((name, by, lines)))
    _instance(PROD, "production")
    _instance(DEV, "development")
    yield told
    live.change({"access_rules_beta": None})
    for name in (PROD, DEV):
        shutil.rmtree(store.folder(name), ignore_errors=True)
        storage().delete(access_rules.COLLECTION, name)
        STATE.remove(name)
    access_gate.load(None)


RULES = {"enabled": True, "mode": "countries", "scope": "back_office", "countries": ["PS"]}


def _code(mailbox: FakeMailer) -> str:
    text = mailbox.sent[-1][2]
    return next(word for word in text.split() if word.isdigit() and len(word) == 6)


def test_changing_rules_needs_the_name_and_a_code(
    app: Flask, admin: Client, mailbox: FakeMailer, beta: Any, geo: None
) -> None:
    client, headers = admin
    shown = client.get(f"/api/v1/instances/{PROD}/access-rules").get_json()["data"]
    assert shown["available"] and shown["production"] and shown["can_change"] and not shown["active"]
    assert shown["channels"] == ["email"] and shown["you"]["address"] == "127.0.0.1"
    preview = client.post(f"/api/v1/instances/{PROD}/access-rules/preview", json={"rules": RULES}, headers=headers)
    data = preview.get_json()["data"]
    assert data["you"]["allowed"] is False and "you_are_outside" in data["warnings"]  # 127.0.0.1 is in no country
    samples = {s["path"]: s["open_to_everyone"] for s in data["samples"]}
    assert (
        samples["/shop"] and samples["/payment/stripe/webhook"] and not samples["/odoo"] and not samples["/web/login"]
    )
    assert storage().get(access_rules.COLLECTION, PROD) is None  # nothing saved by a preview
    url = f"/api/v1/instances/{PROD}/access-rules"
    body = {"rules": RULES, "confirm_name": PROD, "method": "email", "code": "000000"}
    assert client.put(url, json={**body, "confirm_name": "wrong"}, headers=headers).status_code == 400
    assert client.put(url, json=body, headers=headers).status_code == 400  # no code asked yet
    sent = client.post(f"{url}/code", json={"method": "email"}, headers=headers)
    assert sent.status_code == 200 and sent.get_json()["data"]["sent_to"] == "a***@example.com"
    assert mailbox.sent[-1][0] == "ada@example.com" and PROD in mailbox.sent[-1][1]
    code = _code(mailbox)
    saved = client.put(url, json={**body, "code": code}, headers=headers)
    assert saved.status_code == 200, saved.get_json()
    result = saved.get_json()["data"]
    assert result["active"] and result["rules"]["version"] == 1 and result["rules"]["changed_by"] == "admin1"
    assert client.put(url, json={**body, "code": code}, headers=headers).status_code == 400  # a code works once
    assert beta[-1][0] == PROD and beta[-1][1] == "admin1"
    events = [e["event"] for e in audit.latest(limit=20, instance=PROD)]
    assert "access rules changed" in events and "access rules: confirmation code asked" in events
    assert access_gate.CONFIG.policies[PROD].countries == {"PS"}  # the gateway has them at once


def test_a_development_instance_and_a_developer_cannot_change_rules(
    app: Flask, admin: Client, operator: Client, beta: Any
) -> None:
    client, headers = admin
    answer = client.post(f"/api/v1/instances/{DEV}/access-rules/code", json={"method": "email"}, headers=headers)
    assert answer.status_code == 409
    developer, developer_headers = operator
    shown = developer.get(f"/api/v1/instances/{PROD}/access-rules").get_json()["data"]
    assert not shown["can_change"] and shown["channels"] == []
    refused = developer.put(
        f"/api/v1/instances/{PROD}/access-rules",
        json={"rules": RULES, "confirm_name": PROD, "method": "email", "code": "123456"},
        headers=developer_headers,
    )
    assert refused.status_code == 403


def test_the_feature_is_hidden_without_the_beta_setting(app: Flask, admin: Client) -> None:
    _instance(PROD, "production")
    try:
        client, headers = admin
        assert client.get(f"/api/v1/instances/{PROD}/access-rules").get_json()["data"] == {"available": False}
        answer = client.post(f"/api/v1/instances/{PROD}/access-rules/preview", json={"rules": RULES}, headers=headers)
        assert answer.status_code == 404
        assert access_rules.routes_section()["instances"] == {}
    finally:
        shutil.rmtree(store.folder(PROD), ignore_errors=True)


def test_wrong_codes_are_limited(app: Flask, admin: Client, mailbox: FakeMailer, beta: Any) -> None:
    client, headers = admin
    url = f"/api/v1/instances/{PROD}/access-rules"
    client.post(f"{url}/code", json={"method": "email"}, headers=headers)
    body = {"rules": RULES, "confirm_name": PROD, "method": "email", "code": "999999"}
    answers = [client.put(url, json=body, headers=headers).status_code for _ in range(6)]
    assert answers[:5] == [400] * 5 and answers[5] == 429
    from sudoo.web.v1 import access_rules as endpoint

    endpoint._WRONG.clear()


def test_the_app_channel_and_an_sms_channel_later(monkeypatch: pytest.MonkeyPatch) -> None:
    class Sms:
        name = "sms"

        def available(self, username: str, account: dict[str, Any]) -> bool:
            return True

        def send(self, *args: Any) -> dict[str, Any]:
            return {"sent_to": "+970 ••• 12"}

        def verify(self, *args: Any) -> bool:
            return True

    monkeypatch.setitem(access_confirm.CHANNELS, "sms", Sms())
    assert "sms" in access_confirm.available("someone", {})
    assert "app" not in access_confirm.available("someone", {})  # no authenticator app set up


def _set_rules(name: str, **values: Any) -> None:
    rules = access_rules.clean({**RULES, **values})
    storage().put(access_rules.COLLECTION, name, {**rules, "version": 2})
    access_rules.apply()


def test_open_odoo_and_connect_as_go_through_a_pass(app: Flask, admin: Client, beta: Any) -> None:
    client, headers = admin
    plain = client.post(f"/api/v1/instances/{PROD}/open-link", json={}, headers=headers).get_json()["data"]
    assert plain == {"url": f"https://{PROD}.example.com", "pass": False}  # no rules yet
    _set_rules(PROD)
    given = client.post(f"/api/v1/instances/{PROD}/open-link", json={}, headers=headers).get_json()["data"]
    assert given["pass"] and given["url"].startswith(f"https://{PROD}.example.com/__sudoo/pass?token=")
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(given["url"]).query)
    payload, why = access_gate.verify(
        access_rules.pass_key(), query["token"][0], kind="t", instance=PROD, host=f"{PROD}.example.com", version=2
    )
    assert payload is not None and payload["u"] == "admin1" and query["next"] == ["/odoo"], why
    # the same pass in a Connect as link: through the pass, then the connect link
    from sudoo.instances import connect_as

    monkeypatch_users = [{"id": 2, "login": "admin", "name": "Admin"}]
    original = connect_as.odoo_users
    connect_as.odoo_users = lambda name, now=None: monkeypatch_users  # type: ignore[assignment]
    try:
        link = client.post(
            f"/api/v1/instances/{PROD}/connect", json={"uid": 2, "confirm_stage": "production"}, headers=headers
        ).get_json()["data"]["url"]
    finally:
        connect_as.odoo_users = original  # type: ignore[assignment]
        connect_as._GRANTS.clear()
    assert "/__sudoo/pass?token=" in link and parse_qs(urlsplit(link).query)["next"][0].startswith(
        "/__sudoo/connect?token="
    )


def test_no_pass_for_a_role_that_cannot_open_production(beta: Any) -> None:
    """A pass needs instances.connect in the instance's stage (project roles: Maintainer and up in production)."""
    from sudoo import accounts, projects
    from sudoo.projects import access

    project = projects.create(
        repository="Acme/Rules", odoo_version="18.0", edition="community", origin="linked", user="t"
    )
    store.write_env(PROD, {**store.read_env(PROD), "PROJECT": project["id"], "GIT_BRANCH": "main"})
    accounts.create("ar-dev", "developer", "amber-forest-ladder-64")
    try:
        _set_rules(PROD)
        access.forget_restrictions()
        address = f"https://{PROD}.example.com"
        assert access_rules.pass_url({"name": "admin1", "role": "super_admin"}, PROD, address)[1]
        developer = {"name": "ar-dev", "role": "developer"}
        assert not access.allowed_instance(developer, access_rules.OPEN_PERMISSION, PROD)
        url, given = access_rules.pass_url(developer, PROD, address)
        assert not given and url == address
        events = [e["event"] for e in audit.latest(limit=5, instance=PROD)]
        assert "access rules: pass refused" in events
    finally:
        accounts.delete("ar-dev")
        projects.remove(project["id"])
        access.forget_restrictions()


def test_no_pass_for_an_api_token(app: Flask, beta: Any) -> None:
    """A token must not get past the country and address rules through a pass (F-03), however much it may do."""
    from flask import g
    from werkzeug.exceptions import Forbidden

    from sudoo.web.v1 import access_rules as routes

    _set_rules(PROD)
    address = f"https://{PROD}.example.com"
    for token in ({"name": "api:ci", "role": "super_admin"}, {"name": "ci", "role": "super_admin", "token_id": "t1"}):
        assert access_rules.is_api_token(token)
        allowed, why = access_rules.may_open_production(token, PROD)
        assert not allowed and "signed-in session" in why
        url, given = access_rules.pass_url(token, PROD, address)
        assert not given and url == address
    assert not access_rules.is_api_token({"name": "admin1", "role": "super_admin"})
    assert access_rules.pass_url({"name": "admin1", "role": "super_admin"}, PROD, address)[1]
    with app.test_request_context(f"/api/v1/instances/{PROD}/open-link", method="POST", json={}):
        g.user = {"name": "api:ci", "role": "super_admin"}
        with pytest.raises(Forbidden, match="signed-in session"):
            routes.open_link.__wrapped__(PROD)  # type: ignore[attr-defined]


def test_no_pass_while_the_client_closed_access(beta: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Strict: while the client's switch is closed nobody gets a pass, the gateway refuses the ones given before, and
    closing it tells the gateway at once (projects/clients.py calls access_rules.apply)."""
    from sudoo import projects
    from sudoo.projects import access, clients

    project = projects.create(
        repository="Acme/Closed", odoo_version="18.0", edition="community", origin="linked", user="t"
    )
    store.write_env(PROD, {**store.read_env(PROD), "PROJECT": project["id"], "GIT_BRANCH": "main"})
    applied: list[int] = []
    original = access_rules.apply
    monkeypatch.setattr(access_rules, "apply", lambda: (applied.append(1), original())[1])
    try:
        _set_rules(PROD)
        access.forget_restrictions()
        admin = {"name": "admin1", "role": "super_admin"}
        address = f"https://{PROD}.example.com"
        assert access_rules.pass_url(admin, PROD, address)[1]
        assert "no_passes" not in access_rules.routes_section()["instances"][PROD]
        applied.clear()
        clients.set_switch(str(project["id"]), None, "closed", "client")
        assert applied, "closing access did not reach the gateway"
        assert access_rules.client_closed(PROD)
        assert access_rules.may_open_production(admin, PROD) == (
            False,
            "The client has closed access to this production instance.",
        )
        assert not access_rules.pass_url(admin, PROD, address)[1]
        assert access_rules.routes_section()["instances"][PROD]["no_passes"] is True
        assert access_gate.CONFIG.policies[PROD].no_passes  # the gateway already knows
        clients.set_switch(str(project["id"]), None, "open", "client")
        assert not access_rules.client_closed(PROD) and access_rules.pass_url(admin, PROD, address)[1]
    finally:
        projects.remove(project["id"])
        access.forget_restrictions()


def test_the_gateway_refuses_passes_while_closed(geo: None) -> None:
    policy = _raw_policy(countries=["PS"], version=5)
    section = {"format": 1, "pass_key": KEY.hex(), "instances": {"prod": {**policy, "no_passes": True}}}
    config = access_gate.parse_section(section)
    assert config.policies["prod"].no_passes
    cookie = access_gate.sign(
        KEY, {"k": "c", "i": "prod", "h": "erp.example.com", "e": int(time.time()) + 60, "n": "x", "p": 5}
    )
    access_gate.load(section)
    try:
        answer = access_gate.check(
            _request("/odoo", {"Cookie": f"{access_gate.PASS_COOKIE}={cookie}"}), "prod", None, True
        )
        assert answer is not None and answer.status == 403
    finally:
        access_gate.load(None)


def test_rules_apply_only_to_production(beta: Any) -> None:
    _set_rules(PROD)
    _set_rules(DEV)
    assert set(access_rules.routes_section()["instances"]) == {PROD}
    store.write_env(PROD, {**store.read_env(PROD), "STAGE": "staging"})
    assert access_rules.routes_section()["instances"] == {}


def test_who_is_told_about_a_change(beta: Any) -> None:
    from sudoo import accounts

    accounts.create("admin2", "super_admin", "violet-harbour-candle-58", email="bo@example.com")
    try:
        told = access_rules.recipients(PROD, "admin1")
        assert "admin2" in told and "admin1" not in told and "sync1" not in told
    finally:
        accounts.delete("admin2")
