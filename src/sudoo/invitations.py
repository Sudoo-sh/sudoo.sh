"""Invitations: new people are invited by e-mail and choose their own password, rather than receiving one.

An administrator enters the person's e-mail address and role (and, optionally, their name); the panel e-mails them a
link (mail/catalog.py: invitation) to /invite#<id>.<secret>. The secret is in the part of the address after "#",
which browsers never send to a server, so it stays out of access logs and Referer headers. Only a hash of it is kept
(HMAC-SHA256 with a key derived from the vault key), so the stored invitations cannot be turned back into links.

A link works once and until it expires (INVITATION_HOURS, 72 by default). Sending it again makes a new secret, so
the old link stops working; revoking deletes the invitation. Opening the link shows who is invited and as what; the
invitee chooses a password (the usual rules: accounts.password_problem) and their name, the account is created, the
invitation is used up, and they are signed in, then offered a second factor (required first when
REQUIRE_SECOND_FACTOR applies to them).

Invitations are stored in the panel's storage (collection "invitations"), apart from the accounts: an invited person
has no account, receives nothing else and cannot sign in until they accept. Creating and resending is limited per
administrator (INVITATIONS_PER_HOUR); wrong links count against the address like wrong passwords (accounts.THROTTLE).

Whether an administrator may still type or generate a password for a new account instead
(INVITATION_PASSWORD_FALLBACK): "no_mail" (the default: only while e-mail is not set up, as the invitation could not
be sent), "always", or "never".
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from sudoo import accounts
from sudoo.config import settings
from sudoo.core import vault
from sudoo.storage import Document, storage

INVITATIONS = "invitations"
PATH = "/invite"
FORGET_AFTER_SECONDS = 30 * 86400  # an expired invitation stays listed (to resend it) for 30 days, then goes


@dataclass(frozen=True, slots=True)
class Link:
    """A new invitation's link: shown in the e-mail only, never stored."""

    invitation_id: str
    token: str


def _digest(secret: str) -> str:
    key = hmac.new(vault.key(), b"invitation-links", hashlib.sha256).digest()
    return hmac.new(key, secret.encode(), hashlib.sha256).hexdigest()


def _new_secret() -> tuple[str, str]:
    secret = secrets.token_urlsafe(32)
    return secret, _digest(secret)


def lifetime_seconds() -> int:
    return int(settings.invitation_hours) * 3600


def all_invitations() -> dict[str, Document]:
    """Every invitation, after forgetting those expired for more than 30 days."""
    found = storage().all(INVITATIONS)
    now = time.time()
    for key, invitation in list(found.items()):
        if invitation.get("expires", 0) + FORGET_AFTER_SECONDS < now:
            storage().delete(INVITATIONS, key)
            del found[key]
    return found


def get(invitation_id: str) -> Document | None:
    return storage().get(INVITATIONS, invitation_id)


def pending_for(email: str) -> str | None:
    """The id of an invitation of this address that has not expired, or None."""
    wanted = email.strip().lower()
    now = time.time()
    for key, invitation in all_invitations().items():
        if str(invitation.get("email", "")).lower() == wanted and invitation.get("expires", 0) > now:
            return key
    return None


def password_allowed(mail_ready: bool) -> bool:
    """Whether an administrator may set a new account's password instead of inviting (INVITATION_PASSWORD_FALLBACK)."""
    rule = settings.invitation_password_fallback
    return rule == "always" or (rule == "no_mail" and not mail_ready)


def create(
    email: str, role: str, invited_by: str, first_name: str = "", last_name: str = "", github_login: str = ""
) -> tuple[str, Link]:
    """A new invitation; returns its id and its link. Raises ValueError saying why not."""
    email = email.strip()
    if role not in accounts.ROLES:
        raise ValueError("Choose a role from the list.")
    problem = accounts.profile_problem({"email": email, "first_name": first_name, "last_name": last_name})
    if problem:
        raise ValueError(problem)
    if not email:
        raise ValueError("Enter the person's e-mail address: the invitation is sent to it.")
    if accounts.email_taken(email):
        raise ValueError("An account already uses this e-mail address.")
    if pending_for(email):
        raise ValueError("This address is already invited. Send the invitation again from the list instead.")
    invitation_id = secrets.token_urlsafe(9)
    secret, digest = _new_secret()
    now = time.time()
    storage().put(
        INVITATIONS,
        invitation_id,
        {
            "email": email,
            "role": role,
            **{
                key: value
                for key, value in (("first_name", first_name), ("last_name", last_name), ("github_login", github_login))
                if value
            },
            "invited_by": invited_by,
            "created": now,
            "sent": now,
            "sent_count": 1,
            "expires": now + lifetime_seconds(),
            "secret_hash": digest,
        },
    )
    return invitation_id, Link(invitation_id, f"{invitation_id}.{secret}")


def renew(invitation_id: str) -> Link:
    """A new link for the invitation (the old one stops working) and a new expiry. Raises ValueError when there is
    no such invitation."""
    secret, digest = _new_secret()

    def apply(invitation: Document | None) -> Document:
        if invitation is None:
            raise ValueError("No such invitation.")
        now = time.time()
        return {
            **invitation,
            "secret_hash": digest,
            "sent": now,
            "sent_count": int(invitation.get("sent_count", 1)) + 1,
            "expires": now + lifetime_seconds(),
        }

    storage().change(INVITATIONS, invitation_id, apply)
    return Link(invitation_id, f"{invitation_id}.{secret}")


def revoke(invitation_id: str) -> Document | None:
    """Delete the invitation; returns it, or None when there was none."""
    found = get(invitation_id)
    if found is not None:
        storage().delete(INVITATIONS, invitation_id)
    return found


def _split(token: str) -> tuple[str, str]:
    invitation_id, _, secret = token.strip().partition(".")
    return invitation_id, secret


def check(token: str) -> tuple[str, Document] | None:
    """(id, invitation) when `token` is the current link of an invitation that has not expired, else None."""
    invitation_id, secret = _split(token)
    if not invitation_id or not secret or len(token) > 200:
        return None
    invitation = get(invitation_id)
    if invitation is None:
        _digest(secret)  # as long as a check of a real invitation
        return None
    if not hmac.compare_digest(str(invitation.get("secret_hash", "")), _digest(secret)):
        return None
    if invitation.get("expires", 0) <= time.time():
        return None
    return invitation_id, invitation


def use(token: str) -> Document | None:
    """Use the link up, atomically: returns the invitation when `token` was its current link and it had not expired
    (it is deleted), else None. Two acceptances of one link at the same time: only one gets it."""
    invitation_id, secret = _split(token)
    used: list[Document] = []

    def apply(invitation: Document | None) -> Document | None:
        if invitation is None:
            return None
        current = hmac.compare_digest(str(invitation.get("secret_hash", "")), _digest(secret))
        if not current or invitation.get("expires", 0) <= time.time():
            return invitation  # unchanged
        used.append(invitation)
        return None  # deleted: the link works once

    if not invitation_id or not secret:
        return None
    storage().change(INVITATIONS, invitation_id, apply)
    return used[0] if used else None


def take(invitation_id: str) -> Document | None:
    """Use an invitation up without its link, atomically: when its person signed in with GitHub and GitHub vouched
    for the invited address or the GitHub login named in it (auth/github.py). Returns it, or None when it is gone or
    expired."""
    used: list[Document] = []

    def apply(invitation: Document | None) -> Document | None:
        if invitation is None or invitation.get("expires", 0) <= time.time():
            return invitation
        used.append(invitation)
        return None

    storage().change(INVITATIONS, invitation_id, apply)
    return used[0] if used else None


class SendLimit:
    """At most INVITATIONS_PER_HOUR invitations sent (new or again) per administrator in any hour."""

    WINDOW_SECONDS = 3600

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sent: dict[str, deque[float]] = {}

    def seconds_to_wait(self, who: str) -> int:
        now = time.time()
        with self._lock:
            sent = self._sent.get(who)
            if not sent:
                return 0
            while sent and now - sent[0] > self.WINDOW_SECONDS:
                sent.popleft()
            if len(sent) < settings.invitations_per_hour:
                return 0
            return int(self.WINDOW_SECONDS - (now - sent[0])) + 1

    def sent(self, who: str) -> None:
        with self._lock:
            self._sent.setdefault(who, deque()).append(time.time())

    def clear(self) -> None:
        with self._lock:
            self._sent.clear()


SEND_LIMIT = SendLimit()


def resource_fields(invitation: Document) -> dict[str, Any]:
    """What the accounts list shows of an invitation (never its link or hash)."""
    return {
        "email": invitation.get("email"),
        "first_name": invitation.get("first_name"),
        "last_name": invitation.get("last_name"),
        "role": invitation.get("role"),
        "github_login": invitation.get("github_login"),
        "invited_by": invitation.get("invited_by"),
        "sent_count": int(invitation.get("sent_count", 1)),
        "expired": invitation.get("expires", 0) <= time.time(),
    }
