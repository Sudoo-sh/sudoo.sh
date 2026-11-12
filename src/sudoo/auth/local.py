"""Authenticator plugin "local": the panel's own accounts, with scrypt-hashed passwords (accounts.py)."""

from __future__ import annotations

from sudoo import accounts
from sudoo.config import Settings

from .base import Identity


class LocalAccounts:
    manages_passwords = True

    def __init__(self, settings: Settings) -> None:
        pass

    def authenticate(self, username: str, password: str) -> Identity | None:
        account = accounts.check_password(username, password)
        return Identity(username, account["role"], account.get("email")) if account else None
