"""Projects: one GitHub repository each, its branches, their stages and instances (projects/).

    GET    /api/v1/projects                          every project (counts of branches by stage, of instances by
                                                     status); meta.standalone: instances outside every project
    POST   /api/v1/projects                          link an existing repository: {"repository" (address or
                                                     owner/name), "name"?, "odoo_version", "edition"} -> 201
    GET    /api/v1/projects/<id>?refresh=            the project with its branches (read from GitHub at most every
                                                     30 seconds; refresh=1 reads them again) and the server's
                                                     capacity
    PATCH  /api/v1/projects/<id>                     {"name", "odoo_version", "edition", "options": {option: value
                                                     or null (the panel's setting)}}
    DELETE /api/v1/projects/<id>                     {"confirm": "<id>"}; only a project without instances -> 204
    PUT    /api/v1/projects/<id>/branches/<branch>/stage
                                                     {"stage", "confirm_production"}: the branch and every instance
                                                     of it move to that stage
    POST   /api/v1/projects/<id>/branches/<branch>/instances
                                                     a new instance of the branch in the branch's stage ({"name"?,
                                                     "demo"?, "install_modules"?}); staging starts from a neutralised
                                                     copy of production -> 202, Location: job
    POST   /api/v1/projects/<id>/branches/<branch>/rebuild
                                                     rebuild the branch's staging instance from a new copy of
                                                     production -> 202, Location: job

Seeing projects needs instances.view; creating, linking, changing and deleting them and changing a branch's stage
need projects.manage; a new instance needs instances.create, a rebuild instances.restore (it replaces data). The
checks go through projects/access.py, where per-project access will be added. Production asks for more: making a
branch production, or moving it out of production, needs "confirm_production": true; a project has at most
PROJECT_PRODUCTION_BRANCHES production branches, and the server at most MAX_PRODUCTION_INSTANCES production
instances (409 beyond).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from flask import Blueprint, Response, abort, g, request

from sudoo import addons, projects
from sudoo.core import events, jobs
from sudoo.instances import capacity, monitor, odoo, stages, store
from sudoo.limits import quotas
from sudoo.projects import access, adopt, branches, staging
from sudoo.repos import github

from .. import validation as check
from ..security import log, refuse_unless
from . import instances as instances_api
from .resources import job_resource
from .responses import PREFIX, created, iso, no_content, ok

blueprint = Blueprint("projects", __name__)

NAME_MAX = 60
STATUS_ORDER = ("creating", "busy", "starting", "online", "paused", "switched_off", "stopped", "unknown")
OPTION_CHOICES = {
    "staging_on_push": ("rebuild", "keep_data"),
    "branch_deleted": ("keep", "archive", "delete"),
}
OPTION_FLAGS = ("auto_development", "development_demo_data")
OPTION_NUMBERS = {"max_auto_development": 100, "development_lifetime_days": 3650, "staging_lifetime_days": 3650}


def _refuse_unless(permission: str, project: Mapping[str, Any] | None, what: str) -> None:
    """403 unless the caller has `permission` in `project` (projects/access.py), audit-logged."""
    if not access.allowed(g.user, permission, project):
        refuse_unless(permission, what)  # the role lacks it: the usual refusal
        log(f"refused: {what}", ok=False)
        abort(403, "You have no access to this project.")


def _project(project_id: str, permission: str = "instances.view") -> dict[str, Any]:
    if not projects.ID_RE.match(project_id or ""):
        abort(404, "This project does not exist.")
    adopt.adopt_once()
    found = projects.get(project_id)
    if found is None:
        abort(404, "This project does not exist.")
    _refuse_unless(permission, found, f"{permission} in project {project_id}")
    return found


def _branch(value: str) -> str:
    if not github.valid_branch(value):
        abort(404, "There is no such branch.")
    return value


# ---------------------------------------------------------------- resources


def _status(names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in names:
        state = "creating" if jobs.creating(name) else monitor.status(name)
        counts[state] = counts.get(state, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: STATUS_ORDER.index(item[0])))


def _attention(names: list[str]) -> list[str]:
    """Instances of the project that need someone: a failed update, changes made by hand, a deleted branch."""
    from sudoo.core.state import STATE

    found = []
    for name in names:
        state = STATE.get(name)
        if state.get("update_failed") or state.get("drift") or state.get("branch_missing"):
            found.append(name)
    return found


def project_resource(project: Mapping[str, Any]) -> dict[str, Any]:
    """A project as lists show it: no branches from GitHub (those are on its own page), only the branches it knows
    (seen on GitHub, recorded, or followed by an instance) counted by stage."""
    names = projects.instances(project["id"])
    grouped = projects.by_branch(project["id"])
    known = set(project.get("seen") or []) | set(project.get("branches") or {}) | {b for b in grouped if b}
    by_stage = dict.fromkeys(stages.STAGES, 0)
    for branch in known:
        by_stage[projects.branch_stage(project, branch, grouped.get(branch, []))] += 1
    repository = str(project["repository"])
    return {
        "id": project["id"],
        "name": project["name"],
        "repository": {"full_name": repository, "url": f"https://github.com/{repository}"},
        "odoo": {"version": project["odoo_version"], "edition": project["edition"]},
        "origin": project.get("origin", "linked"),
        "created_at": iso(project.get("created_at")),
        "created_by": project.get("created_by") or None,
        "branches": {"total": len(known), **by_stage},
        "instances": {"total": len(names), "by_status": _status(names), "names": names},
        "attention": _attention(names),
        "production_branches": projects.production_branches(project),
        "options": projects.options(project),
        "own_options": {k: v for k, v in (project.get("options") or {}).items() if v is not None},
        "access": {"mode": (project.get("access") or {}).get("mode", "panel_roles")},
        "links": {"self": f"{PREFIX}/projects/{project['id']}"},
    }


def _capacity() -> dict[str, Any] | None:
    """The server's capacity estimate (instances/capacity.py), for the warning about many branches running."""
    found = capacity.current()
    if found is None:
        return None
    return {key: found.get(key) for key in ("comfortable", "running", "headroom", "warning", "message", "overloaded")}


def full_resource(project: Mapping[str, Any], refresh: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    listed, facts = branches.listing(project, refresh)
    resource = project_resource(project) | {"branches_list": listed}
    meta = {
        "default_branch": facts["default"],
        "branches_error": facts["error"],
        "branches_read_at": iso(facts["read_at"]),
        "capacity": _capacity(),
        "production_limit": projects.production_limit() or None,
        "server_production_limit": stages.production_limit() or None,
    }
    return resource, meta


# ---------------------------------------------------------------- reading


@blueprint.get("/projects")
def list_projects() -> Response:
    _refuse_unless("instances.view", None, request.path)
    adopt.adopt_once()
    shown = access.visible(g.user, projects.all_projects())
    standalone = [name for name in sorted(set(store.names()) | set(jobs.being_created())) if not _project_id_of(name)]
    return ok([project_resource(p) for p in shown], meta={"standalone": standalone, "total": len(shown)})


def _project_id_of(name: str) -> str | None:
    if store.exists(name):
        return projects.project_of(name)
    job = jobs.creating(name)
    return str(job.details.get("project") or "") or None if job else None


@blueprint.get("/projects/<project_id>")
def project(project_id: str) -> Response:
    found = _project(project_id)
    resource, meta = full_resource(found, refresh=request.args.get("refresh") in ("1", "true"))
    return ok(resource, meta=meta)


# ---------------------------------------------------------------- creating and changing


def _version_and_edition(data: Mapping[str, Any], current: Mapping[str, Any] | None = None) -> tuple[str, str]:
    version = check.text(data, "odoo_version", max_length=10) or (current or {}).get("odoo_version", "")
    edition = check.text(data, "edition", max_length=20) or (current or {}).get("edition", "community")
    version = check.one_of(version, odoo.versions(), "Choose an Odoo version from the list.")
    edition = check.one_of(edition, ("community", "enterprise"), "Choose Community or Enterprise.")
    if edition == "enterprise" and not addons.available(version):
        abort(400, f"Odoo {version} Enterprise is not available on this server.")
    return version, edition


def _name(data: Mapping[str, Any], required: bool = False) -> str:
    name = check.text(data, "name", max_length=NAME_MAX)
    if required and not name:
        abort(400, "Give the project a name.")
    return name


@blueprint.post("/projects")
def create_project() -> Response:
    _refuse_unless("projects.manage", None, "create a project")
    data = check.body()
    reference = check.text(data, "repository", max_length=200)
    repository = github.repo_name(reference) if reference else None
    if not repository:
        abort(400, "Give the repository's address, for example https://github.com/company/repository.")
    version, edition = _version_and_edition(data)
    adopt.adopt_once()
    if projects.by_repository(repository) is not None:
        abort(409, f"{repository} has a project already.")
    found, default, error = github.heads(f"https://github.com/{repository}")
    if error:
        abort(400, error)
    try:
        project = projects.create(
            repository=repository,
            odoo_version=version,
            edition=edition,
            origin="linked",
            user=g.user["name"],
            name=_name(data),
        )
    except projects.ProjectError as refusal:
        abort(refusal.status, str(refusal))
    projects.change(project["id"], lambda p: {**p, "seen": sorted(found)})  # its branches today are not "new"
    log("project created", detail=f"{project['id']}: linked {repository}")
    events.publish("project", project["id"])
    project = projects.get(project["id"]) or project
    return created(project_resource(project) | {"default_branch": default}, f"{PREFIX}/projects/{project['id']}")


def _options(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        abort(400, 'Send the options as an object: {"options": {"auto_development": false}}.')
    unknown = sorted(set(raw) - set(projects.OPTIONS))
    if unknown:
        abort(400, f"Unknown option {unknown[0][:40]!r}. Options: {', '.join(projects.OPTIONS)}.")
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            cleaned[key] = None
        elif key in OPTION_FLAGS:
            cleaned[key] = check.flag(raw, key)
        elif key in OPTION_CHOICES:
            cleaned[key] = check.one_of(
                check.text(raw, key, max_length=20), OPTION_CHOICES[key], f"{key}: {' or '.join(OPTION_CHOICES[key])}."
            )
        else:
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= OPTION_NUMBERS[key]:
                abort(400, f"{key}: a whole number from 0 to {OPTION_NUMBERS[key]}.")
            cleaned[key] = value
    return cleaned


@blueprint.patch("/projects/<project_id>")
def change_project(project_id: str) -> Response:
    found = _project(project_id, "projects.manage")
    data = check.body()
    values: dict[str, Any] = {}
    if "name" in data:
        values["name"] = _name(data, required=True)
    if "odoo_version" in data or "edition" in data:
        values["odoo_version"], values["edition"] = _version_and_edition(data, found)
    options = _options(data["options"]) if "options" in data else {}
    if not values and not options:
        abort(400, "Send at least one of: name, odoo_version, edition, options.")

    def update(project: dict[str, Any]) -> dict[str, Any]:
        own = {**(project.get("options") or {}), **options}
        return {**project, **values, "options": {k: v for k, v in own.items() if v is not None}}

    projects.change(project_id, update)
    changed = sorted(values) + [f"options.{k}={v}" for k, v in sorted(options.items())]
    log("project changed", detail=f"{project_id}: {', '.join(changed)}"[:300])
    events.publish("project", project_id)
    return ok(project_resource(projects.get(project_id) or found))


@blueprint.delete("/projects/<project_id>")
def delete_project(project_id: str) -> Response:
    found = _project(project_id, "projects.manage")
    if check.text(check.body(), "confirm", max_length=60) != project_id:
        abort(400, "Type the project's id exactly to confirm.")
    members = projects.instances(project_id)
    if members:
        abort(
            409,
            f"{found['name']} still has {len(members)} {'instance' if len(members) == 1 else 'instances'} "
            f"({', '.join(members[:5])}). Delete them first; the repository on GitHub is never touched.",
        )
    projects.remove(project_id)
    log("project deleted", detail=f"{project_id} ({found['repository']})")
    events.publish("project", project_id)
    return no_content()


# ---------------------------------------------------------------- branches


PRODUCTION_CONFIRMATION = (
    "Making {branch} production changes how its instances run: they never pause and never expire, are backed up "
    'every day, and get new code only through a backup and an automatic rollback. Confirm ("confirm_production": '
    "true)."
)
LEAVING_PRODUCTION = (
    '{branch} is a production branch. Confirm that it leaves production ("confirm_production": true): its '
    "instances can then pause and expire, and their daily backup follows the panel's settings."
)


@blueprint.put("/projects/<project_id>/branches/<path:branch>/stage")
def change_stage(project_id: str, branch: str) -> Response:
    """Move a branch, and every instance of it, to another stage."""
    found = _project(project_id, "projects.manage")
    branch = _branch(branch)
    data = check.body()
    stage = check.one_of(check.text(data, "stage", max_length=20), stages.STAGES, instances_api.STAGE_MESSAGE)
    with quotas.LOCK, projects.LOCK:
        found = projects.get(project_id) or found
        members = projects.by_branch(project_id).get(branch, [])
        before = projects.branch_stage(found, branch, members)
        confirmed = check.flag(data, "confirm_production") is True
        if before != stage:
            if stage == stages.PRODUCTION:
                if not confirmed:
                    abort(400, PRODUCTION_CONFIRMATION.format(branch=branch))
                refused = projects.production_refusal(found, branch)
                if refused:
                    log("branch stage change refused (production branches)", ok=False, detail=refused)
                    abort(409, refused)
            if before == stages.PRODUCTION and not confirmed:
                abort(400, LEAVING_PRODUCTION.format(branch=branch))
        busy = [name for name in members if jobs.creating(name)]
        if busy:
            abort(409, f"{', '.join(busy)} is still being created. Try again when its creation has finished.")
        for name in members:  # each instance with the rules of PATCH /instances/<name> (server-wide limit too)
            if stages.of(store.read_env(name)) != stage:
                instances_api._change(name, {"stage": stage, "confirm_production": True})
        projects.record_branch(project_id, branch, stage=stage, changed_by=g.user["name"], changed_at=int(time.time()))
    log("branch stage changed", detail=f"{project_id} {branch}: {before} -> {stage}")
    events.publish("project", project_id)
    notes = []
    if stage == stages.STAGING and members:
        notes.append(
            "Its instances keep their data until they are rebuilt from production (Rebuild, or the next push when "
            "the project rebuilds staging on push)."
        )
    listed, _ = branches.listing(projects.get(project_id) or found)
    shown = next((b for b in listed if b["name"] == branch), None)
    return ok(shown or {"name": branch, "stage": stage, "instances": members}, meta={"notes": notes} if notes else None)


@blueprint.post("/projects/<project_id>/branches/<path:branch>/instances")
def create_instance(project_id: str, branch: str) -> Response:
    """A new instance of the branch, in the branch's stage, with the project's Odoo version and edition."""
    found = _project(project_id, "instances.create")
    branch = _branch(branch)
    data = check.body()
    heads, _, error = branches.heads(found, refresh=True)
    if error:
        abort(502, error)
    if branch not in heads:
        abort(404, f"{found['repository']} has no branch {branch} on GitHub.")
    stage = projects.branch_stage(found, branch)
    version, edition = found["odoo_version"], found["edition"]
    if edition == "enterprise" and not addons.available(version):
        abort(409, f"Odoo {version} Enterprise is not available on this server.")
    from_production = None
    if stage == stages.STAGING:
        _refuse_unless("instances.restore", found, f"create a staging instance of {project_id} from production data")
        from_production = staging.production_instance(found)
        problem = staging.copy_problem(from_production, version, edition)
        if problem:
            abort(409, problem)
    switches = {
        "delete_after_days": projects.lifetime_days(found, stage),
        "auto_pause": stage != stages.PRODUCTION and bool(stages.defaults(stage)["auto_pause"]),
    }
    demo = (
        check.flag(data, "demo", bool(projects.option(found, "development_demo_data")))
        if stage == stages.DEVELOPMENT
        else False
    )
    with quotas.LOCK:
        return instances_api._start_creation(
            {**switches, "demo": demo, "install_modules": check.flag(data, "install_modules", True)},
            f"https://github.com/{found['repository']}",
            branch,
            version,
            edition,
            check.custom_name(check.text(data, "name", max_length=40)),
            None,
            stage,
            project=project_id,
            from_production=from_production,
        )


@blueprint.post("/projects/<project_id>/branches/<path:branch>/rebuild")
def rebuild(project_id: str, branch: str) -> Response:
    """Rebuild the branch's staging instance (the first one when it has several) from a new copy of production."""
    _project(project_id, "instances.restore")
    branch = _branch(branch)
    members = [name for name in projects.by_branch(project_id).get(branch, []) if store.exists(name)]
    if not members:
        abort(404, f"{branch} has no instance to rebuild.")
    name = check.text(check.body(), "instance", max_length=40) or members[0]
    if name not in members:
        abort(404, f"{name} is not an instance of {branch}.")
    refused = staging.rebuild_refusal(name)
    if refused:
        abort(409, refused)
    if jobs.busy(name):
        abort(409, f"{name} is busy with another job. Try again when it has finished.")
    job = jobs.start(name, "Rebuild from production", "restore", staging.rebuild, name, user=g.user["name"])
    log("Rebuild from production", instance=name, detail=f"{project_id} {branch}")
    response = ok(job_resource(job), status=202)
    response.headers["Location"] = f"{PREFIX}/jobs/{job.id}"
    return response
