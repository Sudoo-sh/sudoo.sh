"""Turning a template into an e-mail: an HTML part and a plain-text part (for mail programs that show text only).

Templates are Jinja files in mail/templates/: <name>.html extends layout.html, and <name>.txt is the text
version. To change how e-mails look, put files with the same names in a folder and set MAIL_TEMPLATES_DIR to it;
files found there are used instead of the built-in ones (for example only layout.html, to change the frame of every
e-mail). The accent colour is BRAND_COLOR.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader, select_autoescape

from sudoo.config import settings


@cache
def _environment(custom_dir: str) -> Environment:
    loaders: list[Any] = [FileSystemLoader(custom_dir)] if custom_dir and Path(custom_dir).is_dir() else []
    loaders.append(PackageLoader("sudoo.mail", "templates"))
    return Environment(
        loader=ChoiceLoader(loaders),
        autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _panel_url() -> str | None:
    try:
        from sudoo import exposure

        return exposure.address(exposure.PANEL)
    except Exception:  # noqa: BLE001 - an e-mail without the panel's link is still worth sending
        return None


def render(template: str, subject: str, **context: Any) -> tuple[str, str]:
    """(text, html) of `template` with `context` and what every e-mail shows (panel link, contact, colour)."""
    panel_url = _panel_url()
    common = {
        "subject": subject,
        "panel_url": panel_url,
        "panel_host": urlsplit(panel_url).netloc if panel_url else "",
        "contact": settings.admin_contact,
        "accent": settings.brand_color,
        **context,
    }
    environment = _environment(settings.mail_templates_dir)
    html = environment.get_template(f"{template}.html").render(common)
    text = environment.get_template(f"{template}.txt").render(common)
    return text, html
