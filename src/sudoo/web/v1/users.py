"""Accounts and the audit log.

    GET    /api/v1/roles                         the roles, what each may do, and every permission
    GET    /api/v1/users                         accounts
    POST   /api/v1/users                         create: {"email", "role", "first_name", "last_name", "phone",
                                                 "name" (optional: made from the e-mail address), "password"
                                                 (optional: generated), "must_change_password" (default true)};
                                                 the password is in this answer only -> 201
    PATCH  /api/v1/users/<name>                  change the role or the profile (also your own)
    POST   /api/v1/users/<name>/password         set a new password: {"password"} (optional: generated),
                                                 {"must_change_password"} (default true); returns it once
    DELETE /api/v1/users/<name>/second-factors   remove all second factors (a lost phone) -> 204
    DELETE /api/v1/users/<name>                  delete -> 204
    GET    /api/v1/audit?limit=&user=&instance=  the latest audit-log entries, newest first

Only a super admin creates, changes or deletes super admins; there is always at least one. Accounts created or reset
here choose their own password at their next sign-in, unless must_change_password is false.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g, request

from sudoo import accounts, roles
from sudoo.core import audit

from .. import validation as check
from ..security import log, require
from .account import factors_of
from .responses import PREFIX, created, iso, no_content, ok

blueprint = Blueprint("users", __name__)


def user_resource(name: str, user: dict[str, Any]) -> dict[str, Any]:
    role = roles.normalize(str(user.get("role")))
    return {
        "name": name,
        "display_name": accounts.display_name(name, user),
        **{key: user.get(key) for key in accounts.PROFILE_FIELDS},
        "role": role,
        "role_label": accounts.ROLES[role],
        "last_sign_in_at": iso(user.get("last_login")),
        "password_changed_at": iso(user.get("password_changed")),
        "must_change_password": bool(user.get("must_change_password")),
        "second_factors": [f["name"] for f in factors_of(user) if f["enrolled"]],
        "links": {"self": f"{PREFIX}/users/{name}"},
    }


def _guard(role: str) -> None:
    """Only a super admin creates, changes or deletes super admins."""
    if role == "super_admin" and g.user["role"] != "super_admin":
        abort(403, "Only a super admin can manage super admin accounts.")


def _account(name: str) -> dict[str, Any]:
    check.username(name)
    return accounts.get(name) or abort(404, "No such account.")


def _profile(data: dict[str, Any]) -> dict[str, Any]:
    return {key: check.text(data, key, max_length=120) for key in accounts.PROFILE_FIELDS if key in data}


@blueprint.get("/roles")
def list_roles() -> Response:
    return ok(
        {
            "roles": [
                {
                    "name": name,
                    "label": role.label,
                    "description": role.description,
                    "permissions": sorted(role.permissions),
                }
                for name, role in roles.ROLES.items()
            ],
            "permissions": roles.PERMISSIONS,
        }
    )


@blueprint.get("/users")
@require("accounts.manage")
def list_users() -> Response:
    return ok([user_resource(name, user) for name, user in sorted(accounts.all_users().items())])


def _password(data: dict[str, Any], name: str, profile: dict[str, Any]) -> tuple[str | None, bool]:
    """The typed password (checked against the rules) or None for a generated one, and whether it must change."""
    must_change = data.get("must_change_password", True)
    if not isinstance(must_change, bool):
        abort(400, "must_change_password must be true or false.")
    if not data.get("password"):
        return None, True
    password = check.secret(data, "password")
    names = (str(profile.get("first_name") or ""), str(profile.get("last_name") or ""))
    problem = accounts.password_problem(password, name, profile.get("email"), names)
    if problem:
        abort(400, problem)
    return password, must_change


@blueprint.post("/users")
@require("accounts.manage")
def create_user() -> Response:
    data = check.body()
    role = check.one_of(check.text(data, "role"), roles.ROLES, "Choose a role from the list.")
    _guard(role)
    profile = _profile(data)
    if not profile.get("email"):
        abort(400, "Enter the person's e-mail address: they sign in with it.")
    problem = accounts.profile_problem(profile)
    if problem:
        abort(400, problem)
    chosen = check.text(data, "name", max_length=32).lower()
    name = check.username(chosen) if chosen else accounts.username_for(profile["email"])
    typed, must_change = _password(data, name, profile)
    try:
        password = accounts.create(name, role, typed, must_change=must_change, **profile)
    except ValueError as error:
        abort(400, str(error))
    log("account created", detail=f"{name} ({accounts.ROLES[role]})")
    return created(user_resource(name, accounts.get(name) or {}) | {"password": password}, f"{PREFIX}/users/{name}")


@blueprint.patch("/users/<name>")
@require("accounts.manage")
def change_user(name: str) -> Response:
    account = _account(name)
    _guard(roles.normalize(account["role"]))
    data = check.body()
    values = _profile(data)
    problem = accounts.profile_problem(values)
    if problem:
        abort(400, problem)
    if "email" in values and accounts.email_taken(values["email"], other_than=name):
        abort(400, "Another account already uses this e-mail address.")
    if "role" in data:
        role = check.one_of(check.text(data, "role"), roles.ROLES, "Choose a role from the list.")
        _guard(role)
        if account["role"] == "super_admin" and role != "super_admin":
            others = [n for n, u in accounts.all_users().items() if u["role"] == "super_admin" and n != name]
            if not others:
                abort(400, "The last super admin must stay a super admin.")
        values["role"] = role
    accounts.update(name, **{key: (value or None) for key, value in values.items()})
    if "role" in values:
        accounts.end_sessions(name)  # the new permissions apply from the next sign-in
    log("account changed", detail=f"{name}: {', '.join(sorted(values))}")
    return ok(user_resource(name, accounts.get(name) or {}))


@blueprint.post("/users/<name>/password")
@require("accounts.manage")
def reset_password(name: str) -> Response:
    account = _account(name)
    if name == g.user["name"]:
        abort(400, "Use Change password for your own account.")
    _guard(roles.normalize(account["role"]))
    data = check.body()
    typed, must_change = _password(data, name, account)
    password = typed or accounts.generate_password()
    accounts.set_password(name, password, must_change=must_change)
    log("password reset", detail=name)
    return ok({"password": password})


@blueprint.delete("/users/<name>/second-factors")
@require("accounts.manage")
def reset_second_factors(name: str) -> Response:
    _guard(roles.normalize(_account(name)["role"]))
    accounts.update(name, second_factors=None, totp_pending=None)
    accounts.end_sessions(name)
    log("second factors removed", detail=name)
    return no_content()


@blueprint.delete("/users/<name>")
@require("accounts.manage")
def delete_user(name: str) -> Response:
    account = _account(name)
    if name == g.user["name"]:
        abort(400, "You cannot delete the account you are signed in with.")
    _guard(roles.normalize(account["role"]))
    try:
        accounts.delete(name)
    except ValueError as error:
        abort(400, str(error))
    log("account deleted", detail=name)
    return no_content()


@blueprint.get("/audit")
@require("audit.view")
def audit_log() -> Response:
    limit = check.whole_number(request.args.get("limit", "200"), "limit", minimum=1, maximum=2000)
    user = request.args.get("user") or None
    instance = request.args.get("instance") or None
    return ok(audit.latest(limit, user=user, instance=instance))
