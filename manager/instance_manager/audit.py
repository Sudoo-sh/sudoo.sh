"""Audit log: who did what, from where, and whether it was allowed.

One JSON object per line in audit.log (mode 600), rotated to audit.log.1 at 5 MB.
"""
import json
import os
import threading
import time

from . import settings

_LOCK = threading.Lock()
_MAX_BYTES = 5 * 1024 * 1024


def record(user, ip, event, instance=None, ok=True, detail=None):
    entry = {"ts": round(time.time()), "user": user, "ip": ip, "event": event, "ok": ok}
    if instance:
        entry["instance"] = instance
    if detail:
        entry["detail"] = str(detail)[:300]
    path = settings.AUDIT_FILE
    with _LOCK:
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            os.replace(path, path.with_suffix(".log.1"))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(entry) + "\n")


def latest(limit=200):
    """The newest `limit` entries, newest first."""
    try:
        lines = settings.AUDIT_FILE.read_text().splitlines()[-limit:]
    except OSError:
        return []
    return [json.loads(line) for line in reversed(lines) if line.startswith("{")]
