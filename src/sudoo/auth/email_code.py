"""Second factor "email": a 6-digit code sent to the account's e-mail address, from the panel's mailbox.

Setting it up confirms the address: a code is sent to it and must be typed back. Offered only when e-mail is set
up (a mailer that is ready, see mail/).
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

from sudoo.config import Settings
from sudoo.mail import mailer, send_template

from . import codes
from .base import Account, RequestInfo

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")


def masked(address: str) -> str:
    """y***@example.com: enough to recognise, not enough to collect."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


class EmailCode:
    name = "email"
    label = "E-mail code"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def available(self) -> bool:
        return mailer().ready

    def enrolled(self, account: Account) -> bool:
        return "email" in account.get("second_factors", {}) and bool(account.get("email"))

    def _send(self, address: str, username: str, code: str, origin: RequestInfo, *, setup: bool) -> None:
        """The Odoo-style HTML e-mail with the code (templates/code.html), and its text version."""
        when = datetime.fromtimestamp(origin.at or time.time(), UTC).strftime("%A %d %B %Y, %H:%M UTC")
        if setup:
            subject = f"{code} confirms your e-mail address for Sudoo.sh"
            kicker, title = "Confirm your address", "Your confirmation code"
            intro = "Type this code in Sudoo.sh to use this address for sign-in codes."
            warning = "Did not ask for this? Someone signed in to your account: change your password in Sudoo.sh."
        else:
            subject = f"{code} is your Sudoo.sh sign-in code"
            kicker, title = "Sign in", "Your sign-in code"
            intro = "Type this code in Sudoo.sh to finish signing in."
            warning = (
                "Did not try to sign in? Someone knows your password. Do not share this code, and change your "
                "password in Sudoo.sh."
            )
        send_template(
            address,
            "code",
            subject,
            preheader=f"{code} - valid for {codes.VALID_SECONDS // 60} minutes",
            kicker=kicker,
            title=title,
            username=username,
            intro=intro,
            code=code,
            minutes=codes.VALID_SECONDS // 60,
            details={"time": when, "ip": origin.ip, "browser": origin.browser},
            warning=warning,
        )

    def challenge(self, username: str, account: Account, origin: RequestInfo) -> dict[str, Any]:
        code = codes.new((username, "sign-in"))
        self._send(account["email"], username, code, origin, setup=False)
        return {"sent_to": masked(account["email"])}

    def verify(self, username: str, account: Account, code: str) -> bool:
        return codes.check((username, "sign-in"), code)[0]

    def begin_setup(
        self, username: str, account: Account, details: dict[str, Any], origin: RequestInfo
    ) -> dict[str, Any]:
        address = str(details.get("email", "")).strip()
        if not EMAIL_RE.match(address):
            raise ValueError("Enter a valid e-mail address.")
        code = codes.new((username, "email-setup"), value=address)
        self._send(address, username, code, origin, setup=True)
        return {"sent_to": masked(address)}

    def finish_setup(self, username: str, account: Account, code: str) -> dict[str, Any] | None:
        right, address = codes.check((username, "email-setup"), code)
        if not right:
            return None
        factors = {**account.get("second_factors", {}), "email": {"added": int(time.time())}}
        return {"email": address, "second_factors": factors}

    def remove(self, account: Account) -> dict[str, Any]:
        factors = {k: v for k, v in account.get("second_factors", {}).items() if k != "email"}
        return {"second_factors": factors}
