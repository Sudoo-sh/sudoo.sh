"""API tokens: how other systems use the panel's API (/api/v1, see docs/PANEL_API.md).

Each token has a name, a scope and two secrets, both shown once when it is created:

    token            sudoo_<id>_<secret>   sent in "Authorization: Bearer ..."; stored only as a SHA-256 hash
    signing secret   random               signs every request (HMAC-SHA256); stored encrypted with the vault key

Scopes:
    read    list and read instances, jobs and versions
    write   everything read allows, plus create, change, run actions on and delete instances

A request needs both: the token says who is calling, the signature proves the request was made by the holder of the
signing secret, was not changed on the way, and is new (timestamp within 5 minutes, nonce never seen before). A
token alone, copied from a log or a proxy, is not enough.

Tokens are stored in the panel's storage (collection "api_tokens"). The token secret has 256 bits of randomness, so a fast hash
(SHA-256) is enough; slow password hashing is for secrets people choose.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from typing import Any

from sudoo.core import vault
from sudoo.storage import storage

SCOPES = {"read": "Read only", "write": "Read and write"}
TOKEN_RE = re.compile(r"^sudoo_([0-9a-f]{12})_([A-Za-z0-9_-]{43})$")
NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]{3,40}$")
MAX_CLOCK_SKEW_SECONDS = 300
_LOCK = threading.Lock()
_NONCES: dict[str, float] = {}  # nonce -> time it may be forgotten


TOKENS = "api_tokens"


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _label(token_id: str) -> str:
    return f"api-signing-secret:{token_id}"


def create(name: str, scope: str, created_by: str) -> tuple[str, str]:
    """Create a token; return (token, signing secret). Neither can be shown again."""
    if not NAME_RE.match(name):
        raise ValueError("Token names are 3 to 40 letters, digits, spaces, dots, dashes or underscores.")
    if scope not in SCOPES:
        raise ValueError("Choose the scope read or write.")
    token_id, secret, signing_secret = secrets.token_hex(6), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with _LOCK:
        if any(record["name"] == name for record in storage().all(TOKENS).values()):
            raise ValueError("A token with this name already exists.")
        storage().put(
            TOKENS,
            token_id,
            {
                "name": name,
                "scope": scope,
                "hash": _digest(secret),
                "signing_secret": vault.encrypt(signing_secret, _label(token_id)),
                "created": time.time(),
                "created_by": created_by,
                "last_used": None,
            },
        )
    return f"sudoo_{token_id}_{secret}", signing_secret


def all_tokens() -> list[dict[str, Any]]:
    """Every token without its secrets, newest first."""
    tokens = storage().all(TOKENS)
    public = [
        {"id": token_id, **{key: value for key, value in record.items() if key not in ("hash", "signing_secret")}}
        for token_id, record in tokens.items()
    ]
    return sorted(public, key=lambda record: record["created"], reverse=True)


def revoke(token_id: str) -> str:
    """Delete a token at once; return its name."""
    record = storage().get(TOKENS, token_id)
    if record is None or not storage().delete(TOKENS, token_id):
        raise KeyError(token_id)
    return str(record["name"])


def string_to_sign(timestamp: str, nonce: str, method: str, path: str, body: bytes) -> bytes:
    """What the client signs: timestamp, nonce, method, path with query and the body's SHA-256, one per line."""
    return "\n".join([timestamp, nonce, method.upper(), path, hashlib.sha256(body).hexdigest()]).encode()


def sign(signing_secret: str, message: bytes) -> str:
    return "sha256=" + hmac.new(signing_secret.encode(), message, hashlib.sha256).hexdigest()


def verify(
    authorization: str, timestamp: str, nonce: str, signature: str, method: str, path: str, body: bytes
) -> dict[str, Any]:
    """The token's record when the request is authentic; raises PermissionError with the reason otherwise."""
    match = TOKEN_RE.match(authorization.removeprefix("Bearer ").strip())
    if not match:
        raise PermissionError("Send the API token as: Authorization: Bearer sudoo_...")
    token_id, secret = match.groups()
    with _LOCK:
        record = storage().get(TOKENS, token_id)
    if record is None or not hmac.compare_digest(record["hash"], _digest(secret)):
        raise PermissionError("Unknown or revoked API token.")
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
        raise PermissionError("X-Sudoo-Timestamp is missing, or more than 5 minutes away from the server's clock.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce):
        raise PermissionError(
            "X-Sudoo-Nonce must be 16 to 64 letters, digits, dashes or underscores, new for each request."
        )
    expected = sign(
        vault.decrypt(record["signing_secret"], _label(token_id)), string_to_sign(timestamp, nonce, method, path, body)
    )
    if not hmac.compare_digest(expected, signature):
        raise PermissionError("Invalid X-Sudoo-Signature: see docs/PANEL_API.md for what to sign.")
    _remember_nonce(nonce)
    _mark_used(token_id)
    return {"id": token_id, **record}


def _remember_nonce(nonce: str) -> None:
    """Refuse a nonce seen before (a replayed request). Nonces are forgotten once their timestamp would be refused."""
    now = time.time()
    with _LOCK:
        for old in [key for key, forget_at in _NONCES.items() if forget_at < now]:
            del _NONCES[old]
        if nonce in _NONCES:
            raise PermissionError("This request was already received (X-Sudoo-Nonce used before).")
        _NONCES[nonce] = now + 2 * MAX_CLOCK_SKEW_SECONDS


def _mark_used(token_id: str) -> None:
    storage().change(TOKENS, token_id, lambda record: record and {**record, "last_used": time.time()})
