"""Accounts and the audit log (administrators only)."""
from flask import Blueprint, abort, g, jsonify

from .. import accounts, audit
from . import validation as check
from .security import admin_only, log

blueprint = Blueprint("accounts", __name__, url_prefix="/api")


@blueprint.get("/users")
@admin_only
def list_users():
    return jsonify([{"name": name, "role": user["role"], "role_label": accounts.ROLES[user["role"]],
                     "last_login": user.get("last_login"), "password_changed": user["password_changed"]}
                    for name, user in sorted(accounts.all_users().items())])


@blueprint.post("/users")
@admin_only
def create_user():
    data = check.body()
    name = check.username(check.text(data, "name", max_length=32).lower())
    role = check.one_of(check.text(data, "role"), accounts.ROLES, "Choose a role from the list.")
    try:
        password = accounts.create(name, role)
    except ValueError as error:
        abort(400, str(error))
    log("account created", detail=f"{name} ({accounts.ROLES[role]})")
    return jsonify(name=name, password=password)


@blueprint.post("/users/<name>/reset-password")
@admin_only
def reset_password(name):
    check.username(name)
    if name == g.user["name"]:
        abort(400, "Use Change password for your own account.")
    if not accounts.get(name):
        abort(404, "No such account.")
    password = accounts.generate_password()
    accounts.set_password(name, password)
    log("password reset", detail=name)
    return jsonify(password=password)


@blueprint.post("/users/<name>/delete")
@admin_only
def delete_user(name):
    check.username(name)
    if name == g.user["name"]:
        abort(400, "You cannot delete the account you are signed in with.")
    try:
        accounts.delete(name)
    except ValueError as error:
        abort(400, str(error))
    log("account deleted", detail=name)
    return jsonify(ok=True)


@blueprint.get("/audit")
@admin_only
def audit_log():
    return jsonify(audit.latest(200))
