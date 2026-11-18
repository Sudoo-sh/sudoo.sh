"""The Ed25519 key that signs the manifest of every off-site copy.

The private key is made the first time off-site copies are switched on, and kept like the panel's other secrets
that must be read back: encrypted with the vault key (core/vault.py, label "offsite-signing-key") in
<ODOO_ROOT>/manager/offsite/signing.key, mode 600. The public key is in signing.pub.pem next to it, in the PEM form
OpenSSL reads, so a copy can also be checked away from the panel:

    openssl pkeyutl -verify -pubin -inkey offsite-signing.pub.pem -rawin -in manifest.json -sigfile manifest.json.sig

A signature is the raw 64 bytes of Ed25519 over the manifest's exact bytes. The fingerprint is "SHA256:" and the
hex SHA-256 of the public key in DER, as `openssl pkey -pubin -in KEY -outform DER | sha256sum` prints it (the same
as the cluster's off-site nights).

What this protects (docs/SECURITY.md, "Off-site copies"): someone who can write to the bucket but not to this server
cannot change a copy, swap it for another or pass one off under another instance or date without the check failing.
It does not protect against someone with root on this server: they can read the vault key and sign anything. Keep
a copy of the public key off the server: after the server is lost, it is what proves the copies are yours.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from sudoo.config import settings
from sudoo.core import vault

LABEL = "offsite-signing-key"


class PublicKeyError(ValueError):
    """A public key that is not an Ed25519 public key in PEM form."""


def _folder() -> Path:
    folder = settings.manager_dir / "offsite"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def _private_path() -> Path:
    return settings.manager_dir / "offsite" / "signing.key"


def _public_path() -> Path:
    return settings.manager_dir / "offsite" / "signing.pub.pem"


def exists() -> bool:
    return _private_path().exists()


def ensure() -> bool:
    """Make the key pair when there is none; True when it was made now."""
    if exists():
        return False
    _folder()
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    try:
        fd = os.open(_private_path(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False  # made at the same moment by another thread
    with os.fdopen(fd, "w") as fh:
        fh.write(vault.encrypt(raw, LABEL))
    vault.write_private(_public_path(), public_pem_of(key.public_key()))
    return True


def _private() -> Ed25519PrivateKey:
    with open(_private_path()) as fh:
        raw = vault.decrypt_bytes(fh.read().strip(), LABEL)
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_pem_of(key: Ed25519PublicKey) -> str:
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def public_pem() -> str | None:
    """The panel's public key (PEM), or None before off-site copies were first switched on."""
    if not exists():
        return None
    try:
        with open(_public_path()) as fh:
            return fh.read()
    except FileNotFoundError:  # rebuilt from the private key
        text = public_pem_of(_private().public_key())
        vault.write_private(_public_path(), text)
        return text


def load_public(pem: str) -> Ed25519PublicKey:
    """The public key in `pem`; PublicKeyError when it is not an Ed25519 public key."""
    try:
        key = serialization.load_pem_public_key(pem.strip().encode())
    except (ValueError, TypeError):
        raise PublicKeyError("This is not a public key in PEM form (-----BEGIN PUBLIC KEY-----).") from None
    if not isinstance(key, Ed25519PublicKey):
        raise PublicKeyError("This public key is not an Ed25519 key.")
    return key


def fingerprint(pem: str) -> str:
    """SHA256:<hex of the DER public key>."""
    der = load_public(pem).public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return "SHA256:" + hashlib.sha256(der).hexdigest()


def sign(data: bytes) -> bytes:
    """The raw Ed25519 signature (64 bytes) of `data` with the panel's key (made first if needed)."""
    ensure()
    return _private().sign(data)


def verify(data: bytes, signature: bytes, pem: str) -> bool:
    """Whether `signature` is the signature of `data` by the owner of the public key `pem`."""
    try:
        load_public(pem).verify(signature, data)
    except InvalidSignature:
        return False
    return True
