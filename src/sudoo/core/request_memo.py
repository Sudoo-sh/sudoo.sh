"""Facts looked up again and again while one reading request is answered, looked up once for it.

A list checks each project or instance it shows against the caller's access, and each check asked the storage for
the same documents (the projects, the teams a person belongs to, ...): thousands of small reads for one answer. Under load each read
also waits its turn for the interpreter, so with 1,000 projects and 50 people at once the panel answered about one
request a second.

Only requests that read (GET, HEAD) keep anything, and only until their answer is sent (flask.g): a request that
changes something, and background work, look every time. A fact kept here may be a moment older than the storage,
as a read that started a moment earlier would be.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import cast

_KEY = "_sudoo_request_memo"


def _memo() -> dict[Hashable, object] | None:
    from flask import g, has_request_context, request

    if not has_request_context() or request.method not in ("GET", "HEAD"):
        return None
    found: dict[Hashable, object] | None = g.get(_KEY)
    if found is None:
        found = {}
        setattr(g, _KEY, found)
    return found


def remember[T](key: Hashable, build: Callable[[], T]) -> T:
    """build(), once per `key` within a reading request (callers must not change what it returns); every time
    otherwise."""
    memo = _memo()
    if memo is None:
        return build()
    if key in memo:
        return cast(T, memo[key])  # stored below with the same key, by the same caller
    found = build()
    memo[key] = found
    return found


def forget(which: Callable[[Hashable], bool]) -> None:
    """Drop what the current request keeps for the keys `which` accepts (they were just changed)."""
    from flask import g, has_request_context

    if has_request_context():
        found: dict[Hashable, object] | None = g.get(_KEY)
        if found:
            for key in [key for key in found if which(key)]:
                del found[key]
