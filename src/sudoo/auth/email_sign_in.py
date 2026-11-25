"""Signing in with a code sent by e-mail, without a password (sign-in method "email_code", methods.py).

    1. The person types their e-mail address and asks for a code. The answer is always the same ("if an account
       uses this address, a code is on its way"), whether an account uses the address or not, and the e-mail is sent
       in the background, so neither the answer nor its timing tells who has an account.
    2. When an account uses the address and may sign in this way, a 6-digit code (codes.py: valid 10 minutes, 5 wrong
       tries, one new code a minute) goes to it (mail/catalog.py: email_sign_in_code).
    3. The person types the code; a right one signs the account in (then the second factor rules apply, as after a
       password). Wrong codes count like wrong passwords (accounts.THROTTLE, in the caller).

Each address (IP; an IPv6 address counts as its /64) may ask for EMAIL_CODE_SENDS_PER_HOUR codes per hour, and each
account is sent at most EMAIL_CODE_SENDS_PER_HOUR_PER_ACCOUNT codes per hour from all addresses together, so the form
cannot be used to flood mailboxes. Over the account's limit nothing is sent and the answer stays the same. Wrong
tries are not capped per account: a code allows 5 of them, and an address's wrong tries are limited by
accounts.THROTTLE.

The functions take a `find` (an e-mail address -> the account's key and the account, or None) and a `purpose`, so
another kind of account (a client portal) can use the same machinery with its own accounts: request_code(address,
origin, find=its_find, purpose="portal-sign-in").
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from sudoo import accounts
from sudoo.config import settings

from . import codes, methods
from .base import Account, RequestInfo
from .email_code import EMAIL_RE, masked, send_code

log = logging.getLogger(__name__)

PURPOSE = "email-sign-in"
TEMPLATE = "email_sign_in_code"

Finder = Callable[[str], tuple[str, Account] | None]


def panel_account(address: str) -> tuple[str, Account] | None:
    """The panel's account that uses `address` and may sign in with an e-mail code, or None."""
    name = accounts.find(address) if "@" in address else None
    account = accounts.get(name) if name else None
    if name is None or account is None or not account.get("email"):
        return None
    if not methods.allows(account, "email_code"):
        return None
    return name, account


class SendLimit:
    """At most a number of codes per key in any hour: per address (IP, as its /64 for IPv6) with
    EMAIL_CODE_SENDS_PER_HOUR, and per account with EMAIL_CODE_SENDS_PER_HOUR_PER_ACCOUNT."""

    WINDOW_SECONDS = 3600

    def __init__(self, limit: Callable[[], int] = lambda: settings.email_code_sends_per_hour) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._asked: dict[str, deque[float]] = {}

    def allow(self, ip: str) -> bool:
        """Count one request from `ip`; False when it is over the limit (and then it is not counted)."""
        ip = accounts.rate_key(ip)
        now = time.time()
        with self._lock:
            for key in [k for k, v in self._asked.items() if not v or now - v[-1] > self.WINDOW_SECONDS]:
                del self._asked[key]
            asked = self._asked.setdefault(ip, deque())
            while asked and now - asked[0] > self.WINDOW_SECONDS:
                asked.popleft()
            if len(asked) >= self._limit():
                return False
            asked.append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._asked.clear()


SEND_LIMIT = SendLimit()
ACCOUNT_SEND_LIMIT = SendLimit(lambda: settings.email_code_sends_per_hour_per_account)


def _deliver(job: Callable[[], None]) -> None:
    """Send in the background: a slow mail server must not tell, by the time the answer takes, that an account
    exists. Tests replace this to send at once."""
    threading.Thread(target=job, daemon=True, name="email-sign-in-code").start()


def request_code(
    address: str, origin: RequestInfo, *, find: Finder = panel_account, purpose: str = PURPOSE, template: str = TEMPLATE
) -> dict[str, str]:
    """Step 1: send a code when an account uses `address`; always returns {"sent_to": "y***@example.com"}.
    Raises ValueError for an address that is not one, PermissionError when this IP asked too often."""
    address = address.strip()
    if not EMAIL_RE.match(address):
        raise ValueError("Enter a valid e-mail address.")
    if not SEND_LIMIT.allow(origin.ip):
        raise PermissionError("Too many codes asked for from this address. Try again in an hour.")
    found = find(address)
    if found is not None:
        key, account = found
        target = str(account.get("email") or address)
        try:
            code = codes.new((key, purpose))
        except ValueError:  # one a minute: the code sent a moment ago still works
            return {"sent_to": masked(address)}
        if not ACCOUNT_SEND_LIMIT.allow(f"{purpose}:{key}"):
            # this account was sent its share for the hour: no mail, and the same answer as ever
            codes.drop((key, purpose))
            log.warning("sign-in code for %s not sent: the account's hourly limit is reached", key)
            return {"sent_to": masked(address)}

        def job() -> None:
            try:
                send_code(target, key, code, origin, template)
            except Exception as error:  # noqa: BLE001 - the person asks again; the failure is logged
                log.warning("sign-in code for %s not sent: %r", key, error)

        _deliver(job)
    return {"sent_to": masked(address)}


def check_code(
    address: str, code: str, *, find: Finder = panel_account, purpose: str = PURPOSE
) -> tuple[str, Account] | None:
    """Step 2: (key, account) when `code` is the right code for the account of `address` (it is used up), else None."""
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6 or not EMAIL_RE.match(address.strip()):
        return None
    found = find(address.strip())
    if found is None:
        return None
    key, account = found
    return (key, account) if codes.check((key, purpose), code)[0] else None
