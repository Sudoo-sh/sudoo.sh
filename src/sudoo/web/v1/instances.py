"""Instances, jobs and Odoo versions.

    GET    /api/v1/instances?limit=&offset=          list (instances being created too, status "creating")
    GET    /api/v1/instances/<name>                  one instance, with commits, submodules and recent jobs; while
                                                     its creation is queued or running: status "creating" and the
                                                     job as current_job (404 once a failed creation left nothing)
    GET    /api/v1/instances/<name>/credentials      Odoo admin and master passwords, ports
    GET    /api/v1/instances/<name>/logs?lines=      the end of Odoo's log
    POST   /api/v1/instances                         create; {"from_backup"} optional; without "repo": plain Odoo
                                                     (no code, no branch, no update) -> 202, Location: job
    PATCH  /api/v1/instances/<name>                  change automation settings and lifetime (a lifetime that
                                                     ends sooner also needs instances.delete)
    POST   /api/v1/instances/<name>/actions/<action> restart, update, pause, ...    -> 202, Location: job
                                                     restore: {"file"} optional (any instance's backup of the
                                                     same Odoo series), the latest backup otherwise
                                                     rollback: back to before the last restore (ROLLBACK_HOURS)
                                                     connect-repository: {"repo", "branch", "install_modules"}
                                                     gives plain Odoo a repository (its data is kept)
    DELETE /api/v1/instances/<name>                  delete after a backup; body {"confirm": "<name>"} -> 202;
                                                     "without_backup": true also when the backup fails (needs
                                                     backups.manage too; audit-logged)
    GET    /api/v1/instance-plan?repo=&branch=&name=&version=&edition=
                                                     the name and database a new instance would get
    GET    /api/v1/instance-quota                    how many instances the caller may still create, and when
    GET    /api/v1/jobs?instance=&limit=&offset=     recent jobs, newest first
    GET    /api/v1/jobs/<id>?include=log             a job
    GET    /api/v1/jobs/<id>/log?offset=             its technical log from a line on
    GET    /api/v1/versions                          Odoo versions and Enterprise availability

Every endpoint names the permission it needs (roles.py); each action has its own (ACTIONS). Putting another
instance's backup into an instance, or starting a new instance from a backup, also needs backups.manage, the
permission that shows every backup (_usable_backup). An instance of plain Odoo (created without "repo") refuses
the actions and settings that need a repository (REPOSITORY_ACTIONS) with 409.

A token with the instances scope (a connected app, roles.CONNECTED_APP) is narrowed further than its permissions:
only the actions in CONNECTED_APP_ACTIONS, and PATCH only for a later end of the lifetime (_refuse_connected_app);
it has no instances.restore, so it cannot create from a backup either.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Mapping
from typing import Any, cast

from flask import Blueprint, Response, abort, g, request

from sudoo import addons, exposure, roles
from sudoo.config import settings
from sudoo.core import events, jobs, vault
from sudoo.core.state import STATE
from sudoo.instances import backups, odoo, operations, store
from sudoo.limits import quotas
from sudoo.repos import github
from sudoo.repos.gitrepo import origin_url

from .. import validation as check
from ..security import can, log, message_for_user, refuse_unless, require
from .resources import instance_resource, job_resource
from .responses import PREFIX, ok, page

blueprint = Blueprint("instances", __name__)

# action -> (title shown in the activity list, job kind, permission needed)
ACTIONS = {
    "update": ("Get latest code", "update", "instances.update"),
    "update-submodules": ("Update submodules", "update", "instances.update"),
    "restart": ("Restart", "restart", "instances.update"),
    "start": ("Start", "wake", "instances.update"),
    "add-submodule": ("Add submodule", "submodule", "instances.update"),
    "pause": ("Pause", "pause", "instances.operate"),
    "switch-off": ("Switch off", "stop", "instances.operate"),
    "upgrade": ("Upgrade modules", "upgrade", "instances.operate"),
    "remove-submodule": ("Remove submodule", "submodule", "instances.operate"),
    "switch-branch": ("Switch branch", "update", "instances.operate"),
    "connect-repository": ("Connect a repository", "update", "instances.operate"),
    "backup": ("Back up", "backup", "instances.operate"),
    "update-images": ("Update Odoo and PostgreSQL images", "update", "instances.operate"),
    "repair": ("Restore the panel's configuration", "repair", "instances.operate"),
    "restore": ("Restore a backup", "restore", "instances.restore"),
    "rollback": ("Roll back the restore", "restore", "instances.restore"),
    "delete": ("Delete instance", "delete", "instances.delete"),
}


CONNECTED_APP_ACTIONS = frozenset({"start", "pause", "restart", "backup", "delete"})
"""The only actions a connected app (instances scope) may run, whatever its permissions would allow."""


def _connected_app() -> bool:
    return g.user["role"] == roles.CONNECTED_APP


def _refuse_connected_app(what: str, message: str) -> None:
    """403 for a connected app, audit-logged."""
    log(f"refused: {what}", ok=False)
    abort(403, message)


def existing(name: str) -> str:
    """`name` when that instance exists; 409 while it is still being created, 404 otherwise."""
    if not store.exists(check.instance_name(name)):
        if jobs.creating(name):
            abort(409, "This instance is still being created. Try again when its creation has finished.")
        abort(404, "This instance does not exist.")
    return name


def known(name: str) -> str:
    """`name` when that instance exists or is being created; 404 otherwise (also after a creation that failed and
    left nothing: its job stays under /api/v1/jobs)."""
    if not store.exists(check.instance_name(name)) and not jobs.creating(name):
        abort(404, "This instance does not exist.")
    return name


def _accepted(job: jobs.Job) -> Response:
    """202 Accepted for work that continues in the background; Location points to the job."""
    response = ok(job_resource(job), status=202)
    response.headers["Location"] = f"{PREFIX}/jobs/{job.id}"
    return response


def _limits(default: int = 100) -> tuple[int, int]:
    limit = check.whole_number(request.args.get("limit", str(default)), "limit", minimum=1, maximum=500)
    return limit, check.whole_number(request.args.get("offset", "0"), "offset")


# ---------------------------------------------------------------- reading


@blueprint.get("/instances")
@require("instances.view")
def list_instances() -> Response:
    limit, offset = _limits()
    names = sorted(set(store.names()) | set(jobs.being_created()))  # those being created have status "creating"
    return page(names, limit, offset, instance_resource)


@blueprint.get("/instances/<name>")
@require("instances.view")
def instance(name: str) -> Response:
    return ok(instance_resource(known(name), full=True))


@blueprint.get("/instances/<name>/credentials")
@require("instances.credentials")
def credentials(name: str) -> Response:
    env = store.read_env(existing(name))
    log("instance credentials viewed", instance=name)
    return ok(
        {
            "login": env.get("ODOO_ADMIN_LOGIN") or "admin",  # another login when started from a backup
            "admin_password": env["ODOO_ADMIN_PASSWORD"],
            "master_password": vault.decrypt(env["MASTER_PASSWORD"], store.master_password_label(name)),
            "ports": {key.lower(): int(env[key]) for key in env if key.endswith("_PORT") and env[key].isdigit()},
        }
    )


@blueprint.get("/instances/<name>/logs")
@require("instances.logs")
def odoo_log(name: str) -> Response:
    lines = check.whole_number(request.args.get("lines", "300"), "lines", minimum=1, maximum=5000)
    return ok({"text": odoo.recent_log(existing(name), lines) or ""})


# ---------------------------------------------------------------- creating


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _plan_name(repo: str, branch: str, custom_name: str, version: str = "", edition: str = "") -> tuple[str, str]:
    """Instance name (from the custom name, repository + branch, or for plain Odoo "odoo-<version>-<edition>-<4
    random characters>"), made unique, and its database name."""
    if custom_name:
        base = _slug(custom_name)
    elif not repo and version:  # plain Odoo
        base = "-".join(part for part in ("odoo", _slug(version), _slug(edition), secrets.token_hex(2)) if part)
    else:
        repository = (github.repo_name(repo) or "").split("/")[-1]
        base = "-".join(part for part in (_slug(repository), _slug(branch)) if part)
    base = base[:28].strip("-") or "odoo"
    name, counter = base, 2
    while store.folder(name).exists():
        name, counter = f"{base[:26].strip('-')}-{counter}", counter + 1
    return name, name.replace("-", "_")


def _usable_backup(file_name: str, version: str, edition: str, instance: str | None = None) -> str:
    """`file_name` when that backup exists, the caller may use it and it fits Odoo `version` and `edition`.

    A backup of `instance` itself needs nothing more than the action's permission; any other backup (another
    instance's, an earlier deleted instance's of the same name, or one to start a new instance from) also needs
    backups.manage, which shows every backup."""
    # the permission first: without backups.manage, other instances' backups must not even be found (404 or not)
    match = backups.FILE_RE.match(file_name)
    found = next((b for b in backups.all_backups() if b["file"] == file_name), None) if match else None
    if instance is None or found is None or not backups.belongs_to(instance, found):
        refuse_unless("backups.manage", f"use backup {file_name[:80]}" + (f" for {instance}" if instance else ""))
    path = check.backup_file(file_name)  # 404 for an unknown file or anything that is not a backup name
    problem = backups.problem(path.name, version, edition)
    if problem:
        abort(400, problem)
    return path.name


@blueprint.get("/instance-plan")
@require("instances.create")
def plan() -> Response:
    """What a new instance would be called, before it is created."""
    name, database = _plan_name(
        check.text(request.args, "repo"),
        check.text(request.args, "branch"),
        check.custom_name(check.text(request.args, "name", max_length=40)),
        check.text(request.args, "version", max_length=10),
        check.text(request.args, "edition", max_length=20),
    )
    defaults = {
        key: getattr(settings, key) for key in ("delete_after_days", "auto_update", "auto_upgrade", "auto_pause")
    }
    return ok({"name": name, "database": database, "login": "admin", "defaults": defaults})


@blueprint.get("/instance-quota")
@require("instances.view")
def quota() -> Response:
    return ok(quotas.usage(g.user["name"]))


@blueprint.post("/instances")
@require("instances.create")
def create() -> Response:
    data = check.body()
    repo = check.repo_address(check.text(data, "repo"), required=False)
    branch = check.branch(check.text(data, "branch", default="main"))
    version = check.one_of(check.text(data, "version"), odoo.versions(), "Choose an Odoo version from the list.")
    edition = check.one_of(check.text(data, "edition"), ("community", "enterprise"), "Choose Community or Enterprise.")
    custom_name = check.custom_name(check.text(data, "name", max_length=40))
    if edition == "enterprise" and not addons.available(version):
        abort(400, f"Odoo {version} Enterprise is not available on this server.")
    from_backup = check.text(data, "from_backup", max_length=200) or None
    if from_backup:
        refuse_unless("instances.restore", "create an instance from a backup")
        from_backup = _usable_backup(from_backup, version, edition)
    with quotas.LOCK:  # until the job is registered: from then on it counts toward the limits
        return _start_creation(data, repo, branch, version, edition, custom_name, from_backup)


def _start_creation(
    data: Mapping[str, Any],
    repo: str,
    branch: str,
    version: str,
    edition: str,
    custom_name: str,
    from_backup: str | None,
) -> Response:
    try:
        quotas.claim(g.user["name"])
    except quotas.QuotaError as error:
        g.retry_after = error.retry_after
        log("instance creation refused (limit)", ok=False, detail=error.message)
        abort(error.status, error.message)
    name, db_name = _plan_name(repo, branch, custom_name, version, edition)
    plan_data = {
        "name": name,
        "db_name": db_name,
        "repo": repo,
        "branch": branch,
        "version": version,
        "edition": edition,
        "demo": check.flag(data, "demo"),
        "install_modules": check.flag(data, "install_modules"),
        "delete_after_days": check.days(data, "delete_after_days"),
        # automation switches: the panel's defaults (AUTO_PAUSE, AUTO_UPDATE, AUTO_UPGRADE) unless sent
        **{key: check.flag(data, key, getattr(settings, key)) for key in ("auto_update", "auto_upgrade", "auto_pause")},
        **({} if repo else {"branch": "", "auto_update": False, "auto_upgrade": False}),  # plain Odoo: no code
        "ports": store.reserve_ports(exposure.provider().instance_ports()),
        "created_by": g.user["name"],
        "from_backup": from_backup,
    }
    # what the instance looks like until its files exist (web/v1/resources.creating_resource): no ports, no secrets
    shown = ("version", "edition", "db_name", "repo", "branch", "created_by")
    details = {
        key: plan_data[key] for key in (*shown, "auto_update", "auto_upgrade", "auto_pause", "delete_after_days")
    }
    job = jobs.start(
        name, "Create instance", "create", operations.create, plan_data, user=g.user["name"], details=details
    )
    log(
        "Create instance",
        instance=name,
        detail=f"Odoo {version} {edition}"
        + ("" if repo else " (plain, no repository)")
        + (f" from {from_backup}" if from_backup else ""),
    )
    response = _accepted(job)
    response.headers["Content-Location"] = f"{PREFIX}/instances/{name}"
    return response


# ---------------------------------------------------------------- changing


@blueprint.patch("/instances/<name>")
@require("instances.operate")
def change(name: str) -> Response:
    """Change only the settings that are sent; the others keep their value.

    A lifetime that makes the instance expire sooner than now (also from "keep" to any number of days) deletes it
    sooner, so it needs instances.delete as well."""
    existing(name)
    data = check.body()
    if _connected_app():
        _check_connected_app_change(name, data)
    switches = {"auto_update": "AUTO_UPDATE", "auto_upgrade": "AUTO_UPGRADE", "auto_pause": "AUTO_PAUSE"}
    values: dict[str, object] = {
        key: str(check.flag(data, field)).lower() for field, key in switches.items() if field in data
    }
    if "delete_after_days" in data:
        days = check.days(data, "delete_after_days")
        env = store.read_env(name)
        current = operations.expires_at(env)
        proposed = operations.expires_at({**env, "DELETE_AFTER_DAYS": str(days)})
        if proposed is not None and (current is None or proposed < current):
            refuse_unless("instances.delete", f"shorten the lifetime of {name}")
        values["DELETE_AFTER_DAYS"] = days
    if not values:
        abort(400, "Send at least one of: auto_update, auto_upgrade, auto_pause, delete_after_days.")
    if values.get("AUTO_UPDATE") == "true" or values.get("AUTO_UPGRADE") == "true":
        refuse_plain(name)
    store.update_env(name, **values)
    if "DELETE_AFTER_DAYS" in values:  # a new lifetime: automatic deletion starts counting its attempts again
        STATE.update(name, expiry_failures=0, expiry_stopped=False)
    log("instance settings changed", instance=name, detail=", ".join(sorted(values)))
    events.publish("instance", name)
    return ok(instance_resource(name, full=True))


def _check_connected_app_change(name: str, data: Mapping[str, Any]) -> None:
    """A connected app may only make an instance's lifetime end later: "delete_after_days" alone, a number of days
    after which the instance expires later than it does now (never 0, "keep", unless it is kept already)."""
    message = (
        "This API token has the instances scope: it can only extend an instance's lifetime "
        "(delete_after_days, ending later than now)."
    )
    if set(data) - {"delete_after_days"}:
        _refuse_connected_app(f"change settings of {name}", message)
    if "delete_after_days" not in data:
        return
    days = check.days(data, "delete_after_days")
    env = store.read_env(name)
    current = operations.expires_at(env)
    proposed = operations.expires_at({**env, "DELETE_AFTER_DAYS": str(days)})
    later = (current is None and proposed is None) or (
        current is not None and proposed is not None and proposed >= current
    )
    if not later:
        _refuse_connected_app(f"change the lifetime of {name}", message)


REPOSITORY_ACTIONS = ("update", "update-submodules", "switch-branch", "add-submodule", "remove-submodule")
"""Actions that need the instance's repository: refused for plain Odoo."""


def has_repository(name: str) -> bool:
    """False for plain Odoo: SOURCE=plain in .env, or (instances made before it was recorded) no GitHub remote."""
    return not store.plain(store.read_env(name)) and bool(origin_url(store.addons_dir(name)))


def refuse_plain(name: str) -> None:
    """409 with a problem detail when the instance runs plain Odoo (nothing that needs a repository applies)."""
    if not has_repository(name):
        abort(
            409,
            f"{name} runs plain Odoo without a GitHub repository: it has no code to update, no branch and no "
            "submodules. Connect a repository to it first.",
        )


def _job_for(action: str, name: str, data: Mapping[str, Any]) -> tuple[Callable[..., Any], tuple[Any, ...]]:
    """(function, arguments) of the job for `action`, after validating the request data."""
    if action in REPOSITORY_ACTIONS:
        refuse_plain(name)
    if action == "connect-repository":
        if has_repository(name):
            abort(409, f"{name} has a GitHub repository already. Switch branch changes its code.")
        repo = check.repo_address(check.text(data, "repo"))
        branch = check.branch(check.text(data, "branch", default="main"))
        return operations.connect_repository, (name, repo, branch, check.flag(data, "install_modules"))
    simple: dict[str, tuple[Callable[..., Any], tuple[Any, ...]]] = {
        "update": (operations.update_code, (name,)),
        "update-submodules": (operations.update_code, (name, True)),
        "restart": (operations.restart, (name,)),
        "start": (operations.wake, (name,)),
        "pause": (operations.pause, (name,)),
        "backup": (operations.backup, (name,)),
        "switch-off": (operations.switch_off, (name,)),
        "update-images": (operations.update_images, (name,)),
        "repair": (operations.repair, (name,)),
    }
    if action in simple:
        return simple[action]
    if action == "delete":
        if check.text(data, "confirm", max_length=40) != name:
            abort(400, "Type the instance name exactly to confirm.")
        if check.flag(data, "without_backup"):
            # the backup may be all that is left of the instance's data: only for those who manage backups
            refuse_unless("backups.manage", f"delete {name} without a complete backup")
            log("instance deletion without a complete backup requested", instance=name)
            return operations.delete, (name, True)
        return operations.delete, (name,)
    if action == "restore":
        file_name = check.text(data, "file", max_length=200) or None
        if file_name:
            env = store.read_env(name)
            file_name = _usable_backup(file_name, env["ODOO_VERSION"], env.get("EDITION", "community"), name)
        elif backups.latest(name) is None:
            abort(409, "This instance has no backup to restore.")
        return operations.restore, (name, file_name)
    if action == "rollback":
        if not backups.rollback_snapshot(name):
            abort(
                409,
                "There is nothing to roll back to: no restore in the last "
                f"{settings.rollback_hours:g} hours, or the instance was rolled back already.",
            )
        return operations.rollback, (name,)
    if action == "upgrade":
        return operations.upgrade_modules, (name, check.module_list(check.text(data, "modules", max_length=4000)))
    if action == "switch-branch":
        return operations.switch_branch, (name, check.branch(check.text(data, "branch")))
    path = check.text(data, "path")
    if action == "add-submodule":
        url = check.repo_address(check.text(data, "url"))
        branch = check.branch(check.text(data, "branch"), required=False)
        path = check.submodule_path(path or cast(str, github.repo_name(url)).split("/")[-1])
        return operations.add_submodule, (name, url, path, branch)
    return operations.remove_submodule, (name, check.submodule_path(path))


def run(name: str, action: str) -> Response:
    existing(name)
    if action not in ACTIONS:
        abort(404, "Unknown action.")
    title, kind, permission = ACTIONS[action]
    if _connected_app() and action not in CONNECTED_APP_ACTIONS:
        _refuse_connected_app(
            f"{action} on {name}",
            "This API token has the instances scope: of the actions it can only start, pause, restart, back up "
            "and delete.",
        )
    refuse_unless(permission, f"{action} on {name}")
    fn, args = _job_for(action, name, check.body())
    job = jobs.start(name, title, kind, fn, *args, user=g.user["name"])
    log(title, instance=name)
    return _accepted(job)


@blueprint.post("/instances/<name>/actions/<action>")
def action(name: str, action: str) -> Response:
    return run(name, action)


@blueprint.delete("/instances/<name>")
def delete(name: str) -> Response:
    return run(name, "delete")


# ---------------------------------------------------------------- jobs


@blueprint.get("/jobs")
@require("instances.view")
def list_jobs() -> Response:
    limit, offset = _limits(default=30)
    instance_name = request.args.get("instance")
    if instance_name:
        check.instance_name(instance_name)
    found = [j for j in reversed(jobs.JOBS.values()) if not instance_name or j.instance == instance_name]
    return page(found, limit, offset, job_resource)


def _job(job_id: str) -> jobs.Job:
    return jobs.JOBS.get(job_id) or abort(404, "This job does not exist or is too old to be kept.")


@blueprint.get("/jobs/<job_id>")
@require("instances.view")
def job(job_id: str) -> Response:
    include_log = request.args.get("include") == "log" and can("instances.logs")
    found = job_resource(_job(job_id), include_log=include_log)
    if found["error"]:
        found["error"] = message_for_user(found["error"])
    return ok(found)


@blueprint.get("/jobs/<job_id>/log")
@require("instances.logs")
def job_log(job_id: str) -> Response:
    """The job's log from line `offset` on; `meta.next_offset` is where to continue."""
    found = _job(job_id)
    offset = check.whole_number(request.args.get("offset", "0"), "offset")
    lines = found.log[:]
    return ok({"text": "".join(lines[offset:])}, meta={"offset": offset, "next_offset": len(lines)})


# ---------------------------------------------------------------- versions


@blueprint.get("/versions")
@require("instances.view")
def versions() -> Response:
    return ok([{"version": v, "enterprise": addons.available(v)} for v in odoo.versions()])
