"""The interface of a rate-limiter plugin: where request counts are kept.

Built in: "memory" (memory.py), right for one panel process. Several panels behind one address would share counts
through a plugin for Redis or similar, registered under "sudoo.rate_limiters".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int  # until the window ends (and, when refused, when to try again)


class RateLimiter(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int = 60) -> Decision:
        """Count one request for `key` and decide whether it is within `limit` per window."""
        ...
