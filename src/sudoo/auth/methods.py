"""How each account may sign in (its first factor): "password", "email_code" or "github", any combination.

    password     the e-mail address (or user name) and the password, checked by the authenticator plugin (AUTH)
    email_code   no password: a 6-digit code sent to the account's e-mail address (email_sign_in.py)
    github       Continue with GitHub, for an account linked to a GitHub account (github.py)

An administrator chooses them per account (the field "sign_in_methods"); an account without a choice uses the
server's default: SIGN_IN_METHODS for staff accounts, CLIENT_SIGN_IN_METHODS for client accounts (an account whose
"kind" is "client"). A method is offered only where the server can use it: e-mail codes need e-mail to be set up
(and EMAIL_CODE_SIGN_IN), GitHub needs GITHUB_SIGN_IN and the panel's GitHub App. The second factor rules
(REQUIRE_SECOND_FACTOR) apply after any of them.

Break glass: at least one super admin always keeps a password it can sign in with, so the panel stays reachable when
e-mail or GitHub fails. A change that would leave no such super admin (methods, role, deletion) is refused
(break_glass_problem).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sudoo.config import settings

from .base import Account

METHODS = ("password", "email_code", "github")
FIELD = "sign_in_methods"
CLIENT_KIND = "client"
LABELS = {"password": "Password", "email_code": "E-mail code", "github": "GitHub"}


def _parse(text: str) -> list[str]:
    return [part for part in (p.strip().lower() for p in text.split(",")) if part in METHODS]


def defaults(kind: str = "staff") -> list[str]:
    """The server's default methods for a new account of this kind ("staff" or "client")."""
    return _parse(settings.client_sign_in_methods if kind == CLIENT_KIND else settings.sign_in_methods)


def own_choice(account: Mapping[str, Any]) -> list[str] | None:
    """The methods an administrator chose for the account, or None (the server's default applies)."""
    chosen = account.get(FIELD)
    if not isinstance(chosen, list):
        return None
    return [method for method in METHODS if method in chosen] or None


def of(account: Mapping[str, Any]) -> list[str]:
    """The methods the account may use: its own choice, else the default for its kind."""
    return own_choice(account) or defaults(str(account.get("kind") or "staff"))


def allows(account: Mapping[str, Any], method: str) -> bool:
    return method in of(account)


def checked(raw: Any) -> list[str]:
    """A list of methods sent by an administrator, in the usual order. Raises ValueError."""
    if not isinstance(raw, list) or not raw or any(m not in METHODS for m in raw):
        raise ValueError('sign_in_methods: a list of one or more of "password", "email_code" and "github".')
    return [method for method in METHODS if method in raw]


def email_code_offered() -> bool:
    """Whether the server offers sign-in codes by e-mail now (EMAIL_CODE_SIGN_IN and e-mail set up)."""
    from sudoo.mail import mailer

    return bool(settings.email_code_sign_in) and mailer().ready


def github_offered() -> bool:
    from . import github

    return github.enabled()


def offered() -> dict[str, bool]:
    """Which methods the sign-in page shows."""
    return {"password": True, "email_code": email_code_offered(), "github": github_offered()}


def usable(account: Account) -> list[str]:
    """The account's methods that work now: a password it has, an e-mail address with e-mail codes offered, a linked
    GitHub account with GitHub sign-in offered."""
    found = []
    for method in of(account):
        if (
            method == "password"
            and (account.get("hash") or account.get("external"))
            or method == "email_code"
            and account.get("email")
            and email_code_offered()
            or method == "github"
            and github_offered()
        ):
            found.append(method)
    return found


def has_break_glass(account: Mapping[str, Any]) -> bool:
    """A super admin that can sign in with a password."""
    return account.get("role") == "super_admin" and bool(account.get("hash")) and allows(account, "password")


def break_glass_problem(before: Mapping[str, Mapping[str, Any]], after: Mapping[str, Mapping[str, Any]]) -> str | None:
    """Why a change of the accounts (`before` -> `after`, every account) would leave the panel without a super admin
    that signs in with a password, or None. Only a change that takes the last one away is refused; panels whose
    passwords are managed elsewhere (an external AUTH) are not checked."""
    from . import authenticator

    if not authenticator().manages_passwords:
        return None
    if any(has_break_glass(user) for user in after.values()):
        return None
    if not any(has_break_glass(user) for user in before.values()):
        return None
    return (
        "At least one super admin must keep signing in with a password, so the panel stays reachable when e-mail "
        "or GitHub does not work."
    )
