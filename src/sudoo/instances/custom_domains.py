"""The custom domains that were set up, as the core serves them: with or without the premium package that adds
them (premium "domains": adding domains, their DNS checks and first certificates). Certificates of the domains that
exist are renewed by the core (domain_worker.py, acme.py, certificates.py), whatever the plan.

Each domain is a record in the storage (collection "domains", key: the domain): its instance, whether it is the
instance's primary domain, how https is handled ("acme": the gateway answers on DOMAIN_HTTP_PORT/DOMAIN_HTTPS_PORT
with the certificate in <GATEWAY_DIR>/certs/<domain>.pem; "external": something in front handles it), and its state
(waiting_dns, issuing, active, renewing, failed). This module reads those records, tells the gateway which domains to
answer (with the certificate they have), gives an instance's public address, and removes domains: removing is always
possible. CUSTOM_DOMAINS=off keeps the records but serves none.

Managed names (instances/managed_names.py, every plan) are records here too, marked "managed": served and renewed the
same way whatever CUSTOM_DOMAINS says, never listed among an instance's custom domains, and removed only with their
instance (or when the instance's branch or stage changes).

Moving with the instance (several servers, the premium package "servers"): when an instance moves to another server,
its custom domains go with it, before its old copy is deleted. The new server gets each record (primary or not) and
the certificate it was served with (`certificate_pem`, carried over the servers' signed channel, `install_certificate`
there), so its gateway serves the domain at once; the old server forgets it. The customer's DNS still points at the
old server, so a moved record carries "moved" ({"from": the old server's name, "addresses": its public addresses,
"at"}) and "dns_pending": true until the domain worker sees the domain point at the new server (domain_worker.py);
the API shows both ("moved_from", "dns_pending") and the reason says where to point the DNS now.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from sudoo.config import settings
from sudoo.core import events, vault
from sudoo.storage import storage

COLLECTION = "domains"
TOPIC = "domains"  # live event: the domains of one instance (key: the instance)
STATUSES = ("waiting_dns", "issuing", "active", "renewing", "failed")
SERVING = ("active", "renewing")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
CERTS = "certs"
SERVER_CERT = "_server"  # certs/_server.pem: the panel's own certificate (panel_address.py), never a domain
CHALLENGES = "acme-challenge"

_LOCK = threading.RLock()
WAKE = threading.Event()  # set when something changed that the domains package's worker should look at now
_CACHE: dict[str, dict[str, Any]] | None = None


class Refused(Exception):
    """A domain change that is not allowed; the message says why. `status` is the HTTP status for the API."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def enabled() -> bool:
    return settings.custom_domains != "off"


def tls_mode() -> str:
    return "external" if settings.custom_domains == "external" else "acme"


# ---------------------------------------------------------------- files


def _private_dir(path: Path) -> Path:
    shared = path.is_relative_to(settings.gateway_dir)  # the gateway container writes there too
    return vault.private_dir(path, within=settings.gateway_dir if shared else None)


def certs_dir() -> Path:
    return _private_dir(settings.gateway_dir / CERTS)


def challenges_dir() -> Path:
    return _private_dir(settings.gateway_dir / CHALLENGES)


def cert_file(domain: str) -> Path:
    return settings.gateway_dir / CERTS / f"{domain}.pem"


def certificate_present(domain: str) -> bool:
    return cert_file(domain).is_file()


def certificate_pem(domain: str) -> str | None:
    """The certificate this server serves a domain with (its key and chain, PEM), or None."""
    try:  # the gateway container can write there: never through a link it planted, never more than a certificate
        return vault.read_private(cert_file(domain), within=settings.gateway_dir, limit=256 * 1024).decode()
    except (OSError, ValueError):
        return None


def install_certificate(domain: str, pem: str, now: float | None = None) -> bool:
    """Serve a domain of this server with a certificate it had on another server (a move): kept where the gateway
    reads it, the record active until its renewal. False (nothing changed) for anything but a key with a certificate
    for that name that is still valid; the domain worker then gets a new one."""
    from cryptography import x509

    from . import certificates, domain_worker

    now = time.time() if now is None else now
    if get(domain) is None or "PRIVATE KEY-----" not in (pem or ""):
        return False
    start = pem.find("-----BEGIN CERTIFICATE-----")
    if start < 0:
        return False
    try:
        chain = pem[start:]
        first = x509.load_pem_x509_certificates(chain.encode())[0]
        names = first.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
            x509.DNSName
        )
        found = certificates.facts(chain.encode())
    except (ValueError, IndexError, x509.ExtensionNotFound):
        return False
    if domain not in [n.lower() for n in names] or found.not_after <= now:
        return False
    try:
        certs_dir()
        vault.write_private(cert_file(domain), pem, within=settings.gateway_dir)
    except (OSError, ValueError):  # a link planted in place of certs/: nothing is written through it
        return False
    set_fields(
        domain, status="active", reason=None, attempts=0, checked_at=now, next_attempt_at=None,
        renew_at=domain_worker.renew_at(found.not_before, found.not_after),
        certificate={"issued_at": found.not_before, "expires_at": found.not_after, "issuer": found.issuer,
                     "staging": settings.acme_directory == "staging"},
    )  # fmt: skip
    return True


def challenge_answer(token: str) -> bytes | None:
    """The key authorization for `token` while a check runs (read by the gateway)."""
    if not TOKEN_RE.match(token):
        return None
    try:
        return (settings.gateway_dir / CHALLENGES / token).read_bytes()[:1024]
    except OSError:
        return None


# ---------------------------------------------------------------- reading


def all_records() -> dict[str, dict[str, Any]]:
    """Every domain record (copies), by domain. Read for every instance's address, so kept in memory."""
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            _CACHE = storage().all(COLLECTION)
        return {domain: dict(record) for domain, record in _CACHE.items()}


def forget_cache() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = None


def get(domain: str) -> dict[str, Any] | None:
    return all_records().get(domain)


def for_instance(name: str, managed: bool = False) -> list[dict[str, Any]]:
    """The instance's custom domains (with `managed`, its managed name too), the primary one first, then by name."""
    found = [
        record
        for record in all_records().values()
        if record.get("instance") == name and (managed or not record.get("managed"))
    ]
    return sorted(found, key=lambda r: (not r.get("primary"), r["domain"]))


def shown_status(record: dict[str, Any], now: float | None = None) -> str:
    """The state as people see it: "expiring" when the certificate ends within ALERT_CERTIFICATE_DAYS."""
    status = str(record.get("status"))
    expires = (record.get("certificate") or {}).get("expires_at")
    now = time.time() if now is None else now
    if status in SERVING and expires and expires - now < settings.alert_certificate_days * 86400:
        return "expiring"
    return status


def serving(record: dict[str, Any]) -> bool:
    """Whether visitors reach the instance through this domain now."""
    if record.get("status") not in SERVING:
        return False
    if record.get("tls") == "external":
        return True
    return certificate_present(record["domain"])


def primary_url(name: str) -> str | None:
    """https://<primary domain> when the instance has one that is served now (and domains are on), else its managed
    name when that is served (managed_names.py), else None."""
    if enabled():
        for record in for_instance(name):
            if record.get("primary") and serving(record) and record.get("tls") == tls_mode():
                return f"https://{record['domain']}"
    from . import managed_names

    return managed_names.url(name)


def gateway_hosts() -> dict[str, dict[str, Any]]:
    """The domains the gateway answers on ports 80/443: custom domains with CUSTOM_DOMAINS=acme, managed names while
    they are on (managed_names.on); {domain: {"instance", "certificate"}}."""
    from sudoo.instances import managed_names, store

    custom, managed = settings.custom_domains == "acme", managed_names.on()
    if not custom and not managed:
        return {}
    return {
        domain: {"instance": record["instance"], "certificate": certificate_present(domain)}
        for domain, record in sorted(all_records().items())
        if record.get("tls") == "acme"
        and (managed if record.get("managed") else custom)
        and managed_names.server_of(record) == managed_names.MAIN  # another server's name: that server serves it
        and store.exists(str(record.get("instance")))
    }


def resource(record: dict[str, Any]) -> dict[str, Any]:
    """A domain as the API shows it (without the domains package: no DNS instructions)."""
    certificate = record.get("certificate") or {}
    return {
        "domain": record["domain"],
        "instance": record.get("instance"),
        "primary": bool(record.get("primary")),
        "managed": bool(record.get("managed")),
        "tls": record.get("tls"),
        "status": shown_status(record),
        "reason": record.get("reason"),
        "serving": serving(record),
        "added_at": record.get("added_at"),
        "added_by": record.get("added_by"),
        "checked_at": record.get("checked_at"),
        "certificate": {k: certificate.get(k) for k in ("issuer", "issued_at", "expires_at")} if certificate else None,
    }


# ---------------------------------------------------------------- changing (what is always possible)


def publish(name: str) -> None:
    events.publish(TOPIC, name)
    events.publish("instance", name)


def changed(name: str) -> None:
    """Tell the gateway and the people looking that the domains of `name` changed."""
    refresh_gateway()
    publish(name)


def refresh_gateway() -> None:
    from sudoo.instances import gateway

    try:
        gateway.refresh_domains()
    except Exception as error:  # noqa: BLE001 - the gateway catches up at its next round
        print(f"domains: gateway not refreshed: {error!r}", flush=True)


def set_fields(domain: str, **values: Any) -> dict[str, Any] | None:
    """Change fields of a record that still exists; None when it was removed meanwhile."""

    def apply(existing: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if existing is None else {**existing, **values}

    with _LOCK:
        stored = storage().change(COLLECTION, domain, apply)
        forget_cache()
    return stored


def remove(domain: str, managed: bool = False) -> dict[str, Any]:
    """Remove a domain and its certificate; another domain of the instance becomes primary if this one was. A managed
    name is removed only by managed_names.py (`managed`): it follows its instance."""
    with _LOCK:
        record = get(domain)
        if record is None:
            raise Refused("This instance has no such domain.", 404)
        if record.get("managed") and not managed:
            raise Refused(
                "This is the instance's managed name: it stays while the instance exists and is released when the "
                "instance is deleted (or MANAGED_SUBDOMAINS is turned off).",
                409,
            )
        storage().delete(COLLECTION, domain)
        forget_cache()
        vault.remove_private(cert_file(domain), within=settings.gateway_dir)
        if record.get("primary"):
            rest = for_instance(record["instance"])
            if rest:
                set_fields(rest[0]["domain"], primary=True)
    changed(record["instance"])
    return record


def forget_instance(name: str) -> None:
    """The instance was deleted: remove its domains and certificates; its managed name is released at the hub soon,
    unless the instance lives on another server now (a move: the name went or goes with it)."""
    from . import managed_names

    released = []
    with _LOCK:
        for record in for_instance(name, managed=True):
            if record.get("managed") and managed_names.kept_on_delete(record):
                continue
            storage().delete(COLLECTION, record["domain"])
            vault.remove_private(cert_file(record["domain"]), within=settings.gateway_dir)
            if record.get("managed"):
                released.append(record["domain"])
        forget_cache()
    refresh_gateway()
    if released:
        managed_names.release_later(released)
