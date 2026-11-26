"""The capacity queue (CAPACITY_QUEUE, off by default): a request that a running-instances limit would refuse waits
in line instead, and starts by itself when there is room.

What waits: the creation of a non-production instance and the start of a paused one. Production is never queued
(capacity.refusal never refuses it). A request that fails for another reason (a memory or disk check, a bad value,
a rate limit) is not queued: it is refused as it is without the queue.

The line is first in, first out, kept in the panel's storage so it survives a restart. A waiting item whose own
person is at their limit does not hold up the others: the first item that can start now starts (documented in the
admin guide). The dispatcher (dispatch) takes quotas.LOCK, the same lock the API holds from the limit check until a
job is registered, so two free slots never start more than they hold.

A waiting creation keeps its plan (name, database, stage, switches) without ports: ports are reserved again when it
starts. Its name counts as taken meanwhile (names()).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sudoo.config import settings
from sudoo.core import audit
from sudoo.storage import Document, storage

COLLECTION = "capacity_queue"
LOG = logging.getLogger(__name__)
_LOCK = threading.RLock()


def enabled() -> bool:
    return bool(settings.capacity_queue)


def _sequence(current: dict[str, Document]) -> int:
    return max((int(item.get("seq", 0)) for item in current.values()), default=0) + 1


def items() -> list[Document]:
    """The waiting requests, first in line first."""
    return sorted(storage().all(COLLECTION).values(), key=lambda item: int(item.get("seq", 0)))


def names() -> set[str]:
    """Instance names waiting creations hold (a new instance must not take one)."""
    return {str(item["instance"]) for item in items() if item.get("kind") == "create"}


def waiting(instance: str) -> Document | None:
    return next((item for item in items() if item.get("instance") == instance), None)


def position(item_id: str) -> int | None:
    """1 for the first in line."""
    for number, item in enumerate(items(), start=1):
        if item["id"] == item_id:
            return number
    return None


def resource(item: Mapping[str, Any]) -> dict[str, Any]:
    number = position(str(item["id"]))
    return {
        "id": item["id"],
        "kind": item["kind"],
        "instance": item["instance"],
        "user": item["user"],
        "charged": item.get("charged") or None,
        "stage": item.get("stage"),
        "position": number,
        "created_at": item["created_at"],
        "waiting_for": item.get("waiting_for"),
        "status": f"Waiting for capacity (position {number})",
    }


def _add(
    kind: str,
    instance: str,
    user: str,
    charged: str | None,
    stage: str,
    plan: dict[str, Any] | None,
    held: Iterable[str] = (),
) -> Document:
    with _LOCK:
        current = storage().all(COLLECTION)
        item: Document = {
            "id": uuid.uuid4().hex[:12],
            "seq": _sequence(current),
            "kind": kind,
            "instance": instance,
            "user": user,
            "charged": charged or "",
            "stage": stage,
            "created_at": round(time.time()),
            "held": sorted(held),  # what the caller could do when asking (a token's scope included)
            **({"plan": plan} if plan is not None else {}),
        }
        storage().put(COLLECTION, item["id"], item)
    audit.record(user, None, f"waiting for capacity ({kind})", instance, detail=f"position {position(item['id'])}")
    return item


def enqueue_create(plan: dict[str, Any], user: str, charged: str | None, held: Iterable[str] = ()) -> Document:
    """Queue a creation. `plan`: planning.build's, without its reserved ports (they are released here)."""
    from sudoo.instances import store

    kept = {key: value for key, value in plan.items() if key != "ports"}
    store.release_ports(plan.get("ports") or {})
    return _add("create", str(plan["name"]), user, charged, str(plan["stage"]), kept, held)


def enqueue_start(instance: str, user: str, charged: str | None, stage: str, held: Iterable[str] = ()) -> Document:
    """Queue the start of a paused instance (one request waits once: the same item answers the next one)."""
    found = waiting(instance)
    return found if found is not None else _add("start", instance, user, charged, stage, None, held)


def cancel(item_id: str, user: str, *, admin: bool) -> bool:
    """Take an item out of the line: its own person, or an administrator. False when it is not waiting or not theirs."""
    with _LOCK:
        item = storage().get(COLLECTION, item_id)
        if item is None or not (admin or item["user"] == user):
            return False
        storage().delete(COLLECTION, item_id)
    audit.record(user, None, "left the capacity queue", str(item["instance"]), detail=str(item["kind"]))
    return True


def to_front(item_id: str, user: str) -> bool:
    """Put an item first in line (the lowest sequence number)."""
    with _LOCK:
        item = storage().get(COLLECTION, item_id)
        if item is None:
            return False
        first = min((int(i.get("seq", 0)) for i in items()), default=1)
        if int(item["seq"]) == first:
            return True
        storage().put(COLLECTION, item_id, {**item, "seq": first - 1})
    audit.record(
        user, None, "moved to the front of the capacity queue", str(item["instance"]), detail=str(item["kind"])
    )
    return True


def _refused(item: Mapping[str, Any]) -> str | None:
    """Why the request may not start now, checked again at execution (the person's rights can change while it waits):
    the account is gone or disabled, or no longer (or never, for a token's scope) allowed what the request does."""
    from sudoo import accounts, projects
    from sudoo.projects import access

    account = accounts.get(str(item["user"]))
    if account is None:
        return "the account no longer exists"
    if accounts.is_disabled(account):
        return "the account is disabled"
    paying = str(item.get("charged") or "")
    if paying and paying not in ("system", str(item["user"])):  # nobody ("" or "system") is valid: only the panel limit
        owner = accounts.get(paying)
        if owner is None or accounts.is_disabled(owner):
            return "the person whose limit it counts against no longer has an enabled account"
    need = "instances.create" if item["kind"] == "create" else "instances.update"
    held = item.get("held")
    if held is not None and need not in held:
        return "the request was not allowed to do this"
    if item["kind"] == "create":
        plan = item.get("plan") or {}
        project = projects.get(str(plan.get("project") or "")) if plan.get("project") else None
        found = access.refusal(account, need, project, str(plan.get("branch") or "") or None, str(plan.get("stage")))
    else:
        found = access.instance_refusal(account, need, str(item["instance"]))
    return found[1] or "the person's rights no longer allow it" if found else None


def dispatch() -> list[str]:
    """Start what can start now, first in line first; the names started. Safe to call at any time, also from two
    threads: an item is taken out of the line before it starts, so it never starts twice (a cancel, a replay or a
    second dispatcher finds it gone), and the capacity check and the job's registration share quotas.LOCK."""
    from sudoo.core import slots
    from sudoo.instances import metrics, planning, store
    from sudoo.limits import quotas

    started: list[str] = []
    try:
        with quotas.LOCK:
            for item in items():
                name, user = str(item["instance"]), str(item["user"])
                charged = str(item.get("charged") or "") or None
                if item["kind"] == "start" and (
                    not store.folder(name).exists() or metrics.INSTANCES.get(name, {}).get("running")
                ):
                    _take(str(item["id"]))  # deleted or running already: nothing to wait for
                    continue
                why = _refused(item)
                if why is not None:
                    if _take(str(item["id"])) is not None:
                        audit.record(user, None, "capacity queue: dropped", name, ok=False, detail=why)
                    continue
                blocked = _blocked(item, charged)
                _note(str(item["id"]), blocked)
                if blocked is not None:
                    continue  # no room for this one (its person or the panel): the next may still fit
                if _take(str(item["id"])) is None:
                    continue  # cancelled or started by another dispatcher meanwhile
                ports: dict[str, int] = {}
                try:
                    if item["kind"] == "create":
                        plan = dict(item["plan"])
                        plan["ports"] = ports = _ports()
                        planning.start(plan, user)
                    else:
                        _start(name, user)
                except slots.Refused as error:  # judged again inside the start's own write: not started, its turn stays
                    _restore(item, ports)
                    LOG.info("capacity queue: %s waits (%s)", name, error)
                    continue
                except slots.Unavailable:  # the hold could not be recorded: not started, so it keeps its place
                    _restore(item, ports)
                    raise
                except Exception as error:  # noqa: BLE001 - one broken item must not stop the line
                    LOG.warning("capacity queue: %s could not start: %s", name, error)
                    audit.record(user, None, "capacity queue: start failed", name, ok=False, detail=str(error)[:300])
                else:
                    audit.record(user, None, "started from the capacity queue", name, detail=str(item["kind"]))
                    started.append(name)
    except slots.Unavailable as exc:  # the running record could not be read or the lock waited out: next round
        LOG.warning("capacity queue: nothing started this round (%s)", exc)
    return started


def _blocked(item: Mapping[str, Any], charged: str | None) -> str | None:
    """Why the request cannot start at its turn, or None (the caller holds quotas.LOCK). The running limits for a
    start, the same ones plus the instance limits for a creation: a creation passed the instance limits when it was
    asked for (and its waiting time was charged then), but instances may have been made while it waited. Nothing
    is recorded here, so asking again every few seconds charges nothing."""
    from sudoo.instances import capacity
    from sudoo.limits import quotas

    if item["kind"] == "start":  # the owner now, as the HTTP start reads it (CREATED_BY)
        return capacity.start_refusal(str(item["instance"]))
    try:
        quotas.check(str(item["user"]))
    except quotas.QuotaError as error:
        return error.message
    return capacity.refusal(stage=str(item.get("stage") or ""), user=charged)


def _note(item_id: str, why: str | None) -> None:
    """Keep what the request waits for on the item (shown with its position), written only when it changes and
    only while the item is still waiting: a cancelled item must not come back."""
    with _LOCK:
        item = storage().get(COLLECTION, item_id)
        if item is not None and item.get("waiting_for") != why:
            storage().put(
                COLLECTION,
                item_id,
                {key: value for key, value in {**item, "waiting_for": why}.items() if value is not None},
            )


def _take(item_id: str) -> Document | None:
    """Take an item out of the line; the item, or None when it is not there any more (only one caller gets it)."""
    with _LOCK:
        item = storage().get(COLLECTION, item_id)
        if item is not None:
            storage().delete(COLLECTION, item_id)
        return item


def _restore(item: Mapping[str, Any], ports: Mapping[str, int] | None = None) -> None:
    """Put an item taken for a start that did not happen back with its own id and sequence number (so, its place in
    the line). Idempotent, and it never overwrites: nothing is written when the item is there already (restored by
    an earlier call) or the instance waits again under another item (asked again meanwhile). Only for a start that
    definitely did not begin; one that may have begun is not put back."""
    from sudoo.instances import store

    store.release_ports(dict(ports or {}))  # a creation reserved its ports for a start that did not happen
    with _LOCK:
        item_id = str(item["id"])
        if storage().get(COLLECTION, item_id) is None and waiting(str(item["instance"])) is None:
            storage().put(COLLECTION, item_id, dict(item))


def _ports() -> dict[str, int]:
    from sudoo import exposure
    from sudoo.instances import store

    return store.reserve_ports(exposure.provider().instance_ports())


def _start(name: str, user: str) -> None:
    from sudoo.core import jobs
    from sudoo.instances import operations

    jobs.start(name, "Start", "wake", operations.wake, name, user=user)


def run_forever(interval: float = 5.0) -> None:
    """The dispatcher's loop: every few seconds, and only when the queue has something waiting."""
    while True:
        time.sleep(interval)
        try:
            if storage().all(COLLECTION):
                dispatch()
        except Exception:  # noqa: BLE001 - the loop must survive a bad round
            LOG.exception("capacity queue: dispatch failed")
