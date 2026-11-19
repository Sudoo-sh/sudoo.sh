"""The custom domains that were set up, as the core serves them: with or without the premium package that manages
them (premium "domains": adding domains, DNS checks, getting and renewing certificates).

Each domain is a record in the storage (collection "domains", key: the domain): its instance, whether it is the
instance's primary domain, how https is handled ("acme": the gateway answers on DOMAIN_HTTP_PORT/DOMAIN_HTTPS_PORT
with the certificate in <GATEWAY_DIR>/certs/<domain>.pem; "external": something in front handles it), and its state
(waiting_dns, issuing, active, renewing, failed). This module reads those records, tells the gateway which domains to
answer (with the certificate they have), gives an instance's public address, and removes domains: removing is always
possible. CUSTOM_DOMAINS=off keeps the records but serves none.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from sudoo.config import settings
from sudoo.core import events
from sudoo.storage import storage

COLLECTION = "domains"
TOPIC = "domains"  # live event: the domains of one instance (key: the instance)
STATUSES = ("waiting_dns", "issuing", "active", "renewing", "failed")
SERVING = ("active", "renewing")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
CERTS = "certs"
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
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def certs_dir() -> Path:
    return _private_dir(settings.gateway_dir / CERTS)


def challenges_dir() -> Path:
    return _private_dir(settings.gateway_dir / CHALLENGES)


def cert_file(domain: str) -> Path:
    return settings.gateway_dir / CERTS / f"{domain}.pem"


def certificate_present(domain: str) -> bool:
    return cert_file(domain).is_file()


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


def for_instance(name: str) -> list[dict[str, Any]]:
    """The instance's domains, the primary one first, then by name."""
    found = [record for record in all_records().values() if record.get("instance") == name]
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
    """https://<primary domain> when the instance has one that is served now (and domains are on), else None."""
    if not enabled():
        return None
    for record in for_instance(name):
        if record.get("primary"):
            return f"https://{record['domain']}" if serving(record) and record.get("tls") == tls_mode() else None
    return None


def gateway_hosts() -> dict[str, dict[str, Any]]:
    """The domains the gateway answers on ports 80/443 (CUSTOM_DOMAINS=acme): {domain: {"instance", "certificate"}}."""
    if settings.custom_domains != "acme":
        return {}
    from sudoo.instances import store

    return {
        domain: {"instance": record["instance"], "certificate": certificate_present(domain)}
        for domain, record in sorted(all_records().items())
        if record.get("tls") == "acme" and store.exists(str(record.get("instance")))
    }


def resource(record: dict[str, Any]) -> dict[str, Any]:
    """A domain as the API shows it (without the domains package: no DNS instructions)."""
    certificate = record.get("certificate") or {}
    return {
        "domain": record["domain"],
        "instance": record.get("instance"),
        "primary": bool(record.get("primary")),
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


def remove(domain: str) -> dict[str, Any]:
    """Remove a domain and its certificate; another domain of the instance becomes primary if this one was."""
    with _LOCK:
        record = get(domain)
        if record is None:
            raise Refused("This instance has no such domain.", 404)
        storage().delete(COLLECTION, domain)
        forget_cache()
        cert_file(domain).unlink(missing_ok=True)
        if record.get("primary"):
            rest = for_instance(record["instance"])
            if rest:
                set_fields(rest[0]["domain"], primary=True)
    changed(record["instance"])
    return record


def forget_instance(name: str) -> None:
    """The instance was deleted: remove its domains and certificates."""
    with _LOCK:
        for record in for_instance(name):
            storage().delete(COLLECTION, record["domain"])
            cert_file(record["domain"]).unlink(missing_ok=True)
        forget_cache()
    refresh_gateway()
