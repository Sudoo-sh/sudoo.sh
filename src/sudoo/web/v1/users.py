"""Accounts and the audit log (administrators).

GET    /api/v1/users                    accounts
POST   /api/v1/users                    create: {"name", "role"}; the password is in this answer only -> 201
POST   /api/v1/users/<name>/password    set a new password and return it once
DELETE /api/v1/users/<name>             delete -> 204
GET    /api/v1/audit?limit=             the latest audit-log entries, newest first
"""

from __future__ import annotations

from flask import Blueprint, Response, request

from sudoo import accounts
from sudoo.core import audit

from .. import api_accounts
from .. import validation as check
from ..security import admin_only
from .responses import PREFIX, created, iso, no_content, ok

blueprint = Blueprint("users", __name__)


def _user(name: str, user: dict[str, object]) -> dict[str, object]:
    return {
        "name": name,
        "role": user["role"],
        "role_label": accounts.ROLES[str(user["role"])],
        "last_sign_in_at": iso(user.get("last_login")),  # type: ignore[arg-type]
        "password_changed_at": iso(user.get("password_changed")),  # type: ignore[arg-type]
        "links": {"self": f"{PREFIX}/users/{name}"},
    }


@blueprint.get("/users")
@admin_only
def list_users() -> Response:
    return ok([_user(name, user) for name, user in sorted(accounts.all_users().items())])


@blueprint.post("/users")
def create_user() -> Response:
    made = api_accounts.create_user().get_json()
    user = _user(made["name"], accounts.get(made["name"]) or {})
    return created(user | {"password": made["password"]}, f"{PREFIX}/users/{made['name']}")


@blueprint.post("/users/<name>/password")
def reset_password(name: str) -> Response:
    return ok({"password": api_accounts.reset_password(name).get_json()["password"]})


@blueprint.delete("/users/<name>")
def delete_user(name: str) -> Response:
    api_accounts.delete_user(name)
    return no_content()


@blueprint.get("/audit")
@admin_only
def audit_log() -> Response:
    limit = check.whole_number(request.args.get("limit", "200"), "limit", minimum=1, maximum=1000)
    return ok(audit.latest(limit))
