"""The premium signing keys this panel trusts: separate from the hub's keys, so a marketplace signature never passes
as a premium one, and a hub key can never sign a premium package or binding.

Two roles, never mixed: a release key signs the premium packages (kept offline), a binding key signs the bindings of
a download to a server (the hub signs those on demand, so it holds that key). A package is accepted only with a
release key's signature and a binding only with a binding key's: whoever holds a binding key cannot sign a package.

    roots     release keys: SHIPPED_PREMIUM_KEYS (the public keys of Sudoo.sh's premium key, Ed25519, base64url of the
              raw 32 bytes), or PREMIUM_PUBLIC_KEYS (.env only) instead, for a test panel; binding keys:
              SHIPPED_PREMIUM_BINDING_KEYS, or PREMIUM_BINDING_PUBLIC_KEYS (.env only). A key named in both is a
              release key only.
    rotation  the hub lists its premium keys (GET /api/v1/premium/keys); a new release key is trusted only when a
              release key already trusted endorsed it ("sudoo-premium-key-v1\\n<key id>\\n<public key>\\n<not_before>"),
              a new binding key (listed with "role": "binding") only when a release key endorsed it
              ("sudoo-premium-binding-key-v1\\n..."), as for the hub's own keys. A revoked key is dropped at once and
              never trusted again, whatever the roots become; a retiring one until its not_after.
"""

from __future__ import annotations

import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from sudoo.config import settings
from sudoo.hub import client, crypto
from sudoo.storage import storage

SHIPPED_PREMIUM_KEYS: tuple[str, ...] = (
    "Dz8rIXRIXpzL0dbExRdWJDoj6ogjEuOq5m28wPaGtL0",
)  # Sudoo.sh premium release key 4f4f3c18cd19ac11
SHIPPED_PREMIUM_BINDING_KEYS: tuple[str, ...] = (
    "B1YW6Gbv-kBNeZhhNKckRLw1-ATR-aTd5I28Bop_DGw",
)  # the hub's binding key 13608776833dde7e
KEYS_PATH = "/api/v1/premium/keys"
STATE_COLLECTION = "premium_state"
RELEASE = "release"
BINDING = "binding"


def roots(role: str = RELEASE) -> list[str]:
    if role == BINDING:
        configured = [k for k in str(settings.premium_binding_public_keys).split(",") if k]
        return configured or list(SHIPPED_PREMIUM_BINDING_KEYS)
    configured = [k for k in str(settings.premium_public_keys).split(",") if k]
    return configured or list(SHIPPED_PREMIUM_KEYS)


def _root_entries() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for role in (BINDING, RELEASE):  # a key named in both: a release key only
        for text in roots(role):
            try:
                key = crypto.public_key_from_b64url(text)
            except ValueError:
                continue
            found[crypto.key_id(key)] = {"public_key": crypto.public_key_b64url(key), "state": "active",
                                         "not_after": None, "source": "root", "role": role}  # fmt: skip
    return found


def known() -> dict[str, dict[str, Any]]:
    """The keys as last applied: the roots, with what the hub's list changed (revoked, retiring, endorsed). A state
    kept from the hub's list wins over a root entry, so a revoked root stays revoked when the roots change; the
    role of a root comes from where it is configured (a key kept from before roles: a release key)."""
    stored = (storage().get(STATE_COLLECTION, "keys") or {}).get("keys")
    found = {
        kid: {**entry, "role": entry.get("role") if entry.get("role") in (RELEASE, BINDING) else RELEASE}
        for kid, entry in (stored if isinstance(stored, dict) else {}).items()
        if isinstance(entry, dict)
    }
    roots_now = _root_entries()
    for kid, root in roots_now.items():
        before = found.get(kid)
        if before is not None and before.get("public_key") == root["public_key"]:
            found[kid] = {**before, "role": root["role"]}
        else:
            found[kid] = root
    return {
        kid: entry
        for kid, entry in found.items()
        if entry.get("source") != "root" or kid in roots_now or entry.get("state") == "revoked"
    }


def trusted(now: float | None = None, role: str = RELEASE) -> dict[str, Ed25519PublicKey]:
    """key id -> public key of every premium key of `role` accepted now: RELEASE for package signatures, BINDING for
    bindings."""
    now = time.time() if now is None else now
    found: dict[str, Ed25519PublicKey] = {}
    for kid, entry in known().items():
        if entry.get("role") != role:
            continue
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
    apply_listed(answer.data, checked=True)
    return trusted()


def apply_listed(items: Any, checked: bool = False) -> bool:
    """Apply a list of premium keys as the hub gives it (GET /api/v1/premium/keys), from the hub or passed on by the
    main panel to its agents: what it may change is only what `applied` allows (a new key only with an endorsement by
    a trusted release key, a revocation at once), so whoever passes it on cannot add a key. True when the keys
    changed."""
    listed = []
    for item in items[:64] if isinstance(items, list) else []:
        entry = client._listed(item)
        if entry is not None:
            listed.append({**entry, "role": BINDING if item.get("role") == BINDING else RELEASE})
    before = known()
    after = applied(before, listed, time.time())
    if checked:  # from the hub itself
        storage().put(STATE_COLLECTION, "keys", {"keys": after, "checked_at": time.time()})
    elif after != before:
        storage().put(STATE_COLLECTION, "keys", {**(storage().get(STATE_COLLECTION, "keys") or {}), "keys": after})
    return after != before


def applied(current: dict[str, dict[str, Any]], listed: list[dict[str, Any]], now: float) -> dict[str, dict[str, Any]]:
    """The keys once the hub's list is applied, each role apart: a listed key never changes role, release keys are
    endorsed by release keys, binding keys only by release keys (never by another binding key)."""
    release_known = {kid: e for kid, e in current.items() if e.get("role") == RELEASE}
    binding_known = {kid: e for kid, e in current.items() if e.get("role") == BINDING}
    release_listed = [e for e in listed if e["role"] == RELEASE and e["key_id"] not in binding_known]
    binding_listed = [e for e in listed if e["role"] == BINDING and e["key_id"] not in release_known]
    release = client.trusted_after(release_known, release_listed, now, crypto.premium_endorsement_message)
    message = crypto.premium_binding_endorsement_message
    binding = client.trusted_after(binding_known, binding_listed, now, message)
    binding = {kid: e for kid, e in binding.items() if kid in binding_known}  # states and dates of the known ones
    new = [e for e in binding_listed if e["key_id"] not in binding_known and str(e.get("endorsed_by")) in release]
    endorsed = client.trusted_after(release, new, now, message)
    found = {kid: {**e, "role": RELEASE} for kid, e in release.items()}
    found |= {kid: {**e, "role": BINDING} for kid, e in binding.items()}
    found |= {kid: {**e, "role": BINDING} for kid, e in endorsed.items() if kid not in release}
    return found
