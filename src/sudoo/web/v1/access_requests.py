"""Access requests (access_requests.py): people of the allowed e-mail domains ask for an account.

    POST /api/v1/access-requests              {"email", "first_name", "last_name"}: ask (public). The answer is the
                                              same 202 for every address: allowed or not, known or not, throttled or not
    POST /api/v1/access-requests/confirm      {"token"}: the link from the e-mail; the request then waits (public)
    GET  /api/v1/access-requests              the requests, newest first (accounts.manage)
    POST /api/v1/access-requests/<id>/approve {"role"}: invite the person with that role (accounts.manage) -> 200
    POST /api/v1/access-requests/<id>/reject  {"reason"}: reject, with a reason that stays in the list -> 200

Every decision is in the audit log. A request is decided once.
"""

from __future__ import annotations

import contextlib
from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo import access_requests, accounts, invitations, roles
from sudoo.core import audit
from sudoo.mail import MailError, mailer, send
from sudoo.mail.render import panel_url

from .. import validation as check
from ..security import client_ip, log, require
from . import invitations as invitation_api
from .responses import PREFIX, ok

blueprint = Blueprint("access_requests", __name__)
ANSWER = {"received": True}


def resource(request_id: str, item: dict[str, Any]) -> dict[str, Any]:
    from .responses import iso

    return {
        "id": request_id,
        **access_requests.public_fields(item),
        "requested_at": iso(item.get("created")),
        "confirmed_at": iso(item.get("confirmed")),
        "decided_at": iso(item.get("decided")),
        "links": {"self": f"{PREFIX}/access-requests/{request_id}"},
    }


def _base() -> str:
    from flask import request

    return (panel_url() or request.host_url).rstrip("/")


@blueprint.post("/access-requests")
def ask() -> Response:
    data = check.body()
    email = check.text(data, "email", max_length=254)
    first = check.text(data, "first_name", max_length=60)
    last = check.text(data, "last_name", max_length=60)
    made = access_requests.submit(email, first, last, client_ip())
    if made is not None and mailer().ready:
        request_id, secret = made
        import time
        from datetime import UTC, datetime

        expires = datetime.fromtimestamp(time.time() + access_requests.CONFIRM_HOURS * 3600, UTC)
        with contextlib.suppress(MailError):  # the same answer: a failing mail server tells the form nothing
            send(
                email.strip(),
                "access_request_confirm",
                name=first.strip() or email.strip(),
                expires=expires.strftime("%A %d %B %Y, %H:%M UTC"),
                link=f"{_base()}{access_requests.PATH}#{request_id}.{secret}",
            )
        audit.record("access request", client_ip(), "access request received", detail=email.strip().lower()[:254])
    return ok(ANSWER, status=202)


@blueprint.post("/access-requests/confirm")
def confirm() -> Response:
    token = check.text(check.body(), "token", max_length=100)
    ip = client_ip()
    wait = accounts.THROTTLE.seconds_to_wait(ip, "access-request")
    if wait:
        abort(429, f"Too many attempts. Try again in {max(1, wait // 60)} minutes.")
    item = access_requests.confirm(token)
    if item is None:
        accounts.THROTTLE.failed(ip, "access-request")
        abort(400, "This link does not work any more. Ask for access again.")
    audit.record("access request", ip, "access request confirmed", detail=str(item["email"]))
    _tell_administrators(item)
    return ok(ANSWER, status=200)


def _tell_administrators(item: dict[str, Any]) -> None:
    if not mailer().ready:
        return
    who = f"{item.get('first_name', '')} {item.get('last_name', '')}".strip()
    requester = f"{who} ({item['email']})" if who else str(item["email"])
    for account in accounts.all_users().values():
        if (
            account.get("email")
            and not accounts.is_disabled(account)
            and "accounts.manage" in roles.permissions(roles.normalize(str(account["role"])))
        ):
            try:
                send(str(account["email"]), "access_request_admin", requester=requester, link=f"{_base()}/accounts")
            except MailError:
                continue


@blueprint.get("/access-requests")
@require("accounts.manage")
def list_requests() -> Response:
    found = sorted(access_requests.all_requests().items(), key=lambda pair: -float(pair[1].get("created", 0)))
    meta = {"domains": access_requests.domains(), "enabled": access_requests.enabled()}
    return ok([resource(key, item) for key, item in found if item["status"] != "unconfirmed"], meta=meta)


def _pending(request_id: str) -> dict[str, Any]:
    if not request_id.replace("-", "").replace("_", "").isalnum() or len(request_id) > 40:
        abort(404, "No such request.")
    item = access_requests.get(request_id) or abort(404, "No such request.")
    if item["status"] != "pending":
        abort(409, "This request was decided already.")
    return dict(item)


@blueprint.post("/access-requests/<request_id>/approve")
@require("accounts.manage")
def approve(request_id: str) -> Response:
    item = _pending(request_id)
    role = check.one_of(check.text(check.body(), "role"), roles.ROLES, "Choose a role from the list.")
    invitation_api._guard(role)
    invitation_api._may_send()
    if accounts.email_taken(item["email"]) or invitations.pending_for(item["email"]):
        abort(409, "This address has an account or an invitation already.")
    try:
        invitation_id, link = invitations.create(
            item["email"], role, g.user["name"], item.get("first_name", ""), item.get("last_name", "")
        )
    except ValueError as error:
        abort(400, str(error))
    decided = access_requests.decide(request_id, "approved", g.user["name"], role=role)
    if decided is None:
        invitations.revoke(invitation_id)
        abort(409, "This request was decided already.")
    try:
        invitation_api.send_invitation(link, invitations.get(invitation_id) or {})
    except MailError as error:
        invitations.revoke(invitation_id)
        log("access request approved but invitation not sent", ok=False, detail=f"{item['email']}: {error}")
        abort(502, f"The invitation could not be sent: {error}")
    log("access request approved", detail=f"{item['email']} ({accounts.ROLES[role]})")
    return ok(resource(request_id, decided))


@blueprint.post("/access-requests/<request_id>/reject")
@require("accounts.manage")
def reject(request_id: str) -> Response:
    item = _pending(request_id)
    reason = check.text(check.body(), "reason", max_length=300).strip()
    if not reason:
        abort(400, "Say why: the reason stays in the list for the other administrators.")
    decided = access_requests.decide(request_id, "rejected", g.user["name"], reason=reason)
    if decided is None:
        abort(409, "This request was decided already.")
    log("access request rejected", detail=f"{item['email']}: {reason}")
    return ok(resource(request_id, decided))
