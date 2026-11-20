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


# ---------------------------------------------------------------- a domain that moved here with its instance


def _self_signed(domain: str, days: float = 60) -> str:
    """A key and a certificate for `domain` (PEM), as a server serves a domain with."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
        .sign(key, hashes.SHA256())
    )  # fmt: skip
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()  # fmt: skip
    return key_pem + certificate.public_bytes(serialization.Encoding.PEM).decode()


def test_a_moved_domain_is_served_with_its_certificate_and_waits_for_its_dns(
    admin: tuple[object, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the servers package leaves on the new server: the record (primary) with "moved" and "dns_pending", and
    the certificate it was served with. It is served at once; the worker says where to point the DNS until it
    points here."""
    live.change({"public_ipv4": "9.9.9.10"})
    try:
        _existing(certificate=False, status="waiting_dns", next_attempt_at=0, dns_pending=True,
                  moved={"from": "Riyadh", "addresses": ["5.6.7.8"], "at": time.time()})  # fmt: skip
        pem = _self_signed(DOMAIN)
        # only a valid key and certificate for that very name is taken
        assert not custom_domains.install_certificate(DOMAIN, _self_signed("other.example.org"))
        assert not custom_domains.install_certificate(DOMAIN, "-----BEGIN " + "PRIVATE KEY-----\nx")
        assert not custom_domains.install_certificate(DOMAIN, _self_signed(DOMAIN, days=-0.5))
        assert not custom_domains.install_certificate("nothing.example.org", pem)
        assert custom_domains.install_certificate(DOMAIN, pem)
        record = _record()
        assert record["status"] == "active" and custom_domains.certificate_pem(DOMAIN) == pem
        assert exposure.address(NAME) == f"https://{DOMAIN}"  # the primary domain stays the instance's address
        assert custom_domains.gateway_hosts() == {DOMAIN: {"instance": NAME, "certificate": True}}
        assert record["dns_pending"] and domain_worker.due(record, time.time())

        # its DNS still points at the old server: the probe does not come back here
        def away(url: str) -> bytes:
            raise OSError("connection refused")

        monkeypatch.setattr(domain_worker, "FETCH", away)
        assert domain_worker.round_() == 1
        record = _record()
        assert record["status"] == "active" and record["dns_pending"] and certificates.present(DOMAIN)
        assert "moved here from Riyadh" in str(record["reason"]) and "Update its DNS to A 9.9.9.10" in str(
            record["reason"]
        )
        assert record["next_attempt_at"] > time.time() and not domain_worker.due(record, time.time())
        client, headers = admin
        shown = client.get(f"/api/v1/instances/{NAME}/domains", headers=headers).get_json()["data"]  # type: ignore[attr-defined]
        found = shown["domains"][0]
        assert found["moved_from"] == "Riyadh" and found["dns_pending"] and found["next_check_at"]
        assert found["url"] == f"https://{DOMAIN}"
        # the reason names the old server when the DNS check sees its address
        assert "still points at the old server Riyadh (5.6.7.8)" in domain_worker.moved_reason(
            record, ("5.6.7.8",), "points elsewhere"
        )

        # the DNS was updated: the probe comes back through the domain, the flag goes
        def here(url: str) -> bytes:
            token = url.rsplit("/", 1)[1]
            return (custom_domains.settings.gateway_dir / custom_domains.CHALLENGES / token).read_bytes()

        monkeypatch.setattr(domain_worker, "FETCH", here)
        custom_domains.set_fields(DOMAIN, next_attempt_at=0)
        assert domain_worker.round_() == 1
        record = _record()
        assert not record["dns_pending"] and record["reason"] is None and record["status"] == "active"
        assert not domain_worker.due(record, time.time())
        assert custom_domains.certificate_pem(DOMAIN) == pem  # no new certificate was needed
    finally:
        live.change({"public_ipv4": None})
