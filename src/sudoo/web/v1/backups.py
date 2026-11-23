"""Backups: Odoo-format zips (dump.sql, the filestore and manifest.json), taken before deletions and on request.

GET    /api/v1/backups          backups, newest first, with the Odoo version and edition they are of;
                                ?instance=<name> those taken from that instance, ?project=<id> those of the
                                project's instances (the ones it has now)
GET    /api/v1/backups/<file>   download the zip
DELETE /api/v1/backups/<file>   delete -> 204

All need backups.manage; a backup also needs it in the project and branch of the instance it was taken from
(projects/access.py), also once that instance is deleted (they are recorded in the backup's manifest): the backups
of a project or branch hidden from the caller are not listed, and those of a locked branch are only downloaded or
deleted by the people the lock allows. A backup of a deleted instance that does not say where it belonged (taken by
an earlier version) is only for those from whom nothing is hidden.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, abort, g, request, send_file

from sudoo import projects
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
    if request.args.get("instance"):
        name = check.instance_name(request.args["instance"])
        found = [b for b in found if b["instance"] == name]
    if request.args.get("project"):
        members = set(projects.instances(request.args["project"]))
        found = [b for b in found if b["instance"] in members]
    existing = {b["instance"] for b in found if store.exists(b["instance"])}
    hidden = existing - set(access.visible_instances(g.user, existing))
    if not access.sees_everything(g.user):  # project roles: backups only where the caller may manage them
        hidden |= {name for name in existing - hidden if not access.allowed_instance(g.user, "backups.manage", name)}
    shown = [b for b in found if b["instance"] not in hidden]
    return ok([backup_resource(b) for b in shown if _orphan_refusal(b) is None])


def _owned(backup: Mapping[str, Any]) -> bool:
    """Whether the backup was taken from the instance of its name that exists now."""
    return store.exists(backup["instance"]) and backups.belongs_to(backup["instance"], dict(backup))


def _orphan_refusal(backup: Mapping[str, Any]) -> tuple[int, str] | None:
    """Why the caller may not manage a backup that is not the own backup of an instance that exists now (its instance
    was deleted, maybe replaced by another of the same name), or None: the project and branch recorded in the backup
    decide; a backup that recorded none (an earlier version's) is only for those from whom nothing is hidden."""
    if _owned(backup):
        return None
    missing = (404, "This backup does not exist.")
    recorded = backups.scope(backup["file"])
    if recorded is None:
        return None if access.sees_everything(g.user) else missing
    project_id, branch = recorded
    if not project_id:  # taken from an instance outside every project: the role decides
        return None
    project = projects.get(project_id)
    if project is None:  # its project was deleted since
        return None if access.sees_everything(g.user) else missing
    found = access.refusal(g.user, "backups.manage", project, branch or None)
    return missing if found and found[0] == 404 else found


def refuse_unusable(backup: Mapping[str, Any], what: str) -> None:
    """403/404 (audit-logged) unless the caller has backups.manage where the backup was taken: in its instance when
    it is that instance's own, else in the project and branch it recorded."""
    if _owned(backup):
        refuse_unless("backups.manage", what, instance=backup["instance"])
        return
    refused = _orphan_refusal(backup)
    if refused:
        log(f"refused: {what}", ok=False, detail=backup["file"])
        abort(*refused)


def _allowed_file(file_name: str, what: str) -> Path:
    """The backup's path, when the caller has backups.manage where it was taken (refuse_unusable)."""
    path = check.backup_file(file_name)
    found = next((b for b in backups.all_backups() if b["file"] == path.name), None)
    if found is not None:
        refuse_unusable(found, f"{what} backup {path.name}")
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
