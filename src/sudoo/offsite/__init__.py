"""Off-site copies: backups copied to S3-compatible storage, each with a signed manifest, checked before use.

What is copied (backups.on_created calls backup_created): while OFFSITE_BACKUPS is on and the storage is set up,
every backup of a kind in OFFSITE_KINDS (manual, daily, deletion by default) of a production instance, and of the
other instances too with OFFSITE_OTHER_STAGES. One copy is one backup: its zip file(s) (one per database) with

    manifest.json       format, the copy's id, instance (name and INSTANCE_ID), stage, kind, time, and the name,
                        database, size and SHA-256 of each file
    manifest.json.sig   the raw Ed25519 signature of manifest.json's exact bytes (keys.py)

in <OFFSITE_PREFIX>/<instance>/<stamp>__<kind>/ of the bucket. The zips go first, then the signature, and the
manifest last: a copy without its manifest is not complete, and is not listed.

Copies are uploaded, checked and pruned one after another by jobs of the panel itself (instance "offsite:copies"),
so a large upload never holds an instance's own actions, and the job's log says what happened. Each copy is a
document in the panel's storage (collection offsite_copies) with its status:

    uploading   being copied (or waiting for the copy before it)
    stored      complete in the bucket (every request body's SHA-256 was signed, so the storage refused changed data)
    verifying   being downloaded and checked
    verified    downloaded again, the signature and every checksum checked (verified_at, verified_with: the key's
                fingerprint)
    failed      the copy or its check failed: `reason` says why

A check (verify) downloads into <backups>/.offsite/ (mode 700), checks the signature of the manifest with the panel's
public key or one given for this check (a key kept off the server, after the server was rebuilt), that the manifest
is the one of this copy (instance, time, kind: a signed copy cannot be passed off as another), and the size and
SHA-256 of every file. It fails closed: anything missing, unsigned or different is a failure and nothing is used.
Restoring an off-site copy (restore) runs that check, puts the files among the local backups (with their original
time) and then runs the panel's own restore (operations.restore: the current state is saved first and Rollback is
offered).

Retention (prune): per instance, the newest copy of each of the OFFSITE_KEEP_DAILY_* most recent days that have a
copy and of each of the OFFSITE_KEEP_MONTHLY_* most recent months (production, or the other stages), as restic's
--keep-daily and --keep-monthly count them; the newest verified copy (or, before any check, the newest stored one)
is never removed. Copies being uploaded or checked, and failed ones, are left alone. Every removal is audit-logged.

Alerts (alerts/): "offsite_failed" when a copy or its check fails (and a recovery notice once copies work again for
that instance), "offsite_missing" when a production instance has had no successful copy for
ALERT_OFFSITE_MISSING_HOURS.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from sudoo.config import settings
from sudoo.core import audit, events, jobs
from sudoo.core.jobs import Job, JobError
from sudoo.storage import storage

from . import keys
from .s3 import Client, Destination, S3Error

COLLECTION = "offsite_copies"
META = "offsite_meta"
JOB_INSTANCE = "offsite:copies"
TOPIC = "offsite"  # live event: a copy changed (no details)
FORMAT = "sudoo-panel-offsite-v1"
ID_RE = re.compile(r"^(?P<instance>[a-z0-9][a-z0-9-]{0,30})__(?P<stamp>\d{8}-\d{6})__(?P<kind>[a-z-]{3,20})$")
STATUSES = ("uploading", "stored", "verifying", "verified", "failed")
DONE = ("stored", "verified")
MANIFEST, SIGNATURE = "manifest.json", "manifest.json.sig"

_LOCK = threading.RLock()


class VerifyError(Exception):
    """The copy is not what was signed, or cannot be checked: the text says why."""


# ---------------------------------------------------------------- set-up


def configured() -> bool:
    """Whether the storage is set up: address, bucket, access key ID and secret."""
    return all(
        (
            settings.offsite_endpoint,
            settings.offsite_bucket,
            settings.offsite_access_key_id,
            settings.offsite_secret_access_key,
        )
    )


def active() -> bool:
    """Whether backups are copied off-site now."""
    return bool(settings.offsite_backups) and configured()


def kinds() -> set[str]:
    return {kind for kind in settings.offsite_kinds.split(",") if kind}


def client() -> Client:
    if not configured():
        raise S3Error("Off-site storage is not set up: fill in the address, bucket and access key in Settings.")
    return Client(
        Destination(
            endpoint=settings.offsite_endpoint,
            region=settings.offsite_region,
            bucket=settings.offsite_bucket,
            access_key_id=settings.offsite_access_key_id,
            secret_access_key=settings.offsite_secret_access_key,
            addressing=settings.offsite_addressing,
            timeout=settings.offsite_timeout_seconds,
        )
    )


def location() -> str:
    """Where copies go, as people read it: bucket/prefix (no address, no key)."""
    return "/".join(part for part in (settings.offsite_bucket, settings.offsite_prefix) if part)


def _root() -> str:
    return f"{settings.offsite_prefix}/" if settings.offsite_prefix else ""


def folder_of(copy_id: str) -> str:
    """The copy's folder in the bucket, ending in "/"."""
    found = ID_RE.match(copy_id)
    if not found:
        raise ValueError(f"{copy_id[:80]!r} is not an off-site copy")
    return f"{_root()}{found['instance']}/{found['stamp']}__{found['kind']}/"


def copy_id_of(instance: str, stamp: str, kind: str) -> str:
    return f"{instance}__{stamp}__{kind}"


def _work_dir() -> Path:
    """Where copies are downloaded to be checked: inside the backups folder (same disk), readable by root only."""
    folder = settings.backups_dir / ".offsite"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def test_connection() -> list[str]:
    """Write, read back and delete a small file in the bucket; the steps that worked. Raises S3Error."""
    c = client()
    key = f"{_root()}.sudoo-test-{os.urandom(6).hex()}"
    data = f"Sudoo.sh connection test {datetime.now(UTC).isoformat()}\n".encode()
    done = []
    c.put_bytes(key, data, "text/plain")
    done.append("wrote a test file")
    try:
        if c.get_bytes(key) != data:
            raise S3Error("The test file read back differs from what was written.")
        done.append("read it back")
        next(iter(c.list(_root())), None)
        done.append("listed the folder")
    finally:
        c.delete(key)
    done.append("deleted it")
    return done


# ---------------------------------------------------------------- records


def _publish(copy_id: str) -> None:
    events.publish(TOPIC, copy_id)


def copies() -> list[dict[str, Any]]:
    """Every copy the panel knows, newest first."""
    with _LOCK:
        found = list(storage().all(COLLECTION).values())
    return sorted(found, key=lambda c: (c.get("created") or 0, c["id"]), reverse=True)


def get(copy_id: str) -> dict[str, Any] | None:
    return storage().get(COLLECTION, copy_id)


def _put(record: dict[str, Any]) -> None:
    with _LOCK:
        storage().put(COLLECTION, record["id"], record)
    _publish(record["id"])


def _update(copy_id: str, **changes: Any) -> dict[str, Any] | None:
    with _LOCK:
        record = storage().get(COLLECTION, copy_id)
        if record is None:
            return None
        record.update(changes)
        storage().put(COLLECTION, copy_id, record)
    _publish(copy_id)
    return record


def _forget(copy_id: str) -> None:
    with _LOCK:
        storage().delete(COLLECTION, copy_id)
    _publish(copy_id)


def resource(record: dict[str, Any]) -> dict[str, Any]:
    """A copy as the API shows it."""
    return {
        "id": record["id"],
        "instance": record["instance"],
        "stage": record.get("stage"),
        "kind": record["kind"],
        "stamp": record["stamp"],
        "created": record.get("created"),
        "status": record["status"],
        "reason": record.get("reason"),
        "size": sum(int(f.get("size") or 0) for f in record.get("files") or []),
        "files": [{"name": f["name"], "db": f.get("db"), "size": f.get("size")} for f in record.get("files") or []],
        "location": record.get("location") or location(),
        "uploaded_at": record.get("uploaded_at"),
        "verified_at": record.get("verified_at"),
        "verified_with": record.get("verified_with"),
        "job": record.get("job"),
        "found_in_bucket": bool(record.get("found_in_bucket")),
    }


# ---------------------------------------------------------------- copying


def backup_created(name: str, kind: str, paths: list[Path]) -> None:
    """A backup was written (backups.on_created): copy it off-site when the rules say so."""
    if not active() or kind not in kinds() or not paths:
        return
    from sudoo.instances import stages, store

    env = store.read_env(name)
    stage = stages.of(env)
    if stage != stages.PRODUCTION and not settings.offsite_other_stages:
        return
    queue_upload(name, kind, paths, stage, env.get("INSTANCE_ID", ""))


def queue_upload(
    name: str, kind: str, paths: list[Path], stage: str, instance_id: str = "", user: str = "system"
) -> Job:
    """Record the copy (status uploading) and start the job that uploads it."""
    from sudoo.instances import backups

    found = backups.FILE_RE.match(paths[0].name)
    if not found:
        raise ValueError(f"{paths[0].name} is not a backup file")
    copy_id = copy_id_of(name, found["stamp"], kind)
    record = {
        "id": copy_id,
        "instance": name,
        "instance_id": instance_id,
        "stage": stage,
        "kind": kind,
        "stamp": found["stamp"],
        "created": paths[0].stat().st_mtime,
        "files": [
            {"name": p.name, "db": (backups.FILE_RE.match(p.name) or {"db": ""})["db"], "size": p.stat().st_size}
            for p in paths
        ],
        "status": "uploading",
        "reason": None,
        "location": location(),
        "attempts": int((get(copy_id) or {}).get("attempts") or 0) + 1,
    }
    _put(record)
    job = jobs.start(JOB_INSTANCE, f"Off-site copy of {name}", "offsite-upload", upload, copy_id, user=user)
    _update(copy_id, job=job.id)
    return job


def manifest_bytes(record: dict[str, Any], files: list[dict[str, Any]], signer: str) -> bytes:
    """The exact bytes of a copy's manifest.json."""
    created = float(record["created"])
    manifest = {
        "format": FORMAT,
        "id": record["id"],
        "instance": record["instance"],
        "instance_id": record.get("instance_id") or "",
        "stage": record.get("stage") or "",
        "kind": record["kind"],
        "stamp": record["stamp"],
        "created": datetime.fromtimestamp(created, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_at": created,
        "files": [{k: f[k] for k in ("name", "db", "size", "sha256")} for f in files],
        "signed_by": signer,
    }
    return json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"


def upload(job: Job, copy_id: str) -> None:
    """Upload a recorded copy: its zips, the signature, the manifest; then apply the retention rules."""
    from sudoo.instances import backups

    record = get(copy_id)
    if record is None:
        raise JobError("This off-site copy is no longer recorded.")
    folder = folder_of(copy_id)
    uploaded: list[str] = []
    job.expect(len(record["files"]) + 3)
    c: Client | None = None
    try:
        c = client()
        job.step("Reading the backup files")
        files = []
        for entry in record["files"]:
            path = backups.path_of(entry["name"])
            if path is None:
                raise JobError(f"The backup file {entry['name']} was removed before it could be copied off-site.")
            digest = hashlib.sha256()
            with open(path, "rb") as source:
                while chunk := source.read(1 << 20):
                    digest.update(chunk)
            files.append({**entry, "size": path.stat().st_size, "sha256": digest.hexdigest(), "path": path})
            job.write(f"{entry['name']}: {files[-1]['size']} bytes, SHA-256 {files[-1]['sha256']}\n")
        for entry in files:
            job.step(f"Copying {entry['name']} to {location()}")
            key = folder + entry["name"]
            uploaded.append(key)
            c.put_file(key, entry["path"], entry["sha256"], log=job.write)
            stored = c.head(key)
            if stored != entry["size"]:
                raise JobError(f"The storage holds {stored} bytes of {entry['name']}, not {entry['size']}.")
        job.step("Signing and writing the manifest")
        public = keys.public_pem() if keys.exists() else None
        if public is None:
            keys.ensure()
            public = keys.public_pem() or ""
        signer = keys.fingerprint(public)
        manifest = manifest_bytes(record, files, signer)
        signature = keys.sign(manifest)
        uploaded.append(folder + SIGNATURE)
        c.put_bytes(folder + SIGNATURE, signature)
        uploaded.append(folder + MANIFEST)
        c.put_bytes(folder + MANIFEST, manifest, "application/json")
        job.write(f"Signed with {signer}\n")
    except Exception as error:  # noqa: BLE001 - whatever stopped it, the copy is recorded as failed
        reason = str(error) or repr(error)
        _update(copy_id, status="failed", reason=reason)
        _failed_alert(record["instance"], f"The backup of {record['instance']} could not be copied off-site", reason,
                      copy_id)  # fmt: skip
        for key in reversed(uploaded):  # leave no half copy behind
            try:
                if c is not None:
                    c.delete(key)
            except S3Error:
                pass
        raise JobError(f"The off-site copy failed: {reason}") from None
    _update(
        copy_id,
        status="stored",
        reason=None,
        uploaded_at=time.time(),
        files=[{k: f[k] for k in ("name", "db", "size", "sha256")} for f in files],
        signed_by=signer,
        location=location(),
    )
    job.note(f"Copied off-site to {location()} as {folder}")
    _working_again(record["instance"])
    job.step("Applying the off-site retention rules")
    removed = prune(record["instance"], job=job)
    if removed:
        job.note(f"Removed {len(removed)} older off-site {'copy' if len(removed) == 1 else 'copies'} (retention rules)")


def retry(copy_id: str, user: str) -> Job:
    """Copy a failed copy again, from the local backup files (they must still be on this server)."""
    from sudoo.instances import backups

    record = get(copy_id)
    if record is None:
        raise LookupError("No such off-site copy.")
    if record["status"] in ("uploading", "verifying"):
        raise ValueError("This copy is being uploaded or checked right now.")
    paths = [backups.path_of(f["name"]) for f in record.get("files") or []]
    if not paths or any(p is None for p in paths):
        raise ValueError("The backup files of this copy are no longer on this server, so it cannot be copied again.")
    return queue_upload(
        record["instance"], record["kind"], [p for p in paths if p], record.get("stage") or "",
        record.get("instance_id") or "", user=user,
    )  # fmt: skip


# ---------------------------------------------------------------- checking


class _HashingWriter:
    def __init__(self, target: IO[bytes]) -> None:
        self.target = target
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        self.size += len(data)
        return self.target.write(data)

    def seek(self, offset: int) -> int:  # a retried download starts again
        self.digest = hashlib.sha256()
        self.size = 0
        return self.target.seek(offset)

    def truncate(self) -> int:
        return self.target.truncate()


def check_manifest(copy_id: str, manifest: bytes, signature: bytes, public_pem: str) -> dict[str, Any]:
    """The manifest's content once its signature and its fields are checked; raises VerifyError otherwise."""
    from sudoo.instances import backups

    if len(signature) != 64 or not keys.verify(manifest, signature, public_pem):
        raise VerifyError(
            f"The signature of the manifest does not verify with the key {keys.fingerprint(public_pem)}: the copy "
            "was changed in the bucket, or was signed by another key (another server's)."
        )
    try:
        content = json.loads(manifest)
    except ValueError:
        raise VerifyError("The manifest is not readable.") from None
    found = ID_RE.match(copy_id)
    assert found is not None
    expected = {"format": FORMAT, "id": copy_id, "instance": found["instance"], "stamp": found["stamp"],
                "kind": found["kind"]}  # fmt: skip
    for key, value in expected.items():
        if not isinstance(content, dict) or content.get(key) != value:
            raise VerifyError(f"The signed manifest is of another copy ({key} differs): it was moved in the bucket.")
    files = content.get("files")
    if not isinstance(files, list) or not files:
        raise VerifyError("The signed manifest lists no file.")
    for entry in files:
        name = entry.get("name") if isinstance(entry, dict) else None
        match = backups.FILE_RE.match(name or "")
        if (
            not match
            or (match["instance"], match["stamp"], match["kind"] or "manual") != (found["instance"], found["stamp"],
                                                                                   found["kind"])
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256")))
            or not isinstance(entry.get("size"), int)
        ):  # fmt: skip
            raise VerifyError(f"The signed manifest lists a file this copy cannot have: {str(name)[:80]}.")
    return content


def _download(job: Job, copy_id: str, public_pem: str) -> tuple[dict[str, Any], Path]:
    """Download the copy into a new folder of the work area and check it (check_manifest, then the size and
    SHA-256 of every file). Returns (manifest, folder); the caller removes the folder. Raises VerifyError (nothing
    usable) or S3Error (the storage could not be read)."""
    c = client()
    folder = folder_of(copy_id)
    work = Path(tempfile.mkdtemp(prefix="check-", dir=_work_dir()))
    try:
        job.step("Checking the signature of the manifest")
        try:
            manifest = c.get_bytes(folder + MANIFEST)
        except S3Error as error:
            if error.status == 404:
                raise VerifyError(
                    "The copy has no manifest.json in the bucket: it is incomplete or was removed."
                ) from None
            raise
        try:
            signature = c.get_bytes(folder + SIGNATURE)
        except S3Error as error:
            if error.status == 404:
                raise VerifyError("The copy has no signature (manifest.json.sig): it cannot be trusted.") from None
            raise
        content = check_manifest(copy_id, manifest, signature, public_pem)
        job.write(f"The manifest is signed by {keys.fingerprint(public_pem)}\n")
        for entry in content["files"]:
            job.step(f"Downloading and checking {entry['name']}")
            target = work / entry["name"]
            with open(target, "wb") as raw:
                writer = _HashingWriter(raw)
                try:
                    c.get_to_file(folder + entry["name"], writer)  # type: ignore[arg-type]
                except S3Error as error:
                    if error.status == 404:
                        raise VerifyError(f"{entry['name']} is missing from the bucket.") from None
                    raise
            if writer.size != entry["size"]:
                raise VerifyError(f"{entry['name']} has {writer.size} bytes in the bucket, not {entry['size']}.")
            if writer.digest.hexdigest() != entry["sha256"]:
                raise VerifyError(f"{entry['name']} in the bucket does not match its signed SHA-256 checksum.")
            job.write(f"{entry['name']}: size and SHA-256 match\n")
        return content, work
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _key_for_check(public_pem: str | None) -> tuple[str, bool]:
    """(the PEM to check with, whether it is one given for this check rather than the panel's own)."""
    if public_pem:
        keys.load_public(public_pem)
        return public_pem, True
    own = keys.public_pem()
    if own is None:
        raise VerifyError(
            "This panel has no signing key yet, so it cannot check copies by itself: give the public key kept off "
            "the server."
        )
    return own, False


def start_verify(copy_id: str, public_pem: str | None, user: str) -> Job:
    record = get(copy_id)
    if record is None:
        raise LookupError("No such off-site copy.")
    if record["status"] in ("uploading", "verifying"):
        raise ValueError("This copy is being uploaded or checked right now.")
    _update(copy_id, status="verifying", before_check=record["status"])
    job = jobs.start(JOB_INSTANCE, f"Check of the off-site copy {copy_id}", "offsite-verify", verify, copy_id,
                     public_pem, user=user)  # fmt: skip
    _update(copy_id, job=job.id)
    return job


def verify(job: Job, copy_id: str, public_pem: str | None = None) -> None:
    """Download the copy, check it, record the result (verified, or failed and why), remove the download."""
    record = get(copy_id) or {}
    before = record.get("before_check") or "stored"
    try:
        pem, given = _key_for_check(public_pem)
        content, work = _download(job, copy_id, pem)
    except (VerifyError, keys.PublicKeyError) as error:
        _record_check_failure(copy_id, str(error), job.user)
        raise JobError(f"The copy did not pass its check: {error}") from None
    except (S3Error, OSError) as error:
        _update(copy_id, status=before, reason=f"The last check could not finish: {error}")
        raise JobError(f"The copy could not be checked: {error}") from None
    shutil.rmtree(work, ignore_errors=True)
    _record_check_success(copy_id, content, pem)
    job.note(f"The copy is intact: signature ({keys.fingerprint(pem)}) and every checksum match"
             + (" (with the public key given for this check)" if given else ""))  # fmt: skip


def _record_check_success(copy_id: str, content: dict[str, Any], pem: str) -> None:
    _update(
        copy_id,
        status="verified",
        reason=None,
        verified_at=time.time(),
        verified_with=keys.fingerprint(pem),
        files=[{k: f[k] for k in ("name", "db", "size", "sha256")} for f in content["files"]],
        instance_id=content.get("instance_id") or "",
        stage=content.get("stage") or None,
        created=float(content.get("created_at") or 0) or (get(copy_id) or {}).get("created"),
    )


def _record_check_failure(copy_id: str, reason: str, user: str) -> None:
    record = _update(copy_id, status="failed", reason=reason) or {}
    audit.record(user, None, "off-site copy failed its check", record.get("instance"), ok=False,
                 detail=f"{copy_id}: {reason}")  # fmt: skip
    instance = record.get("instance") or copy_id.split("__")[0]
    _failed_alert(instance, f"An off-site copy of {instance} failed its check", reason, copy_id)


# ---------------------------------------------------------------- restoring


def restore(job: Job, name: str, copy_id: str, public_pem: str | None = None) -> None:
    """Restore instance `name` from an off-site copy: download it, check it (fail closed), put its files among the
    local backups, then the panel's own restore (current state saved first, Rollback offered)."""
    from sudoo.instances import odoo, operations

    _update(copy_id, status="verifying", before_check=(get(copy_id) or {}).get("status") or "stored")
    try:
        pem, _ = _key_for_check(public_pem)
        content, work = _download(job, copy_id, pem)
    except (VerifyError, keys.PublicKeyError) as error:
        _record_check_failure(copy_id, str(error), job.user)
        raise JobError(f"Nothing was restored: the off-site copy did not pass its check. {error}") from None
    except (S3Error, OSError) as error:
        _update(copy_id, status=(get(copy_id) or {}).get("before_check") or "stored",
                reason=f"The last check could not finish: {error}")  # fmt: skip
        raise JobError(f"Nothing was restored: the off-site copy could not be downloaded. {error}") from None
    try:
        created = float(content.get("created_at") or time.time())
        for entry in content["files"]:
            target = settings.backups_dir / entry["name"]
            os.chmod(work / entry["name"], 0o600)
            if target.exists():
                job.write(f"{entry['name']} is on this server already; it is replaced by the verified copy\n")
            os.replace(work / entry["name"], target)
            os.utime(target, (created, created))  # its own time: it is not taken for a newer backup
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _record_check_success(copy_id, content, pem)
    job.note(f"The off-site copy {copy_id} passed its check (signature {keys.fingerprint(pem)}, every checksum)")
    databases = odoo.databases(name)
    chosen = next((f for f in content["files"] if f["db"] in databases), content["files"][0])
    operations.restore(job, name, chosen["name"])


# ---------------------------------------------------------------- the bucket's own list


def scan() -> dict[str, int]:
    """Read the list of copies in the bucket: record the complete copies the panel does not know (after the server
    was rebuilt, say) as stored, and mark known copies whose manifest is gone as failed. Returns {"found",
    "missing"}."""
    c = client()
    folders: dict[str, dict[str, Any]] = {}
    for item in c.list(_root()):
        rest = item["key"][len(_root()) :]
        parts = rest.split("/")
        if len(parts) != 3:
            continue
        instance, copy_dir, file_name = parts
        copy_id = f"{instance}__{copy_dir}"
        if not ID_RE.match(copy_id):
            continue
        entry = folders.setdefault(copy_id, {"manifest": False, "files": [], "modified": 0.0})
        if file_name == MANIFEST:
            entry["manifest"] = True
            entry["modified"] = item["modified"]
        elif file_name.endswith(".zip"):
            entry["files"].append({"name": file_name, "size": item["size"]})
    known = {record["id"]: record for record in copies()}
    found = missing = 0
    for copy_id, entry in folders.items():
        if not entry["manifest"] or copy_id in known:
            continue
        match = ID_RE.match(copy_id)
        assert match is not None
        stamp = match["stamp"]
        try:
            created = datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=settings.zone).timestamp()
        except ValueError:
            created = entry["modified"]
        _put(
            {
                "id": copy_id, "instance": match["instance"], "instance_id": "", "stage": None,
                "kind": match["kind"], "stamp": stamp, "created": created, "files": entry["files"],
                "status": "stored", "reason": None, "location": location(), "uploaded_at": entry["modified"],
                "found_in_bucket": True,
            }
        )  # fmt: skip
        found += 1
    for copy_id, record in known.items():
        if record["status"] in DONE and not folders.get(copy_id, {}).get("manifest"):
            if record.get("location") not in (None, location()):
                continue  # recorded in another bucket or folder, before the settings changed
            _update(copy_id, status="failed", reason="The copy is no longer in the bucket (its manifest is gone).")
            missing += 1
    storage().put(META, "scan", {"at": time.time(), "found": found, "missing": missing})
    return {"found": found, "missing": missing}


def last_scan() -> dict[str, Any] | None:
    return storage().get(META, "scan")


# ---------------------------------------------------------------- retention


def keep_rules(stage: str | None) -> tuple[int, int]:
    """(days, months) kept for an instance of `stage`."""
    if stage == "production":
        return settings.offsite_keep_daily_production, settings.offsite_keep_monthly_production
    return settings.offsite_keep_daily_other, settings.offsite_keep_monthly_other


def to_prune(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The copies of ONE instance that the retention rules remove (records: every copy of it, any order)."""
    done = sorted((r for r in records if r["status"] in DONE), key=lambda r: r.get("created") or 0, reverse=True)
    if not done:
        return []
    stage = next((r.get("stage") for r in done if r.get("stage")), None)
    from sudoo.instances import store

    if store.exists(done[0]["instance"]):
        from sudoo.instances import stages

        stage = stages.of(store.read_env(done[0]["instance"]))
    days, months = keep_rules(stage)
    keep: set[str] = set()
    seen_days: list[str] = []
    seen_months: list[str] = []
    for record in done:
        when = datetime.fromtimestamp(float(record.get("created") or 0), settings.zone)
        day, month = when.strftime("%Y-%m-%d"), when.strftime("%Y-%m")
        if day not in seen_days and len(seen_days) < days:
            seen_days.append(day)
            keep.add(record["id"])
        if month not in seen_months and len(seen_months) < months:
            seen_months.append(month)
            keep.add(record["id"])
    newest_good = next((r for r in done if r["status"] == "verified"), done[0])
    keep.add(newest_good["id"])
    return [r for r in done if r["id"] not in keep]


def protected(record: dict[str, Any]) -> bool:
    """Whether the copy is the newest verified copy of its instance (or, before any check, its newest stored one):
    never removed by the retention rules, and not deleted by hand while its instance exists."""
    same = [r for r in copies() if r["instance"] == record["instance"] and r["status"] in DONE]
    newest_good = next((r for r in same if r["status"] == "verified"), same[0] if same else None)
    return newest_good is not None and newest_good["id"] == record["id"]


def remove(copy_id: str) -> None:
    """Delete the copy from the bucket (the zips first, the manifest last) and forget it. Raises S3Error."""
    record = get(copy_id)
    if record is None:
        return
    folder = folder_of(copy_id)
    c = client()
    names = [f["name"] for f in record.get("files") or []]
    for name in [*names, SIGNATURE, MANIFEST]:
        c.delete(folder + name)
    _forget(copy_id)


def prune(instance: str | None = None, job: Job | None = None, now: float | None = None) -> list[str]:
    """Apply the retention rules to one instance's copies (all instances when None); the removed copies' ids. Each
    removal is audit-logged; a removal that fails is tried again next time."""
    del now  # the rules count days and months that have copies, not days before now
    by_instance: dict[str, list[dict[str, Any]]] = {}
    for record in copies():
        if record.get("location") not in (None, location()):
            continue  # in another bucket or folder: the current settings cannot reach it
        if instance is None or record["instance"] == instance:
            by_instance.setdefault(record["instance"], []).append(record)
    removed: list[str] = []
    for name, records in by_instance.items():
        for record in to_prune(records):
            try:
                remove(record["id"])
            except S3Error as error:
                if job:
                    job.write(f"Could not remove {record['id']}: {error}\n")
                continue
            removed.append(record["id"])
            audit.record("system", None, "off-site copy removed by the retention rules", name, detail=record["id"])
            if job:
                job.write(f"Removed {record['id']} (retention rules)\n")
    return removed


# ---------------------------------------------------------------- alerts


def _failed_alert(instance: str, title: str, reason: str, copy_id: str) -> None:
    from sudoo.alerts import Alert, send

    send(
        Alert(
            "offsite_failed", title, reason, instance=instance, path="/backups?view=offsite",
            key=f"offsite_failed:{instance}", details=[f"Copy: {copy_id}", f"Storage: {location()}"],
            facts={"reason": reason, "copy": copy_id},
        )
    )  # fmt: skip


def _working_again(instance: str) -> None:
    from sudoo.alerts import Alert, recover

    recover(
        f"offsite_failed:{instance}",
        Alert("offsite_failed", f"Off-site copies of {instance} work again",
              f"A backup of {instance} was copied off-site.", instance=instance, path="/backups?view=offsite"),
    )  # fmt: skip


def check_missing(now: float | None = None) -> list[str]:
    """Alert for each production instance without a successful copy for ALERT_OFFSITE_MISSING_HOURS (and recover
    when it has one again); the instances alerted."""
    from sudoo.alerts import Alert, close, recover, send
    from sudoo.instances import stages, store

    now = time.time() if now is None else now
    hours = settings.alert_offsite_missing_hours
    late: list[str] = []
    newest: dict[str, float] = {}
    for record in copies():
        if record["status"] in DONE:
            when = float(record.get("uploaded_at") or record.get("created") or 0)
            newest[record["instance"]] = max(newest.get(record["instance"], 0), when)
    for name in store.names():
        key = f"offsite_missing:{name}"
        env = store.read_env(name)
        if not active() or not stages.is_production(env):
            close(key, now)
            continue
        since = max(newest.get(name, 0), float(env.get("CREATED_AT") or 0), _active_since())
        if now - since < hours * 3600:
            recover(key, Alert("offsite_missing", f"{name} has an off-site copy again",
                               f"A backup of {name} was copied off-site.", instance=name,
                               path="/backups?view=offsite"))  # fmt: skip
            continue
        last = newest.get(name)
        last_text = datetime.fromtimestamp(last, settings.zone).strftime("%Y-%m-%d %H:%M") if last else "never"
        send(
            Alert(
                "offsite_missing", f"No off-site copy of {name} for {hours:g} hours",
                f"Production instance {name} has had no successful off-site copy for {hours:g} hours (last: "
                f"{last_text}). Its backups may be failing, or the storage refuses them: see the Backups page.",
                instance=name, path="/backups?view=offsite", key=key,
                facts={"hours": f"{hours:g}", "last_copy": last_text},
            ),
            now,
        )  # fmt: skip
        late.append(name)
    return late


def _active_since() -> float:
    """When off-site copies were last switched on (the alert counts from then), 0 when unknown."""
    return float((storage().get(META, "enabled") or {}).get("at") or 0)


# ---------------------------------------------------------------- background


def settings_changed(changed: dict[str, object]) -> None:
    """Off-site copies switched on: make the signing key if there is none, remember when (for the alert)."""
    if "offsite_backups" in changed and settings.offsite_backups:
        _ensure_key()
        storage().put(META, "enabled", {"at": time.time()})


def _ensure_key() -> None:
    if keys.ensure():
        fingerprint = keys.fingerprint(keys.public_pem() or "")
        audit.record("system", None, "off-site signing key created", detail=fingerprint)


def resume_after_restart() -> None:
    """Copies left uploading or verifying when the panel stopped: upload again when the files are still here,
    otherwise record why they failed; a check that was cut off goes back to the status before it."""
    from sudoo.instances import backups

    for record in copies():
        if record["status"] == "verifying":
            _update(record["id"], status=record.get("before_check") or "stored", reason="The last check was cut off "
                    "by a restart of the panel.")  # fmt: skip
        elif record["status"] == "uploading":
            paths = [backups.path_of(f["name"]) for f in record.get("files") or []]
            if active() and paths and all(paths):
                jobs.start(JOB_INSTANCE, f"Off-site copy of {record['instance']} (again)", "offsite-upload", upload,
                           record["id"])  # fmt: skip
            else:
                _update(record["id"], status="failed", reason="The upload was cut off by a restart of the panel.")


def due_checks(now: float | None = None) -> list[str]:
    """Copies to check now (OFFSITE_VERIFY_DAYS): for each instance whose newest verified copy is older than that,
    its newest stored copy."""
    if not settings.offsite_verify_days:
        return []
    now = time.time() if now is None else now
    by_instance: dict[str, list[dict[str, Any]]] = {}
    for record in copies():
        by_instance.setdefault(record["instance"], []).append(record)
    due = []
    for records in by_instance.values():
        if any(r["status"] in ("uploading", "verifying") for r in records):
            continue
        verified = [float(r.get("verified_at") or 0) for r in records if r["status"] == "verified"]
        if verified and now - max(verified) < settings.offsite_verify_days * 86400:
            continue
        newest = next((r for r in records if r["status"] in DONE), None)
        if newest and newest["status"] == "stored":
            due.append(newest["id"])
    return due


ROUND_SECONDS = 600
DAILY_SECONDS = 86400


def run_once(now: float | None = None) -> None:
    """One round of the background work: the alert check; once a day the bucket's list, retention and checks."""
    check_missing(now)
    if not active():
        return
    now = time.time() if now is None else now
    last = float((storage().get(META, "daily") or {}).get("at") or 0)
    if now - last < DAILY_SECONDS:
        return
    storage().put(META, "daily", {"at": now})
    try:
        scan()
    except S3Error as error:
        print(f"offsite: could not read the bucket's list: {error}", flush=True)
    jobs.start(JOB_INSTANCE, "Off-site retention rules", "offsite-prune", _prune_job)
    for copy_id in due_checks(now):
        start_verify(copy_id, None, "system")


def _prune_job(job: Job) -> None:
    job.step("Applying the off-site retention rules")
    removed = prune(job=job)
    job.note(f"Removed {len(removed)} off-site {'copy' if len(removed) == 1 else 'copies'}" if removed
             else "Nothing to remove")  # fmt: skip


def run_forever() -> None:
    time.sleep(120)
    resume_after_restart()
    while True:
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001 - the loop must keep running; the error is logged
            print("offsite:", repr(exc), flush=True)
        time.sleep(ROUND_SECONDS)


def start(on_change: Callable[[Callable[[dict[str, Any]], None]], None]) -> None:
    """Copy new backups, follow setting changes, and run the background loop (called once at start)."""
    from sudoo.instances import backups

    backups.on_created(backup_created)
    on_change(settings_changed)
    if settings.offsite_backups:  # switched on in .env: the key exists from the start
        _ensure_key()
        if storage().get(META, "enabled") is None:
            storage().put(META, "enabled", {"at": time.time()})
    threading.Thread(target=run_forever, daemon=True, name="offsite").start()
