"""The interface of a mailer plugin: how the panel sends e-mail (sign-in codes, and later notifications).

Built in: "smtp" (your own mailbox, e.g. no-reply@your-site.com) and "none". A plugin for an e-mail service's API
(SendGrid, Amazon SES, ...) implements this Protocol and is registered under "sudoo.mailers".
"""

from __future__ import annotations

from typing import Protocol


class MailError(RuntimeError):
    """The message could not be sent; the text says why, for the person who asked for it."""


class Mailer(Protocol):
    @property
    def ready(self) -> bool:
        """True when it is configured and can send; e-mail codes are offered only then."""
        ...

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        """Send one message, or raise MailError."""
        ...
