"""Mailer plugin "smtp": sends through a mailbox you already have, for example your website's
no-reply@your-site.com, with STARTTLS (port 587) or SSL (port 465).

    MAILER=smtp
    MAIL_FROM=Sudoo.sh <no-reply@your-site.com>
    SMTP_HOST=mail.your-site.com
    SMTP_USERNAME=no-reply@your-site.com
    SMTP_PASSWORD=...
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from sudoo.config import Settings

from .base import MailError


class Smtp:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.smtp_host and "@" not in parseaddr(settings.mail_from or settings.smtp_username)[1]:
            raise ValueError("MAIL_FROM (or SMTP_USERNAME) must be an e-mail address when SMTP_HOST is set.")

    @property
    def ready(self) -> bool:
        return bool(self.settings.smtp_host)

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        s = self.settings
        if not self.ready:
            raise MailError("E-mail is not set up on this panel (SMTP_HOST is empty).")
        sender = s.mail_from or s.smtp_username
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = sender, to, subject
        message["Date"], message["Message-ID"] = formatdate(localtime=False), make_msgid(
            domain=sender.split("@")[-1].rstrip(">")
        )
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
        context = ssl.create_default_context()
        try:
            if s.smtp_security == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=context)
            else:
                server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            with server:
                if s.smtp_security == "starttls":
                    server.starttls(context=context)
                if s.smtp_username:
                    server.login(s.smtp_username, s.smtp_password)
                server.send_message(message)
        except (smtplib.SMTPException, OSError) as error:
            raise MailError(f"The e-mail could not be sent ({type(error).__name__}). Try again in a minute.") from error
