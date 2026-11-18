"""The "new sign-in" e-mail: after every successful sign-in, the account's owner is told when, from where, from
which device and how, with a link to change the password or sign out everywhere.

SIGN_IN_EMAIL (on by default) decides for everyone; SIGN_IN_EMAIL_OPT_OUT (on by default) lets each person turn it
off on their own account page. Someone may always turn it on for themselves. Nothing is sent when e-mail is not set
up or the account has no address, and the e-mail is sent in the background: a slow or failing mail server never
delays or stops a sign-in.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

from sudoo.config import settings

from .base import Account, RequestInfo

log = logging.getLogger(__name__)

FIELD = "sign_in_email"  # the account's own choice: True, False, or absent (the server's default)

METHODS = {
    "": "Password",
    "totp": "Password and authenticator app",
    "email": "Password and e-mail code",
    "recovery": "Password and recovery code",
}


def own_choice(account: Account) -> bool | None:
    choice = account.get(FIELD)
    return choice if isinstance(choice, bool) else None


def enabled(account: Account) -> bool:
    """Whether this account gets the e-mail: its own choice where allowed, otherwise the server's default."""
    choice = own_choice(account)
    if choice is True:
        return True
    if choice is False and settings.sign_in_email_opt_out:
        return False
    return settings.sign_in_email


def preference(account: Account) -> dict[str, Any]:
    """What the account page shows: whether the e-mail is sent, the person's own choice, the server's default,
    whether it may be turned off, and whether e-mail works at all."""
    from sudoo.mail import mailer

    return {
        "enabled": enabled(account),
        "own_choice": own_choice(account),
        "default": settings.sign_in_email,
        "can_turn_off": settings.sign_in_email_opt_out,
        "mail_ready": mailer().ready,
    }


def _when(at: float) -> str:
    moment = datetime.fromtimestamp(at or time.time(), settings.zone)
    return f"{moment.strftime('%A %d %B %Y, %H:%M')} {settings.timezone}"


def _send(username: str, address: str, origin: RequestInfo, method: str) -> None:
    from sudoo.mail import send
    from sudoo.mail.catalog import details_block
    from sudoo.mail.render import panel_url

    when = _when(origin.at)
    how = METHODS.get(method, "Password")
    panel = panel_url()
    opt_out = (
        "You receive this e-mail at each sign-in. You can turn it off on your account page."
        if settings.sign_in_email_opt_out
        else ""
    )
    send(
        address,
        "sign_in_notice",
        name=username,
        time=when,
        ip=origin.ip,
        device=origin.browser,
        method=how,
        sign_in=details_block([("When", when), ("From", origin.ip), ("Device", origin.browser), ("How", how)]),
        account_link=f"{panel}/account" if panel else "",
        opt_out=opt_out,
    )


def send_later(username: str, account: Account, origin: RequestInfo, method: str = "") -> bool:
    """Send the e-mail in the background when it applies; True when it was started. Never raises."""
    try:
        from sudoo.mail import mailer

        address = str(account.get("email") or "")
        if not address or not enabled(account) or not mailer().ready:
            return False
    except Exception:  # noqa: BLE001 - the e-mail never stops a sign-in
        log.exception("sign-in e-mail: could not decide whether to send it")
        return False

    def run() -> None:
        try:
            _send(username, address, origin, method)
        except Exception as error:  # noqa: BLE001 - a mail server that fails is reported, nothing else
            log.warning("sign-in e-mail to %s's address not sent: %r", username, error)

    threading.Thread(target=run, daemon=True, name="sign-in-email").start()
    return True
