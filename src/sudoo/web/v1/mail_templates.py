"""The texts of the e-mails the panel sends (permission mail.manage, super admins).

    GET    /api/v1/mail-templates                 every e-mail: its current and built-in text, its placeholders
    GET    /api/v1/mail-templates/<key>           one e-mail
    PUT    /api/v1/mail-templates/<key>           change it: {"subject", "title", "body"}; refused (400, every
                                                  problem listed) with an unknown placeholder or a link that is not
                                                  allowed
    DELETE /api/v1/mail-templates/<key>           back to the built-in text -> 204
    POST   /api/v1/mail-templates/<key>/preview   {"subject", "title", "body"} (optional: the current text) -> the
                                                  e-mail with sample values: {"subject", "html", "text", "problems"}
    POST   /api/v1/mail-templates/<key>/test      send that preview to your own address (unsaved text included)

Texts use the plain format of mail/textformat.py: placeholders, paragraphs, bold, lists, notes and links, never
template code, and every value is escaped. Changes and resets are in the audit log.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo import accounts, mail
from sudoo.mail import MailError, overrides, textformat
from sudoo.mail.catalog import MESSAGES, Message
from sudoo.mail.render import Text, compose, current_text, default_text

from .. import validation as check
from ..security import log, require
from .responses import iso, no_content, ok

blueprint = Blueprint("mail_templates", __name__)


def _message(key: str) -> Message:
    return MESSAGES.get(key) or abort(404, "No e-mail with this name.")


def _text_dict(text: Text) -> dict[str, str]:
    return {"subject": text.subject, "title": text.title, "body": text.body}


def resource(message: Message) -> dict[str, Any]:
    saved = overrides.get(message.key)
    return {
        "key": message.key,
        "name": message.name,
        "description": message.description,
        "recipients": message.recipients,
        **_text_dict(current_text(message)),
        "default": _text_dict(default_text(message)),
        "customized": saved is not None,
        "changed_at": iso(saved.get("changed")) if saved else None,
        "changed_by": saved.get("changed_by") if saved else None,
        "placeholders": [
            {"name": p.name, "description": p.description, "kind": p.kind}
            for p in sorted(message.spec.values(), key=lambda p: p.name == "panel_link")
        ],
        "required": list(message.required),
    }


def _draft(message: Message, data: dict[str, Any], *, partial: bool) -> Text:
    """The text sent in the body; with `partial`, missing parts come from the current text."""
    current = current_text(message)
    parts = {}
    for key in ("subject", "title", "body"):
        value = data.get(key)
        if value is None and partial:
            value = getattr(current, key)
        if not isinstance(value, str):
            abort(400, f"The field '{key}' must be text.")
        parts[key] = value.replace("\r\n", "\n").strip() if key == "body" else value.strip()
    return Text(**parts)


def _problems(message: Message, text: Text) -> list[str]:
    return textformat.problems(text.subject, text.title, text.body, message.spec, message.required)


@blueprint.get("/mail-templates")
@require("mail.manage")
def list_templates() -> Response:
    return ok([resource(message) for message in MESSAGES.values()], meta={"mail_ready": mail.mailer().ready})


@blueprint.get("/mail-templates/<key>")
@require("mail.manage")
def read(key: str) -> Response:
    return ok(resource(_message(key)))


@blueprint.put("/mail-templates/<key>")
@require("mail.manage")
def change(key: str) -> Response:
    message = _message(key)
    text = _draft(message, check.body(), partial=False)
    found = _problems(message, text)
    if found:
        abort(400, " ".join(found))
    if text == default_text(message):
        overrides.reset(key)  # the same as the built-in text: nothing to keep
    else:
        overrides.save(key, text.subject, text.title, text.body, g.user["name"])
    log("e-mail template changed", detail=message.name)
    return ok(resource(message))


@blueprint.delete("/mail-templates/<key>")
@require("mail.manage")
def reset(key: str) -> Response:
    message = _message(key)
    if overrides.reset(key):
        log("e-mail template reset to default", detail=message.name)
    return no_content()


@blueprint.post("/mail-templates/<key>/preview")
@require("mail.manage")
def preview(key: str) -> Response:
    message = _message(key)
    text = _draft(message, check.body(), partial=True)
    found = _problems(message, text)
    if found:
        return ok({"subject": None, "html": None, "text": None, "problems": found})
    composed = compose(key, message.samples(), text)
    return ok({"subject": composed.subject, "html": composed.html, "text": composed.text, "problems": []})


@blueprint.post("/mail-templates/<key>/test")
@require("mail.manage")
def send_test(key: str) -> Response:
    message = _message(key)
    text = _draft(message, check.body(), partial=True)
    found = _problems(message, text)
    if found:
        abort(400, " ".join(found))
    address = str((accounts.get(g.user["name"]) or {}).get("email") or "")
    if not address:
        abort(400, "Your account has no e-mail address: add one on your account page first.")
    if not mail.mailer().ready:
        abort(400, "E-mail is not set up yet: fill in the mail server (SMTP) settings first.")
    composed = compose(key, message.samples(), text)
    try:
        mail.mailer().send(address, f"[Test] {composed.subject}", composed.text, composed.html)
    except MailError as error:
        abort(502, str(error))
    log("test e-mail sent", detail=f"{message.name} to {address}")
    return ok({"sent_to": address})
