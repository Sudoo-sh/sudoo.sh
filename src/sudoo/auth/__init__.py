"""Signing in: the authenticator and second-factor plugins named by AUTH and SECOND_FACTORS (base.py).

The rest of the panel calls the functions here; which plugins do the work is decided by configuration.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from werkzeug.exceptions import ServiceUnavailable

from sudoo import accounts, plugin_guard, plugins
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
    """The AUTH plugin; the panel's own accounts (local) when it cannot be loaded, and for each call it fails
    (plugin_guard.py): the administrators can always sign in with their panel password."""
    plugin: Authenticator = plugin_guard.load_safely(
        plugins.AUTH_GROUP, settings.auth, settings, plugin_guard.default_of("auth"), call_fallback=True
    )
    return plugin


class UnavailableFactor:
    """A second factor named in SECOND_FACTORS that cannot be used (its plugin failed, plugin_guard.py). It keeps
    asking for itself: an account that set it up is never let in without a second factor because it broke, and
    REQUIRE_SECOND_FACTOR still applies. The account signs in with another factor it set up, or a recovery code,
    or once an administrator repaired the plugin."""

    available = True

    def __init__(self, name: str) -> None:
        self.name = name
        self.label = f"{name} (not available right now)"

    def _refuse(self) -> ServiceUnavailable:
        return ServiceUnavailable(
            f"The second factor {self.name} is not available right now: its plugin failed. Use another method, "
            "or ask an administrator."
        )

    def enrolled(self, account: Account) -> bool:
        return self.name in (account.get("second_factors") or {})

    def challenge(self, username: str, account: Account, origin: RequestInfo) -> dict[str, Any]:
        raise self._refuse()

    def verify(self, username: str, account: Account, code: str) -> bool:
        raise self._refuse()

    def begin_setup(
        self, username: str, account: Account, details: dict[str, Any], origin: RequestInfo
    ) -> dict[str, Any]:
        raise ValueError(str(self._refuse().description))

    def finish_setup(self, username: str, account: Account, code: str) -> dict[str, Any] | None:
        raise self._refuse()

    def remove(self, account: Account) -> dict[str, Any]:
        return {"second_factors": {k: v for k, v in (account.get("second_factors") or {}).items() if k != self.name}}


@cache
def _all_factors() -> dict[str, SecondFactor]:
    """Each factor of SECOND_FACTORS; one that cannot be loaded (or was switched off after failing) stands as an
    UnavailableFactor and never stops the others."""
    names = [name.strip() for name in settings.second_factors.split(",") if name.strip()]
    found: dict[str, SecondFactor] = {}
    for name in names:
        plugin = plugin_guard.load_safely(plugins.SECOND_FACTOR_GROUP, name, settings, passthrough=(ValueError,))
        found[name] = plugin if plugin is not None else UnavailableFactor(name)
    return found


plugin_guard.on_reset(plugins.AUTH_GROUP, authenticator.cache_clear)
plugin_guard.on_reset(plugins.SECOND_FACTOR_GROUP, _all_factors.cache_clear)


def factors() -> dict[str, SecondFactor]:
    """The second factors that can be used now (e-mail only when e-mail is set up). A factor whose plugin fails to
    answer stands as an UnavailableFactor: never left out silently."""
    found: dict[str, SecondFactor] = {}
    for name, plugin in _all_factors().items():
        try:
            usable = bool(plugin.available)
        except Exception:  # noqa: BLE001 - counted and shown by its guard (plugin_guard.py)
            plugin, usable = UnavailableFactor(name), True
        if usable:
            found[name] = plugin
    return found


def factor(name: str) -> SecondFactor | None:
    return factors().get(name)


def enrolled(account: Account) -> list[str]:
    """Names of the usable factors the account has set up."""
    found = []
    for name, plugin in factors().items():
        try:
            yes = plugin.enrolled(account)
        except Exception:  # noqa: BLE001 - counted by its guard; set up as far as the account record says
            yes = UnavailableFactor(name).enrolled(account)
        if yes:
            found.append(name)
    return found


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
