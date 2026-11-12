"""Backups (administrators): Odoo-format zips taken before deletions and on request.

GET    /api/v1/backups          backups, newest first
GET    /api/v1/backups/<file>   download the zip
DELETE /api/v1/backups/<file>   delete -> 204
"""

from __future__ import annotations

from flask import Blueprint, Response

from sudoo.instances import backups

from .. import api_backups
from ..security import admin_only
from .responses import no_content, ok

blueprint = Blueprint("backups", __name__)


@blueprint.get("/backups")
@admin_only
def list_backups() -> Response:
    return ok(backups.all_backups())


@blueprint.get("/backups/<file_name>")
def download(file_name: str) -> Response:
    return api_backups.download(file_name)


@blueprint.delete("/backups/<file_name>")
def delete(file_name: str) -> Response:
    api_backups.delete(file_name)
    return no_content()
