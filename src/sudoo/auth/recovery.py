"""Recovery codes: what signs in when the phone with the authenticator app is lost.

When someone sets up the authenticator app, the panel makes RECOVERY_CODES_COUNT codes (10 by default) and shows
them once. Each code is 10 random characters from 31 that are hard to confuse (about 49 bits), shown as
"k7m2p-x9qrt", and works once, at sign-in, in place of the app's code. The panel keeps only a keyed hash of each
(HMAC-SHA256 with a key derived from the vault key, like the password pepper): the codes are random and long, so a
slow hash adds nothing, and without the vault key the hashes cannot even be tested.

Making new codes replaces every earlier one. They belong to the authenticator app: removing the app, or an
administrator removing an account's second factors, removes them too. Wrong codes count like wrong passwords
(accounts.THROTTLE), and every use is in the audit log.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

from sudoo import accounts
from sudoo.config import settings
from sudoo.core import vault
from sudoo.storage import Document, storage

ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # no 0/o, 1/l/i to mistype
LENGTH = 10
FIELD = "recovery_codes"
METHOD = "recovery"
LABEL = "Recovery code"


def _key() -> bytes:
    return hmac.new(vault.key(), b"recovery-codes", hashlib.sha256).digest()


def normalize(code: str) -> str:
    """What was typed, without spaces, dashes and capitals: "K7M2P-X9QRT" -> "k7m2px9qrt"."""
    return "".join(code.split()).replace("-", "").lower()


def digest(username: str, code: str) -> str:
    """The stored form of a code; the user name is part of it, so a hash copied to another account matches nothing."""
    return hmac.new(_key(), f"{username}\0{normalize(code)}".encode(), hashlib.sha256).hexdigest()


def _new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    return f"{raw[:5]}-{raw[5:]}"


def create(username: str) -> list[str]:
    """Make a new set of codes for the account, replacing every earlier one; returns them (shown once)."""
    codes = [_new_code() for _ in range(settings.recovery_codes_count)]
    stored = {"hashes": [digest(username, code) for code in codes], "created": time.time(), "total": len(codes)}
    accounts.update(username, **{FIELD: stored})
    return codes


def state(account: dict[str, Any]) -> dict[str, Any] | None:
    """{"left", "total", "created"} of the account's codes, or None when it has none."""
    stored = account.get(FIELD)
    if not stored:
        return None
    return {"left": len(stored.get("hashes", [])), "total": stored.get("total", 0), "created": stored.get("created")}


def available(account: dict[str, Any]) -> bool:
    """Whether a recovery code can sign this account in: it uses the authenticator app and has codes left."""
    has_app = "secret" in account.get("second_factors", {}).get("totp", {})
    return has_app and bool((account.get(FIELD) or {}).get("hashes"))


def needed(account: dict[str, Any]) -> bool:
    """The account uses the authenticator app but has no recovery codes (set up before they existed, or all used)."""
    has_app = "secret" in account.get("second_factors", {}).get("totp", {})
    return has_app and not (account.get(FIELD) or {}).get("hashes")


def use(username: str, code: str) -> int | None:
    """Spend a code: the number of codes left when it was right (it never works again), None when it was not."""
    wanted = normalize(code)
    if len(wanted) != LENGTH or any(c not in ALPHABET for c in wanted):
        return None
    candidate = digest(username, wanted)

    def spend(current: Document | None) -> Document | None:
        """Atomically: the same code sent twice at once works only once."""
        stored = (current or {}).get(FIELD) or {}
        hashes = list(stored.get("hashes", []))
        match = next((h for h in hashes if hmac.compare_digest(h, candidate)), None)
        if current is None or match is None:
            raise PermissionError("not a code of this account")
        hashes.remove(match)
        return {**current, FIELD: {**stored, "hashes": hashes, "last_used": time.time()}}

    try:
        changed = storage().change(accounts.USERS, username, spend) or {}
    except PermissionError:
        return None
    return len(changed[FIELD]["hashes"])
