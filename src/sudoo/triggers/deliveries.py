"""GitHub deliveries handled once: the same delivery sent again (GitHub's own redelivery, a retry after a timeout),
or the same push sent by both the GitHub App and a repository webhook, updates the instances once.

A delivery is claimed before it is handled, inside one write of the storage, so two copies arriving at the same
moment cannot both pass: the first one is handled, the others are answered "delivered already". A delivery whose
handling fails is given back, so GitHub's next try is handled. Claims are kept for a day (at most KEEP_MOST).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from sudoo.storage import Document, storage

COLLECTION = "deliveries"
_KEY = "recent"
KEEP_SECONDS = 24 * 3600
KEEP_MOST = 2000


def keys_of(delivery: str | None, event: str, payload: Mapping[str, Any]) -> list[str]:
    """What identifies a delivery: GitHub's delivery id, and for a push the push itself (repository, branch, the
    commits before and after), which is the same whichever way it was sent."""
    keys = [f"delivery:{delivery[:100]}"] if delivery else []
    after = str(payload.get("after") or "")
    if event == "push" and after:
        repository = str((payload.get("repository") or {}).get("full_name", "")).lower()
        keys.append(f"push:{repository}:{payload.get('ref', '')}:{payload.get('before', '')}:{after}"[:400])
    return keys


def claim(keys: list[str], now: float | None = None) -> bool:
    """True when none of `keys` was claimed in the last KEEP_SECONDS: they are claimed now, in the same write."""
    if not keys:
        return True
    now = time.time() if now is None else now
    claimed: list[bool] = []

    def apply(doc: Document | None) -> Document:
        seen = {key: when for key, when in ((doc or {}).get("seen") or {}).items() if now - float(when) < KEEP_SECONDS}
        if any(key in seen for key in keys):
            return {"seen": seen}
        seen.update(dict.fromkeys(keys, now))
        if len(seen) > KEEP_MOST:
            seen = dict(sorted(seen.items(), key=lambda item: item[1])[-KEEP_MOST:])
        claimed.append(True)
        return {"seen": seen}

    storage().change(COLLECTION, _KEY, apply)
    return bool(claimed)


def release(keys: list[str]) -> None:
    """Forget the claims of a delivery that could not be handled: GitHub's next try is handled."""

    def apply(doc: Document | None) -> Document | None:
        if doc is None:
            return None
        return {"seen": {key: when for key, when in (doc.get("seen") or {}).items() if key not in keys}}

    storage().change(COLLECTION, _KEY, apply)


def once[T](keys: list[str], handle: Callable[[], T], duplicate: Callable[[], T]) -> T:
    """handle() for the first copy of a delivery, duplicate() for every other one."""
    if not claim(keys):
        return duplicate()
    try:
        return handle()
    except BaseException:
        release(keys)
        raise
