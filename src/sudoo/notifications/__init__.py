"""Notifications: a project tells its people's chat, webhooks and mailboxes what happened to it.

Each project has its channels (collection "notification_channels", one document each): a kind (channels.py: Slack,
Discord, Telegram, Microsoft Teams, a signed webhook, e-mail), a name, the events it wants, and its secrets (the
webhook address, the bot token, the signing secret) encrypted with the vault key (core/vault.py, label
"notification:<channel id>:<field>"). A secret is never shown, logged or audited again: only its last 4 characters.
A webhook's signing secret is made by the panel and shown once, when it is made.

Events (EVENTS) come from what the panel already publishes, never from a second watch:

    build_failed, build_warning, build_recovered   a build of a branch ended (the live "build" event of
                                                   projects/builds.py); recovered: a success after a failure
    stage_changed, promoted                        a branch moved to another stage (the "stage" entry of the
                                                   branch's history); promoted: to production
    restore_done                                   a backup was restored into an instance of the project
    staging_refreshed                              the scheduled refresh of staging from production ran
                                                   (the "refresh" entry of the branch's history,
                                                   projects/refresh.py)
    production_down, production_up                 the status checks (status/checks.py), down after two failed
                                                   checks in a row
    backup_failed                                  a backup job of an instance of the project failed (jobs.on_end)
    storage_over                                   an instance of the project is over its storage quota (the
                                                   "alert" event; needs ALERTS and ALERT_STORAGE)

Delivery is in one background thread, in order of when it is due: a failure that is worth trying again (no answer,
HTTP 429 or 5xx) is tried again NOTIFICATION_RETRIES times, 30 seconds after the first try and twice as long each
time after (at most an hour). Each channel keeps how its last delivery went. Deliveries waiting when the panel
restarts are lost.
"""

from __future__ import annotations

import heapq
import itertools
import re
import secrets as random
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sudoo.config import settings
from sudoo.core import events, vault
from sudoo.core.hints import GENERAL_HINT
from sudoo.core.shell import redact
from sudoo.storage import Document, storage

from . import channels, guard

COLLECTION = "notification_channels"
EVENTS = (
    "build_failed",
    "build_warning",
    "build_recovered",
    "stage_changed",
    "promoted",
    "production_down",
    "production_up",
    "backup_failed",
    "restore_done",
    "staging_refreshed",
    "storage_over",
)
DEFAULT_EVENTS = (
    "build_failed",
    "build_recovered",
    "promoted",
    "production_down",
    "production_up",
    "backup_failed",
    "storage_over",
)
EMAIL_RE = re.compile(r"[^@\s,]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}")
FIRST_RETRY_SECONDS = 30
LONGEST_WAIT_SECONDS = 3600


class ChannelError(ValueError):
    """A channel cannot be saved as asked; the text says why, for people."""


# ---------------------------------------------------------------- channels


def _label(channel_id: str, field_name: str) -> str:
    return f"notification:{channel_id}:{field_name}"


def _hint(value: str) -> str:
    return f"…{value[-4:]}" if len(value) > 8 else "…"


def of_project(project_id: str) -> list[Document]:
    return sorted(
        (c for c in storage().all(COLLECTION).values() if c.get("project") == project_id),
        key=lambda c: (c.get("created_at", 0), c["id"]),
    )


def get(channel_id: str) -> Document | None:
    return storage().get(COLLECTION, channel_id)


def secrets_of(channel: Mapping[str, Any]) -> dict[str, str]:
    """The channel's secrets, decrypted (only to deliver)."""
    found = {}
    for name, token in (channel.get("secrets") or {}).items():
        try:
            found[name] = vault.decrypt(token, _label(channel["id"], name))
        except vault.VaultError:
            found[name] = ""
    return found


def resource(channel: Mapping[str, Any]) -> dict[str, Any]:
    """A channel as the API shows it: never a secret, only whether it is set and its last 4 characters."""
    return {
        "id": channel["id"],
        "project": channel["project"],
        "type": channel["type"],
        "name": channel["name"],
        "events": list(channel.get("events") or []),
        "enabled": bool(channel.get("enabled", True)),
        "config": dict(channel.get("config") or {}),
        "secrets": {name: channel.get("hints", {}).get(name) for name in channels.SECRETS[channel["type"]]},
        "created_at": channel.get("created_at"),
        "created_by": channel.get("created_by"),
        "last": channel.get("last"),
    }


def _clean_events(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(e, str) for e in value):
        raise ChannelError(f"Choose the events as a list: {', '.join(EVENTS)}.")
    unknown = [e for e in value if e not in EVENTS]
    if unknown:
        raise ChannelError(f"Unknown event: {unknown[0][:40]}. Choose from {', '.join(EVENTS)}.")
    return [e for e in EVENTS if e in value]


def _clean_config(kind: str, data: Mapping[str, Any], current: Mapping[str, Any] | None) -> dict[str, Any]:
    config = dict(current or {})
    if kind == "telegram" and "chat_id" in data:
        chat = str(data.get("chat_id") or "").strip()
        if not channels.TELEGRAM_CHAT_RE.match(chat):
            raise ChannelError("The chat id is a number (such as -1001234567890) or a channel's @name.")
        config["chat_id"] = chat
    if kind == "email" and "addresses" in data:
        raw = data.get("addresses")
        items = raw.split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else None
        if items is None:
            raise ChannelError("Send the e-mail addresses as a list.")
        addresses = list(dict.fromkeys(str(item).strip().lower() for item in items if str(item).strip()))
        for address in addresses:
            if not EMAIL_RE.fullmatch(address):
                raise ChannelError(f"{address[:80]} is not an e-mail address.")
        if len(addresses) > 20:
            raise ChannelError("A channel may e-mail at most 20 addresses.")
        if not addresses:
            raise ChannelError("Add at least one e-mail address.")
        config["addresses"] = addresses
    if kind == "telegram" and not config.get("chat_id"):
        raise ChannelError("Add the chat id the bot writes to.")
    if kind == "email" and not config.get("addresses"):
        raise ChannelError("Add at least one e-mail address.")
    return config


def _clean_secret(kind: str, name: str, value: Any) -> str:
    text = str(value or "").strip()
    if name == "url":
        try:
            guard.parse(text)
        except guard.Refused as error:
            raise ChannelError(str(error)) from None
    if name == "bot_token" and not channels.TELEGRAM_TOKEN_RE.match(text):
        raise ChannelError("The bot token looks like 123456789:AA… (from @BotFather).")
    return text


def create(project_id: str, data: Mapping[str, Any], user: str) -> tuple[Document, str | None]:
    """A new channel; returns it and, for a webhook, its signing secret (shown this once)."""
    kind = data.get("type")
    if kind not in channels.TYPES:
        raise ChannelError(f"Choose the kind of channel: {', '.join(channels.TYPES)}.")
    if len(of_project(project_id)) >= settings.notification_channels_per_project:
        raise ChannelError(
            f"A project may have {settings.notification_channels_per_project} channels (NOTIFICATION_CHANNELS_PER_PROJECT)."
        )
    channel_id = uuid.uuid4().hex[:12]
    name = str(data.get("name") or "").strip()[:60] or kind.capitalize()
    wanted = _clean_events(data["events"]) if "events" in data else list(DEFAULT_EVENTS)
    config = _clean_config(kind, data.get("config") or {}, None)
    given = data.get("secrets") or {}
    sealed: dict[str, str] = {}
    hints: dict[str, str] = {}
    for secret_name in channels.SECRETS[kind]:
        if secret_name == "signing_secret":
            continue
        value = _clean_secret(kind, secret_name, given.get(secret_name) if isinstance(given, dict) else None)
        if not value:
            raise ChannelError("Add the channel's address." if secret_name == "url" else "Add the bot token.")
        sealed[secret_name] = vault.encrypt(value, _label(channel_id, secret_name))
        hints[secret_name] = _hint(value)
    signing = None
    if kind == "webhook":
        signing = "whsec_" + random.token_urlsafe(32)
        sealed["signing_secret"] = vault.encrypt(signing, _label(channel_id, "signing_secret"))
        hints["signing_secret"] = _hint(signing)
    channel: Document = {
        "id": channel_id,
        "project": project_id,
        "type": kind,
        "name": name,
        "events": wanted,
        "enabled": True,
        "config": config,
        "secrets": sealed,
        "hints": hints,
        "created_at": round(time.time()),
        "created_by": user,
        "last": None,
    }
    storage().put(COLLECTION, channel_id, channel)
    return channel, signing


def change(channel: Mapping[str, Any], data: Mapping[str, Any]) -> Document:
    """Change a channel's name, events, on/off, settings or secrets (a secret left out stays as it is)."""
    kind = channel["type"]
    updated = dict(channel)
    if "name" in data:
        updated["name"] = str(data.get("name") or "").strip()[:60] or kind.capitalize()
    if "events" in data:
        updated["events"] = _clean_events(data["events"])
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            raise ChannelError('"enabled" is true or false.')
        updated["enabled"] = data["enabled"]
    if "config" in data:
        updated["config"] = _clean_config(kind, data.get("config") or {}, channel.get("config"))
    given = data.get("secrets")
    if isinstance(given, dict):
        sealed, hints = dict(channel.get("secrets") or {}), dict(channel.get("hints") or {})
        for secret_name in channels.SECRETS[kind]:
            if secret_name == "signing_secret" or not given.get(secret_name):
                continue
            value = _clean_secret(kind, secret_name, given[secret_name])
            sealed[secret_name] = vault.encrypt(value, _label(channel["id"], secret_name))
            hints[secret_name] = _hint(value)
        updated.update(secrets=sealed, hints=hints)
    storage().put(COLLECTION, channel["id"], updated)
    return updated


def new_signing_secret(channel: Mapping[str, Any]) -> tuple[Document, str]:
    """A webhook's new signing secret (the old one stops being used at once); shown this once."""
    if channel["type"] != "webhook":
        raise ChannelError("Only a webhook has a signing secret.")
    signing = "whsec_" + random.token_urlsafe(32)
    updated = dict(channel)
    updated["secrets"] = {
        **(channel.get("secrets") or {}),
        "signing_secret": vault.encrypt(signing, _label(channel["id"], "signing_secret")),
    }
    updated["hints"] = {**(channel.get("hints") or {}), "signing_secret": _hint(signing)}
    storage().put(COLLECTION, channel["id"], updated)
    return updated, signing


def remove(channel_id: str) -> None:
    storage().delete(COLLECTION, channel_id)


def remove_project(project_id: str) -> None:
    for channel in of_project(project_id):
        remove(channel["id"])


def _note_delivery(channel_id: str, last: dict[str, Any]) -> None:
    def apply(found: Document | None) -> Document | None:
        if found is None:
            return None
        return {**found, "last": last}

    storage().change(COLLECTION, channel_id, apply)


# ---------------------------------------------------------------- what happened


def notification(
    event: str,
    project_id: str,
    *,
    title: str,
    text: str,
    instance: str | None = None,
    branch: str | None = None,
    details: list[str] | None = None,
    severity: str = "problem",
    path: str | None = None,
    test: bool = False,
) -> dict[str, Any]:
    from sudoo import projects

    project = projects.get(project_id) or {}
    shown = [f"Project: {project.get('name') or project_id}"]
    if branch:
        shown.append(f"Branch: {branch}")
    if instance:
        shown.append(f"Instance: {instance}")
    return {
        "id": uuid.uuid4().hex[:16],
        "event": event,
        "project": project_id,
        "project_name": project.get("name") or project_id,
        "title": redact(title)[:200],
        "text": redact(text)[:2000],
        "details": [redact(d)[:300] for d in [*shown, *(details or [])]][:20],
        "instance": instance,
        "branch": branch,
        "severity": severity,
        "path": path or f"/projects/{project_id}",
        "at": time.time(),
        **({"test": True} if test else {}),
    }


def emit(event: str, project_id: str | None, **values: Any) -> int:
    """Something happened in a project: queue a notification for each of its channels that wants `event`.
    Returns how many deliveries were queued."""
    if not project_id or not settings.notifications or event not in EVENTS:
        return 0
    wanted = [c for c in of_project(project_id) if c.get("enabled", True) and event in (c.get("events") or [])]
    if not wanted:
        return 0
    note = notification(event, project_id, **values)
    for channel in wanted:
        QUEUE.add(Delivery(channel["id"], note))
    return len(wanted)


# ---------------------------------------------------------------- delivery


@dataclass
class Delivery:
    channel: str
    note: dict[str, Any]
    attempt: int = 0  # tries made so far
    due: float = field(default_factory=time.time)
    retries: bool = True  # a test is tried once


def backoff(attempt: int) -> float:
    """Seconds to wait before try number `attempt + 1` (attempt >= 1): 30 s, 60 s, 2 min... at most an hour."""
    return float(min(LONGEST_WAIT_SECONDS, FIRST_RETRY_SECONDS * 2 ** max(0, attempt - 1)))


def attempt(delivery: Delivery, now: float | None = None) -> float | None:
    """Try one delivery now and note how it went on its channel. Returns when to try again, or None (delivered,
    not worth trying again, or out of tries)."""
    now = time.time() if now is None else now
    channel = get(delivery.channel)
    if channel is None or not (
        channel.get("enabled", True) or delivery.note.get("test")
    ):  # a test is sent all the same
        return None
    delivery.attempt += 1
    from sudoo.alerts import link, panel_url

    target = link(delivery.note, panel_url())
    last: dict[str, Any] = {"at": now, "event": delivery.note["event"], "attempts": delivery.attempt}
    retry_at = None
    try:
        result = channels.send(
            channel["type"], delivery.note, target, channel.get("config") or {}, secrets_of(channel),
            settings.notification_timeout_seconds,
        )  # fmt: skip
        last.update(ok=True, result=result)
    except guard.Refused as error:
        last.update(ok=False, result=str(error))
    except guard.DeliveryError as error:
        last.update(ok=False, result=str(error))
        if error.retry and delivery.retries and delivery.attempt <= settings.notification_retries:
            retry_at = now + backoff(delivery.attempt)
            last["retry_at"] = retry_at
    if delivery.note.get("test"):
        last["test"] = True
    _note_delivery(channel["id"], last)
    events.publish("notification", channel["project"], {"channel": channel["id"], "last": last})
    return retry_at


class Queue:
    """Deliveries waiting, in order of when they are due, worked by one thread."""

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Delivery]] = []
        self._counter = itertools.count()
        self._wake = threading.Condition()
        self._thread: threading.Thread | None = None
        self._busy = 0

    def add(self, delivery: Delivery) -> None:
        """Queue a delivery; when BACKGROUND_QUEUE_MAX wait already (a webhook down for long), the one due last
        is dropped and logged, so the queue never fills the memory."""
        with self._wake:
            heapq.heappush(self._heap, (delivery.due, next(self._counter), delivery))
            while len(self._heap) > settings.background_queue_max:
                dropped = max(self._heap)
                self._heap.remove(dropped)
                heapq.heapify(self._heap)
                print(f"notification {dropped[2].channel}: dropped, too many deliveries waiting", flush=True)
            self._wake.notify()
            if self._thread is None:
                self._thread = threading.Thread(target=self._work_forever, daemon=True, name="notifications")
                self._thread.start()

    def due(self, now: float) -> Delivery | None:
        with self._wake:
            if self._heap and self._heap[0][0] <= now:
                self._busy += 1
                return heapq.heappop(self._heap)[2]
            return None

    def run_due(self, now: float | None = None) -> int:
        """Try every delivery that is due (tests call it without the thread). Returns how many were tried."""
        now = time.time() if now is None else now
        tried = 0
        while (delivery := self.due(now)) is not None:
            tried += 1
            self._try(delivery, now)
        return tried

    def _try(self, delivery: Delivery, now: float) -> None:
        try:
            retry_at = attempt(delivery, now)
        except Exception as exc:  # noqa: BLE001 - one delivery never stops the others
            print(f"notification {delivery.channel}: {exc!r}", flush=True)
            retry_at = None
        with self._wake:
            self._busy -= 1
            if retry_at is not None:
                delivery.due = retry_at
                heapq.heappush(self._heap, (retry_at, next(self._counter), delivery))
            self._wake.notify_all()

    def _work_forever(self) -> None:
        while True:
            with self._wake:
                while not self._heap or self._heap[0][0] > time.time():
                    self._wake.wait(timeout=(self._heap[0][0] - time.time()) if self._heap else None)
                self._busy += 1
                delivery = heapq.heappop(self._heap)[2]
            self._try(delivery, time.time())

    def waiting(self) -> list[Delivery]:
        with self._wake:
            return [item[2] for item in sorted(self._heap)]

    def clear(self) -> None:
        with self._wake:
            self._heap.clear()

    def drain(self, timeout: float = 10) -> None:
        """Wait until nothing is due now (tests)."""
        end = time.time() + timeout
        with self._wake:
            while (self._busy or (self._heap and self._heap[0][0] <= time.time())) and time.time() < end:
                self._wake.wait(timeout=0.05)


QUEUE = Queue()


def send_test(channel: Mapping[str, Any], user: str) -> dict[str, Any]:
    """Send a test notification to one channel now (no retries); its last delivery."""
    note = notification(
        "test", channel["project"], title="Test notification",
        text=f"This is a test notification{f' sent by {user}' if user else ''}. Notifications to this channel work.",
        severity="info", path=f"/projects/{channel['project']}/notifications", test=True,
    )  # fmt: skip
    delivery = Delivery(channel["id"], note, retries=False)
    attempt(delivery)
    return (get(channel["id"]) or {}).get("last") or {}


# ---------------------------------------------------------------- following the panel's events


_SEEN: OrderedDict[str, None] = OrderedDict()  # entries already notified ("<entry id>:<status>")
_SEEN_LOCK = threading.Lock()
_WORK: list[tuple[str, str, Any]] = []
_WORK_READY = threading.Condition()


def _first_time(key: str) -> bool:
    with _SEEN_LOCK:
        if key in _SEEN:
            return False
        _SEEN[key] = None
        while len(_SEEN) > 5000:
            _SEEN.popitem(last=False)
        return True


def _on_event(event: events.Event) -> None:
    """A "build" or "alert" event was published: look at it in the notifications' own thread (publish stays quick)."""
    with _WORK_READY:
        _WORK.append((event.topic, event.key, event.data))
        _WORK_READY.notify()


def _follow_forever() -> None:
    while True:
        with _WORK_READY:
            while not _WORK:
                _WORK_READY.wait()
            topic, key, data = _WORK.pop(0)
        try:
            if topic == "build":
                on_build(key)
            elif topic == "alert" and isinstance(data, dict):
                on_alert(data)
        except Exception as exc:  # noqa: BLE001 - the next event must still be looked at
            print(f"notifications {topic} {key}: {exc!r}", flush=True)


def on_build(entry_id: str) -> int:
    """A branch's history changed: a build that ended, a stage change, a restore."""
    from sudoo.projects import builds

    entry = builds.find(entry_id)
    if not entry:
        return 0
    project_id, branch, kind = entry["project"], entry["branch"], entry.get("type")
    status = entry.get("status")
    common = {"branch": branch, "instance": entry.get("instance"), "path": f"/projects/{project_id}/builds"}
    if kind == "build" and status in ("failed", "warning", "success"):
        if not _first_time(f"{entry_id}:{status}"):
            return 0
        commit = entry.get("commit") or {}
        details = [f"Commit: {commit.get('sha')} {commit.get('subject') or ''}".strip()] if commit.get("sha") else []
        result = str(entry.get("result") or "")
        if status == "failed":
            rule = (entry.get("failure") or {}).get("rule")
            if isinstance(rule, dict) and rule.get("title"):  # the recognised cause (core/failure_rules.py)
                details = [*details, f"Cause: {rule['title']}. {rule.get('fix') or ''}".strip()]
                from sudoo.core import hints

                if hints.is_pattern_hint(result):  # one cause, not two: the rule's replaces the older hint's
                    result = f"{rule['title']}. {rule.get('explanation') or ''}".strip()
            return emit("build_failed", project_id, title=f"Build of {branch} failed", text=result or "The build failed.",
                        details=details, severity="problem", **common)  # fmt: skip
        if status == "warning":
            return emit("build_warning", project_id, title=f"Build of {branch} has warnings",
                        text=result or "The build succeeded with warnings.", details=details, severity="info",
                        **common)  # fmt: skip
        if _previous_failed(project_id, branch, entry):
            return emit("build_recovered", project_id, title=f"Build of {branch} works again",
                        text=result or "The build succeeded after a failure.", details=details,
                        severity="recovered", **common)  # fmt: skip
        return 0
    if kind == "stage" and _first_time(f"{entry_id}:stage"):
        before, after = entry.get("from"), entry.get("to")
        what = f"{branch} moved from {before} to {after}" + (f" by {entry.get('by')}" if entry.get("by") else "")
        if after == "production":
            return emit("promoted", project_id, title=f"{branch} is now in production", text=what + ".",
                        severity="info", **common)  # fmt: skip
        return emit("stage_changed", project_id, title=f"{branch} is now {after}", text=what + ".", severity="info",
                    **common)  # fmt: skip
    if kind == "refresh" and _first_time(f"{entry_id}:refresh"):
        refreshed = status == "success"
        return emit("staging_refreshed", project_id,
                    title=f"Staging {branch} refreshed from production" if refreshed else f"Staging {branch} not refreshed",
                    text=str(entry.get("result") or ""), severity="info" if refreshed else "problem",
                    **common)  # fmt: skip
    if kind == "restore" and status == "success" and _first_time(f"{entry_id}:restore"):
        return emit("restore_done", project_id, title=f"Backup restored into {entry.get('instance')}",
                    text=str(entry.get("result") or "A backup was restored."), severity="info", **common)  # fmt: skip
    return 0


def _previous_failed(project_id: str, branch: str, entry: Mapping[str, Any]) -> bool:
    """Whether the build of `branch` before `entry` (of the same instance, if it has one) failed."""
    from sudoo.projects import builds

    earlier = [
        e
        for e in builds.history(project_id, branch)
        if e.get("type") == "build"
        and e["id"] != entry["id"]
        and float(e.get("at") or 0) < float(entry.get("at") or 0)
        and e.get("status") in ("failed", "success", "warning", "dropped")
        and (not entry.get("instance") or e.get("instance") == entry.get("instance"))
    ]
    return bool(earlier) and max(earlier, key=lambda e: float(e.get("at") or 0)).get("status") == "failed"


def on_alert(record: Mapping[str, Any]) -> int:
    """A new alert: an instance of a project over its storage quota."""
    from sudoo import projects

    if record.get("severity") != "problem" or int(record.get("count") or 1) != 1:
        return 0
    if record.get("type") != "storage" or not str(record.get("key", "")).startswith("storage_over:"):
        return 0
    name = record.get("instance")
    if not name or not _first_time(f"alert:{record.get('id')}"):
        return 0
    return emit("storage_over", projects.project_of(str(name)), title=str(record.get("title") or ""),
                text=str(record.get("text") or ""), instance=str(name), severity="problem",
                path=f"/i/{name}")  # fmt: skip


def on_job_end(job: Any) -> int:
    """jobs.on_end: a failed backup of an instance that belongs to a project."""
    from sudoo import projects
    from sudoo.core import jobs

    if job.kind != "backup" or job.status != "failed" or jobs.is_panel_job(job.instance):
        return 0
    try:
        project_id = projects.project_of(job.instance)
    except Exception:  # noqa: BLE001 - the instance is gone
        return 0
    return emit("backup_failed", project_id, title=f"Backup of {job.instance} failed",
                text=str(job.hint or GENERAL_HINT), instance=job.instance,
                branch=projects.branch_of(job.instance) or None, severity="problem",
                path=f"/i/{job.instance}/activity?job={job.id}")  # fmt: skip


def _job_ended(job: Any) -> None:
    on_job_end(job)


_STARTED = threading.Event()


def start() -> None:
    """Follow the panel's events (called once when the panel starts)."""
    if _STARTED.is_set():
        return
    _STARTED.set()
    from sudoo.core import jobs

    events.listen(frozenset({"build", "alert"}), _on_event)
    jobs.on_end(_job_ended)
    threading.Thread(target=_follow_forever, daemon=True, name="notifications-follow").start()
