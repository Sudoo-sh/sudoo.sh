"""Custom domains (domains/): names, DNS checks, certificates from an ACME authority, renewal, alerts, the API and
the gateway serving them over https.

Nothing here asks real DNS or a real ACME authority: DNS answers come from a table (dns.lookup), certificates from
the stand-in authority in fake_acme.py, and the http probe reads what the gateway would serve.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import shutil
import socket
import ssl
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from flask.testing import FlaskClient

from fake_acme import BASE, FakeAcme
from sudoo import alerts, domains, exposure
from sudoo.config import live
from sudoo.core.state import STATE
from sudoo.domains import acme, certificates, dns, names, worker
from sudoo.instances import gateway, gateway_service, store
from sudoo.storage import storage

NAME = "dom-test"
OTHER = "dom-other"
DOMAIN = "erp.client-one.com"
TARGET = "edge.client-host.net"
SERVER = "203.0.113.10"
ELSEWHERE = "198.51.100.7"

Client = tuple[FlaskClient, dict[str, str]]


# --------------------------------------------------------------------------- fixtures


def _make_instance(name: str, stage: str = "development") -> None:
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(name, {"GATEWAY_PORT": 1, "ODOO_PORT": 2, "STAGE": stage})


@pytest.fixture
def records() -> dict[tuple[str, str], tuple[list[str], str]]:
    """The DNS the tests see: (name, kind) -> (records, canonical name)."""
    return {
        (TARGET, "A"): ([SERVER], TARGET),
        (DOMAIN, "A"): ([SERVER], TARGET),
    }


@pytest.fixture(autouse=True)
def setup(monkeypatch: pytest.MonkeyPatch, records: dict[tuple[str, str], tuple[list[str], str]]) -> Iterator[None]:
    def lookup(name: str, kind: str, timeout: float) -> tuple[list[str], str]:
        assert timeout == 5
        if name == "dns-down.client-one.com":
            raise dns.LookupFailed("DNS did not answer for dns-down.client-one.com: Timeout")
        return records.get((name, kind), ([], name))

    monkeypatch.setattr(dns, "lookup", lookup)
    refreshed: list[int] = []
    monkeypatch.setattr(gateway, "refresh_domains", lambda: refreshed.append(1))
    monkeypatch.setattr(exposure, "default_address", lambda name: f"https://{name}.trycloudflare.com")
    live.change({"custom_domains": "acme", "domain_dns_target": TARGET})
    for name in (NAME, OTHER):
        _make_instance(name)
    yield
    live.change({"custom_domains": None, "domain_dns_target": None, "domains_per_instance": None})
    for name in (NAME, OTHER):
        domains.forget_instance(name)
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)


@pytest.fixture
def authority(monkeypatch: pytest.MonkeyPatch) -> FakeAcme:
    fake = FakeAcme(certificates.challenge_answer)
    monkeypatch.setattr(worker, "TRANSPORT", fake)
    monkeypatch.setattr(acme, "DIRECTORIES", {"production": f"{BASE}/directory", "staging": f"{BASE}/directory"})
    monkeypatch.setattr(acme, "POLL_SECONDS", 0.0)

    def fetch(url: str) -> bytes:  # the probe reaches this server's gateway: what it would serve for the token
        token = url.rsplit("/", 1)[1]
        answer = certificates.challenge_answer(token)
        if answer is None:
            raise OSError("not found")
        return answer

    monkeypatch.setattr(worker, "FETCH", fetch)
    return fake


# --------------------------------------------------------------------------- names


def test_names_are_checked() -> None:
    assert names.normalize(" ERP.Client-One.com. ") == "erp.client-one.com"
    assert names.normalize("bücher.client-one.com") == "xn--bcher-kva.client-one.com"
    for bad, says in (
        ("https://erp.client-one.com/web", "no https://"),
        ("erp.client-one.com:8069", "no https://"),
        ("*.client-one.com", "Wildcard"),
        ("203.0.113.10", "not an IP address"),
        ("2001:db8::1", "no https://"),
        ("1.2.3.4.5", "not an IP address"),
        ("localhost", "not a valid domain"),
        ("intranet.local", "under local"),
        ("demo.sudoo.sh", "under sudoo.sh"),
        ("shop.trycloudflare.com", "under trycloudflare.com"),
        ("erp.example.com", "under example.com"),
        ("bad_name.client-one.com", "not a valid domain"),
        ("-erp.client-one.com", "not a valid domain"),
        ("a" * 64 + ".client-one.com", "not a valid domain"),
        (TARGET, "belongs to this server"),
    ):
        with pytest.raises(names.InvalidDomain, match=says):
            names.normalize(bad)
    assert names.is_apex("client-one.com") and names.is_apex("client.co.uk")
    assert not names.is_apex(DOMAIN)


# --------------------------------------------------------------------------- DNS


def test_dns_records_to_create_and_the_check(records: dict[tuple[str, str], tuple[list[str], str]]) -> None:
    assert dns.instructions(DOMAIN) == [{"type": "CNAME", "name": DOMAIN, "value": TARGET}]
    # a bare domain cannot have a CNAME: the target's addresses
    assert dns.instructions("client-one.com") == [{"type": "A", "name": "client-one.com", "value": SERVER}]
    assert dns.check(DOMAIN) == dns.Check(True, "", (SERVER,))

    missing = dns.check("new.client-one.com")
    assert not missing.ok and "no DNS record yet" in missing.reason
    records[("www.client-one.com", "A")] = ([SERVER, ELSEWHERE], "www.client-one.com")
    stray = dns.check("www.client-one.com")
    assert not stray.ok and ELSEWHERE in stray.reason and "DNS only" in stray.reason
    down = dns.check("dns-down.client-one.com")
    assert not down.ok and "did not answer" in down.reason

    live.change({"domain_dns_target": SERVER})  # an address: an A record
    assert dns.instructions(DOMAIN) == [{"type": "A", "name": DOMAIN, "value": SERVER}]
    assert dns.check(DOMAIN).ok
    live.change({"domain_dns_target": "2001:db8::10"})
    assert dns.instructions(DOMAIN)[0]["type"] == "AAAA"
    live.change({"domain_dns_target": ""})
    assert dns.target() is None and dns.instructions(DOMAIN) == []
    assert "DOMAIN_DNS_TARGET" in dns.check(DOMAIN).reason


# --------------------------------------------------------------------------- the ACME client


def test_the_acme_client_gets_a_certificate_from_the_authority(authority: FakeAcme) -> None:
    published: dict[str, str] = {}
    authority.answer_for = lambda token: published.get(token, "").encode() or None
    account = acme.new_key()
    client = acme.Client(f"{BASE}/directory", account, transport=authority, sleep=lambda _: None)
    authority.bad_nonce_once = True  # retried once with a new nonce
    url = client.register("ops@client-one.com")
    assert url.startswith(f"{BASE}/acct/")
    key = acme.new_key()
    chain = client.certificate(DOMAIN, key, published.__setitem__, lambda token: published.pop(token, None))
    first = x509.load_pem_x509_certificates(chain.encode())[0]
    assert first.subject.rfc4514_string() == f"CN={DOMAIN}"
    assert first.public_key().public_numbers() == key.public_key().public_numbers()
    assert not published  # the challenge answer is withdrawn afterwards

    authority.refuse_challenge = "Connection refused"
    with pytest.raises(acme.AcmeError, match="Connection refused"):
        client.certificate(DOMAIN, acme.new_key(), published.__setitem__, lambda token: published.pop(token, None))
    assert not published


# --------------------------------------------------------------------------- the state machine


def _record() -> dict[str, Any]:
    found = domains.get(DOMAIN)
    assert found is not None
    return found


def test_a_domain_waits_for_dns_gets_its_certificate_and_renews(
    authority: FakeAcme, records: dict[tuple[str, str], tuple[list[str], str]]
) -> None:
    del records[(DOMAIN, "A")]
    added = domains.add(NAME, DOMAIN, "admin1")
    assert added["status"] == "waiting_dns" and added["primary"] and added["tls"] == "acme"
    now = time.time()
    worker.process(_record(), now)
    waiting = _record()
    assert waiting["status"] == "waiting_dns" and "no DNS record yet" in waiting["reason"]
    assert waiting["next_attempt_at"] == pytest.approx(now + 60)
    worker.process(_record(), now)
    assert _record()["next_attempt_at"] == pytest.approx(now + 120)  # waiting longer each time
    assert exposure.address(NAME) == f"https://{NAME}.trycloudflare.com"  # not served yet

    records[(DOMAIN, "A")] = ([SERVER], TARGET)
    assert worker.round_(now + 600) == 1
    active = _record()
    assert active["status"] == "active" and active["reason"] is None and active["attempts"] == 0
    assert active["certificate"]["issuer"] == "Test Authority Fake R1"
    assert active["renew_at"] == pytest.approx(active["certificate"]["expires_at"] - 30 * 86400, abs=2)
    file = certificates.cert_file(DOMAIN)
    assert oct(file.stat().st_mode & 0o777) == "0o600" and oct(file.parent.stat().st_mode & 0o777) == "0o700"
    assert b"PRIVATE KEY" in file.read_bytes() and b"BEGIN CERTIFICATE" in file.read_bytes()
    assert not list(certificates.challenges_dir().iterdir())  # nothing left behind
    # the primary domain is the instance's address now (and so Odoo's web.base.url, monitor.py)
    assert exposure.address(NAME) == f"https://{DOMAIN}"
    assert domains.gateway_hosts() == {DOMAIN: {"instance": NAME, "certificate": True}}
    assert domains.shown_status(active) == "active"

    # nothing is due until the renewal
    assert not worker.due(active, now + 1000)
    before = file.read_bytes()
    worker.round_(active["renew_at"] + 1)
    renewed = _record()
    assert renewed["status"] == "active" and file.read_bytes() != before
    assert renewed["certificate"]["expires_at"] >= active["certificate"]["expires_at"]

    # a certificate expiring soon that could not be renewed is shown (and alerted) as expiring
    soon = {**renewed, "status": "renewing", "certificate": {**renewed["certificate"], "expires_at": now + 5 * 86400}}
    assert domains.shown_status(soon, now) == "expiring"

    domains.remove(DOMAIN)
    assert not file.exists() and exposure.address(NAME) == f"https://{NAME}.trycloudflare.com"


def test_failures_back_off_alert_and_keep_serving_the_old_certificate(authority: FakeAcme) -> None:
    domains.add(NAME, DOMAIN, "admin1")
    now = time.time()
    authority.refuse_challenge = "Timeout during connect (likely firewall problem)"
    worker.process(_record(), now)
    failed = _record()
    assert failed["status"] == "failed" and "firewall" in failed["reason"]
    assert failed["next_attempt_at"] == pytest.approx(now + 300)
    alerts.drain()
    alert = next(a for a in alerts.recent() if a["type"] == "certificate" and a["instance"] == NAME)
    assert alert["title"] == f"Certificate of {DOMAIN} could not be issued" and alert["facts"]["domain"] == DOMAIN
    assert alerts.template(alert) == "certificate_failed"
    assert alert["path"] == f"/i/{NAME}/domains"
    assert not worker.due(_record(), now + 100)
    authority.refuse_challenge = "Timeout again"
    worker.process(_record(), now + 300)
    assert _record()["next_attempt_at"] == pytest.approx(now + 300 + 600)

    worker.process(_record(), now + 900)  # the authority reaches it now
    active = _record()
    assert active["status"] == "active"
    resolved = next(a for a in alerts.recent() if a["key"] == f"certificate:{DOMAIN}" and a["severity"] == "problem")
    assert resolved["resolved_at"]

    # a renewal that fails keeps the certificate (and the domain served), with the reason
    authority.refuse_challenge = "Connection refused"
    worker.process({**active, "renew_at": now}, active["renew_at"] + 1)
    renewing = _record()
    assert renewing["status"] == "renewing" and "refused" in renewing["reason"]
    assert certificates.present(DOMAIN) and exposure.address(NAME) == f"https://{DOMAIN}"

    # expiring: alerted once a day at most, with its own e-mail
    expiring = {**renewing, "status": "active", "renew_at": None,
                "certificate": {**renewing["certificate"], "expires_at": now + 3 * 86400}}  # fmt: skip
    storage().put(domains.COLLECTION, DOMAIN, expiring)
    domains._forget_cache()
    assert worker.due(expiring, now)
    worker.process(expiring, now)
    assert not worker.due(_record(), now + 60)
    alerts.drain()
    notice = next(a for a in alerts.recent() if a["key"] == f"certificate-expiring:{DOMAIN}")
    assert alerts.template(notice) == "certificate_expiring" and notice["facts"]["expires"]


def test_backoff_and_renewal_schedule() -> None:
    assert [worker.backoff(n, 300, 3600) for n in (1, 2, 3, 4, 5, 6)] == [300, 600, 1200, 2400, 3600, 3600]
    day = 86400
    assert worker.renew_at(0, 90 * day) == 60 * day  # 30 days before a 90-day certificate expires
    assert worker.renew_at(0, 45 * day) == 30 * day  # a third of a short-lived one
    live.change({"certificate_renew_days": 10})
    try:
        assert worker.renew_at(0, 90 * day) == 80 * day
    finally:
        live.change({"certificate_renew_days": None})


def test_external_tls_only_records_the_domain_and_checks_it_through_the_proxy(
    authority: FakeAcme, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.change({"custom_domains": "external"})
    asked: list[str] = []
    real_fetch = worker.FETCH
    monkeypatch.setattr(worker, "FETCH", lambda url: asked.append(url) or real_fetch(url))
    domains.add(NAME, DOMAIN, "admin1")
    worker.process(_record(), time.time())
    record = _record()
    assert record["status"] == "active" and record["tls"] == "external" and record["certificate"] is None
    assert asked[0].startswith(f"https://{DOMAIN}/.well-known/acme-challenge/")
    assert authority.requests == []  # no certificate is asked for
    assert exposure.address(NAME) == f"https://{DOMAIN}" and domains.gateway_hosts() == {}
    live.change({"custom_domains": "off"})
    assert exposure.address(NAME) == f"https://{NAME}.trycloudflare.com"


# --------------------------------------------------------------------------- the API


def _url(name: str = NAME, domain: str = "") -> str:
    return f"/api/v1/instances/{name}/domains" + (f"/{domain}" if domain else "")


def test_who_may_see_and_change_domains(admin: Client, operator: Client) -> None:
    client, headers = admin
    developer, developer_headers = operator
    answer = client.post(_url(), json={"domain": "ERP.client-one.com"}, headers=headers)
    assert answer.status_code == 201, answer.get_json()
    assert answer.headers["Location"].endswith(f"{_url()}/{DOMAIN}")
    shown = answer.get_json()["data"]
    assert shown["status"] == "waiting_dns" and shown["primary"]
    assert shown["dns_records"] == [{"type": "CNAME", "name": DOMAIN, "value": TARGET}]

    # developers see them, cannot change them
    seen = developer.get(_url()).get_json()["data"]
    assert seen["mode"] == "acme" and seen["target"] == {"host": TARGET, "record_type": "CNAME"}
    assert [d["domain"] for d in seen["domains"]] == [DOMAIN]
    assert seen["default_url"] == f"https://{NAME}.trycloudflare.com"
    refused = developer.post(_url(), json={"domain": "two.client-one.com"}, headers=developer_headers)
    assert refused.status_code == 403
    assert developer.delete(_url(domain=DOMAIN), headers=developer_headers).status_code == 403

    # unique across instances, valid names only, and a limit per instance
    taken = client.post(_url(OTHER), json={"domain": DOMAIN}, headers=headers)
    assert taken.status_code == 409 and "another instance" in taken.get_json()["detail"]
    bad = client.post(_url(), json={"domain": "*.client-one.com"}, headers=headers)
    assert bad.status_code == 400 and "Wildcard" in bad.get_json()["detail"]
    live.change({"domains_per_instance": 2})
    assert client.post(_url(), json={"domain": "two.client-one.com"}, headers=headers).status_code == 201
    over = client.post(_url(), json={"domain": "three.client-one.com"}, headers=headers)
    assert over.status_code == 409 and "DOMAINS_PER_INSTANCE" in over.get_json()["detail"]

    # primary, check now, remove (the next one becomes primary)
    made = client.patch(_url(domain="two.client-one.com"), json={"primary": True}, headers=headers)
    assert made.status_code == 200 and made.get_json()["data"]["primary"]
    assert [d["primary"] for d in client.get(_url()).get_json()["data"]["domains"]] == [True, False]
    assert client.post(f"{_url(domain=DOMAIN)}/check", headers=headers).status_code == 200
    assert client.delete(_url(domain="two.client-one.com"), headers=headers).status_code == 204
    assert client.get(_url()).get_json()["data"]["domains"][0]["primary"]
    assert client.delete(_url(OTHER, DOMAIN), headers=headers).status_code == 404  # not this instance's

    # off: nothing can be added
    live.change({"custom_domains": "off"})
    off = client.post(_url(), json={"domain": "four.client-one.com"}, headers=headers)
    assert off.status_code == 409 and "Settings" in off.get_json()["detail"]


def test_production_domains_need_a_confirmation(admin: Client) -> None:
    client, headers = admin
    _make_instance(NAME, "production")
    unconfirmed = client.post(_url(), json={"domain": DOMAIN}, headers=headers)
    assert unconfirmed.status_code == 400 and "confirm_production" in unconfirmed.get_json()["detail"]
    confirmed = client.post(_url(), json={"domain": DOMAIN, "confirm_production": True}, headers=headers)
    assert confirmed.status_code == 201
    assert client.delete(_url(domain=DOMAIN), headers=headers).status_code == 400
    assert client.delete(_url(domain=DOMAIN), json={"confirm_production": True}, headers=headers).status_code == 204


# --------------------------------------------------------------------------- the gateway


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _self_signed(domain: str) -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
        .sign(key, hashes.SHA256())
    )  # fmt: skip
    return acme.key_pem(key), certificate.public_bytes(serialization.Encoding.PEM)


class _Host:
    def __init__(self, odoo_port: int) -> None:
        self.odoo_port = odoo_port
        self.visits: list[str] = []

    def route(self, name: str) -> gateway.Route | None:
        return gateway.Route(self.odoo_port) if name == NAME else None

    def edge(self) -> gateway.Edge:
        return gateway_service.Edge("127.0.0.1", True, "CF-Connecting-IP")

    def visited(self, name: str) -> None:
        self.visits.append(name)

    def answered(self, name: str, status: int, duration_ms: float) -> None:
        pass

    async def not_ready(self, request: web.Request, name: str) -> web.StreamResponse:
        return web.Response(status=503)


def test_the_gateway_answers_custom_domains_over_https(tmp_path: Path) -> None:
    key, chain = _self_signed(DOMAIN)
    certificates.store(DOMAIN, key, chain.decode())
    pending = "new.client-one.com"
    trust = ssl.create_default_context(cadata=chain.decode())

    async def odoo_echo(request: web.Request) -> web.Response:
        return web.json_response(
            {"host": request.host, "proto": request.headers.get("X-Forwarded-Proto"),
             "for": request.headers.get("X-Forwarded-For"), "path": request.path_qs}
        )  # fmt: skip

    async def main() -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", odoo_echo)
        odoo = TestServer(app)
        await odoo.start_server()
        host = _Host(odoo.port)
        before = gateway.HOST
        gateway.use(host)
        await gateway._create_session()
        http_port, https_port = _free_port(), _free_port()
        routes = gateway.DomainRoutes(
            http_port, https_port, {DOMAIN: gateway.DomainHost(NAME, True), pending: gateway.DomainHost(NAME, False)}
        )
        try:
            await gateway.reconcile({}, routes)
            assert set(gateway.listening()) == {gateway.HTTP_LISTENER, gateway.HTTPS_LISTENER}
            async with aiohttp.ClientSession() as session:
                # https: routed by name, forwarding headers the visitor cannot forge
                async with session.get(
                    f"https://127.0.0.1:{https_port}/web/login?db=x",
                    headers={"Host": DOMAIN, "X-Forwarded-For": "6.6.6.6", "CF-Connecting-IP": "6.6.6.6"},
                    ssl=trust,
                    server_hostname=DOMAIN,
                ) as answer:
                    assert answer.status == 200
                    seen = await answer.json()
                assert seen["host"] == DOMAIN and seen["proto"] == "https" and seen["path"] == "/web/login?db=x"
                assert seen["for"] == "6.6.6.6, 127.0.0.1"  # the connection's address is what Odoo believes
                assert host.visits == [NAME]
                # another name in Host than in SNI: refused
                async with session.get(
                    f"https://127.0.0.1:{https_port}/", headers={"Host": pending}, ssl=trust, server_hostname=DOMAIN
                ) as answer:
                    assert answer.status == 421
                # an unknown name (or one without a certificate yet): no handshake
                for name in ("unknown.client-one.com", pending):
                    with pytest.raises(aiohttp.ClientError):
                        await session.get(
                            f"https://127.0.0.1:{https_port}/", ssl=False, server_hostname=name,
                            headers={"Host": name},
                        )  # fmt: skip
                # http: redirected to https once there is a certificate, 503 while it is being issued
                async with session.get(
                    f"http://127.0.0.1:{http_port}/web?x=1", headers={"Host": DOMAIN}, allow_redirects=False
                ) as answer:
                    assert answer.status == 308
                    assert answer.headers["Location"] == f"https://{DOMAIN}:{https_port}/web?x=1"
                async with session.get(f"http://127.0.0.1:{http_port}/", headers={"Host": pending}) as answer:
                    assert answer.status == 503
                async with session.get(f"http://127.0.0.1:{http_port}/", headers={"Host": "nope.client-one.com"}) as a:
                    assert a.status == 404
                # the HTTP-01 answer while a check runs, for a known domain only
                certificates.publish_challenge("t" * 43, "t" * 43 + ".thumb")
                path = f"/.well-known/acme-challenge/{'t' * 43}"
                async with session.get(f"http://127.0.0.1:{http_port}{path}", headers={"Host": pending}) as answer:
                    assert answer.status == 200 and await answer.text() == "t" * 43 + ".thumb"
                async with session.get(
                    f"http://127.0.0.1:{http_port}{path}", headers={"Host": "x.client-one.com"}
                ) as a:
                    assert a.status == 404
                certificates.withdraw_challenge("t" * 43)
                async with session.get(f"http://127.0.0.1:{http_port}{path}", headers={"Host": pending}) as answer:
                    assert answer.status == 404

                # a renewed certificate is used at the next connection, without a restart
                key2, chain2 = _self_signed(DOMAIN)
                os.utime(certificates.cert_file(DOMAIN), (0, 0))
                certificates.store(DOMAIN, key2, chain2.decode())
                async with session.get(
                    f"https://127.0.0.1:{https_port}/",
                    headers={"Host": DOMAIN},
                    ssl=ssl.create_default_context(cadata=chain2.decode()),
                    server_hostname=DOMAIN,
                ) as answer:
                    assert answer.status == 200
            # no domains: no listeners
            await gateway.reconcile({}, gateway.DomainRoutes())
            assert gateway.listening() == {}
        finally:
            await gateway.reconcile({}, gateway.DomainRoutes())
            gateway.use(before)
            assert gateway._SESSION is not None
            await gateway._SESSION.close()
            await odoo.close()
            certificates.remove(DOMAIN)

    asyncio.run(main())


def test_routes_carry_the_domains_and_wrong_ones_are_refused() -> None:
    edge = gateway_service.Edge("127.0.0.1", True, "CF-Connecting-IP")
    entry = {NAME: {"gateway_port": 8100, "odoo_port": 28100, "switched_off": False}}
    domains_routes = gateway.DomainRoutes(80, 443, {DOMAIN: gateway.DomainHost(NAME, True)})
    import json

    good = json.loads(gateway_service.routes_document("service", edge, {}, entry, domains_routes))
    routes = gateway_service.parse_routes(good)
    assert routes.served_domains() == domains_routes
    assert routes.served_domains().wanted() == {"*http": ("", 80), "*https": ("", 443)}
    assert gateway_service.parse_routes({**good, "mode": "embedded"}).served_domains().hosts == {}
    without = {k: v for k, v in good.items() if k != "domains"}  # written by an earlier panel
    assert gateway_service.parse_routes(without).served_domains().hosts == {}
    for bad in (
        {**good["domains"], "hosts": {"../etc": {"instance": NAME}}},
        {**good["domains"], "hosts": {"*.client-one.com": {"instance": NAME}}},
        {**good["domains"], "hosts": {DOMAIN: {"instance": "missing"}}},
        {**good["domains"], "http_port": 8100},
        {**good["domains"], "https_port": 80},
        {**good["domains"], "hosts": []},
    ):
        with pytest.raises(ValueError):
            gateway_service.parse_routes({**good, "domains": bad})
