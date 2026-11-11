"""Persistent per-instance state that is not part of the instance's files: last visit, paused, switched off.

Stored in state.json. Visits are recorded on every request, so they are kept in memory and written at most every
30 seconds; all other changes are written immediately.
"""
import json
import threading
import time

from . import settings


class StateStore:
    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        self._dirty = False
        try:
            self._data = json.loads(path.read_text())
        except (OSError, ValueError):
            self._data = {}

    def get(self, name):
        """A copy of the instance's state: {"last_visit", "paused", "switched_off"}."""
        with self._lock:
            return dict(self._data.get(name, {}))

    def update(self, name, **values):
        with self._lock:
            self._data.setdefault(name, {}).update(values)
        self.save()

    def record_visit(self, name):
        with self._lock:
            self._data.setdefault(name, {})["last_visit"] = time.time()
            self._dirty = True

    def remove(self, name):
        with self._lock:
            self._data.pop(name, None)
        self.save()

    def save(self):
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=1))
            tmp.replace(self._path)
            self._dirty = False

    def save_visits_forever(self):
        """Background loop: write recorded visits every 30 seconds."""
        while True:
            time.sleep(30)
            if self._dirty:
                self.save()


STATE = StateStore(settings.STATE_FILE)
