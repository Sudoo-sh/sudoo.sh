"""Short-lived one-time codes (sent by e-mail, or later SMS): created, checked, used once.

Codes are kept in memory as hashes, valid for 10 minutes and for 5 wrong tries; a restart forgets them, and the
person asks for a new one. A new code can be sent once a minute.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass

VALID_SECONDS = 600
MAX_TRIES = 5
RESEND_SECONDS = 60


@dataclass
class _Code:
    digest: str
    expires: float
    sent: float
    tries: int = 0
    value: str = ""  # what the code confirms, e.g. the e-mail address being set up


_CODES: dict[tuple[str, str], _Code] = {}
_LOCK = threading.Lock()


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def new(key: tuple[str, str], value: str = "") -> str:
    """A new 6-digit code for `key` (user, purpose). Raises ValueError when asked again within a minute."""
    now = time.time()
    with _LOCK:
        previous = _CODES.get(key)
        if previous and now - previous.sent < RESEND_SECONDS:
            raise ValueError(f"A code was just sent. Wait {int(RESEND_SECONDS - (now - previous.sent)) + 1} seconds.")
        code = f"{secrets.randbelow(10**6):06d}"
        _CODES[key] = _Code(_digest(code), now + VALID_SECONDS, now, value=value)
        for old in [k for k, c in _CODES.items() if c.expires < now]:
            del _CODES[old]
    return code


def check(key: tuple[str, str], code: str) -> tuple[bool, str]:
    """(right, value). A right code is used up; too many wrong tries use it up too."""
    with _LOCK:
        found = _CODES.get(key)
        if found is None or found.expires < time.time():
            return False, ""
        if hmac.compare_digest(found.digest, _digest(code.strip())):
            del _CODES[key]
            return True, found.value
        found.tries += 1
        if found.tries >= MAX_TRIES:
            del _CODES[key]
        return False, ""
