"""Where the panel keeps its own data, through the storage plugin named by the STORAGE setting.

Built in: "sqlite" (sqlite.py). The rest of the panel calls `storage()`; which plugin does the work is decided by
configuration. Data from the JSON files of earlier versions (users.json, api_tokens.json, state.json, audit.log)
is moved in the first time the panel starts (migrate.py).
"""

from __future__ import annotations

import threading

from sudoo import plugins
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

                created: Storage = plugins.load(plugins.STORAGE_GROUP, settings.storage, settings)
                migrate(created, settings.manager_dir)
                _STORAGE = created
    return _STORAGE
