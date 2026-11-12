"""The signed-in person's own account and second factors.

GET    /api/v1/account                          the account, its e-mail and second factors
POST   /api/v1/account/second-factors/<name>    start setting one up: e-mail {"email"}; app {} -> the secret
PUT    /api/v1/account/second-factors/<name>    confirm with {"code"}
DELETE /api/v1/account/second-factors/<name>    remove it; {"password"} confirms -> 204
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g, session

from sudoo import accounts, auth, roles
from sudoo.config import settings
from sudoo.mail import MailError

from .. import signin
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
    role = roles.normalize(account["role"])
    return ok(
        {
            "name": g.user["name"],
            "display_name": accounts.display_name(g.user["name"], account),
            **{key: account.get(key) for key in accounts.PROFILE_FIELDS},
            "role": role,
            "role_label": accounts.ROLES[role],
            "password_managed_here": auth.authenticator().manages_passwords,
            "second_factors": factors_of(account),
            "second_factor_required": auth.must_use_second_factor(account),
            "require_second_factor": settings.require_second_factor,
        }
    )


@blueprint.patch("/account")
def change_profile() -> Response:
    data = check.body()
    values = {key: check.text(data, key, max_length=120) for key in accounts.PROFILE_FIELDS if key in data}
    if "email" in values and "email" in account_factor_names():
        abort(400, "Your e-mail address receives sign-in codes: change it by setting up the e-mail code again.")
    problem = accounts.profile_problem(values)
    if problem:
        abort(400, problem)
    accounts.update(g.user["name"], **{key: (value or None) for key, value in values.items()})
    log("own profile changed", detail=", ".join(sorted(values)))
    return read()


def account_factor_names() -> list[str]:
    return auth.enrolled(_account())


def _plugin(name: str) -> auth.SecondFactor:
    return auth.factor(name) or abort(404, "This kind of second factor is not offered on this panel.")


@blueprint.post("/account/second-factors/<name>")
def begin(name: str) -> Response:
    plugin, account = _plugin(name), _account()
    try:
        started = plugin.begin_setup(g.user["name"], account, check.body(), signin.request_info())
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
    if session.get("restricted") == "second_factor":
        session["restricted"] = signin.restriction(accounts.get(g.user["name"]) or {})
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
