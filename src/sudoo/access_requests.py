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
import time
from typing import Any

from sudoo import accounts, invitations
from sudoo.config import settings
from sudoo.core import vault
from sudoo.core.shared import SharedLock
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
    """One hour's allowance per key, counted by the panel's rate limiter (limits/: the plugin named by RATE_LIMITER,
    so panels that share one plugin share these counts; the built-in "memory" one counts in this process). The key
    holds a digest, not the address."""

    def __init__(self, name: str, per_hour: int) -> None:
        self.name = name
        self.per_hour = per_hour
        self._epoch = 0  # clear() starts new counts here, without reaching into a shared plugin

    def take(self, who: str) -> bool:
        """Count one request; False when `who` has used up the hour."""
        from sudoo import limits

        digest = hashlib.sha256(f"{self.name}:{self._epoch}:{who}".encode()).hexdigest()[:32]
        return limits.limiter().hit(f"access-request:{digest}", self.per_hour, 3600).allowed

    def clear(self) -> None:
        self._epoch += 1


BY_ADDRESS = _Limit("address", PER_ADDRESS_PER_HOUR)
BY_CLIENT = _Limit("client", PER_CLIENT_PER_HOUR)


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
    if not BY_CLIENT.take(client):  # the caller's own allowance: it tells them nothing about anyone else
        return None
    # From here every request does the same work, whichever of the answers it ends in: nobody learns from the time
    # it takes whether the domain is allowed, the address is known, or its own allowance for it is used up.
    valid = enabled() and allowed(email) and not accounts.profile_problem({"email": email})
    within = BY_ADDRESS.take(email)
    request_id = secrets.token_urlsafe(9)
    secret = secrets.token_urlsafe(32)
    with _SUBMIT:  # the checks and the new request together: two at once for one address make one
        known = bool(accounts.email_taken(email) or invitations.pending_for(email) or _open_for(email))
        if not (valid and within) or known:
            return None
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


_SUBMIT = SharedLock("access-requests-submit")


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
    used: list[Document] = []

    def use(current: Document | None) -> Document | None:  # once: the link clicked twice at once confirms once
        if current is None or current.get("status") != "unconfirmed" or "secret_hash" not in current:
            return current
        changed = {**current, "status": "pending", "confirmed": time.time()}
        changed.pop("secret_hash", None)  # used up
        used.append(changed)
        return changed

    storage().change(COLLECTION, request_id, use)
    return used[0] if used else None


def decide(request_id: str, status: str, by: str, reason: str = "", role: str = "") -> Document | None:
    """Mark a pending request approved or rejected; None when it is not pending (so it is decided once)."""
    decided: list[Document] = []

    def apply(item: Document | None) -> Document | None:
        if item is None or item.get("status") != "pending":
            return item
        changed = {**item, "status": status, "decided": time.time(), "decided_by": by}
        if reason:
            changed["reason"] = reason
        if role:
            changed["role"] = role
        decided.append(changed)
        return changed

    with _DECIDE:  # one write decides (every process: the storage's own transaction)
        storage().change(COLLECTION, request_id, apply)
    return decided[0] if decided else None


_DECIDE = SharedLock("access-requests-decide")


def public_fields(item: Document) -> dict[str, Any]:
    return {
        key: item.get(key) for key in ("email", "first_name", "last_name", "status", "reason", "role", "decided_by")
    }
