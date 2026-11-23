"""The setup wizard of the first-run page (setup_wizard.py) and the server check.

    GET    /api/v1/setup/server-check      the server's memory, processors, free disk, system, virtualisation, ports
                                           80 and 443, the engine recommended for it and why, the engine in use and
                                           the command that switches it; measured again on each call (at most every
                                           few seconds). Read only. Before the first account: with the setup code
                                           (X-Setup-Code); after: a signed-in session with settings.manage
    GET    /api/v1/setup/draft             the choices so far (resumable): the panel's settings and an install code's
                                           values, then what was saved; secrets only as set or not (setup code)
    PUT    /api/v1/setup/draft             save choices of one or more steps; nothing is applied (setup code)
    POST   /api/v1/setup/test-email        send the test e-mail to {"to"} with the draft's mail server, before it
                                           is saved as the panel's (setup code; at most 5 every 15 minutes)

The final confirm is POST /api/v1/setup (web/v1/session.py): it creates the first super admin and applies the draft.
The setup code comes in the header X-Setup-Code, never in an address; wrong codes count towards the same limit as
POST /api/v1/setup.
"""

from __future__ import annotations

import hmac

from flask import Blueprint, Response, abort, request

from sudoo import accounts, roles, server_check, setup_wizard
from sudoo.core import audit

from .. import validation as check
from ..security import client_ip, current_user
from .responses import ok

blueprint = Blueprint("setup_wizard", __name__)

SETUP_CODE_HEADER = "X-Setup-Code"
TEST_EMAILS = accounts.SignInThrottle()
"""Test e-mails of the setup page, counted per address: at most 5 every 15 minutes."""


def require_setup_code() -> None:
    """The first account does not exist yet, and the request carries the setup code."""
    if not accounts.setup_needed():
        abort(409, "The panel is set up already. Sign in instead.")
    ip = client_ip()
    if accounts.THROTTLE.seconds_to_wait(ip, "setup"):
        abort(429, "Too many wrong setup codes from this address. Try again in 15 minutes.")
    given = request.headers.get(SETUP_CODE_HEADER, "").strip().upper().replace(" ", "")
    expected = accounts.setup_code() or ""
    if not given or not expected or not hmac.compare_digest(given, expected.replace(" ", "")):
        accounts.THROTTLE.failed(ip, "setup")
        abort(403, "The setup code is not correct. Show it again with: docker exec sudoo-panel sudoo.sh setup-code")


def require_setup_code_or_admin() -> None:
    if accounts.setup_needed():
        require_setup_code()
        return
    user = current_user()
    if not user:
        abort(401, "Your session has ended. Please sign in again.")
    if "settings.manage" not in roles.permissions(user["role"]):
        abort(403, "Only super admins see the server check.")


@blueprint.get("/setup/server-check")
def server_check_read() -> Response:
    require_setup_code_or_admin()
    return ok(server_check.check(fresh=request.args.get("fresh") == "1"))


@blueprint.get("/setup/draft")
def draft_read() -> Response:
    require_setup_code()
    return ok(setup_wizard.read())


@blueprint.put("/setup/draft")
def draft_save() -> Response:
    require_setup_code()
    try:
        return ok(setup_wizard.save(check.body()))
    except setup_wizard.DraftError as error:
        abort(400, str(error))


@blueprint.post("/setup/test-email")
def test_email() -> Response:
    from sudoo.mail import MailError
    from sudoo.mail.render import compose
    from sudoo.mail.smtp import Smtp

    require_setup_code()
    ip = client_ip()
    if TEST_EMAILS.seconds_to_wait(ip, "test"):
        abort(429, "Too many test e-mails from this address. Try again later.")
    data = check.body()
    to = check.text(data, "to", max_length=254)
    if not accounts.EMAIL_RE.match(to):
        abort(400, "Enter the e-mail address to send the test to.")
    draft = setup_wizard.read()
    password = data.get("password") if isinstance(data.get("password"), str) and data.get("password") else None
    try:
        draft = {
            **draft,
            **setup_wizard.clean({"mail": {**draft["mail"], **(data.get("mail") or {}), "enabled": True}})[0],
        }
        mailer = Smtp(setup_wizard.mail_settings(draft, password))
    except (setup_wizard.DraftError, ValueError) as error:
        abort(400, str(error))
    server = f"{draft['mail']['host']}:{draft['mail']['port']} ({draft['mail']['security']})"
    TEST_EMAILS.failed(ip, "test")  # every test counts towards the limit
    message = compose("test", {"server": server})
    try:
        mailer.send(to, message.subject, message.text, message.html)
    except MailError as error:
        abort(502, f"{error} For Gmail, the password must be an app password.")
    audit.record("setup", ip, "first-run setup: test e-mail sent", detail=to)
    return ok({"sent_to": to})
