"""How many instances, and how often: MAX_INSTANCES, MAX_INSTANCES_PER_USER, CREATE_COOLDOWN_MINUTES.

All three can be changed on the Settings page (0 turns each off). An instance belongs to whoever created it (the
person, or "api:<token name>"), recorded as CREATED_BY in its .env. Creation times are kept in the storage
(collection "creations"), so the waiting time also counts instances that were deleted since.

Creations that are queued or running count as instances already (of the user who started them), and the API holds
LOCK from the check until the creation's job is registered, so creations started at the same time cannot all pass.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from sudoo.config import settings
from sudoo.instances import store
from sudoo.storage import Document, storage

CREATIONS = "creations"
LOCK = threading.RLock()  # checking and recording a creation happen together (and starting its job, see above)


@dataclass
class QuotaError(Exception):
    message: str
    status: int  # 403: a limit is reached; 429: wait
    retry_after: int | None = None


def owned_by(user: str) -> list[str]:
    return [name for name in store.names() if store.read_env(name).get("CREATED_BY") == user]


def _counted() -> tuple[list[str], list[tuple[str, str]]]:
    """The instances that exist, and (name, user) of the creations not finished yet that are not among them (an
    instance being created is listed once its files are written: it is counted once)."""
    from sudoo.core import jobs  # late: core/jobs.py is imported by most of the panel

    names = store.names()
    existing = set(names)
    return names, [(name, user) for name, user in jobs.pending_creations() if name not in existing]


def usage(user: str) -> dict[str, object]:
    """What the user may still create, for the panel to show before they try."""
    last = (storage().get(CREATIONS, user) or {}).get("last", 0)
    cooldown = settings.create_cooldown_minutes * 60
    names, pending = _counted()
    from sudoo.instances import capacity  # the running instances: the limits of instances/capacity.py

    running = capacity.running_figures(user)
    return {
        "running": running["total"],
        "running_production": running["production"],
        "max_running": settings.max_running_instances or None,
        "running_own": running["own"],
        "max_running_own": capacity.user_limit(user) or None,
        "instances": len(names) + len(pending),
        "max_instances": settings.max_instances or None,
        "own_instances": len(owned_by(user)) + sum(1 for _, creator in pending if creator == user),
        "max_instances_per_user": settings.max_instances_per_user or None,
        "cooldown_minutes": settings.create_cooldown_minutes or None,
        "next_creation_at": int(last + cooldown) if cooldown and time.time() < last + cooldown else None,
    }


def claim(user: str) -> None:
    """Check every limit for a new instance of `user` and, when all allow it, record the creation now.
    Raises QuotaError saying which limit, and when it is a waiting time, how long."""
    with LOCK:
        names, pending = _counted()
        total = len(names) + len(pending)
        if settings.max_instances and total >= settings.max_instances:
            raise QuotaError(
                f"This server has its maximum of {settings.max_instances} instances. Delete one first.", 403
            )
        own = len(owned_by(user)) + sum(1 for _, creator in pending if creator == user)
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
