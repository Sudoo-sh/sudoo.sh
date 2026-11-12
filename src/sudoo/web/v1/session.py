"""The browser's session: sign in, sign out, who is signed in, own password.

    GET    /api/v1/contact            who to contact (public: shown on the sign-in page)
    POST   /api/v1/session            sign in: {"username", "password"} -> 201 signed in, or 200 with
                                      "second_factor" when a code is needed (public)
    POST   /api/v1/session/second-factor           the code: {"method", "code"} -> 201 signed in (public)
    POST   /api/v1/session/second-factor/<method>  send a new code (e-mail) (public, between the two steps)
    GET    /api/v1/session            the signed-in account and the CSRF token for changes
    DELETE /api/v1/session            sign out
    PUT    /api/v1/session/password   change own password: {"current", "new"}

Sign-in, throttling and audit are the panel's own (api_session.py). A browser then sends the session cookie with
every call, and X-CSRF-Token with every change.
"""

from __future__ import annotations

from typing import Any, cast

from flask import Blueprint, Response, g, session

from sudoo import accounts
from sudoo.config import settings

from .. import api_session
from .. import validation as check
from .responses import no_content, ok

blueprint = Blueprint("session", __name__)


def _me() -> dict[str, object]:
    return {
        "user": g.user["name"],
        "role": g.user["role"],
        "role_label": accounts.ROLES[g.user["role"]],
        "csrf_token": session.get("csrf"),
        "must_set_up_second_factor": bool(session.get("enroll_only")),
    }


def _signed_in_answer() -> Response:
    account = cast(dict[str, Any], accounts.get(session["user"]))
    g.user = {"name": session["user"], "role": account["role"]}
    return ok(_me(), status=201)


@blueprint.get("/contact")
def contact() -> Response:
    return ok({"sentence": settings.contact_sentence(), **settings.admin_contact})


@blueprint.post("/session")
def sign_in() -> Response:
    result = api_session.password_step(check.body())
    if result.get("signed_in"):
        return _signed_in_answer()
    return ok({"second_factor": result["second_factor"]})


@blueprint.post("/session/second-factor")
def second_factor() -> Response:
    api_session.second_factor_step(check.body())
    return _signed_in_answer()


@blueprint.post("/session/second-factor/<method>")
def send_code(method: str) -> Response:
    return ok(api_session.send_code(method))


@blueprint.get("/session")
def me() -> Response:
    return ok(_me())


@blueprint.delete("/session")
def sign_out() -> Response:
    api_session.logout()
    return no_content()


@blueprint.put("/session/password")
def change_password() -> Response:
    api_session.change_password()
    return no_content()
