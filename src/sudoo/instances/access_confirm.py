"""The one-time code that confirms a change of production access rules (access_rules.py), on top of typing the
instance's name.

A code comes through a channel. Each channel reuses the panel's own second-factor machinery:

    email   a 6-digit code (auth/codes.py: kept as a hash, 10 minutes, 5 tries, one a minute) e-mailed to the
            account's address with the template access_rules_code (or access_rules_code_ar)
    app     the account's authenticator app (the "totp" second factor, auth/totp.py; each code works once)

A channel is anything with the three methods of Channel. An SMS channel later is one more class in CHANNELS
(sending the code through the SMS provider's plugin, checking it with auth/codes.py like "email"); nothing else
changes: the API lists the channels an account can use (available) and the web app offers them.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Protocol

from sudoo.auth import codes
from sudoo.auth.base import Account, RequestInfo

PURPOSE = "access-rules"


class Channel(Protocol):
    name: str

    def available(self, username: str, account: Account) -> bool:
        """Whether this account can get a code this way now."""
        ...

    def send(self, username: str, account: Account, instance: str, origin: RequestInfo) -> dict[str, Any]:
        """Send a code (nothing to send for an app); returns what to tell the person. Raises ValueError (asked again
        too soon) or MailError."""
        ...

    def verify(self, username: str, account: Account, instance: str, code: str) -> bool:
        """Whether `code` is right (it is used up then)."""
        ...


def _key(username: str, instance: str) -> tuple[str, str]:
    return username, f"{PURPOSE}:{instance}"


class EmailChannel:
    name = "email"

    def available(self, username: str, account: Account) -> bool:
        from sudoo.mail import mailer

        return bool(account.get("email")) and mailer().ready

    def send(self, username: str, account: Account, instance: str, origin: RequestInfo) -> dict[str, Any]:
        from sudoo import accounts
        from sudoo.auth.email_code import masked
        from sudoo.mail import send
        from sudoo.mail.catalog import code_block, details_block

        code = codes.new(_key(username, instance))
        minutes = codes.VALID_SECONDS // 60
        when = datetime.fromtimestamp(origin.at or time.time(), UTC).strftime("%A %d %B %Y, %H:%M UTC")
        arabic = accounts.language(account) == "ar"
        send(
            str(account["email"]),
            "access_rules_code_ar" if arabic else "access_rules_code",
            name=accounts.display_name(username, account),
            instance=instance,
            code=code,
            minutes=minutes,
            code_box=code_block(code, minutes),
            request=details_block([("When", when), ("From", origin.ip), ("Device", origin.browser)], "Requested"),
        )
        return {"sent_to": masked(str(account["email"]))}

    def verify(self, username: str, account: Account, instance: str, code: str) -> bool:
        return codes.check(_key(username, instance), code)[0]


class AppChannel:
    name = "app"

    def _factor(self) -> Any:
        from sudoo import auth

        return auth.factor("totp")

    def available(self, username: str, account: Account) -> bool:
        factor = self._factor()
        return factor is not None and bool(factor.enrolled(account))

    def send(self, username: str, account: Account, instance: str, origin: RequestInfo) -> dict[str, Any]:
        return {}  # the code is in the app

    def verify(self, username: str, account: Account, instance: str, code: str) -> bool:
        factor = self._factor()
        return factor is not None and factor.enrolled(account) and bool(factor.verify(username, account, code.strip()))


CHANNELS: dict[str, Channel] = {"email": EmailChannel(), "app": AppChannel()}


def available(username: str, account: Account) -> list[str]:
    """The channels this account can use now, in the order they are offered."""
    found = []
    for name, channel in CHANNELS.items():
        try:
            if channel.available(username, account):
                found.append(name)
        except Exception:  # noqa: BLE001 - a channel that cannot tell is not offered
            continue
    return found
