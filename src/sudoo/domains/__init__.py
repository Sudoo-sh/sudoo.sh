"""Custom domains: an instance answers on names such as erp.client.com, over https, with a certificate the panel
gets and renews on its own.

CUSTOM_DOMAINS says how (an admin setting, "off" by default):

    acme      the gateway answers custom domains on DOMAIN_HTTP_PORT (80) and DOMAIN_HTTPS_PORT (443) of the server,
              routes each by its name (TLS SNI and Host) to its instance, and redirects http to https; certificates
              come from the ACME authority (ACME_DIRECTORY, Let's Encrypt by default) by HTTP-01. Needs a server that
              accepts connections on those ports (EXPOSURE=host-ports, or a tunnel exposure with 80 and 443 open).
    external  something in front handles https and sends the visits to the instance's gateway port (your own
              Cloudflare tunnel or proxy): the panel records the domains, checks that each reaches the instance
              (over https), and uses the primary one as the instance's address. No certificate, no port 80/443.
    off       no custom domains (those recorded are kept, not served).

Each domain is a record in the storage (collection "domains", key: the domain) and goes through these states
(worker.py moves it along, in the background):

    waiting_dns  added; the panel checks its DNS (dns.py) until it points at this server
    issuing      DNS is right; the certificate is being requested
    active       served over https (acme: with a valid certificate; external: reachable)
    renewing     the certificate is due for renewal (CERTIFICATE_RENEW_DAYS before it expires) or its renewal
                 failed; the current certificate is still served
    failed       could not be issued (the reason says why); tried again later, waiting longer each time
                 (up to CERTIFICATE_RETRY_MAX_HOURS)

"expiring" is shown instead of active/renewing when the certificate expires within ALERT_CERTIFICATE_DAYS; an alert
(type "certificate") is sent then, and when an issuance or renewal fails.

One domain of an instance is its primary domain: once active it is the instance's public address (exposure.address),
so the monitor writes it into Odoo's web.base.url (instances/monitor.follow_public_addresses).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from sudoo.config import settings
from sudoo.core import events
from sudoo.storage import storage

from . import certificates
from .names import InvalidDomain, normalize

COLLECTION = "domains"
TOPIC = "domains"  # live event: the domains of one instance (key: the instance)
STATUSES = ("waiting_dns", "issuing", "active", "renewing", "failed")
SERVING = ("active", "renewing")

__all__ = ["COLLECTION", "InvalidDomain", "Refused", "add", "for_instance", "normalize", "remove"]

_LOCK = threading.RLock()
WAKE = threading.Event()  # set when something changed that the worker should look at now


class Refused(Exception):
    """A domain change that is not allowed; the message says why. `status` is the HTTP status for the API."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def enabled() -> bool:
    return settings.custom_domains != "off"


def mode() -> str:
    return str(settings.custom_domains)


# ---------------------------------------------------------------- reading


_CACHE: dict[str, dict[str, Any]] | None = None  # every record; every change goes through this module


def all_records() -> dict[str, dict[str, Any]]:
    """Every domain record (copies), by domain. Read for every instance's address, so kept in memory."""
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            _CACHE = storage().all(COLLECTION)
        return {domain: dict(record) for domain, record in _CACHE.items()}


def _forget_cache() -> None:
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
    return certificates.present(record["domain"])


def primary_url(name: str) -> str | None:
    """https://<primary domain> when the instance has one that is served now (and domains are on), else None."""
    if not enabled():
        return None
    for record in for_instance(name):
        if record.get("primary"):
            return f"https://{record['domain']}" if serving(record) and record.get("tls") == tls_mode() else None
    return None


def tls_mode() -> str:
    return "external" if settings.custom_domains == "external" else "acme"


def gateway_hosts() -> dict[str, dict[str, Any]]:
    """The domains the gateway answers on ports 80/443 (CUSTOM_DOMAINS=acme): {domain: {"instance", "certificate"}}."""
    if settings.custom_domains != "acme":
        return {}
    from sudoo.instances import store

    return {
        domain: {"instance": record["instance"], "certificate": certificates.present(domain)}
        for domain, record in sorted(all_records().items())
        if record.get("tls") == "acme" and store.exists(str(record.get("instance")))
    }


# ---------------------------------------------------------------- changing


def _publish(name: str) -> None:
    events.publish(TOPIC, name)
    events.publish("instance", name)


def changed(name: str) -> None:
    """Tell the gateway and the people looking that the domains of `name` changed."""
    from sudoo.instances import gateway

    try:
        gateway.refresh_domains()
    except Exception as error:  # noqa: BLE001 - the gateway catches up at its next round
        print(f"domains: gateway not refreshed: {error!r}", flush=True)
    _publish(name)


def add(name: str, value: str, user: str) -> dict[str, Any]:
    """Add a domain to the instance (the first becomes primary); raises InvalidDomain or Refused."""
    if not enabled():
        raise Refused("Custom domains are off on this server: an administrator can turn them on in Settings.")
    domain = normalize(value)
    limit = settings.domains_per_instance
    now = time.time()
    with _LOCK:
        mine = for_instance(name)
        if limit == 0 or len(mine) >= limit:
            raise Refused(
                f"An instance may have {limit} custom domain{'' if limit == 1 else 's'} at most (DOMAINS_PER_INSTANCE)."
            )
        record = {
            "domain": domain,
            "instance": name,
            "primary": not mine,
            "tls": tls_mode(),
            "status": "waiting_dns",
            "reason": None,
            "added_at": now,
            "added_by": user,
            "checked_at": None,
            "addresses": [],
            "attempts": 0,
            "next_attempt_at": now,
            "certificate": None,
            "renew_at": None,
        }

        def create(existing: dict[str, Any] | None) -> dict[str, Any]:
            if existing is not None:
                where = "this instance" if existing.get("instance") == name else "another instance"
                raise Refused(f"{domain} is already a custom domain of {where}.")
            return record

        storage().change(COLLECTION, domain, create)
        _forget_cache()
    WAKE.set()
    changed(name)
    return record


def remove(domain: str) -> dict[str, Any]:
    """Remove a domain and its certificate; another domain of the instance becomes primary if this one was."""
    with _LOCK:
        record = get(domain)
        if record is None:
            raise Refused("This instance has no such domain.", 404)
        storage().delete(COLLECTION, domain)
        _forget_cache()
        certificates.remove(domain)
        if record.get("primary"):
            rest = for_instance(record["instance"])
            if rest:
                _set(rest[0]["domain"], primary=True)
    changed(record["instance"])
    return record


def make_primary(domain: str) -> dict[str, Any]:
    with _LOCK:
        record = get(domain)
        if record is None:
            raise Refused("This instance has no such domain.", 404)
        for other in for_instance(record["instance"]):
            if other["domain"] != domain and other.get("primary"):
                _set(other["domain"], primary=False)
        updated = _set(domain, primary=True) or record
    changed(record["instance"])
    return updated


def retry(domain: str) -> dict[str, Any]:
    """Check (and issue) now instead of at the next attempt."""
    record = _set(domain, next_attempt_at=time.time(), attempts=0)
    if record is None:
        raise Refused("This instance has no such domain.", 404)
    WAKE.set()
    _publish(record["instance"])
    return record


def forget_instance(name: str) -> None:
    """The instance was deleted: remove its domains and certificates."""
    with _LOCK:
        for record in for_instance(name):
            storage().delete(COLLECTION, record["domain"])
            certificates.remove(record["domain"])
        _forget_cache()
    from sudoo.instances import gateway

    try:
        gateway.refresh_domains()
    except Exception as error:  # noqa: BLE001 - the gateway catches up at its next round
        print(f"domains: gateway not refreshed: {error!r}", flush=True)


def _set(domain: str, **values: Any) -> dict[str, Any] | None:
    """Change fields of a record that still exists; None when it was removed meanwhile."""

    def apply(existing: dict[str, Any] | None) -> dict[str, Any] | None:
        if existing is None:
            return None
        return {**existing, **values}

    with _LOCK:
        stored = storage().change(COLLECTION, domain, apply)
        _forget_cache()
    return stored
