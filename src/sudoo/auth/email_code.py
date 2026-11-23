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
from sudoo.mail import mailer, send
from sudoo.mail.catalog import code_block, details_block

from . import codes
from .base import Account, RequestInfo

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")


def masked(address: str) -> str:
    """y***@example.com: enough to recognise, not enough to collect."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


def send_code(address: str, name: str, code: str, origin: RequestInfo, template: str) -> None:
    """E-mail a code (mail/catalog.py: sign_in_code, email_confirmation or email_sign_in_code) with when, from where
    and from which device it was asked for. Raises MailError."""
    when = datetime.fromtimestamp(origin.at or time.time(), UTC).strftime("%A %d %B %Y, %H:%M UTC")
    minutes = codes.VALID_SECONDS // 60
    send(
        address,
        template,
        name=name,
        code=code,
        minutes=minutes,
        code_box=code_block(code, minutes),
        time=when,
        ip=origin.ip,
        device=origin.browser,
        request=details_block([("When", when), ("From", origin.ip), ("Device", origin.browser)], "Requested"),
    )


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
        send_code(address, username, code, origin, "email_confirmation" if setup else "sign_in_code")

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
