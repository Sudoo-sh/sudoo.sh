"""Persistent per-instance state that is not part of the instance's files: last visit, paused, switched off.

Kept by the storage plugin (collection "instance_state"). Visits are recorded on every request, so they are kept in
memory and written at most every 30 seconds; all other changes are written immediately.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from sudoo.storage import storage

COLLECTION = "instance_state"


class StateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] | None = None
        self._dirty: set[str] = set()

    def _all(self) -> dict[str, dict[str, Any]]:
        if self._data is None:  # loaded on first use, not at import (storage needs the settings first)
            self._data = storage().all(COLLECTION)
        return self._data

    def get(self, name: str) -> dict[str, Any]:
        """A copy of the instance's state: {"last_visit", "paused", "switched_off", "branch_missing"}."""
        with self._lock:
            return dict(self._all().get(name, {}))

    def update(self, name: str, **values: Any) -> None:
        with self._lock:
            state = self._all().setdefault(name, {})
            state.update(values)
            self._dirty.discard(name)
            snapshot = dict(state)
        storage().put(COLLECTION, name, snapshot)

    def record_visit(self, name: str) -> None:
        with self._lock:
            self._all().setdefault(name, {})["last_visit"] = time.time()
            self._dirty.add(name)

    def remove(self, name: str) -> None:
        with self._lock:
            self._all().pop(name, None)
            self._dirty.discard(name)
        storage().delete(COLLECTION, name)

    def save(self) -> None:
        """Write the visits recorded since the last save."""
        with self._lock:
            dirty = {name: dict(self._all()[name]) for name in self._dirty if name in self._all()}
            self._dirty.clear()
        for name, state in dirty.items():
            storage().put(COLLECTION, name, state)

    def save_visits_forever(self) -> None:
        """Background loop: write recorded visits every 30 seconds."""
        while True:
            time.sleep(30)
            self.save()


STATE = StateStore()
