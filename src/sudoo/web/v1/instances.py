"""Instances, jobs and Odoo versions.

    GET    /api/v1/instances?limit=&offset=          list (instances being created too, status "creating")
    GET    /api/v1/instances/<name>                  one instance, with commits, submodules and recent jobs; while
                                                     its creation is queued or running: status "creating" and the
                                                     job as current_job (404 once a failed creation left nothing)
    GET    /api/v1/instances/<name>/credentials      Odoo admin and master passwords, ports
    GET    /api/v1/instances/<name>/logs?lines=      the end of Odoo's log
    POST   /api/v1/instances                         create; {"from_backup"} optional; without "repo": plain Odoo
                                                     (no code, no branch, no update); "stage" (development by
                                                     default; production within stages.production_limit())
                                                     -> 202, Location: job
    PATCH  /api/v1/instances/<name>                  change automation settings, lifetime and stage (a lifetime
                                                     that ends sooner also needs instances.delete; leaving
                                                     production needs "confirm_production": true)
    POST   /api/v1/instances/<name>/actions/<action> restart, update, pause, ...    -> 202, Location: job
                                                     restore: {"file"} optional (any instance's backup of the
                                                     same Odoo series), the latest backup otherwise
                                                     rollback: back to before the last restore (ROLLBACK_HOURS)
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

A token with the developer scope (roles.DEVELOPER_TOKEN) is narrowed too: only the actions in DEVELOPER_ACTIONS,
creating and deleting development instances only, never PATCH (settings, lifetime, stage).

A production instance (instances/stages.py) asks for more: pausing, switching off, restoring and rolling back need
"confirm_production": true in the body, deleting needs "confirm_stage": "production" besides its name.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, cast

from flask import Blueprint, Response, abort, g, request

from sudoo import addons, entitlements, link_deadline, projects, roles, servers
from sudoo.config import settings
from sudoo.core import events, jobs, vault
from sudoo.core.state import STATE
from sudoo.instances import backups, capacity, metrics, naming, odoo, operations, planning, resources, stages, store
from sudoo.limits import quotas
from sudoo.mailcatcher import panel as mail_catcher
from sudoo.projects import access
from sudoo.repos import github
from sudoo.repos.gitrepo import origin_url
from sudoo.servers import forward, inventory, placement
from sudoo.servers import registry as servers_registry

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
    names = sorted(set(store.names()) | set(jobs.being_created()) | set(inventory.remote_names()))
    return page(access.visible_instances(g.user, names), limit, offset, any_resource)


def any_resource(name: str) -> dict[str, Any]:
    """An instance of the main server, or of another server (servers/forward.py)."""
    found = None if store.exists(name) or jobs.creating(name) else forward.remote_resource(name)
    return found if found is not None else instance_resource(name)


@blueprint.get("/instances/<name>")
@require("instances.view")
def instance(name: str) -> Response:
    """The instance; meta.permissions: what the caller may do in it (role ∩ project access, locked branch)."""
    resource = instance_resource(known(name), full=True)
    project, branch = access.scope_of_instance(name)
    return ok(resource, meta={"permissions": sorted(access.permissions(g.user, project, branch))})


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
    if found is not None and found["instance"] != instance and store.exists(found["instance"]):
        # another instance's backup: that instance's access applies too (restricted project, locked branch)
        refuse_unless("backups.manage", f"use backup {file_name[:80]}", instance=found["instance"])
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
            "servers": placement.choices(),
        }
    )


STAGE_MESSAGE = "Choose a stage: production, staging or development."


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
    server_id = _placement(data, project, repo, from_backup, from_production)
    if server_id == servers.MAIN:  # another server's agent checks its own running instances
        _refuse_over_capacity("instance creation refused (running instances)")
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
        from_backup=from_backup,
        from_production=from_production,
        project=project,
    )
    name = plan_data["name"]
    if server_id != servers.MAIN:
        return _start_remote_creation(server_id, plan_data)
    job = planning.start(plan_data, g.user["name"])
    log(
        "Create instance",
        instance=name,
        detail=f"Odoo {version} {edition}, {stage}"
        + ("" if repo else " (plain, no repository)")
        + (f" from {from_backup}" if from_backup else "")
        + (f" from a copy of {from_production}" if from_production else ""),
    )
    response = _accepted(job)
    response.headers["Content-Location"] = f"{PREFIX}/instances/{name}"
    return response


def _placement(
    data: Mapping[str, Any], project: str | None, repo: str, from_backup: str | None, from_production: str | None
) -> str:
    """The server the new instance goes to (servers/placement.py): the one asked for, else the project's default
    server, else the main one. 400/409/503 when it cannot go there."""
    requested = check.text(data, "server", max_length=20) or None
    found = (
        projects.get(project) if project else (projects.by_repository(github.repo_name(repo) or "") if repo else None)
    )
    try:
        server_id = placement.choose(requested, found)
    except placement.Refused as error:
        log("instance creation refused (server)", ok=False, detail=error.message[:200])
        abort(error.status, error.message)
    if server_id == servers.MAIN:
        return server_id
    if from_backup:
        abort(
            409,
            "A new instance starts from a backup on the main server only: create it there from the backup, then "
            "move it to the other server (Move to another server).",
        )
    if from_production and inventory.server_of(from_production) != server_id:
        abort(
            409,
            f"A staging copy is made on the server of its production instance ({from_production} runs on "
            f"{servers_registry.name_of(inventory.server_of(from_production))}). Choose that server.",
        )
    return server_id


def _start_remote_creation(server_id: str, plan_data: dict[str, Any]) -> Response:
    """Pass a planned creation to the agent of another server: its job runs there (servers/agent.py)."""
    from sudoo.servers import links

    store.release_ports(plan_data.pop("ports"))  # the other server reserves its own
    name = plan_data["name"]
    links.expect_owner(server_id, plan_data.get("repo") or "")
    try:
        job = links.call(server_id, "create", {"plan": plan_data, "user": g.user["name"]})
    except links.Offline:
        abort(503, f"The server {servers_registry.name_of(server_id)} went offline. Choose another server.")
    except links.RemoteError as error:
        abort(error.status if 400 <= error.status < 600 else 502, error.message)
    planning.record_stage(plan_data, g.user["name"])
    inventory.instance_changed(server_id, name, {"name": name, "status": "creating"}, {
        "STAGE": plan_data["stage"], "PROJECT": plan_data.get("project") or "", "GIT_BRANCH": plan_data["branch"],
        "ODOO_VERSION": plan_data["version"], "EDITION": plan_data["edition"],
    })  # fmt: skip
    inventory.job_changed(server_id, job)
    log(
        "Create instance",
        instance=name,
        detail=f"Odoo {plan_data['version']} {plan_data['edition']}, {plan_data['stage']}, "
        f"on {servers_registry.name_of(server_id)}",
    )
    response = ok(job, status=202)
    response.headers["Location"] = f"{PREFIX}/jobs/{job['id']}"
    response.headers["Content-Location"] = f"{PREFIX}/instances/{name}"
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
    production: only with "confirm_production": true. A production instance refuses a lifetime and the automatic
    pause."""
    existing(name)
    data = check.body()
    if developer_token():
        refuse_developer_token(f"change settings of {name}", "it cannot change an instance's settings or stage.")
    if _connected_app():
        _check_connected_app_change(name, data)
    with quotas.LOCK:  # moving into production counts like a creation: the check and the change happen together
        return _change(name, data)


def _change(name: str, data: Mapping[str, Any]) -> Response:
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
        days = check.days(data, "delete_after_days")
        current = operations.expires_at(env)
        proposed = operations.expires_at({**env, "DELETE_AFTER_DAYS": str(days)})
        if proposed is not None and (current is None or proposed < current):
            refuse_unless("instances.delete", f"shorten the lifetime of {name}")
        values["DELETE_AFTER_DAYS"] = days
    if not values and not {"delete_after_days", "stage"} & set(data):
        abort(400, "Send at least one of: auto_update, auto_upgrade, auto_pause, delete_after_days, stage.")
    if not values:  # the stage it has already, or a production instance's lifetime "keep": nothing changes
        return ok(instance_resource(name, full=True))
    if values.get("AUTO_UPDATE") == "true" or values.get("AUTO_UPGRADE") == "true":
        refuse_plain(name)
    store.update_env(name, **values)
    if "DELETE_AFTER_DAYS" in values:  # a new lifetime: automatic deletion starts counting its attempts again
        STATE.update(name, expiry_failures=0, expiry_stopped=False)
    if "STAGE" in values:
        log("instance stage changed", instance=name, detail=f"{stage_before} -> {stage}")
        if env.get("PROJECT") and env.get("GIT_BRANCH") and projects.get(env["PROJECT"]):
            # the branch takes the stage its instance was given (projects/: a branch's stage is recorded)
            projects.record_branch(
                env["PROJECT"], env["GIT_BRANCH"], stage=stage, changed_by=g.user["name"], changed_at=int(time.time())
            )
        mail_catcher.stage_changed(name)  # production sends its own e-mail; the other stages to the mail catcher
    log("instance settings changed", instance=name, detail=", ".join(sorted(values)))
    events.publish("instance", name)
    return ok(instance_resource(name, full=True))


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
    waits for its link (link_deadline.py). Out of production: only when confirmed."""
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


def _running(name: str) -> bool:
    return bool(metrics.INSTANCES.get(name, {}).get("running"))


def _refuse_over_capacity(what: str) -> None:
    """403 when MAX_RUNNING_INSTANCES instances run already (off by default: the panel only warns)."""
    refused = capacity.refusal()
    if refused:
        log(what, ok=False, detail=refused)
        abort(403, refused)


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
    if action == "start" and not _running(name):
        _refuse_over_capacity(f"start of {name} refused (running instances)")
    job = jobs.start(name, title, kind, fn, *args, user=g.user["name"])
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
    previous = resources.overrides(name)
    resources.save_overrides(name, limits, g.user["name"])
    job = jobs.start(
        name, "Apply resources", "restart", operations.apply_resources, name, previous, user=g.user["name"]
    )
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
    remote = inventory.remote_jobs(instance_name or None)  # resources other servers reported (servers/)
    if remote:  # one list, newest first: the main server's jobs as resources too
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
    shown = _job(job_id)
    include_log = request.args.get("include") == "log" and (
        can("instances.logs")
        if jobs.is_panel_job(shown.instance)
        else access.allowed_instance(g.user, "instances.logs", shown.instance)
    )
    found = job_resource(shown, include_log=include_log)
    if found["error"]:
        found["error"] = message_for_user(found["error"])
    return ok(found)


@blueprint.get("/jobs/<job_id>/log")
@require("instances.logs")
def job_log(job_id: str) -> Response:
    """The job's log from line `offset` on; `meta.next_offset` is where to continue."""
    found = _job(job_id, "instances.logs")
    offset = check.whole_number(request.args.get("offset", "0"), "offset")
    lines = found.log[:]
    return ok({"text": "".join(lines[offset:])}, meta={"offset": offset, "next_offset": len(lines)})


# ---------------------------------------------------------------- versions


@blueprint.get("/versions")
@require("instances.view")
def versions() -> Response:
    return ok([{"version": v, "enterprise": addons.available(v)} for v in odoo.versions()])
