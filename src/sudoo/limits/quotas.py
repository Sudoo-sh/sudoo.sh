"""How many instances, and how often: MAX_INSTANCES, MAX_INSTANCES_PER_USER, CREATE_COOLDOWN_MINUTES.

All three can be changed on the Settings page (0 turns each off). An instance belongs to whoever created it (the
person, or "api:<token name>"), recorded as CREATED_BY in its .env. Creation times are kept in the storage
(collection "creations"), so the waiting time also counts instances that were deleted since.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from sudoo.config import settings
from sudoo.instances import store
from sudoo.storage import Document, storage

CREATIONS = "creations"
_LOCK = threading.Lock()  # checking and recording a creation happen together


@dataclass
class QuotaError(Exception):
    message: str
    status: int  # 403: a limit is reached; 429: wait
    retry_after: int | None = None


def owned_by(user: str) -> list[str]:
    return [name for name in store.names() if store.read_env(name).get("CREATED_BY") == user]


def usage(user: str) -> dict[str, object]:
    """What the user may still create, for the panel to show before they try."""
    last = (storage().get(CREATIONS, user) or {}).get("last", 0)
    cooldown = settings.create_cooldown_minutes * 60
    return {
        "instances": len(store.names()),
        "max_instances": settings.max_instances or None,
        "own_instances": len(owned_by(user)),
        "max_instances_per_user": settings.max_instances_per_user or None,
        "cooldown_minutes": settings.create_cooldown_minutes or None,
        "next_creation_at": int(last + cooldown) if cooldown and time.time() < last + cooldown else None,
    }


def claim(user: str) -> None:
    """Check every limit for a new instance of `user` and, when all allow it, record the creation now.
    Raises QuotaError saying which limit, and when it is a waiting time, how long."""
    with _LOCK:
        total = len(store.names())
        if settings.max_instances and total >= settings.max_instances:
            raise QuotaError(
                f"This server has its maximum of {settings.max_instances} instances. Delete one first.", 403
            )
        own = len(owned_by(user))
        if settings.max_instances_per_user and own >= settings.max_instances_per_user:
            raise QuotaError(
                f"You have {own} instances, the most one user may have ({settings.max_instances_per_user}). "
                "Delete one of yours first.",
                403,
            )
        cooldown = settings.create_cooldown_minutes * 60

        def record(document: Document | None) -> Document:
            last = (document or {}).get("last", 0)
            wait = int(last + cooldown - time.time()) + 1
            if cooldown and wait > 0:
                raise QuotaError(
                    f"One instance per {settings.create_cooldown_minutes:g} minutes. "
                    f"Try again in {max(1, round(wait / 60))} minutes.",
                    429,
                    wait,
                )
            return {"last": time.time(), "count": (document or {}).get("count", 0) + 1}

        storage().change(CREATIONS, user, record)
