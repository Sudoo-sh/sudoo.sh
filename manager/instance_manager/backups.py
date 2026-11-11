"""Backups of instances, in Odoo's own backup format, kept in BACKUPS_DIR.

One zip file per database, named <instance>__<database>__<YYYYMMDD-HHMMSS>.zip, containing what Odoo's database
manager produces and accepts under "Restore":

    dump.sql        the database, as SQL (pg_dump --no-owner)
    filestore/      attachments and images
    manifest.json   what the backup is (database, Odoo version)

A backup is taken before every deletion (by hand or automatic) and on request. The database container is started
for the backup if the instance is paused; the instance's state is not changed otherwise.
"""
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

from . import instances, odoo, settings
from .jobs import JobError

FILE_RE = re.compile(r"^(?P<instance>[a-z0-9][a-z0-9-]{0,30})__(?P<db>[a-z0-9_]{1,63})__(?P<stamp>\d{8}-\d{6})\.zip$")


def create(job, name):
    """Back up every database of instance `name`; return the paths of the backup files."""
    settings.BACKUPS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    job.run(instances.compose(name, "up", "-d", "db"), timeout=300)
    if not odoo.wait_for_database(name, timeout=60):
        raise JobError("the database did not start, so no backup could be taken")
    version = instances.read_env(name)["ODOO_VERSION"]
    return [_backup_database(job, name, db, version) for db in odoo.databases(name)]


def _backup_database(job, name, db, version):
    target = settings.BACKUPS_DIR / f"{name}__{db}__{time.strftime('%Y%m%d-%H%M%S')}.zip"
    job.write(f"Backing up database {db} to {target}\n")
    with tempfile.TemporaryDirectory(dir=settings.BACKUPS_DIR) as tmp:
        dump, files = Path(tmp) / "dump.sql", Path(tmp) / "filestore.tar"
        _run_to_file(["docker", "exec", instances.container(name, "db"), "pg_dump", "-U", "odoo", "--no-owner", db],
                     dump, "database dump")
        # docker cp also works while Odoo is stopped; "-" writes the folder as a tar stream
        _run_to_file(["docker", "cp", f"{instances.container(name, 'odoo')}:/var/lib/odoo/filestore/{db}", "-"],
                     files, "file storage copy")
        partial = target.with_name(target.name + ".part")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(dump, "dump.sql")
            with tarfile.open(files) as tar:
                for member in tar:
                    inside = member.name.split("/", 1)[1] if "/" in member.name else ""
                    if member.isfile() and inside:
                        archive.writestr(f"filestore/{inside}", tar.extractfile(member).read())
            archive.writestr("manifest.json", json.dumps({
                "odoo_dump": "1", "db_name": db, "version": version, "major_version": version,
                "created_by": "Odoo Instance Manager", "instance": name}, indent=2))
        os.chmod(partial, 0o600)
        partial.replace(target)
    job.write(f"Backup written: {target.name} ({target.stat().st_size // 1024} KB)\n")
    return target


def _run_to_file(cmd, path, what):
    with open(path, "wb") as out:
        result = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, timeout=3600)
    if result.returncode != 0:
        raise JobError(f"{what} failed: {result.stderr.decode(errors='replace').strip()[-300:]}")


def all_backups():
    """Every backup file, newest first: [{"file", "instance", "db", "size", "created"}]."""
    if not settings.BACKUPS_DIR.exists():
        return []
    found = []
    for path in settings.BACKUPS_DIR.iterdir():
        match = FILE_RE.match(path.name)
        if match and path.is_file():
            found.append({"file": path.name, "instance": match["instance"], "db": match["db"],
                          "size": path.stat().st_size, "created": path.stat().st_mtime})
    return sorted(found, key=lambda b: b["created"], reverse=True)


def path_of(file_name):
    """The path of an existing backup, or None. Only names in the listing are accepted, never a path."""
    if not FILE_RE.match(file_name or ""):
        return None
    path = settings.BACKUPS_DIR / file_name
    return path if path.is_file() else None
