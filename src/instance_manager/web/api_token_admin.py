"""API tokens, managed by administrators in the panel (Accounts, API tokens)."""

from __future__ import annotations

from flask import Blueprint, Response, abort, g, jsonify

from instance_manager import api_tokens

from . import validation as check
from .security import admin_only, log

blueprint = Blueprint("api_token_admin", __name__, url_prefix="/api")


@blueprint.get("/tokens")
@admin_only
def list_tokens() -> Response:
    return jsonify(api_tokens.all_tokens())


@blueprint.post("/tokens")
@admin_only
def create_token() -> Response:
    """Create a token; its token and signing secret are in this answer only."""
    data = check.body()
    name = check.text(data, "name", max_length=40)
    scope = check.one_of(check.text(data, "scope"), api_tokens.SCOPES, "Choose the scope read or write.")
    try:
        token, signing_secret = api_tokens.create(name, scope, g.user["name"])
    except ValueError as error:
        abort(400, str(error))
    log("API token created", detail=f"{name} ({scope})")
    return jsonify(name=name, scope=scope, token=token, signing_secret=signing_secret)


@blueprint.post("/tokens/<token_id>/revoke")
@admin_only
def revoke_token(token_id: str) -> Response:
    try:
        name = api_tokens.revoke(token_id)
    except KeyError:
        abort(404, "This API token does not exist.")
    log("API token revoked", detail=name)
    return jsonify(ok=True)
