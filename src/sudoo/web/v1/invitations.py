"""Invitations (invitations.py): new people are invited by e-mail and choose their own password.

    GET    /api/v1/invitations                  pending and expired invitations; "meta" says whether e-mail is
                                                ready, whether a password may still be set instead
                                                (INVITATION_PASSWORD_FALLBACK) and how long links work
    POST   /api/v1/invitations                  invite: {"email", "role", "first_name", "last_name"} (names
                                                optional); e-mails the link -> 201
    POST   /api/v1/invitations/<id>/resend      a new link (the old one stops working) and a new expiry, e-mailed
    DELETE /api/v1/invitations/<id>             revoke -> 204

    POST   /api/v1/invitation                   {"token"}: who is invited and as what (public)
    POST   /api/v1/invitation/accept            {"token", "password", "first_name", "last_name", "phone"}: create
                                                the account, use the link up and sign in -> 201 (public)

The first four need accounts.manage; only a super admin invites super admins. Every step is in the audit log. Wrong,
used or expired links count against the address like wrong passwords (5 per 15 minutes).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, Response, abort, g, request

from sudoo import accounts, auth, invitations, roles
from sudoo.core import audit
from sudoo.mail import MailError, mailer, send
from sudoo.mail.render import panel_url
from sudoo.storage import Document

from .. import signin
from .. import validation as check
from ..security import client_ip, log, require, start_session
from .responses import PREFIX, created, iso, no_content, ok
from .session import me

blueprint = Blueprint("invitations", __name__)

THROTTLE_KEY = "invitation"


def resource(invitation_id: str, invitation: Document) -> dict[str, Any]:
    role = roles.normalize(str(invitation.get("role")))
    return {
        "id": invitation_id,
        **invitations.resource_fields(invitation),
        "role": role,
        "role_label": accounts.ROLES.get(role, role),
        "invited_at": iso(invitation.get("created")),
        "sent_at": iso(invitation.get("sent")),
        "expires_at": iso(invitation.get("expires")),
        "links": {"self": f"{PREFIX}/invitations/{invitation_id}"},
    }


def _guard(role: str) -> None:
    if role == "super_admin" and g.user["role"] != "super_admin":
        abort(403, "Only a super admin can invite super admins.")


def _invitation(invitation_id: str) -> Document:
    if not invitation_id.replace("-", "").replace("_", "").isalnum() or len(invitation_id) > 40:
        abort(404, "No such invitation.")
    found = invitations.get(invitation_id) or abort(404, "No such invitation.")
    _guard(roles.normalize(str(found.get("role"))))
    return found


def _may_send() -> None:
    if not auth.authenticator().manages_passwords:
        abort(400, "Passwords of this panel are managed elsewhere (AUTH setting): there is nothing to invite to.")
    if not mailer().ready:
        abort(400, "E-mail is not set up, so the invitation cannot be sent. Set it up in Settings, E-mail.")
    wait = invitations.SEND_LIMIT.seconds_to_wait(g.user["name"])
    if wait:
        abort(429, f"Too many invitations sent in the last hour. Try again in {max(1, wait // 60)} minutes.")


def _send(link: invitations.Link, invitation: Document) -> None:
    """E-mail the invitation; raises MailError."""
    base = (panel_url() or request.host_url).rstrip("/")
    inviter = accounts.get(g.user["name"]) or {}
    expires = datetime.fromtimestamp(float(invitation["expires"]), UTC).strftime("%A %d %B %Y, %H:%M UTC")
    send(
        str(invitation["email"]),
        "invitation",
        name=invitation.get("first_name") or invitation["email"],
        inviter=accounts.display_name(g.user["name"], inviter),
        role=accounts.ROLES.get(str(invitation["role"]), str(invitation["role"])),
        expires=expires,
        hours=invitations.lifetime_seconds() // 3600,
        link=f"{base}{invitations.PATH}#{link.token}",
    )
    invitations.SEND_LIMIT.sent(g.user["name"])


@blueprint.get("/invitations")
@require("accounts.manage")
def list_invitations() -> Response:
    found = sorted(invitations.all_invitations().items(), key=lambda item: -float(item[1].get("created", 0)))
    ready = mailer().ready
    meta = {
        "mail_ready": ready,
        "password_allowed": invitations.password_allowed(ready),
        "invitation_hours": invitations.lifetime_seconds() // 3600,
    }
    return ok([resource(key, invitation) for key, invitation in found], meta=meta)


@blueprint.post("/invitations")
@require("accounts.manage")
def invite() -> Response:
    data = check.body()
    role = check.one_of(check.text(data, "role"), roles.ROLES, "Choose a role from the list.")
    _guard(role)
    email = check.text(data, "email", max_length=254).strip()
    names = {key: check.text(data, key, max_length=60).strip() for key in ("first_name", "last_name")}
    _may_send()
    try:
        invitation_id, link = invitations.create(email, role, g.user["name"], **names)
    except ValueError as error:
        abort(400, str(error))
    invitation = invitations.get(invitation_id) or {}
    try:
        _send(link, invitation)
    except MailError as error:
        invitations.revoke(invitation_id)
        log("invitation not sent", detail=f"{email}: {error}", ok=False)
        abort(502, f"The invitation could not be sent: {error}")
    log("invitation sent", detail=f"{email} ({accounts.ROLES[role]})")
    return created(resource(invitation_id, invitation), f"{PREFIX}/invitations/{invitation_id}")


@blueprint.post("/invitations/<invitation_id>/resend")
@require("accounts.manage")
def resend(invitation_id: str) -> Response:
    found = _invitation(invitation_id)
    _may_send()
    if accounts.email_taken(str(found.get("email"))):
        abort(400, "An account already uses this e-mail address. Revoke the invitation.")
    link = invitations.renew(invitation_id)
    invitation = invitations.get(invitation_id) or {}
    try:
        _send(link, invitation)
    except MailError as error:
        log("invitation not sent again", detail=f"{found.get('email')}: {error}", ok=False)
        abort(502, f"The invitation could not be sent: {error}")
    log("invitation sent again", detail=str(found.get("email")))
    return ok(resource(invitation_id, invitation))


@blueprint.delete("/invitations/<invitation_id>")
@require("accounts.manage")
def revoke(invitation_id: str) -> Response:
    found = _invitation(invitation_id)
    invitations.revoke(invitation_id)
    log("invitation revoked", detail=str(found.get("email")))
    return no_content()


# ---------------------------------------------------------------- the invitee (public)


def _valid(data: dict[str, Any]) -> tuple[str, str, Document]:
    """(token, id, invitation) for a current link; a wrong, used or expired one counts as a failed attempt."""
    ip = client_ip()
    wait = accounts.THROTTLE.seconds_to_wait(ip, THROTTLE_KEY)
    if wait:
        abort(429, f"Too many attempts. Try again in {max(1, wait // 60)} minutes.")
    token = check.text(data, "token", max_length=200).strip()
    found = invitations.check(token)
    if found is None:
        accounts.THROTTLE.failed(ip, THROTTLE_KEY)
        audit.record("-", ip, "invitation link refused (wrong, used or expired)", ok=False)
        abort(
            410,
            "This invitation link does not work: it was used, sent again, revoked or it expired. Ask for a new one.",
        )
    return token, found[0], found[1]


def _invitee(invitation_id: str, invitation: Document) -> dict[str, Any]:
    role = roles.normalize(str(invitation.get("role")))
    future = {"role": role}
    return {
        "email": invitation.get("email"),
        "first_name": invitation.get("first_name"),
        "last_name": invitation.get("last_name"),
        "role": role,
        "role_label": accounts.ROLES.get(role, role),
        "invited_by": accounts.display_name(
            str(invitation.get("invited_by")), accounts.get(str(invitation.get("invited_by"))) or {}
        ),
        "expires_at": iso(invitation.get("expires")),
        "second_factor_required": auth.must_use_second_factor(future),
    }


@blueprint.post("/invitation")
def read_invitation() -> Response:
    _token, invitation_id, invitation = _valid(check.body())
    return ok(_invitee(invitation_id, invitation))


@blueprint.post("/invitation/accept")
def accept() -> Response:
    data = check.body()
    token, _invitation_id, invitation = _valid(data)
    ip = client_ip()
    profile = {
        "first_name": check.text(data, "first_name", max_length=60).strip(),
        "last_name": check.text(data, "last_name", max_length=60).strip(),
        "phone": check.text(data, "phone", max_length=30).strip(),
        "email": str(invitation["email"]),
    }
    if not profile["first_name"]:
        abort(400, "Enter your first name.")
    problem = accounts.profile_problem(profile)
    if problem:
        abort(400, problem)
    if accounts.email_taken(profile["email"]):
        abort(409, "An account already uses this e-mail address. Sign in instead.")
    username = accounts.username_for(profile["email"])
    password = check.secret(data, "password")
    names = (profile["first_name"], profile["last_name"])
    problem = accounts.password_problem(password, username, profile["email"], names)
    if problem:
        abort(400, problem)
    used = invitations.use(token)  # atomic: a second acceptance of the same link gets nothing
    if used is None:
        accounts.THROTTLE.failed(ip, THROTTLE_KEY)
        abort(410, "This invitation link does not work any more. Ask for a new one.")
    role = str(used["role"])
    try:
        accounts.create(username, role, password, must_change=False, **profile)
    except ValueError as error:
        abort(400, str(error))
    account = accounts.get(username) or {}
    accounts.record_sign_in(username)
    start_session(username, account["session_version"], restricted=signin.restriction(account))
    audit.record(
        username,
        ip,
        "invitation accepted: account created",
        detail=f"{profile['email']} ({accounts.ROLES[role]}), "
        f"invited by {used.get('invited_by')} {int((time.time() - float(used.get('created', time.time()))) // 3600)} h ago",
    )
    g.user = {"name": username, "role": roles.normalize(role)}
    return ok(me(), status=201)
