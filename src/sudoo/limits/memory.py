"""Rate-limiter plugin "memory": counts with a sliding window, in the panel's memory, or in the panel's storage when it
runs several worker processes (PANEL_WORKERS > 1: every process then counts the same requests).

The sliding window is estimated from two fixed windows (the current and the previous minute, weighted by how much
of the previous one still counts), the method large proxies use: constant memory per key, no burst of twice the
limit at the edge of a minute.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from sudoo.config import Settings

from .base import Decision

COLLECTION = "rate_limits"


class MemoryRateLimiter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, int], int] = {}  # (key, window number) -> requests
        self._cleaned = 0
        self._swept = 0

    def hit(self, key: str, limit: int, window_seconds: int = 60) -> Decision:
        now = time.time()
        window = int(now // window_seconds)
        elapsed = (now % window_seconds) / window_seconds
        reset = math.ceil(window_seconds * (1 - elapsed))
        if int(getattr(self._settings, "panel_workers", 1) or 1) > 1:
            return self._shared_hit(key, limit, window, elapsed, reset)
        with self._lock:
            previous = self._counts.get((key, window - 1), 0)
            current = self._counts.get((key, window), 0)
            estimate = previous * (1 - elapsed) + current
            if estimate + 1 > limit:
                return Decision(False, limit, 0, max(1, reset))
            self._counts[(key, window)] = current + 1
            if window != self._cleaned:  # forget windows older than the previous one
                self._cleaned = window
                for old in [k for k in self._counts if k[1] < window - 1]:
                    del self._counts[old]
        return Decision(True, limit, max(0, int(limit - estimate - 1)), reset)

    def _shared_hit(self, key: str, limit: int, window: int, elapsed: float, reset: int) -> Decision:
        """The same count in the panel's storage: checked and counted in one write, for every worker process."""
        from sudoo.storage import storage

        seen: list[float] = []

        def count(found: dict[str, Any] | None) -> dict[str, Any]:
            doc = dict(found or {})
            if doc.get("w") != window:  # a new window: the current one becomes the previous one (or nothing)
                doc = {"w": window, "cur": 0, "prev": int(doc.get("cur") or 0) if doc.get("w") == window - 1 else 0}
            estimate = doc["prev"] * (1 - elapsed) + doc["cur"]
            seen.append(estimate)
            if estimate + 1 <= limit:
                doc["cur"] += 1
            return doc

        storage().change(COLLECTION, key, count)
        if window != self._swept:  # forget keys without a request in this or the previous window
            self._swept = window
            for old, doc in storage().all(COLLECTION).items():
                if int(doc.get("w") or 0) < window - 1:
                    storage().change(
                        COLLECTION, old, lambda d: None if d is None or int(d.get("w") or 0) < window - 1 else d
                    )
        estimate = seen[-1]
        if estimate + 1 > limit:
            return Decision(False, limit, 0, max(1, reset))
        return Decision(True, limit, max(0, int(limit - estimate - 1)), reset)
