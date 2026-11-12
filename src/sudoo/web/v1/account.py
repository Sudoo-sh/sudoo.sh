"""The signed-in person's own account and second factors.

GET    /api/v1/account                          the account, its e-mail and second factors
POST   /api/v1/account/second-factors/<name>    start setting one up: e-mail {"email"}; app {} -> the secret
PUT    /api/v1/account/second-factors/<name>    confirm with {"code"}
DELETE /api/v1/account/second-factors/<name>    remove it; {"password"} confirms -> 204
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g, session

from sudoo import accounts, auth
from sudoo.config import settings
from sudoo.mail import MailError

from .. import validation as check
from ..security import log
from .responses import iso, no_content, ok

blueprint = Blueprint("account", __name__)


def factors_of(account: dict[str, Any]) -> list[dict[str, Any]]:
    added = account.get("second_factors", {})
    return [
        {
            "name": name,
            "label": plugin.label,
            "enrolled": plugin.enrolled(account),
            "added_at": iso(added.get(name, {}).get("added")) if plugin.enrolled(account) else None,
        }
        for name, plugin in auth.factors().items()
    ]


def _account() -> dict[str, Any]:
    return accounts.get(g.user["name"]) or abort(404, "This account no longer exists.")


@blueprint.get("/account")
def read() -> Response:
    account = _account()
    return ok(
        {
            "name": g.user["name"],
            "role": account["role"],
            "role_label": accounts.ROLES[account["role"]],
            "email": account.get("email"),
            "password_managed_here": auth.authenticator().manages_passwords,
            "second_factors": factors_of(account),
            "second_factor_required": auth.must_use_second_factor(account),
            "require_second_factor": settings.require_second_factor,
        }
    )


def _plugin(name: str) -> auth.SecondFactor:
    return auth.factor(name) or abort(404, "This kind of second factor is not offered on this panel.")


@blueprint.post("/account/second-factors/<name>")
def begin(name: str) -> Response:
    plugin, account = _plugin(name), _account()
    try:
        started = plugin.begin_setup(g.user["name"], account, check.body())
    except ValueError as error:
        abort(400, str(error))
    except MailError as error:
        abort(503, str(error))
    return ok(started)


@blueprint.put("/account/second-factors/<name>")
def finish(name: str) -> Response:
    plugin, account = _plugin(name), _account()
    code = check.text(check.body(), "code", max_length=12).replace(" ", "")
    fields = plugin.finish_setup(g.user["name"], account, code) if code.isdigit() else None
    if fields is None:
        abort(400, "The code is not correct, or it has expired.")
    accounts.update(g.user["name"], **fields)
    session.pop("enroll_only", None)
    log(f"second factor set up ({name})")
    return ok(factors_of(_account()))


@blueprint.delete("/account/second-factors/<name>")
def remove(name: str) -> Response:
    plugin, account = _plugin(name), _account()
    if auth.authenticator().authenticate(g.user["name"], check.secret(check.body(), "password")) is None:
        log(f"second factor removal refused ({name}): wrong password", ok=False)
        abort(400, "The password is not correct.")
    if not plugin.enrolled(account):
        abort(404, "This second factor is not set up.")
    if auth.must_use_second_factor(account) and auth.enrolled(account) == [name]:
        abort(400, "A second factor is required for your account: set up another one before removing this one.")
    accounts.update(g.user["name"], **plugin.remove(account))
    session["version"] = accounts.end_sessions(g.user["name"])  # other sessions end; this one continues
    log(f"second factor removed ({name})")
    return no_content()
