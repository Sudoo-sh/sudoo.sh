"""The e-mails a staging or development instance sent, caught by the mail catcher (mailcatcher/): its Mail tab.

    GET    /api/v1/instances/<name>/mail                          caught e-mails, newest first (?q= search, ?limit=,
                                                                  ?offset=); meta: total, catcher, usage, limits
    GET    /api/v1/instances/<name>/mail/<id>                     one e-mail: summary, headers, text, attachments
    GET    /api/v1/instances/<name>/mail/<id>/html                its HTML, for the viewer's sandboxed frame
    GET    /api/v1/instances/<name>/mail/<id>/raw                 the message as received (.eml)
    GET    /api/v1/instances/<name>/mail/<id>/attachments/<n>     one attachment, as a download
    DELETE /api/v1/instances/<name>/mail/<id>                     delete it -> 204
    DELETE /api/v1/instances/<name>/mail                          delete them all -> 204
    POST   /api/v1/instances/<name>/mail/<id>/release             {"to": address}: send a copy there -> 200

Reading needs caught_mail.view (developers and up); deleting and releasing need caught_mail.manage (administrators
and super admins) and are audit-logged. Releasing also needs MAIL_CATCHER_RELEASE. A production instance has no
caught e-mail: 409.

The HTML is served on its own, never inside the panel's page: with a Content-Security-Policy that allows nothing
but inline styles and data: images (no scripts, no remote images that would tell the sender it was opened, no
forms), the CSP sandbox, and only as a frame of the panel itself; the web app shows it in an iframe with the sandbox
attribute. Attachments and the .eml always download (application/octet-stream, Content-Disposition attachment).
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, request

from sudoo import accounts
from sudoo.config import settings
from sudoo.instances import store
from sudoo.mail import MailError
from sudoo.mailcatcher import mailbox
from sudoo.mailcatcher import panel as catcher

from .. import validation as check
from ..security import log, require
from .instances import existing
from .responses import no_content, ok

blueprint = Blueprint("caught_mail", __name__)

HTML_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox"
)


def _instance(name: str) -> str:
    existing(name)
    if not catcher.catches(store.read_env(name)):
        abort(409, "A production instance sends its own e-mail: the mail catcher keeps nothing for it.")
    return name


def _message_id(value: str) -> str:
    if not mailbox.ID_RE.match(value):
        abort(404, "This e-mail does not exist (it may have been deleted, or removed after the retention period).")
    return value


def mail_resource(found: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": found["id"],
        "received_at": found.get("received_at"),
        "subject": found.get("subject", ""),
        "from": found.get("from", ""),
        "to": found.get("to", []),
        "cc": found.get("cc", []),
        "mail_from": found.get("mail_from", ""),
        "rcpt_to": found.get("rcpt_to", []),
        "date": found.get("date", ""),
        "size": found.get("size", 0),
        "has_html": bool(found.get("has_html")),
        "attachments": found.get("attachments", 0),
        "snippet": found.get("snippet", ""),
    }


def catcher_state() -> dict[str, Any]:
    current = catcher.state()
    return {"running": current["running"], "exists": current["exists"]}


@blueprint.get("/instances/<name>/mail")
@require("caught_mail.view")
def list_mail(name: str) -> Response:
    _instance(name)
    query = check.text(request.args, "q", max_length=200)
    limit = check.whole_number(request.args.get("limit", "50"), "limit", minimum=1, maximum=200)
    offset = check.whole_number(request.args.get("offset", "0"), "offset", maximum=10**6)
    found = mailbox.search(name, query)
    chosen = found[offset : offset + limit]
    meta = {
        "total": len(found),
        "count": len(chosen),
        "limit": limit,
        "offset": offset,
        "catcher": catcher_state(),
        "usage": mailbox.usage(name),
        "limits": {
            "retention_days": settings.mail_catcher_retention_days,
            "max_messages": settings.mail_catcher_max_messages,
            "max_mb": settings.mail_catcher_max_mb,
            "max_message_mb": settings.mail_catcher_max_message_mb,
        },
        "release": bool(settings.mail_catcher_release),
    }
    return ok([mail_resource(item) for item in chosen], meta=meta)


@blueprint.get("/instances/<name>/mail/<message_id>")
@require("caught_mail.view")
def read_mail(name: str, message_id: str) -> Response:
    _instance(name)
    found = mailbox.detail(name, _message_id(message_id))
    if found is None:
        _message_id("")
    assert found is not None
    return ok(
        mail_resource(found)
        | {"headers": found["headers"], "text": found["text"], "attachment_list": found["attachments"]}
    )


@blueprint.get("/instances/<name>/mail/<message_id>/html")
@require("caught_mail.view")
def mail_html(name: str, message_id: str) -> Response:
    _instance(name)
    body = mailbox.html(name, _message_id(message_id))
    if body is None:
        abort(404, "This e-mail has no HTML part.")
    page = (
        '<!doctype html><meta charset="utf-8"><base target="_blank">'
        '<meta name="referrer" content="no-referrer">' + body
    )
    response = Response(page, mimetype="text/html")
    response.headers["X-Sudoo-Frame"] = "caught-mail"  # security._add_headers keeps this answer's own policy
    return response


def _download(data: bytes, filename: str) -> Response:
    response = Response(data, mimetype="application/octet-stream")
    safe = "".join(char if char.isalnum() or char in "._- " else "_" for char in filename)[:150] or "download"
    response.headers["Content-Disposition"] = f'attachment; filename="{safe}"'
    return response


@blueprint.get("/instances/<name>/mail/<message_id>/raw")
@require("caught_mail.view")
def mail_raw(name: str, message_id: str) -> Response:
    _instance(name)
    data = mailbox.raw(name, _message_id(message_id))
    if data is None:
        _message_id("")
    assert data is not None
    return _download(data, f"{name}-{message_id}.eml")


@blueprint.get("/instances/<name>/mail/<message_id>/attachments/<int:index>")
@require("caught_mail.view")
def mail_attachment(name: str, message_id: str, index: int) -> Response:
    _instance(name)
    found = mailbox.attachment(name, _message_id(message_id), index)
    if found is None:
        abort(404, "This attachment does not exist.")
    filename, _, data = found
    return _download(data, filename)


@blueprint.delete("/instances/<name>/mail/<message_id>")
@require("caught_mail.manage")
def delete_mail(name: str, message_id: str) -> Response:
    _instance(name)
    found = mailbox.get(name, _message_id(message_id))
    if found is None or not mailbox.delete(name, message_id):
        _message_id("")
    log("caught e-mail deleted", instance=name, detail=f"{message_id}: {str(found and found.get('subject'))[:120]}")
    return no_content()


@blueprint.delete("/instances/<name>/mail")
@require("caught_mail.manage")
def delete_all_mail(name: str) -> Response:
    _instance(name)
    count = mailbox.delete_all(name)
    log("caught e-mails deleted", instance=name, detail=f"{count} e-mails")
    return no_content()


@blueprint.post("/instances/<name>/mail/<message_id>/release")
@require("caught_mail.manage")
def release_mail(name: str, message_id: str) -> Response:
    _instance(name)
    to = check.text(check.body(), "to", max_length=254).strip()
    if not accounts.EMAIL_RE.match(to):
        abort(400, "Type one e-mail address to send the copy to.")
    if not settings.mail_catcher_release:
        abort(403, "Releasing caught e-mails is switched off on this panel (Settings, E-mail).")
    found = mailbox.get(name, _message_id(message_id))
    if found is None:
        _message_id("")
    assert found is not None
    detail = f"{message_id} to {to}: {str(found.get('subject', ''))[:120]}"
    try:
        catcher.release(name, message_id, to)
    except LookupError:
        _message_id("")
    except (catcher.ReleaseError, MailError) as error:
        log("caught e-mail not released", instance=name, ok=False, detail=f"{detail} ({error})")
        abort(503, str(error))
    log("caught e-mail released", instance=name, detail=detail)
    return ok({"released": True, "to": to, "id": message_id})
