"""Persistent per-instance state that is not part of the instance's files: last visit, paused, switched off, and
"used_since": the first visit after the instance's last daily backup (instances/backups.py), cleared by that backup,
"rolled_back": the before-restore backup the instance was last rolled back to (its Rollback offer then ends),
"update_failed": the last code update failed and was (or could not be) put back (instances/operations.update_code),
and "keep_backups": before-update backups kept for a restore by hand after an automatic rollback failed.

Kept by the storage plugin (collection "instance_state"), the one place every worker process reads and changes:
each change is one atomic write onto the stored state (a change made by another process stays), and a read gives
the stored state. Visits are recorded on every request, so they are kept in memory and written at most every 30
seconds (each process writes its own, the newest visit wins); all other changes are written immediately.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from sudoo.storage import storage

COLLECTION = "instance_state"
_VISIT_KEYS = ("last_visit", "used_since")


class StateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()  # the visits recorded in this process and not written yet
        self._visits: dict[str, dict[str, float]] = {}

    def get(self, name: str) -> dict[str, Any]:
        """A copy of the instance's state: {"last_visit", "used_since", "paused", "switched_off", "branch_missing",
        "rolled_back", "update_failed", "keep_backups"}."""
        state = dict(storage().get(COLLECTION, name) or {})
        with self._lock:
            visit = self._visits.get(name)
        if visit:
            state = self._with_visit(state, visit)
        return state

    @staticmethod
    def _with_visit(state: dict[str, Any], visit: dict[str, float]) -> dict[str, Any]:
        if visit["last_visit"] > float(state.get("last_visit") or 0):
            state["last_visit"] = visit["last_visit"]
        if not state.get("used_since"):
            state["used_since"] = visit["used_since"]
        return state

    def update(self, name: str, **values: Any) -> None:
        storage().change(COLLECTION, name, lambda found: {**(found or {}), **values})

    def record_visit(self, name: str) -> None:
        now = time.time()
        with self._lock:
            visit = self._visits.setdefault(name, {"last_visit": now, "used_since": now})
            visit["last_visit"] = now

    def merge_visit(self, name: str, when: float) -> bool:
        """A visit recorded elsewhere (by the gateway service, instances/gateway_link.py): taken when it is newer
        than the last one known. Returns whether it was."""
        if when <= float(self.get(name).get("last_visit") or 0):
            return False
        with self._lock:
            visit = self._visits.setdefault(name, {"last_visit": when, "used_since": when})
            visit["last_visit"] = max(visit["last_visit"], when)
        return True

    def remove(self, name: str) -> None:
        with self._lock:
            self._visits.pop(name, None)
        storage().delete(COLLECTION, name)

    def save(self) -> None:
        """Write the visits recorded since the last save onto the stored state (never bringing back the state of an
        instance removed meanwhile)."""
        with self._lock:
            visits, self._visits = self._visits, {}
        from sudoo.instances import store  # late: the instances package imports this module

        for name, visit in visits.items():
            if not store.exists(name):
                continue

            def merged(found: dict[str, Any] | None, visit: dict[str, float] = visit) -> dict[str, Any]:
                return self._with_visit(dict(found or {}), visit)

            storage().change(COLLECTION, name, merged)

    def save_visits_forever(self) -> None:
        """Background loop (every worker process): write recorded visits every 30 seconds."""
        while True:
            time.sleep(30)
            self.save()


STATE = StateStore()
