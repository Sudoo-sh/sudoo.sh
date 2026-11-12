"""Backups of instances, in Odoo's own backup format, kept in BACKUPS_DIR.

One zip file per database, named <instance>__<database>__<YYYYMMDD-HHMMSS>__<kind>.zip, containing what Odoo's
database manager produces and accepts under "Restore":

    dump.sql        the database, as SQL (pg_dump --no-owner)
    filestore/      attachments and images
    manifest.json   what the backup is (database, Odoo version)

Kinds (files of earlier versions have no kind and count as "manual"):

    manual          "Back up now"; kept until deleted by hand
    daily           once a day (DAILY_BACKUP_TIME in TIMEZONE) for each instance used since its last daily backup;
                    each instance keeps only its latest one, and they go when the instance is deleted
    deletion        taken before an instance is deleted; kept
    before-restore  the state an instance had before a restore; each instance keeps only its latest one

The database container is started for a backup if the instance is paused; the instance's state is not changed
otherwise. restore() puts a backup back: the database is replaced and so is the filestore.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO, Any, cast

from sudoo.config import settings
from sudoo.core.jobs import Job, JobError
from sudoo.instances import odoo, store

FILE_RE = re.compile(
    r"^(?P<instance>[a-z0-9][a-z0-9-]{0,30})__(?P<db>[a-z0-9_]{1,63})__(?P<stamp>\d{8}-\d{6})"
    r"(?:__(?P<kind>manual|daily|deletion|before-restore))?\.zip$"
)
KINDS = ("manual", "daily", "deletion", "before-restore")
RESTORABLE = ("manual", "daily", "deletion")  # what "Restore the latest backup" chooses from
_KEEP_ONE = ("daily", "before-restore")  # kinds of which each instance keeps only the latest


def create(job: Job, name: str, kind: str = "manual") -> list[Path]:
    """Back up every database of instance `name`; return the paths of the backup files."""
    settings.backups_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _database_up(job, name)
    version = store.read_env(name)["ODOO_VERSION"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    saved = [_backup_database(job, name, db, version, stamp, kind) for db in odoo.databases(name)]
    if kind in _KEEP_ONE:
        for older in of_instance(name, (kind,)):
            if older["file"] not in {path.name for path in saved}:
                (settings.backups_dir / older["file"]).unlink(missing_ok=True)
                job.write(f"Removed the previous {kind} backup {older['file']}\n")
    return saved


def _database_up(job: Job, name: str) -> None:
    job.run(store.compose(name, "up", "-d", "db"), timeout=300)
    if not odoo.wait_for_database(name, timeout=60):
        raise JobError("the database did not start")


def _backup_database(job: Job, name: str, db: str, version: str, stamp: str, kind: str) -> Path:
    target = settings.backups_dir / f"{name}__{db}__{stamp}__{kind}.zip"
    job.write(f"Backing up database {db} to {target}\n")
    with tempfile.TemporaryDirectory(dir=settings.backups_dir) as tmp:
        dump, files = Path(tmp) / "dump.sql", Path(tmp) / "filestore.tar"
        _run_to_file(
            ["docker", "exec", store.container(name, "db"), "pg_dump", "-U", "odoo", "--no-owner", db],
            dump,
            "database dump",
        )
        # docker cp also works while Odoo is stopped; "-" writes the folder as a tar stream
        _run_to_file(
            ["docker", "cp", f"{store.container(name, 'odoo')}:/var/lib/odoo/filestore/{db}", "-"],
            files,
            "file storage copy",
        )
        partial = target.with_name(target.name + ".part")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(dump, "dump.sql")
            with tarfile.open(files) as tar:
                for member in tar:
                    inside = member.name.split("/", 1)[1] if "/" in member.name else ""
                    if member.isfile() and inside:
                        archive.writestr(f"filestore/{inside}", cast(IO[bytes], tar.extractfile(member)).read())
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "odoo_dump": "1",
                        "db_name": db,
                        "version": version,
                        "major_version": version,
                        "created_by": "Sudoo",
                        "instance": name,
                    },
                    indent=2,
                ),
            )
        os.chmod(partial, 0o600)
        partial.replace(target)
    job.write(f"Backup written: {target.name} ({target.stat().st_size // 1024} KB)\n")
    return target


def _run_to_file(cmd: list[str], path: Path, what: str) -> None:
    with open(path, "wb") as out:
        result = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, timeout=3600)
    if result.returncode != 0:
        raise JobError(f"{what} failed: {result.stderr.decode(errors='replace').strip()[-300:]}")


def all_backups() -> list[dict[str, Any]]:
    """Every backup file, newest first: [{"file", "instance", "db", "kind", "size", "created"}]."""
    if not settings.backups_dir.exists():
        return []
    found: list[dict[str, Any]] = []
    for path in settings.backups_dir.iterdir():
        match = FILE_RE.match(path.name)
        if match and path.is_file():
            found.append(
                {
                    "file": path.name,
                    "instance": match["instance"],
                    "db": match["db"],
                    "kind": match["kind"] or "manual",
                    "size": path.stat().st_size,
                    "created": path.stat().st_mtime,
                }
            )
    return sorted(found, key=lambda b: b["created"], reverse=True)


def of_instance(name: str, kinds: tuple[str, ...] = KINDS) -> list[dict[str, Any]]:
    """The instance's backups of these kinds, newest first."""
    return [b for b in all_backups() if b["instance"] == name and b["kind"] in kinds]


def latest(name: str) -> dict[str, Any] | None:
    """The newest backup that "Restore the latest backup" would put back, or None."""
    databases = odoo.databases(name)
    return next((b for b in of_instance(name, RESTORABLE) if b["db"] in databases), None)


def remove_daily(name: str) -> None:
    """Delete the instance's daily and before-restore backups (when the instance is deleted)."""
    for backup in of_instance(name, _KEEP_ONE):
        (settings.backups_dir / backup["file"]).unlink(missing_ok=True)


# ---------------------------------------------------------------- daily backups


def daily_slot(now: float | None = None) -> float:
    """The latest time the daily backups were due (DAILY_BACKUP_TIME today, or yesterday if that is still ahead)."""
    zone = settings.zone
    current = datetime.fromtimestamp(time.time() if now is None else now, zone)
    hour, minute = (int(part) for part in settings.daily_backup_time.split(":"))
    slot = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > current:
        slot -= timedelta(days=1)
    return slot.timestamp()


def needs_daily(used_since: float | None, slot: float) -> bool:
    """Whether an instance first used at `used_since` (since its last daily backup) is due for one at `slot`."""
    return used_since is not None and used_since < slot


# ---------------------------------------------------------------- restore


def restore(job: Job, name: str, backup: dict[str, Any]) -> None:
    """Replace the database and filestore of `backup["db"]` with the backup's. Odoo must be stopped."""
    path = path_of(backup["file"])
    if path is None:
        raise JobError("the backup file is gone")
    db = backup["db"]
    container = store.container(name, "db")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "dump.sql" not in names:
            raise JobError("the backup has no dump.sql; it cannot be restored here")
        _database_up(job, name)
        job.write(f"Replacing database {db}\n")
        for sql in (
            f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)',
            f"CREATE DATABASE \"{db}\" OWNER odoo ENCODING 'UTF8' TEMPLATE template0",
        ):
            job.run(["docker", "exec", container, "psql", "-q", "-U", "odoo", "-d", "postgres", "-c", sql], timeout=600)
        with archive.open("dump.sql") as dump:
            _feed(["docker", "exec", "-i", container, "psql", "-q", "-X", "-U", "odoo", "-d", db], dump, "database")
        job.run(["docker", "exec", container, "psql", "-q", "-U", "odoo", "-d", db, "-c", "SELECT 1 FROM res_users"])

        job.write(f"Replacing the filestore of {db}\n")
        with tempfile.TemporaryFile(dir=settings.backups_dir) as tar_file:
            with tarfile.open(fileobj=tar_file, mode="w") as tar:
                for entry in _filestore_entries(archive):
                    info = tarfile.TarInfo(entry.filename.removeprefix("filestore/"))
                    info.size = entry.file_size
                    with archive.open(entry) as data:
                        tar.addfile(info, data)
            tar_file.seek(0)
            target = f"/var/lib/odoo/filestore/{db}"
            script = f"rm -rf {target} && mkdir -p {target} && tar -x -C {target} && chown -R odoo: {target}"
            command = store.compose(name, "run", "--rm", "--no-deps", "-T", "--user", "root", "--entrypoint", "sh")
            _feed([*command, "odoo", "-c", script], tar_file, "filestore")


def _filestore_entries(archive: zipfile.ZipFile) -> Iterator[zipfile.ZipInfo]:
    for entry in archive.infolist():
        inside = entry.filename.removeprefix("filestore/")
        if entry.filename.startswith("filestore/") and not entry.is_dir() and ".." not in inside.split("/"):
            yield entry


def _feed(cmd: list[str], data: IO[bytes], what: str) -> None:
    """Run `cmd` with `data` on its standard input; raise JobError if it fails."""
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors) as proc:
            assert proc.stdin is not None
            try:
                while chunk := data.read(1 << 20):
                    proc.stdin.write(chunk)
            except BrokenPipeError:
                pass
            proc.stdin.close()
            code = proc.wait(timeout=3600)
        errors.seek(0)
        message = errors.read().decode(errors="replace").strip()
    if code != 0:
        raise JobError(f"restoring the {what} failed: {message[-300:]}")


def path_of(file_name: str) -> Path | None:
    """The path of an existing backup, or None. Only names in the listing are accepted, never a path."""
    if not FILE_RE.match(file_name or ""):
        return None
    path = settings.backups_dir / file_name
    return path if path.is_file() else None
