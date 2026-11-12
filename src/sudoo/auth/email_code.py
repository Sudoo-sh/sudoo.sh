"""Second factor "email": a 6-digit code sent to the account's e-mail address, from the panel's mailbox.

Setting it up confirms the address: a code is sent to it and must be typed back. Offered only when e-mail is set
up (a mailer that is ready, see mail/).
"""

from __future__ import annotations

import re
import time
from typing import Any

from sudoo.config import Settings
from sudoo.mail import mailer

from . import codes
from .base import Account

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

    def _send(self, address: str, code: str, why: str) -> None:
        mailer().send(
            address,
            f"Your Sudoo.sh code: {code}",
            f"Your code is {code}\n\n{why} It is valid for 10 minutes and can be used once.\n\n"
            "If you did not ask for it, someone knows your password: change it in the panel.\n",
        )

    def challenge(self, username: str, account: Account) -> dict[str, Any]:
        code = codes.new((username, "sign-in"))
        self._send(account["email"], code, "Type it in the panel to finish signing in.")
        return {"sent_to": masked(account["email"])}

    def verify(self, username: str, account: Account, code: str) -> bool:
        return codes.check((username, "sign-in"), code)[0]

    def begin_setup(self, username: str, account: Account, details: dict[str, Any]) -> dict[str, Any]:
        address = str(details.get("email", "")).strip()
        if not EMAIL_RE.match(address):
            raise ValueError("Enter a valid e-mail address.")
        code = codes.new((username, "email-setup"), value=address)
        self._send(address, code, "Type it in the panel to confirm this address for signing in.")
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
