"""Passwords, the vault, and the hashed Odoo master password."""

from __future__ import annotations

import pytest

from sudoo import accounts
from sudoo.core import vault
from sudoo.instances import odoo


def test_vault_round_trip_and_labels() -> None:
    token = vault.encrypt("s3cret", "master-password:a")
    assert token.startswith("enc:v1:")
    assert vault.decrypt(token, "master-password:a") == "s3cret"
    with pytest.raises(vault.VaultError):
        vault.decrypt(token, "master-password:b")  # copied to another place: refused


def test_password_hashes_are_peppered_scrypt() -> None:
    stored = accounts.hash_password("green tea at 7 on the balcony")
    assert stored.startswith("scrypt-peppered$32768$8$1$")
    assert accounts.verify_password("green tea at 7 on the balcony", stored)
    assert not accounts.verify_password("green tea at 8 on the balcony", stored)


@pytest.mark.parametrize(
    ("password", "problem"),
    [("short", "at least 12"), ("MyPassword2024!!", "common word"), ("aaaaaaaaaaaaaaaa", "too simple"),
     ("bob-likes-tea-a-lot", "user name"), ("x" * 129, "at most 128")],  # fmt: skip
)
def test_password_rules(password: str, problem: str) -> None:
    assert problem in (accounts.password_problem(password, "bob") or "")


def test_good_password_is_accepted() -> None:
    assert accounts.password_problem("blue-Tiger-71-river", "bob") is None


def test_master_password_hash_is_odoo_compatible_format() -> None:
    hashed = odoo.hash_master_password("pw-123", rounds=1000)
    algorithm, rounds, salt, digest = hashed.lstrip("$").split("$")
    assert (algorithm, rounds) == ("pbkdf2-sha512", "1000")
    assert "+" not in salt + digest and "=" not in salt + digest  # passlib's base64 alphabet
