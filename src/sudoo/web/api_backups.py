"""Backups (administrators only): list, download and delete the Odoo-format backup zips."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, send_file

from sudoo.instances import backups

from . import validation as check
from .security import admin_only, log

blueprint = Blueprint("backups", __name__, url_prefix="/api")


@blueprint.get("/backups")
@admin_only
def list_backups() -> Response:
    return jsonify(backups.all_backups())


@blueprint.get("/backups/<file_name>")
@admin_only
def download(file_name: str) -> Response:
    path = check.backup_file(file_name)
    log("backup downloaded", detail=path.name)
    return send_file(path, mimetype="application/zip", as_attachment=True, download_name=path.name)


@blueprint.post("/backups/<file_name>/delete")
@admin_only
def delete(file_name: str) -> Response:
    path = check.backup_file(file_name)
    path.unlink()
    log("backup deleted", detail=path.name)
    return jsonify(ok=True)
