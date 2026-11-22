"""Projects: one GitHub repository each, its branches, their stages and instances (projects/).

    GET    /api/v1/projects                          every project (counts of branches by stage, of instances by
                                                     status); meta.standalone: instances outside every project
    GET    /api/v1/projects/repository-owners        where a new, empty repository can be created (GitHub App
                                                     installations with Administration: write), and what to do
                                                     elsewhere; ?refresh=1 reads the installations again
    POST   /api/v1/projects                          link an existing repository: {"repository" (address or
                                                     owner/name), "name"?, "odoo_version", "edition"}, or create a
                                                     new, empty one: {"create": {"owner", "name", "private",
                                                     "description"}, ...} (also github.manage) -> 201
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
    GET    /api/v1/projects/<id>/builds?per_branch=  the last builds of every branch (10 each by default, at most
                                                     200), newest first (projects/builds.py)
    GET    /api/v1/projects/<id>/builds/<build_id>   one build, or any other entry of a branch's history
    GET    /api/v1/projects/<id>/branches/<branch>/history?limit=&offset=&commits=
                                                     the branch's timeline, newest first: builds, stage changes
                                                     and restores (commits=1: also the commits a branch that builds
                                                     nothing on new commits recorded); meta.total, meta.limit,
                                                     meta.offset
    GET    /api/v1/projects/<id>/branches/<branch>/options
                                                     the branch's build options: what applies ("options"), where
                                                     each comes from ("sources"), its own values ("own")
    PUT    /api/v1/projects/<id>/branches/<branch>/options
                                                     {"on_new_commit", "module_install", "modules", "test_suite",
                                                     "test_tags", "demo_data"}: a value sets it, null follows the
                                                     project again (projects/build_options.py)
    GET    /api/v1/projects/<id>/access              who may see and work in the project: mode, grants, branches'
                                                     grants and locks, with names            (projects.access)
    PUT    /api/v1/projects/<id>/access              {"mode", "grants", "branches"}: replaces them (locks stay)
    PUT    /api/v1/projects/<id>/branches/<branch>/lock
                                                     lock the branch, or change who its lock allows: {"reason"?,
                                                     "allow": [{"type": "team"|"user", "id"}]}
    DELETE /api/v1/projects/<id>/branches/<branch>/lock
                                                     unlock it -> 204

Seeing projects needs instances.view; creating, linking, changing and deleting them and changing a branch's stage
or build options need projects.manage; a new instance needs instances.create, a rebuild instances.restore (it
replaces data); access and locks need projects.access. Every check goes through projects/access.py: role ∩ the
caller's level in the project and branch (teams, grants, restricted projects and branches), and locked branches;
projects and branches hidden from the caller answer 404 and are left out of lists. Production asks for more: making a
branch production, or moving it out of production, needs "confirm_production": true; a project has at most
PROJECT_PRODUCTION_BRANCHES production branches, and the server at most MAX_PRODUCTION_INSTANCES production
instances (409 beyond).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from flask import Blueprint, Response, abort, g, request

from sudoo import accounts, addons, projects, teams
from sudoo.config import settings
from sudoo.core import events, jobs
from sudoo.instances import capacity, monitor, odoo, stages, store
from sudoo.limits import quotas
from sudoo.projects import access, adopt, branches, build_options, builds, commit_status, staging
from sudoo.repos import github, github_app

from .. import validation as check
from ..security import log, refuse_unless, refuse_without_feature, refuse_without_premium
from . import instances as instances_api
from .resources import job_resource
from .responses import PREFIX, created, iso, no_content, ok

blueprint = Blueprint("projects", __name__)

NAME_MAX = 60
STATUS_ORDER = ("creating", "busy", "starting", "online", "paused", "switched_off", "stopped", "unknown")
OPTION_CHOICES = {
    "staging_on_push": ("rebuild", "keep_data"),
    "branch_deleted": ("keep", "archive", "delete"),
    "development_on_new_commit": build_options.ON_NEW_COMMIT,
    "module_install": build_options.MODULE_INSTALL,
    "test_suite": build_options.TEST_SUITE,
}
OPTION_FLAGS = (
    "auto_development", "development_demo_data", "upgrade_test_on_push", "staging_tests", "commit_statuses",
)  # fmt: skip
OPTION_TEXTS = ("modules", "test_tags")  # checked by projects/build_options.clean
OPTION_NUMBERS = {"max_auto_development": 100, "development_lifetime_days": 3650, "staging_lifetime_days": 3650}


def _refuse_unless(permission: str, project: Mapping[str, Any] | None, what: str, branch: str | None = None) -> None:
    """403 unless the caller has `permission` in `project` (and `branch`; projects/access.py), audit-logged; 404 when
    the project or branch is hidden from them."""
    found = access.refusal(g.user, permission, project, branch)
    if found is None:
        return
    if permission not in access.role_permissions(g.user):
        refuse_unless(permission, what)  # the role lacks it: the usual refusal
    log(f"refused: {what}", ok=False, detail=found[1])
    abort(*found)


def _project(project_id: str, permission: str = "instances.view") -> dict[str, Any]:
    if not projects.ID_RE.match(project_id or ""):
        abort(404, "This project does not exist.")
    adopt.adopt_once()
    found = projects.get(project_id)
    if found is None:
        abort(404, "This project does not exist.")
    if not access.sees_project(g.user, found):
        log(f"refused: {permission} in project {project_id}", ok=False, detail="not visible")
        abort(404, "This project does not exist.")
    if permission != "instances.view":
        _refuse_unless(permission, found, f"{permission} in project {project_id}")
    return found


def _branch(value: str) -> str:
    if not github.valid_branch(value):
        abort(404, "There is no such branch.")
    return value


# ---------------------------------------------------------------- resources


def _status(names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    from sudoo.premium import servers

    for name in names:
        remote = servers.remote_resource(name)  # an instance of another server: what its agent reported
        state = "creating" if jobs.creating(name) else str(remote["status"]) if remote else monitor.status(name)
        state = state if state in STATUS_ORDER else "unknown"
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


def _hidden_branches(project: Mapping[str, Any], branches_known: set[str], user: Mapping[str, Any] | None) -> set[str]:
    """The branches of `branches_known` hidden from `user` (none when no user is given: the shared resource)."""
    if user is None or access.sees_everything(user):
        return set()
    return {branch for branch in branches_known if not access.sees_branch(user, project, branch)}


def project_resource(project: Mapping[str, Any], user: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A project as lists show it: no branches from GitHub (those are on its own page), only the branches it knows
    (seen on GitHub, recorded, or followed by an instance) counted by stage. With `user`: what that user may see
    (branches hidden from them, and their instances, are left out)."""
    grouped = projects.by_branch(project["id"])
    known = set(project.get("seen") or []) | set(project.get("branches") or {}) | {b for b in grouped if b}
    hidden = _hidden_branches(project, known | set(grouped), user)
    known -= hidden
    names = sorted(name for branch, members in grouped.items() if branch not in hidden for name in members)
    by_stage = dict.fromkeys(stages.STAGES, 0)
    for branch in known:
        by_stage[projects.branch_stage(project, branch, grouped.get(branch, []))] += 1
    repository = str(project["repository"])
    found_access = access.access_of(project)
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
        "production_branches": [b for b in projects.production_branches(project) if b not in hidden],
        "options": projects.options(project),
        "default_server": project.get("default_server") or "main",  # where its new instances go (premium/servers.py)
        "own_options": {k: v for k, v in (project.get("options") or {}).items() if v is not None},
        "access": {
            "mode": access.mode(project),
            "grants": len(found_access.get("grants") or ()),
            "restricted_branches": sorted(
                b for b, v in (found_access.get("branches") or {}).items() if v.get("restricted") and b not in hidden
            ),
            "locked_branches": sorted(b for b in found_access.get("locks") or {} if b not in hidden),
        },
        "links": {"self": f"{PREFIX}/projects/{project['id']}"},
    }


def _capacity() -> dict[str, Any] | None:
    """The server's capacity estimate (instances/capacity.py), for the warning about many branches running."""
    found = capacity.current()
    if found is None:
        return None
    return {key: found.get(key) for key in ("comfortable", "running", "headroom", "warning", "message", "overloaded")}


def full_resource(
    project: Mapping[str, Any], refresh: bool = False, user: Mapping[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The project's own page: its branches with their lock and, for `user`, what they may do in each."""
    listed, facts = branches.listing(project, refresh)
    if user is not None:
        listed = [b for b in listed if access.sees_branch(user, project, b["name"])]
    for branch in listed:
        branch["lock"] = access.describe_lock(access.lock_of(project, branch["name"]))
        branch["restricted"] = bool(access.branch_access(project, branch["name"]).get("restricted"))
        if user is not None:
            branch["permissions"] = sorted(
                access.permissions(user, project, branch["name"]) & access.PROJECT_PERMISSIONS
            )
    resource = project_resource(project, user) | {"branches_list": listed}
    meta = {
        "default_branch": facts["default"],
        "branches_error": facts["error"],
        "branches_read_at": iso(facts["read_at"]),
        "capacity": _capacity(),
        "production_limit": projects.production_limit() or None,
        "server_production_limit": stages.production_limit() or None,
        "commit_statuses": {"available": commit_status.available(project)},
    }
    if user is not None:
        meta["permissions"] = sorted(access.permissions(user, project) & access.PROJECT_PERMISSIONS)
    return resource, meta


# ---------------------------------------------------------------- reading


@blueprint.get("/projects")
def list_projects() -> Response:
    _refuse_unless("instances.view", None, request.path)
    adopt.adopt_once()
    shown = access.visible(g.user, projects.all_projects())
    standalone = [name for name in sorted(set(store.names()) | set(jobs.being_created())) if not _project_id_of(name)]
    return ok([project_resource(p, g.user) for p in shown], meta={"standalone": standalone, "total": len(shown)})


def _project_id_of(name: str) -> str | None:
    if store.exists(name):
        return projects.project_of(name)
    job = jobs.creating(name)
    return str(job.details.get("project") or "") or None if job else None


@blueprint.get("/projects/<project_id>")
def project(project_id: str) -> Response:
    found = _project(project_id)
    resource, meta = full_resource(found, refresh=request.args.get("refresh") in ("1", "true"), user=g.user)
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


@blueprint.get("/projects/repository-owners")
def repository_owners() -> Response:
    """Where a new, empty repository can be created (organisations with the GitHub App and its Administration:
    write permission), and for the others what to do; ?refresh=1 reads the installations from GitHub again."""
    _refuse_unless("projects.manage", None, request.path)
    if not github_app.connected():
        return ok([], meta={"connected": False, "creation": github_app.repository_creation("")})
    try:
        if request.args.get("refresh") in ("1", "true"):
            github_app.sync_installations()
        else:
            github_app.sync_if_stale()
    except github_app.GitHubError as error:
        abort(502, str(error))
    return ok(github_app.repository_owners(), meta={"connected": True})


def _create_repository(data: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Create the new, empty repository a project asks for ({"owner", "name", "private", "description"});
    returns ("Owner/Name", its branches: none)."""
    refuse_unless("github.manage", "create a repository on GitHub")
    owner = check.text(data, "owner", max_length=39)
    name = check.text(data, "name", max_length=100)
    if not owner or not name:
        abort(400, "Choose the organisation and the name of the new repository.")
    if projects.by_repository(f"{owner}/{name}") is not None:
        abort(409, f"{owner}/{name} has a project already.")
    try:
        made = github_app.create_repository(
            owner, name, check.flag(data, "private", True), check.text(data, "description", max_length=350)
        )
    except github_app.GitHubError as error:
        log("repository creation refused", ok=False, detail=f"{owner}/{name}: {error}")
        abort(error.status if error.status in (400, 409) else 502, str(error))
    log("repository created on GitHub", detail=made["full_name"])
    return str(made["full_name"]), []


@blueprint.post("/projects")
def create_project() -> Response:
    _refuse_unless("projects.manage", None, "create a project")
    data = check.body()
    version, edition = _version_and_edition(data)
    adopt.adopt_once()
    if isinstance(data.get("create"), dict):
        made, seen = _create_repository(data["create"])
        return _new_project(data, made, version, edition, "created", seen, None)
    reference = check.text(data, "repository", max_length=200)
    repository = github.repo_name(reference) if reference else None
    if not repository:
        abort(400, "Give the repository's address, for example https://github.com/company/repository.")
    if projects.by_repository(repository) is not None:
        abort(409, f"{repository} has a project already.")
    found, default, error = github.heads(f"https://github.com/{repository}")
    if error:
        abort(400, error)
    return _new_project(data, repository, version, edition, "linked", sorted(found), default)


def _new_project(
    data: Mapping[str, Any],
    repository: str,
    version: str,
    edition: str,
    origin: str,
    seen: list[str],
    default: str | None,
) -> Response:
    try:
        project = projects.create(
            repository=repository,
            odoo_version=version,
            edition=edition,
            origin=origin,
            user=g.user["name"],
            name=_name(data),
        )
    except projects.ProjectError as refusal:
        abort(refusal.status, str(refusal))
    projects.change(project["id"], lambda p: {**p, "seen": seen})  # its branches today are not "new"
    log("project created", detail=f"{project['id']}: {origin} {repository}")
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
        elif key in OPTION_TEXTS:
            try:
                cleaned[key] = build_options.clean(key, value)
            except ValueError as error:
                abort(400, str(error))
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
    if "default_server" in data:  # where its new instances go when nobody chooses (402 without several servers)
        from sudoo.premium import servers

        values["default_server"] = servers.default_server(check.text(data, "default_server", max_length=20) or None)
    if not values and not options:
        abort(400, "Send at least one of: name, odoo_version, edition, options, default_server.")

    def update(project: dict[str, Any]) -> dict[str, Any]:
        own = {**(project.get("options") or {}), **options}
        return {**project, **values, "options": {k: v for k, v in own.items() if v is not None}}

    if options:  # a list without modules, custom tests without tags: refused before they are kept
        own = {k: v for k, v in {**(found.get("options") or {}), **options}.items() if v is not None}
        merged = {**found, "options": own}
        keys = ("module_install", "modules", "test_suite", "test_tags")
        refused = build_options.problem({key: projects.option(merged, key) for key in keys})
        if refused:
            abort(400, refused)
    projects.change(project_id, update)
    changed = sorted(values) + [f"options.{k}={v}" for k, v in sorted(options.items())]
    log("project changed", detail=f"{project_id}: {', '.join(changed)}"[:300])
    events.publish("project", project_id)
    return ok(project_resource(projects.get(project_id) or found, g.user))


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
    builds.forget_project(project_id)
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
    found = _project(project_id)
    branch = _branch(branch)
    _refuse_unless("projects.manage", found, f"change the stage of {project_id} {branch}", branch)
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
                instances_api.refuse_unlinked(
                    "moving branches into production",
                    f"stage change of {project_id} {branch} refused (panel not linked)",
                )
                refused = projects.production_refusal(found, branch)
                if refused:
                    log("branch stage change refused (production branches)", ok=False, detail=refused)
                    abort(409, refused)
            if before == stages.PRODUCTION and not confirmed:
                abort(400, LEAVING_PRODUCTION.format(branch=branch))
            if before == stages.PRODUCTION:  # its instances can then expire: as much as deleting them
                _refuse_unless("instances.delete", found, f"move {project_id} {branch} out of production", branch)
        busy = [name for name in members if jobs.creating(name)]
        if busy:
            abort(409, f"{', '.join(busy)} is still being created. Try again when its creation has finished.")
        for name in members:  # each instance with the rules of PATCH /instances/<name> (server-wide limit too)
            if stages.of(store.read_env(name)) != stage:
                instances_api._change(name, {"stage": stage, "confirm_production": True}, history=False)
        projects.record_branch(project_id, branch, stage=stage, changed_by=g.user["name"], changed_at=int(time.time()))
        builds.stage_changed(project_id, branch, before, stage, g.user["name"])
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


def _refuse_locked_production(project: Mapping[str, Any], production: str | None) -> None:
    """Staging copies production's data: refused unless the caller may restore production itself (a restricted
    production branch that hides it from them keeps its data from them too), and when production's branch is locked
    against the caller."""
    if not production:
        return
    branch = projects.branch_of(production)
    lock = access.lock_of(project, branch)
    if lock is not None and not access.lock_allows(g.user, lock):
        log("refused: copy of a locked production branch", instance=production, ok=False)
        abort(403, f"The production branch {branch} is locked: only the people and teams its lock allows may copy its "
                   "data into staging.")  # fmt: skip
    if not access.allowed_instance(g.user, "instances.restore", production):
        log("refused: copy of production's data into staging", ok=False, detail=project.get("id"))
        abort(403, "Copying production's data into staging needs the right to restore production's instance.")


@blueprint.post("/projects/<project_id>/branches/<path:branch>/instances")
def create_instance(project_id: str, branch: str) -> Response:
    """A new instance of the branch, in the branch's stage, with the project's Odoo version and edition."""
    found = _project(project_id)
    branch = _branch(branch)
    _refuse_unless("instances.create", found, f"create an instance of {project_id} {branch}", branch)
    data = check.body()
    heads, _, error = branches.heads(found, refresh=True)
    if error:
        abort(502, error)
    if branch not in heads:
        abort(404, f"{found['repository']} has no branch {branch} on GitHub.")
    stage = projects.branch_stage(found, branch)
    if instances_api.developer_token() and stage != stages.DEVELOPMENT:
        instances_api.refuse_developer_token(
            f"create a {stage} instance of {project_id} {branch}", "only instances of development branches."
        )
    version, edition = found["odoo_version"], found["edition"]
    if edition == "enterprise" and not addons.available(version):
        abort(409, f"Odoo {version} Enterprise is not available on this server.")
    from_production = None
    if stage == stages.STAGING:
        _refuse_unless(
            "instances.restore", found, f"create a staging instance of {project_id} from production data", branch
        )
        from_production = staging.production_instance(found)
        _refuse_locked_production(found, from_production)
        problem = staging.copy_problem(from_production, version, edition)
        if problem:
            abort(409, problem)
    switches = {
        "delete_after_days": projects.lifetime_days(found, stage),
        "auto_pause": stage != stages.PRODUCTION and bool(stages.defaults(stage)["auto_pause"]),
    }
    demo = (
        check.flag(data, "demo", bool(build_options.effective(found, branch, stage)["demo_data"]))
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
    found = _project(project_id)
    branch = _branch(branch)
    _refuse_unless("instances.restore", found, f"rebuild {project_id} {branch}", branch)
    instances_api.refuse_unlinked("rebuilding staging", f"rebuild of {project_id} {branch} refused (panel not linked)")
    members = [name for name in projects.by_branch(project_id).get(branch, []) if store.exists(name)]
    if not members:
        abort(404, f"{branch} has no instance to rebuild.")
    name = check.text(check.body(), "instance", max_length=40) or members[0]
    if name not in members:
        abort(404, f"{name} is not an instance of {branch}.")
    refused = staging.rebuild_refusal(name)
    if refused:
        abort(409, refused)
    _refuse_locked_production(found, staging.production_instance(found))
    if jobs.busy(name):
        abort(409, f"{name} is busy with another job. Try again when it has finished.")
    job = jobs.start(
        name, "Rebuild from production", "restore", staging.rebuild, name, user=g.user["name"],
        details={"build": "rebuild"},
    )  # fmt: skip
    log("Rebuild from production", instance=name, detail=f"{project_id} {branch}")
    response = ok(job_resource(job), status=202)
    response.headers["Location"] = f"{PREFIX}/jobs/{job.id}"
    return response


# ---------------------------------------------------------------- builds and history

PER_BRANCH_DEFAULT = 10
HISTORY_PAGE_DEFAULT = 50
HISTORY_PAGE_MAX = 200


def _duration(start: Any, end: Any) -> int | None:
    if not start or not end:
        return None
    return max(0, round(float(end) - float(start)))


def history_resource(entry: Mapping[str, Any]) -> dict[str, Any]:
    """An entry of a branch's history (projects/builds.py) as the API shows it: a build, a stage change or a
    restore."""
    base = f"{PREFIX}/projects/{entry['project']}"
    resource: dict[str, Any] = {
        "id": entry["id"],
        "type": entry["type"],
        "project": entry["project"],
        "branch": entry["branch"],
        "at": iso(entry.get("at")),
        "by": entry.get("by") or None,
    }
    job_id = entry.get("job")
    links: dict[str, Any] = {"self": f"{base}/builds/{entry['id']}"}
    if entry["type"] == "commit":  # a commit of a branch that builds nothing on new commits
        commit = entry.get("commit") or None
        resource |= {
            "instance": entry.get("instance"),
            "status": entry.get("status") or "skipped",
            "result": entry.get("result") or "",
            "commit": (
                {key: commit.get(key) or "" for key in ("sha", "full_sha", "subject", "author", "when")}
                if commit
                else None
            ),
        }
        links["instance"] = f"{PREFIX}/instances/{entry.get('instance')}" if entry.get("instance") else None
    elif entry["type"] == "stage":
        resource |= {
            "from": entry.get("from"),
            "to": entry.get("to"),
            "explanation": entry.get("explanation") or "",
            "reason": entry.get("reason") or None,
        }
    else:
        resource |= {
            "instance": entry.get("instance"),
            "status": entry.get("status"),
            "action": entry.get("action") or None,
            "result": entry.get("result") or "",
            "job": job_id or None,
        }
        links["instance"] = f"{PREFIX}/instances/{entry.get('instance')}" if entry.get("instance") else None
    if entry["type"] == "build":
        commit = entry.get("commit") or None
        resource |= {
            "trigger": entry.get("trigger"),
            "commit": (
                {key: commit.get(key) or "" for key in ("sha", "full_sha", "subject", "author", "when")}
                if commit
                else None
            ),
            "queued_at": iso(entry.get("queued_at")),
            "started_at": iso(entry.get("started_at")),
            "finished_at": iso(entry.get("finished_at")),
            "duration_seconds": _duration(entry.get("started_at"), entry.get("finished_at")),
            "dropped_at": iso(entry.get("dropped_at")),
            "messages": list(entry.get("messages") or []),
            "tests": _tests(entry.get("tests")),
        }
    links["job"] = f"{PREFIX}/jobs/{job_id}" if job_id else None
    resource["links"] = links
    return resource


def _tests(found: Any) -> dict[str, Any] | None:
    """A build's tests (projects/builds.tests_summary): status, counts and the modules with a failing test; None
    when the build ran none."""
    if not isinstance(found, Mapping):
        return None
    counts = {key: int(found.get(key) or 0) for key in ("tests", "failed", "errors", "warnings")}
    return {"status": found.get("status") or "success", **counts, "modules": list(found.get("modules") or [])}


def _sees(project: Mapping[str, Any], branch: str) -> bool:
    return access.sees_branch(g.user, project, branch)


@blueprint.get("/projects/<project_id>/builds")
def list_builds(project_id: str) -> Response:
    """The last builds of every branch of the project the caller may see, newest first."""
    found = _project(project_id)
    per_branch = check.whole_number(
        request.args.get("per_branch", str(PER_BRANCH_DEFAULT)), "per_branch", minimum=1, maximum=200
    )
    grouped = projects.by_branch(project_id)
    shown = [
        (projects.branch_stage(found, branch, grouped.get(branch, [])), branch, entries)
        for branch, entries in builds.builds(project_id, per_branch).items()
        if _sees(found, branch)
    ]
    shown.sort(key=lambda found: (-projects.STAGE_RANK[found[0]], found[1].lower()))  # production first
    listed = [
        {"branch": branch, "stage": stage, "builds": [history_resource(entry) for entry in entries]}
        for stage, branch, entries in shown
    ]
    return ok(listed, meta={"per_branch": per_branch, "kept_per_branch": int(settings.project_builds_kept)})


@blueprint.get("/projects/<project_id>/builds/<build_id>")
def read_build(project_id: str, build_id: str) -> Response:
    found = _project(project_id)
    entry = builds.find(build_id, project_id) if build_id.isalnum() and len(build_id) <= 40 else None
    if entry is None or not _sees(found, str(entry["branch"])):
        abort(404, "There is no such build in this project.")
    return ok(history_resource(entry))


@blueprint.get("/projects/<project_id>/branches/<path:branch>/history")
def branch_history(project_id: str, branch: str) -> Response:
    """The branch's timeline, newest first, a page at a time."""
    found = _project(project_id)
    branch = _branch(branch)
    _refuse_unless("instances.view", found, f"history of {project_id} {branch}", branch)
    limit = check.whole_number(
        request.args.get("limit", str(HISTORY_PAGE_DEFAULT)), "limit", minimum=1, maximum=HISTORY_PAGE_MAX
    )
    offset = check.whole_number(request.args.get("offset", "0"), "offset", minimum=0, maximum=1_000_000)
    entries = builds.history(project_id, branch)
    if request.args.get("commits") not in ("1", "true"):  # recorded commits only for those who ask for them
        entries = [entry for entry in entries if entry["type"] != "commit"]
    page = [history_resource(entry) for entry in entries[offset : offset + limit]]
    return ok(page, meta={"total": len(entries), "limit": limit, "offset": offset})


def build_event(entry_id: str) -> dict[str, Any] | None:
    """A "build" live event: the entry as the API shows it, or None when it is gone."""
    entry = builds.find(entry_id)
    return history_resource(entry) if entry else None


# ---------------------------------------------------------------- build options of a branch


def branch_options_resource(project: Mapping[str, Any], branch: str) -> dict[str, Any]:
    """A branch's build options (projects/build_options.py): what applies, where it comes from, what it sets."""
    stage = projects.branch_stage(project, branch)
    found = build_options.effective(project, branch, stage)
    entry = (project.get("branches") or {}).get(branch) or {}
    return {
        "project": project["id"],
        "branch": branch,
        "stage": stage,
        "options": {key: found[key] for key in build_options.KEYS},
        "sources": found["sources"],
        "own": build_options.own(project, branch),
        "project_defaults": {key: build_options.project_default(project, key, stage) for key in build_options.KEYS},
        "changed_by": entry.get("options_changed_by"),
        "changed_at": iso(entry.get("options_changed_at")),
        "commit_statuses": {
            "enabled": bool(projects.option(project, "commit_statuses")),
            "available": commit_status.available(project),
            "context": commit_status.context(stage),
        },
        "links": {"self": f"{PREFIX}/projects/{project['id']}/branches/{branch}/options"},
    }


@blueprint.get("/projects/<project_id>/branches/<path:branch>/options")
def read_branch_options(project_id: str, branch: str) -> Response:
    found = _project(project_id)
    branch = _branch(branch)
    _refuse_unless("instances.view", found, f"build options of {project_id} {branch}", branch)
    return ok(branch_options_resource(found, branch))


@blueprint.put("/projects/<project_id>/branches/<path:branch>/options")
def change_branch_options(project_id: str, branch: str) -> Response:
    """Set (a value) or reset (null: the project's) the branch's own build options; keys left out are kept."""
    found = _project(project_id)
    branch = _branch(branch)
    _refuse_unless("projects.manage", found, f"change the build options of {project_id} {branch}", branch)
    data = check.body()
    unknown = sorted(set(data) - set(build_options.KEYS))
    if unknown or not data:
        known = ", ".join(build_options.KEYS)
        abort(400, f"Send one or more of: {known}" + (f" (unknown: {unknown[0][:40]!r})." if unknown else "."))
    try:
        values = {key: build_options.clean(key, value) for key, value in data.items()}
    except ValueError as error:
        abort(400, str(error))
    with projects.LOCK:
        current = projects.get(project_id) or found
        mine = {k: v for k, v in {**build_options.own(current, branch), **values}.items() if v is not None}
        stage = projects.branch_stage(current, branch)
        merged = {key: mine.get(key, build_options.project_default(current, key, stage)) for key in build_options.KEYS}
        refused = build_options.problem(merged)
        if refused:
            abort(400, refused)
        build_options.change(project_id, branch, values, g.user["name"])
    changed = ", ".join(f"{k}={v}" for k, v in sorted(values.items()))
    log("branch build options changed", detail=f"{project_id} {branch}: {changed}"[:300])
    events.publish("project", project_id)
    return ok(branch_options_resource(projects.get(project_id) or found, branch))


# ---------------------------------------------------------------- access and locks

MAX_GRANTS = 200
MAX_BRANCH_RULES = 100
LOCK_REASON_MAX = 200


def _subject_name(kind: str, value: str) -> str | None:
    """The name people read for a grant's team or account, None when it does not exist (any more)."""
    if kind == "team":
        team = teams.get(value)
        return str(team["name"]) if team else None
    account = accounts.get(value)
    return accounts.display_name(value, account) if account else None


def _subjects(raw: Any, what: str, with_level: bool) -> list[dict[str, str]]:
    """Checked grants ({"type", "id", "level"}) or lock entries ({"type", "id"}): known teams and accounts, once each."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_GRANTS:
        abort(400, f"{what}: a list of at most {MAX_GRANTS} entries.")
    found: dict[tuple[str, str], dict[str, str]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            abort(400, f'{what}: each entry is {{"type": "team" or "user", "id"}}.')
        kind = check.one_of(
            check.text(entry, "type", max_length=10), access.GRANT_TYPES, f"{what}: type is team or user."
        )
        value = check.text(entry, "id", max_length=120)
        if _subject_name(kind, value) is None:
            abort(400, f"{what}: there is no {'team' if kind == 'team' else 'account'} {value[:60]!r}.")
        item = {"type": kind, "id": value}
        if with_level:
            item["level"] = check.one_of(
                check.text(entry, "level", max_length=20),
                access.LEVELS,
                f"{what}: level is {', '.join(access.LEVELS)}.",
            )
        found[(kind, value)] = item
    return sorted(found.values(), key=lambda e: (e["type"], e["id"]))


def _described(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e | {"name": _subject_name(str(e["type"]), str(e["id"]))} for e in entries]


def access_resource(project: Mapping[str, Any]) -> dict[str, Any]:
    found = access.access_of(project)
    locks = {
        branch: (access.describe_lock(lock) or {}) | {"allow": _described(list(lock.get("allow") or []))}
        for branch, lock in (found.get("locks") or {}).items()
    }
    return {
        "mode": access.mode(project),
        "grants": _described(list(found.get("grants") or [])),
        "branches": {
            branch: {"restricted": bool(rule.get("restricted")), "grants": _described(list(rule.get("grants") or []))}
            for branch, rule in sorted((found.get("branches") or {}).items())
        },
        "locks": dict(sorted(locks.items())),
        "levels": list(access.LEVELS),
    }


@blueprint.get("/projects/<project_id>/access")
def read_access(project_id: str) -> Response:
    found = _project(project_id, "projects.access")
    return ok(access_resource(found), meta={"permissions": sorted(access.permissions(g.user, found))})


@blueprint.put("/projects/<project_id>/access")
def change_access(project_id: str) -> Response:
    """Replace the project's mode, grants and branches' rules (its locks stay as they are)."""
    found = _project(project_id, "projects.access")
    data = check.body()
    mode = check.one_of(check.text(data, "mode", max_length=20, default="panel_roles"), access.MODES,
                        "mode: panel_roles (everyone's role applies) or restricted (only those granted).")  # fmt: skip
    grants = _subjects(data.get("grants"), "grants", with_level=True)
    rules_in = data.get("branches") or {}
    if not isinstance(rules_in, dict) or len(rules_in) > MAX_BRANCH_RULES:
        abort(400, f'branches: {{"<branch>": {{"restricted", "grants"}}}}, at most {MAX_BRANCH_RULES} branches.')
    rules: dict[str, Any] = {}
    for branch, rule in rules_in.items():
        if not github.valid_branch(str(branch)) or not isinstance(rule, dict):
            abort(400, f"branches: {str(branch)[:60]!r} is not a branch name with its rules.")
        own = _subjects(rule.get("grants"), f"branches.{branch}.grants", with_level=True)
        restricted = check.flag(rule, "restricted") is True
        if own or restricted:
            rules[str(branch)] = {"restricted": restricted, "grants": own}
    _refuse_branch_rules(found, rules)
    if not _only_removes(access.access_of(found), mode, grants, rules):  # removing restrictions is always possible
        refuse_without_premium("teams", f"change the access of {project_id}")
        refuse_without_feature("per_project_access", f"change the access of {project_id}")
    with projects.LOCK:
        current = projects.get(project_id) or found
        _refuse_branch_rules(current, rules)
        after = {**current, "access": {**access.access_of(current), "mode": mode, "grants": grants, "branches": rules}}
        if not access.allowed(g.user, "projects.access", after):  # nobody locks themselves out of a project
            abort(409, "After this change you could no longer manage this project's access. Grant yourself (or one of "
                       "your teams) the admin level first.")  # fmt: skip
        projects.change(project_id, lambda p: {**p, "access": after["access"]})
    access.forget_restrictions()
    restricted_branches = sorted(b for b, v in rules.items() if v["restricted"])
    log("project access changed", detail=f"{project_id}: mode {mode}, {len(grants)} grants, branch rules "
        f"{', '.join(sorted(rules)) or 'none'}" + (f", restricted {', '.join(restricted_branches)}" if restricted_branches
        else ""))  # fmt: skip
    events.publish("project", project_id)
    return ok(access_resource(projects.get(project_id) or found))


def _refuse_branch_rules(project: Mapping[str, Any], rules: Mapping[str, Any]) -> None:
    """403 unless the caller manages the access of every branch whose rule this change adds, changes or removes: an
    admin of the project that a restricted branch (or the branch's own grants) leaves out cannot change that rule."""
    before = access.access_of(project).get("branches") or {}
    for branch in sorted(set(before) | set(rules)):
        old, new = before.get(branch) or {}, rules.get(branch) or {}
        same = bool(old.get("restricted")) == bool(new.get("restricted")) and _entries(old.get("grants")) == _entries(
            new.get("grants")
        )
        if same:
            continue
        level = access.level(g.user, project, branch)
        if "projects.access" not in access.role_permissions(g.user) & access.LEVEL_PERMISSIONS[level]:
            log(f"refused: change the access rule of {project['id']} {branch}", ok=False)
            abort(403, f"Your access to the branch {branch} does not let you change its rule.")


def _entries(items: Any) -> set[tuple[str, str, str]]:
    return {(str(e.get("type")), str(e.get("id")), str(e.get("level", ""))) for e in items or ()}


def _only_removes(before: Mapping[str, Any], mode: str, grants: list[Any], rules: dict[str, Any]) -> bool:
    """Whether the new access only takes away restrictions: no new grant, no higher level, no newly restricted project
    or branch (always allowed, with or without the teams package)."""
    if mode == "restricted" and before.get("mode") != "restricted":
        return False
    if not _entries(grants) <= _entries(before.get("grants")):
        return False
    old_rules = before.get("branches") or {}
    for branch, rule in rules.items():
        old = old_rules.get(branch) or {}
        if rule["restricted"] and not old.get("restricted"):
            return False
        if not _entries(rule["grants"]) <= _entries(old.get("grants")):
            return False
    return True


def _known_branch(project: Mapping[str, Any], branch: str) -> None:
    known = set(projects.by_branch(project["id"])) | set(project.get("branches") or {}) | set(project.get("seen") or [])
    if branch not in known:
        found, _, _ = branches.heads(project)
        if branch not in found:
            abort(404, "There is no such branch.")


@blueprint.put("/projects/<project_id>/branches/<path:branch>/lock")
def lock_branch(project_id: str, branch: str) -> Response:
    """Lock a branch, or change its lock's reason and who it allows (the caller always stays allowed)."""
    found = _project(project_id)
    branch = _branch(branch)
    _refuse_unless(
        "projects.access", found, f"lock {project_id} {branch}", branch if not access.lock_of(found, branch) else None
    )
    _known_branch(found, branch)
    data = check.body()
    reason = check.text(data, "reason", max_length=LOCK_REASON_MAX)
    allow = _subjects(data.get("allow"), "allow", with_level=False)
    with projects.LOCK:
        current = projects.get(project_id) or found
        before = access.lock_of(current, branch)
        if before is not None and not access.may_unlock(g.user, current, branch):
            log(f"refused: change the lock of {project_id} {branch}", ok=False)
            abort(403, f"The branch {branch} is locked by {before.get('by')}: only the people and teams its lock allows "
                       "(and super admins) may change or remove it.")  # fmt: skip
        me = access.person(g.user)
        lock = {"by": g.user["name"], "at": int(time.time()), "reason": reason, "allow": allow}
        if before is not None:
            lock |= {"by": before.get("by"), "at": before.get("at"), "changed_by": g.user["name"],
                     "changed_at": int(time.time())}  # fmt: skip
        if me and not access.lock_allows(g.user, lock):
            lock["allow"] = sorted([*allow, {"type": "user", "id": me}], key=lambda e: (e["type"], e["id"]))

        def update(project: dict[str, Any]) -> dict[str, Any]:
            current_access = dict(access.access_of(project))
            current_access["locks"] = {**(current_access.get("locks") or {}), branch: lock}
            return {**project, "access": current_access}

        projects.change(project_id, update)
    allowed = ", ".join(f"{e['type']} {e['id']}" for e in lock["allow"])
    log("branch locked" if before is None else "branch lock changed", detail=f"{project_id} {branch}: allows {allowed}"
        + (f"; {reason}" if reason else ""))  # fmt: skip
    for name in projects.by_branch(project_id).get(branch, []):
        events.publish("instance", name)
    events.publish("project", project_id)
    return ok((access.describe_lock(lock) or {}) | {"allow": _described(lock["allow"])})


@blueprint.delete("/projects/<project_id>/branches/<path:branch>/lock")
def unlock_branch(project_id: str, branch: str) -> Response:
    found = _project(project_id)
    branch = _branch(branch)
    lock = access.lock_of(found, branch)
    if lock is None:
        abort(404, f"{branch} is not locked.")
    if not access.may_unlock(g.user, found, branch):
        refuse_unless("projects.access", f"unlock {project_id} {branch}")
        log(f"refused: unlock {project_id} {branch}", ok=False)
        abort(403, f"The branch {branch} is locked by {lock.get('by')}: only the people and teams its lock allows (and "
                   "super admins) may unlock it.")  # fmt: skip

    def update(project: dict[str, Any]) -> dict[str, Any]:
        current_access = dict(access.access_of(project))
        current_access["locks"] = {k: v for k, v in (current_access.get("locks") or {}).items() if k != branch}
        return {**project, "access": current_access}

    projects.change(project_id, update)
    log("branch unlocked", detail=f"{project_id} {branch} (locked by {lock.get('by')})")
    for name in projects.by_branch(project_id).get(branch, []):
        events.publish("instance", name)
    events.publish("project", project_id)
    return no_content()
