"""The premium signing keys this panel trusts: separate from the hub's keys, so a marketplace signature never passes
as a premium one, and a hub key can never sign a premium package or binding.

    roots     SHIPPED_PREMIUM_KEYS (the public keys of Sudoo.sh's premium key, Ed25519, base64url of the raw 32
              bytes), or PREMIUM_PUBLIC_KEYS (.env only) instead, for a test panel
    rotation  the hub lists its premium keys (GET /api/v1/premium/keys); a new key is trusted only when a key already
              trusted endorsed it ("sudoo-premium-key-v1\\n<key id>\\n<public key>\\n<not_before>"), as for the hub's
              own keys. A revoked key is dropped at once and never trusted again; a retiring one until its not_after.
"""

from __future__ import annotations

import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from sudoo.config import settings
from sudoo.hub import client, crypto
from sudoo.storage import storage

SHIPPED_PREMIUM_KEYS: tuple[str, ...] = ()  # the owner adds the public key of the premium signing key here
KEYS_PATH = "/api/v1/premium/keys"
STATE_COLLECTION = "premium_state"


def roots() -> list[str]:
    configured = [k for k in str(settings.premium_public_keys).split(",") if k]
    return configured or list(SHIPPED_PREMIUM_KEYS)


def _root_entries() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for text in roots():
        try:
            key = crypto.public_key_from_b64url(text)
        except ValueError:
            continue
        found[crypto.key_id(key)] = {"public_key": crypto.public_key_b64url(key), "state": "active",
                                     "not_after": None, "source": "root"}  # fmt: skip
    return found


def known() -> dict[str, dict[str, Any]]:
    """The keys as last applied: the roots, with what the hub's list changed (revoked, retiring, endorsed)."""
    stored = (storage().get(STATE_COLLECTION, "keys") or {}).get("keys")
    roots_now = _root_entries()
    if not isinstance(stored, dict) or set(roots_now) - set(stored):
        return {**(stored if isinstance(stored, dict) else {}), **roots_now}
    return {kid: entry for kid, entry in stored.items() if entry.get("source") != "root" or kid in roots_now}


def trusted(now: float | None = None) -> dict[str, Ed25519PublicKey]:
    """key id -> public key of every premium key accepted now."""
    now = time.time() if now is None else now
    found: dict[str, Ed25519PublicKey] = {}
    for kid, entry in known().items():
        not_after = entry.get("not_after")
        if entry.get("state") == "active" or (entry.get("state") == "retiring" and not_after and now < not_after):
            try:
                found[kid] = crypto.public_key_from_b64url(str(entry["public_key"]))
            except (KeyError, ValueError):
                continue
    return found


def refresh() -> dict[str, Ed25519PublicKey]:
    """Apply the hub's list of premium keys (a signed answer); returns the keys trusted afterwards."""
    answer = client.request("GET", KEYS_PATH)
    items = answer.data if isinstance(answer.data, list) else []
    listed = [entry for entry in map(client._listed, items) if entry is not None]
    applied = client.trusted_after(known(), listed, time.time(), crypto.premium_endorsement_message)
    storage().put(STATE_COLLECTION, "keys", {"keys": applied, "checked_at": time.time()})
    return trusted()
