"""Sending e-mail, through the mailer plugin named by the MAILER setting (base.py), from templates (render.py)."""

from __future__ import annotations

from functools import cache
from typing import Any

from sudoo import plugins
from sudoo.config import settings

from .base import Mailer, MailError

__all__ = ["MailError", "Mailer", "mailer", "send_template"]


@cache
def mailer() -> Mailer:
    plugin: Mailer = plugins.load(plugins.MAILER_GROUP, settings.mailer, settings)
    return plugin


def send_template(to: str, template: str, subject: str, **context: Any) -> None:
    """Render `template` (HTML and text, see render.py) and send it; raises MailError."""
    from .render import render

    text, html = render(template, subject, **context)
    mailer().send(to, subject, text, html)
