"""API tokens.

    GET    /api/v1/tokens         tokens (never their secrets)
    POST   /api/v1/tokens         create: {"name", "scope"}; token and signing secret in this answer only -> 201
    DELETE /api/v1/tokens/<id>    revoke -> 204

A "write" token acts as an administrator, a "read" token as a viewer (roles.py).
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, g

from sudoo import api_tokens

from .. import validation as check
from ..security import log, require
from .responses import PREFIX, created, no_content, ok

blueprint = Blueprint("tokens", __name__)


@blueprint.get("/tokens")
@require("tokens.manage")
def list_tokens() -> Response:
    return ok(api_tokens.all_tokens())


@blueprint.post("/tokens")
@require("tokens.manage")
def create_token() -> Response:
    data = check.body()
    name = check.text(data, "name", max_length=40)
    scope = check.one_of(check.text(data, "scope"), api_tokens.SCOPES, "Choose the scope read or write.")
    try:
        token, signing_secret = api_tokens.create(name, scope, g.user["name"])
    except ValueError as error:
        abort(400, str(error))
    log("API token created", detail=f"{name} ({scope})")
    token_id = token.split("_")[1]
    made = {"id": token_id, "name": name, "scope": scope, "token": token, "signing_secret": signing_secret}
    return created(made, f"{PREFIX}/tokens/{token_id}")


@blueprint.delete("/tokens/<token_id>")
@require("tokens.manage")
def revoke_token(token_id: str) -> Response:
    try:
        name = api_tokens.revoke(token_id)
    except KeyError:
        abort(404, "This API token does not exist.")
    log("API token revoked", detail=name)
    return no_content()
