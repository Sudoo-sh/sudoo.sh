"""Access requests: people whose e-mail address is in the allowed domains (ACCESS_REQUEST_DOMAINS, empty: off) ask for
an account, confirm their address from a link, and an administrator approves or rejects. Approval is an invitation
(invitations.py): the person chooses their own password from the e-mailed link. Everyone else is invited as before.

The request form answers the same way whatever the address is: allowed or not, known or not, throttled or not. A
person learns nothing about which domains or accounts exist. What the form accepts is limited per address and per
client address; the confirmation link is a random secret kept only as a keyed digest.

States: "unconfirmed" (link sent), "pending" (confirmed, waits for an administrator), "approved", "rejected".
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from typing import Any

from sudoo import accounts, invitations
from sudoo.config import settings
from sudoo.core import vault
from sudoo.storage import Document, storage

COLLECTION = "access_requests"
PATH = "/request-access"
CONFIRM_HOURS = 48
KEEP_DECIDED_SECONDS = 30 * 86400
PER_ADDRESS_PER_HOUR = 3
PER_CLIENT_PER_HOUR = 10


def domains() -> list[str]:
    return [d.strip().lower().lstrip("@") for d in str(settings.access_request_domains or "").split(",") if d.strip()]


def enabled() -> bool:
    return bool(domains())


def allowed(email: str) -> bool:
    """Whether the address ends in one of the allowed domains (the whole domain: a subdomain is not the domain)."""
    wanted = domains()
    _, _, domain = email.strip().lower().rpartition("@")
    return bool(domain) and domain in wanted


def _digest(secret: str) -> str:
    key = hmac.new(vault.key(), b"access-request-links", hashlib.sha256).digest()
    return hmac.new(key, secret.encode(), hashlib.sha256).hexdigest()


class _Limit:
    def __init__(self, per_hour: int) -> None:
        self.per_hour = per_hour
        self._lock = threading.Lock()
        self._seen: dict[str, deque[float]] = {}

    def take(self, who: str) -> bool:
        """Count one request; False when `who` has used up the hour."""
        now = time.time()
        with self._lock:
            seen = self._seen.setdefault(who, deque())
            while seen and now - seen[0] > 3600:
                seen.popleft()
            if len(seen) >= self.per_hour:
                return False
            seen.append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


BY_ADDRESS = _Limit(PER_ADDRESS_PER_HOUR)
BY_CLIENT = _Limit(PER_CLIENT_PER_HOUR)


def all_requests() -> dict[str, Document]:
    """Every request, after forgetting unconfirmed ones past their link's life and decided ones after 30 days."""
    found = storage().all(COLLECTION)
    now = time.time()
    for key, item in list(found.items()):
        stale = item["status"] == "unconfirmed" and item["created"] + CONFIRM_HOURS * 3600 < now
        old = item["status"] in ("approved", "rejected") and item.get("decided", now) + KEEP_DECIDED_SECONDS < now
        if stale or old:
            storage().delete(COLLECTION, key)
            del found[key]
    return found


def get(request_id: str) -> Document | None:
    return storage().get(COLLECTION, request_id)


def _open_for(email: str) -> str | None:
    wanted = email.strip().lower()
    for key, item in all_requests().items():
        if item["email"] == wanted and item["status"] in ("unconfirmed", "pending"):
            return key
    return None


def submit(email: str, first_name: str, last_name: str, client: str) -> tuple[str, str] | None:
    """A request from the form. Returns (id, secret) when a confirmation e-mail is to be sent, else None: the caller
    answers the same in both cases. Nothing is sent for a domain that is not allowed, an address that has an account,
    an invitation or a request already, or a person who asked too often."""
    email = email.strip().lower()
    if not BY_CLIENT.take(client):
        return None
    if not enabled() or not allowed(email) or accounts.profile_problem({"email": email}):
        return None
    if not BY_ADDRESS.take(email):
        return None
    if accounts.email_taken(email) or invitations.pending_for(email) or _open_for(email):
        return None
    request_id = secrets.token_urlsafe(9)
    secret = secrets.token_urlsafe(32)
    storage().put(
        COLLECTION,
        request_id,
        {
            "email": email,
            "first_name": first_name.strip()[:60],
            "last_name": last_name.strip()[:60],
            "status": "unconfirmed",
            "created": time.time(),
            "secret_hash": _digest(secret),
        },
    )
    return request_id, secret


def confirm(token: str) -> Document | None:
    """The request whose link this is, now waiting for an administrator; None for a wrong, used or old link."""
    request_id, _, secret = token.partition(".")
    item = get(request_id) if request_id and len(request_id) <= 40 else None
    if item is None or item["status"] != "unconfirmed" or not secret:
        return None
    if not hmac.compare_digest(item["secret_hash"], _digest(secret)):
        return None
    if item["created"] + CONFIRM_HOURS * 3600 < time.time():
        return None
    changed = {**item, "status": "pending", "confirmed": time.time()}
    changed.pop("secret_hash", None)  # used up
    storage().put(COLLECTION, request_id, changed)
    return changed


def decide(request_id: str, status: str, by: str, reason: str = "", role: str = "") -> Document | None:
    """Mark a pending request approved or rejected; None when it is not pending (so it is decided once)."""
    with _DECIDE:
        item = get(request_id)
        if item is None or item["status"] != "pending":
            return None
        changed = {**item, "status": status, "decided": time.time(), "decided_by": by}
        if reason:
            changed["reason"] = reason
        if role:
            changed["role"] = role
        storage().put(COLLECTION, request_id, changed)
        return changed


_DECIDE = threading.Lock()


def public_fields(item: Document) -> dict[str, Any]:
    return {
        key: item.get(key) for key in ("email", "first_name", "last_name", "status", "reason", "role", "decided_by")
    }
