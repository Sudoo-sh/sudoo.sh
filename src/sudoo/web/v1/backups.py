"""Backups: Odoo-format zips (dump.sql, the filestore and manifest.json), taken before deletions and on request.

GET    /api/v1/backups          backups, newest first
GET    /api/v1/backups/<file>   download the zip
DELETE /api/v1/backups/<file>   delete -> 204
"""

from __future__ import annotations

from flask import Blueprint, Response, send_file

from sudoo.instances import backups

from .. import validation as check
from ..security import log, require
from .responses import no_content, ok

blueprint = Blueprint("backups", __name__)


@blueprint.get("/backups")
@require("backups.manage")
def list_backups() -> Response:
    return ok(backups.all_backups())


@blueprint.get("/backups/<file_name>")
@require("backups.manage")
def download(file_name: str) -> Response:
    path = check.backup_file(file_name)
    log("backup downloaded", detail=path.name)
    return send_file(path, mimetype="application/zip", as_attachment=True, download_name=path.name)


@blueprint.delete("/backups/<file_name>")
@require("backups.manage")
def delete(file_name: str) -> Response:
    path = check.backup_file(file_name)
    path.unlink()
    log("backup deleted", detail=path.name)
    return no_content()
