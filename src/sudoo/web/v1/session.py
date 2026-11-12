"""The browser's session: first-run setup, sign in, sign out, who is signed in, own password.

    GET    /api/v1/setup                           whether the panel still needs its first account (public)
    POST   /api/v1/setup                           create the first account, a super admin, with the setup code
                                                   printed by the installer, and sign in -> 201 (public)
    GET    /api/v1/contact                         who to contact (public: shown on the sign-in page)
    POST   /api/v1/session                         sign in: {"username", "password"}, where "username" may be the
                                                   e-mail address -> 201 signed in, or 200
                                                   with "second_factor" when a code is needed (public)
    POST   /api/v1/session/second-factor           the code: {"method", "code"} -> 201 signed in (public)
    POST   /api/v1/session/second-factor/<method>  send a new code (e-mail) (public, between the two steps)
    GET    /api/v1/session                         the signed-in account, its permissions and the CSRF token
    DELETE /api/v1/session                         sign out -> 204
    PUT    /api/v1/session/password                change own password: {"current", "new"} -> 204

A browser then sends the session cookie with every call, and X-CSRF-Token with every change.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g, session

from sudoo import accounts, auth, roles
from sudoo.config import settings
from sudoo.core import audit

from .. import signin
from .. import validation as check
from ..security import client_ip, log, start_session
from .responses import no_content, ok

blueprint = Blueprint("session", __name__)


def me() -> dict[str, Any]:
    account = accounts.get(g.user["name"]) or {}
    return {
        "user": g.user["name"],
        "display_name": accounts.display_name(g.user["name"], account),
        **{key: account.get(key) for key in accounts.PROFILE_FIELDS},
        "role": g.user["role"],
        "role_label": accounts.ROLES[g.user["role"]],
        "permissions": sorted(roles.permissions(g.user["role"])),
        "csrf_token": session.get("csrf"),
        "restricted": session.get("restricted"),
        "must_set_up_second_factor": session.get("restricted") == "second_factor",
        "brand_color": settings.brand_color,
    }


def _signed_in_answer() -> Response:
    account = accounts.get(session["user"]) or abort(401, "Your session has ended. Please sign in again.")
    g.user = {"name": session["user"], "role": roles.normalize(account["role"])}
    return ok(me(), status=201)


# ---------------------------------------------------------------- first run


@blueprint.get("/setup")
def setup_state() -> Response:
    return ok({"needed": accounts.setup_needed(), "contact": settings.contact_sentence()})


@blueprint.post("/setup")
def setup() -> Response:
    data = check.body()
    ip = client_ip()
    if accounts.THROTTLE.seconds_to_wait(ip, "setup"):
        abort(429, "Too many wrong setup codes from this address. Try again in 15 minutes.")
    profile = {key: check.text(data, key, max_length=120) for key in accounts.PROFILE_FIELDS}
    if not profile["first_name"] or not profile["email"]:
        abort(400, "Enter at least your first name and your e-mail address.")
    problem = accounts.profile_problem(profile)
    if problem:
        abort(400, problem)
    chosen = check.text(data, "username", max_length=32).lower()
    username = check.username(chosen) if chosen else accounts.username_for(profile["email"])
    try:
        accounts.complete_setup(
            check.text(data, "setup_code", max_length=20), username, check.secret(data, "password"), profile
        )
    except PermissionError as error:
        accounts.THROTTLE.failed(ip, "setup")
        audit.record(username, ip, "first-run setup refused (wrong setup code)", ok=False)
        abort(403, str(error))
    except ValueError as error:
        abort(400, str(error))
    account = accounts.get(username) or {}
    start_session(username, account["session_version"], restricted=signin.restriction(account))
    audit.record(username, ip, "first-run setup: super admin account created")
    return _signed_in_answer()


# ---------------------------------------------------------------- signing in and out


@blueprint.get("/contact")
def contact() -> Response:
    return ok({"sentence": settings.contact_sentence(), **settings.admin_contact})


@blueprint.post("/session")
def sign_in() -> Response:
    result = signin.password_step(check.body())
    if result.get("signed_in"):
        return _signed_in_answer()
    return ok({"second_factor": result["second_factor"]})


@blueprint.post("/session/second-factor")
def second_factor() -> Response:
    signin.second_factor_step(check.body())
    return _signed_in_answer()


@blueprint.post("/session/second-factor/<method>")
def send_code(method: str) -> Response:
    return ok(signin.send_code(method))


@blueprint.get("/session")
def read() -> Response:
    return ok(me())


@blueprint.delete("/session")
def sign_out() -> Response:
    log("signed out")
    session.clear()
    return no_content()


@blueprint.put("/session/password")
def change_password() -> Response:
    data = check.body()
    current, new = check.secret(data, "current"), check.secret(data, "new")
    name = g.user["name"]
    if not auth.authenticator().manages_passwords:
        abort(400, "Passwords of this panel are managed elsewhere (AUTH setting).")
    if not accounts.check_password(name, current):
        log("password change refused (wrong current password)", ok=False)
        abort(400, "The current password is not correct.")
    if new == current:
        abort(400, "Choose a password different from the current one.")
    account = accounts.get(name) or {}
    names = (str(account.get("first_name") or ""), str(account.get("last_name") or ""))
    problem = accounts.password_problem(new, name, account.get("email"), names)
    if problem:
        abort(400, problem)
    session["version"] = accounts.set_password(name, new)  # other sessions of this account end
    session["restricted"] = signin.restriction(accounts.get(name) or {})
    log("changed own password")
    return no_content()
