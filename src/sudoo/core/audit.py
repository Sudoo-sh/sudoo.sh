"""Audit log: who did what, from where, and whether it was allowed.

Kept by the storage plugin (storage/, the "audit" table of SQLite), newest first when read; the oldest entries are
removed beyond 200,000.
"""

from __future__ import annotations

import time
from typing import Any

from sudoo.storage import storage


def record(
    user: str,
    ip: str | None,
    event: str,
    instance: str | None = None,
    ok: bool = True,
    detail: object = None,
) -> None:
    entry: dict[str, Any] = {"ts": round(time.time()), "user": user, "ip": ip, "event": event, "ok": ok}
    if instance:
        entry["instance"] = instance
    if detail:
        entry["detail"] = str(detail)[:300]
    storage().log(entry)


def latest(
    limit: int = 200, offset: int = 0, user: str | None = None, instance: str | None = None
) -> list[dict[str, Any]]:
    """The newest entries, newest first, optionally of one user or instance."""
    return storage().logs(limit, offset, user=user, instance=instance)
