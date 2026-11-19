"""Custom domains that exist keep being served and renewed by the core, without the premium "domains" package (it
adds new domains: their DNS checks and first certificates). Against the stand-in ACME authority (fake_acme.py)."""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator

import pytest

from fake_acme import BASE, FakeAcme
from sudoo import exposure, premium
from sudoo.config import live
from sudoo.core.state import STATE
from sudoo.instances import acme, certificates, custom_domains, domain_worker, gateway, store
from sudoo.storage import storage

NAME = "renew-test"
DOMAIN = "erp.client-two.com"


@pytest.fixture(autouse=True)
def setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    assert not premium.loaded("domains")  # the package is not installed in the panel's own tests
    monkeypatch.setattr(gateway, "refresh_domains", lambda: None)
    monkeypatch.setattr(exposure, "default_address", lambda name: f"https://{name}.example.net")
    live.change({"custom_domains": "acme", "domain_http_probe": False})
    folder = store.folder(NAME)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(NAME, {"GATEWAY_PORT": 1, "ODOO_PORT": 2, "STAGE": "production"})
    yield
    live.change({"custom_domains": None, "domain_http_probe": None})
    custom_domains.forget_instance(NAME)
    shutil.rmtree(store.folder(NAME), ignore_errors=True)
    STATE.remove(NAME)


@pytest.fixture
def authority(monkeypatch: pytest.MonkeyPatch) -> FakeAcme:
    fake = FakeAcme(certificates.challenge_answer)
    monkeypatch.setattr(domain_worker, "TRANSPORT", fake)
    monkeypatch.setattr(acme, "DIRECTORIES", {"production": f"{BASE}/directory", "staging": f"{BASE}/directory"})
    monkeypatch.setattr(acme, "POLL_SECONDS", 0.0)
    return fake


def _existing(certificate: bool = True, **values: object) -> dict[str, object]:
    """A domain set up before (when the plan had custom domains), as stored."""
    now = time.time()
    record = {"domain": DOMAIN, "instance": NAME, "primary": True, "tls": "acme", "status": "active",
              "reason": None, "added_at": now - 90 * 86400, "added_by": "admin1", "checked_at": now - 60 * 86400,
              "addresses": [], "attempts": 0, "next_attempt_at": None,
              "certificate": {"issued_at": now - 80 * 86400, "expires_at": now + 10 * 86400, "issuer": "Old"}
              if certificate else None, "renew_at": now - 60 if certificate else None, **values}  # fmt: skip
    storage().put(custom_domains.COLLECTION, DOMAIN, record)
    custom_domains.forget_cache()
    return record


def _record() -> dict[str, object]:
    found = custom_domains.get(DOMAIN)
    assert found is not None
    return found


def test_an_existing_domain_is_renewed_without_the_package(authority: FakeAcme) -> None:
    _existing()
    certificates.certs_dir()
    certificates.cert_file(DOMAIN).write_text("old")  # the certificate it has is served until the renewal
    assert exposure.address(NAME) == f"https://{DOMAIN}"
    assert domain_worker.due(_record(), time.time())
    assert domain_worker.round_() == 1
    renewed = _record()
    assert renewed["status"] == "active" and renewed["reason"] is None
    assert renewed["certificate"]["issuer"] == "Test Authority Fake R1"  # type: ignore[index]
    assert b"BEGIN CERTIFICATE" in certificates.cert_file(DOMAIN).read_bytes()
    assert not domain_worker.due(renewed, time.time() + 60)
    assert custom_domains.gateway_hosts() == {DOMAIN: {"instance": NAME, "certificate": True}}


def test_a_failed_renewal_keeps_serving_the_current_certificate(authority: FakeAcme) -> None:
    _existing()
    certificates.certs_dir()
    certificates.cert_file(DOMAIN).write_text("old")
    authority.refuse_challenge = "Connection refused"
    domain_worker.process(_record(), time.time())
    renewing = _record()
    assert renewing["status"] == "renewing" and "refused" in str(renewing["reason"])
    assert certificates.present(DOMAIN) and exposure.address(NAME) == f"https://{DOMAIN}"


def test_a_new_domain_waits_for_the_package(authority: FakeAcme) -> None:
    _existing(certificate=False, status="waiting_dns", next_attempt_at=0)
    assert not domain_worker.due(_record(), time.time())
    domain_worker.process(_record(), time.time())
    assert _record()["status"] == "waiting_dns" and not certificates.present(DOMAIN)
    assert domain_worker.round_() == 0
    # removing it is always possible
    custom_domains.remove(DOMAIN)
    assert custom_domains.get(DOMAIN) is None
