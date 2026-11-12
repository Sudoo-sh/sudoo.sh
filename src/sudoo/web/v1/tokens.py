"""API tokens (administrators).

GET    /api/v1/tokens         tokens (never their secrets)
POST   /api/v1/tokens         create: {"name", "scope"}; token and signing secret in this answer only -> 201
DELETE /api/v1/tokens/<id>    revoke -> 204
"""

from __future__ import annotations

from flask import Blueprint, Response

from sudoo import api_tokens

from .. import api_token_admin
from ..security import admin_only
from .responses import PREFIX, created, no_content, ok

blueprint = Blueprint("tokens", __name__)


@blueprint.get("/tokens")
@admin_only
def list_tokens() -> Response:
    return ok(api_tokens.all_tokens())


@blueprint.post("/tokens")
def create_token() -> Response:
    made = api_token_admin.create_token().get_json()
    token_id = next(t["id"] for t in api_tokens.all_tokens() if t["name"] == made["name"])
    return created(made | {"id": token_id}, f"{PREFIX}/tokens/{token_id}")


@blueprint.delete("/tokens/<token_id>")
def revoke_token(token_id: str) -> Response:
    api_token_admin.revoke_token(token_id)
    return no_content()
