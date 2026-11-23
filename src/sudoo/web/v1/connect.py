"""Connect as: open an instance's Odoo signed in as one of its users (instances/connect_as.py).

    GET  /api/v1/instances/<name>/odoo-users    active internal Odoo users: [{"id", "login", "name"}] (read again
                                                after connect_as.USERS_CACHE_SECONDS)
    POST /api/v1/instances/<name>/connect       {"uid": <Odoo user id>} -> 201 {"url", "expires_at", "login"}: a
                                                single-use link valid for connect_as.TOKEN_SECONDS; production also
                                                needs "confirm_stage": "production"

Both need instances.connect in the instance (project roles: Tester and up in staging and development, Maintainer in
production; earlier projects: those who may see Odoo passwords).
Every link given is audit-logged (who, instance, which Odoo user), and so is every use (instances/connect_as.py).
"""

from __future__ import annotations

from datetime import UTC, datetime

from flask import Blueprint, Response, abort, g

from sudoo import exposure
from sudoo.instances import access_rules, connect_as, stages, store

from .. import validation as check
from ..security import log, require
from .instances import existing
from .responses import ok

blueprint = Blueprint("connect", __name__)


def _users(name: str) -> list[dict[str, object]]:
    try:
        return connect_as.odoo_users(name)
    except connect_as.ConnectError as error:
        abort(409, str(error))


@blueprint.get("/instances/<name>/odoo-users")
@require("instances.connect")
def odoo_users(name: str) -> Response:
    return ok(_users(existing(name)))


@blueprint.post("/instances/<name>/connect")
@require("instances.connect")
def connect(name: str) -> Response:
    existing(name)
    data = check.body()
    uid = data.get("uid")
    if not isinstance(uid, int) or isinstance(uid, bool) or uid < 1:
        abort(400, "Choose the Odoo user to connect as.")
    production = stages.is_production(store.read_env(name))
    if production and check.text(data, "confirm_stage", max_length=20) != stages.PRODUCTION:
        log("connect as refused (production not confirmed)", instance=name, ok=False)
        abort(400, 'This is a production instance: type "production" to confirm.')
    target = next((u for u in _users(name) if u["id"] == uid), None)
    if target is None:
        abort(404, "This Odoo user does not exist or is not an active internal user.")
    address = exposure.address(name)
    if not address:
        abort(409, "This instance has no public address yet.")
    token, expires = connect_as.issue(name, uid, str(target["login"]), g.user["name"])
    log(
        "connect as link given" + (" (production)" if production else ""),
        instance=name,
        detail=f"{target['login']} (id {uid})",
    )
    # a production instance with access rules: through a pass, so the link works from anywhere (access_rules.py)
    url, _ = access_rules.pass_url(g.user, name, address, f"{connect_as.PATH}?token={token}")
    return ok(
        {
            "url": url,
            "expires_at": datetime.fromtimestamp(expires, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "login": target["login"],
        },
        status=201,
    )
