"""The plan of a new instance (what operations.create gets) and starting its creation, for the API and for the
panel's own creations (projects/automation.py: a development instance for a new branch).

An instance of a repository always belongs to that repository's project: the project is created ("adopted") when it
is the repository's first instance (projects/), and the branch's stage is recorded when the branch has none yet.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from sudoo import exposure
from sudoo.core import jobs
from sudoo.instances import naming, operations, stages, store
from sudoo.repos import github

SHOWN = ("version", "edition", "db_name", "repo", "branch", "created_by", "stage", "project")
"""Facts of the plan shown while the instance is being created (web/v1/resources.creating_resource): no ports, no
secrets."""


def build(
    *,
    repo: str,
    branch: str,
    version: str,
    edition: str,
    stage: str,
    user: str,
    switches: Mapping[str, Any],
    custom_name: str = "",
    demo: bool = False,
    install_modules: bool = False,
    modules: list[str] | None = None,
    from_backup: str | None = None,
    from_production: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """The plan: name, database, ports (reserved: release them if the plan is not used), and what to start from.

    `switches`: auto_update, auto_upgrade, auto_pause, delete_after_days. `install_modules`: the repository's
    modules (or its project branch's choice, operations._modules_to_install). `modules`: more modules (Odoo's apps,
    any other technical name) installed with them in the same first run; a repository is not needed for those.
    `from_production`: a production instance
    whose data a new staging instance copies (neutralised). `project`: its id; for an instance of a repository
    without one, the repository's project is found or created."""
    name, db_name = naming.plan_name(repo, branch, custom_name, version, edition)
    if repo and not project:
        from sudoo import projects

        found = projects.for_repository(str(github.repo_name(repo)), version, edition, user)
        project = found["id"]
    return {
        "name": name,
        "db_name": db_name,
        "repo": repo,
        "branch": branch,
        "version": version,
        "edition": edition,
        "demo": demo,
        "install_modules": install_modules,
        # an agent of an older version on another server does not know it: it installs the repository's modules
        # (install_modules) and leaves these out
        "modules": list(modules or []),
        "delete_after_days": int(switches["delete_after_days"]),
        **{key: bool(switches[key]) for key in ("auto_update", "auto_upgrade", "auto_pause")},
        "stage": stage,
        **({} if repo else {"branch": "", "auto_update": False, "auto_upgrade": False}),  # plain Odoo: no code
        "ports": store.reserve_ports(exposure.provider().instance_ports()),
        "created_by": user,
        "from_backup": from_backup,
        "from_production": from_production,
        "project": project or "",
    }


def start(plan: dict[str, Any], user: str, fn: Callable[..., object] | None = None, *args: Any) -> jobs.Job:
    """Start the creation's job; record the branch's stage in its project when the branch has none yet. `fn`: what
    the job runs instead of operations.create, fn(job, plan, *args) (a database upgrade's test instance)."""
    details = {key: plan[key] for key in (*SHOWN, "auto_update", "auto_upgrade", "auto_pause", "delete_after_days")}
    if plan["project"] and plan["branch"]:
        details["build"] = "create"  # the first build of the branch's instance (projects/builds.py)
    before: list[Any] = []

    def prepare() -> None:
        if plan["project"] and plan["branch"]:
            from sudoo import projects

            found = projects.get(plan["project"])
            old = copy.deepcopy(((found or {}).get("branches") or {}).get(plan["branch"]) or {})
            record_stage(plan, user)
            after = ((projects.get(plan["project"]) or {}).get("branches") or {}).get(plan["branch"]) or {}
            keys = {key: after[key] for key in ("stage", "changed_by") if after.get(key) != old.get(key)}
            before.append((old, keys))
            return
        record_stage(plan, user)

    try:
        return jobs.start(  # the branch's stage is recorded once the slot is held: a refused start leaves nothing
            plan["name"], "Create instance", "create", fn or operations.create, plan, *args, user=user, details=details,
            prepare=prepare,
        )  # fmt: skip
    except BaseException:
        if before and plan["project"] and plan["branch"]:  # recorded, and the job then could not begin: undo it
            from sudoo import projects

            projects.put_back_branch(plan["project"], plan["branch"], *before[0])
        raise


def record_stage(plan: Mapping[str, Any], user: str) -> None:
    """Record the stage of the plan's branch in its project when the branch has none yet (also for an instance
    created on another server: the panel keeps the projects)."""
    if plan["project"] and plan["branch"]:
        from sudoo import projects

        project = projects.get(plan["project"])
        if project is not None and not (project.get("branches") or {}).get(plan["branch"], {}).get("stage"):
            projects.record_branch(plan["project"], plan["branch"], stage=plan["stage"], changed_by=user)


def switches_for(stage: str, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The stage's defaults (stages.defaults), with the values `data` gives (already validated)."""
    values = dict(stages.defaults(stage))
    values.update({key: value for key, value in (data or {}).items() if key in values})
    return values
