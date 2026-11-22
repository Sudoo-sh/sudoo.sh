"""Import database: upload an Odoo dump in pieces and put it into an instance (instances/imports.py).

    POST   /api/v1/instances/<name>/imports                 {"file_name", "size"} -> 201: the upload
                                                            {"id", "file_name", "format", "size", "received",
                                                            "complete", "chunk_size"}
    GET    /api/v1/instances/<name>/imports/<id>            the upload: how much arrived (to continue after a lost
                                                            connection)
    PUT    /api/v1/instances/<name>/imports/<id>?offset=    the next piece, as application/octet-stream (at most
                                                            imports.CHUNK_MAX); 409 with meta.received when the
                                                            offset is not where the upload stands
    DELETE /api/v1/instances/<name>/imports/<id>            cancel -> 204
    POST   /api/v1/instances/<name>/imports/<id>/restore    {"neutralize": true|false, "confirm_production": true,
                                                            "confirm_stage": "production"} -> 202: the job

All need instances.restore in the instance, and an upload belongs to the person who started it. The file is checked
before the job starts (zip slip, dump.sql, a manifest of another major Odoo version: 400). "neutralize" is true by
default outside production, false in production (where the dump usually is the production data itself).
Production also needs "confirm_production": true and "confirm_stage": "production" (typed); its current state is
backed up first, as before every import.
"""

from __future__ import annotations

import re

from flask import Blueprint, Response, abort, g, request

from sudoo.core import jobs
from sudoo.instances import imports, stages, store

from .. import validation as check
from ..security import log, require
from .instances import _accepted, existing
from .responses import no_content, ok

blueprint = Blueprint("imports", __name__)


def _upload(name: str, upload_id: str) -> dict[str, object]:
    found = imports.get(upload_id, name, g.user["name"])
    if found is None:
        abort(404, "This upload does not exist (any more). Upload the file again.")
    return found


@blueprint.post("/instances/<name>/imports")
@require("instances.restore")
def start(name: str) -> Response:
    existing(name)
    data = check.body()
    file_name = check.text(data, "file_name", max_length=255)
    size = data.get("size")
    if not isinstance(size, int) or isinstance(size, bool):
        abort(400, "The field 'size' must be a whole number of bytes.")
    try:
        meta = imports.start(name, g.user["name"], file_name, size)
    except imports.ImportProblem as problem:
        abort(413 if size > imports.max_bytes() else 400, str(problem))
    log("database import upload started", instance=name, detail=f"{file_name} ({size} bytes)")
    return ok(imports.public(meta), status=201)


@blueprint.get("/instances/<name>/imports/<upload_id>")
@require("instances.restore")
def status(name: str, upload_id: str) -> Response:
    return ok(imports.public(_upload(existing(name), upload_id)))


@blueprint.put("/instances/<name>/imports/<upload_id>")
@require("instances.restore")
def piece(name: str, upload_id: str) -> Response:
    meta = _upload(existing(name), upload_id)
    if request.mimetype != "application/octet-stream":
        abort(415, "Send the piece as application/octet-stream.")
    raw = request.args.get("offset", "")
    if not re.fullmatch(r"[0-9]{1,15}", raw):
        abort(400, "'offset' must be a whole number.")
    offset = int(raw)
    try:
        meta = imports.append(meta, offset, request.get_data(cache=False))
    except imports.ImportProblem as problem:
        current = imports.get(upload_id, name, g.user["name"]) or meta
        return ok({"detail": str(problem)}, status=409, meta={"received": current["received"]})
    return ok(imports.public(meta))


@blueprint.delete("/instances/<name>/imports/<upload_id>")
@require("instances.restore")
def cancel(name: str, upload_id: str) -> Response:
    _upload(existing(name), upload_id)
    imports.remove(upload_id)
    return no_content()


@blueprint.post("/instances/<name>/imports/<upload_id>/restore")
@require("instances.restore")
def restore(name: str, upload_id: str) -> Response:
    meta = _upload(existing(name), upload_id)
    data = check.body()
    env = store.read_env(name)
    production = stages.is_production(env)
    if production and (
        check.flag(data, "confirm_production") is not True
        or check.text(data, "confirm_stage", max_length=20) != stages.PRODUCTION
    ):
        log("database import refused (production not confirmed)", instance=name, ok=False)
        abort(400, 'This is a production instance: confirm, and type "production", to replace its database.')
    neutralize = check.flag(data, "neutralize", default=not production)
    try:
        imports.check_file(meta, env["ODOO_VERSION"])
    except imports.ImportProblem as problem:
        log("database import refused", instance=name, ok=False, detail=str(problem))
        abort(400, str(problem))
    job = jobs.start(
        name,
        "Import a database",
        "restore",
        imports.import_database,
        name,
        upload_id,
        g.user["name"],
        bool(neutralize),
        user=g.user["name"],
    )
    log(
        "database import started" + (" (production)" if production else ""),
        instance=name,
        detail=f"{meta['file_name']}, neutralise: {'yes' if neutralize else 'no'}",
    )
    return _accepted(job)
