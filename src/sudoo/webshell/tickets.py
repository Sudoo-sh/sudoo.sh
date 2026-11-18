"""One-time tickets that open a shell's WebSocket.

A browser cannot add headers to a WebSocket, and the panel's CSRF token travels in a header. So the web app first
asks for a ticket with an ordinary authenticated change (POST, same origin, session cookie and X-CSRF-Token: every
check of security.py), then opens the socket with the ticket. A ticket:

    - is 32 random bytes; only its SHA-256 is kept, in memory (the panel runs one process);
    - is bound to the person, their panel session (sid), the instance and what they asked for (target, root,
      production confirmed); the socket accepts it only from that same session;
    - works once (taken out when used, even when the connection is then refused) and for SHELL_TICKET_SECONDS.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass

from sudoo.config import settings

MAX_WAITING_PER_USER = 5  # tickets asked for but not used yet; more are refused until some are used or expired


@dataclass(frozen=True, slots=True)
class Ticket:
    user: str
    sid: str
    instance: str
    target: str
    as_root: bool
    confirmed: bool
    ip: str
    expires: float


_TICKETS: dict[str, Ticket] = {}  # SHA-256 of the ticket -> what it opens
_LOCK = threading.Lock()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _forget_expired(now: float) -> None:
    for digest in [d for d, t in _TICKETS.items() if t.expires <= now]:
        del _TICKETS[digest]


def waiting(*, user: str | None = None, instance: str | None = None) -> int:
    """Valid tickets not used yet, of a person or for an instance (they count toward the limits of open shells)."""
    now = time.time()
    with _LOCK:
        _forget_expired(now)
        return sum(
            1
            for t in _TICKETS.values()
            if (user is None or t.user == user) and (instance is None or t.instance == instance)
        )


def issue(
    *, user: str, sid: str, instance: str, target: str, as_root: bool, confirmed: bool, ip: str
) -> tuple[str, int]:
    """A new ticket and the seconds it stays valid. Raises OverflowError when the person has too many waiting."""
    seconds = int(settings.shell_ticket_seconds)
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        _forget_expired(now)
        if sum(1 for t in _TICKETS.values() if t.user == user) >= MAX_WAITING_PER_USER:
            raise OverflowError("Too many shells are being opened at once. Wait a few seconds.")
        _TICKETS[_digest(token)] = Ticket(user, sid, instance, target, as_root, confirmed, ip, now + seconds)
    return token, seconds


def take(token: str) -> Ticket | None:
    """The ticket's details, once: a ticket is removed when it is presented, valid or not. None when unknown or
    expired."""
    if not token or len(token) > 100:
        return None
    with _LOCK:
        found = _TICKETS.pop(_digest(token), None)
    if found is None or found.expires <= time.time():
        return None
    return found


def clear() -> None:
    """Forget every ticket (tests)."""
    with _LOCK:
        _TICKETS.clear()
