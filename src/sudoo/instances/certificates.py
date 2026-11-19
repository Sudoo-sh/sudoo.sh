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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cryptography import x509

from sudoo.config import settings
from sudoo.core import vault
from sudoo.instances import acme
from sudoo.instances.custom_domains import (
    CERTS,
    CHALLENGES,
    TOKEN_RE,
    _private_dir,
    cert_file,
    certificate_present,
    certs_dir,
    challenge_answer,
    challenges_dir,
)

present = certificate_present
__all__ = ["CERTS", "CHALLENGES", "cert_file", "certs_dir", "challenge_answer", "challenges_dir", "present"]

ACCOUNTS = "acme-accounts.json"


# ---------------------------------------------------------------- challenges


def publish_challenge(token: str, content: str) -> None:
    if not TOKEN_RE.match(token):
        raise acme.AcmeError("The ACME authority sent a token that is not valid.")
    vault.write_private(challenges_dir() / token, content)


def withdraw_challenge(token: str) -> None:
    if TOKEN_RE.match(token):
        (settings.gateway_dir / CHALLENGES / token).unlink(missing_ok=True)


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


def remove(domain: str) -> None:
    cert_file(domain).unlink(missing_ok=True)


def store(domain: str, key_pem: bytes, chain_pem: str) -> Facts:
    """Keep a new certificate where the gateway reads it; returns its facts."""
    found = facts(chain_pem.encode())
    certs_dir()
    vault.write_private(cert_file(domain), key_pem.decode() + chain_pem)
    return found


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
