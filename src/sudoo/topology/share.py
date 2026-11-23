"""Sharing the topology with Sudoo.sh: off unless an administrator turns it on (SHARE_TOPOLOGY_WITH_SUDOO).

While MARKETPLACE and SHARE_TOPOLOGY_WITH_SUDOO are both on, a background loop sends the snapshot (builder.py)
every TOPOLOGY_INTERVAL_SECONDS, signed like every hub request, to POST /api/v1/installations/topology. The hub may
ask for a longer interval (`next_seconds`, never shorter than the setting) or answer 403 topology_disabled (the
loop then waits an hour). It is a separate request from the heartbeat, which runs every 15 minutes and carries
none of this.

Switching sharing off sends DELETE /api/v1/installations/topology once (tried again until the hub answers), which
removes what the hub kept, and forgets the pseudonym key, so ids shared later cannot be matched with the earlier
ones. Nothing is kept on disk but the key and whether the hub still holds a snapshot.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sudoo.config import live, settings
from sudoo.storage import storage

from . import builder, privacy

log = logging.getLogger("sudoo.topology")

PATH = "/api/v1/installations/topology"
DISABLED_WAIT_SECONDS = 3600
RETRY_SECONDS = 120
MAX_INTERVAL_SECONDS = 600

_WAKE = threading.Event()


def enabled() -> bool:
    return bool(settings.marketplace) and bool(settings.share_topology_with_sudoo)


def state() -> dict[str, Any]:
    return storage().get(privacy.COLLECTION, "state") or {}


def _save(**values: Any) -> None:
    storage().change(privacy.COLLECTION, "state", lambda doc: {**(doc or {}), **values})


def send_once() -> float:
    """Send the snapshot now; the seconds to wait before the next one."""
    from sudoo.hub import client

    interval = float(settings.topology_interval_seconds)
    snapshot = builder.snapshot()
    answer = client.request("POST", PATH, snapshot).data or {}
    asked = answer.get("next_seconds") if isinstance(answer, dict) else None
    _save(shared=True, last_sent_at=time.time(), error=None)
    if isinstance(asked, int | float) and not isinstance(asked, bool):
        interval = min(float(MAX_INTERVAL_SECONDS), max(interval, float(asked)))
    return interval


def withdraw_once() -> None:
    """Ask the hub to delete what it kept, and forget the pseudonym key."""
    from sudoo.hub import client

    client.request("DELETE", PATH)
    privacy.forget()
    builder.forget()
    _save(shared=False, withdrawn_at=time.time(), error=None)


def step() -> float:
    """One turn of the loop: send, withdraw, or nothing; the seconds to wait."""
    from sudoo.hub.client import HubRefused, HubUnavailable

    if enabled():
        try:
            return send_once()
        except HubRefused as error:
            _save(error=error.code or error.detail)
            if error.code == "topology_disabled":
                return float(DISABLED_WAIT_SECONDS)
            return float(max(RETRY_SECONDS, error.retry_after or 0))
        except HubUnavailable as error:
            _save(error=str(error)[:200])
            return float(RETRY_SECONDS)
    if state().get("shared") and settings.marketplace:
        try:
            withdraw_once()
        except (HubRefused, HubUnavailable) as error:
            log.info("topology: the hub could not be told to forget the snapshot yet: %s", error)
            return float(RETRY_SECONDS)
    return float(settings.topology_interval_seconds)


def _settings_changed(changed: dict[str, Any]) -> None:
    if {"share_topology_with_sudoo", "topology_include_names", "marketplace"} & set(changed):
        builder.forget()
        _WAKE.set()


def share_forever() -> None:
    """Background loop (background.start). Never raises."""
    live.on_change(_settings_changed)
    while True:
        try:
            wait = step()
        except Exception:  # noqa: BLE001 - the loop must keep running; the error is logged
            log.exception("topology: could not share the snapshot")
            wait = float(RETRY_SECONDS)
        _WAKE.wait(wait)
        _WAKE.clear()
