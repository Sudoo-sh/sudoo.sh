"""Backups of instances, in Odoo's own backup format, kept in BACKUPS_DIR.

One zip file per database, named <instance>__<database>__<YYYYMMDD-HHMMSS>__<kind>.zip, containing what Odoo's
database manager produces and accepts under "Restore":

    dump.sql        the database, as SQL (pg_dump --no-owner)
    filestore/      attachments and images
    manifest.json   what the backup is (database, Odoo version, edition, and the instance it was taken from:
                    its name and INSTANCE_ID)

Kinds (files of earlier versions have no kind and count as "manual"):

    manual          "Back up now"; kept until deleted by hand
    daily           once a day (DAILY_BACKUP_TIME in TIMEZONE) for each instance used since its last daily backup;
                    each instance keeps only its latest one, and they go when the instance is deleted
    deletion        taken before an instance is deleted; kept
    before-restore  the state an instance had before a restore; each instance keeps only its latest one, for
                    ROLLBACK_HOURS: while it exists the instance offers "Rollback" (see rollback_snapshot)
    before-rollback the state an instance had before a rollback; kept like before-restore, so a rollback can be
                    undone by hand, but never offered as a rollback itself (a rollback never replaces the snapshot
                    it restores)
    before-repair   taken before the panel recreated a database container that was changed by hand
                    (integrity.py); each instance keeps only its latest one

The database container is started for a backup if the instance is paused; the instance's state is not changed
otherwise. restore() puts a backup back: the database is replaced and so is the filestore. A backup can be put into
another instance (or a new one) of the same Odoo series: it is loaded under the target's database name and then
neutralised (neutralize()), so the copy sends no e-mail and runs no scheduled actions of the original.

A backup is an instance's own only when it was taken from that very instance (belongs_to), not from an earlier,
deleted instance of the same name: the file name gives the instance's name, the manifest its INSTANCE_ID.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid
import zipfile
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO, Any, cast

from sudoo.config import settings
from sudoo.core.cache import FileMemo
from sudoo.core.jobs import Job, JobError
from sudoo.core.shell import capture
from sudoo.core.state import STATE
from sudoo.instances import odoo, store

FILE_RE = re.compile(
    r"^(?P<instance>[a-z0-9][a-z0-9-]{0,30})__(?P<db>[a-z0-9_]{1,63})__(?P<stamp>\d{8}-\d{6})"
    r"(?:__(?P<kind>manual|daily|deletion|before-restore|before-rollback|before-repair))?\.zip$"
)
DB_NAME_RE = re.compile(r"[a-z0-9_]{1,63}")  # the database names the panel gives (and FILE_RE accepts)
KINDS = ("manual", "daily", "deletion", "before-restore", "before-rollback", "before-repair")
RESTORABLE = ("manual", "daily", "deletion")  # what "Restore the latest backup" chooses from
SNAPSHOTS = ("before-restore", "before-rollback")  # safety copies, removed ROLLBACK_HOURS after they were taken
_KEEP_ONE = ("daily", "before-repair", *SNAPSHOTS)  # kinds of which each instance keeps only the latest


def create(job: Job, name: str, kind: str = "manual") -> list[Path]:
    """Back up every database of instance `name`; return the paths of the backup files."""
    settings.backups_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _database_up(job, name)
    env = store.read_env(name)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    facts = {
        "version": env["ODOO_VERSION"],
        "edition": env.get("EDITION", "community"),
        "instance_id": env.get("INSTANCE_ID", ""),
    }
    saved = [_backup_database(job, name, db, facts, stamp, kind) for db in odoo.databases(name)]
    if kind in _KEEP_ONE:
        for older in of_instance(name, (kind,)):
            if older["file"] not in {path.name for path in saved}:
                (settings.backups_dir / older["file"]).unlink(missing_ok=True)
                job.write(f"Removed the previous {kind} backup {older['file']}\n")
    return saved


def _database_up(job: Job, name: str) -> None:
    # --no-recreate: a backup starts the database as it is; containers change only when the instance starts
    job.run(store.compose(name, "up", "-d", "--no-recreate", "db"), timeout=300)
    if not odoo.wait_for_database(name, timeout=60):
        raise JobError("the database did not start")


def _backup_database(job: Job, name: str, db: str, facts: dict[str, str], stamp: str, kind: str) -> Path:
    """Back up database `db` of instance `name`; `facts`: its "version", "edition" and "instance_id"."""
    if not DB_NAME_RE.fullmatch(db):  # it becomes part of the backup's file name
        raise JobError(f"{db[:70]!r} is not a database name this panel uses")
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
                        "version": facts["version"],
                        "major_version": facts["version"],
                        "created_by": "Sudoo",
                        "instance": name,
                        "edition": facts["edition"],
                        **({"instance_id": facts["instance_id"]} if facts.get("instance_id") else {}),
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


def _list_backups(folder: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    try:
        paths = list(folder.iterdir())
    except FileNotFoundError:
        return found
    for path in paths:
        match = FILE_RE.match(path.name)
        if not match:
            continue
        try:
            facts = path.stat()
        except FileNotFoundError:  # removed meanwhile
            continue
        if stat.S_ISREG(facts.st_mode):
            found.append(
                {
                    "file": path.name,
                    "instance": match["instance"],
                    "db": match["db"],
                    "stamp": match["stamp"],
                    "kind": match["kind"] or "manual",
                    "size": facts.st_size,
                    "created": facts.st_mtime,
                }
            )
    return sorted(found, key=lambda b: b["created"], reverse=True)


# A backup file is written under another name and renamed when complete, and removed by unlinking it: both change
# the folder itself, so its signature says when the list must be read again (core/cache.py).
_LISTING = FileMemo(_list_backups, size=4)


def all_backups() -> list[dict[str, Any]]:
    """Every backup file, newest first: [{"file", "instance", "db", "stamp", "kind", "size", "created"}]."""
    return [dict(backup) for backup in _LISTING.get(settings.backups_dir)]


def of_instance(name: str, kinds: tuple[str, ...] = KINDS) -> list[dict[str, Any]]:
    """The instance's backups of these kinds, newest first."""
    return [b for b in all_backups() if b["instance"] == name and b["kind"] in kinds]


def belongs_to(name: str, backup: dict[str, Any]) -> bool:
    """Whether `backup` was taken from the instance `name` that exists now, not from another one or from an earlier,
    deleted instance of the same name.

    Both carry INSTANCE_ID (recorded at creation, and in each backup's manifest): they must be the same. A backup
    without one (taken before it was recorded) is the instance's own when it was taken after the instance was
    created; one with an id is never the own backup of an instance without one."""
    if backup["instance"] != name:
        return False
    env = store.read_env(name)
    own, taken_from = env.get("INSTANCE_ID", ""), instance_id(backup["file"])
    if taken_from:
        return taken_from == own
    try:
        created = float(env.get("CREATED_AT") or "inf")
    except ValueError:
        return False
    return float(backup["created"]) >= created


def own_backups(name: str, kinds: tuple[str, ...] = KINDS) -> list[dict[str, Any]]:
    """The backups of these kinds taken from the instance `name` that exists now (belongs_to), newest first."""
    return [b for b in of_instance(name, kinds) if belongs_to(name, b)]


def latest(name: str) -> dict[str, Any] | None:
    """The newest backup that "Restore the latest backup" would put back, or None: one of the instance's own."""
    databases = odoo.databases(name)
    return next((b for b in own_backups(name, RESTORABLE) if b["db"] in databases), None)


def remove_daily(name: str) -> None:
    """Delete the instance's daily and safety (before-restore, before-rollback) backups (when it is deleted)."""
    for backup in of_instance(name, _KEEP_ONE):
        (settings.backups_dir / backup["file"]).unlink(missing_ok=True)


# ---------------------------------------------------------------- what a backup contains

_DETAILS: dict[tuple[str, float], dict[str, str | None]] = {}  # (file, mtime) -> manifest; files never change


def details(file_name: str) -> dict[str, str | None]:
    """{"version", "series", "edition"} read from the backup's manifest.json; None for what it does not say.

    "series" is the Odoo release the database belongs to ("17.0", "saas~17.1" gives "17.1"): a database can only be
    opened by Odoo of the same series. "edition" is only recorded by this version of Sudoo.sh on.
    """
    found = _manifest(file_name)
    return {key: found.get(key) for key in ("version", "series", "edition")}


def instance_id(file_name: str) -> str | None:
    """The INSTANCE_ID of the instance the backup was taken from (manifest.json), or None for backups taken before
    it was recorded."""
    return _manifest(file_name).get("instance_id")


def _manifest(file_name: str) -> dict[str, str | None]:
    path = path_of(file_name)
    if path is None:
        return {"version": None, "series": None, "edition": None, "instance_id": None}
    key = (path.name, path.stat().st_mtime)
    if key not in _DETAILS:
        manifest: dict[str, Any] = {}
        try:
            with zipfile.ZipFile(path) as archive, archive.open("manifest.json") as raw:
                loaded = json.load(raw)
            manifest = loaded if isinstance(loaded, dict) else {}
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            pass
        version = manifest.get("version") or manifest.get("major_version")
        version = str(version) if isinstance(version, str | int | float) else None
        edition = manifest.get("edition")
        _DETAILS[key] = {
            "version": version,
            "series": series(version),
            "edition": edition if edition in ("community", "enterprise") else None,
            "instance_id": manifest["instance_id"] if isinstance(manifest.get("instance_id"), str) else None,
        }
    return _DETAILS[key]


def series(version: str | None) -> str | None:
    """The Odoo series of a version string: "17.0" for "17.0", "17.0+e" or "17.0-20240101", "17.1" for
    "saas~17.1"; None when it is not a version."""
    match = re.match(r"^(?:saas~)?(\d{1,3})\.(\d{1,2})", (version or "").strip())
    return f"{int(match[1])}.{int(match[2])}" if match else None


def problem(file_name: str, version: str, edition: str) -> str | None:
    """Why the backup cannot be put into an instance of Odoo `version` and `edition`, or None when it can."""
    found = details(file_name)
    if found["series"] is None:
        return "This backup does not say which Odoo version it is of (its manifest.json is missing or unreadable)."
    if found["series"] != series(version):
        return f"This backup is of Odoo {found['series']}; it can only go into an Odoo {found['series']} instance."
    if found["edition"] == "enterprise" and edition != "enterprise":
        return "This backup uses Odoo Enterprise; it can only go into an Enterprise instance."
    return None


# ---------------------------------------------------------------- rollback


def rollback_snapshot(name: str, now: float | None = None) -> list[dict[str, Any]]:
    """The before-restore backup(s) a rollback puts back: the newest of the instance (one file per database, all
    with the same stamp), if taken less than ROLLBACK_HOURS ago and the instance was not rolled back to it already
    (STATE "rolled_back"); [] otherwise. Going back to it once more is still possible by restoring the file."""
    found = own_backups(name, ("before-restore",))
    if not found or found[0]["created"] < (time.time() if now is None else now) - settings.rollback_hours * 3600:
        return []
    if STATE.get(name).get("rolled_back") == found[0]["file"]:
        return []
    return [b for b in found if b["stamp"] == found[0]["stamp"]]


def rollback_until(name: str) -> float | None:
    """When the rollback of the instance stops being offered (a timestamp), or None when there is none."""
    snapshot = rollback_snapshot(name)
    return snapshot[0]["created"] + settings.rollback_hours * 3600 if snapshot else None


def expired_snapshots(now: float | None = None) -> list[dict[str, Any]]:
    """Safety backups (before-restore, before-rollback) older than ROLLBACK_HOURS."""
    cutoff = (time.time() if now is None else now) - settings.rollback_hours * 3600
    return [b for b in all_backups() if b["kind"] in SNAPSHOTS and b["created"] < cutoff]


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


def restore(job: Job, name: str, backup: dict[str, Any], db: str | None = None) -> None:
    """Replace the database `db` of instance `name` (the backup's own database by default) and its filestore with
    the backup's. Odoo must be stopped. The dump is plain SQL without database name or owner, so it loads into a
    database of any name."""
    path = path_of(backup["file"])
    if path is None:
        raise JobError("the backup file is gone")
    db = db or backup["db"]
    if not DB_NAME_RE.fullmatch(db):  # it goes into SQL and into a path below: only names the panel gives
        raise JobError(f"{db[:70]!r} is not a database name this panel uses")
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
                    info.mtime = int(time.mktime((*entry.date_time, 0, 0, -1)))  # not 1970
                    with archive.open(entry) as data:
                        tar.addfile(info, data)
            tar_file.seek(0)
            # the folder is the script's argument ($1), never part of the script's text
            script = 'rm -rf "$1" && mkdir -p "$1" && tar -x -C "$1" && chown -R odoo: "$1"'
            target = f"/var/lib/odoo/filestore/{db}"
            # root in a short-lived container, with only what replacing files owned by Odoo's user needs (the
            # instance's containers drop every capability otherwise)
            capabilities = [arg for cap in odoo.FILESTORE_CAPABILITIES for arg in ("--cap-add", cap)]
            command = store.compose(
                name, "run", "--rm", "--no-deps", "-T", "--user", "root", *capabilities, "--entrypoint", "sh"
            )
            _feed([*command, "odoo", "-c", script, "sh", target], tar_file, "filestore")


_NEUTRALIZE_SQL = (
    # what Odoo's own "neutralize" does for the base modules, for when that command is not there (Odoo 15 and
    # earlier) or fails: no outgoing or incoming mail servers, no scheduled actions except the database cleanup
    "UPDATE ir_mail_server SET active = false;"
    "UPDATE ir_cron SET active = false WHERE id NOT IN (SELECT res_id FROM ir_model_data "
    "WHERE model = 'ir.cron' AND module = 'base' AND name = 'autovacuum_job');"
    "DO $$ BEGIN IF to_regclass('fetchmail_server') IS NOT NULL THEN "
    "UPDATE fetchmail_server SET active = false; END IF; END $$;"
)


def neutralize(job: Job, name: str, db: str) -> str:
    """Make database `db` of instance `name`, a copy of another instance's, harmless: no e-mail goes out, no
    scheduled action runs, no mail is fetched, and the copy gets its own identity (database.uuid, database.secret)
    so it is not taken for the original by Odoo's services and old sessions do not work. Returns a sentence for
    the job's notes."""
    version = series(store.read_env(name).get("ODOO_VERSION")) or "0.0"
    container = store.container(name, "db")
    how = ""
    if int(version.split(".")[0]) >= 16:  # "odoo neutralize" exists since Odoo 16 and knows every module's needs
        if job.run_unchecked(odoo.run_cmd(name, "neutralize", "-d", db), timeout=1800) == 0:
            how = "Odoo's neutralize command"
        else:
            job.write("odoo neutralize failed; switching off mail servers and scheduled actions directly\n")
    sql = odoo.parameter_sql({"database.uuid": str(uuid.uuid4()), "database.secret": str(uuid.uuid4())})
    if not how:
        sql = _NEUTRALIZE_SQL + odoo.parameter_sql({"database.is_neutralized": "True"}) + sql
        how = "switching off its mail servers and scheduled actions"
    job.run(
        ["docker", "exec", container, "psql", "-q", "-X", "-v", "ON_ERROR_STOP=1", "-U", "odoo", "-d", db, "-c", sql]
    )
    return (
        f"The data comes from another instance, so it was neutralised ({how}): it sends no e-mail and runs no "
        "scheduled actions until you switch them back on in Odoo."
    )


def installed_modules(name: str, db: str) -> set[str]:
    """Modules installed in database `db` of instance `name` (its database container must be running)."""
    sql = "SELECT name FROM ir_module_module WHERE state = 'installed'"
    found = capture(["docker", "exec", store.container(name, "db"), "psql", "-U", "odoo", "-d", db, "-tAc", sql])
    return {line.strip() for line in found.splitlines() if line.strip()}


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
