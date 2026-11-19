"""The premium signing keys this panel trusts: separate from the hub's keys, so a marketplace signature never passes
as a premium one. SHIPPED_PREMIUM_KEYS lists the public keys of Sudoo.sh's premium key (Ed25519, base64url of the
raw 32 bytes); PREMIUM_PUBLIC_KEYS (.env only) replaces the list, for a test panel. Several keys may be listed while
a key is rotated."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from sudoo.config import settings
from sudoo.hub import crypto

SHIPPED_PREMIUM_KEYS: tuple[str, ...] = ()  # the owner adds the public key of the premium signing key here


def trusted() -> dict[str, Ed25519PublicKey]:
    """key id -> public key of every trusted premium key."""
    configured = [k for k in str(settings.premium_public_keys).split(",") if k]
    found: dict[str, Ed25519PublicKey] = {}
    for text in configured or SHIPPED_PREMIUM_KEYS:
        try:
            key = crypto.public_key_from_b64url(text)
        except ValueError:
            continue
        found[crypto.key_id(key)] = key
    return found
