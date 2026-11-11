"""Instances: overview, details, creation, actions, logs and job progress."""
import re

from flask import Blueprint, abort, g, jsonify, request

from .. import gateway, github, instances, jobs, monitor, odoo, operations, settings, tunnels, vault
from ..gitrepo import commits, submodules
from ..state import STATE
from . import validation as check
from .security import admin_only, is_admin, log, message_for_user, refuse_unless_admin

blueprint = Blueprint("instances", __name__, url_prefix="/api")

# action -> (title shown in the activity list, job kind, allowed for the sync operator)
ACTIONS = {
    "update": ("Get latest code", "update", True),
    "update-submodules": ("Update submodules", "update", True),
    "restart": ("Restart", "restart", True),
    "start": ("Start", "wake", True),
    "add-submodule": ("Add submodule", "submodule", True),
    "pause": ("Pause", "pause", False),
    "switch-off": ("Switch off", "stop", False),
    "upgrade": ("Upgrade modules", "upgrade", False),
    "remove-submodule": ("Remove submodule", "submodule", False),
    "switch-branch": ("Switch branch", "update", False),
    "delete": ("Delete instance", "delete", False),
    "backup": ("Back up", "backup", False),
}


def _existing(name):
    if not instances.exists(check.instance_name(name)):
        abort(404, "This instance does not exist.")
    return name


def _summary(name):
    """What the instance list shows about an instance."""
    env, measured, state = instances.read_env(name), monitor.STATUS.get(name, {}), STATE.get(name)
    status = monitor.status(name)
    auto_pause = env["AUTO_PAUSE"] == "true" and settings.IDLE_HOURS > 0
    commit = (measured.get("commit") or "").split("|")
    job = jobs.ACTIVE_JOB.get(name)
    last_visit = state.get("last_visit")
    return {
        "name": name,
        "status": status,
        "url": tunnels.address(name),
        "version": env["ODOO_VERSION"],
        "edition": env["EDITION"],
        "branch": env["GIT_BRANCH"],
        "repo": measured.get("repo", ""),
        "commit": dict(zip(("sha", "subject", "when", "author"), commit)) if len(commit) == 4 else None,
        "cpu": measured.get("cpu", 0),
        "memory": measured.get("memory", 0),
        "history": list(monitor.HISTORY[name])[-60:],
        "last_visit": last_visit,
        "pauses_at": last_visit + settings.IDLE_HOURS * 3600 if auto_pause and status == "online" and last_visit else None,
        "uptime": measured.get("uptime", ""),
        "job": job.summary() if job else None,
        "gateway_error": gateway.LISTENER_ERRORS.get(name),
        "branch_missing": bool(state.get("branch_missing")),
        "expires_at": operations.expires_at(env),
    }


@blueprint.get("/overview")
def overview():
    host = {key: value for key, value in monitor.HOST.items() if key != "history"}
    host["history"] = list(monitor.HOST["history"])[-60:]
    return jsonify(host=host, instances=[_summary(n) for n in instances.names()], jobs=jobs.recent(limit=15),
                   delete_after_days=settings.DELETE_AFTER_DAYS,
                   panel_url=tunnels.address(tunnels.PANEL), idle_hours=settings.IDLE_HOURS)


@blueprint.get("/instances/<name>")
def details(name):
    _existing(name)
    env, addons = instances.read_env(name), instances.addons_dir(name)
    data = _summary(name)
    data.update(
        db_name=env["DB_NAME"],
        auto_update=env["AUTO_UPDATE"] == "true",
        auto_upgrade=env["AUTO_UPGRADE"] == "true",
        auto_pause=env["AUTO_PAUSE"] == "true",
        delete_after_days=int(env["DELETE_AFTER_DAYS"]),
        created_at=int(env["CREATED_AT"]),
        idle_hours=settings.IDLE_HOURS,
        poll_minutes=settings.POLL_MINUTES,
        submodules=submodules(addons),
        commits=commits(addons),
        disk=monitor.disk_usage(name),
        jobs=jobs.recent(name, limit=15),
    )
    if is_admin():      # passwords and ports are never sent to the sync operator
        data.update(admin_password=env["ODOO_ADMIN_PASSWORD"],
                    master_password=vault.decrypt(env["MASTER_PASSWORD"], instances.master_password_label(name)),
                    ports={key: env[key] for key in ("GATEWAY_PORT", "ODOO_PORT", "CONNECTOR_METRICS_PORT")})
    return jsonify(data)


@blueprint.get("/instances/<name>/logs")
def logs(name):
    return jsonify(log=odoo.recent_log(_existing(name)) or "No log output yet.")


@blueprint.get("/jobs/<job_id>")
def job_progress(job_id):
    job = jobs.JOBS.get(job_id) or abort(404, "This activity is no longer available.")
    offset = check.whole_number(request.args.get("offset", "0"), "offset")
    log_lines = job.log[:]
    data = job.summary() | {"log": "".join(log_lines[offset:]), "offset": len(log_lines), "notes": job.notes,
                            "hint": job.hint, "result": job.result if is_admin() else {}}
    if job.status == "failed":
        data["hint"] = message_for_user(job.hint)
    return jsonify(data)


# --------------------------------------------------------------------------- creating

@blueprint.get("/versions")
@admin_only
def versions():
    return jsonify([{"version": v, "published": odoo.version_published(v), "enterprise": odoo.enterprise_available(v)}
                    for v in settings.ODOO_VERSIONS])


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _plan_name(repo, branch, custom_name):
    """Instance name (from the custom name, or repository + branch), made unique, and its database name."""
    if custom_name:
        base = _slug(custom_name)
    else:
        repository = (github.repo_name(repo) or "").split("/")[-1]
        base = "-".join(part for part in (_slug(repository), _slug(branch)) if part)
    base = base[:28].strip("-") or "odoo"
    name, counter = base, 2
    while instances.folder(name).exists():
        name, counter = f"{base[:26].strip('-')}-{counter}", counter + 1
    return name, name.replace("-", "_")


@blueprint.get("/plan")
@admin_only
def plan():
    name, db_name = _plan_name(check.text(request.args, "repo"), check.text(request.args, "branch"),
                               check.custom_name(check.text(request.args, "name", max_length=40)))
    return jsonify(name=name, db_name=db_name, login=f"admin / {settings.ODOO_ADMIN_PASSWORD}",
                   delete_after_days=settings.DELETE_AFTER_DAYS)


@blueprint.post("/instances")
@admin_only
def create():
    data = check.body()
    repo = check.repo_address(check.text(data, "repo"), required=False)
    branch = check.branch(check.text(data, "branch", default="main"))
    version = check.one_of(check.text(data, "version"), settings.ODOO_VERSIONS, "Choose an Odoo version from the list.")
    edition = check.one_of(check.text(data, "edition"), ("community", "enterprise"), "Choose Community or Enterprise.")
    custom_name = check.custom_name(check.text(data, "name", max_length=40))
    if not repo and not custom_name:
        abort(400, "Enter a GitHub repository or a name for the instance.")
    if edition == "enterprise" and not odoo.enterprise_available(version):
        abort(400, f"Odoo {version} Enterprise is not available on this server.")
    name, db_name = _plan_name(repo, branch, custom_name)
    plan_data = {"name": name, "db_name": db_name, "repo": repo, "branch": branch, "version": version,
                 "edition": edition, "demo": check.flag(data, "demo"), "install_modules": check.flag(data, "install_modules"),
                 "delete_after_days": check.days(data, "delete_after_days"),
                 "ports": instances.reserve_ports()}
    job = jobs.start(name, "Create instance", "create", operations.create, plan_data, user=g.user["name"])
    log("Create instance", instance=name, detail=f"Odoo {version} {edition}")
    return jsonify(job=job.id, name=name)


# --------------------------------------------------------------------------- actions

@blueprint.post("/instances/<name>/<action>")
def run_action(name, action):
    _existing(name)
    if action not in ACTIONS:
        abort(404, "Unknown action.")
    title, kind, operator_allowed = ACTIONS[action]
    if not operator_allowed:
        refuse_unless_admin(f"{action} on {name}")
    fn, args = _job_for(action, name, check.body())
    job = jobs.start(name, title, kind, fn, *args, user=g.user["name"])
    log(title, instance=name)
    return jsonify(job=job.id)


def _job_for(action, name, data):
    """(function, arguments) of the job for `action`, after validating the request data."""
    if action == "update":
        return operations.update_code, (name,)
    if action == "update-submodules":
        return operations.update_code, (name, True)
    if action == "restart":
        return operations.restart, (name,)
    if action == "start":
        return operations.wake, (name,)
    if action == "pause":
        return operations.pause, (name,)
    if action == "backup":
        return operations.backup, (name,)
    if action == "switch-off":
        return operations.switch_off, (name,)
    if action == "delete":
        if check.text(data, "confirm", max_length=40) != name:
            abort(400, "Type the instance name exactly to confirm.")
        return operations.delete, (name,)
    if action == "upgrade":
        return operations.upgrade_modules, (name, check.module_list(check.text(data, "modules", max_length=4000)))
    if action == "switch-branch":
        return operations.switch_branch, (name, check.branch(check.text(data, "branch")))
    path = check.text(data, "path")
    if action == "add-submodule":
        url = check.repo_address(check.text(data, "url"))
        branch = check.branch(check.text(data, "branch"), required=False)
        path = check.submodule_path(path or github.repo_name(url).split("/")[-1])
        return operations.add_submodule, (name, url, path, branch)
    return operations.remove_submodule, (name, check.submodule_path(path))


@blueprint.post("/instances/<name>/settings")
@admin_only
def save_settings(name):
    """Automation switches of an instance. Switching branch is a separate action (it restarts the instance)."""
    _existing(name)
    data = check.body()
    instances.update_env(name, AUTO_UPDATE=str(check.flag(data, "auto_update")).lower(),
                         AUTO_UPGRADE=str(check.flag(data, "auto_upgrade")).lower(),
                         AUTO_PAUSE=str(check.flag(data, "auto_pause")).lower(),
                         DELETE_AFTER_DAYS=check.days(data, "delete_after_days"))
    log("automation settings changed", instance=name)
    return jsonify(ok=True)
