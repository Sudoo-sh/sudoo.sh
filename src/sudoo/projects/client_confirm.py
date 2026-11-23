"""Confirmation by e-mail of a client's major actions in the portal (projects/clients.py).

Opening or closing the switch (project or branch), locking or unlocking, turning emergency access on or off,
approving an access request, and naming or removing the people a closure lets through: each waits until the client
confirms it. The portal asks; the panel keeps the action as a confirmation (storage collection
"client_confirmations") and e-mails the client (template client_action_confirm, or _ar in Arabic) a one-time link and
a 6-digit code:

    link    <portal>/confirm#<id>.<secret>: the secret is after "#", so it never reaches a server's logs; only an
            HMAC of it is kept (as invitations do)
    code    6 digits, through the panel's code machinery (auth/codes.py: hashed, 5 wrong tries), under the purpose
            "client-action:<id>"

Either works once, within CLIENT_CONFIRM_MINUTES (15 by default), and only in the client's own session: the session
that asked (its sid) and the same account. Confirming runs the action as it was asked (its checks run again then);
cancelling, a wrong session or the time passing leave everything as it was. Asking, confirming, refusing and the time
running out are in the audit log, scoped to the project, so the client sees them in its activity log.

CLIENT_CONFIRMATIONS off: actions run at once (the panel's owner chose so). On, and e-mail not set up: actions are
refused with a sentence that says why, since they could never be confirmed.

`request_code` and `check_code` of auth/email_sign_in.py follow the same pattern (a key and a purpose per kind of
code); this module adds the link and the action that waits.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Any

from sudoo import accounts
from sudoo.auth import codes
from sudoo.config import settings
from sudoo.core import audit, events, vault
from sudoo.storage import Document, storage

from . import clients

log = logging.getLogger(__name__)

COLLECTION = "client_confirmations"
PURPOSE = "client-action"
TEMPLATE = "client_action_confirm"
PATH = "/confirm"
KINDS = ("switch", "lock", "emergency", "approve", "allow")
FORGET_SECONDS = 7 * 86400


class ConfirmError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _digest(secret: str) -> str:
    key = hmac.new(vault.key(), b"client-confirmation-links", hashlib.sha256).digest()
    return hmac.new(key, secret.encode(), hashlib.sha256).hexdigest()


def required() -> bool:
    return bool(settings.client_confirmations)


def lifetime() -> int:
    return int(settings.client_confirm_minutes) * 60


def _deliver(job: Callable[[], None]) -> None:
    """Send now (the client is waiting for it; tests replace it too)."""
    job()


def ask(
    project: Mapping[str, Any], client: str, sid: str, kind: str, action: Mapping[str, Any], summary: str
) -> Document:
    """A new confirmation of `action` (what the API will run once confirmed); the client is e-mailed. Raises
    ConfirmError when it cannot be sent."""
    from sudoo.mail import MailError, mailer, send

    if kind not in KINDS:
        raise ConfirmError("Unknown action.", 400)
    if not mailer().ready:
        raise ConfirmError(
            "This change needs your confirmation by e-mail, and e-mail is not set up on this panel yet. Ask your "
            "provider to set it up."
        )
    account = accounts.get(client) or {}
    address = str(account.get("email") or "")
    if not address:
        raise ConfirmError("Your account has no e-mail address to confirm with.", 400)
    _forget_old()
    confirmation_id = secrets.token_urlsafe(9)
    secret = secrets.token_urlsafe(24)
    code = codes.new((client, f"{PURPOSE}:{confirmation_id}"), valid_seconds=lifetime())
    now = time.time()
    found: Document = {
        "id": confirmation_id,
        "project": project["id"],
        "client": client,
        "sid": sid,
        "kind": kind,
        "action": dict(action),
        "summary": summary[:300],
        "created": now,
        "expires": now + lifetime(),
        "secret_hash": _digest(secret),
        "status": "pending",
    }
    storage().put(COLLECTION, confirmation_id, found)
    link = f"{clients.portal_url().rstrip('/')}{PATH}#{confirmation_id}.{secret}"
    arabic = accounts.language(account) == "ar"
    minutes = lifetime() // 60

    def job() -> None:
        send(address, f"{TEMPLATE}_ar" if arabic else TEMPLATE, name=accounts.display_name(client, account),
             project=str(project.get("name") or project["id"]), action=summary, code=code, minutes=str(minutes),
             link=link)  # fmt: skip

    try:
        _deliver(job)
    except MailError as error:
        storage().delete(COLLECTION, confirmation_id)
        raise ConfirmError(f"The confirmation e-mail could not be sent: {error}", 502) from error
    audit.record(client, clients._ip(), "client action waiting for e-mail confirmation",
                 detail=f"{project['id']}: {summary}"[:300])  # fmt: skip
    events.publish(clients.TOPIC, str(project["id"]), {"project": str(project["id"])})
    return found


def pending(project_id: str, client: str) -> list[Document]:
    """The client's confirmations of the project still waiting (after expiring those whose time passed)."""
    now = time.time()
    shown = []
    for found in storage().all(COLLECTION).values():
        if found.get("project") != project_id or found.get("client") != client or found.get("status") != "pending":
            continue
        if float(found.get("expires") or 0) <= now:
            _end(found, "expired")
            continue
        shown.append(found)
    return sorted(shown, key=lambda f: -float(f.get("created") or 0))


def confirm(confirmation_id: str, client: str, sid: str, *, code: str = "", token: str = "") -> Document:
    """Use the confirmation up when `code` (or the link's `token`) is right, in the session that asked; returns it
    (the caller runs its action). Raises ConfirmError."""
    found = storage().get(COLLECTION, confirmation_id) if confirmation_id else None
    if found is None or found.get("client") != client:
        raise ConfirmError("There is no such confirmation.", 404)
    if found.get("status") != "pending":
        raise ConfirmError(f"This was {found.get('status')} already.", 410)
    if float(found.get("expires") or 0) <= time.time():
        _end(found, "expired")
        raise ConfirmError("This confirmation expired: do it again to get a new e-mail.", 410)
    if not hmac.compare_digest(str(found.get("sid") or ""), sid or "-"):
        audit.record(client, clients._ip(), "client confirmation refused (another session)", ok=False,
                     detail=f"{found['project']}: {found.get('summary')}"[:300])  # fmt: skip
        raise ConfirmError("Confirm in the browser where you asked for this change (the same session).", 403)
    if token:
        right = hmac.compare_digest(str(found.get("secret_hash") or ""), _digest(token))
    else:
        right = codes.check((client, f"{PURPOSE}:{confirmation_id}"), code)[0]
    if not right:
        audit.record(client, clients._ip(), "client confirmation refused (wrong code or link)", ok=False,
                     detail=f"{found['project']}: {found.get('summary')}"[:300])  # fmt: skip
        raise ConfirmError("The code is not correct, or it has expired.", 401)
    used: list[Document] = []

    def take(current: Document | None) -> Document | None:
        if current is None or current.get("status") != "pending":
            return current
        changed = {**current, "status": "confirmed", "confirmed_at": time.time()}
        used.append(changed)
        return changed

    storage().change(COLLECTION, confirmation_id, take)  # atomic: two clicks of one link run the action once
    if not used:
        raise ConfirmError("This was confirmed already.", 410)
    audit.record(
        client,
        clients._ip(),
        "client action confirmed by e-mail",
        detail=f"{found['project']}: {found.get('summary')}"[:300],
    )
    return used[0]


def cancel(confirmation_id: str, client: str) -> None:
    found = storage().get(COLLECTION, confirmation_id) if confirmation_id else None
    if found is None or found.get("client") != client or found.get("status") != "pending":
        raise ConfirmError("There is no such confirmation waiting.", 404)
    _end(found, "cancelled")


def _end(found: Mapping[str, Any], status: str) -> None:
    storage().put(COLLECTION, str(found["id"]), {**found, "status": status, "ended_at": time.time()})
    audit.record(str(found.get("client")), None, f"client action not confirmed ({status})",
                 detail=f"{found.get('project')}: {found.get('summary')}"[:300])  # fmt: skip


def _forget_old() -> None:
    now = time.time()
    for key, found in list(storage().all(COLLECTION).items()):
        if now - float(found.get("created") or 0) > FORGET_SECONDS:
            storage().delete(COLLECTION, key)


def resource(found: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": found.get("id"),
        "kind": found.get("kind"),
        "summary": found.get("summary"),
        "asked_at": clients._iso(found.get("created")),
        "expires_at": clients._iso(found.get("expires")),
        "status": found.get("status"),
    }
