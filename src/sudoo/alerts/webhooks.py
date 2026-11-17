"""Webhooks: alerts posted to other systems (ALERT_WEBHOOK_1_URL to ALERT_WEBHOOK_3_URL).

Formats (ALERT_WEBHOOK_<n>_FORMAT):

    json      {"event": "alert", "id", "type", "severity", "title", "text", "hint", "details", "instance", "link",
               "count", "at", "sent_at"}, for your own receiver
    slack     a Slack incoming webhook ({"text"})
    discord   a Discord webhook ({"content"}, mentions turned off)
    teams     a Microsoft Teams incoming webhook or Workflows trigger (an Adaptive Card)

When the webhook has a secret (ALERT_WEBHOOK_<n>_SECRET), every request carries
`X-Sudoo-Signature: sha256=<hex HMAC-SHA256 of the raw body, keyed with the secret>`; compare it in constant time
before trusting the body, and reject a `sent_at` older than a few minutes (replays).

Rules: https:// only (http:// for localhost while testing), checked again before each request; redirects are never
followed (a 3xx counts as failed); ALERT_WEBHOOK_TIMEOUT_SECONDS per request; the body is at most 64 KB and at most
64 KB of the answer is read. A webhook's address and secret are secrets: never shown, logged or audited.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sudoo.config import settings

FORMATS = ("json", "slack", "discord", "teams")
NUMBERS = (1, 2, 3)
MAX_BODY = 64 * 1024
MAX_ANSWER = 64 * 1024
SIGNATURE_HEADER = "X-Sudoo-Signature"
_TEXT_LIMIT = 1800  # Discord allows 2000 characters per message; the others more


class WebhookError(RuntimeError):
    """The webhook was not delivered; the text says why (never the address)."""


def configured() -> list[tuple[int, str, str, str]]:
    """(number, url, secret, format) of each webhook with an address."""
    found = []
    for number in NUMBERS:
        url = getattr(settings, f"alert_webhook_{number}_url")
        if url:
            found.append(
                (number, url, getattr(settings, f"alert_webhook_{number}_secret"),
                 getattr(settings, f"alert_webhook_{number}_format"))
            )  # fmt: skip
    return found


def check_url(url: str) -> None:
    """Raise WebhookError unless `url` is https (or http to localhost) without credentials."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        raise WebhookError("its address is not valid") from None
    local = host in ("localhost", "127.0.0.1", "::1")
    if parts.scheme not in ("https", "http") or not host or (parts.scheme == "http" and not local):
        raise WebhookError("its address must start with https:// (http:// only for localhost)")
    if parts.username or parts.password:
        raise WebhookError("its address must not contain a user name or password")


def _iso(at: float | None) -> str | None:
    return datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds") if at else None


def _lines(record: dict[str, Any], link: str | None, bold: tuple[str, str]) -> str:
    mark = {"recovered": "Resolved", "info": "Notice"}.get(record["severity"], "Alert")
    parts = [f"{bold[0]}{mark}: {record['title']}{bold[1]}", record["text"]]
    if record.get("hint"):
        parts.append(record["hint"])
    parts += [f"- {item}" for item in record.get("details") or []]
    if record.get("count", 1) > 1:
        parts.append(f"(seen {record['count']} times)")
    text = "\n".join(part for part in parts if part)[:_TEXT_LIMIT]
    return text + (f"\n{link}" if link else "")


def payload(record: dict[str, Any], form: str, link: str | None, now: float | None = None) -> dict[str, Any]:
    """The JSON document sent for `record` in format `form`."""
    if form == "slack":
        text = _lines(record, None, ("*", "*"))
        return {"text": text + (f"\n<{link}|Open Sudoo.sh>" if link else "")}
    if form == "discord":
        return {"content": _lines(record, link, ("**", "**")), "allowed_mentions": {"parse": []}}
    if form == "teams":
        body: list[dict[str, Any]] = [
            {"type": "TextBlock", "text": record["title"], "weight": "Bolder", "size": "Medium", "wrap": True},
            {"type": "TextBlock", "text": record["text"][:_TEXT_LIMIT], "wrap": True},
        ]
        if record.get("hint"):
            body.append({"type": "TextBlock", "text": record["hint"][:_TEXT_LIMIT], "wrap": True, "isSubtle": True})
        for item in record.get("details") or []:
            body.append({"type": "TextBlock", "text": f"- {item}", "wrap": True, "fontType": "Monospace"})
        card: dict[str, Any] = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": body,
        }
        if link:
            card["actions"] = [{"type": "Action.OpenUrl", "title": "Open Sudoo.sh", "url": link}]
        return {
            "type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
        }
    return {
        "event": "alert",
        "id": record["id"],
        "type": record["type"],
        "severity": record["severity"],
        "title": record["title"],
        "text": record["text"],
        "hint": record.get("hint"),
        "details": record.get("details") or [],
        "instance": record.get("instance"),
        "link": link,
        "count": record.get("count", 1),
        "at": _iso(record.get("at")),
        "sent_at": _iso(time.time() if now is None else now),
    }


def body(record: dict[str, Any], form: str, link: str | None, now: float | None = None) -> bytes:
    """The request body: compact JSON, at most MAX_BODY bytes (details are dropped, then the text cut, to fit)."""
    data = json.dumps(payload(record, form, link, now), separators=(",", ":"), ensure_ascii=False).encode()
    if len(data) > MAX_BODY:
        smaller = {**record, "details": [], "hint": None, "text": record["text"][:2000]}
        data = json.dumps(payload(smaller, form, link, now), separators=(",", ":"), ensure_ascii=False).encode()
    return data[:MAX_BODY]


def signature(secret: str, data: bytes) -> str:
    """The X-Sudoo-Signature value: "sha256=" and the hex HMAC-SHA256 of the raw body."""
    return "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # a 3xx ends as an HTTPError: an alert never goes somewhere it was not configured to go


_OPENER = urllib.request.build_opener(_NoRedirect)


def post(url: str, secret: str, form: str, record: dict[str, Any], link: str | None, timeout: float) -> int:
    """POST one alert; the HTTP status (2xx). Raises WebhookError, without the address, when it was not delivered."""
    check_url(url)
    data = body(record, form if form in FORMATS else "json", link)
    headers = {"Content-Type": "application/json", "User-Agent": "Sudoo.sh-alerts", "X-Sudoo-Event": record["type"]}
    if secret:
        headers[SIGNATURE_HEADER] = signature(secret, data)
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with _OPENER.open(request, timeout=timeout) as answer:
            answer.read(MAX_ANSWER)
            status = int(answer.status)
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            raise WebhookError(f"answered with a redirect (HTTP {error.code}), which is not followed") from None
        raise WebhookError(f"answered HTTP {error.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        raise WebhookError(f"could not be reached ({type(reason).__name__})") from None
    if not 200 <= status < 300:
        raise WebhookError(f"answered HTTP {status}")
    return status
