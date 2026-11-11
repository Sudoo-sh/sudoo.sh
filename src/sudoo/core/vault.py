"""Encryption of the secrets the panel must be able to read back.

Two kinds of secrets are kept, and each is protected the way it is used:

    checked only     panel passwords             one-way scrypt hash (accounts.py), never stored readable
    read back        Odoo master passwords,      AES-256-GCM, encrypted with the vault key (this module)
                     GitHub deploy keys

The vault key is 32 random bytes in VAULT_KEY_FILE (mode 600), created on first use. It is kept apart from the
data it protects: a copy of users.json, of an instance folder or of the deploy keys is useless without it.

Encrypted text looks like `enc:v1:<base64 of nonce + ciphertext + tag>`. Every value is encrypted together with a
label saying what it is (for example "deploy-key:owner/repo"); decrypting it under another label fails, so an
encrypted value cannot be copied into another place and used there.
"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sudoo.config import settings

PREFIX = "enc:v1:"
_NONCE_BYTES = 12
_KEY: bytes | None = None


def write_private(path: Path, text: str) -> None:
    """Write a file atomically, readable by its owner (root) only."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


class VaultError(Exception):
    """The value cannot be decrypted: it was changed, or belongs to another label or another vault key."""


def key() -> bytes:
    """The 32-byte vault key, created on first use."""
    global _KEY
    if _KEY is None:
        if not settings.vault_key_file.exists():
            fd = os.open(settings.vault_key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(secrets.token_bytes(32))
        _KEY = settings.vault_key_file.read_bytes()
        if len(_KEY) != 32:
            raise VaultError(f"{settings.vault_key_file} is not a valid vault key.")
    return _KEY


def encrypt(plain: str | bytes, label: str) -> str:
    """`plain` (str or bytes) encrypted for `label`, as text safe to store in any file."""
    data = plain.encode() if isinstance(plain, str) else plain
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(key()).encrypt(nonce, data, label.encode())
    return PREFIX + base64.b64encode(nonce + sealed).decode()


def decrypt_bytes(token: str, label: str) -> bytes:
    """The bytes encrypted in `token` for `label`; raises VaultError when it was changed or has another label."""
    if not token.startswith(PREFIX):
        raise VaultError("The value is not encrypted.")
    raw = base64.b64decode(token[len(PREFIX) :])
    try:
        return AESGCM(key()).decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], label.encode())
    except Exception as error:  # cryptography raises InvalidTag without a message
        raise VaultError(f"Cannot decrypt the value for {label}.") from error


def decrypt(token: str, label: str) -> str:
    """The text encrypted in `token` for `label`."""
    return decrypt_bytes(token, label).decode()
