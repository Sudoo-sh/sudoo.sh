"""The interfaces of sign-in plugins.

Signing in has two parts, each a plugin type:

    Authenticator   "sudoo.auth"             checks a user name and password. Built in: "local" (the panel's own
                                             accounts). Others could ask LDAP, Active Directory or a company's own
                                             system; the account is then created in the panel on first sign-in
                                             with the role the plugin gives.
    SecondFactor    "sudoo.second_factors"   proves it is really that person after the password. Built in: "email"
                                             (a code sent by e-mail) and "totp" (an authenticator app). An SMS
                                             plugin would send the code through an SMS provider the same way.

Plugins get the settings in their constructor, like every plugin (docs/PLUGINS.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

Account = dict[str, Any]


@dataclass(frozen=True, slots=True)
class RequestInfo:
    """Where a sign-in or setup came from, shown in e-mails so people recognise attempts that were not theirs."""

    ip: str = ""
    user_agent: str = ""
    at: float = 0.0

    @property
    def browser(self) -> str:
        """'Chrome on Windows': enough to recognise one's own device."""
        agent = self.user_agent
        name = next(
            (label for key, label in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                      ("Chrome/", "Chrome"), ("Safari/", "Safari")) if key in agent),
            "",
        )  # fmt: skip
        system = next(
            (label for key, label in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                      ("Windows", "Windows"), ("Mac OS X", "macOS"), ("Linux", "Linux")) if key in agent),
            "",
        )  # fmt: skip
        return " on ".join(part for part in (name, system) if part)


@dataclass(frozen=True, slots=True)
class Identity:
    """Who signed in, as an authenticator reports it."""

    username: str
    role: str = "viewer"  # used only when the account is created in the panel on first sign-in (roles.py)
    email: str | None = None


class Authenticator(Protocol):
    manages_passwords: bool
    """True when passwords are set in the panel (Change password, Reset password are offered)."""

    def authenticate(self, username: str, password: str) -> Identity | None:
        """The identity when the password is right, else None. Must take as long for unknown users."""
        ...


class SecondFactor(Protocol):
    name: str  # "email", "totp", ...
    label: str  # shown to people: "E-mail code", "Authenticator app"

    @property
    def available(self) -> bool:
        """True when it can be used on this server (e.g. e-mail is set up)."""
        ...

    def enrolled(self, account: Account) -> bool:
        """True when the account has set this factor up."""
        ...

    def challenge(self, username: str, account: Account, origin: RequestInfo) -> dict[str, Any]:
        """Prepare a sign-in check (send the code); returns what to tell the person, e.g. {"sent_to": "y***@site.com"}."""
        ...

    def verify(self, username: str, account: Account, code: str) -> bool:
        """True when the code is right (each code works once)."""
        ...

    def begin_setup(
        self, username: str, account: Account, details: dict[str, Any], origin: RequestInfo
    ) -> dict[str, Any]:
        """Start setting the factor up (send a code to the new e-mail address, create an app secret); returns what
        the person needs (e.g. {"otpauth_uri", "secret"}). Raises ValueError for bad details."""
        ...

    def finish_setup(self, username: str, account: Account, code: str) -> dict[str, Any] | None:
        """Confirm the setup with a code; returns the account fields to store, or None when the code is wrong."""
        ...

    def remove(self, account: Account) -> dict[str, Any]:
        """The account fields to change so the factor is no longer set up (None values remove a field)."""
        ...
