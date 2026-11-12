"""Signing in: the authenticator and second-factor plugins named by AUTH and SECOND_FACTORS (base.py).

The rest of the panel calls the functions here; which plugins do the work is decided by configuration.
"""

from __future__ import annotations

from functools import cache

from sudoo import accounts, plugins
from sudoo.config import settings
from sudoo.roles import ADMINISTRATORS

from .base import Account, Authenticator, Identity, RequestInfo, SecondFactor

__all__ = [
    "Account",
    "Authenticator",
    "Identity",
    "RequestInfo",
    "SecondFactor",
    "account_for",
    "authenticator",
    "enrolled",
    "factor",
    "factors",
    "must_use_second_factor",
]


@cache
def authenticator() -> Authenticator:
    plugin: Authenticator = plugins.load(plugins.AUTH_GROUP, settings.auth, settings)
    return plugin


@cache
def _all_factors() -> dict[str, SecondFactor]:
    names = [name.strip() for name in settings.second_factors.split(",") if name.strip()]
    return {name: plugins.load(plugins.SECOND_FACTOR_GROUP, name, settings) for name in names}


def factors() -> dict[str, SecondFactor]:
    """The second factors that can be used now (e-mail only when e-mail is set up)."""
    return {name: plugin for name, plugin in _all_factors().items() if plugin.available}


def factor(name: str) -> SecondFactor | None:
    return factors().get(name)


def enrolled(account: Account) -> list[str]:
    """Names of the usable factors the account has set up."""
    return [name for name, plugin in factors().items() if plugin.enrolled(account)]


def must_use_second_factor(account: Account) -> bool:
    """REQUIRE_SECOND_FACTOR applies to this account (and at least one factor can be used on this server)."""
    rule = settings.require_second_factor
    applies = rule == "everyone" or (rule == "admins" and account.get("role") in ADMINISTRATORS)
    return applies and bool(factors())


def account_for(identity: Identity) -> Account:
    """The panel's account of an identity; created on first sign-in when an external authenticator vouched for it."""
    existing = accounts.get(identity.username)
    if existing is not None:
        return existing
    accounts.create(identity.username, identity.role, password="", email=identity.email, external=True)
    return accounts.get(identity.username) or {}
