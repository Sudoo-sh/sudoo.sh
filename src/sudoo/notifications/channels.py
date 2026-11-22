"""How each kind of channel is written to: the request body per service, and sending one notification.

    slack      a Slack incoming webhook ({"text"}, mrkdwn; < > & escaped so nothing becomes a mention or link)
    discord    a Discord webhook ({"content"}, mentions turned off)
    teams      a Microsoft Teams Workflows webhook (an Adaptive Card)
    telegram   the Telegram Bot API, sendMessage to a chat (plain text, no link previews)
    webhook    your own receiver: JSON signed with HMAC-SHA256 (X-Sudoo-Signature: sha256=<hex>)
    email      the panel's mailer, the "alert" e-mail (mail/catalog.py)

Slack, Discord and Teams are written by alerts/webhooks.py, the same way alerts are: a notification is shaped as an
alert record for them. Words that come from elsewhere (a branch name, a job's hint) never mention a channel or form a
link.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sudoo.alerts import webhooks as alert_webhooks

from . import guard

TYPES = ("slack", "discord", "teams", "telegram", "webhook", "email")
SECRETS: dict[str, tuple[str, ...]] = {
    "slack": ("url",),
    "discord": ("url",),
    "teams": ("url",),
    "telegram": ("bot_token",),
    "webhook": ("url", "signing_secret"),
    "email": (),
}
"""The values of each kind that are secrets: stored encrypted, never shown (only their last 4 characters)."""

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_TOKEN_RE = re.compile(r"^\d{3,15}:[A-Za-z0-9_-]{30,64}$")
TELEGRAM_CHAT_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")
EVENT_HEADER = "X-Sudoo-Event"
DELIVERY_HEADER = "X-Sudoo-Delivery"
_TEXT_LIMIT = 3500  # Telegram allows 4096 characters per message


def _iso(at: float | None) -> str | None:
    return datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds") if at else None


def as_alert(note: Mapping[str, Any]) -> dict[str, Any]:
    """A notification shaped as an alert record, for alerts/webhooks.py and the alert e-mail."""
    return {
        "id": note["id"],
        "type": note["event"],
        "severity": note.get("severity") or "info",
        "title": note["title"],
        "text": note["text"],
        "hint": None,
        "details": list(note.get("details") or []),
        "count": 1,
        "at": note["at"],
        "instance": note.get("instance"),
        "path": note.get("path"),
    }


def plain_text(note: Mapping[str, Any], link: str | None) -> str:
    mark = {"recovered": "Resolved", "info": "Notice"}.get(str(note.get("severity")), "Alert")
    parts = [f"{mark}: {note['title']}", str(note["text"])]
    parts += [f"- {item}" for item in note.get("details") or []]
    text = "\n".join(p for p in parts if p)[:_TEXT_LIMIT]
    return text + (f"\n{link}" if link else "")


def payload(
    kind: str, note: Mapping[str, Any], link: str | None, config: Mapping[str, Any], now: float | None = None
) -> dict[str, Any]:
    """The JSON document sent to a channel of `kind` for `note`."""
    if kind in ("slack", "discord", "teams"):
        return alert_webhooks.payload(as_alert(note), kind, link, now)
    if kind == "telegram":
        return {
            "chat_id": str(config.get("chat_id") or ""),
            "text": plain_text(note, link),
            "disable_web_page_preview": True,
        }
    return {
        "event": note["event"],
        "id": note["id"],
        "project": {"id": note["project"], "name": note.get("project_name")},
        "severity": note.get("severity") or "info",
        "title": note["title"],
        "text": note["text"],
        "details": list(note.get("details") or []),
        "instance": note.get("instance"),
        "branch": note.get("branch"),
        "link": link,
        "at": _iso(note.get("at")),
        "sent_at": _iso(time.time() if now is None else now),
        **({"test": True} if note.get("test") else {}),
    }


def body(
    kind: str, note: Mapping[str, Any], link: str | None, config: Mapping[str, Any], now: float | None = None
) -> bytes:
    data = json.dumps(payload(kind, note, link, config, now), separators=(",", ":"), ensure_ascii=False).encode()
    if len(data) > alert_webhooks.MAX_BODY:
        smaller = {**note, "details": [], "text": str(note["text"])[:1000]}
        data = json.dumps(payload(kind, smaller, link, config, now), separators=(",", ":"), ensure_ascii=False).encode()
    return data[: alert_webhooks.MAX_BODY]


def signature(secret: str, data: bytes) -> str:
    """X-Sudoo-Signature: "sha256=" and the hex HMAC-SHA256 of the raw body (as alerts' webhooks sign)."""
    return alert_webhooks.signature(secret, data)


def send(
    kind: str,
    note: Mapping[str, Any],
    link: str | None,
    config: Mapping[str, Any],
    secrets: Mapping[str, str],
    timeout: float,
) -> str:
    """Deliver one notification to one channel; a short note of what happened ("HTTP 200", "e-mailed to 2").
    Raises guard.Refused (never worth retrying) or guard.DeliveryError."""
    if kind == "email":
        return _email(note, link, config)
    if kind == "telegram":
        url = f"{TELEGRAM_API}/bot{secrets.get('bot_token', '')}/sendMessage"
    else:
        url = secrets.get("url", "")
    if not url:
        raise guard.Refused("The channel has no address yet.")
    data = body(kind, note, link, config)
    headers = {"Content-Type": "application/json", "User-Agent": "Sudoo.sh-notifications"}
    if kind == "webhook":
        headers[EVENT_HEADER] = str(note["event"])
        headers[DELIVERY_HEADER] = str(note["id"])
        if secrets.get("signing_secret"):
            headers[alert_webhooks.SIGNATURE_HEADER] = signature(secrets["signing_secret"], data)
    status = guard.post(url, data, headers, timeout)
    return f"HTTP {status}"


def _email(note: Mapping[str, Any], link: str | None, config: Mapping[str, Any]) -> str:
    from sudoo import mail
    from sudoo.alerts import _mail_values

    addresses = [a for a in config.get("addresses") or [] if a]
    if not addresses:
        raise guard.Refused("The channel has no e-mail address.")
    if not mail.mailer().ready:
        raise guard.DeliveryError("e-mail is not set up yet (SMTP settings)", retry=False)
    record = as_alert(note)
    values = _mail_values(record, None) | {"link": link or ""}
    sent, failed = 0, ""
    for address in addresses:
        try:
            mail.send(address, "alert", **values)
            sent += 1
        except (mail.MailError, OSError) as error:
            failed = type(error).__name__
    if not sent:
        raise guard.DeliveryError(f"the e-mail could not be sent ({failed})")
    return f"e-mailed to {sent} of {len(addresses)}"
