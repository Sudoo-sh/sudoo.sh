"""Sending e-mail, through the mailer plugin named by the MAILER setting (base.py)."""

from __future__ import annotations

from functools import cache

from sudoo import plugins
from sudoo.config import settings

from .base import Mailer, MailError

__all__ = ["MailError", "Mailer", "mailer"]


@cache
def mailer() -> Mailer:
    plugin: Mailer = plugins.load(plugins.MAILER_GROUP, settings.mailer, settings)
    return plugin
