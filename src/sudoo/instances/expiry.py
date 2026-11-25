"""The end of an instance's lifetime: who is told, and when.

An instance with an expiry (never production) is stopped when it expires and deleted EXPIRY_GRACE_DAYS later
(background.delete_expired_instances). The people who look after it get two e-mails:

  instance_expiry_warning   EXPIRY_WARNING_DAYS before it expires, once for each expiry date (an extension is a new
                            date, so it may be warned again)
  instance_stopped          when it was stopped, with the date it will be deleted

They are the person who made it, the token's creator when a connected app made it, and the administrators of its
project (projects.access). Each person may turn the e-mails off on their own account page (EXPIRY_EMAIL_FIELD), except
a project administrator who made the instance. Nothing is sent while e-mail is not set up; a failing mail server never
stops a job, and every pass of the warning is safe to repeat (the claim for an expiry date is kept in the storage).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sudoo import accounts, api_tokens
from sudoo.config import settings
from sudoo.core import audit
from sudoo.instances import operations, store
from sudoo.storage import storage

log = logging.getLogger(__name__)

FIELD = "expiry_email"  # the account's own choice: False turns the e-mails off, True or absent keeps them
COLLECTION = "expiry_notices"


def own_choice(account: Mapping[str, Any]) -> bool | None:
    choice = account.get(FIELD)
    return choice if isinstance(choice, bool) else None


def recipients(name: str) -> list[tuple[str, dict[str, Any]]]:
    """The accounts with an address that look after instance `name` and have not turned the e-mails off."""
    from sudoo import projects
    from sudoo.projects import notices

    env = store.read_env(name)
    maker = env.get("CREATED_BY") or ""
    project = projects.get(env["PROJECT"]) if env.get("PROJECT") else None
    admins = dict(notices.admins(project)) if project else {}
    if maker.startswith("api:"):  # made by a connected app: the person who made the token, while it exists
        token = next((t for t in api_tokens.all_tokens() if t["name"] == maker[4:]), None)
        maker = str(token.get("created_by") or "") if token else ""
    people: dict[str, dict[str, Any]] = {}
    if maker and (account := accounts.get(maker)) and account.get("email"):
        people[maker] = account
    people.update(admins)
    found = []
    for user, account in people.items():
        owner_and_admin = user == maker and user in admins  # an administrator who made it cannot opt out
        if own_choice(account) is False and not owner_and_admin:
            continue
        found.append((user, account))
    return found


def _when(at: float) -> str:
    return f"{datetime.fromtimestamp(at, settings.zone).strftime('%A %d %B %Y, %H:%M')} {settings.timezone}"


def _send(name: str, key: str, **values: Any) -> int:
    from sudoo import projects
    from sudoo.mail.render import panel_url
    from sudoo.projects import notices

    env = store.read_env(name)
    project = projects.get(env["PROJECT"]) if env.get("PROJECT") else None
    base = (panel_url() or "").rstrip("/")
    sent = 0
    for user, account in recipients(name):
        if notices._send(  # noqa: SLF001 - the same sender as the access e-mails (language, failures)
            str(account["email"]), key, account, name=account.get("first_name") or user, instance=name,
            project=(project or {}).get("name") or "", link=f"{base}/i/{name}", **values,
        ):  # fmt: skip
            sent += 1
    if sent:
        audit.record("system", None, f"instance {key.removeprefix('instance_')} e-mail sent", name,
                     detail=f"{sent} e-mail{'s' if sent != 1 else ''}")  # fmt: skip
    return sent


def notify_stopped(name: str, deletion: float) -> int:
    """Tell the instance's people it was stopped and will be deleted at `deletion`. Never raises."""
    try:
        env = store.read_env(name)
        expires = operations.expires_at(env) or 0
        return _send(name, "instance_stopped", expires=_when(expires), delete_on=_when(deletion))
    except Exception as error:  # noqa: BLE001 - reported, never blocks the job
        log.warning("expiry e-mail for %s not sent: %r", name, error)
        return 0


def warn_expiring(now: float | None = None) -> list[str]:
    """E-mail the people of each instance that expires within EXPIRY_WARNING_DAYS, once for each expiry date;
    return the instances warned. Safe to repeat: the claim for (instance, expiry date) is made before sending."""
    from sudoo.mail import mailer

    moment = time.time() if now is None else now
    window = settings.expiry_warning_days * 86400
    warned: list[str] = []
    if not window:
        return warned
    try:
        ready = mailer().ready
    except Exception:  # noqa: BLE001
        ready = False
    if not ready:
        return warned
    for name in store.names():
        env = store.read_env(name)
        expires = operations.expires_at(env)
        if not expires or not 0 < expires - moment <= window:
            continue
        key = f"{name}:{expires}"
        first: list[bool] = []

        def claim(document: Any, first: list[bool] = first) -> Any:
            if document:
                return document
            first.append(True)
            return {"at": moment}

        storage().change(COLLECTION, key, claim)
        if not first:
            continue
        try:
            if _send(
                name, "instance_expiry_warning", expires=_when(expires), days=max(1, round((expires - moment) / 86400))
            ):
                warned.append(name)
        except Exception as error:  # noqa: BLE001 - reported, never blocks the loop
            log.warning("expiry warning for %s not sent: %r", name, error)
    return warned
