"""Backups: Odoo-format zips (dump.sql, the filestore and manifest.json), taken before deletions and on request.

GET    /api/v1/backups          backups, newest first, with the Odoo version and edition they are of
GET    /api/v1/backups/<file>   download the zip
DELETE /api/v1/backups/<file>   delete -> 204

All need backups.manage; a backup of an instance that exists also needs it in that instance's project and branch
(projects/access.py): the backups of a project or branch hidden from the caller are not listed, and those of a
locked branch are only downloaded or deleted by the people the lock allows.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, g, send_file

from sudoo.instances import backups, store
from sudoo.projects import access

from .. import validation as check
from ..security import log, refuse_unless, require
from .resources import backup_resource
from .responses import no_content, ok

blueprint = Blueprint("backups", __name__)


@blueprint.get("/backups")
@require("backups.manage")
def list_backups() -> Response:
    found = backups.all_backups()
    existing = {b["instance"] for b in found if store.exists(b["instance"])}
    hidden = existing - set(access.visible_instances(g.user, existing))
    return ok([backup_resource(b) for b in found if b["instance"] not in hidden])


def _allowed_file(file_name: str, what: str) -> Path:
    """The backup's path, when the caller has backups.manage in the instance it was taken from (when it exists)."""
    path = check.backup_file(file_name)
    owner = next((b["instance"] for b in backups.all_backups() if b["file"] == path.name), None)
    if owner and store.exists(owner):
        refuse_unless("backups.manage", f"{what} backup {path.name}", instance=owner)
    return path


@blueprint.get("/backups/<file_name>")
@require("backups.manage")
def download(file_name: str) -> Response:
    path = _allowed_file(file_name, "download")
    log("backup downloaded", detail=path.name)
    return send_file(path, mimetype="application/zip", as_attachment=True, download_name=path.name)


@blueprint.delete("/backups/<file_name>")
@require("backups.manage")
def delete(file_name: str) -> Response:
    path = _allowed_file(file_name, "delete")
    path.unlink()
    log("backup deleted", detail=path.name)
    return no_content()
