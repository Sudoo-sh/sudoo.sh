"""Audit log: who did what, from where, and whether it was allowed.

One JSON object per line in audit.log (mode 600), rotated to audit.log.1 at 5 MB.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from instance_manager.config import settings

_LOCK = threading.Lock()
_MAX_BYTES = 5 * 1024 * 1024


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
    path = settings.audit_file
    with _LOCK:
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            os.replace(path, path.with_suffix(".log.1"))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(entry) + "\n")


def latest(limit: int = 200) -> list[dict[str, Any]]:
    """The newest `limit` entries, newest first."""
    try:
        lines = settings.audit_file.read_text().splitlines()[-limit:]
    except OSError:
        return []
    return [json.loads(line) for line in reversed(lines) if line.startswith("{")]
