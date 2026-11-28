"""Projects: one project is one GitHub repository, and every branch of it can run as an instance (the Odoo.sh model).

A project is a document in the storage (collection "projects", key: the project's id):

    id, name            "acme-erp" (letters, digits, hyphens; never changes) and the name people read
    repository          "Owner/Repo" as GitHub spells it (repository_id: the same in lower case, unique)
    odoo_version, edition   what new instances of its branches get (a branch can choose its own version: below)
    origin              how it came: "linked" (an existing repository), "created" (a new, empty repository made from
                        the panel), "adopted" (made from existing instances when projects arrived, or by creating an
                        instance of a repository that had no project yet)
    created_at, created_by
    options             the project's own choices; a missing (or null) one follows the panel's setting (OPTIONS)
    branches            {branch: {"stage", "changed_at", "changed_by", "auto_created", "options", "odoo_version"}}:
                        the stage of each branch someone chose (or the panel recorded), which instance the panel
                        created by itself, the branch's own build options (projects/build_options.py) and its own
                        Odoo version (recorded when someone creates its instance with one; missing or null: the
                        project's), which every new instance of the branch gets (branch_version)
    seen                the branches found on GitHub at the last look (projects/automation.py): a branch missing
                        from it is new, one that disappears was deleted
    access              who may work in it: {"mode": "panel_roles"} today (projects/access.py)

An instance belongs to a project through PROJECT=<id> in its .env (written once, when it is created or adopted, so
nothing restarts). An instance of plain Odoo has no repository and stays outside every project ("standalone").

A branch's stage is the one recorded for it; a branch without a record takes the highest stage of its instances,
and a branch without either is a development branch. Changing a branch's stage moves every instance of the branch
to that stage (web/v1/projects.py), with the rules of instances/stages.py.
"""

from __future__ import annotations

import copy
import re
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from sudoo.config import settings
from sudoo.core.shared import SharedLock
from sudoo.instances import stages
from sudoo.instances.naming import slug
from sudoo.storage import Document, storage

COLLECTION = "projects"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
ORIGINS = ("linked", "created", "adopted")
LOCK = SharedLock(
    "projects"
)  # every worker process; reading and changing a project's branches and instances happen together

OPTIONS: dict[str, str] = {
    "auto_development": "project_auto_development",
    "max_auto_development": "project_max_auto_development",
    "development_demo_data": "project_development_demo_data",
    "staging_on_push": "project_staging_on_push",
    "branch_deleted": "project_branch_deleted",
    "upgrade_test_on_push": "project_upgrade_test_on_push",
    "development_lifetime_days": "delete_after_days",
    "staging_lifetime_days": "staging_delete_after_days",
    # build options (projects/build_options.py: each branch can change them too)
    "development_on_new_commit": "project_development_on_new_commit",
    "module_install": "project_module_install",
    "modules": "project_modules",
    "test_suite": "project_test_suite",
    "test_tags": "project_test_tags",
    "staging_tests": "project_staging_tests",
    "commit_statuses": "project_commit_statuses",
    "email_pusher": "project_email_pusher",
    # pull-request previews (projects/previews.py)
    "pr_previews": "project_pr_previews",
    "pr_preview_limit": "project_pr_preview_limit",
    "pr_previews_forks": "project_pr_previews_forks",
}
"""A project's options and the panel settings that give their defaults."""

STAGE_RANK = {stages.DEVELOPMENT: 0, stages.STAGING: 1, stages.PRODUCTION: 2}


class ProjectError(Exception):
    """A change to a project that is not allowed; the message is meant for people."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- reading


def all_projects() -> list[Document]:
    """Every project, by name."""
    return sorted(storage().all(COLLECTION).values(), key=lambda p: (str(p.get("name", "")).lower(), p["id"]))


def get(project_id: str) -> Document | None:
    return storage().get(COLLECTION, project_id) if project_id else None


def by_repository(repository: str) -> Document | None:
    """The project of a repository ("owner/repo", any case), or None."""
    wanted = repository.lower()
    return next((p for p in storage().all(COLLECTION).values() if p.get("repository_id") == wanted), None)


def project_of(name: str) -> str | None:
    """The id of the project instance `name` belongs to, or None (standalone)."""
    from sudoo.premium import servers  # every server's instances

    return servers.env(name).get("PROJECT") or None


def index() -> dict[str, list[str]]:
    """{project id: its instances, those being created included, sorted}, from one look at every instance. For
    callers that need the instances of many projects at once: instances() looks at every instance of the server
    each time, so asking it once per project costs projects x instances reads (minutes with 1,000 of each)."""
    from sudoo.core import jobs  # late: core/jobs.py is imported by most of the panel
    from sudoo.premium import servers

    found: dict[str, set[str]] = {}
    for name in servers.names():
        project_id = servers.env(name).get("PROJECT")
        if project_id:
            found.setdefault(project_id, set()).add(name)
    for name in jobs.being_created():
        job = jobs.creating(name)
        project_id = job.details.get("project") if job is not None else None
        if project_id:
            found.setdefault(str(project_id), set()).add(name)
    return {project_id: sorted(names) for project_id, names in found.items()}


_INDEX_KEY = "_sudoo_project_index"


def _reading_index() -> dict[str, list[str]] | None:
    """index(), made once for a request that only reads (GET or HEAD) and kept until its answer, else None.

    The access checks of a list ask for a project's instances once per branch or instance they show (the stage of a
    branch is that of its instances), so a list of 1,000 projects or instances asked for them a million times: two
    minutes for one person's project list. Within one read nothing creates an instance; a write to an instance's
    .env during it (store.write_env) drops the copy. Background work and changing requests look every time."""
    from flask import g, has_request_context, request

    if not has_request_context() or request.method not in ("GET", "HEAD"):
        return None
    found: dict[str, list[str]] | None = g.get(_INDEX_KEY)
    if found is None:
        found = index()
        setattr(g, _INDEX_KEY, found)
    return found


def forget_reading_index() -> None:
    """Drop the copy _reading_index() keeps for the current request (an instance's .env was written)."""
    from flask import g, has_request_context

    if has_request_context():
        g.pop(_INDEX_KEY, None)


def instances(project_id: str, known: Mapping[str, list[str]] | None = None) -> list[str]:
    """The instances of a project, those being created included, sorted. `known`: an index() made for this answer,
    when many projects are looked at in a row (within a reading request, one is made and kept by itself)."""
    known = _reading_index() if known is None else known
    if known is not None:
        return list(known.get(project_id, []))
    from sudoo.core import jobs  # late: core/jobs.py is imported by most of the panel
    from sudoo.premium import servers

    found = {name for name in servers.names() if servers.env(name).get("PROJECT") == project_id}
    for name in jobs.being_created():
        job = jobs.creating(name)
        if job is not None and job.details.get("project") == project_id:
            found.add(name)
    return sorted(found)


def branch_of(name: str) -> str:
    """The branch an instance follows ("" for plain Odoo; the plan's branch while it is being created)."""
    from sudoo.core import jobs
    from sudoo.premium import servers

    if servers.exists(name):
        return servers.env(name).get("GIT_BRANCH", "")
    job = jobs.creating(name)
    return str(job.details.get("branch") or "") if job else ""


def stage_of_instance(name: str) -> str:
    from sudoo.core import jobs
    from sudoo.premium import servers

    if servers.exists(name):
        return stages.of(servers.env(name))
    job = jobs.creating(name)
    return str(job.details.get("stage") or stages.DEFAULT) if job else stages.DEFAULT


def by_branch(project_id: str, known: Mapping[str, list[str]] | None = None) -> dict[str, list[str]]:
    """{branch: instances following it} of a project (`known`: as for instances())."""
    found: dict[str, list[str]] = {}
    for name in instances(project_id, known):
        found.setdefault(branch_of(name), []).append(name)
    return found


def branch_stage(project: Mapping[str, Any], branch: str, members: Iterable[str] | None = None) -> str:
    """The stage of a branch: the one recorded, else the highest of its instances, else development."""
    recorded = (project.get("branches") or {}).get(branch, {}).get("stage")
    if recorded in stages.STAGES:
        return str(recorded)
    names = list(members) if members is not None else by_branch(project["id"]).get(branch, [])
    found = [stage_of_instance(name) for name in names]
    return max(found, key=STAGE_RANK.__getitem__) if found else stages.DEVELOPMENT


def branch_version(project: Mapping[str, Any], branch: str) -> str:
    """The Odoo version new instances of `branch` get: the branch's own (recorded on it), else the project's."""
    own = ((project.get("branches") or {}).get(branch) or {}).get("odoo_version")
    return str(own) if own else str(project.get("odoo_version") or "")


def production_branches(project: Mapping[str, Any], grouped: Mapping[str, list[str]] | None = None) -> list[str]:
    """Branches of the project in production: recorded as production, or with a production instance. `grouped`:
    the project's by_branch() when the caller has it already."""
    grouped = by_branch(project["id"]) if grouped is None else grouped
    names = set(project.get("branches") or {}) | set(grouped)
    return sorted(
        branch
        for branch in names
        if branch
        and (
            branch_stage(project, branch, grouped.get(branch, [])) == stages.PRODUCTION
            or any(stage_of_instance(name) == stages.PRODUCTION for name in grouped.get(branch, []))
        )
    )


def production_limit() -> int:
    """Production branches one project may have (PROJECT_PRODUCTION_BRANCHES); 0: no limit. The plan's own limit on
    the whole panel is entitlements.production_refusal."""
    return int(settings.project_production_branches)


def production_refusal(
    project: Mapping[str, Any], branch: str, replacing: str | None = None, replacing_key: str | None = None
) -> str | None:
    """Why `branch` may not become one more production branch of `project`, or None: the project's limit, then the
    plan's (entitlements.py: production branches of the whole panel, withheld production, an expired licence).
    `replacing`: a branch of the project that leaves production as `branch` comes in (it is not counted);
    `replacing_key`: the plan's key that leaves production instead (a plain instance joining the project)."""
    limit = production_limit()
    current = [b for b in production_branches(project) if b not in (branch, replacing)]
    if limit and len(current) >= limit:
        return (
            f"{project['name']} may have {limit} production {'branch' if limit == 1 else 'branches'}, and "
            f"{', '.join(current)} {'is' if len(current) == 1 else 'are'} in production already. Move it to staging or "
            "development first, or raise Production branches per project (Settings, Projects)."
        )
    from sudoo import entitlements  # late: entitlements counts every project's production branches

    return entitlements.production_refusal(
        f"{project['id']}/{branch}", replacing_key or (f"{project['id']}/{replacing}" if replacing else None)
    )


def stays_production(project_id: str, branch: str, without: str) -> bool:
    """Whether another production instance than `without` follows `branch` of the project (the branch then stays a
    production branch whatever happens to `without`)."""
    return any(
        name != without and stage_of_instance(name) == stages.PRODUCTION
        for name in by_branch(project_id).get(branch, [])
    )


def move_production(project_id: str, before: str, after: str, user: str, keep_before: bool) -> Callable[[], None]:
    """Record `after` as a production branch of the project in place of `before` (whose recorded stage is dropped
    unless `keep_before`: another production instance keeps it production); returns what puts both records back as
    they were."""
    saved: dict[str, Any] = {}

    def apply(project: Document) -> Document:
        branches = dict(project.get("branches") or {})
        saved.update({b: branches.get(b) for b in (before, after)})
        branches[after] = {**branches.get(after, {}), "stage": stages.PRODUCTION, "changed_by": user,
                           "changed_at": int(time.time())}  # fmt: skip
        if before in branches and not keep_before:
            branches[before] = {k: v for k, v in branches[before].items() if k != "stage"}
        project["branches"] = branches
        return project

    change(project_id, apply)

    def undo() -> None:
        def restore(project: Document) -> Document:
            branches = dict(project.get("branches") or {})
            for branch, entry in saved.items():
                if entry is None:
                    branches.pop(branch, None)
                else:
                    branches[branch] = entry
            project["branches"] = branches
            return project

        change(project_id, restore)

    return undo


def option(project: Mapping[str, Any], key: str) -> Any:
    """The project's own value of an option, else the panel's setting."""
    own = (project.get("options") or {}).get(key)
    return own if own is not None else getattr(settings, OPTIONS[key])


def options(project: Mapping[str, Any]) -> dict[str, Any]:
    return {key: option(project, key) for key in OPTIONS}


def lifetime_days(project: Mapping[str, Any], stage: str) -> int:
    """Days a new instance of the project in `stage` is kept; 0: kept (always in production)."""
    if stage == stages.PRODUCTION:
        return 0
    return int(option(project, "staging_lifetime_days" if stage == stages.STAGING else "development_lifetime_days"))


# ---------------------------------------------------------------- changing


def _new_id(name: str) -> str:
    base = slug(name)[:36].strip("-") or "project"
    taken = set(storage().all(COLLECTION))
    found, counter = base, 2
    while found in taken:
        found, counter = f"{base[:33].strip('-')}-{counter}", counter + 1
    return found


def create(*, repository: str, odoo_version: str, edition: str, origin: str, user: str, name: str = "") -> Document:
    """A new project for `repository` ("Owner/Repo"). Raises ProjectError when the repository has one already."""
    with LOCK:
        if by_repository(repository) is not None:
            raise ProjectError(f"{repository} has a project already.")
        shown = name.strip() or repository.split("/")[-1]
        project: Document = {
            "id": _new_id(shown),
            "name": shown,
            "repository": repository,
            "repository_id": repository.lower(),
            "odoo_version": odoo_version,
            "edition": edition,
            "origin": origin,
            "created_at": round(time.time()),
            "created_by": user,
            "options": {},
            "branches": {},
            "seen": None,
            # project roles (projects/project_roles.py), or the earlier model when PROJECT_ACCESS_MODEL says so
            "access": {
                "mode": "panel_roles",
                "model": settings.project_access_model,
                "github_read_role": settings.github_read_role,
            },
        }
        storage().put(COLLECTION, project["id"], project)
        return project


def change(project_id: str, fn: Callable[[Document], Document | None]) -> Document | None:
    """Atomically change a project: fn gets the stored document and returns the new one (None deletes it)."""
    with LOCK:

        def apply(found: Document | None) -> Document | None:
            if found is None:
                raise ProjectError("This project does not exist.", 404)
            return fn(dict(found))

        return storage().change(COLLECTION, project_id, apply)


def record_branch(project_id: str, branch: str, **values: Any) -> None:
    """Record facts about a branch (its stage, who changed it, the instance created for it)."""

    def update(project: Document) -> Document:
        branches = dict(project.get("branches") or {})
        branches[branch] = {**branches.get(branch, {}), **values}
        project["branches"] = branches
        return project

    change(project_id, update)


def put_back_branch(project_id: str, branch: str, before: Mapping[str, Any], wrote: Mapping[str, Any]) -> None:
    """Undo only what a start wrote into a branch: each key in `wrote` goes back to its value in `before` (absent: removed),
    and only while it still holds what was written, so another writer's later change to the branch stays."""

    def update(project: Document) -> Document:
        branches = dict(project.get("branches") or {})
        current = dict(branches.get(branch) or {})
        for key, value in wrote.items():
            if current.get(key) != value:
                continue
            if key in before:
                current[key] = copy.deepcopy(before[key])
            else:
                current.pop(key, None)
        if current:
            branches[branch] = current
        else:
            branches.pop(branch, None)
        project["branches"] = branches
        return project

    change(project_id, update)


def remove(project_id: str) -> None:
    storage().delete(COLLECTION, project_id)
    from sudoo import notifications  # late: notifications/ reads projects
    from sudoo.status import pages

    notifications.remove_project(project_id)  # a later project with the same id starts without them
    pages.remove(project_id)
    from sudoo import repo_guard

    repo_guard.forget(project_id)


def for_repository(repository: str, odoo_version: str, edition: str, user: str) -> Document:
    """The project of `repository` ("Owner/Repo"), created ("adopted") when there is none yet."""
    with LOCK:
        return by_repository(repository) or create(
            repository=repository, odoo_version=odoo_version, edition=edition, origin="adopted", user=user
        )
