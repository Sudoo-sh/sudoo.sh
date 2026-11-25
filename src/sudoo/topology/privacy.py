"""Pseudonyms for the topology: the same name gives the same short id on this panel, and nothing outside it.

An id is HMAC-SHA256 of the thing's kind and real name under a random key that never leaves the panel (kept in the
panel's storage, collection "topology"), cut to 10 hexadecimal characters and prefixed with the kind's letter:
n- (node), p- (project), i- (instance), g- (compose project or namespace). Sudoo.sh can follow one instance from
one snapshot to the next (its rates over time), but cannot tell its name, nor match it with another panel's.
Forgetting the key (`forget()`, done when sharing is switched off) makes every later id different.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading

from sudoo.storage import storage

COLLECTION = "topology"
PREFIXES = {"node": "n", "project": "p", "instance": "i", "group": "g"}
LENGTH = 10

_LOCK = threading.Lock()
_KEY: bytes | None = None


def _key() -> bytes:
    global _KEY
    with _LOCK:
        if _KEY is None:
            found = storage().get(COLLECTION, "key") or {}
            value = str(found.get("value") or "")
            if len(value) != 64:
                value = secrets.token_hex(32)
                storage().put(COLLECTION, "key", {"value": value})
            _KEY = bytes.fromhex(value)
        return _KEY


def pseudonym(kind: str, name: str) -> str:
    """The short, stable id of a node, project, instance or group by its real name."""
    digest = hmac.new(_key(), f"{kind}\0{name}".encode(), hashlib.sha256).hexdigest()
    return f"{PREFIXES[kind]}-{digest[:LENGTH]}"


def has_key() -> bool:
    """Whether a pseudonym key is kept (it is made at the first pseudonym, so not before something is shared)."""
    with _LOCK:
        return _KEY is not None or storage().get(COLLECTION, "key") is not None


def forget() -> None:
    """Drop the key: ids made from now on differ from every earlier one."""
    global _KEY
    with _LOCK:
        storage().delete(COLLECTION, "key")
        _KEY = None
