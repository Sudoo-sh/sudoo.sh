"""The panel's own address with EXPOSURE=direct: the gateway answers it on ports 80 and 443 and forwards it to the
web service (127.0.0.1:PANEL_PORT), like an instance's domain.

Which address, in this order:

    PANEL_DOMAIN            a name the owner points at this server (A/AAAA records): served with a certificate from
                            the ACME authority (HTTP-01 on port 80), got and renewed here
    PUBLIC_HOST, a name     the same, when PANEL_DOMAIN is empty
    the managed name        panel.<company>.<base>, from the linked Sudoo.sh account (panel_name.py), when neither
                            is set: the same (the hub keeps its DNS records)
    the server's address    PUBLIC_HOST when it is an address, else PUBLIC_IPV4 / PUBLIC_IPV6, else the address
                            the server sends from (hub/marketplace.public_addresses): served with the panel's own
                            certificate (self-signed, made here), so browsers warn once; meant for the first sign-in
                            until a name is set

While the name's certificate is not issued yet, the panel's address is the server's address. The panel's own
certificate (`<GATEWAY_DIR>/certs/_server.pem`) is also what the gateway serves on an instance's gateway port in
direct mode (an instance without a managed name or custom domain: https://<server address>:<gateway port>).

The state of the name's certificate is a document in the storage (collection "panel_address", key "certificate"):
the domain, status (waiting, active, failed), reason, attempts, next_attempt_at, renew_at, certificate facts.

The client portal's name (PORTAL_DOMAIN, projects/clients.py) is served the same way, as a second name of the panel:
the gateway answers it on ports 80 and 443 and forwards it to the web service, its certificate is got and renewed
here (key "portal"), and the web app shows the client portal there (web/security.py: audience).
"""

from __future__ import annotations

import datetime
import ipaddress
import threading
import time
from typing import Any

from sudoo.config import exposure_mode, settings
from sudoo.core import audit, vault
from sudoo.storage import storage

COLLECTION = "panel_address"
KEY = "certificate"
PORTAL_KEY = "portal"
SERVER_DAYS = 397  # the panel's own certificate: as long as browsers accept, made again 30 days before the end
SERVER_RENEW_DAYS = 30

_LOCK = threading.Lock()


def direct() -> bool:
    return exposure_mode(settings.exposure) == "direct"


def _is_address(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def owner_domain() -> str:
    """The name the owner gave the panel (PANEL_DOMAIN, else PUBLIC_HOST when it is a name); "" when none."""
    if settings.panel_domain:
        return str(settings.panel_domain)
    host = str(settings.public_host)
    return host if host and "." in host and not _is_address(host) else ""


def domain() -> str:
    """The name the panel is served under: the owner's (owner_domain), else its managed name from Sudoo.sh
    (panel_name.hostname); "" when none."""
    found = owner_domain()
    if found:
        return found
    from sudoo.instances import panel_name

    return panel_name.hostname()


def source() -> str | None:
    """Where the panel's name comes from: "panel_domain", "public_host", "managed", or None (no name)."""
    if settings.panel_domain:
        return "panel_domain"
    if owner_domain():
        return "public_host"
    return "managed" if domain() else None


def server_addresses() -> list[str]:
    """The server's own addresses the panel is answered on with its own certificate: PUBLIC_HOST when it is an
    address, the public IPv4 and IPv6 addresses (set, or the ones the server sends from)."""
    found: list[str] = []
    if settings.public_host and _is_address(str(settings.public_host)):
        found.append(str(settings.public_host))
    try:
        from sudoo.hub import marketplace

        public = marketplace.public_addresses()
    except Exception:  # noqa: BLE001 - no route out: only what is set
        public = {}
    for key in ("public_ipv4", "public_ipv6"):
        value = public.get(key)
        if value and value not in found:
            found.append(value)
    return found


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def server_host() -> str:
    """How the server itself is written in an address: PUBLIC_HOST, else its first public address; "" when none."""
    if settings.public_host:
        return _url_host(str(settings.public_host))
    found = server_addresses()
    return _url_host(found[0]) if found else ""


def portal_domain() -> str:
    """The client portal's name (PORTAL_DOMAIN), when it is not the panel's own; "" when none."""
    name = str(settings.portal_domain or "")
    return name if name and name != domain() else ""


def _name_of(key: str) -> str:
    return portal_domain() if key == PORTAL_KEY else domain()


def state(key: str = KEY) -> dict[str, Any]:
    found = storage().get(COLLECTION, key) or {}
    return found if found.get("domain") == _name_of(key) else {}


def certificate_ready(key: str = KEY) -> bool:
    from sudoo.instances import custom_domains

    name = _name_of(key)
    return bool(name) and state(key).get("status") == "active" and custom_domains.certificate_present(name)


def portal_resource() -> dict[str, Any]:
    """What Settings shows about the client portal's address."""
    found = state(PORTAL_KEY)
    name = portal_domain()
    return {
        "domain": name or None,
        "url": f"https://{name}" if name else None,
        "status": (found.get("status") or "waiting") if name and direct() else None,
        "reason": found.get("reason"),
    }


def url() -> str | None:
    """The panel's public address in direct mode: https://<its name> once that has a certificate, else
    https://<the server's address>; None when neither is known."""
    if certificate_ready():
        return f"https://{domain()}"
    host = server_host()
    return f"https://{host}" if host else None


# ---------------------------------------------------------------- the panel's own certificate


def server_cert_file() -> Any:
    from sudoo.instances import custom_domains

    return custom_domains.cert_file(custom_domains.SERVER_CERT)


def _names_of(pem: bytes) -> tuple[set[str], float]:
    from cryptography import x509

    certificate = x509.load_pem_x509_certificates(pem[pem.index(b"-----BEGIN CERTIFICATE-----") :])[0]
    names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    found = {str(n) for n in names.get_values_for_type(x509.DNSName)}
    found |= {str(a) for a in names.get_values_for_type(x509.IPAddress)}
    return found, certificate.not_valid_after_utc.timestamp()


def wanted_names() -> list[str]:
    """What the panel's own certificate names: the server's addresses, PUBLIC_HOST, and "localhost"."""
    names = [*server_addresses()]
    host = str(settings.public_host)
    if host and host not in names:
        names.append(host)
    return names or ["localhost"]


def ensure_server_certificate(now: float | None = None) -> bool:
    """Make the panel's own certificate when it is missing, ends within 30 days or no longer names the server's
    addresses; True when a new one was made."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID

    from sudoo.instances import acme, custom_domains

    now = time.time() if now is None else now
    names = wanted_names()
    with _LOCK:
        try:
            pem = vault.read_private(server_cert_file(), within=settings.gateway_dir, limit=64 * 1024)
            have, not_after = _names_of(pem)
            if set(names) <= have and not_after - now > SERVER_RENEW_DAYS * 86400:
                return False
        except (OSError, ValueError, IndexError, x509.ExtensionNotFound):
            pass
        key = acme.new_key()
        start = datetime.datetime.fromtimestamp(now - 3600, datetime.UTC)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0][:64])])
        alternative = [x509.IPAddress(ipaddress.ip_address(n)) if _is_address(n) else x509.DNSName(n) for n in names]
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(start)
            .not_valid_after(start + datetime.timedelta(days=SERVER_DAYS))
            .add_extension(x509.SubjectAlternativeName(alternative), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        from cryptography.hazmat.primitives import serialization

        custom_domains.certs_dir()
        text = acme.key_pem(key).decode() + certificate.public_bytes(serialization.Encoding.PEM).decode()
        vault.write_private(server_cert_file(), text, within=settings.gateway_dir)
    return True


# ---------------------------------------------------------------- the name's certificate (ACME)


def _save(values: dict[str, Any], key: str = KEY) -> None:
    storage().put(COLLECTION, key, {"domain": _name_of(key), **values})


def due(now: float, key: str = KEY) -> bool:
    if not direct() or not _name_of(key):
        return False
    found = state(key)
    if found.get("status") == "active":
        return bool(now >= float(found.get("renew_at") or 0))
    return bool(float(found.get("next_attempt_at") or 0) <= now)


def round_(now: float | None = None) -> None:
    """Get or renew the certificates of the panel's names (its own, the client portal's) when they are due (called by
    the domain worker's rounds)."""
    from sudoo.instances import custom_domains

    now = time.time() if now is None else now
    if direct() and ensure_server_certificate(now):  # the server's addresses changed, or it ends soon
        custom_domains.refresh_gateway()
    _certificate_round(KEY, "the panel", now)
    _certificate_round(PORTAL_KEY, "the client portal", now)


def _certificate_round(key: str, label: str, now: float) -> None:
    """Get or renew the certificate of one of the panel's names (its own, or the client portal's) when it is due."""
    from sudoo.instances import custom_domains, domain_worker

    if not due(now, key):
        return
    name = _name_of(key)
    found = state(key)
    attempts = int(found.get("attempts") or 0) + 1
    problem = domain_worker.probe(name, "http") if settings.domain_http_probe else None
    if problem is None:
        try:
            facts = domain_worker.obtain(name)
        except Exception as error:  # noqa: BLE001 - acme.AcmeError, ValueError, OSError: tried again later
            problem = str(error)
        else:
            _save({"status": "active", "reason": None, "attempts": 0, "next_attempt_at": None,
                   "renew_at": domain_worker.renew_at(facts.not_before, facts.not_after),
                   "certificate": {"issued_at": now, "expires_at": facts.not_after, "issuer": facts.issuer}},
                  key)  # fmt: skip
            audit.record("panel", None, "certificate issued", None, detail=f"{name} ({label}), {facts.issuer}")
            custom_domains.refresh_gateway()
            return
    wait = domain_worker.backoff(
        attempts, domain_worker.FAILED_FIRST_SECONDS, settings.certificate_retry_max_hours * 3600
    )
    keep = found.get("status") == "active" and custom_domains.certificate_present(name)
    _save({**found, "status": "active" if keep else "failed", "reason": problem, "attempts": attempts,
           "next_attempt_at": now + wait, "renew_at": now + wait if keep else None}, key)  # fmt: skip
    audit.record("panel", None, "certificate not issued", None, ok=False, detail=f"{name} ({label}): {problem}")


def resource() -> dict[str, Any]:
    """What the Settings page shows about the panel's address, and its managed name (panel_name.py)."""
    from sudoo.instances import panel_name

    found = state()
    return {
        "mode": exposure_mode(settings.exposure),
        "url": url(),
        "domain": domain() or None,
        "source": source(),
        "status": found.get("status") or ("waiting" if domain() else None),
        "reason": found.get("reason"),
        "server_addresses": server_addresses(),
        "managed_name": panel_name.resource(),
        "portal": portal_resource(),
    }
