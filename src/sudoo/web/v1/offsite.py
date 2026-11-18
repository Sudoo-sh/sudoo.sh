"""Off-site copies of backups (offsite/): where they go, the signing key, the copies, checking and restoring them.

    GET    /api/v1/offsite                          state: on or off, where copies go, the public key and its
                                                    fingerprint, the last read of the bucket's list
    GET    /api/v1/offsite/public-key               the public key as a .pem file (keep a copy off the server)
    POST   /api/v1/offsite/test                     write, read back and delete a test file with the saved settings
    POST   /api/v1/offsite/scan                     read the bucket's list: copies the panel does not know are added
    GET    /api/v1/offsite/copies                   every copy, newest first
    POST   /api/v1/offsite/copies/<id>/verify       download and check it {"public_key"?: PEM} -> 202 job
    POST   /api/v1/offsite/copies/<id>/retry        copy a failed copy again from the local files -> 202 job
    POST   /api/v1/offsite/copies/<id>/restore      {"instance", "public_key"?, "confirm_production"?} -> 202 job
    DELETE /api/v1/offsite/copies/<id>              delete it from the bucket -> 204

Permissions: backups.manage to see, check and copy again; offsite.configure to test the storage (its settings are
changed with PATCH /api/v1/settings, which also needs it); offsite.restore and instances.restore to restore;
offsite.delete to delete. "public_key" is a key kept off the server, used for this check only (after the server was
rebuilt, its own key is a new one). Restoring, deleting, testing and every check someone asks for are audit-logged.
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, g

from sudoo import offsite
from sudoo.config import settings
from sudoo.core import jobs
from sudoo.instances import stages, store
from sudoo.offsite import keys
from sudoo.offsite.s3 import S3Error

from .. import validation as check
from ..security import log, refuse_unless, require
from .resources import job_resource
from .responses import PREFIX, no_content, ok

blueprint = Blueprint("offsite", __name__)


def _accepted(job: jobs.Job) -> Response:
    response = ok(job_resource(job), status=202)
    response.headers["Location"] = f"{PREFIX}/jobs/{job.id}"
    return response


def _copy(copy_id: str) -> dict[str, object]:
    if not offsite.ID_RE.match(copy_id or ""):
        abort(404, "No such off-site copy.")
    return offsite.get(copy_id) or abort(404, "No such off-site copy.")


def _public_key() -> str | None:
    """The public key given for this check ("public_key", PEM), checked; None for the panel's own."""
    text = check.text(check.body(), "public_key", max_length=2000).strip()
    if not text:
        return None
    try:
        keys.load_public(text)
    except keys.PublicKeyError as error:
        abort(400, str(error))
    return text


def _need_storage() -> None:
    if not offsite.configured():
        abort(409, "Off-site storage is not set up yet: fill in its address, bucket and access key in Settings, "
                   "Backups.")  # fmt: skip


@blueprint.get("/offsite")
@require("backups.manage")
def state() -> Response:
    pem = keys.public_pem()
    return ok(
        {
            "enabled": bool(settings.offsite_backups),
            "configured": offsite.configured(),
            "active": offsite.active(),
            "location": offsite.location() if offsite.configured() else None,
            "endpoint": settings.offsite_endpoint or None,
            "other_stages": bool(settings.offsite_other_stages),
            "kinds": sorted(offsite.kinds()),
            "public_key": pem,
            "fingerprint": keys.fingerprint(pem) if pem else None,
            "last_scan": offsite.last_scan(),
        }
    )


@blueprint.get("/offsite/public-key")
@require("backups.manage")
def public_key() -> Response:
    pem = keys.public_pem() or abort(
        404, "There is no signing key yet: it is made when off-site copies are switched on."
    )
    log("off-site public key downloaded", detail=keys.fingerprint(pem))
    response = Response(pem, mimetype="application/x-pem-file")
    response.headers["Content-Disposition"] = 'attachment; filename="sudoo-offsite-signing.pub.pem"'
    return response


@blueprint.post("/offsite/test")
@require("offsite.configure")
def test() -> Response:
    _need_storage()
    try:
        done = offsite.test_connection()
    except S3Error as error:
        log("off-site storage test failed", detail=str(error), ok=False)
        abort(502, str(error))
    log("off-site storage tested", detail=offsite.location())
    return ok({"location": offsite.location(), "steps": done})


@blueprint.post("/offsite/scan")
@require("backups.manage")
def scan() -> Response:
    _need_storage()
    try:
        found = offsite.scan()
    except S3Error as error:
        abort(502, str(error))
    return ok(found)


@blueprint.get("/offsite/copies")
@require("backups.manage")
def list_copies() -> Response:
    return ok([offsite.resource(record) for record in offsite.copies()])


@blueprint.post("/offsite/copies/<copy_id>/verify")
@require("backups.manage")
def verify(copy_id: str) -> Response:
    _copy(copy_id)
    _need_storage()
    pem = _public_key()
    try:
        job = offsite.start_verify(copy_id, pem, g.user["name"])
    except ValueError as error:
        abort(409, str(error))
    log("off-site copy check started", instance=copy_id.split("__")[0],
        detail=copy_id + (f" with the key {keys.fingerprint(pem)}" if pem else ""))  # fmt: skip
    return _accepted(job)


@blueprint.post("/offsite/copies/<copy_id>/retry")
@require("backups.manage")
def retry(copy_id: str) -> Response:
    record = _copy(copy_id)
    _need_storage()
    try:
        job = offsite.retry(copy_id, g.user["name"])
    except ValueError as error:
        abort(409, str(error))
    log("off-site copy started again", instance=str(record["instance"]), detail=copy_id)
    return _accepted(job)


@blueprint.post("/offsite/copies/<copy_id>/restore")
@require("offsite.restore")
def restore(copy_id: str) -> Response:
    record = _copy(copy_id)
    _need_storage()
    refuse_unless("instances.restore", f"restore the off-site copy {copy_id}")
    data = check.body()
    name = check.instance_name(check.text(data, "instance", max_length=40) or str(record["instance"]))
    if not store.exists(name):
        abort(404, f"There is no instance {name}. Create one of the same Odoo version first, then restore into it.")
    if record["status"] in ("uploading", "verifying"):
        abort(409, "This copy is being uploaded or checked right now.")
    if stages.is_production(store.read_env(name)) and check.flag(data, "confirm_production") is not True:
        abort(400, f'{name} is a production instance: confirm that you want to restore into it ("confirm_production": '
                   "true).")  # fmt: skip
    pem = _public_key()
    job = jobs.start(name, "Restore an off-site copy", "restore", offsite.restore, name, copy_id, pem,
                     user=g.user["name"])  # fmt: skip
    log("off-site copy restore started", instance=name,
        detail=copy_id + (f" with the key {keys.fingerprint(pem)}" if pem else ""))  # fmt: skip
    return _accepted(job)


@blueprint.delete("/offsite/copies/<copy_id>")
@require("offsite.delete")
def delete(copy_id: str) -> Response:
    record = _copy(copy_id)
    _need_storage()
    if record["status"] in ("uploading", "verifying"):
        abort(409, "This copy is being uploaded or checked right now.")
    if store.exists(str(record["instance"])) and offsite.protected(record):
        abort(409, "This is the newest good off-site copy of the instance, so it is kept. Delete it once a newer copy "
                   "is stored (or after the instance is deleted).")  # fmt: skip
    try:
        offsite.remove(copy_id)
    except S3Error as error:
        log("off-site copy deletion failed", instance=str(record["instance"]), detail=f"{copy_id}: {error}", ok=False)
        abort(502, str(error))
    log("off-site copy deleted", instance=str(record["instance"]), detail=copy_id)
    return no_content()
