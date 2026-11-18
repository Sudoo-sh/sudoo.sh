"""Where the custom domains' keys, certificates and challenge answers are kept.

    <GATEWAY_DIR>/certs/<domain>.pem        the certificate's private key followed by its chain (PEM), mode 600:
                                            the gateway reads it for TLS (SNI). GATEWAY_DIR is mode 700, root only.
    <GATEWAY_DIR>/acme-challenge/<token>    an HTTP-01 key authorization while a check runs (removed after it),
                                            served by the gateway at /.well-known/acme-challenge/<token>
    <MANAGER_DIR>/keys/acme-accounts.json   the ACME account key of each authority, encrypted with the vault key
                                            (core/vault.py), and the account's URL

The certificate keys are not encrypted: the gateway service has no vault key (it holds no secrets but these), and a
TLS server must read its key. They are protected like the vault key itself: files of root only, in a folder of root
only, never in a backup or a log.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cryptography import x509

from sudoo.config import settings
from sudoo.core import vault

from . import acme

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
CERTS = "certs"
CHALLENGES = "acme-challenge"
ACCOUNTS = "acme-accounts.json"


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def certs_dir() -> Path:
    return _private_dir(settings.gateway_dir / CERTS)


def challenges_dir() -> Path:
    return _private_dir(settings.gateway_dir / CHALLENGES)


def cert_file(domain: str) -> Path:
    return settings.gateway_dir / CERTS / f"{domain}.pem"


# ---------------------------------------------------------------- challenges


def publish_challenge(token: str, content: str) -> None:
    if not TOKEN_RE.match(token):
        raise acme.AcmeError("The ACME authority sent a token that is not valid.")
    vault.write_private(challenges_dir() / token, content)


def withdraw_challenge(token: str) -> None:
    if TOKEN_RE.match(token):
        (settings.gateway_dir / CHALLENGES / token).unlink(missing_ok=True)


def challenge_answer(token: str) -> bytes | None:
    """The key authorization for `token`, when a check is running (read by the gateway)."""
    if not TOKEN_RE.match(token):
        return None
    try:
        return (settings.gateway_dir / CHALLENGES / token).read_bytes()[:1024]
    except OSError:
        return None


# ---------------------------------------------------------------- certificates


@dataclass(frozen=True, slots=True)
class Facts:
    not_before: float
    not_after: float
    issuer: str


def facts(chain_pem: bytes) -> Facts:
    """Validity and issuer of the first certificate of a chain."""
    certificate = x509.load_pem_x509_certificates(chain_pem)[0]
    issuer = certificate.issuer.get_attributes_for_oid(x509.oid.NameOID.ORGANIZATION_NAME)
    common = certificate.issuer.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
    name = " ".join(str(part.value) for part in (*issuer[:1], *common[:1]))

    def stamp(value: datetime) -> float:
        return value.timestamp()

    return Facts(stamp(certificate.not_valid_before_utc), stamp(certificate.not_valid_after_utc), name)


def store(domain: str, key_pem: bytes, chain_pem: str) -> Facts:
    """Keep a new certificate where the gateway reads it; returns its facts."""
    found = facts(chain_pem.encode())
    certs_dir()
    vault.write_private(cert_file(domain), key_pem.decode() + chain_pem)
    return found


def remove(domain: str) -> None:
    cert_file(domain).unlink(missing_ok=True)


def present(domain: str) -> bool:
    return cert_file(domain).is_file()


# ---------------------------------------------------------------- ACME accounts


def _accounts_file() -> Path:
    return settings.keys_dir / ACCOUNTS


def account(directory: str) -> tuple[bytes, str | None]:
    """The account key (PEM) and URL for an authority, made on first use (the URL is None until registered)."""
    try:
        saved = json.loads(_accounts_file().read_text())
    except (OSError, ValueError):
        saved = {}
    entry = saved.get(directory) if isinstance(saved, dict) else None
    if isinstance(entry, dict) and isinstance(entry.get("key"), str):
        try:
            return vault.decrypt_bytes(entry["key"], f"acme-account:{directory}"), entry.get("url") or None
        except vault.VaultError:
            pass  # another vault key: start a new account
    key = acme.key_pem(acme.new_key())
    save_account(directory, key, None)
    return key, None


def save_account(directory: str, key_pem: bytes, url: str | None) -> None:
    try:
        saved = json.loads(_accounts_file().read_text())
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    saved[directory] = {"key": vault.encrypt(key_pem, f"acme-account:{directory}"), "url": url}
    _private_dir(settings.keys_dir)
    vault.write_private(_accounts_file(), json.dumps(saved, indent=1))
