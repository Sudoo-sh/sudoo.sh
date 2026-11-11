"""Backups (administrators only): list, download and delete the Odoo-format backup zips."""
from flask import Blueprint, jsonify, send_file

from .. import backups
from . import validation as check
from .security import admin_only, log

blueprint = Blueprint("backups", __name__, url_prefix="/api")


@blueprint.get("/backups")
@admin_only
def list_backups():
    return jsonify(backups.all_backups())


@blueprint.get("/backups/<file_name>")
@admin_only
def download(file_name):
    path = check.backup_file(file_name)
    log("backup downloaded", detail=path.name)
    return send_file(path, mimetype="application/zip", as_attachment=True, download_name=path.name)


@blueprint.post("/backups/<file_name>/delete")
@admin_only
def delete(file_name):
    path = check.backup_file(file_name)
    path.unlink()
    log("backup deleted", detail=path.name)
    return jsonify(ok=True)
