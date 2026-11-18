"""The Sudoo.sh Licence Agreement (licence/).

    GET    /api/v1/licence/text          the agreement this release bundles: version, effective date, SHA-256, links,
                                         the statements to accept, both summaries and the full text as HTML (public:
                                         first-run setup shows it)
    GET    /api/v1/licence               the bundled and required versions, whether acceptance is pending and the
                                         latest acceptance; with settings.manage also every acceptance (the
                                         installer's too), with its address, browser and when the hub got it
    POST   /api/v1/licence/acceptances   a super admin accepts the bundled version: {"accepted": true, "version",
                                         "sha256", "use": "personal"|"company"|"service_provider", "on_behalf",
                                         "language"} -> 201

A super admin can open only these endpoints (and their own session and account) while acceptance is pending
(security.py). Accepting again later is allowed (for example after the declared use changed); every acceptance is
added, none replaced.
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, g, request

from sudoo import accounts, licence, roles

from .. import validation as check
from ..security import client_ip, log
from .responses import ok

blueprint = Blueprint("licence", __name__)


@blueprint.get("/licence/text")
def text() -> Response:
    return ok(licence.rendered())


@blueprint.get("/licence")
def status() -> Response:
    return ok(licence.status(full="settings.manage" in roles.permissions(g.user["role"])))


@blueprint.post("/licence/acceptances")
def accept() -> Response:
    if g.user["role"] != "super_admin" or g.user["name"].startswith("api:"):
        abort(403, "Only a super admin can accept the Sudoo.sh Licence Agreement for this panel.")
    try:
        accepted = licence.check_acceptance(check.body())
    except licence.LicenceError as error:
        abort(400, str(error))
    name = g.user["name"]
    entry = licence.record(
        user=name,
        account=accounts.get(name) or {},
        role=g.user["role"],
        ip=client_ip(),
        user_agent=request.headers.get("User-Agent", ""),
        surface="panel",
        **accepted,
    )
    log("accepted the Sudoo.sh Licence Agreement", detail=f"version {entry['version']}, use {entry['use']}")
    return ok(licence.status(full=True), status=201)
