"""What the panel can do with an instance. Every function here runs as a job (see jobs.py): fn(job, ...).

Each function is one straight sequence of steps; a failing step raises JobError and the job stops there.
"""

from __future__ import annotations

import re
import secrets
import shutil
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sudoo import addons as addons_sources
from sudoo import exposure
from sudoo.config import settings
from sudoo.core import events, vault
from sudoo.core.hints import panel_hint, specific_hint
from sudoo.core.jobs import Job, JobError
from sudoo.core.odoo_args import demo_args
from sudoo.core.shell import capture
from sudoo.core.state import STATE
from sudoo.instances import (
    backups,
    disk_use,
    gateway,
    integrity,
    managed_names,
    monitor,
    odoo,
    resources,
    stages,
    store,
)
from sudoo.instances.store import compose
from sudoo.mailcatcher import panel as mail_catcher
from sudoo.repos import github
from sudoo.repos.gitrepo import branch_on_github, changed_modules, git, module_folders, own_modules, snapshot

# --------------------------------------------------------------------------- helpers


def refresh_addons_path(job: Job, name: str) -> None:
    """Point Odoo's addons_path at every folder of the repository that contains modules, then Enterprise.

    The instance's own modules come first, so one of them can replace an Enterprise module of the same name.
    """
    addons = store.addons_dir(name)
    paths = [
        odoo.CUSTOM_ADDONS_MOUNT if folder == addons else f"{odoo.CUSTOM_ADDONS_MOUNT}/{folder.relative_to(addons)}"
        for folder in module_folders(addons)
    ]
    if store.read_env(name)["EDITION"] == "enterprise":
        paths.append(odoo.ENTERPRISE_ADDONS_MOUNT)
    job.write(f"addons_path = {odoo.set_addons_path(name, paths)}\n")


def _remove_leftovers(job: Job, name: str) -> None:
    """Remove containers and volumes of an earlier instance with the same name, so nothing old is reused."""
    label = f"label=com.docker.compose.project=odoo-{name}"
    containers = capture(["docker", "ps", "-aq", "--filter", label]).split()
    volumes = capture(["docker", "volume", "ls", "-q", "--filter", label]).split()
    if containers:
        job.run(["docker", "rm", "-f", *containers])
    if volumes:
        job.run(["docker", "volume", "rm", "-f", *volumes])


def _paused(name: str) -> bool:
    return not monitor.STATUS.get(name, {}).get("running") and bool(
        STATE.get(name).get("paused") or STATE.get(name).get("switched_off")
    )


def _wait_until_ready(job: Job, name: str, strict: bool = False) -> None:
    """Wait until Odoo answers. When it does not in time: a warning, or with `strict` a failed step."""
    if odoo.wait_until_ready(store.read_env(name)["ODOO_PORT"]):
        monitor.mark_running(name)
    elif strict:
        raise JobError("Odoo did not answer after it was started")
    else:
        job.note(
            "Odoo is taking longer than usual to answer. It may still be starting; check the logs if it does "
            "not come up in a few minutes.",
            "warning",
        )


def _start_odoo(job: Job, name: str, restart: bool = False, strict: bool = False) -> None:
    """Start the instance (database and Odoo), restart Odoo if asked, and wait until Odoo answers (with `strict`,
    the step fails when it does not in time).

    Its connector is started too, in case it was stopped, so the instance keeps its public link.
    """
    refresh_addons_path(job, name)
    resources.write_conf(name)  # workers and limits in odoo.conf, as the compose file gets them
    mail_catcher.prepare(name)  # staging and development: e-mail goes to the mail catcher
    integrity.apply(job, name)  # current settings and hardening; Compose recreates what changed
    exposure.ensure(name)
    job.run(compose(name, "up", "-d", "--remove-orphans"), timeout=900)
    if restart:
        job.run(compose(name, "restart", "odoo"), timeout=300)
    _catch_mail(job, name)
    STATE.update(name, paused=False, switched_off=False)
    STATE.record_visit(name)
    job.step("Waiting for Odoo to be ready")
    _wait_until_ready(job, name, strict)


def _catch_mail(job: Job, name: str) -> None:
    """Staging and development: the mail catcher on the instance's network, its own mail servers switched off."""
    if not mail_catcher.catches(store.read_env(name)):
        mail_catcher.detach(name)
        return
    odoo.wait_for_database(name)
    switched_off = mail_catcher.after_start(name)
    if switched_off:
        job.note(
            f"{switched_off} outgoing mail {'server was' if switched_off == 1 else 'servers were'} switched off: a "
            "staging or development instance sends its e-mail to the mail catcher (the Mail tab), never to real "
            "addresses.",
            "warning",
        )


# --------------------------------------------------------------------------- create and delete


def create(job: Job, plan: dict[str, Any]) -> None:
    """Create an instance from `plan` (see web/api_instances.py). On failure everything created is removed."""
    name, folder = plan["name"], store.folder(plan["name"])
    try:
        folder.mkdir(parents=True)  # claims the name; the cleanup below never touches another instance
    except FileExistsError as error:
        store.release_ports(plan["ports"])
        raise JobError("An instance with this name already exists. Choose another name.") from error
    try:
        _create(job, plan)
    except Exception:
        job.write("\nRemoving the half-created instance so the same name can be used again.\n")
        compose_file, env_file = folder / "docker-compose.yml", folder / ".env"

        def containers() -> None:
            if compose_file.exists():
                mail_catcher.detach(name)  # Docker keeps a network something is connected to
                job.run_unchecked(compose(name, "down", "-v", "--remove-orphans"), timeout=300)

        def link() -> None:
            if env_file.exists():  # they need its ports, in .env (written after the images and the code)
                gateway.remove(name)
                exposure.remove(name)

        undo: list[tuple[str, Callable[[], object]]] = [
            ("stop its containers", containers),
            ("remove its gateway and public link", link),
            ("remove its folder", lambda: shutil.rmtree(folder, ignore_errors=True)),
            ("forget its state", lambda: STATE.remove(name)),
            ("forget its configuration", lambda: integrity.forget(name)),
            ("remove its caught e-mails", lambda: mail_catcher.forget(name)),
        ]
        for what, step in undo:  # each one on its own: a failing one must not keep the others or the error away
            try:
                step()
            except Exception as error:  # noqa: BLE001 - reported in the log, the creation's own error follows
                job.write(f"Could not {what}: {error!r}\n")
        events.publish("instance.deleted", name, {"name": name})  # it was listed as "creating"; its job keeps the error
        raise
    finally:
        store.release_ports(plan["ports"])
    managed_names.wake()  # its managed name (every plan): asked for now


INSTANCE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def planned_instance_id(plan: dict[str, Any]) -> str | None:
    """The INSTANCE_ID the panel planned for the instance (a move: the one the panel has on record for it), or None
    for a new one. Never taken from a backup: its manifest comes from wherever the backup was made."""
    found = str(plan.get("instance_id") or "")
    return found if INSTANCE_ID_RE.match(found) else None


def _create(job: Job, plan: dict[str, Any]) -> None:
    name, db, version = plan["name"], plan["db_name"], plan["version"]
    enterprise = plan["edition"] == "enterprise"
    addons = store.addons_dir(name)
    job.expect(
        7  # Odoo image, preparing, database container, Odoo database, public link, starting Odoo, waiting
        + bool(plan["repo"])
        + enterprise
        + bool(plan["install_modules"] and plan["repo"])
        + (settings.odoo_admin_password != "admin" or bool(plan.get("from_backup") or plan.get("from_production")))
        + bool(plan.get("from_backup") or plan.get("from_production"))  # neutralising another instance's data
        + bool(plan.get("from_production"))  # copying production
        + 2 * bool(plan.get("upgrade"))  # backing up the instance upgraded, upgrading the copy
    )
    source = _backup_to_start_from(job, plan)
    foreign = source is not None and source["instance"] != name
    _download_images(job, version)
    job.step("Preparing the instance")
    (store.folder(name) / "config").mkdir()
    _remove_leftovers(job, name)
    if plan["repo"]:
        job.step("Downloading the code from GitHub")
        try:
            job.run(
                ["git", "clone", "--recurse-submodules", "-b", plan["branch"], "--", plan["repo"], str(addons)],
                timeout=1800,
            )
        except JobError:
            found = github.diagnose(plan["repo"])  # the GitHub App says what keeps it from the repository
            if found:
                job.write(panel_hint(found["message"]))
            raise
    else:  # no repository (plain Odoo): an empty add-ons folder, a repository without a remote so git stays inside it
        job.run(["git", "init", "-q", "-b", plan["branch"] or "main", str(addons)])

    enterprise_folder: Path | None = None
    if enterprise:
        job.step(f"Preparing the Odoo {version} Enterprise add-ons")
        enterprise_folder = addons_sources.prepare(version, job.write)

    db_password, master_password = secrets.token_urlsafe(18), secrets.token_urlsafe(16)
    store.write_env(
        name,
        {
            "ODOO_VERSION": version,
            "EDITION": plan["edition"],
            **{key: str(port) for key, port in plan["ports"].items()},
            "DB_NAME": db,
            "DB_PASSWORD": db_password,
            "GIT_BRANCH": plan["branch"] if plan["repo"] else "",
            **({} if plan["repo"] else {"SOURCE": "plain"}),
            # an instance moved from another server keeps its admin password (the servers package's moves)
            "ODOO_ADMIN_PASSWORD": plan.get("admin_password") or settings.odoo_admin_password,
            "MASTER_PASSWORD": vault.encrypt(master_password, store.master_password_label(name)),
            "AUTO_UPDATE": str(plan["auto_update"]).lower(),
            "AUTO_UPGRADE": str(plan["auto_upgrade"]).lower(),
            "AUTO_PAUSE": str(plan["auto_pause"]).lower(),
            "DELETE_AFTER_DAYS": plan["delete_after_days"],  # 0: keep until deleted by hand
            "CREATED_AT": int(time.time()),
            "CREATED_BY": plan.get("created_by", ""),
            # tells this instance's backups from those of an earlier one; a moved instance keeps its own, as the
            # panel recorded it (the plan), never the one a backup's manifest says
            "INSTANCE_ID": planned_instance_id(plan) or secrets.token_hex(16),
            "ENTERPRISE_FOLDER": str(enterprise_folder or ""),
            "STAGE": plan.get("stage") or stages.DEFAULT,
            **({"PROJECT": plan["project"]} if plan.get("project") else {}),  # projects/: its repository's project
        },
    )
    odoo.write_config(name, db, db_password, master_password)
    # workers and limits in odoo.conf, as the compose file gets them (integrity.apply below): without them a project
    # stage with workers would start Odoo in threaded mode while the gateway sends WebSockets to the gevent port
    resources.write_conf(name)
    refresh_addons_path(job, name)
    mail_catcher.prepare(name)  # staging and development: e-mail goes to the mail catcher
    STATE.update(name, last_visit=time.time(), paused=False, switched_off=False)

    integrity.apply(job, name)  # the images just downloaded, pinned by digest; hardened compose file; protected files

    job.step("Starting the database")
    job.run(compose(name, "up", "-d", "db"), timeout=300)

    if source:
        taken = datetime.fromtimestamp(source["created"], settings.zone).strftime("%Y-%m-%d %H:%M")
        job.step(f"Loading the backup of {source['instance']} of {taken} into database {db}")
        backups.restore(job, name, source, db)
        if plan.get("upgrade"):
            from sudoo.upgrades import run as upgrade_run

            upgrade_run.migrate_copy(job, name, db, plan["upgrade"])
        if foreign:
            job.step("Neutralising the copied data")
            job.note(backups.neutralize(job, name, db))
    else:
        job.step(f"Creating the Odoo {'Enterprise' if enterprise else 'Community'} database (about 1-2 minutes)")
        # web_enterprise turns the database into Enterprise: Enterprise interface and apps
        init = ["-d", db, "-i", "base,web_enterprise" if enterprise else "base", "--stop-after-init", "--no-http"]
        init += demo_args(version, bool(plan["demo"]))  # the flag depends on the version (core/odoo_args.py)
        job.run(odoo.run_cmd(name, *init), timeout=1800)

    modules = _modules_to_install(job, name, db) if plan["install_modules"] and plan["repo"] else []
    if modules and source:  # the backup's database may have them already: those are kept as they are
        installed = backups.installed_modules(name, db)
        already = [module for module in modules if module in installed]
        modules = [module for module in modules if module not in installed]
        if already:
            job.note(f"Already installed in the backup, so not installed again: {', '.join(already)}")
        if not modules:
            job.step("The repository's modules are all installed already")
    if modules:
        job.step(f"Installing the repository's modules ({len(modules)})")
        if job.run_unchecked(
            odoo.run_cmd(name, "-d", db, "-i", ",".join(modules), "--stop-after-init", "--no-http"), timeout=3600
        ):
            job.note(
                "Some of the repository's modules could not be installed. Odoo works; install them from Apps "
                "after a developer has looked at the technical log.",
                "warning",
            )

    _before_serving(job, name, db)  # a project's build: its tests, before Odoo serves the instance

    # a new database already has the login admin / admin; a backup's administrator (user 2) gets the panel's
    # password too, so the instance's credentials in the panel work for it
    login = "admin"
    if settings.odoo_admin_password != "admin" or source:
        job.step("Setting the admin password")
        if source and plan.get("moved"):  # the same instance on another server: its people keep their passwords
            login = _record_admin_login(name, db)
        elif source:
            login = _adopt_admin(job, name, db)
        else:
            _set_admin_password(job, name, db)

    job.step("Creating the public link")
    gateway.add(name)
    exposure.ensure(name)
    tunnel = bool(exposure.provider().instance_ports())  # a connector per instance: its address comes from it
    url = exposure.wait_for_address(name) if tunnel else exposure.address(name)
    if not url and tunnel:
        raise JobError("Cloudflare did not provide a public address for this instance.")
    if url:
        for cmd in odoo.public_address_cmds(name, url):
            job.run(cmd, timeout=60)
    else:  # direct or proxy without a known address yet: Odoo gets it once there is one (monitor.py)
        job.note("No public address yet: the instance gets one with its managed name or a custom domain.")

    job.step("Starting Odoo")
    job.run(compose(name, "up", "-d"), timeout=600)
    _catch_mail(job, name)
    _wait_until_ready(job, name)
    job.result = {"name": name, "url": url, "login": login, "password": settings.odoo_admin_password, "db": db}
    if source:
        job.note(f"Started from the backup {source['file']}")


def _modules_to_install(job: Job, name: str, db: str) -> list[str]:
    """The modules a new database gets: the repository's, or what its project's branch asks for (module_install,
    projects/build_options.py)."""
    if not store.read_env(name).get("PROJECT"):
        return own_modules(store.addons_dir(name))
    from sudoo.projects import testing  # late: projects/ imports this package

    return testing.modules_to_install(job, name, db)


def _before_serving(job: Job, name: str, db: str | None = None) -> None:
    """A build of a project's branch runs its tests here, with Odoo stopped, before it serves the new code
    (projects/testing.py); nothing for other instances and jobs."""
    if not job.details.get("build") or not store.read_env(name).get("PROJECT"):
        return
    from sudoo.projects import testing  # late: projects/ imports this package

    testing.before_serving(job, name, db)


def _download_images(job: Job, version: str) -> None:
    """The first step of a creation: download the Odoo and PostgreSQL images (or check for newer ones), with the
    download's progress in the log, so a first instance of a version shows what takes the time."""
    odoo_image = f"{settings.odoo_image}:{version}"
    new = not integrity.present(odoo_image)
    job.step(f"Downloading Odoo {version} image" if new else f"Checking for a newer Odoo {version} image")
    if new:
        job.write(f"{odoo_image} is not on this server yet: the first download can take several minutes.\n")
    started = time.monotonic()
    for image in (odoo_image, settings.postgres_image):
        integrity.pull_image(job, image)
    job.write(f"Images ready in {round(time.monotonic() - started)} s.\n")


def _backup_to_start_from(job: Job, plan: dict[str, Any]) -> dict[str, Any] | None:
    """The backup a new instance starts from, checked again: plan["from_backup"], or for a staging instance a new
    copy of its project's production instance (plan["from_production"], projects/staging.py); None for an empty
    database."""
    if plan.get("upgrade"):  # the test of a database upgrade: a copy of an instance of the earlier version
        from sudoo.upgrades import run as upgrade_run  # late: upgrades/ uses this module

        return upgrade_run.fresh_copy(job, plan["upgrade"]["source"])
    if plan.get("from_production"):
        from sudoo.projects import staging  # late: projects/ uses this module

        problem = staging.copy_problem(plan["from_production"], plan["version"], plan["edition"])
        if problem:
            raise JobError(problem)
        return staging.copy_of_production(job, plan["from_production"])
    if not plan.get("from_backup"):
        return None
    source = next((b for b in backups.all_backups() if b["file"] == plan["from_backup"]), None)
    if source is None:
        raise JobError("The backup to start from no longer exists.")
    problem = backups.problem(source["file"], plan["version"], plan["edition"])
    if problem:
        raise JobError(problem)
    return source


def _set_admin_password(job: Job, name: str, db: str) -> None:
    """Give the administrator (user 2) of database `db` the instance's admin password (ODOO_ADMIN_PASSWORD)."""
    password = store.read_env(name).get("ODOO_ADMIN_PASSWORD") or settings.odoo_admin_password
    script = f"env['res.users'].browse(2).write({{'password': {password!r}}})\nenv.cr.commit()\n"
    job.run(odoo.run_cmd(name, "shell", "-d", db), timeout=600, input=script)


def _adopt_admin(job: Job, name: str, db: str) -> str:
    """After another instance's data was loaded into database `db`: its administrator (user 2) gets this instance's
    admin password, and its login is recorded (ODOO_ADMIN_LOGIN), so the credentials the panel shows work. Returns
    the login."""
    _set_admin_password(job, name, db)
    return _record_admin_login(name, db)


ADMIN_LOGIN_MAX_LENGTH = 200


def _record_admin_login(name: str, db: str) -> str:
    """Read the login of the administrator (user 2) of database `db` and keep it as ODOO_ADMIN_LOGIN (shown with the
    instance's credentials). When it cannot be read, the login kept so far is left as it is and returned."""
    sql = "SELECT login FROM res_users WHERE id = 2"
    container = store.container(name, "db")
    kept = store.read_env(name).get("ODOO_ADMIN_LOGIN") or "admin"
    login = capture(["docker", "exec", container, "psql", "-U", "odoo", "-d", db, "-tAc", sql]).strip()
    # the database belongs to the instance (its add-ons and its Odoo administrator can change it): only a short,
    # single-line login is kept, anything else leaves the recorded one as it is
    if len(login) > ADMIN_LOGIN_MAX_LENGTH or store.unsafe_env_value(login):
        return kept
    if login and login != kept:
        store.update_env(name, ODOO_ADMIN_LOGIN=login)
    return login or kept


def delete(job: Job, name: str, without_backup: bool = False, no_backup: bool = False) -> None:
    """Back up every database, then remove the instance completely. No backup, no deletion, unless `without_backup`
    (asked for by someone who may manage backups): then a failed backup is noted and audit-logged, and the instance
    is removed anyway. Code running in the instance can make its own backup fail; this keeps it from staying.
    `no_backup`: no backup is taken at all (a pull request's preview, projects/previews.py: its data is throw-away)."""
    job.expect(3)
    job.step("Backing up the database and files" if not no_backup else "Leaving out the backup")
    if no_backup:
        job.note("No backup: this instance's data is throw-away.")
    else:
        _backup_before_deletion(job, name, without_backup)
    job.step("Removing Odoo, its database and its files")
    mail_catcher.detach(name)  # Docker keeps a network something is connected to
    job.run(compose(name, "down", "-v", "--remove-orphans"), timeout=600)
    gateway.remove(name)
    exposure.remove(name)
    from sudoo.instances import custom_domains

    custom_domains.forget_instance(name)
    job.step("Removing the instance folder")
    shutil.rmtree(store.folder(name))
    STATE.remove(name)
    integrity.forget(name)
    resources.forget(name)
    disk_use.forget(name)
    mail_catcher.forget(name)
    from sudoo.instances import profiler

    profiler.forget_instance(name)  # its recordings
    from sudoo import upgrades  # late: upgrades/ uses this module

    upgrades.forget_staging(name)  # a later instance of the same name is never an upgrade's test instance
    backups.remove_daily(name)  # the backup above is newer


def _backup_before_deletion(job: Job, name: str, without_backup: bool) -> None:
    try:
        saved = backups.create(job, name, "deletion")
    except Exception as error:
        if not without_backup:
            job.write(panel_hint(DELETION_BACKUP_FAILED))
            raise
        from sudoo.core import audit

        job.note(f"The backup failed, so the instance is deleted without a complete backup: {error}", "warning")
        audit.record(job.user, None, "instance deleted without a complete backup", name, ok=False, detail=error)
    else:
        job.note(f"Backup kept: {', '.join(path.name for path in saved)}")


DELETION_BACKUP_FAILED = (
    "The backup taken before the deletion failed, so the instance was not deleted. Something in the instance may "
    "keep its backup from working. Someone who may delete instances and manage backups can delete it without a "
    'complete backup (Delete, then "Delete even if the backup fails").'
)
EXPIRY_ATTEMPTS = 3  # failed automatic deletions in a row before the panel switches the instance off instead


def expire(job: Job, name: str) -> None:
    """The automatic deletion at the end of the instance's lifetime (background.py): delete() with its backup. After
    EXPIRY_ATTEMPTS failures in a row the instance is switched off instead, the attempts stop (STATE
    "expiry_stopped") and the audit log says so, until someone deletes it or gives it another lifetime."""
    try:
        delete(job, name)
    except Exception as error:
        failures = int(STATE.get(name).get("expiry_failures") or 0) + 1
        STATE.update(name, expiry_failures=failures)
        if failures >= EXPIRY_ATTEMPTS and store.exists(name):
            from sudoo.core import audit

            job.write(
                panel_hint(
                    f"The automatic deletion failed {failures} times, so the instance was switched off instead and is "
                    "no longer deleted automatically. Delete it by hand (without a complete backup if needed), or "
                    "give it another lifetime."
                )
            )
            job.run_unchecked(compose(name, "stop"), timeout=300)
            STATE.update(name, switched_off=True, paused=False, expiry_stopped=True)
            monitor.mark_stopped(name)
            audit.record("system", None, "automatic deletion stopped, instance switched off", name, ok=False,
                         detail=f"{failures} failed attempts; last: {error}")  # fmt: skip
        raise


def backup(job: Job, name: str, kind: str = "manual", comment: str = "") -> None:
    job.step("Backing up the database and files")
    paused = _paused(name)
    if kind == "daily":
        STATE.update(name, used_since=None)  # visits from now on count for the next one
    saved = backups.create(job, name, kind, comment)
    if paused:
        job.run(compose(name, "stop"), timeout=300)  # leave a paused instance paused
    job.note(f"Backup saved: {', '.join(path.name for path in saved)}")


def daily_backup(job: Job, name: str) -> None:
    """The daily backup of an instance used since its last one; replaces the previous daily backup."""
    backup(job, name, "daily")


def chosen_backup(name: str, file_name: str | None) -> dict[str, Any]:
    """The backup a restore of instance `name` puts back: `file_name` (a backup of any instance), or the latest
    restorable backup of the instance. Raises JobError when it cannot go into this instance."""
    if file_name:
        chosen = next((b for b in backups.all_backups() if b["file"] == file_name), None)
    else:
        chosen = backups.latest(name)
    if chosen is None:
        raise JobError("There is no such backup to restore." if file_name else "There is no backup of this instance.")
    env = store.read_env(name)
    problem = backups.problem(chosen["file"], env["ODOO_VERSION"], env.get("EDITION", "community"))
    if problem:
        raise JobError(problem)
    if backups.belongs_to(name, chosen) and chosen["db"] not in odoo.databases(name):
        raise JobError(f"The backup is of database {chosen['db']}, which this instance does not have.")
    return chosen


def target_database(name: str, backup: dict[str, Any]) -> str:
    """The database of instance `name` a backup goes into: its own database for a backup of this instance, the
    instance's (first) database for a backup of another one (or of an earlier instance of the same name)."""
    return backup["db"] if backups.belongs_to(name, backup) else odoo.databases(name)[0]


def restore(job: Job, name: str, file_name: str | None = None) -> None:
    """Put a backup back (the latest of this instance, or `file_name`, which may be another instance's): the
    current state is backed up first (before-restore, offered as Rollback for ROLLBACK_HOURS), then the database
    and the filestore are replaced and Odoo starts. Another instance's data is loaded under this instance's
    database name and neutralised.

    Restoring the instance's own before-restore backup is a rollback (see rollback): taking a new before-restore
    first would replace the very file being restored."""
    chosen = chosen_backup(name, file_name)
    foreign = not backups.belongs_to(name, chosen)  # another instance's, or an earlier one's of the same name
    if not foreign and chosen["kind"] == "before-restore":
        _rollback(job, name, [chosen])
        return
    taken = datetime.fromtimestamp(chosen["created"], settings.zone).strftime("%Y-%m-%d %H:%M")
    db = target_database(name, chosen)
    job.expect(5 + 2 * foreign)  # the last step, waiting for Odoo, is _start_odoo's
    job.step("Saving the current state first")
    saved = backups.create(job, name, "before-restore")
    hours = f"{settings.rollback_hours:g}"
    job.note(f"The state before the restore is kept for {hours} hours as {', '.join(p.name for p in saved)}")
    job.step("Stopping Odoo")
    job.run(compose(name, "stop", "odoo"), timeout=300)
    origin = chosen["instance"] if chosen["instance"] != name else f"an earlier instance named {name}"
    job.step(f"Restoring the backup of {taken}" + (f" from {origin}" if foreign else ""))
    backups.restore(job, name, chosen, db)
    if foreign:
        job.step("Neutralising the copied data")
        job.note(backups.neutralize(job, name, db))
        job.step("Setting the admin password")
        login = _adopt_admin(job, name, db)
        job.note(f"The administrator ({login}) now has this instance's admin password, shown on its Access tab.")
    _finish_restore(job, name)
    job.note(f"Restored {chosen['file']}" + (f" into database {db}" if db != chosen["db"] else ""))


def rollback(job: Job, name: str) -> None:
    """Put back the state the instance had before its last restore (the before-restore backup, while it is kept).

    The current state is saved as before-rollback first, never as before-restore: that would replace the snapshot
    being restored. The snapshot itself stays until it expires: the action is offered once per restore, but the
    snapshot can still be put back by restoring its file."""
    snapshot = backups.rollback_snapshot(name)
    if not snapshot:
        raise JobError(
            "There is nothing to roll back to: no restore in the last "
            f"{settings.rollback_hours:g} hours, or the instance was rolled back already."
        )
    _rollback(job, name, snapshot)


def _rollback(job: Job, name: str, snapshot: list[dict[str, Any]]) -> None:
    taken = datetime.fromtimestamp(snapshot[0]["created"], settings.zone).strftime("%Y-%m-%d %H:%M")
    databases = odoo.databases(name)
    snapshot = [b for b in snapshot if b["db"] in databases]
    if not snapshot:
        raise JobError("The state before the restore is of a database this instance no longer has.")
    job.expect(5)
    job.step("Saving the current state first")
    try:
        saved = backups.create(job, name, "before-rollback")
        job.note(f"The state before the rollback is kept as {', '.join(p.name for p in saved)}")
    except JobError as error:
        # a rollback is also the way back from a restore that failed halfway, when the database may be gone and
        # cannot be backed up: that must not block it
        job.note(f"The current state could not be saved first ({error}); rolling back anyway.", "warning")
    job.step("Stopping Odoo")
    job.run(compose(name, "stop", "odoo"), timeout=300)
    job.step(f"Rolling back to {taken}, before the restore")
    for chosen in snapshot:
        backups.restore(job, name, chosen)
    _finish_restore(job, name)
    STATE.update(name, rolled_back=snapshot[0]["file"])
    job.note(f"Rolled back to {', '.join(b['file'] for b in snapshot)}")


def _finish_restore(job: Job, name: str) -> None:
    """After a restore: the database gets this instance's public address again, the administrator's login is read
    again (a rollback may bring back another one), and Odoo starts."""
    _record_admin_login(name, odoo.databases(name)[0])
    url = exposure.address(name)
    if url:
        for cmd in odoo.public_address_cmds(name, url):
            job.run(cmd, timeout=60)
    job.step("Starting Odoo")
    _start_odoo(job, name)


def expires_at(env: dict[str, str]) -> int | None:
    """When the instance is deleted automatically (a timestamp), or None when it is kept (always in production)."""
    days = stages.lifetime_days(env)
    return int(env["CREATED_AT"]) + days * 86400 if days > 0 else None


# --------------------------------------------------------------------------- running state


def restart(job: Job, name: str) -> None:
    job.expect(2)
    job.step("Restarting Odoo")
    _start_odoo(job, name, restart=True)


def wake(job: Job, name: str) -> None:
    job.expect(2)
    job.step("Starting Odoo")
    _start_odoo(job, name)


def pause(job: Job, name: str) -> None:
    job.step("Pausing to free server resources")
    job.run(compose(name, "stop"), timeout=300)
    STATE.update(name, paused=True, switched_off=False)
    monitor.mark_stopped(name)


def switch_off(job: Job, name: str) -> None:
    job.step("Switching off")
    job.run(compose(name, "stop"), timeout=300)
    STATE.update(name, switched_off=True, paused=False)
    monitor.mark_stopped(name)


# --------------------------------------------------------------------------- configuration and images


def repair(job: Job, name: str) -> None:
    """Put the panel's own configuration back after the instance was changed outside the panel (integrity.py):
    back up the database first when its container is affected, write the panel's files back, stop containers that
    pretend to belong to the instance, and recreate the affected containers. A paused instance stays paused."""
    found = integrity.findings(name)
    restorable = [finding for finding in found if finding.restorable]
    job.expect(3 + any(finding.part == "db" for finding in restorable))
    job.step("Comparing the instance with the panel's configuration")
    for finding in found:
        job.write(f"- {finding.text}\n")
    if any(finding.part == "env" for finding in found):
        integrity.accept_env(name)
        job.note(
            "The .env file was changed by hand. The values that shape the containers (images, ports, passwords) "
            "still come from the panel's own record; the rest of the file is used as it is now.",
            "warning",
        )
    if not restorable:
        job.note("Nothing else to restore: the containers and files match the panel's configuration.")
        return
    services = {finding.part for finding in restorable} & {"odoo", "db"}
    if any(finding.part == "files" for finding in restorable):
        services = {"odoo", "db"}  # the files shape both containers
    if any(finding.part == "db" for finding in restorable):
        job.step("Backing up the database first")
        saved = backups.create(job, name, "before-repair")
        job.note(f"Backup taken before the repair: {', '.join(path.name for path in saved)}")
    job.step("Putting the panel's configuration back")
    put_back = integrity.rewrite_if_changed(name)
    integrity.protect(name, integrity.inputs(name).get("ODOO_USER", ""))
    stopped = integrity.stop_strangers(job, name)
    if stopped:
        job.note(
            f"Stopped containers that pretended to belong to this instance (kept for review): {', '.join(stopped)}"
        )
    paused = _paused(name)
    if services:
        job.step("Recreating " + " and ".join(sorted(services)) + " from the panel's configuration")
        if paused:
            job.run(compose(name, "create", "--force-recreate", *sorted(services)), timeout=600)
        else:
            job.run(compose(name, "up", "-d", "--force-recreate", "--remove-orphans", *sorted(services)), timeout=900)
            _wait_until_ready(job, name)
    left = [finding.text for finding in integrity.findings(name) if finding.restorable]
    if left:
        job.note(f"Still different after the repair: {'; '.join(left)}", "warning")
    from sudoo.core import audit

    audit.record("system", None, "configuration restored", name, ok=not left,
                 detail="; ".join([*put_back, *stopped, *sorted(services)]))  # fmt: skip


def apply_resources(
    job: Job, name: str, previous: dict[str, Any], layer: str = "instance", scope: tuple[str, str] | None = None
) -> None:
    """Apply the instance's resources (resources.py): write odoo.conf and the compose file, and recreate or restart
    Odoo with them. A paused instance gets them when it next starts.

    Production is changed safely, as an update is: a backup first, and when Odoo does not answer with the new
    limits, `previous` (the values of the layer that changed, before the change) is put back and Odoo started with
    them. `layer`: "instance" (the instance's own limits), "project" (scope: (project, stage)) or "branch" (scope:
    (project, branch))."""
    production = stages.is_production(store.read_env(name))
    paused = _paused(name)
    job.expect(1 if paused else 2 + production)
    if paused:
        job.step("Writing the new resources")
        resources.write_conf(name)
        integrity.apply(job, name)
        job.note("The instance is paused: it uses the new resources when it next starts.")
        return
    if production:
        job.step("Backing up the database and files before the change")
        saved = backups.create(job, name, "before-update")
        job.note(f"Backup taken before the change: {', '.join(path.name for path in saved)}")
    job.step("Applying the new resources")
    try:
        _start_odoo(job, name, restart=True, strict=production)
    except JobError:
        if not production:
            raise
        job.step("Putting the previous resources back")
        if layer == "project" and scope:
            resources.save_project(scope[0], scope[1], previous, job.user)
        elif layer == "branch" and scope:
            resources.save_branch(scope[0], scope[1], previous, job.user)
        else:
            resources.save_overrides(name, previous, job.user)
        _start_odoo(job, name, restart=True)
        raise JobError(
            "Odoo did not answer with the new resources, so the previous ones were put back. Check Odoo's log: "
            "a memory limit may be too low for its workers."
        ) from None


def update_images(job: Job, name: str) -> None:
    """Download the instance's Odoo and PostgreSQL images again (same names and tags), pin the new digests and
    recreate the containers with them. A paused instance gets them when it next starts."""
    job.expect(3)
    job.step("Downloading the images")
    paused = _paused(name)
    integrity.apply(job, name, pull=True)
    if paused:
        job.note("The instance stays paused and uses the new images when someone opens it.")
        return
    job.step("Starting Odoo with the new images")
    _start_odoo(job, name)


# --------------------------------------------------------------------------- code updates


def _pull(job: Job, addons: Path) -> None:
    job.run(git(addons, "pull", "--ff-only"), timeout=900)
    job.run(git(addons, "submodule", "sync", "--recursive"))
    job.run(git(addons, "submodule", "update", "--init", "--recursive"), timeout=1800)


def upgrade_modules(job: Job, name: str, modules: list[str], stay_paused: bool = False) -> None:
    """Run `odoo -u <modules>` on every database of the instance, with Odoo stopped meanwhile."""
    _upgrade(job, name, modules)
    if stay_paused:
        _stay_paused(job, name)
    else:
        job.step("Starting Odoo")
        _start_odoo(job, name)


def _upgrade(job: Job, name: str, modules: list[str]) -> None:
    job.step(f"Upgrading modules: {', '.join(modules)}")
    refresh_addons_path(job, name)
    job.run(compose(name, "up", "-d", "db"), timeout=300)
    job.run(compose(name, "stop", "odoo"), timeout=300)
    for db in odoo.databases(name):
        job.run(odoo.run_cmd(name, "-d", db, "-u", ",".join(modules), "--stop-after-init", "--no-http"), timeout=3600)


def _stay_paused(job: Job, name: str) -> None:
    job.run(compose(name, "stop"), timeout=300)
    job.note("The instance stays paused and uses the new code when someone opens it.")


class _Update:
    """What an update has done so far, so that a failure can be undone (update_code)."""

    def __init__(self, before: dict[str, str]) -> None:
        self.before = before  # snapshot(): the commit of the repository and of every submodule before the update
        self.target = ""  # the commit the update goes to, when known
        self.stage = "downloading the code"  # what it is doing, in the words of the failure message
        self.backup: list[Path] = []  # the before-update backup
        self.database_touched = False  # an upgrade ran or started: the database may have changed


def update_code(job: Job, name: str, submodules_only: bool = False) -> None:
    """Get new code from GitHub (or move submodules to their latest commit), upgrade what changed, restart.

    With SAFE_UPDATES (the default; always for a production instance), the instance's database and files are backed up first (before-update) when the
    update changes the code (for a paused instance: only when an upgrade will run), and a failure at any later step
    puts the instance back: the previous code (repository and submodules), the backup when an upgrade may have
    changed the database, and Odoo started again on that version. The job then fails with a plain sentence saying
    so. An automatic update does not try again a commit whose update was rolled back; Get latest code does."""
    addons = store.addons_dir(name)
    if not capture(git(addons, "remote", "get-url", "origin")):
        raise JobError("This instance has no GitHub repository.")
    paused = _paused(name)
    safe = stages.safe_updates(store.read_env(name))  # always in production
    run = _Update(snapshot(addons))
    upgrade = store.read_env(name)["AUTO_UPGRADE"] == "true"
    try:
        if submodules_only:
            run.stage = "moving the submodules"
            job.step("Moving the submodules to their latest version")
            job.run(git(addons, "submodule", "update", "--init", "--remote", "--recursive"), timeout=1800)
        else:
            job.step("Checking GitHub for new code" if safe else "Downloading the latest code from GitHub")
            _require_branch(job, name, addons)
            if safe:
                job.run(git(addons, "fetch", "origin"), timeout=900)
                run.target = capture(git(addons, "rev-parse", "@{upstream}"))
                failed = STATE.get(name).get("update_failed") or {}
                if job.user == "system" and run.target and failed.get("target") == run.target:
                    job.note(
                        f"The update to {run.target[:7]} failed before and the instance was put back, so it is not "
                        "tried again automatically. Push a fix, or use Get latest code to try it again."
                    )
                    return
                if run.target != run.before["."] and not paused:
                    _back_up_before_update(job, name, run)
                job.step("Downloading the latest code from GitHub")
            _pull(job, addons)
        after = snapshot(addons)
        if run.before == after:
            job.note("Already up to date: there is nothing new on GitHub.")
            STATE.update(name, update_failed=None)
            return
        run.target = after["."] if after["."] != run.before["."] else run.target
        modules = changed_modules(addons, run.before, after)
        job.note(f"Changed modules: {', '.join(modules)}" if modules else "New code downloaded.")
        upgrading = bool(modules) and upgrade
        if safe and not run.backup and (upgrading or not paused):
            _back_up_before_update(job, name, run)
        if upgrading:
            run.stage, run.database_touched = "upgrading modules", True
            _upgrade(job, name, modules)
            run.stage = "running the tests"
            _before_serving(job, name)
            if paused:
                _stay_paused(job, name)
            else:
                run.stage = "starting Odoo"
                job.step("Starting Odoo")
                _start_odoo(job, name, strict=safe)
        elif paused:
            refresh_addons_path(job, name)
            job.note("The instance stays paused and uses the new code when someone opens it.")
        else:
            run.stage = "running the tests"
            _before_serving(job, name)  # stops Odoo when tests run; the restart below starts it again
            run.stage = "starting Odoo"
            job.step("Restarting Odoo")
            _start_odoo(job, name, restart=True, strict=safe)
    except Exception as error:
        if not safe or not (run.backup or run.database_touched or snapshot(addons) != run.before):
            raise  # nothing was changed yet (GitHub not reachable, branch deleted): nothing to put back
        _put_back(job, name, run, paused, submodules_only, error)
    STATE.update(name, update_failed=None)  # a before-update backup kept after a failed rollback stays kept


def _back_up_before_update(job: Job, name: str, run: _Update) -> None:
    stage, run.stage = run.stage, "backing up the database"
    job.step("Backing up the database and files before the update")
    try:
        run.backup = backups.create(job, name, "before-update")
    except Exception:
        job.write(panel_hint(UPDATE_BACKUP_FAILED))
        raise
    run.stage = stage
    job.note(f"Backup taken before the update: {', '.join(path.name for path in run.backup)}")


UPDATE_BACKUP_FAILED = (
    "The backup taken before the update failed, so the update was not applied. Something in the instance may keep "
    "its backup from working; the technical log shows what."
)


def _put_back(job: Job, name: str, run: _Update, paused: bool, submodules_only: bool, error: Exception) -> None:
    """After an update failed: the previous code, the before-update backup when the database may have changed, and
    Odoo on that version. Always raises JobError with a sentence that says what happened."""
    from sudoo.core import audit

    hint = specific_hint("".join(job.log))  # what explains the failure, before the rollback writes its own log
    previous = run.before["."][:7]
    target = "the latest submodules" if submodules_only else (run.target[:7] or "the latest code")
    job.write(f"\nThe update failed ({error}); putting the instance back to {previous}.\n")
    job.step(f"Putting the instance back to {previous}")
    problem = ""
    try:
        _reset_code(job, store.addons_dir(name), run.before)
        if run.database_touched and run.backup:
            job.run(compose(name, "stop", "odoo"), timeout=300)
            for path in run.backup:
                found = backups.FILE_RE.match(path.name)
                backups.restore(job, name, {"file": path.name, "db": found["db"] if found else ""})
        if paused:
            refresh_addons_path(job, name)
            if run.database_touched:
                job.run(compose(name, "stop"), timeout=300)
        else:
            _start_odoo(job, name, restart=True, strict=True)
    except Exception as undo_error:  # noqa: BLE001 - said in the job's message, which ends it
        problem = str(undo_error)
    if problem:
        message = (
            f"The update to {target} failed at {run.stage}, and putting the instance back to {previous} failed "
            f"too ({problem})."
        )
        if run.backup:
            files = ", ".join(path.name for path in run.backup)
            message += f" The backup taken before the update is kept for a restore by hand: {files}."
            kept = list(STATE.get(name).get("keep_backups") or [])
            STATE.update(name, keep_backups=kept + [path.name for path in run.backup if path.name not in kept])
    else:
        message = f"The update to {target} failed at {run.stage}; the instance was put back to {previous}."
    if hint:
        message += f" {hint}"
    STATE.update(
        name,
        update_failed={
            "target": run.target,
            "message": message,
            "at": time.time(),
            "job": job.id,
            "rolled_back": not problem,
        },
    )
    audit.record(job.user, None, "update failed, instance put back" if not problem else "update failed, rollback failed",
                 name, ok=False, detail=message)  # fmt: skip
    job.write(panel_hint(message))
    raise JobError(message) from error


def _reset_code(job: Job, addons: Path, before: dict[str, str]) -> None:
    """Put the repository and every submodule back to the commits of `before` (snapshot())."""
    job.run(git(addons, "reset", "--hard", before["."]))
    job.run(git(addons, "submodule", "sync", "--recursive"))
    job.run(git(addons, "submodule", "update", "--init", "--recursive", "--force"), timeout=1800)
    current = snapshot(addons)
    for path in sorted((p for p in before if p != "."), key=lambda p: p.count("/")):
        if current.get(path) != before[path]:  # moved with --remote before the update: back where it was
            job.run(git(addons / path, "checkout", "-q", "--force", "--detach", before[path]))


def _require_branch(job: Job, name: str, addons: Path) -> None:
    """Stop with a clear message when the instance's branch was deleted on GitHub (often after a merged pull
    request); git itself only says "no such ref was fetched"."""
    branch = store.read_env(name)["GIT_BRANCH"]
    exists = branch_on_github(addons, branch)
    STATE.update(name, branch_missing=exists is False)
    if exists is False:
        job.write(f"The branch {branch} no longer exists on GitHub.\n")
        raise JobError(f"branch {branch} no longer exists on GitHub")


def switch_branch(job: Job, name: str, branch: str) -> None:
    addons = store.addons_dir(name)
    job.step(f"Switching the code to branch {branch}")
    job.run(git(addons, "fetch", "origin", branch), timeout=900)
    job.run(git(addons, "checkout", "-B", branch, f"origin/{branch}"))
    job.run(git(addons, "branch", f"--set-upstream-to=origin/{branch}", branch))
    job.run(git(addons, "submodule", "update", "--init", "--recursive"), timeout=1800)
    store.update_env(name, GIT_BRANCH=branch)
    STATE.update(name, branch_missing=False)
    managed_names.wake()  # its managed name follows the branch
    if not _paused(name):
        restart(job, name)


def connect_repository(job: Job, name: str, repo: str, branch: str, install_modules: bool = False) -> None:
    """Give an instance of plain Odoo a GitHub repository: clone it next to the empty add-ons folder, move it in (the
    folder stays the same, so the containers keep their mount), record the branch, install the repository's modules
    if asked, and restart. From then on the instance is like one created from that repository; its data is kept."""
    addons = store.addons_dir(name)
    if capture(git(addons, "remote", "get-url", "origin")):
        raise JobError("This instance already has a GitHub repository.")
    clone = store.folder(name) / "addons.connecting"
    shutil.rmtree(clone, ignore_errors=True)  # left over from an attempt that failed halfway
    job.step(f"Downloading the code of {github.repo_name(repo) or repo} ({branch}) from GitHub")
    try:
        job.run(["git", "clone", "--recurse-submodules", "-b", branch, "--", repo, str(clone)], timeout=1800)
    except JobError:
        shutil.rmtree(clone, ignore_errors=True)
        found = github.diagnose(repo)
        if found:
            job.write(panel_hint(found["message"]))
        raise
    job.step("Putting the code into the instance")
    others = [entry.name for entry in addons.iterdir() if entry.name != ".git"]
    if others:  # a plain instance's add-ons folder is empty: never overwrite what someone put there by hand
        shutil.rmtree(clone, ignore_errors=True)
        raise JobError(f"The add-ons folder is not empty ({', '.join(sorted(others)[:5])}). Empty it first.")
    shutil.rmtree(addons / ".git")
    for entry in clone.iterdir():
        entry.rename(addons / entry.name)
    clone.rmdir()
    env = store.read_env(name)
    env.pop("SOURCE", None)
    env["GIT_BRANCH"] = branch
    from sudoo import projects  # late: projects/ reads instances' files through this package

    project = projects.for_repository(str(github.repo_name(repo)), env["ODOO_VERSION"], env["EDITION"], job.user)
    env["PROJECT"] = project["id"]  # it joins its repository's project
    store.write_env(name, env)
    if not (project.get("branches") or {}).get(branch, {}).get("stage"):
        projects.record_branch(project["id"], branch, stage=stages.of(env), changed_by=job.user)
    STATE.update(name, branch_missing=False)
    events.publish("instance", name)
    modules = own_modules(addons) if install_modules else []
    paused = _paused(name)
    if modules:
        job.step(f"Installing the repository's modules ({len(modules)})")
        refresh_addons_path(job, name)
        job.run(compose(name, "up", "-d", "db"), timeout=300)
        job.run(compose(name, "stop", "odoo"), timeout=300)
        for db in odoo.databases(name):
            if job.run_unchecked(
                odoo.run_cmd(name, "-d", db, "-i", ",".join(modules), "--stop-after-init", "--no-http"), timeout=3600
            ):
                job.note(
                    "Some of the repository's modules could not be installed. Odoo works; install them from Apps "
                    "after a developer has looked at the technical log.",
                    "warning",
                )
        if paused:
            job.run(compose(name, "stop"), timeout=300)
    elif install_modules:
        job.note("The repository has no Odoo modules to install.")
    job.note(f"Connected to {repo}, branch {branch}. Get latest code and Switch branch work from now on.")
    _apply_code_change(job, name)


def _apply_code_change(job: Job, name: str) -> None:
    """After the code changed: a paused instance only gets the new addons_path, a running one restarts."""
    if _paused(name):
        refresh_addons_path(job, name)
    else:
        restart(job, name)


def _save_to_github(job: Job, addons: Path, undo: Callable[[], None]) -> None:
    """Push the last commit; if GitHub refuses, run `undo` so the server matches GitHub again, and fail."""
    if not capture(git(addons, "remote", "get-url", "origin")):
        return
    job.step("Saving the change to GitHub")
    if job.run_unchecked(git(addons, "push"), timeout=300):
        undo()
        raise JobError(
            "GitHub refused the change. The repository's deploy key needs write access "
            "(tick 'Allow write access' for it on GitHub). The change was undone on the server."
        )


def add_submodule(job: Job, name: str, url: str, path: str, branch: str | None) -> None:
    addons = store.addons_dir(name)
    job.step(f"Adding submodule {path}")
    job.run(git(addons, "submodule", "add", *(["-b", branch] if branch else []), "--", url, path), timeout=1800)
    job.run(git(addons, "submodule", "update", "--init", "--recursive", path), timeout=1800)
    job.run(git(addons, "commit", "-m", f"Add submodule {path}"))

    def undo() -> None:
        job.run_unchecked(git(addons, "reset", "--hard", "HEAD~1"))
        job.run_unchecked(git(addons, "submodule", "deinit", "-f", path))
        shutil.rmtree(addons / ".git" / "modules" / path, ignore_errors=True)
        shutil.rmtree(addons / path, ignore_errors=True)

    _save_to_github(job, addons, undo)
    _apply_code_change(job, name)


def remove_submodule(job: Job, name: str, path: str) -> None:
    addons = store.addons_dir(name)
    job.step(f"Removing submodule {path}")
    job.run(git(addons, "submodule", "deinit", "-f", path))
    job.run(git(addons, "rm", "-f", path))
    shutil.rmtree(addons / ".git" / "modules" / path, ignore_errors=True)
    job.run(git(addons, "commit", "-m", f"Remove submodule {path}"))

    def undo() -> None:
        job.run_unchecked(git(addons, "reset", "--hard", "HEAD~1"))
        job.run_unchecked(git(addons, "submodule", "update", "--init", "--recursive", path))

    _save_to_github(job, addons, undo)
    _apply_code_change(job, name)
