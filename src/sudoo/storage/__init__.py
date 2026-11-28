"""Where the panel keeps its own data, through the storage plugin named by the STORAGE setting.

Built in: "sqlite" (sqlite.py). The rest of the panel calls `storage()`; which plugin does the work is decided by
configuration. A storage plugin that cannot be loaded is listed for the administrators and the panel uses its own
SQLite storage until it is repaired and the panel restarted (plugin_guard.py). Data from the JSON files of earlier versions (users.json, api_tokens.json, state.json, audit.log)
is moved in the first time the panel starts (migrate.py).
"""

from __future__ import annotations

import threading

from sudoo import plugin_guard, plugins
from sudoo.config import settings

from .base import Document, Storage

__all__ = ["Document", "Storage", "storage"]

_LOCK = threading.Lock()
_STORAGE: Storage | None = None


def storage() -> Storage:
    """The configured storage, created (and earlier data moved into it) on first use."""
    global _STORAGE
    if _STORAGE is None:
        with _LOCK:
            if _STORAGE is None:
                from .migrate import migrate

                created: Storage | None = plugin_guard.load_safely(
                    plugins.STORAGE_GROUP, settings.storage, settings, plugin_guard.default_of("storage")
                )
                if created is None:  # not even the built-in one: every use raises, and is answered cleanly
                    raise RuntimeError("The panel's storage cannot be opened; see the server's log.")
                migrate(created, settings.manager_dir)
                _STORAGE = created
    return _STORAGE
