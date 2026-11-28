"""Live events of every worker process reach the browsers.

The live event stream (web/v1/events.py) is served by the process that runs the background work (core/shared.py
leadership). An event published in another worker process (a job it runs, a change made by a request it served) is
handed over through the panel's storage: written there by that process, read and delivered to the stream's clients
by the leader within POLL_SECONDS. The listeners inside the panel (build history, notifications, alerts) already ran
in the process that published it, so the leader delivers a handed-over event to the stream's clients only.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from typing import Any

from sudoo.core import events, shared

COLLECTION = "event_bridge"
POLL_SECONDS = 0.25
FORGET_SECONDS = 60  # an event no leader read in this time is dropped (no leader runs: the panel is restarting)
LOCAL_ONLY = frozenset({"metrics"})  # topics the leader publishes itself

_STARTED = threading.Event()


def _forward(topic: str, key: str, data: Any) -> None:
    """Hand an event published in this process to the leader (when this process is not the leader)."""
    if shared.is_leader() or topic in LOCAL_ONLY:
        return
    try:
        safe = json.loads(json.dumps(data, default=str)) if data is not None else None
    except (TypeError, ValueError):
        safe = None
    try:
        shared.storage().put(COLLECTION, f"{time.time():020.6f}-{uuid.uuid4().hex[:8]}",
                             {"topic": topic, "key": key, "data": safe, "at": time.time()})  # fmt: skip
    except Exception:  # noqa: BLE001 - an event is never worth failing the work that published it
        return


def install() -> None:
    """Every process: hand its events over while it is not the leader."""
    events.BUS.forward = _forward


def deliver_once() -> int:
    """The leader: deliver the handed-over events, oldest first; returns how many."""
    store = shared.storage()
    now = time.time()
    count = 0
    for key, doc in sorted(store.all(COLLECTION).items()):
        store.delete(COLLECTION, key)
        if now - float(doc.get("at") or 0) > FORGET_SECONDS:
            continue
        events.BUS.publish(str(doc["topic"]), str(doc.get("key") or ""), doc.get("data"), forwarded=True)
        count += 1
    return count


def run_as_leader() -> None:
    """Start delivering the handed-over events (once per process)."""
    if _STARTED.is_set():
        return
    _STARTED.set()

    def loop() -> None:
        while True:
            with contextlib.suppress(Exception):  # tried again at the next round
                deliver_once()
            time.sleep(POLL_SECONDS)

    threading.Thread(target=loop, daemon=True, name="event-bridge").start()
