"""Alerts: telling the right people when something needs attention.

What raises an alert (watch.py): a job failed, an update failed and was rolled back (or its rollback failed too), a
backup failed, an instance that should run does not answer, the disk is almost full, memory stays above the capacity
warning, an instance was changed outside the panel (drift), the panel or an instance has no public link. When a
condition is over (back online, disk freed), a recovery notice follows, if its alert was sent.

Where it goes: e-mail (the panel's mailer) to every super admin with an address (ALERT_SUPER_ADMINS) and to
ALERT_EMAILS, each alert at once or one summary a day (ALERT_DIGEST); up to three webhooks (webhooks.py: signed
JSON, or Slack, Discord and Teams incoming webhooks); and the bell of the web app (GET /api/v1/alerts and the live
event "alert", for those who may read the audit log).

The rules are settings (config.py, ALERT_SETTINGS), each with a default: every kind of alert can be turned off, the
thresholds and durations changed, and ALERT_QUIET_MINUTES keeps the same alert (same kind, same instance) from being
sent more than once in that time; a repeat inside it only counts up on the alert already listed.

What an alert says is written here, never copied from a log: no password, token or address with credentials is ever
sent (text is redacted all the same).
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sudoo.config import settings
from sudoo.core import events
from sudoo.core.shell import redact
from sudoo.storage import storage

from . import webhooks

COLLECTION = "alerts"
META = "alert_meta"
KEEP = 200  # alerts listed in the bell (and kept in storage)
TOPIC = "alert"  # the live event of a new or changed alert

TYPES: dict[str, str | None] = {
    "job_failed": "alert_job_failed",
    "update_failed": "alert_update_failed",
    "backup_failed": "alert_backup_failed",
    "instance_down": "alert_instance_down",
    "disk": "alert_disk",
    "memory": "alert_memory",
    "drift": "alert_drift",
    "public_link": "alert_public_link",
    "test": None,
}
"""Kind of alert -> the setting that turns it on (a test alert is always sent)."""


class AlertError(RuntimeError):
    """An alert could not be sent; the text says why, for the person who asked."""


class NotConfigured(AlertError):
    """There is nothing to send to: e-mail is not set up, nobody receives alert e-mails, or the webhook has no
    address."""


@dataclass
class Alert:
    """What happened, in plain words. `key` identifies "the same alert" for the quiet period (default: kind and
    instance); `path` is the page of the web app that shows it (/i/<name>/activity?job=<id>)."""

    type: str
    title: str
    text: str
    instance: str | None = None
    path: str | None = None
    hint: str | None = None
    details: list[str] = field(default_factory=list)
    severity: str = "problem"  # "problem", "recovered" or "info"
    key: str | None = None

    def identity(self) -> str:
        return self.key or f"{self.type}:{self.instance or ''}"


_LOCK = threading.RLock()
_RECORDS: dict[str, dict[str, Any]] | None = None  # id -> alert record, loaded on first use


def _all() -> dict[str, dict[str, Any]]:
    global _RECORDS
    if _RECORDS is None:
        _RECORDS = storage().all(COLLECTION)
    return _RECORDS


def _save(record: dict[str, Any]) -> None:
    storage().put(COLLECTION, record["id"], record)


def _prune() -> None:
    records = _all()
    for old in sorted(records.values(), key=lambda r: r["last_at"])[: max(0, len(records) - KEEP)]:
        records.pop(old["id"], None)
        storage().delete(COLLECTION, old["id"])


def _publish(record: dict[str, Any]) -> None:
    events.publish(TOPIC, record["id"], dict(record))


def enabled(kind: str) -> bool:
    """Whether alerts of this kind are sent now (ALERTS and the kind's own switch)."""
    switch = TYPES.get(kind)
    return bool(settings.alerts) and (switch is None or bool(getattr(settings, switch)))


def _clean(text: str | None, limit: int = 2000) -> str:
    return redact(str(text or "")).strip()[:limit]


def _latest(identity: str, severity: str) -> dict[str, Any] | None:
    found = [r for r in _all().values() if r["key"] == identity and r["severity"] == severity]
    return max(found, key=lambda r: r["sent_at"]) if found else None


def send(alert: Alert, now: float | None = None) -> dict[str, Any] | None:
    """Record an alert, show it in the bell and deliver it (e-mail, webhooks) in the background. Returns the record,
    or None when this kind of alert is off. Inside the quiet period of the same alert, nothing is delivered: the
    listed alert counts the repeat (and opens again if it was resolved); that record is returned."""
    if not enabled(alert.type):
        return None
    now = time.time() if now is None else now
    identity = alert.identity()
    quiet = settings.alert_quiet_minutes * 60
    with _LOCK:
        previous = _latest(identity, alert.severity)
        if previous and quiet > 0 and now - previous["sent_at"] < quiet:
            previous.update(count=previous.get("count", 1) + 1, last_at=now)
            if alert.severity == "problem":
                previous["resolved_at"] = None
            _save(previous)
            _publish(previous)
            return previous
        record: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "key": identity,
            "type": alert.type,
            "severity": alert.severity,
            "title": _clean(alert.title, 200),
            "text": _clean(alert.text),
            "hint": _clean(alert.hint) or None,
            "details": [_clean(item, 300) for item in alert.details[:20]],
            "instance": alert.instance,
            "path": alert.path,
            "at": now,
            "last_at": now,
            "sent_at": now,
            "count": 1,
            "resolved_at": None,
            "emailed": False,
            "delivery": [],
        }
        _all()[record["id"]] = record
        _save(record)
        _prune()
    _publish(record)
    _deliver_later(record["id"])
    return record


def recover(key: str, alert: Alert, now: float | None = None) -> dict[str, Any] | None:
    """A problem is over: mark its alert resolved and, with ALERT_RECOVERY, send `alert` (severity "recovered") as
    the notice. Nothing happens when no alert was sent for `key` or it is resolved already."""
    now = time.time() if now is None else now
    with _LOCK:
        problem = _latest(key, "problem")
        if not problem or problem.get("resolved_at"):
            return None
        problem["resolved_at"] = now
        _save(problem)
    _publish(problem)
    if not settings.alert_recovery:
        return None
    alert.severity, alert.key = "recovered", key
    return send(alert, now)


def close(key: str, now: float | None = None) -> None:
    """Mark the alert of `key` resolved without a notice (the instance was paused, switched off or deleted)."""
    with _LOCK:
        problem = _latest(key, "problem")
        if not problem or problem.get("resolved_at"):
            return
        problem["resolved_at"] = time.time() if now is None else now
        _save(problem)
    _publish(problem)


def recent(limit: int = 50) -> list[dict[str, Any]]:
    """The newest alerts first (repeats count as new)."""
    with _LOCK:
        records = sorted(_all().values(), key=lambda r: r["last_at"], reverse=True)[:limit]
        return [dict(r) for r in records]


def resource(record: dict[str, Any]) -> dict[str, Any]:
    """An alert as the API and the live event show it."""
    shown = {k: v for k, v in record.items() if k not in ("emailed", "sent_at")}
    shown["link"] = record.get("path")
    return shown


# ---------------------------------------------------------------- who receives e-mails


def recipients() -> list[str]:
    """Addresses alert e-mails go to: ALERT_EMAILS, and every super admin with an address (ALERT_SUPER_ADMINS)."""
    found = [address for address in settings.alert_emails.split(",") if address]
    if settings.alert_super_admins:
        from sudoo import accounts

        found += [
            account["email"]
            for account in accounts.all_users().values()
            if account.get("role") == "super_admin" and account.get("email")
        ]
    return list(dict.fromkeys(address.strip().lower() for address in found))


def panel_url() -> str | None:
    try:
        from sudoo import exposure

        return exposure.address(exposure.PANEL)
    except Exception:  # noqa: BLE001 - an alert without the panel's link is still worth sending
        return None


def link(record: dict[str, Any], base: str | None) -> str | None:
    if not base:
        return None
    return base.rstrip("/") + (record.get("path") or "/")


# ---------------------------------------------------------------- delivery


_QUEUE: queue.Queue[str] = queue.Queue()
_WORKER = threading.Event()


def _deliver_later(record_id: str) -> None:
    """Deliver in one background thread, in order: a slow mail server or webhook never holds a job or the monitor."""
    _QUEUE.put(record_id)
    if not _WORKER.is_set():
        _WORKER.set()
        threading.Thread(target=_work_forever, daemon=True, name="alerts").start()


def _work_forever() -> None:
    while True:
        record_id = _QUEUE.get()
        try:
            with _LOCK:
                record = dict(_all().get(record_id) or {})
            if record:
                deliver(record)
        except Exception as exc:  # noqa: BLE001 - the next alert must still go out
            print(f"alert {record_id}: {exc!r}", flush=True)
        finally:
            _QUEUE.task_done()


def drain() -> None:
    """Wait until every alert waiting for delivery went out (tests)."""
    _QUEUE.join()


def _note(record: dict[str, Any], notes: list[str], emailed: bool) -> None:
    with _LOCK:
        stored = _all().get(record["id"])
        if stored is None:
            return
        stored["delivery"] = notes
        stored["emailed"] = stored.get("emailed") or emailed
        _save(stored)
        shown = dict(stored)
    _publish(shown)


def deliver(record: dict[str, Any]) -> list[str]:
    """Send one alert by e-mail (unless the daily summary is on) and to every webhook; returns what happened, also
    noted on the alert. Failures are noted and logged, never raised."""
    notes: list[str] = []
    emailed = False
    base = panel_url()
    if not settings.alert_digest:
        to = recipients()
        if to:
            try:
                sent = email(record, to, base)
                notes.append(f"e-mailed to {sent} of {len(to)} {'address' if len(to) == 1 else 'addresses'}")
                emailed = sent > 0
            except AlertError as error:
                notes.append(str(error))
    for number, url, secret, form in webhooks.configured():
        try:
            status = webhooks.post(
                url, secret, form, record, link(record, base), settings.alert_webhook_timeout_seconds
            )
            notes.append(f"webhook {number}: delivered (HTTP {status})")
        except webhooks.WebhookError as error:
            notes.append(f"webhook {number}: {error}")
            from sudoo.core import audit

            audit.record("panel", None, "alert webhook failed", record.get("instance"), ok=False,
                         detail=f"webhook {number}: {error}")  # fmt: skip
    _note(record, notes, emailed)
    return notes


def _mail_values(record: dict[str, Any], base: str | None) -> dict[str, Any]:
    """The placeholders of the "alert" e-mail (mail/catalog.py)."""
    from sudoo.mail.catalog import lines_block

    count = int(record.get("count") or 1)
    return {
        "kind": {"recovered": "Resolved", "info": "Notice"}.get(record["severity"], "Alert"),
        "alert_title": record["title"],
        "alert_text": record["text"],
        "hint": record.get("hint") or "",
        "details": lines_block([str(item) for item in record.get("details") or []]),
        "repeats": str(count) if count > 1 else "",
        "link": link(record, base) or "",
    }


def email(record: dict[str, Any], to: list[str], base: str | None) -> int:
    """E-mail one alert to each address; the number sent. Raises AlertError when e-mail is not set up or nothing
    could be sent."""
    from sudoo import mail

    if not mail.mailer().ready:
        raise NotConfigured("E-mail is not set up yet: fill in the mail server (SMTP) settings first.")
    sent, failed = 0, ""
    for address in to:
        try:
            mail.send(address, "alert", **_mail_values(record, base))
            sent += 1
        except (mail.MailError, OSError) as error:
            failed = str(error)
            print("alert e-mail:", repr(error), flush=True)
    if not sent:
        raise AlertError(failed or "The e-mail could not be sent.")
    return sent


# ---------------------------------------------------------------- test alerts


def send_test(channel: str, number: int | None = None, user: str = "") -> dict[str, Any]:
    """Send a test alert now, to the e-mail recipients or to one webhook, with the saved settings. Returns
    {"alert", "sent_to"} or {"alert", "webhook", "status"}; raises AlertError saying what is missing or failed."""
    alert = Alert("test", "Test alert", f"This is a test alert{f' sent by {user}' if user else ''}. Alerts work.",
                  path="/settings?tab=alerts", severity="info", key=f"test:{uuid.uuid4().hex}")  # fmt: skip
    base = panel_url()
    if channel == "email":
        to = recipients()
        if not to:
            raise NotConfigured(
                "Nobody receives alert e-mails: add addresses, or give a super admin an e-mail address."
            )
        record = _record_test(alert)
        sent = email(record, to, base)
        _note(record, [f"e-mailed to {sent} of {len(to)} {'address' if len(to) == 1 else 'addresses'}"], True)
        return {"alert": record["id"], "sent_to": to}
    found = {n: (url, secret, form) for n, url, secret, form in webhooks.configured()}
    if number not in found:
        raise NotConfigured(f"Webhook {number} has no address yet: save one first.")
    url, secret, form = found[number]
    record = _record_test(alert)
    try:
        status = webhooks.post(url, secret, form, record, link(record, base), settings.alert_webhook_timeout_seconds)
    except webhooks.WebhookError as error:
        _note(record, [f"webhook {number}: {error}"], False)
        raise AlertError(f"Webhook {number}: {error}") from error
    _note(record, [f"webhook {number}: delivered (HTTP {status})"], False)
    return {"alert": record["id"], "webhook": number, "status": status}


def _record_test(alert: Alert) -> dict[str, Any]:
    """A test alert in the bell, delivered by the caller only (not through the queue)."""
    now = time.time()
    record: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12], "key": alert.identity(), "type": "test", "severity": "info",
        "title": alert.title, "text": alert.text, "hint": None, "details": [], "instance": None, "path": alert.path,
        "at": now, "last_at": now, "sent_at": now, "count": 1, "resolved_at": None, "emailed": True, "delivery": [],
    }  # fmt: skip
    with _LOCK:
        _all()[record["id"]] = record
        _save(record)
        _prune()
    _publish(record)
    return record


# ---------------------------------------------------------------- daily summary


def digest_slot(now: float | None = None) -> float:
    """The latest time the summary was due (ALERT_DIGEST_TIME today, or yesterday if that is still ahead)."""
    current = datetime.fromtimestamp(time.time() if now is None else now, settings.zone)
    hour, minute = (int(part) for part in settings.alert_digest_time.split(":"))
    slot = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > current:
        slot -= timedelta(days=1)
    return slot.timestamp()


def send_digest(now: float | None = None) -> int:
    """With ALERT_DIGEST: once the summary time has passed, e-mail the alerts not e-mailed yet in one message.
    Returns how many alerts it listed (0: nothing due, nothing new, or no recipient)."""
    if not settings.alerts or not settings.alert_digest:
        return 0
    slot = digest_slot(now)
    last = (storage().get(META, "digest") or {}).get("at", 0)
    if last >= slot:
        return 0
    with _LOCK:
        due = sorted(
            (
                dict(r)
                for r in _all().values()
                if not r.get("emailed") and r["type"] != "test" and max(last, slot - 86400) < r["at"] <= slot
            ),
            key=lambda r: r["at"],
        )
    storage().put(META, "digest", {"at": slot})
    to = recipients()
    if not due or not to:
        return 0
    from sudoo import mail

    if not mail.mailer().ready:
        return 0
    base = panel_url()
    items = [{**r, "link": link(r, base)} for r in due]
    from sudoo.mail.catalog import alerts_block

    count = f"{len(due)} {'alert' if len(due) == 1 else 'alerts'}"
    sent = 0
    for address in to:
        try:
            mail.send(address, "alert_digest", count=count, alerts=alerts_block(items), link=base or "")
            sent += 1
        except (mail.MailError, OSError) as error:
            print("alert summary e-mail:", repr(error), flush=True)
    if sent:
        with _LOCK:
            for r in due:
                if r["id"] in _all():
                    _all()[r["id"]]["emailed"] = True
                    _save(_all()[r["id"]])
    return len(due)


def digest_forever() -> None:
    """Background loop: every minute, send the daily summary when it is due."""
    while True:
        time.sleep(60)
        try:
            send_digest()
        except Exception as exc:  # noqa: BLE001 - try again in a minute
            print("alert summary:", repr(exc), flush=True)


def start() -> None:
    """Watch jobs and the server, and send the daily summary (called once when the panel starts)."""
    from sudoo.core import jobs

    from . import watch

    jobs.on_end(watch.on_job_end)
    for loop in (watch.run_forever, digest_forever):
        threading.Thread(target=loop, daemon=True, name=f"alerts-{loop.__name__}").start()
