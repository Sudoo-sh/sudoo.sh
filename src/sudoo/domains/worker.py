"""The background work of custom domains: check DNS, get certificates, renew them, alert when that fails.

One thread (run_forever) looks at every domain whose next attempt is due, every LOOP_SECONDS, and at once when a
domain is added or retried (domains.WAKE). One domain at a time: ACME authorities limit how fast one may ask, and a
check waits for the authority anyway.

    waiting_dns / failed   DNS check (dns.py); then, with DOMAIN_HTTP_PROBE (acme) or always (external), an http
                           probe: a random answer the gateway serves at /.well-known/acme-challenge/<token>, fetched
                           through the domain the way the ACME authority will. Then:
                             acme      issuing -> ACME order (acme.py) -> active, or failed with the reason
                             external  active
    active                 renewing once renew_at has passed (CERTIFICATE_RENEW_DAYS before expiry, at the latest a
                           third of the certificate's lifetime before it)
    renewing               a new certificate replaces the old one -> active; on failure it stays renewing (the old
                           certificate is still served) with the reason, and becomes failed once it expired

Waiting and failing wait longer each time: DNS checks 1, 2, 4 ... minutes up to an hour; failed issuances 5, 10,
20 ... minutes up to CERTIFICATE_RETRY_MAX_HOURS (ACME authorities allow only a few failed checks per hour).
"""

from __future__ import annotations

import secrets
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from sudoo import alerts
from sudoo.config import settings
from sudoo.core import audit

from . import COLLECTION, WAKE, _publish, _set, acme, all_records, certificates, changed, dns, enabled, tls_mode

LOOP_SECONDS = 30.0
DNS_FIRST_SECONDS, DNS_MAX_SECONDS = 60.0, 3600.0
FAILED_FIRST_SECONDS = 300.0
PROBE_TIMEOUT_SECONDS = 10.0
PROBE_PATH = "/.well-known/acme-challenge/"

TRANSPORT: acme.Transport = acme.urllib_transport  # replaced in tests: never a real ACME authority there
_RUNNING = threading.Lock()


def backoff(attempts: int, first: float, longest: float) -> float:
    """Seconds to wait after `attempts` attempts in a row: first, 2 x first, 4 x first ... at most `longest`."""
    return float(min(longest, first * 2 ** max(0, attempts - 1)))


def renew_at(not_before: float, not_after: float) -> float:
    """When to renew: CERTIFICATE_RENEW_DAYS before expiry, or a third of the lifetime before it if that is later
    (short-lived certificates)."""
    window = min(settings.certificate_renew_days * 86400, (not_after - not_before) / 3)
    return not_after - window


# ---------------------------------------------------------------- the http probe


def fetch(url: str) -> bytes:
    """GET `url` (10 seconds), the body's first kilobyte; raises OSError when it cannot be fetched."""
    request = urllib.request.Request(url, headers={"User-Agent": acme.USER_AGENT})
    with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:  # noqa: S310 - http(s) only
        return bytes(response.read(1024))


FETCH: Callable[[str], bytes] = fetch  # replaced in tests


def probe(domain: str) -> str | None:
    """Fetch a fresh answer through the domain; None when it came back, else why not."""
    token = secrets.token_urlsafe(32)
    content = secrets.token_urlsafe(32)
    certificates.publish_challenge(token, content)
    scheme = "https" if tls_mode() == "external" else "http"
    try:
        body = FETCH(f"{scheme}://{domain}{PROBE_PATH}{token}")
    except urllib.error.HTTPError as error:
        return f"{scheme}://{domain} answered HTTP {error.code} instead of the panel's check."
    except (OSError, ValueError) as error:
        reason = getattr(error, "reason", None) or error.__class__.__name__
        where = "port 80 must be open to the internet" if scheme == "http" else "check your tunnel or proxy"
        return f"{scheme}://{domain} could not be reached from this server ({reason}): {where}."
    finally:
        certificates.withdraw_challenge(token)
    if body.strip() != content.encode():
        return f"{scheme}://{domain} reached another server, not this instance's gateway."
    return None


# ---------------------------------------------------------------- one domain


def _alert(record: dict[str, Any], title: str, text: str, key: str, expires: str = "") -> None:
    from urllib.parse import quote

    alerts.send(
        alerts.Alert(
            "certificate", title, text, instance=record["instance"],
            path=f"/i/{quote(record['instance'])}/domains", key=key,
            facts={"domain": record["domain"], "expires": expires},
        )
    )  # fmt: skip


def _recover(record: dict[str, Any]) -> None:
    from urllib.parse import quote

    domain = record["domain"]
    alerts.recover(
        f"certificate:{domain}",
        alerts.Alert("certificate", f"{domain} is served over https again", f"The certificate of {domain} was "
                     "issued.", instance=record["instance"], path=f"/i/{quote(record['instance'])}/domains"),
    )  # fmt: skip


def _not_now(record: dict[str, Any], reason: str, now: float, status: str) -> None:
    """DNS or the probe is not right yet: wait (the DNS back-off), keep the reason."""
    attempts = int(record.get("attempts") or 0) + 1
    _set(record["domain"], status=status, reason=reason, attempts=attempts, checked_at=now,
         next_attempt_at=now + backoff(attempts, DNS_FIRST_SECONDS, DNS_MAX_SECONDS))  # fmt: skip


def _failed(record: dict[str, Any], reason: str, now: float) -> None:
    """The ACME authority refused or failed: back off, alert."""
    attempts = int(record.get("attempts") or 0) + 1
    certificate = record.get("certificate") or {}
    expired = bool(certificate) and certificate.get("expires_at", 0) <= now
    status = "renewing" if certificate and not expired else "failed"
    wait = backoff(attempts, FAILED_FIRST_SECONDS, settings.certificate_retry_max_hours * 3600)
    _set(record["domain"], status=status, reason=reason, attempts=attempts, checked_at=now, next_attempt_at=now + wait)
    domain = record["domain"]
    verb = "renewed" if status == "renewing" else "issued"
    audit.record("panel", None, f"certificate not {verb}", record["instance"], ok=False, detail=f"{domain}: {reason}")
    _alert(record, f"Certificate of {domain} could not be {verb}", reason, f"certificate:{domain}")


def _issue(record: dict[str, Any], now: float) -> None:
    domain = record["domain"]
    directory = acme.directory_url(settings.acme_directory)
    key_pem, account_url = certificates.account(directory)
    client = acme.Client(directory, acme.load_key(key_pem), account_url, transport=TRANSPORT)
    if not account_url:
        account_url = client.register(settings.acme_email or settings.admin_contact_email)
        certificates.save_account(directory, key_pem, account_url)
    key = acme.new_key()
    try:
        chain = client.certificate(domain, key, certificates.publish_challenge, certificates.withdraw_challenge)
    except acme.AcmeError as error:
        if error.kind != "accountDoesNotExist":
            raise
        # the authority no longer knows the account (deactivated, or another authority at the same address)
        client.account_url = None
        certificates.save_account(directory, key_pem, client.register(settings.acme_email or settings.admin_contact_email))
        chain = client.certificate(domain, key, certificates.publish_challenge, certificates.withdraw_challenge)
    found = certificates.store(domain, acme.key_pem(key), chain)
    stored = _set(
        domain, status="active", reason=None, attempts=0, checked_at=now, next_attempt_at=None,
        renew_at=renew_at(found.not_before, found.not_after),
        certificate={"issued_at": now, "expires_at": found.not_after, "issuer": found.issuer,
                     "staging": settings.acme_directory == "staging"},
    )  # fmt: skip
    if stored is None:  # removed while it was being issued
        certificates.remove(domain)
        return
    audit.record("panel", None, "certificate issued", record["instance"], detail=f"{domain}, {found.issuer}")
    _recover(record)


def process(record: dict[str, Any], now: float | None = None) -> None:
    """Move one domain along (see the module's description)."""
    now = time.time() if now is None else now
    domain, status = record["domain"], record.get("status")
    if record.get("tls") != tls_mode():
        return  # recorded for the other mode: kept, not served, until the mode changes back
    if status == "active":
        if record.get("tls") == "acme" and record.get("renew_at") and now >= record["renew_at"]:
            record = _set(domain, status="renewing", reason=None, attempts=0) or record
            _publish(record["instance"])
        else:
            _expiry_alert(record, now)
            return
    checked = dns.check(domain) if record.get("tls") == "acme" else dns.Check(True, "")
    if not checked.ok:
        if status == "renewing":
            _failed(record, checked.reason, now)
        else:
            _not_now(record, checked.reason, now, "waiting_dns")
        _publish(record["instance"])
        return
    _set(domain, addresses=list(checked.addresses))
    if settings.domain_http_probe or record.get("tls") == "external":
        problem = probe(domain)
        if problem:
            if status == "renewing":
                _failed(record, problem, now)
            else:
                _not_now(record, problem, now, "waiting_dns" if status == "waiting_dns" else "failed")
            _publish(record["instance"])
            return
    if record.get("tls") == "external":
        _set(domain, status="active", reason=None, attempts=0, checked_at=now, next_attempt_at=None)
        audit.record("panel", None, "custom domain active", record["instance"], detail=domain)
        changed(record["instance"])
        return
    if status != "renewing":
        _set(domain, status="issuing", reason=None)
        changed(record["instance"])  # the gateway answers the challenge for it
    try:
        _issue(record, now)
    except (acme.AcmeError, ValueError, OSError) as error:
        _failed(record, str(error), now)
    changed(record["instance"])


def _expiry_alert(record: dict[str, Any], now: float) -> None:
    expires = (record.get("certificate") or {}).get("expires_at")
    if not _expiring(record, now) or not expires:
        return
    _set(record["domain"], expiry_alerted_at=now)
    from datetime import UTC, datetime

    day = datetime.fromtimestamp(expires, UTC).strftime("%d %B %Y")
    domain = record["domain"]
    reason = record.get("reason") or "the panel keeps trying"
    _alert(record, f"Certificate of {domain} expires on {day}", f"It was not renewed yet: {reason}.",
           f"certificate-expiring:{domain}", expires=day)  # fmt: skip


def _expiring(record: dict[str, Any], now: float) -> bool:
    """Within ALERT_CERTIFICATE_DAYS of expiry and not alerted in the last day."""
    expires = (record.get("certificate") or {}).get("expires_at")
    if not expires or expires - now > settings.alert_certificate_days * 86400:
        return False
    return bool(now - float(record.get("expiry_alerted_at") or 0) >= 86400)


def due(record: dict[str, Any], now: float) -> bool:
    status = record.get("status")
    if status == "active":
        return bool(record.get("renew_at") and now >= record["renew_at"]) or _expiring(record, now)
    if status == "issuing":  # left half-way (the panel restarted): start again
        return True
    return bool((record.get("next_attempt_at") or 0) <= now)


def round_(now: float | None = None) -> int:
    """One pass over every due domain; how many were looked at."""
    if not enabled():
        return 0
    seen = 0
    with _RUNNING:
        for domain in sorted(all_records()):
            record = all_records().get(domain)
            current = time.time() if now is None else now
            if record is None or not due(record, current):
                continue
            seen += 1
            try:
                process(record, current)
            except Exception as error:  # noqa: BLE001 - one domain must not stop the others
                print(f"domains: {domain}: {error!r}", flush=True)
    return seen


def run_forever() -> None:
    while True:
        WAKE.wait(LOOP_SECONDS)
        WAKE.clear()
        try:
            round_()
        except Exception as error:  # noqa: BLE001 - try again at the next round
            print(f"domains: {error!r}", flush=True)


__all__ = ["COLLECTION", "backoff", "process", "renew_at", "round_", "run_forever"]
