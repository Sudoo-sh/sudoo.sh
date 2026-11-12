"""Mailer plugin "none": the panel sends no e-mail, so e-mail codes are not offered."""

from __future__ import annotations

from sudoo.config import Settings

from .base import MailError


class NoMail:
    ready = False

    def __init__(self, settings: Settings) -> None:
        pass

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        raise MailError("This panel sends no e-mail (MAILER=none).")
