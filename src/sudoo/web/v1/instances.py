"""Instances, jobs and Odoo versions.

    GET    /api/v1/instances?limit=&offset=          list (instances being created too, status "creating")
    GET    /api/v1/instances/<name>                  one instance, with commits, submodules and recent jobs; while
                                                     its creation is queued or running: status "creating" and the
                                                     job as current_job (404 once a failed creation left nothing)
    GET    /api/v1/instances/<name>/credentials      Odoo admin and master passwords, ports
    GET    /api/v1/instances/<name>/logs?lines=      the end of Odoo's log
    POST   /api/v1/instances                         create; {"from_backup"} optional; without "repo": plain Odoo
                                                     (no code, no branch, no update); "stage" (development by
                                                     default; production within stages.production_limit());
                                                     "install_modules": the repository's modules;
                                                     "modules": [names] more modules (Odoo's apps...) installed
                                                     in the same run
                                                     -> 202, Location: job
    PATCH  /api/v1/instances/<name>                  change automation settings, lifetime and stage (a lifetime
                                                     that ends sooner also needs instances.delete; leaving
                                                     production needs "confirm_production": true)
    POST   /api/v1/instances/<name>/actions/<action> restart, update, pause, ...    -> 202, Location: job
                                                     restore: {"file"} optional (any instance's backup of the
                                                     same Odoo series), the latest backup otherwise
                                                     rollback: back to before the last restore (ROLLBACK_HOURS)
                                                     backup: {"comment"} optional, kept with the backup
                                                     connect-repository: {"repo", "branch", "install_modules"}
                                                     gives plain Odoo a repository (its data is kept)
    DELETE /api/v1/instances/<name>                  delete after a backup; body {"confirm": "<name>"} -> 202
                                                     (production: also "confirm_stage": "production");
                                                     "without_backup": true also when the backup fails (needs
                                                     backups.manage too; audit-logged)
    GET    /api/v1/instances/<name>/resources        priority under load and limits: its own, its stage's
                                                     defaults, what applies, warnings (resources.py)
    PUT    /api/v1/instances/<name>/resources        {"limits": {knob: value or null}} sets the instance's own
                                                     limits (null: the stage's default) and applies them in a job
                                                     -> 202; production needs "confirm_production": true
    GET    /api/v1/instance-plan?repo=&branch=&name=&version=&edition=
                                                     the name and database a new instance would get
    GET    /api/v1/instance-quota                    how many instances the caller may still create, and when
    GET    /api/v1/repositories/modules?url=&branch= the Odoo modules of a repository's branch (submodules too);
                                                     nothing is created
    GET    /api/v1/odoo-apps?version=&edition=       the standard Odoo apps the module picker offers
    GET    /api/v1/jobs?instance=&limit=&offset=     recent jobs, newest first
    GET    /api/v1/jobs/<id>                         a job (its steps, notes and result; never its technical log)
    GET    /api/v1/versions                          Odoo versions and Enterprise availability

Every endpoint names the permission it needs (roles.py); each action has its own (ACTIONS). Putting another
instance's backup into an instance, or starting a new instance from a backup, also needs backups.manage, the
permission that shows every backup (_usable_backup). An instance of plain Odoo (created without "repo") refuses
the actions and settings that need a repository (REPOSITORY_ACTIONS) with 409.

A token with the instances scope (a connected app, roles.CONNECTED_APP) is narrowed further than its permissions:
only the actions in CONNECTED_APP_ACTIONS, and PATCH only for a later end of the lifetime (_refuse_connected_app);
it has no instances.restore, so it cannot create from a backup either.

A token with the developer scope (roles.DEVELOPER_TOKEN) is narrowed too: only the actions in DEVELOPER_ACTIONS,
creating and deleting development instances only, never PATCH (settings, lifetime, stage).

A production instance (instances/stages.py) asks for more: pausing, switching off, restoring and rolling back need
"confirm_production": true in the body, deleting needs "confirm_stage": "production" besides its name.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, NoReturn, cast

from flask import Blueprint, Response, abort, g, request

from sudoo import addons, engines, entitlements, link_deadline, projects, roles
from sudoo.config import settings
from sudoo.core import events, jobs, vault
from sudoo.core.state import STATE
from sudoo.instances import (
    backups,
    capacity,
    naming,
    odoo,
    odoo_apps,
    operations,
    planning,
    resources,
    stages,
    store,
)
from sudoo.limits import queue, quotas
from sudoo.mailcatcher import panel as mail_catcher
from sudoo.premium import servers
from sudoo.projects import access
from sudoo.repos import github, mirror
from sudoo.repos import modules as repository_module_list
from sudoo.repos.gitrepo import origin_url

from .. import validation as check
from ..security import log, message_for_user, refuse_unless, require
from .backups import refuse_unusable
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
    "change-engine": ("Move to another engine", "move", "instances.move"),
}


STARTING_ACTIONS = frozenset({"start", "restart", "restore", "rollback", "upgrade", "change-engine"})
"""Actions that always end with Odoo running (operations.wake, restart, _finish_restore, upgrade_modules): on an
instance that is not running they count as one more running instance, like a start."""

BUILD_ACTIONS = frozenset({"update", "update-submodules"})
"""Actions that build the instance from its branch's latest code: each is a build of the branch (projects/builds.py)."""

CONNECTED_APP_ACTIONS = frozenset({"start", "pause", "restart", "backup", "delete"})
"""The only actions a connected app (instances scope) may run, whatever its permissions would allow."""


DEVELOPER_ACTIONS = frozenset({"start", "pause", "restart", "update", "update-submodules"})
"""The only actions a developer token (developer scope) may run, whatever its permissions would allow."""

DEVELOPER_SCOPE = (
    "This API token has the developer scope: it can start, pause, restart and update instances and create and "
    "delete development instances; "
)


def _connected_app() -> bool:
    return g.user["role"] == roles.CONNECTED_APP


def developer_token() -> bool:
    """A token with the developer scope (roles.DEVELOPER_TOKEN)."""
    return g.user["role"] == roles.DEVELOPER_TOKEN


def refuse_developer_token(what: str, rest: str) -> None:
    """403 for a developer token, audit-logged."""
    log(f"refused: {what}", ok=False)
    abort(403, DEVELOPER_SCOPE + rest)


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
    # those being created have status "creating"; other servers' instances as their agents last reported them
    names = sorted(set(store.names()) | set(jobs.being_created()) | set(servers.remote_names()))
    return page(access.visible_instances(g.user, names), limit, offset, any_resource)


@blueprint.get("/deleted-instances")
@require("instances.view")
def deleted_instances() -> Response:
    """The instances deleted in the last DELETED_HISTORY_DAYS days (the instance list's Deleted tab), latest first:
    `name`, `project` (`id`, `name`; or null), `branch`, `stage`, `created_at`, `deleted_at`, `deleted_by` (null when
    the lifetime ended: `expired`). Those of a project the caller may not see are left out."""
    from sudoo.instances import history
    from sudoo.web.v1.responses import iso

    rows = []
    for found in history.recent():
        project = projects.get(found["project"]) if found["project"] else None
        if not access.sees_everything(g.user) and (
            project is None or "instances.view" not in access.permissions(g.user, project, found["branch"])
        ):
            continue
        rows.append(
            {
                "name": found["name"],
                "project": {"id": project["id"], "name": project["name"]} if project else None,
                "branch": found["branch"],
                "stage": found["stage"],
                "created_at": iso(found["created_at"]),
                "deleted_at": iso(found["deleted_at"]),
                "deleted_by": found["deleted_by"],
                "expired": found["expired"],
            }
        )
    return ok(rows)


def any_resource(name: str) -> dict[str, Any]:
    """An instance of this server, or of another server (premium/servers.py)."""
    found = None if store.exists(name) or jobs.creating(name) else servers.remote_resource(name)
    return found if found is not None else instance_resource(name)


@blueprint.get("/instances/<name>")
@require("instances.view")
def instance(name: str) -> Response:
    """The instance; meta.permissions: what the caller may do in it (role ∩ project access, locked branch)."""
    resource = instance_resource(known(name), full=True)
    project, branch = access.scope_of_instance(name)
    permitted = access.permissions(g.user, project, branch, access.instance_stage(name, project))
    if store.exists(name) and permitted & {"instances.shell", "instances.credentials"}:
        # for the commands a project's branch offers (SSH, psql): names only, never a password
        resource["containers"] = {
            "odoo": store.container(name, "odoo"),
            "db": store.container(name, "db"),
            "db_user": "odoo",
            "host": settings.public_host or None,
        }
    return ok(resource, meta={"permissions": sorted(permitted)})


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


def _plan_name(repo: str, branch: str, custom_name: str, version: str = "", edition: str = "") -> tuple[str, str]:
    return naming.plan_name(repo, branch, custom_name, version, edition)


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
    if found is not None and not (instance is not None and backups.belongs_to(instance, found)):
        # another instance's backup, or a deleted one's: its access applies too (restricted project, locked branch),
        # also once that instance is gone (the project and branch recorded in the backup)
        refuse_unusable(found, f"use backup {file_name[:80]}")
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
    stage = check.one_of(
        check.text(request.args, "stage", default=stages.DEFAULT, max_length=20), stages.STAGES, STAGE_MESSAGE
    )
    return ok(
        {
            "name": name,
            "database": database,
            "login": "admin",
            "defaults": stages.defaults(stage),
            "stage_defaults": {each: stages.defaults(each) for each in stages.STAGES},
            "production": production_capacity(),
            "servers": servers.choices(),  # where a new instance may go (the main server first)
            "engines": engines.offered(),  # docker, and kubernetes while its beta is on
        }
    )


STAGE_MESSAGE = "Choose a stage: production, staging or development."


@blueprint.get("/repositories/modules")
@require("instances.create")
def repository_modules() -> Response:
    """The Odoo modules of a repository's branch (its submodules' too), read from the panel's copy of the repository
    without creating anything (for API users: the panel's own form offers Odoo's apps only)."""
    address = check.repo_address(check.text(request.args, "url"))
    branch = check.branch(check.text(request.args, "branch", default="main"))
    found = projects.by_repository(github.repo_name(address) or "")
    if found is not None:  # the same access as creating an instance of that branch
        _refuse_in_branch("instances.create", found, branch, f"list the modules of {found['id']} {branch}")
    else:
        refused = access.refusal(g.user, "instances.create", None)
        if refused is not None:
            log("refused: list the modules of a repository outside every project", ok=False, detail=refused[1])
            abort(*refused)
    try:
        result = repository_module_list.of_branch(address, branch, refresh=request.args.get("refresh") == "true")
    except repository_module_list.BranchMissing as error:
        abort(404, str(error))
    except mirror.GitError as error:
        abort(502, str(error))
    return ok(result)


@blueprint.get("/odoo-apps")
@require("instances.create")
def standard_apps() -> Response:
    """The standard Odoo apps the module picker offers for a version and edition (instances/odoo_apps.py)."""
    version = check.text(request.args, "version", max_length=20)
    edition = check.one_of(
        check.text(request.args, "edition", default="community", max_length=20),
        ("community", "enterprise"),
        "Choose Community or Enterprise.",
    )
    return ok(odoo_apps.apps(version, edition), meta={"max_modules": settings.max_initial_modules})


def production_capacity() -> dict[str, Any]:
    """How many production instances there may be and are, and why one more is refused (None when it is not)."""
    return {
        "limit": stages.production_limit(),
        "instances": stages.production_instances(),
        "refusal": stages.production_refusal(),
    }


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
    stage = check.one_of(check.text(data, "stage", default=stages.DEFAULT, max_length=20), stages.STAGES, STAGE_MESSAGE)
    from_backup = check.text(data, "from_backup", max_length=200) or None
    if developer_token() and (stage != stages.DEVELOPMENT or from_backup):
        refuse_developer_token(f"create a {stage} instance", "only development instances, without a backup.")
    found = projects.by_repository(github.repo_name(repo) or "") if repo else None
    if found is not None:  # an instance of a project's branch: the caller's access in that branch
        _refuse_in_branch("instances.create", found, branch, f"create an instance of {found['id']} {branch}")
    else:  # outside every project (Sudoo.sh support: only with a grant for "all")
        refused = access.refusal(g.user, "instances.create", None)
        if refused is not None:
            log("refused: create an instance outside every project", ok=False, detail=refused[1])
            abort(*refused)
    if from_backup:
        refuse_unless("instances.restore", "create an instance from a backup")
        if found is not None:
            _refuse_in_branch("instances.restore", found, branch, "create an instance from a backup")
        from_backup = _usable_backup(from_backup, version, edition)
    with quotas.LOCK:  # until the job is registered: from then on it counts toward the limits
        return _start_creation(data, repo, branch, version, edition, custom_name, from_backup, stage)


def _refuse_in_branch(permission: str, project: Mapping[str, Any], branch: str, what: str) -> None:
    """403 (404 when the branch is hidden from the caller) unless the caller has `permission` in the project's
    branch (projects/access.py), audit-logged."""
    found = access.refusal(g.user, permission, project, branch)
    if found is not None:
        log(f"refused: {what}", ok=False, detail=found[1])
        abort(*found)


def _start_creation(
    data: Mapping[str, Any],
    repo: str,
    branch: str,
    version: str,
    edition: str,
    custom_name: str,
    from_backup: str | None,
    stage: str,
    project: str | None = None,
    from_production: str | None = None,
) -> Response:
    """Check the limits and start the creation (the caller holds quotas.LOCK). `project`: the project's id when the
    instance is made for one of its branches (web/v1/projects.py); `from_production`: see planning.build."""
    refuse_unlinked("creating instances", "instance creation refused (panel not linked)")
    modules = check.initial_modules(data)  # before anything counts toward the limits
    server_id = _placement(data, project, repo, from_backup, from_production)
    engine = check.one_of(
        check.text(data, "engine", default=engines.DOCKER, max_length=20),
        engines.offered(),
        "Choose an engine this panel offers (Kubernetes is a beta: KUBERNETES_ENGINE_BETA).",
    )
    if engine != engines.DOCKER and server_id != servers.MAIN:
        abort(400, "The Kubernetes engine (beta) runs instances of the main server only.")
    if engine != engines.DOCKER and edition == "enterprise":
        abort(400, "Enterprise instances cannot run on the Kubernetes engine yet (beta).")
    waits = False
    if server_id == servers.MAIN:  # another server's agent checks its own running instances
        waits = _refuse_over_capacity("instance creation refused (running instances)", stage, g.user["name"])
    defaults = stages.defaults(stage)
    if stage == stages.PRODUCTION:
        refused = stages.production_refusal() or _project_production_refusal(project, repo, branch)
        if refused:
            log("instance creation refused (production instances)", ok=False, detail=refused)
            abort(409, refused)
        if data.get("delete_after_days", 0) != 0:
            abort(400, PRODUCTION_LIFETIME)
        if check.flag(data, "auto_pause"):
            abort(400, PRODUCTION_PAUSE)
    try:
        quotas.claim(g.user["name"])
    except quotas.QuotaError as error:
        g.retry_after = error.retry_after
        log("instance creation refused (limit)", ok=False, detail=error.message)
        abort(error.status, error.message)
    switches = {
        "delete_after_days": (
            check.days(data, "delete_after_days") if "delete_after_days" in data else defaults["delete_after_days"]
        ),
        # automation switches: the stage's defaults (stages.defaults: the panel's AUTO_PAUSE, AUTO_UPDATE,
        # AUTO_UPGRADE; production never pauses and gets new code only when PRODUCTION_AUTO_UPDATE says) unless sent
        **{key: check.flag(data, key, bool(defaults[key])) for key in ("auto_update", "auto_upgrade", "auto_pause")},
    }
    plan_data = planning.build(
        repo=repo,
        branch=branch,
        version=version,
        edition=edition,
        stage=stage,
        user=g.user["name"],
        switches=switches,
        custom_name=custom_name,
        demo=check.flag(data, "demo"),
        install_modules=check.flag(data, "install_modules"),
        modules=modules,
        from_backup=from_backup,
        from_production=from_production,
        project=project,
    )
    name = plan_data["name"]
    if engine != engines.DOCKER:
        plan_data["engine"] = engine
    if server_id != servers.MAIN:
        return _start_remote_creation(server_id, plan_data)
    if waits:
        waiting = queue.enqueue_create(plan_data, g.user["name"], g.user["name"], access.role_permissions(g.user))
        log("Create instance (waiting for capacity)", instance=name, detail=f"Odoo {version} {edition}, {stage}")
        return _queued(waiting)
    job = planning.start(plan_data, g.user["name"])
    log(
        "Create instance",
        instance=name,
        detail=f"Odoo {version} {edition}, {stage}"
        + ("" if repo else " (plain, no repository)")
        + (f" from {from_backup}" if from_backup else "")
        + (f" from a copy of {from_production}" if from_production else "")
        + (f", modules: {', '.join(modules)[:300] or 'none'}" if modules is not None else ""),
    )
    response = _accepted(job)
    response.headers["Content-Location"] = f"{PREFIX}/instances/{name}"
    return response


def _placement(
    data: Mapping[str, Any], project: str | None, repo: str, from_backup: str | None, from_production: str | None
) -> str:
    """The server the new instance goes to (premium/servers.py): the one asked for ("server"), else the project's
    default server, else this one. 400/409/503 when it cannot go there, 402 without several servers."""
    requested = check.text(data, "server", max_length=20) or None
    found = (
        projects.get(project) if project else (projects.by_repository(github.repo_name(repo) or "") if repo else None)
    )
    try:
        return servers.choose(requested, found, from_backup=from_backup, from_production=from_production)
    except servers.Refused as error:
        log("instance creation refused (server)", ok=False, detail=error.message[:200])
        abort(error.status, error.message)


def _start_remote_creation(server_id: str, plan_data: dict[str, Any]) -> Response:
    """Pass a planned creation to the agent of another server: its job runs there."""
    store.release_ports(plan_data.pop("ports"))  # the other server reserves its own
    try:
        job = servers.start_remote(server_id, plan_data, g.user["name"])
    except servers.Refused as error:
        abort(error.status, error.message)
    log(
        "Create instance",
        instance=plan_data["name"],
        detail=f"Odoo {plan_data['version']} {plan_data['edition']}, {plan_data['stage']}, "
        f"on {servers.reference(server_id)['name']}",
    )
    response = ok(job, status=202)
    response.headers["Location"] = f"{PREFIX}/jobs/{job['id']}"
    response.headers["Content-Location"] = f"{PREFIX}/instances/{plan_data['name']}"
    return response


# ---------------------------------------------------------------- changing


@blueprint.patch("/instances/<name>")
@require("instances.operate")
def change(name: str) -> Response:
    """Change only the settings that are sent; the others keep their value.

    A lifetime that makes the instance expire sooner than now (also from "keep" to any number of days) deletes it
    sooner, so it needs instances.delete as well.

    "stage" moves the instance to another stage (instances/stages.py). Into production: refused beyond
    stages.production_limit(); the instance is then kept (lifetime "keep") and no longer pauses when idle. Out of
    production: only with "confirm_production": true, and with instances.delete (it can then expire). A production
    instance refuses a lifetime and the automatic pause."""
    existing(name)
    data = check.body()
    if developer_token():
        refuse_developer_token(f"change settings of {name}", "it cannot change an instance's settings or stage.")
    if _connected_app():
        _check_connected_app_change(name, data)
    with quotas.LOCK:  # moving into production counts like a creation: the check and the change happen together
        return _change(name, data)


def _change(name: str, data: Mapping[str, Any], history: bool = True) -> Response:
    """`history`: record a change of the branch's stage in its history (False when the branch's own stage change
    moves its instances: that records it once)."""
    env = store.read_env(name)
    stage_before = stages.of(env)
    stage = stage_before
    if "stage" in data:
        stage = check.one_of(check.text(data, "stage", max_length=20), stages.STAGES, STAGE_MESSAGE)
    switches = {"auto_update": "AUTO_UPDATE", "auto_upgrade": "AUTO_UPGRADE", "auto_pause": "AUTO_PAUSE"}
    values: dict[str, object] = {
        key: str(check.flag(data, field)).lower() for field, key in switches.items() if field in data
    }
    if stage == stages.PRODUCTION:
        if values.get("AUTO_PAUSE") == "true":
            abort(409, PRODUCTION_PAUSE)
        if "delete_after_days" in data and check.days(data, "delete_after_days") != 0:
            abort(409, PRODUCTION_LIFETIME)
    if stage != stage_before:
        _check_stage_change(name, stage_before, stage, data)
        values["STAGE"] = stage
        if stage == stages.PRODUCTION:
            values |= {"AUTO_PAUSE": "false", "DELETE_AFTER_DAYS": 0}
    if "delete_after_days" in data and stage != stages.PRODUCTION:
        values["DELETE_AFTER_DAYS"] = check.days(data, "delete_after_days")
    # the expiry with every change of the request applied (a stage leaving production and a lifetime together too)
    current = operations.expires_at(env)
    proposed = operations.expires_at({**env, **{key: str(value) for key, value in values.items()}})
    if proposed is not None and (current is None or proposed < current):
        refuse_unless("instances.delete", f"shorten the lifetime of {name}", instance=name)
    extended = _extends(current, proposed)
    if extended:
        _check_extension(name, env, current, proposed)
    if not values and not {"delete_after_days", "stage"} & set(data):
        abort(400, "Send at least one of: auto_update, auto_upgrade, auto_pause, delete_after_days, stage.")
    if not values:  # the stage it has already, or a production instance's lifetime "keep": nothing changes
        return ok(instance_resource(name, full=True))
    if values.get("AUTO_UPDATE") == "true" or values.get("AUTO_UPGRADE") == "true":
        refuse_plain(name)
    store.update_env(name, **values)
    if "DELETE_AFTER_DAYS" in values:  # a new lifetime: automatic deletion starts counting its attempts again
        STATE.update(name, expiry_failures=0, expiry_stopped=False)
    restart_note = _extended(name, env) if extended else None
    if "STAGE" in values:
        log("instance stage changed", instance=name, detail=f"{stage_before} -> {stage}")
        from sudoo.instances import managed_names

        managed_names.wake()  # its managed name names the stage
        if env.get("PROJECT") and env.get("GIT_BRANCH") and (project := projects.get(env["PROJECT"])):
            # the branch takes the stage its instance was given (projects/: a branch's stage is recorded)
            branch_before = (project.get("branches") or {}).get(env["GIT_BRANCH"], {}).get("stage") or stage_before
            projects.record_branch(
                env["PROJECT"], env["GIT_BRANCH"], stage=stage, changed_by=g.user["name"], changed_at=int(time.time())
            )
            if history:
                from sudoo.projects import builds

                builds.stage_changed(env["PROJECT"], env["GIT_BRANCH"], branch_before, stage, g.user["name"],
                                     reason=f"stage of {name} changed")  # fmt: skip
        mail_catcher.stage_changed(name)  # production sends its own e-mail; the other stages to the mail catcher
    log("instance settings changed", instance=name, detail=", ".join(sorted(values)))
    events.publish("instance", name)
    return ok(instance_resource(name, full=True), meta={"restart": restart_note} if restart_note else None)


def _extends(current: float | None, proposed: float | None) -> bool:
    """Whether the new expiry is later than the current one (None is "keep": no expiry, the latest of all)."""
    if current is None:
        return False
    return proposed is None or proposed > current


def _unlimited_extension(name: str) -> bool:
    """Whether the caller may extend an instance without the limits (instances.extend_unlimited). A connected app
    never may, whatever role its token has."""
    if _connected_app():
        return False
    return access.allowed_instance(g.user, "instances.extend_unlimited", name)


def _check_extension(name: str, env: Mapping[str, Any], current: float | None, proposed: float | None) -> None:
    """403 when the caller may not extend the lifetime of `name` like this: without instances.extend_unlimited, by at
    most EXTENSION_MAX_DAYS, EXTENSION_MAX_COUNT times, never to "keep", and never an instance stopped at the end of
    its lifetime (an administrator restarts that one by extending it)."""
    if _unlimited_extension(name):
        return
    what = f"extend the lifetime of {name}"
    if STATE.get(name).get("expired_at"):
        _refuse_extension(what, f"{name} was stopped at the end of its lifetime: an administrator extends it.")
    if proposed is None or current is None:  # (current is None cannot be: that is not an extension)
        _refuse_extension(
            what, "Keeping an instance without an end date needs the permission to extend without limits."
        )
    added = -(-int(proposed - current) // 86400)  # whole days, rounded up
    if added > settings.expiry_extend_max_days:
        _refuse_extension(
            what, f"You can extend a lifetime by at most {settings.expiry_extend_max_days} days at a time."
        )
    made = int(env.get("EXTENSIONS") or 0)
    if made >= settings.expiry_extend_max_count:
        _refuse_extension(
            what, f"This instance was extended {made} time{'s' if made != 1 else ''} already, the most you can do."
        )


def _refuse_extension(what: str, reason: str) -> NoReturn:
    message = f"{reason} {settings.contact_sentence()}"
    log(f"{what} refused", ok=False, detail=reason)
    abort(403, message)


def _extended(name: str, before: Mapping[str, Any]) -> str | None:
    """After an extension: count it, and when the instance had been stopped at the end of its lifetime, start it
    again unless the server cannot take one more running instance now. Returns the reason it stays stopped, or None."""
    if not _unlimited_extension(name):
        store.update_env(name, EXTENSIONS=int(before.get("EXTENSIONS") or 0) + 1)
    if not STATE.get(name).get("expired_at"):
        return None
    STATE.update(name, expired_at=None)
    with quotas.LOCK:  # from the limit check until the start's job is registered, as in run()
        refused = capacity.start_refusal(name)
        if not refused:
            jobs.start(name, "Start", "wake", operations.wake, name, user=g.user["name"])
    if refused:
        log(f"{name} extended but not restarted", ok=False, detail=refused)
        return f"The lifetime was extended, but {name} stays stopped. {refused}"
    log("started again after its lifetime was extended", instance=name)
    return None


PRODUCTION_LIFETIME = "A production instance is never deleted automatically: its lifetime is always keep (0)."
PRODUCTION_PAUSE = "A production instance is never paused automatically."


def _project_production_refusal(project_id: str | None, repo: str, branch: str, name: str = "") -> str | None:
    """Why `branch` of the project (`project_id`, else the project of `repo`) may not be one more production branch
    (PROJECT_PRODUCTION_BRANCHES, then the plan: entitlements.py), or None. An instance outside every project (plain
    Odoo, `name`; "" for one not created yet) counts as one production branch of the plan."""
    found = projects.get(project_id or "") or (projects.by_repository(github.repo_name(repo) or "") if repo else None)
    if found and branch:
        return projects.production_refusal(found, branch)
    return entitlements.production_refusal(name or "\0new")


def refuse_unlinked(what: str, logged: str) -> None:
    """403 once the days to link the panel to a Sudoo.sh account have passed while it is unlinked
    (link_deadline.py), audit-logged."""
    refused = link_deadline.refusal(what)
    if refused:
        log(logged, ok=False, detail=refused)
        abort(403, refused)


def _check_stage_change(name: str, before: str, after: str, data: Mapping[str, Any]) -> None:
    """Into production: within the limits (the server's, and the project's for its branch), and not while the panel
    waits for its link (link_deadline.py). Out of production: only when confirmed, and with instances.delete."""
    if after == stages.PRODUCTION and before != stages.PRODUCTION:
        refuse_unlinked("moving instances into production", f"stage change of {name} refused (panel not linked)")
    if after == stages.PRODUCTION:
        env = store.read_env(name)
        refused = stages.production_refusal(exclude=name) or _project_production_refusal(
            env.get("PROJECT"), "", env.get("GIT_BRANCH", ""), name
        )
        if refused:
            log(f"stage change of {name} refused (production instances)", ok=False, detail=refused)
            abort(409, refused)
    if before == stages.PRODUCTION and check.flag(data, "confirm_production") is not True:
        abort(
            400,
            f"{name} is a production instance. Confirm that it leaves production "
            '("confirm_production": true): it can then be paused and given a lifetime.',
        )
    if before == stages.PRODUCTION:
        # out of production it can be given a lifetime and expire: as much as deleting it
        refuse_unless("instances.delete", f"move {name} out of production", instance=name)


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


PRODUCTION_CONFIRMED = {
    "pause": "pause",
    "switch-off": "switch off",
    "restore": "restore a backup into",
    "rollback": "roll back",
    "change-engine": "move to another engine (it is switched off during the move)",
}
"""Actions on a production instance that need "confirm_production": true (deleting has its own confirmation)."""


def _job_for(action: str, name: str, data: Mapping[str, Any]) -> tuple[Callable[..., Any], tuple[Any, ...]]:
    """(function, arguments) of the job for `action`, after validating the request data."""
    if action in REPOSITORY_ACTIONS:
        refuse_plain(name)
    if action == "connect-repository":
        if has_repository(name):
            abort(409, f"{name} has a GitHub repository already. Switch branch changes its code.")
        repo = check.repo_address(check.text(data, "repo"))
        branch = check.branch(check.text(data, "branch", default="main"))
        joined = projects.by_repository(github.repo_name(repo) or "")
        if joined is not None:  # the instance joins that project's branch: as creating an instance of it
            _refuse_in_branch("instances.create", joined, branch, f"connect {name} to {joined['id']} {branch}")
        _refuse_joining_production(name, repo, branch)
        return operations.connect_repository, (name, repo, branch, check.flag(data, "install_modules"))
    simple: dict[str, tuple[Callable[..., Any], tuple[Any, ...]]] = {
        "update": (operations.update_code, (name,)),
        "update-submodules": (operations.update_code, (name, True)),
        "restart": (operations.restart, (name,)),
        "start": (operations.wake, (name,)),
        "pause": (operations.pause, (name,)),
        "backup": (operations.backup, (name, "manual", check.text(data, "comment", max_length=backups.COMMENT_MAX))),
        "switch-off": (operations.switch_off, (name,)),
        "update-images": (operations.update_images, (name,)),
        "repair": (operations.repair, (name,)),
    }
    production = stages.is_production(store.read_env(name))
    if production and action in PRODUCTION_CONFIRMED and check.flag(data, "confirm_production") is not True:
        abort(
            400,
            f"{name} is a production instance: confirm that you want to {PRODUCTION_CONFIRMED[action]} it "
            '("confirm_production": true).',
        )
    if action in simple:
        return simple[action]
    if action == "delete":
        if check.text(data, "confirm", max_length=40) != name:
            abort(400, "Type the instance name exactly to confirm.")
        if production and check.text(data, "confirm_stage", max_length=20) != stages.PRODUCTION:
            abort(400, 'This is a production instance: type "production" as well to confirm.')
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
    if action == "change-engine":
        kind = check.one_of(
            check.text(data, "engine", max_length=20), engines.offered(), "Choose an engine this panel offers."
        )
        if kind == engines.kind_of(store.read_env(name)):
            abort(409, f"{name} runs on {kind} already.")
        if kind == engines.KUBERNETES and store.read_env(name).get("EDITION") == "enterprise":
            abort(409, "Enterprise instances cannot run on the Kubernetes engine yet (beta).")
        return operations.change_engine, (name, kind)
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
        branch = check.branch(check.text(data, "branch"))
        undo = _move_production_branch(name, branch)
        if undo is not None:
            return _switch_production_branch, (name, branch, undo)
        return operations.switch_branch, (name, branch)
    path = check.text(data, "path")
    if action == "add-submodule":
        url = check.repo_address(check.text(data, "url"))
        branch = check.branch(check.text(data, "branch"), required=False)
        path = check.submodule_path(path or cast(str, github.repo_name(url)).split("/")[-1])
        return operations.add_submodule, (name, url, path, branch)
    return operations.remove_submodule, (name, check.submodule_path(path))


def _move_production_branch(name: str, branch: str) -> Callable[[], None] | None:
    """A production instance moved to another branch makes that branch a production branch in place of its earlier
    one, which stays production only while another production instance follows it: 409 when that is beyond the
    project's or the plan's limits, as for a stage change. Within them the project's records follow at once, under
    the same lock as the check; returns what puts them back (None: nothing recorded)."""
    env = store.read_env(name)
    before = env.get("GIT_BRANCH", "")
    if not stages.is_production(env) or branch == before:
        return None
    project = projects.get(env.get("PROJECT", ""))
    with quotas.LOCK:
        if project is None:  # outside every project: the instance is the plan's key, whatever its branch
            refused = entitlements.production_refusal(name)
        else:
            keep_before = projects.stays_production(project["id"], before, without=name)
            refused = projects.production_refusal(project, branch, replacing=None if keep_before else before)
        if refused:
            log(f"switch branch of {name} refused (production branches)", ok=False, detail=refused)
            abort(409, refused)
        if project is None:
            return None
        return projects.move_production(project["id"], before, branch, g.user["name"], keep_before)


def _switch_production_branch(job: Any, name: str, branch: str, undo: Callable[[], None]) -> None:
    """operations.switch_branch of a production instance: when the instance did not reach the new branch, the
    project's records of its production branches are put back."""
    try:
        operations.switch_branch(job, name, branch)
    except BaseException:
        if store.read_env(name).get("GIT_BRANCH") != branch:
            undo()
        raise


def _refuse_joining_production(name: str, repo: str, branch: str) -> None:
    """A plain production instance given a repository joins that repository's project as a production branch, in
    place of counting for itself in the plan: 409 beyond the project's limit (or the plan's)."""
    if not stages.is_production(store.read_env(name)):
        return
    found = projects.by_repository(github.repo_name(repo) or "")
    if found is None:  # a new project: its one production branch replaces the instance in the plan's count
        return
    with quotas.LOCK:
        refused = projects.production_refusal(found, branch, replacing_key=name)
    if refused:
        log(f"connecting a repository to {name} refused (production branches)", ok=False, detail=refused)
        abort(409, refused)


def _running(name: str) -> bool:
    return name in capacity.counted_names()


def _refuse_over_capacity(what: str, stage: str | None = None, user: str | None = None, *, queued: bool = True) -> bool:
    """403 when MAX_RUNNING_INSTANCES instances run already, or when `user` has the most non-production instances
    running they may (both off by default: the panel only warns). Never for a production instance (`stage`).
    With CAPACITY_QUEUE on, True instead of 403: the request waits (limits/queue.py); `queued` False: it cannot wait
    (the caller needs the answer now, such as an upgrade test), so it is refused as without the queue."""
    refused = capacity.refusal(stage=stage, user=user)
    if not refused:
        return False
    if queued and queue.enabled():
        return True
    log(what, ok=False, detail=refused)
    abort(403, refused)


def admit_start(name: str, what: str) -> None:
    """403 when `name` is stopped and starting it would pass a running limit (the instance's own owner and stage, read
    from its .env; no queue: the caller needs the answer now). The caller holds quotas.LOCK from here until the
    job is registered with holds_slot. A running instance is not asked: it holds its slot already."""
    if _running(name):
        return
    env = store.read_env(name)
    _refuse_over_capacity(what, stages.of(env), env.get("CREATED_BY") or None, queued=False)


def _queued(item: Mapping[str, Any]) -> Response:
    """202 for a request that waits for capacity: the item (position, status), and where to follow it."""
    response = ok(queue.resource(item), status=202)
    response.headers["Location"] = f"{PREFIX}/capacity-queue/{item['id']}"
    response.headers["Content-Location"] = f"{PREFIX}/instances/{item['instance']}"
    return response


def run(name: str, action: str) -> Response:
    existing(name)
    if action not in ACTIONS:
        abort(404, "Unknown action.")
    title, kind, permission = ACTIONS[action]
    if developer_token() and action not in DEVELOPER_ACTIONS | {"delete"}:
        refuse_developer_token(
            f"{action} on {name}", "of the actions only start, pause, restart, update and update-submodules."
        )
    if developer_token() and action == "delete" and stages.of(store.read_env(name)) != stages.DEVELOPMENT:
        refuse_developer_token(f"delete {name}", "it deletes development instances only.")
    if _connected_app() and action not in CONNECTED_APP_ACTIONS:
        _refuse_connected_app(
            f"{action} on {name}",
            "This API token has the instances scope: of the actions it can only start, pause, restart, back up "
            "and delete.",
        )
    refuse_unless(permission, f"{action} on {name}")
    if action in link_deadline.DEPLOY_ACTIONS:
        refuse_unlinked("updating and deploying instances", f"{action} on {name} refused (panel not linked)")
    fn, args = _job_for(action, name, check.body())
    build = {"build": "manual"} if action in BUILD_ACTIONS else None  # a new build in the branch's history
    with quotas.LOCK:  # from the limit check until the start's job is registered (it counts as running from then on)
        if action in STARTING_ACTIONS and not _running(name):
            env = store.read_env(name)
            if _refuse_over_capacity(
                f"{action} of {name} refused (running instances)",
                stages.of(env),
                env.get("CREATED_BY") or None,
                queued=action == "start",  # only a plain start can wait in line
            ):
                waiting = queue.enqueue_start(
                    name, g.user["name"], env.get("CREATED_BY") or None, stages.of(env), access.role_permissions(g.user)
                )
                log("Start (waiting for capacity)", instance=name)
                return _queued(waiting)
        job = jobs.start(name, title, kind, fn, *args, user=g.user["name"], details=build,
                         holds_slot=True if action in STARTING_ACTIONS else None)  # fmt: skip
    log(title, instance=name)
    return _accepted(job)


@blueprint.post("/instances/<name>/actions/<action>")
def action(name: str, action: str) -> Response:
    return run(name, action)


@blueprint.delete("/instances/<name>")
def delete(name: str) -> Response:
    return run(name, "delete")


# ---------------------------------------------------------------- resources


@blueprint.get("/instances/<name>/resources")
@require("instances.view")
def read_resources(name: str) -> Response:
    return ok(resources.resource(existing(name)))


@blueprint.put("/instances/<name>/resources")
@require("instances.resources")
def change_resources(name: str) -> Response:
    """Set the instance's own limits and apply them (a job): Odoo is restarted, or recreated when its container
    changes. On production: only when confirmed, after a backup, and put back when Odoo does not answer."""
    existing(name)
    data = check.body()
    raw = data.get("limits")
    if not isinstance(raw, dict):
        abort(400, 'Send the limits to set: {"limits": {"odoo_workers": 2, "odoo_memory_limit": "4g"}}.')
    try:
        limits = resources.clean(raw)
    except resources.Invalid as error:
        abort(400, str(error))
    if stages.is_production(store.read_env(name)) and check.flag(data, "confirm_production") is not True:
        abort(
            400,
            f"{name} is a production instance: applying resources restarts it. Confirm "
            '("confirm_production": true).',
        )
    stage = resources.stage_of(name)
    after = resources.resolve(resources.layers(name, stage) | {"instance": limits})
    refused = resources.plan({name: (stage, after)})["refusal"] if after != resources.effective(name, stage) else None
    if refused:
        log("instance resources refused", instance=name, ok=False, detail=refused)
        abort(409, refused)
    previous = resources.overrides(name)
    resources.save_overrides(name, limits, g.user["name"])
    job = jobs.start(
        name, "Apply resources", "restart", operations.apply_resources, name, previous, user=g.user["name"],
        holds_slot=True,
    )  # fmt: skip
    log(
        "instance resources changed",
        instance=name,
        detail=", ".join(f"{k}={v}" for k, v in sorted(limits.items())) or "stage defaults",
    )
    return _accepted(job)


# ---------------------------------------------------------------- jobs


@blueprint.get("/jobs")
@require("instances.view")
def list_jobs() -> Response:
    limit, offset = _limits(default=30)
    instance_name = request.args.get("instance")
    if instance_name:
        check.instance_name(instance_name)
    found: list[Any] = [j for j in reversed(jobs.JOBS.values()) if not instance_name or j.instance == instance_name]
    remote = servers.remote_jobs(instance_name or None)  # resources other servers' agents reported
    if remote:  # one list, newest first: this server's jobs as resources too
        found = sorted(
            [job_resource(j) for j in found] + remote, key=lambda j: str(j.get("started_at") or ""), reverse=True
        )

    def instance_of(job: Any) -> str:
        return str(job["instance"] if isinstance(job, dict) else job.instance)

    shown = set(
        access.visible_instances(g.user, {instance_of(j) for j in found if not jobs.is_panel_job(instance_of(j))})
    )
    found = [j for j in found if jobs.is_panel_job(instance_of(j)) or instance_of(j) in shown]
    return page(found, limit, offset, lambda j: j if isinstance(j, dict) else job_resource(j))


def _job(job_id: str, permission: str = "instances.view") -> jobs.Job:
    """The job, when the caller has `permission` in its instance (404 for an instance hidden from them)."""
    found = jobs.JOBS.get(job_id) or abort(404, "This job does not exist or is too old to be kept.")
    if not jobs.is_panel_job(found.instance):
        refusal = access.instance_refusal(g.user, permission, found.instance)
        if refusal is not None:
            log(f"refused: {permission} in job {job_id}", instance=found.instance, ok=False)
            if refusal[0] == 404:
                abort(404, "This job does not exist or is too old to be kept.")
            abort(*refusal)
    return found


@blueprint.get("/jobs/<job_id>")
@require("instances.view")
def job(job_id: str) -> Response:
    found = job_resource(_job(job_id))  # never its technical log: that stays in the server's log
    if found["error"]:
        found["error"] = message_for_user(found["error"])
    return ok(found)


# ---------------------------------------------------------------- versions


@blueprint.get("/versions")
@require("instances.view")
def versions() -> Response:
    return ok([{"version": v, "enterprise": addons.available(v)} for v in odoo.versions()])
