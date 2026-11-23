"""The two ways a database is upgraded. Both run as separate programs in temporary containers; the panel bundles,
copies and imports nothing of them, and only starts them and reads their results.

enterprise (an Enterprise database)
    Odoo S.A.'s upgrade service, used through Odoo's own upgrade script exactly as Odoo documents it for on-premise
    databases ("python <(curl -s https://upgrade.odoo.com/upgrade) test -d <db> -t <target>"). A temporary container
    of the instance's PostgreSQL image, on the instance's database network and with Internet access, installs the
    tools the script needs (python3, rsync, ssh), downloads the script from UPGRADE_SERVICE_URL/upgrade and runs it
    with --no-restore: the script dumps the database, creates the request with the customer's subscription code
    (--contract; the database's own code wins when it has one, as the script decides), uploads the dump, waits for
    Odoo's platform and downloads the upgraded dump and the new files of the filestore into a work folder. The panel
    then loads that dump into the database and merges those files into its filestore. The code goes to the container
    in a file that the container deletes as soon as it has read it; the job's log masks it. The script needs to reach
    the platform on port 443 and on a port between 32768 and 60999.

openupgrade (a Community database)
    OCA OpenUpgrade (AGPL-3.0) in a temporary container of the instance's own Odoo image of the target version (a
    `docker compose run` of the instance): the container downloads OpenUpgrade's unmodified sources from
    OPENUPGRADE_REPOSITORY at the commit OPENUPGRADE_REFS pins for the target, installs openupgradelib
    (OPENUPGRADE_LIB) in its own /tmp, and runs Odoo with OpenUpgrade's framework and scripts on the database
    (`-u all`). Nothing of it stays once the container is gone.

After either, verify() checks that the database says it is of the target version (the base module's version).
"""

from __future__ import annotations

import os
import secrets
import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sudoo.config import settings
from sudoo.core.jobs import Job, JobError
from sudoo.core.shell import capture
from sudoo.instances import backups, odoo, store
from sudoo.repos.gitrepo import own_modules
from sudoo.upgrades import Secret, openupgrade_refs

ODOO_SCRIPT = r"""set -e
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null
  apt-get install -y -qq --no-install-recommends python3 rsync openssh-client curl ca-certificates >/dev/null
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache -q python3 rsync openssh-client curl ca-certificates >/dev/null
fi
echo "Downloading Odoo's upgrade script from $UPGRADE_SERVER_NAME/upgrade"
curl -fsS --proto =https -o /tmp/odoo-upgrade.py "$UPGRADE_SERVER_NAME/upgrade"
contract=$(cat /work/.contract)
rm -f /work/.contract
cd /work
python3 /tmp/odoo-upgrade.py "$@" --contract "$contract"
"""
"""Runs in the temporary container: $@ are the script's arguments (test|production -d <db> -t <target> -x)."""

OPENUPGRADE_SCRIPT = r"""set -e
echo "Downloading OpenUpgrade from $1"
python3 -c '
import sys, tarfile, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=900) as answer:
    archive = tarfile.open(fileobj=answer, mode="r|gz")
    archive.extractall("/tmp/openupgrade-src", **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
' "$1"
mv /tmp/openupgrade-src/* /tmp/openupgrade
echo "Installing $2"
python3 -m pip install --quiet --no-cache-dir --disable-pip-version-check --no-deps --target /tmp/openupgrade-lib "$2"
shift 2
PYTHONPATH=/tmp/openupgrade-lib exec odoo "$@"
"""
"""Runs in the temporary Odoo container: $1 the source archive, $2 openupgradelib, then Odoo's arguments."""

_RESULTS_IGNORED = {"filestore", "upgrade-report.html", "upgrade.log", "origin.dump", ".contract", ".env"}


def _seconds() -> float:
    return settings.upgrade_max_hours * 3600


@contextmanager
def work_folder() -> Iterator[Path]:
    """A private folder for the dumps of one upgrade (on the server's disk next to the backups), removed after."""
    base = settings.odoo_root / "upgrades" / "work"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(dir=base))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def network(name: str) -> str:
    """The instance's database network (Compose project odoo-<name>, network backend)."""
    return f"odoo-{name}_backend"


def db_image(name: str) -> str:
    env = store.read_env(name)
    return env.get("DB_IMAGE_REF") or settings.postgres_image


# ---------------------------------------------------------------- Odoo's upgrade service (Enterprise)


def odoo_service(job: Job, name: str, db: str, target: str, aim: str, code: Secret, work: Path) -> Path:
    """Upgrade database `db` of instance `name` to `target` with Odoo's upgrade service ("test" or "production");
    return the downloaded upgraded dump in `work` (its new filestore files are in work/filestore)."""
    if aim not in ("test", "production"):
        raise JobError(f"unknown aim {aim}")
    if not code:
        raise JobError("Odoo's upgrade service needs the Enterprise subscription code.")
    secret = code.reveal()
    job.mask(secret)
    password = store.read_env(name)["DB_PASSWORD"]
    job.mask(password)
    container = f"sudoo-upgrade-{job.id}-{secrets.token_hex(3)}"
    env_file, contract = work / ".env", work / ".contract"
    for path, text in (
        (env_file, f"PGHOST=db\nPGPORT=5432\nPGUSER=odoo\nPGPASSWORD={password}\n"
                   f"UPGRADE_SERVER_NAME={settings.upgrade_service_url}\n"),  # fmt: skip
        (contract, secret),
    ):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
    job.write(f"Odoo's upgrade service: {aim} upgrade of {db} to Odoo {target}\n")
    create = ["docker", "create", "--name", container, "--network", "bridge", "--env-file", str(env_file),
              "-v", f"{work}:/work", "-w", "/work", "--entrypoint", "sh", db_image(name), "-c", ODOO_SCRIPT, "sh",
              aim, "-d", db, "-t", target, "--no-restore"]  # fmt: skip
    try:
        job.run(create, timeout=300)
        env_file.unlink(missing_ok=True)  # Docker has read it
        job.run(["docker", "network", "connect", network(name), container], timeout=120)
        code_exit = job.run_unchecked(["docker", "start", "-a", container], timeout=_seconds())
    finally:
        contract.unlink(missing_ok=True)
        env_file.unlink(missing_ok=True)
        job.run_unchecked(["docker", "rm", "-f", container], timeout=120)
    result = upgraded_dump(work)
    if code_exit != 0 or result is None:
        raise JobError(
            "Odoo's upgrade service did not return an upgraded database. Its messages are in the server's log (your "
            "administrator can read it); upgrade-report.html and Odoo's support (odoo.com/help) say more about a failed request."
        )
    job.note(f"Odoo's upgrade service returned the database upgraded to Odoo {target}.")
    return result


def upgraded_dump(work: Path) -> Path | None:
    """The upgraded dump the script downloaded into `work` (a file, or a folder in PostgreSQL's directory format)."""
    for entry in sorted(work.iterdir()):
        if entry.name in _RESULTS_IGNORED:
            continue
        if entry.is_dir() and (entry / "toc.dat").exists():
            return entry
        if entry.is_file() and entry.suffix in (".dump", ".sql", ".gz", ".zip"):
            return entry
    return None


def load_dump(job: Job, name: str, db: str, dump: Path) -> None:
    """Replace database `db` of instance `name` with an upgraded dump (Odoo must be stopped)."""
    if not backups.DB_NAME_RE.fullmatch(db):
        raise JobError(f"{db[:70]!r} is not a database name this panel uses")
    container = store.container(name, "db")
    job.write(f"Loading the upgraded database into {db}\n")
    for sql in (
        f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)',
        f"CREATE DATABASE \"{db}\" OWNER odoo ENCODING 'UTF8' TEMPLATE template0",
    ):
        job.run(["docker", "exec", container, "psql", "-q", "-U", "odoo", "-d", "postgres", "-c", sql], timeout=600)
    password = store.read_env(name)["DB_PASSWORD"]
    job.mask(password)
    inside = f"/work/{dump.name}"
    if dump.suffix == ".sql":
        tool = ["psql", "-q", "-X", "-v", "ON_ERROR_STOP=1", "-f", inside]
    else:
        tool = ["pg_restore", "--no-owner", "--exit-on-error", *(["-j", "2"] if dump.is_dir() else []), inside]
    command = ["docker", "run", "--rm", "--network", network(name), "-e", "PGHOST=db", "-e", "PGUSER=odoo",
               "-e", f"PGPASSWORD={password}", "-v", f"{dump.parent}:/work:ro", "--entrypoint", tool[0],
               db_image(name), *tool[1:], "-d", db]  # fmt: skip
    job.run(command, timeout=_seconds())
    files = dump.parent / "filestore"
    if files.is_dir() and any(files.iterdir()):
        merge_filestore(job, name, db, files)


def merge_filestore(job: Job, name: str, db: str, files: Path) -> None:
    """Add the files the upgrade made (work/filestore) to the database's filestore; existing files are kept."""
    job.write(f"Adding the upgrade's new files to the filestore of {db}\n")
    with tempfile.TemporaryFile(dir=files.parent) as tar_file:
        with tarfile.open(fileobj=tar_file, mode="w") as tar:
            for path in sorted(files.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    tar.add(path, arcname=str(path.relative_to(files)), recursive=False)
        tar_file.seek(0)
        script = 'mkdir -p "$1" && tar -x -C "$1" && chown -R odoo: "$1"'
        capabilities = [arg for cap in odoo.FILESTORE_CAPABILITIES for arg in ("--cap-add", cap)]
        command = store.compose(
            name, "run", "--rm", "--no-deps", "-T", "--user", "root", *capabilities, "--entrypoint", "sh"
        )
        backups._feed([*command, "odoo", "-c", script, "sh", f"/var/lib/odoo/filestore/{db}"], tar_file, "filestore")


def own_modules_upgrade(job: Job, name: str, db: str) -> None:
    """Run the repository's installed modules' own upgrade scripts on the upgraded database (`-u`)."""
    modules = sorted(set(own_modules(store.addons_dir(name))) & backups.installed_modules(name, db))
    if not modules:
        return
    job.write(f"Upgrading the repository's modules: {', '.join(modules)}\n")
    command = odoo.run_cmd(name, "-d", db, "-u", ",".join(modules), "--stop-after-init", "--no-http")
    if job.run_unchecked(command, timeout=_seconds()):
        raise JobError("The repository's modules could not be upgraded on the new version; the server's log says why.")


# ---------------------------------------------------------------- OpenUpgrade (Community)


def openupgrade_archive(target: str) -> str:
    """The address of OpenUpgrade's sources at the commit pinned for `target`."""
    commit = openupgrade_refs().get(target)
    if not commit:
        raise JobError(f"No OpenUpgrade commit is set for Odoo {target} (OPENUPGRADE_REFS).")
    return f"{settings.openupgrade_repository}/archive/{commit}.tar.gz"


def openupgrade(job: Job, name: str, db: str, target: str) -> None:
    """Upgrade database `db` of instance `name` (already on the Odoo `target` image, Odoo stopped) with
    OpenUpgrade in a temporary container."""
    archive = openupgrade_archive(target)
    paths = odoo.read_config(name).get("options", "addons_path", fallback="")
    addons_path = ",".join([*(p for p in paths.split(",") if p), "/tmp/openupgrade"])
    job.write(f"OpenUpgrade {archive.rsplit('/', 1)[-1].removesuffix('.tar.gz')} for Odoo {target} on {db}\n")
    command = store.compose(name, "run", "--rm", "--no-deps", "-T", "-e", "PIP_BREAK_SYSTEM_PACKAGES=1",
                            "--entrypoint", "sh", "odoo", "-c", OPENUPGRADE_SCRIPT, "sh", archive,
                            settings.openupgrade_lib, "-c", "/etc/odoo/odoo.conf", "-d", db,
                            "--addons-path", addons_path,
                            "--upgrade-path", "/tmp/openupgrade/openupgrade_scripts/scripts",
                            "--load", "base,web,openupgrade_framework", "-u", "all",
                            "--stop-after-init", "--no-http")  # fmt: skip
    if job.run_unchecked(command, timeout=_seconds()):
        raise JobError(
            "OpenUpgrade could not upgrade the database. Its messages are in the server's log: a module it does not "
            "cover, or a repository module that is not ported yet, is the usual reason."
        )


# ---------------------------------------------------------------- after either


def database_version(name: str, db: str) -> str:
    sql = "SELECT latest_version FROM ir_module_module WHERE name = 'base'"
    return capture(["docker", "exec", store.container(name, "db"), "psql", "-U", "odoo", "-d", db, "-tAc", sql])


def verify(job: Job, name: str, db: str, target: str) -> None:
    """Fail unless database `db` says it is of Odoo `target` (its base module's version)."""
    found = database_version(name, db)
    job.write(f"The database's base module is at version {found or 'unknown'}\n")
    if not found.startswith(f"{target}."):
        raise JobError(f"The database is not at Odoo {target} after the upgrade (base module: {found or 'unknown'}).")
