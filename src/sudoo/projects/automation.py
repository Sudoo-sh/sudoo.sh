"""What a project does by itself when its repository changes on GitHub.

    new branch      a development instance is created for it when the project's auto_development option is on
                    (PROJECT_AUTO_DEVELOPMENT), the branch has no instance and no other stage recorded, the project
                    has fewer than max_auto_development instances it created this way (PROJECT_MAX_AUTO_DEVELOPMENT,
                    0: no limit), and the server's limits allow one more (MAX_INSTANCES, MAX_RUNNING_INSTANCES)
    push            what the branch's on_new_commit says (projects/build_options.py): "update" the instances
                    following the branch get the new code as before (triggers/); "new_build" a development instance
                    is built again from scratch (projects/testing.py) and a staging instance is rebuilt from a new
                    copy of production (projects/staging.py; the default of a project whose staging_on_push is
                    "rebuild", PROJECT_STAGING_ON_PUSH); "nothing" the commit is only recorded. The test
                    instance of a database upgrade (its upgrade branch) runs the upgrade test again when the
                    project's upgrade_test_on_push is on (PROJECT_UPGRADE_TEST_ON_PUSH) and the upgrade uses
                    OpenUpgrade (Odoo's service needs the subscription code, never kept: that test instance only
                    gets the new code)
    deleted branch  its instances show that the branch is gone; then, by the project's branch_deleted option
                    (PROJECT_BRANCH_DELETED): "keep" nothing more, "archive" a backup and the instance switched off,
                    "delete" a backup and the instance deleted. Staging is archived at most; production is never
                    archived or deleted for this

Where the news comes from: the GitHub App's webhook (create, delete and push events) and the github-webhook trigger
(push events, which say when a branch was created or deleted) within seconds; and, as a safety net that needs nothing
from GitHub, a look at every project's branches every POLL_MINUTES (sync_forever). A project remembers the branches
it last saw ("seen"), so each new or deleted branch is handled once, whichever source tells first. A project that has
never been looked at (one made from existing instances) records its branches at the first look without creating
anything, so turning projects on never creates an instance for every existing branch.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from sudoo import link_deadline, projects, upgrades
from sudoo.config import settings
from sudoo.core import audit, events, jobs
from sudoo.core.jobs import Job
from sudoo.core.state import STATE
from sudoo.instances import capacity, operations, planning, stages, store
from sudoo.limits import quotas
from sudoo.projects import access, branches, build_options, previews, staging
from sudoo.projects import charged as charge

SYNC_MINIMUM_SECONDS = 60


# ---------------------------------------------------------------- new branches


def _seen(project: Mapping[str, Any], branch: str | None = None) -> set[str]:
    """The branches the project knows; for a project never looked at, its branches on GitHub now (without `branch`,
    the one being reported as new)."""
    if project.get("seen") is not None:
        return set(project["seen"])
    found, _, error = branches.heads(project, refresh=True)
    return set() if error else set(found) - {branch or ""}


def _remember(
    project_id: str, add: set[str] | None = None, remove: set[str] | None = None, base: set[str] | None = None
) -> None:
    """Change the branches the project knows; `base`: what it knows when it has never looked (seen is None)."""

    def update(project: dict[str, Any]) -> dict[str, Any]:
        current = set(project["seen"]) if project.get("seen") is not None else set(base or ())
        project["seen"] = sorted((current | (add or set())) - (remove or set()))
        return project

    projects.change(project_id, update)


def auto_created(project: Mapping[str, Any]) -> list[str]:
    """The instances the project created by itself for new branches that still exist (or are being created)."""
    members = set(projects.instances(project["id"]))
    names = [str(v.get("auto_created")) for v in (project.get("branches") or {}).values() if v.get("auto_created")]
    return sorted(name for name in names if name in members)


def creation_refusal(project: Mapping[str, Any], branch: str, charged: str = charge.SYSTEM) -> str | None:
    """Why no development instance is created by itself for `branch`, or None. `charged`: the person whose own
    limit the instance counts against (projects/charged.py), "system" for nobody."""
    if not projects.option(project, "auto_development"):
        return "the project does not create instances for new branches"
    if access.held(project, branch) is not None:
        return (
            "the branch is locked"
            if access.lock_of(project, branch) is not None
            else "the branch is closed by its client"
        )
    if link_deadline.restricted():
        return "the panel is not linked to a Sudoo.sh account and the days to link it have passed"
    if projects.by_branch(project["id"]).get(branch):
        return "the branch has an instance already"
    recorded = (project.get("branches") or {}).get(branch, {}).get("stage")
    if recorded and recorded != stages.DEVELOPMENT:
        return f"the branch is a {recorded} branch: its instance is created by hand"
    limit = int(projects.option(project, "max_auto_development"))
    if limit and len(auto_created(project)) >= limit:
        return f"the project has {limit} instances created for new branches already (its limit)"
    if settings.max_instances and len(store.names()) + len(jobs.being_created()) >= settings.max_instances:
        return f"the server has its maximum of {settings.max_instances} instances"
    from sudoo.core import slots

    try:
        return capacity.refusal(
            stage=stages.DEVELOPMENT, user=None if charged == charge.SYSTEM else charged, own_voice=False
        )
    except slots.Unavailable as exc:  # the running record could not be read: refused, said so
        return str(exc)


def branch_created(repository: str, branch: str, user: str = "system", github_id: int | None = None) -> str | None:
    """A branch appeared in `repository` ("owner/repo"): create its development instance when the project's
    options allow it. Returns the new instance's name, or None. `github_id`: the GitHub account the signed webhook
    names as the sender; the instance counts against the person linked to it (projects/charged.py)."""
    project = projects.by_repository(repository)
    if project is None or not branch:
        return None
    with quotas.LOCK, projects.LOCK:
        project = projects.get(project["id"]) or project
        known = _seen(project, branch)
        if branch in known:
            return None  # handled already (the webhook and the branch check both report it)
        _remember(project["id"], add={branch}, base=known)
        branches.forget(project["id"])
        charged, how = charge.of(project, github_id)
        refused = charge.disabled_sender(github_id) or creation_refusal(project, branch, charged)
        if refused:
            audit.record(user, None, "no instance for a new branch", detail=f"{project['id']} {branch}: {refused}")
            return None
        stage = stages.DEVELOPMENT
        defaults = stages.defaults(stage)
        plan = planning.build(
            repo=f"https://github.com/{project['repository']}",
            branch=branch,
            version=projects.branch_version(project, branch),
            edition=project["edition"],
            stage=stage,
            user=user if charged == charge.SYSTEM else charged,
            switches={**defaults, "delete_after_days": projects.lifetime_days(project, stage)},
            demo=bool(build_options.effective(project, branch, stage)["demo_data"]),
            install_modules=True,
            project=project["id"],
        )
        from sudoo.premium import servers

        servers.start_planned(plan, user, str(project.get("default_server") or ""))
        projects.record_branch(project["id"], branch, stage=stage, auto_created=plan["name"], changed_by=user)
    audit.record(
        user, None, "instance created for a new branch", plan["name"],
        detail=f"{project['id']} {branch}; counts against {charge.label(charged, how)}",
    )  # fmt: skip
    # the branch pane shows the creation at once (instead of "No instance" and a Create button that would make a second)
    events.publish("project", project["id"])
    return str(plan["name"])


# ---------------------------------------------------------------- deleted branches


def archive(job: Job, name: str, branch: str) -> None:
    """Back up an instance whose branch was deleted on GitHub, then switch it off; it stays until it is deleted or
    its lifetime ends, and can be started again."""
    job.expect(2)
    operations.backup(job, name)
    operations.switch_off(job, name)
    STATE.update(name, archived={"branch": branch, "at": time.time()})
    job.note(f"The branch {branch} was deleted on GitHub: the instance was backed up and switched off.")


def branch_deleted(repository: str, branch: str, user: str = "system") -> list[str]:
    """A branch was deleted in `repository`: mark its instances and archive or delete them as the project's
    branch_deleted option says. Returns "<instance>:<what was done>" for each instance."""
    project = projects.by_repository(repository)
    if project is None or not branch:
        return []
    done: list[str] = []
    with projects.LOCK:
        project = projects.get(project["id"]) or project
        _remember(project["id"], remove={branch}, base=_seen(project))
        branches.forget(project["id"])
        members = [name for name in projects.by_branch(project["id"]).get(branch, []) if store.exists(name)]
        wanted = str(projects.option(project, "branch_deleted"))
        for name in members:
            STATE.update(name, branch_missing=True)
            stage = stages.of(store.read_env(name))
            action = "keep" if stage == stages.PRODUCTION else wanted
            if stage == stages.STAGING and action == "delete":
                action = "archive"  # staging may hold work of testers: never deleted for this
            if action != "keep" and jobs.busy(name):
                action = "keep"  # it is busy: left as it is, the warning shows that the branch is gone
            if action != "keep" and access.held(project, branch) is not None:
                action = "keep"  # a locked branch: nothing is done to its instances automatically
            if action == "archive":
                jobs.start(name, "Archive (branch deleted on GitHub)", "stop", archive, name, branch, user=user)
            elif action == "delete":
                jobs.start(name, "Delete (branch deleted on GitHub)", "delete", operations.delete, name, user=user)
            done.append(f"{name}:{action}")
            audit.record(user, None, f"branch deleted on GitHub: {action}", name, detail=f"{project['id']} {branch}")
        if not members:

            def drop(found: dict[str, Any]) -> dict[str, Any]:
                found["branches"] = {k: v for k, v in (found.get("branches") or {}).items() if k != branch}
                return found

            projects.change(project["id"], drop)
    return done


# ---------------------------------------------------------------- pushes


def _behaviour(project: Mapping[str, Any], name: str, stage: str) -> str:
    """What a new commit does to instance `name` by its branch's build options (on_new_commit)."""
    return str(build_options.effective(project, projects.branch_of(name), stage)["on_new_commit"])


def skips_update(name: str) -> bool:
    """A new commit for an instance whose branch builds nothing on new commits (on_new_commit "nothing"): the
    commit is recorded in the branch's history (once) and True is returned; False otherwise."""
    env = store.read_env(name)
    project = projects.get(env.get("PROJECT", ""))
    stage = stages.of(env)
    if project is None or stage == stages.PRODUCTION or upgrades.source_of(name) is not None:
        return False
    if _behaviour(project, name, stage) != "nothing":
        return False
    branch = projects.branch_of(name)
    found, _, error = branches.heads(project, refresh=True)
    if not error and found.get(branch):
        from sudoo.projects import builds  # late: builds follows jobs, imported when the panel starts

        builds.commit_recorded(project["id"], branch, name, found[branch])
    return True


def update_for(name: str) -> tuple[str, str, Any, tuple[Any, ...]] | None:
    """How a push updates instance `name` when it is not the usual update: (title, kind, function, arguments) of a
    staging rebuild from production, a development instance built from scratch (on_new_commit "new_build"), or
    None (the usual update, operations.update_code, which runs the branch's tests when it has some)."""
    env = store.read_env(name)
    project = projects.get(env.get("PROJECT", ""))
    stage = stages.of(env)
    if project is None or stage == stages.PRODUCTION:
        return None
    if access.held(project, projects.branch_of(name)) is not None:
        return None  # a locked branch: triggers.request_update skips it anyway
    if stage == stages.DEVELOPMENT:
        if _behaviour(project, name, stage) == "new_build" and not store.plain(env):
            from sudoo.projects import testing

            return "Automatic new build", "update", testing.new_build, (name,)
        return None
    if stage != stages.STAGING:
        return None
    tested = upgrades.source_of(name)
    if tested is not None:  # the test instance of a database upgrade (its upgrade branch)
        if store.read_env(tested).get("PROJECT", "") != env.get("PROJECT", ""):
            return None  # an upgrade's test instance is in its source's project: never copy across projects
        rerun = projects.option(project, "upgrade_test_on_push") and upgrades.method_for(store.read_env(tested)) == (
            "openupgrade"  # Odoo's service needs the subscription code, which is never kept: the code only
        )
        if not rerun or not store.exists(tested) or link_deadline.restricted():
            return None
        if access.copy_reveals(project, projects.branch_of(tested), projects.branch_of(name)):
            return None  # production's data would reach people its branch keeps it from: only by hand
        from sudoo.upgrades import run as upgrade_run

        return "Automatic upgrade test", "upgrade-test", upgrade_run.rerun, (name, tested, None)
    production = staging.production_instance(project)
    if production and access.held(project, projects.branch_of(production)) is not None:
        return None  # production is locked: its data is not copied automatically, the usual update instead
    if _behaviour(project, name, stage) != "new_build" or staging.rebuild_refusal(name):
        return None
    if production and access.copy_reveals(project, projects.branch_of(production), projects.branch_of(name)):
        return None  # production's data would reach people its branch keeps it from: the usual update instead
    return "Automatic rebuild from production", "restore", staging.rebuild, (name,)


# ---------------------------------------------------------------- the branch check


def sync(project: Mapping[str, Any]) -> dict[str, list[str]]:
    """Compare the project's branches on GitHub with those it saw last; handle new and deleted ones."""
    found, _, error = branches.heads(project, refresh=True)
    report: dict[str, list[str]] = {"created": [], "deleted": []}
    if error:
        return report
    if project.get("seen") is None:  # the first look: everything there now is known, nothing is new
        _remember(project["id"], base=set(found))
        return report
    known = set(project["seen"])
    for branch in sorted(set(found) - known):
        if branch_created(str(project["repository_id"]), branch):
            report["created"].append(branch)
    for branch in sorted(known - set(found)):
        branch_deleted(str(project["repository_id"]), branch)
        report["deleted"].append(branch)
    return report


def sync_forever() -> None:
    """Background loop: look at every project's branches (and open pull requests, projects/previews.py) every
    POLL_MINUTES (at least a minute apart)."""
    while True:
        time.sleep(max(SYNC_MINIMUM_SECONDS, settings.poll_minutes * 60))
        for project in projects.all_projects():
            try:
                sync(project)
                previews.poll(project)  # pull requests whose webhook did not arrive
            except Exception as error:  # noqa: BLE001 - one project's trouble never stops the others
                print(f"projects: branches of {project['id']} not checked: {error!r}", flush=True)
