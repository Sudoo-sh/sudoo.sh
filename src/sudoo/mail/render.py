"""Turning one of the panel's e-mails into a message: subject, an HTML part and a plain-text part (for mail programs
that show text only).

The text of each e-mail is its built-in text (catalog.py), or the one changed on the E-mail templates page
(overrides.py), in the plain format of textformat.py: placeholders and a little formatting, never template code. The
HTML part puts it in the frame every e-mail shares, templates/layout.html (Jinja, part of the panel's code). To
change that frame, put a layout.html in a folder and set MAIL_TEMPLATES_DIR to it. The accent colour is BRAND_COLOR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader, select_autoescape

from sudoo.config import settings

from . import overrides, textformat
from .catalog import MESSAGES, Message

log = logging.getLogger(__name__)

FOOTER = "--\nSudoo.sh - Your Odoo instances, on your own server."


@dataclass(frozen=True, slots=True)
class Text:
    """What can be changed of an e-mail."""

    subject: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class Composed:
    subject: str
    text: str
    html: str


@cache
def _environment(custom_dir: str) -> Environment:
    loaders: list[Any] = [FileSystemLoader(custom_dir)] if custom_dir and Path(custom_dir).is_dir() else []
    loaders.append(PackageLoader("sudoo.mail", "templates"))
    return Environment(
        loader=ChoiceLoader(loaders),
        autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def panel_url() -> str | None:
    try:
        from sudoo import exposure

        return exposure.address(exposure.PANEL)
    except Exception:  # noqa: BLE001 - an e-mail without the panel's link is still worth sending
        return None


def default_text(message: Message) -> Text:
    return Text(message.subject, message.title, message.body)


def current_text(message: Message) -> Text:
    """The text in use: the changed one when it is still valid, else the built-in one."""
    saved = overrides.get(message.key)
    if not saved:
        return default_text(message)
    text = Text(str(saved.get("subject", "")), str(saved.get("title", "")), str(saved.get("body", "")))
    if textformat.problems(text.subject, text.title, text.body, message.spec, message.required):
        log.warning("mail: the changed text of %s is no longer valid; the built-in text is used", message.key)
        return default_text(message)
    return text


def compose(key: str, values: dict[str, Any], text: Text | None = None) -> Composed:
    """The e-mail `key` with these placeholder values, in its current text (or `text`, for a preview)."""
    message = MESSAGES[key]
    chosen = text or current_text(message)
    link = panel_url()
    known = {"panel_link": link or "", **{k: v for k, v in values.items() if k in message.spec}}
    subject = textformat.fill(chosen.subject, known)[: textformat.MAX_SUBJECT]
    body_html, body_text = textformat.render(chosen.body, known, settings.brand_color)
    context = {
        "subject": subject,
        "kicker": textformat.fill(message.kicker, known),
        "title": textformat.fill(chosen.title, known),
        "preheader": textformat.fill(message.preheader, known)[:120],
        "body": body_html,
        "panel_url": link,
        "panel_host": urlsplit(link).netloc if link else "",
        "contact": settings.admin_contact,
        "accent": settings.brand_color,
    }
    page = _environment(settings.mail_templates_dir).get_template("message.html").render(context)
    return Composed(subject, f"{body_text}\n\n{FOOTER}\n", page)
