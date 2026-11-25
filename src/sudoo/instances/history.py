"""The history of deleted instances: who deleted each one (or that its lifetime ended), when, and from which project
and branch, kept DELETED_HISTORY_DAYS days (0: not kept). The instance list's "Deleted" tab shows it. It holds names
and dates only: no data of the instance, no secrets."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from sudoo.config import settings
from sudoo.storage import storage

COLLECTION = "deleted_instances"


def record(name: str, env: Mapping[str, Any], by: str, expired: bool, now: float | None = None) -> None:
    """Remember a deleted instance (called by operations.delete before its folder goes). `expired`: its lifetime
    ended and the panel deleted it itself."""
    if settings.deleted_history_days <= 0:
        return
    moment = int(time.time() if now is None else now)
    storage().put(
        COLLECTION,
        f"{name}:{moment}",
        {
            "name": name,
            "project": env.get("PROJECT") or None,
            "branch": env.get("GIT_BRANCH") or None,
            "stage": env.get("STAGE") or None,
            "created_at": int(env["CREATED_AT"]) if env.get("CREATED_AT") else None,
            "deleted_at": moment,
            "deleted_by": None if expired else by,
            "expired": expired,
        },
    )


def recent(now: float | None = None) -> list[dict[str, Any]]:
    """The deleted instances still inside the history period, the latest first. Older ones are dropped."""
    moment = time.time() if now is None else now
    limit = moment - settings.deleted_history_days * 86400
    kept = []
    for key, document in storage().all(COLLECTION).items():
        if settings.deleted_history_days <= 0 or document.get("deleted_at", 0) < limit:
            storage().delete(COLLECTION, key)
        else:
            kept.append(dict(document))
    return sorted(kept, key=lambda d: d["deleted_at"], reverse=True)
