"""Second factor "totp": an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, ...).

Time-based one-time passwords, RFC 6238: the app and the panel share a secret and both compute a 6-digit code from
it and the current 30-second step. Free, works without a network on the phone, nothing to send. The secret is
stored encrypted with the vault key; each code works once (the last used step is remembered), and a code of the
step before or after is accepted for clocks that are a little off.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Any
from urllib.parse import quote, urlencode

from sudoo import accounts
from sudoo.config import Settings
from sudoo.core import vault
from sudoo.storage import Document, storage

from .base import Account, RequestInfo

STEP_SECONDS = 30
DIGITS = 6
ISSUER = "Sudoo.sh"


def code_at(secret_base32: str, step: int) -> str:
    """The code of a 30-second step (RFC 6238 with HMAC-SHA1, the algorithm every app supports)."""
    key = base64.b32decode(secret_base32 + "=" * (-len(secret_base32) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 10**DIGITS:0{DIGITS}d}"


def _matching_step(secret_base32: str, code: str, after: int) -> int | None:
    """The step (now, one before or one after) whose code is `code`, if it is later than `after`."""
    now = int(time.time() // STEP_SECONDS)
    for step in (now, now - 1, now + 1):
        if step > after and hmac.compare_digest(code_at(secret_base32, step), code.strip()):
            return step
    return None


def _label(username: str) -> str:
    return f"totp:{username}"


class AuthenticatorApp:
    name = "totp"
    label = "Authenticator app"
    available = True

    def __init__(self, settings: Settings) -> None:
        pass

    def enrolled(self, account: Account) -> bool:
        return "secret" in account.get("second_factors", {}).get("totp", {})

    def challenge(self, username: str, account: Account, origin: RequestInfo) -> dict[str, Any]:
        return {}  # nothing to send: the code is in the app

    def verify(self, username: str, account: Account, code: str) -> bool:
        stored = account["second_factors"]["totp"]
        step = _matching_step(vault.decrypt(stored["secret"], _label(username)), code, stored.get("last_step", 0))
        if step is None:
            return False

        def use(current: Document | None) -> Document | None:
            """Remember the step, atomically: the same code sent twice at once works only once."""
            totp = (current or {}).get("second_factors", {}).get("totp", {})
            if current is None or totp.get("last_step", 0) >= step:
                raise PermissionError("code already used")
            return {**current, "second_factors": {**current["second_factors"], "totp": {**totp, "last_step": step}}}

        try:
            storage().change(accounts.USERS, username, use)
        except PermissionError:
            return False
        return True

    def begin_setup(
        self, username: str, account: Account, details: dict[str, Any], origin: RequestInfo
    ) -> dict[str, Any]:
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        accounts.update(username, totp_pending=vault.encrypt(secret, _label(username)))
        query = urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": STEP_SECONDS})
        return {"secret": secret, "otpauth_uri": f"otpauth://totp/{quote(ISSUER)}:{quote(username)}?{query}"}

    def finish_setup(self, username: str, account: Account, code: str) -> dict[str, Any] | None:
        pending = account.get("totp_pending")
        if not pending:
            return None
        secret = vault.decrypt(pending, _label(username))
        step = _matching_step(secret, code, 0)
        if step is None:
            return None
        totp = {"secret": pending, "last_step": step, "added": int(time.time())}
        return {"totp_pending": None, "second_factors": {**account.get("second_factors", {}), "totp": totp}}

    def remove(self, account: Account) -> dict[str, Any]:
        factors = {k: v for k, v in account.get("second_factors", {}).items() if k != "totp"}
        return {"second_factors": factors, "totp_pending": None}
