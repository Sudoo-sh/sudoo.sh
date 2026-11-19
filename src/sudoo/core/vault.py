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
import contextlib
import errno
import os
import secrets
import stat
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sudoo.config import settings

PREFIX = "enc:v1:"
_NONCE_BYTES = 12
_KEY: bytes | None = None


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)


def open_dir(folder: Path, within: Path | None = None) -> int:
    """`folder` opened as a directory (a file descriptor the caller closes).

    With `within`, a folder another container can also write (GATEWAY_DIR, MAIL_CATCHER_DIR): every part of
    `folder` below `within` is opened without following a link, so a link planted there makes this fail
    (OSError) instead of leading somewhere else."""
    if within is None:
        return os.open(folder, _DIR_FLAGS)
    parts = Path(folder).relative_to(within).parts  # ValueError: not inside `within`
    if any(part in (".", "..") for part in parts):
        raise ValueError(f"{folder} is not inside {within}")
    fd = os.open(within, _DIR_FLAGS)
    try:
        for part in parts:
            inner = os.open(part, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = inner
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_private(path: Path, text: str | bytes, *, within: Path | None = None) -> None:
    """Write a file atomically, readable by its owner (root) only.

    The new content goes to a temporary file with a random name, created only if nothing (not even a link) is
    there yet, then moved into place: a link planted at the file's name is replaced, never followed. `within`:
    the folder is shared with another container, see open_dir()."""
    data = text.encode() if isinstance(text, str) else text
    folder = open_dir(path.parent, within)
    try:
        temporary = f".{path.name}.{secrets.token_hex(8)}.tmp"
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=folder,
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                os.fchmod(fh.fileno(), 0o600)
                fh.write(data)
            os.replace(temporary, path.name, src_dir_fd=folder, dst_dir_fd=folder)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=folder)
            raise
    finally:
        os.close(folder)


def remove_private(path: Path, *, within: Path | None = None) -> None:
    """Remove the file `path` when it is there; with `within`, as open_dir() reaches its folder (a link planted
    in place of the folder: nothing of ours is there, nothing is removed)."""
    try:
        folder = open_dir(path.parent, within)
    except OSError:
        return
    try:
        os.unlink(path.name, dir_fd=folder)
    except FileNotFoundError:
        pass
    finally:
        os.close(folder)


def private_dir(folder: Path, within: Path | None = None) -> Path:
    """`folder`, created when missing, readable by root only. With `within` (a folder another container can
    write): a link planted in its place is removed first, and the mode is set on the folder itself, never on
    where a link points."""
    if within is not None and folder.is_symlink():
        folder.unlink()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = open_dir(folder, within)
    try:
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
    return folder


def chmod_private(path: Path, mode: int = 0o600) -> None:
    """chmod `path` itself: refuses (OSError) when it is a link, so a link swapped in cannot pass the change on."""
    if not hasattr(os, "O_PATH"):  # not Linux
        if path.is_symlink():
            raise OSError(errno.ELOOP, "is a link", str(path))
        os.chmod(path, mode)
        return
    fd = os.open(path, os.O_PATH | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        if stat.S_ISLNK(os.fstat(fd).st_mode):
            raise OSError(errno.ELOOP, "is a link", str(path))
        os.chmod(f"/proc/self/fd/{fd}", mode)  # the very file opened above
    finally:
        os.close(fd)


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
