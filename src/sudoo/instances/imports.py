"""Import database: put an Odoo database dump uploaded from elsewhere (Odoo.sh, another server, Odoo's own database
manager) into an instance.

Accepted files:

    .zip       Odoo's backup format: dump.sql, filestore/, and usually manifest.json
    .dump      pg_dump's custom format (pg_restore), database only
    .sql       a plain SQL dump, database only
    .sql.gz    the same, compressed with gzip

The upload comes in pieces (PUT .../imports/<id>?offset=, CHUNK_MAX each at most), so a large dump survives a lost
connection: the browser asks how much arrived (GET .../imports/<id>) and continues from there. Uploads are kept in
BACKUPS_DIR/.imports/ (an upload's data and a small JSON file about it), each only for its instance and the person
who started it, at most IMPORT_MAX_GB, and removed after UPLOAD_HOURS without a new piece, after the import, or when
cancelled.

Before anything is replaced, the file is checked (check_file): a zip must be a valid archive whose entries all stay
inside it (no absolute paths, no "..", no links: "zip slip"), must contain dump.sql, and must not unpack to more
than UNPACKED_RATIO times IMPORT_MAX_GB. The Odoo version of the dump (manifest.json, or else the version of the base
module recorded in the dump itself, version_in_sql) must be of the instance's major version: a database of another
version is refused, with what to do instead (an upgrade).

The import (import_database, a job) then works like a restore: the instance's current state is backed up first
(before-restore: Rollback is offered for ROLLBACK_HOURS), Odoo stops, the database and the filestore are replaced,
the copy is neutralised unless the person said otherwise (no e-mail, no scheduled actions, no payments: the default
outside production), and Odoo starts with the instance's public address. Users keep their own passwords from the
dump; none is changed (Connect as signs in as any of them, connect_as.py).
"""

from __future__ import annotations

import gzip
import json
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from sudoo.config import settings
from sudoo.core.jobs import Job, JobError
from sudoo.core.shared import SharedLock
from sudoo.instances import backups, odoo, store

FORMATS = (".sql.gz", ".zip", ".dump", ".sql")
"""Accepted endings of the uploaded file's name (longest first)."""
CHUNK_SIZE = 4 * 2**20
"""The size of the pieces the web app sends."""
CHUNK_MAX = 8 * 2**20
"""The largest piece accepted."""
UPLOAD_HOURS = 24
"""An upload without a new piece for this long is removed."""
UNPACKED_RATIO = 10
"""A zip may unpack to at most this many times IMPORT_MAX_GB."""
FREE_SPACE_MARGIN = 2 * 2**30
"""Disk space left free besides the upload itself."""
ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,40}$")
FOLDER = ".imports"
_LOCK = SharedLock("imports")


class ImportProblem(ValueError):
    """The file cannot be imported; the message says why, for the person importing."""


# ---------------------------------------------------------------- uploads


def folder() -> Path:
    found = settings.backups_dir / FOLDER
    found.mkdir(mode=0o700, parents=True, exist_ok=True)
    return found


def max_bytes() -> int:
    return int(settings.import_max_gb * 2**30)


def format_of(file_name: str) -> str | None:
    """The format of an uploaded file, from its name ("zip", "dump", "sql", "sql.gz"), or None."""
    lower = file_name.lower()
    return next((ending[1:] for ending in FORMATS if lower.endswith(ending)), None)


def _meta_path(upload_id: str) -> Path:
    return folder() / f"{upload_id}.json"


def data_path(upload_id: str) -> Path:
    return folder() / f"{upload_id}.part"


def _write_meta(meta: dict[str, Any]) -> None:
    path = _meta_path(meta["id"])
    partial = path.with_suffix(".tmp")
    partial.write_text(json.dumps(meta))
    partial.chmod(0o600)
    partial.replace(path)


def start(instance: str, user: str, file_name: str, size: int) -> dict[str, Any]:
    """A new upload of `size` bytes for `instance`; raises ImportProblem when it cannot be taken."""
    kind = format_of(file_name)
    if kind is None:
        raise ImportProblem("Upload a .zip (Odoo backup with its filestore), .dump, .sql or .sql.gz file.")
    if size <= 0:
        raise ImportProblem("The file is empty.")
    if size > max_bytes():
        raise ImportProblem(f"The file is larger than the {settings.import_max_gb:g} GB this panel accepts.")
    remove_stale()
    free = shutil.disk_usage(folder()).free
    if free < size + FREE_SPACE_MARGIN:
        raise ImportProblem("There is not enough free disk space on the server for this file.")
    meta: dict[str, Any] = {
        "id": secrets.token_urlsafe(18),
        "instance": instance,
        "user": user,
        "file_name": file_name[-200:],
        "format": kind,
        "size": size,
        "received": 0,
        "created": time.time(),
        "touched": time.time(),
    }
    with _LOCK:
        data_path(meta["id"]).touch(mode=0o600)
        _write_meta(meta)
    return meta


def get(upload_id: str, instance: str, user: str) -> dict[str, Any] | None:
    """The upload, when it is for `instance` and was started by `user`; None otherwise."""
    if not ID_RE.match(upload_id or ""):
        return None
    try:
        meta = json.loads(_meta_path(upload_id).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("instance") != instance or meta.get("user") != user:
        return None
    return meta


def append(meta: dict[str, Any], offset: int, chunk: bytes) -> dict[str, Any]:
    """Add a piece at `offset`; raises ImportProblem when it is not where the upload stands, or too much."""
    with _LOCK:
        current = json.loads(_meta_path(meta["id"]).read_text())
        if offset != current["received"]:
            raise ImportProblem(f"Expected the piece at {current['received']}.")
        if not chunk or len(chunk) > CHUNK_MAX:
            raise ImportProblem("A piece must not be empty or larger than 8 MB.")
        if current["received"] + len(chunk) > current["size"]:
            raise ImportProblem("The pieces are larger than the file announced.")
        with open(data_path(meta["id"]), "r+b") as out:
            out.seek(offset)
            out.write(chunk)
            out.truncate()
        current["received"] += len(chunk)
        current["touched"] = time.time()
        _write_meta(current)
    return current


def remove(upload_id: str) -> None:
    if ID_RE.match(upload_id or ""):
        for path in (_meta_path(upload_id), data_path(upload_id)):
            path.unlink(missing_ok=True)


def remove_stale(now: float | None = None) -> None:
    """Remove uploads without a new piece for UPLOAD_HOURS (and stray files)."""
    cutoff = (time.time() if now is None else now) - UPLOAD_HOURS * 3600
    for path in folder().iterdir():
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def public(meta: dict[str, Any]) -> dict[str, Any]:
    """An upload as the API shows it."""
    return {
        "id": meta["id"],
        "file_name": meta["file_name"],
        "format": meta["format"],
        "size": meta["size"],
        "received": meta["received"],
        "complete": meta["received"] == meta["size"],
        "chunk_size": CHUNK_SIZE,
    }


# ---------------------------------------------------------------- checking the file


def major(version: str | None) -> int | None:
    found = backups.series(version)
    return int(found.split(".")[0]) if found else None


def check_version(found: str | None, instance_version: str) -> None:
    """Refuse a dump of another major Odoo version than the instance's."""
    if found is None:
        return
    if major(found) != major(instance_version):
        raise ImportProblem(
            f"This dump is of Odoo {backups.series(found)}, and this instance runs Odoo "
            f"{backups.series(instance_version) or instance_version}. Import it into an instance of Odoo "
            f"{backups.series(found)}, then upgrade that instance to a newer version if you need one."
        )


def unsafe_member(info: zipfile.ZipInfo) -> bool:
    """An entry that would land outside the folder it is unpacked into, or is a link (zip slip)."""
    name = info.filename
    if not name or "\x00" in name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return True
    if ".." in name.split("/"):
        return True
    return stat.S_ISLNK(info.external_attr >> 16)


def check_zip(path: Path, instance_version: str) -> dict[str, Any]:
    """Check an uploaded Odoo backup; {"version": the version its manifest gives, or None}."""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise ImportProblem("This is not a valid zip file.") from error
    with archive:
        entries = archive.infolist()
        bad = next((info.filename for info in entries if unsafe_member(info)), None)
        if bad is not None:
            raise ImportProblem(f"The archive contains an unsafe path ({bad[:80]!r}); it is refused.")
        names = {info.filename for info in entries}
        if "dump.sql" not in names:
            raise ImportProblem("The archive has no dump.sql: export it from Odoo's database manager (with filestore).")
        if sum(info.file_size for info in entries) > UNPACKED_RATIO * max_bytes():
            raise ImportProblem("The archive unpacks to far more than this panel accepts.")
        version = None
        if "manifest.json" in names:
            try:
                with archive.open("manifest.json") as raw:
                    manifest = json.load(raw)
            except (ValueError, OSError, zipfile.BadZipFile) as error:
                raise ImportProblem("The archive's manifest.json cannot be read.") from error
            if isinstance(manifest, dict):
                found = manifest.get("version") or manifest.get("major_version")
                version = str(found) if isinstance(found, str | int | float) else None
        check_version(version, instance_version)
        return {"version": version}


def check_file(meta: dict[str, Any], instance_version: str) -> dict[str, Any]:
    """The quick checks before an import starts (the version of a dump without manifest is checked in the job)."""
    if meta["received"] != meta["size"]:
        raise ImportProblem("The upload is not complete yet.")
    path = data_path(meta["id"])
    if meta["format"] == "zip":
        return check_zip(path, instance_version)
    with open(path, "rb") as head:
        start_bytes = head.read(5)
    if meta["format"] == "dump" and start_bytes != b"PGDMP":
        raise ImportProblem("This is not a pg_dump custom-format file (.dump).")
    if meta["format"] == "sql.gz" and not start_bytes.startswith(b"\x1f\x8b"):
        raise ImportProblem("This is not a gzip-compressed file (.sql.gz).")
    return {"version": None}


_COPY_RE = re.compile(r"^COPY (?:public\.)?\"?ir_module_module\"? \((?P<columns>[^)]*)\) FROM stdin;")


def version_in_sql(lines: Iterable[str]) -> str | None:
    """The version of the base module in a plain SQL dump (the rows of ir_module_module), e.g. "17.0.1.3"; None when
    the dump has none. Reads only as far as it needs."""
    columns: list[str] | None = None
    for line in lines:
        if columns is None:
            match = _COPY_RE.match(line)
            if match:
                columns = [c.strip().strip('"') for c in match["columns"].split(",")]
                if "name" not in columns or "latest_version" not in columns:
                    return None
            continue
        if line.startswith("\\."):
            return None
        values = line.rstrip("\n").split("\t")
        if len(values) == len(columns) and values[columns.index("name")] == "base":
            found = values[columns.index("latest_version")]
            return None if found == "\\N" else found
    return None


def _text_lines(raw: Iterable[bytes]) -> Iterator[str]:
    for line in raw:
        yield line.decode("utf-8", errors="replace")


def dump_version(name: str, meta: dict[str, Any]) -> str | None:
    """The Odoo version recorded in the uploaded dump (its base module), read from the dump itself."""
    path = data_path(meta["id"])
    kind = meta["format"]
    if kind == "zip":
        with zipfile.ZipFile(path) as archive, archive.open("dump.sql") as raw:
            return version_in_sql(_text_lines(raw))
    if kind == "sql":
        with open(path, "rb") as raw:
            return version_in_sql(_text_lines(raw))
    if kind == "sql.gz":
        try:
            with gzip.open(path, "rb") as raw:
                return version_in_sql(_text_lines(raw))
        except (OSError, EOFError) as error:
            raise JobError("The .sql.gz file is damaged.") from error
    # custom format: the data of that one table, as SQL, from pg_restore in the database container
    cmd = ["docker", "exec", "-i", store.container(name, "db"), "pg_restore", "-a", "-t", "ir_module_module", "-f", "-"]
    with open(path, "rb") as raw:
        result = subprocess.run(cmd, stdin=raw, capture_output=True, timeout=1800)
    if result.returncode != 0:
        raise JobError("pg_restore cannot read this .dump file: " + result.stderr.decode(errors="replace")[-200:])
    return version_in_sql(result.stdout.decode(errors="replace").splitlines(keepends=True))


# ---------------------------------------------------------------- the import


def import_database(job: Job, name: str, upload_id: str, user: str, neutralize: bool) -> None:
    """Replace the instance's database (and filestore) with the uploaded dump; see the module's description."""
    from sudoo.instances import connect_as, operations

    try:
        meta = get(upload_id, name, user)
        if meta is None:
            raise JobError("The uploaded file is gone. Upload it again.")
        env = store.read_env(name)
        db = odoo.databases(name)[0]
        job.expect(6 + neutralize)
        job.step("Checking the file")
        backups._database_up(job, name)  # a .dump is read by the database container's pg_restore
        try:
            found = check_file(meta, env["ODOO_VERSION"])["version"] or dump_version(name, meta)
            if found is None:
                raise ImportProblem("This dump has no Odoo database in it (no installed base module was found).")
            check_version(found, env["ODOO_VERSION"])
        except ImportProblem as problem:
            raise JobError(str(problem)) from problem
        job.note(f"The dump is of Odoo {backups.series(found) or found}.")
        job.step("Saving the current state first")
        saved = backups.create(job, name, "before-restore")
        job.note(f"The state before the import is kept for {settings.rollback_hours:g} hours as "
                 f"{', '.join(p.name for p in saved)}")  # fmt: skip
        job.step("Stopping Odoo")
        job.run(store.compose(name, "stop", "odoo"), timeout=300)
        job.step(f"Importing {meta['file_name']}")
        _load(job, name, db, meta)
        if neutralize:
            job.step("Neutralising the imported data")
            job.note(backups.neutralize(job, name, db))
        else:
            job.note("The imported data was not neutralised: it may send e-mail and run scheduled actions.", "warning")
        job.note("Users keep their passwords from the dump. Use Connect as to sign in as any of them.")
        connect_as.forget(name)
        operations._finish_restore(job, name)
    finally:
        remove(upload_id)


def _load(job: Job, name: str, db: str, meta: dict[str, Any]) -> None:
    path = data_path(meta["id"])
    kind = meta["format"]
    backups.recreate_database(job, name, db)
    if kind == "zip":
        with zipfile.ZipFile(path) as archive:
            with archive.open("dump.sql") as dump:
                backups.load_sql(name, db, dump)
            backups.check_database(job, name, db)
            backups.replace_filestore(job, name, db, archive)
        return
    if kind == "dump":
        container = store.container(name, "db")
        with open(path, "rb") as dump, tempfile.TemporaryFile() as errors:
            code = subprocess.run(
                ["docker", "exec", "-i", container, "pg_restore", "--no-owner", "--no-privileges", "-U", "odoo",
                 "-d", db],  # fmt: skip
                stdin=dump,
                stdout=subprocess.DEVNULL,
                stderr=errors,
                timeout=6 * 3600,
            ).returncode
            if code != 0:  # pg_restore also reports what it skipped (extensions, roles): the check below decides
                errors.seek(0)
                job.write(errors.read().decode(errors="replace")[-2000:] + "\n")
    else:
        opener = gzip.open if kind == "sql.gz" else open
        with opener(path, "rb") as dump:
            backups.load_sql(name, db, dump)  # type: ignore[arg-type]
    backups.check_database(job, name, db)
    backups.replace_filestore(job, name, db, None)
    job.note("The dump has no filestore: attachments and images stored as files are not part of it.", "warning")
