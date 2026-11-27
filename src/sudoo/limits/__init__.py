"""Limits: how many requests per minute (rate limiting), and how many instances, how often (quotas.py).

Request limits are per signed-in user, per API token, or per address for callers not signed in; reads and changes
are counted apart (RATE_LIMIT_READS_PER_MINUTE, RATE_LIMIT_CHANGES_PER_MINUTE, both changeable on the Settings
page). The counts are kept by the rate-limiter plugin named by RATE_LIMITER (base.py).
"""

from __future__ import annotations

from functools import cache

from sudoo import plugin_guard, plugins
from sudoo.config import settings

from .base import Decision, RateLimiter

__all__ = ["Decision", "RateLimiter", "check_request", "limiter"]


@cache
def limiter() -> RateLimiter:
    """The RATE_LIMITER plugin; the built-in one when it cannot be loaded, and for each call it fails."""
    plugin: RateLimiter = plugin_guard.load_safely(
        plugins.RATE_LIMITER_GROUP, settings.rate_limiter, settings, plugin_guard.default_of("rate_limiter"),
        call_fallback=True,
    )  # fmt: skip
    return plugin


plugin_guard.on_reset(plugins.RATE_LIMITER_GROUP, limiter.cache_clear)


def check_request(caller: str, changes: bool) -> Decision | None:
    """Count a request of `caller` ("user:<name>", "ip:<address>"); None when no limit is set."""
    limit = settings.rate_limit_changes_per_minute if changes else settings.rate_limit_reads_per_minute
    if limit <= 0:
        return None
    return limiter().hit(f"{'changes' if changes else 'reads'}:{caller}", limit)
