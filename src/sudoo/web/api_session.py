"""Sign-in, sign-out and the signed-in user's own account.

Signing in takes one or two steps (password_step, then second_factor_step):
    1. user name and password, checked by the authenticator plugin (auth/). Without a second factor this signs in.
    2. when the account has a second factor: a code (e-mail, authenticator app). Between the two steps the
       session only holds "pending": who passed step 1 and when (5 minutes at most); it opens nothing.
When REQUIRE_SECOND_FACTOR applies to an account that has none yet, the session can only set one up.
"""

from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, Response, abort, g, jsonify, session

from sudoo import accounts, auth
from sudoo.config import settings
from sudoo.core import audit
from sudoo.mail import MailError

from . import validation as check
from .security import client_ip, log, start_session

PENDING_SECONDS = 300

blueprint = Blueprint("session", __name__, url_prefix="/api")


@blueprint.get("/contact")
def contact() -> Response:
    """Who to contact; shown on the sign-in page."""
    return jsonify(sentence=settings.contact_sentence())


def _refuse_if_throttled(ip: str, name: str) -> None:
    wait = accounts.THROTTLE.seconds_to_wait(ip, name)
    if wait:
        audit.record(name, ip, "sign-in refused (too many attempts)", ok=False)
        abort(429, f"Too many failed attempts. Try again in {max(1, wait // 60)} minutes.")


def _signed_in(name: str, account: dict[str, Any], ip: str, enroll_only: bool = False) -> None:
    accounts.THROTTLE.succeeded(ip, name)
    accounts.record_sign_in(name)
    start_session(name, account["session_version"], enroll_only=enroll_only)
    audit.record(name, ip, "signed in" + (" (must set up a second factor)" if enroll_only else ""))


def password_step(data: dict[str, Any]) -> dict[str, Any]:
    """Step 1. Returns {"signed_in": True}, or {"second_factor": {"methods": [...], ...}} when a code is needed."""
    name = check.text(data, "username", max_length=32).lower()
    password = check.secret(data, "password")
    ip = client_ip()
    _refuse_if_throttled(ip, name)
    identity = auth.authenticator().authenticate(name, password) if accounts.USERNAME_RE.match(name) else None
    if identity is None:
        accounts.THROTTLE.failed(ip, name)
        audit.record(name, ip, "sign-in failed", ok=False)
        abort(401, "Wrong user name or password.")
    account = auth.account_for(identity)
    methods = auth.enrolled(account)
    if not methods:
        _signed_in(name, account, ip, enroll_only=auth.must_use_second_factor(account))
        return {"signed_in": True}
    session.clear()
    session["pending"] = {"user": name, "version": account["session_version"], "at": time.time()}
    challenge: dict[str, Any] = {
        "methods": [{"name": m, "label": auth.factors()[m].label} for m in methods],
    }
    if methods[0] == "email":  # nothing to open: send the code right away
        challenge["sent_to"] = _challenge(name, account, "email")["sent_to"]
    audit.record(name, ip, "password accepted, waiting for the second factor")
    return {"second_factor": challenge}


def _pending() -> tuple[str, dict[str, Any]]:
    pending = session.get("pending") or {}
    account = accounts.get(pending.get("user", "")) if pending else None
    if (
        account is None
        or time.time() - pending.get("at", 0) > PENDING_SECONDS
        or pending.get("version") != account["session_version"]
    ):
        session.clear()
        abort(401, "Signing in took too long. Enter your user name and password again.")
    return pending["user"], account


def _challenge(name: str, account: dict[str, Any], method: str) -> dict[str, Any]:
    plugin = auth.factor(method)
    if plugin is None or not plugin.enrolled(account):
        abort(400, "This account does not use this kind of code.")
    try:
        return plugin.challenge(name, account)
    except ValueError as error:  # asked again too soon
        abort(429, str(error))
    except MailError as error:
        abort(503, str(error))


def send_code(method: str) -> dict[str, Any]:
    """Between the steps: (re)send the code of `method` (e-mail)."""
    name, account = _pending()
    return _challenge(name, account, method)


def second_factor_step(data: dict[str, Any]) -> None:
    """Step 2: the code. Wrong codes count like wrong passwords (5 per 15 minutes)."""
    name, account = _pending()
    ip = client_ip()
    _refuse_if_throttled(ip, name)
    method = check.text(data, "method", max_length=20)
    code = check.text(data, "code", max_length=12).replace(" ", "")
    plugin = auth.factor(method)
    if plugin is None or not plugin.enrolled(account) or not code.isdigit() or not plugin.verify(name, account, code):
        accounts.THROTTLE.failed(ip, name)
        audit.record(name, ip, f"second factor refused ({method})", ok=False)
        abort(401, "The code is not correct, or it has expired.")
    _signed_in(name, account, ip)


@blueprint.post("/login")
def login() -> Response:
    """The previous pages' sign-in: password only. Accounts that need a second factor use the new panel."""
    result = password_step(check.body())
    if not result.get("signed_in") or session.get("enroll_only"):
        session.clear()
        abort(403, "This account signs in with a second factor: use the new panel's sign-in page.")
    return jsonify(ok=True)


@blueprint.post("/logout")
def logout() -> Response:
    log("signed out")
    session.clear()
    return jsonify(ok=True)


@blueprint.get("/me")
def me() -> Response:
    return jsonify(
        user=g.user["name"],
        role=g.user["role"],
        role_label=accounts.ROLES[g.user["role"]],
        csrf=session["csrf"],
        contact=settings.admin_contact,
        idle_hours=settings.idle_hours,
        poll_minutes=settings.poll_minutes,
    )


@blueprint.post("/account/password")
def change_password() -> Response:
    data = check.body()
    current, new = check.secret(data, "current"), check.secret(data, "new")
    name = g.user["name"]
    if not auth.authenticator().manages_passwords:
        abort(400, "Passwords of this panel are managed elsewhere (AUTH setting).")
    if not accounts.check_password(name, current):
        log("password change refused (wrong current password)", ok=False)
        abort(400, "The current password is not correct.")
    problem = accounts.password_problem(new, name)
    if problem:
        abort(400, problem)
    session["version"] = accounts.set_password(name, new)  # other sessions of this account end
    log("changed own password")
    return jsonify(ok=True)
