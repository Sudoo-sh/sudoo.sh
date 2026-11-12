"""Rate-limiter plugin "memory": counts in the panel's memory, with a sliding window.

The sliding window is estimated from two fixed windows (the current and the previous minute, weighted by how much
of the previous one still counts), the method large proxies use: constant memory per key, no burst of twice the
limit at the edge of a minute.
"""

from __future__ import annotations

import math
import threading
import time

from sudoo.config import Settings

from .base import Decision


class MemoryRateLimiter:
    def __init__(self, settings: Settings) -> None:
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, int], int] = {}  # (key, window number) -> requests
        self._cleaned = 0

    def hit(self, key: str, limit: int, window_seconds: int = 60) -> Decision:
        now = time.time()
        window = int(now // window_seconds)
        elapsed = (now % window_seconds) / window_seconds
        with self._lock:
            previous = self._counts.get((key, window - 1), 0)
            current = self._counts.get((key, window), 0)
            estimate = previous * (1 - elapsed) + current
            reset = math.ceil(window_seconds * (1 - elapsed))
            if estimate + 1 > limit:
                return Decision(False, limit, 0, max(1, reset))
            self._counts[(key, window)] = current + 1
            if window != self._cleaned:  # forget windows older than the previous one
                self._cleaned = window
                for old in [k for k in self._counts if k[1] < window - 1]:
                    del self._counts[old]
        return Decision(True, limit, max(0, int(limit - estimate - 1)), reset)
