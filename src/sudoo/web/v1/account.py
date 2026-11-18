"""The signed-in person's own account and second factors.

GET    /api/v1/account                          the account, its e-mail, second factors, recovery codes left and
                                                whether it gets the sign-in e-mail
PATCH  /api/v1/account                          change the profile (names, e-mail, phone), {"sign_in_email":
                                                true|false|null} and {"language": "en"|"ar"|null} (null: the
                                                server's default)
POST   /api/v1/account/second-factors/<name>    start setting one up: e-mail {"email"}; app {} -> the secret;
                                                replacing one already set up also takes {"password"}
PUT    /api/v1/account/second-factors/<name>    confirm with {"code"}; setting up the app also makes recovery
                                                codes, returned once in meta.recovery_codes
DELETE /api/v1/account/second-factors/<name>    remove it; {"password"} confirms -> 204
POST   /api/v1/account/recovery-codes           new recovery codes, replacing the old ones; {"password"} confirms
                                                -> 201 {"codes", ...}, shown this once
DELETE /api/v1/account/sessions                 sign out every other session of the account -> 204

An e-mail address another account uses is refused. Replacing a second factor, or changing the sign-in e-mail
address, ends the account's other sessions; the one making the change continues.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g, session

from sudoo import accounts, auth, roles
from sudoo.auth import recovery, sign_in_notice
from sudoo.config import settings
from sudoo.mail import MailError

from .. import signin
from .. import validation as check
from ..security import client_ip, log
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


EMAIL_TAKEN = "Another account already uses this e-mail address."


def _end_other_sessions() -> None:
    """End every other session of the signed-in account; this one continues."""
    session["version"] = accounts.end_sessions(g.user["name"])


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
            "recovery_codes": recovery_resource(account),
            "recovery_codes_needed": recovery.needed(account),
            "sign_in_email": sign_in_notice.preference(account),
            "language": {
                "value": accounts.language(account),
                "own_choice": accounts.own_language(account),
                "default": settings.default_language,
            },
        }
    )


def recovery_resource(account: dict[str, Any]) -> dict[str, Any] | None:
    found = recovery.state(account)
    return found and {**found, "created_at": iso(found.pop("created"))}


@blueprint.patch("/account")
def change_profile() -> Response:
    data = check.body()
    if "sign_in_email" in data:
        _sign_in_email(data["sign_in_email"])
    if "language" in data:
        _language(data["language"])
    values = {key: check.text(data, key, max_length=120) for key in accounts.PROFILE_FIELDS if key in data}
    if not values:
        return read()
    if "email" in values and "email" in account_factor_names():
        abort(400, "Your e-mail address receives sign-in codes: change it by setting up the e-mail code again.")
    problem = accounts.profile_problem(values)
    if problem:
        abort(400, problem)
    if values.get("email") and accounts.email_taken(values["email"], other_than=g.user["name"]):
        abort(400, EMAIL_TAKEN)
    before = str(_account().get("email") or "").lower()
    accounts.update(g.user["name"], **{key: (value or None) for key, value in values.items()})
    if "email" in values and str(values["email"] or "").lower() != before:
        _end_other_sessions()  # people sign in with this address
    log("own profile changed", detail=", ".join(sorted(values)))
    return read()


def _sign_in_email(choice: Any) -> None:
    """The person's own choice for the sign-in e-mail; turning it off only where the server allows it."""
    if choice is not None and not isinstance(choice, bool):
        abort(400, "sign_in_email must be true, false or null.")
    if choice is False and not settings.sign_in_email_opt_out:
        abort(403, "The sign-in e-mail cannot be turned off on this panel. " + settings.contact_sentence())
    accounts.update(g.user["name"], **{sign_in_notice.FIELD: choice})
    log("sign-in e-mail " + ("turned on" if choice else "turned off" if choice is False else "back to the default"))


def _language(choice: Any) -> None:
    """The person's own language for the web app; null goes back to the server's default."""
    if choice is not None and choice not in accounts.LANGUAGES:
        abort(400, "language must be one of " + ", ".join(accounts.LANGUAGES) + ", or null.")
    accounts.update(g.user["name"], **{accounts.LANGUAGE_FIELD: choice})
    log("language " + (f"set to {choice}" if choice else "back to the default"))


def account_factor_names() -> list[str]:
    return auth.enrolled(_account())


def _plugin(name: str) -> auth.SecondFactor:
    return auth.factor(name) or abort(404, "This kind of second factor is not offered on this panel.")


def _confirm_password(what: str) -> None:
    """Refuse unless the body carries the account's current password. Wrong passwords count like wrong sign-ins."""
    name, ip = g.user["name"], client_ip()
    if accounts.THROTTLE.seconds_to_wait(ip, name):
        abort(429, "Too many wrong passwords. Try again in 15 minutes.")
    if auth.authenticator().authenticate(name, check.secret(check.body(), "password")) is None:
        accounts.THROTTLE.failed(ip, name)
        log(f"{what} refused: wrong password", ok=False)
        abort(400, "The password is not correct.")


@blueprint.post("/account/second-factors/<name>")
def begin(name: str) -> Response:
    plugin, account = _plugin(name), _account()
    body = check.body()
    session.pop("replacing_factor", None)
    if plugin.enrolled(account):  # replacing it: as for removing it, the password confirms
        _confirm_password(f"second factor replacement ({name})")
        session["replacing_factor"] = name
    email = body.get("email")
    if isinstance(email, str) and accounts.email_taken(email, other_than=g.user["name"]):
        abort(400, EMAIL_TAKEN)
    details = {key: value for key, value in body.items() if key != "password"}
    try:
        started = plugin.begin_setup(g.user["name"], account, details, signin.request_info())
    except ValueError as error:
        abort(400, str(error))
    except MailError as error:
        abort(503, str(error))
    return ok(started)


@blueprint.put("/account/second-factors/<name>")
def finish(name: str) -> Response:
    plugin, account = _plugin(name), _account()
    replacing = plugin.enrolled(account)
    if replacing and session.get("replacing_factor") != name:
        abort(400, "Start again: replacing a second factor that is set up needs your password.")
    code = check.text(check.body(), "code", max_length=12).replace(" ", "")
    fields = plugin.finish_setup(g.user["name"], account, code) if code.isdigit() else None
    if fields is None:
        abort(400, "The code is not correct, or it has expired.")
    email_changed = "email" in fields and str(fields["email"] or "").lower() != str(account.get("email") or "").lower()
    if email_changed and accounts.email_taken(fields["email"], other_than=g.user["name"]):
        abort(400, EMAIL_TAKEN)
    accounts.update(g.user["name"], **fields)
    session.pop("replacing_factor", None)
    if replacing or email_changed:
        _end_other_sessions()
    if session.get("restricted") == "second_factor":
        session["restricted"] = signin.restriction(accounts.get(g.user["name"]) or {})
    log(f"second factor {'replaced' if replacing else 'set up'} ({name})")
    meta = None
    if name == "totp" and recovery.needed(_account()):  # new to the app: codes for the day the phone is lost
        meta = {"recovery_codes": recovery.create(g.user["name"])}
        log("recovery codes made", detail=f"{len(meta['recovery_codes'])} codes")
    return ok(factors_of(_account()), meta=meta)


@blueprint.delete("/account/second-factors/<name>")
def remove(name: str) -> Response:
    plugin, account = _plugin(name), _account()
    _confirm_password(f"second factor removal ({name})")
    if not plugin.enrolled(account):
        abort(404, "This second factor is not set up.")
    if auth.must_use_second_factor(account) and auth.enrolled(account) == [name]:
        abort(400, "A second factor is required for your account: set up another one before removing this one.")
    accounts.update(g.user["name"], **plugin.remove(account))
    _end_other_sessions()
    log(f"second factor removed ({name})")
    return no_content()


@blueprint.post("/account/recovery-codes")
def new_recovery_codes() -> Response:
    """New recovery codes; every earlier one stops working. The password confirms, as for a second factor."""
    account = _account()
    if not auth.factor("totp") or "totp" not in auth.enrolled(account):
        abort(400, "Recovery codes are for the authenticator app: set it up first.")
    _confirm_password("new recovery codes")
    codes = recovery.create(g.user["name"])
    log("recovery codes made", detail=f"{len(codes)} codes, the earlier ones no longer work")
    return ok({"codes": codes, **(recovery_resource(_account()) or {})}, status=201)


@blueprint.delete("/account/sessions")
def sign_out_elsewhere() -> Response:
    """Sign out every other session of the account (a browser left signed in, a sign-in that was not yours)."""
    _end_other_sessions()
    log("signed out every other session")
    return no_content()
