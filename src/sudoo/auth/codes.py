"""Short-lived one-time codes (sent by e-mail, or later SMS): created, checked, used once.

Codes are kept as hashes in the panel's storage (core/shared.py SharedMap), so every worker process knows them,
valid for 10 minutes (unless the caller says otherwise) and for 5 wrong tries; a new code can be sent once a minute.
Checking a code and counting a wrong try are one write: a code tried from two processes at once is used once.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

from sudoo.core.shared import SharedMap

VALID_SECONDS = 600
MAX_TRIES = 5
RESEND_SECONDS = 60

_CODES = SharedMap("one_time_codes")


def _key(key: tuple[str, str]) -> str:
    return "\x1f".join(key)


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def new(key: tuple[str, str], value: str = "", valid_seconds: int = VALID_SECONDS) -> str:
    """A new 6-digit code for `key` (user, purpose), valid `valid_seconds`. Raises ValueError when asked again within
    a minute."""
    now = time.time()
    code = f"{secrets.randbelow(10**6):06d}"
    waits: list[int] = []

    def replace(previous: dict[str, Any] | None) -> dict[str, Any] | None:
        if previous and now - float(previous["sent"]) < RESEND_SECONDS:
            waits.append(int(RESEND_SECONDS - (now - float(previous["sent"]))) + 1)
            return previous
        return {"digest": _digest(code), "expires": now + valid_seconds, "sent": now, "tries": 0, "value": value}

    _CODES.change(_key(key), replace)
    _CODES.prune()
    if waits:
        raise ValueError(f"A code was just sent. Wait {waits[0]} seconds.")
    return code


def check(key: tuple[str, str], code: str) -> tuple[bool, str]:
    """(right, value). A right code is used up; too many wrong tries use it up too."""
    result: list[tuple[bool, str]] = []

    def use(found: dict[str, Any] | None) -> dict[str, Any] | None:
        if found is None:
            result.append((False, ""))
            return None
        if hmac.compare_digest(str(found["digest"]), _digest(code.strip())):
            result.append((True, str(found.get("value") or "")))
            return None
        result.append((False, ""))
        tries = int(found.get("tries") or 0) + 1
        return None if tries >= MAX_TRIES else {**found, "tries": tries}

    _CODES.change(_key(key), use)
    return result[0] if result else (False, "")


def clear() -> None:
    """Forget every code (tests)."""
    _CODES.clear()
