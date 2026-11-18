"""Sending e-mail, through the mailer plugin named by the MAILER setting (base.py).

Every e-mail the panel sends is listed in catalog.py; its text is the built-in one or the one changed on the
E-mail templates page, and render.py turns it into a message. Call send(to, key, **values) with the values of its
placeholders.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from sudoo import plugins
from sudoo.config import settings

from .base import Mailer, MailError

__all__ = ["MailError", "Mailer", "mailer", "send"]


@cache
def mailer() -> Mailer:
    plugin: Mailer = plugins.load(plugins.MAILER_GROUP, settings.mailer, settings)
    return plugin


def send(to: str, key: str, **values: Any) -> None:
    """Compose the e-mail `key` (catalog.MESSAGES) with these placeholder values and send it; raises MailError."""
    from .render import compose

    message = compose(key, values)
    mailer().send(to, message.subject, message.text, message.html)
