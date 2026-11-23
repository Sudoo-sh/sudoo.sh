"""Staging from production: a staging instance runs on a neutralised copy of its project's production data.

Building a staging instance (operations.create with plan["from_production"]) and rebuilding one (rebuild(): a push
to its branch when the project's staging_on_push is "rebuild", or "Rebuild from production" by hand) both start from
a new backup of the project's production instance (kind "staging-copy"; the production instance keeps only its
latest one). The production instance keeps serving meanwhile: the backup is a database dump and a copy of its files.
No job of the production instance (an update, a restore) runs while it is copied.

The copy is then neutralised (instances/backups.neutralize): Odoo's own `neutralize` (Odoo 16 and later) switches off
outgoing and incoming mail servers, scheduled actions, payment providers (test mode or off), IAP and other services
that act on the outside world, and marks the database as neutralised; older versions get the same for mail, crons
and payment providers in SQL. The copy gets its own database identity, the administrator gets this instance's admin
password, web.base.url becomes the staging instance's address, and its e-mail goes to the mail catcher (every
instance that is not production, mailcatcher/). On a rebuild the branch's latest code is downloaded first and the
repository's installed modules are upgraded on the copy, so staging shows production's data with the branch's code.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sudoo import projects
from sudoo.config import settings
from sudoo.core import jobs
from sudoo.core.jobs import Job, JobError
from sudoo.core.state import STATE
from sudoo.instances import backups, odoo, operations, stages, store
from sudoo.instances.store import compose
from sudoo.repos.gitrepo import own_modules

PRODUCTION_WAIT_SECONDS = 1800  # a job running on production (an update) is waited for this long at most


def production_instance(project: Mapping[str, Any]) -> str | None:
    """The project's production instance that staging copies: the one of the first production branch (by name)
    that has an instance whose files exist, or None."""
    grouped = projects.by_branch(project["id"])
    for branch in projects.production_branches(project):
        for name in grouped.get(branch, []):
            if store.exists(name) and stages.is_production(store.read_env(name)):
                return name
    return None


def copy_problem(production: str | None, version: str, edition: str) -> str | None:
    """Why a staging instance of Odoo `version` and `edition` cannot copy `production`, or None."""
    if production is None:
        return (
            "This project has no production instance to copy. Create the instance of its production branch first "
            "(or give a branch the production stage), or make this branch a development branch."
        )
    env = store.read_env(production)
    if backups.series(env["ODOO_VERSION"]) != backups.series(version):
        return (
            f"Production ({production}) runs Odoo {env['ODOO_VERSION']}, this project's new instances Odoo {version}: "
            "a copy can only go into the same Odoo version."
        )
    if env.get("EDITION", "community") != edition:
        return f"Production ({production}) runs Odoo {env.get('EDITION', 'community')}, not {edition}."
    return None


def copy_of_production(job: Job, production: str) -> dict[str, Any]:
    """A new backup of `production` (kind staging-copy), taken while no other job runs on it; the backup's listing
    entry (backups.all_backups) of its main database."""
    job.step(f"Copying the data of {production} (production)")
    lock = jobs.INSTANCE_LOCKS[production]
    if not lock.acquire(timeout=PRODUCTION_WAIT_SECONDS):
        raise JobError(f"{production} has been busy for half an hour; try again when its job has finished.")
    try:
        if not store.exists(production):
            raise JobError(f"The production instance {production} no longer exists.")
        saved = backups.create(job, production, "staging-copy")
    finally:
        lock.release()
    database = store.read_env(production)["DB_NAME"]
    chosen = next((path for path in saved if f"__{database}__" in path.name), saved[0] if saved else None)
    found = next((b for b in backups.all_backups() if chosen and b["file"] == chosen.name), None)
    if found is None:
        raise JobError(f"The copy of {production} could not be read back.")
    job.note(f"Copied the data of {production} ({found['file']})")
    return found


def rebuild(job: Job, name: str) -> None:
    """Replace a staging instance's database and files with a new neutralised copy of its project's production,
    with the branch's latest code and the repository's installed modules upgraded, then start it."""
    env = store.read_env(name)
    project = projects.get(env.get("PROJECT", ""))
    if project is None or not stages.of(env) == stages.STAGING:
        raise JobError("Only a staging instance of a project is rebuilt from production.")
    production = production_instance(project)
    problem = copy_problem(production, env["ODOO_VERSION"], env.get("EDITION", "community"))
    if problem:
        raise JobError(problem)
    assert production is not None
    database = env["DB_NAME"]
    addons = store.addons_dir(name)
    job.expect(8)
    source = copy_of_production(job, production)
    job.step(f"Downloading the latest code of {env['GIT_BRANCH']}")
    operations._require_branch(job, name, addons)
    operations._pull(job, addons)
    job.step("Stopping Odoo")
    job.run(compose(name, "stop", "odoo"), timeout=300)
    job.step(f"Loading the copy into database {database}")
    backups.restore(job, name, source, database)
    job.step("Neutralising the copied data")
    job.note(backups.neutralize(job, name, database))
    job.step("Setting the admin password")
    login = operations._adopt_admin(job, name, database)
    job.note(f"The administrator ({login}) has this instance's admin password, shown on its Access tab.")
    modules = sorted(set(own_modules(addons)) & backups.installed_modules(name, database))
    job.step(f"Upgrading the repository's modules ({len(modules)})" if modules else "Checking the repository's modules")
    if modules:
        operations.refresh_addons_path(job, name)
        command = odoo.run_cmd(name, "-d", database, "-u", ",".join(modules), "--stop-after-init", "--no-http")
        if job.run_unchecked(command, timeout=3600):
            job.note(
                "Some of the repository's modules could not be upgraded on the copy. Odoo works; your administrator can "
                "read why in the server's log.",
                "warning",
            )
    from sudoo.projects import testing  # late: testing imports this package's neighbours

    testing.before_serving(job, name, database)  # the branch's tests, on a throw-away copy of the copy
    STATE.update(name, rebuilt_at=time.time(), rebuilt_from=production, update_failed=None)
    operations._finish_restore(job, name)
    taken = datetime.fromtimestamp(source["created"], settings.zone).strftime("%Y-%m-%d %H:%M")
    job.note(f"Rebuilt from the copy of {production} taken {taken}.")


def rebuild_refusal(name: str) -> str | None:
    """Why instance `name` cannot be rebuilt from production now, or None (checked before the job starts)."""
    env = store.read_env(name)
    project = projects.get(env.get("PROJECT", ""))
    if project is None:
        return f"{name} belongs to no project, so there is no production to copy."
    if stages.of(env) != stages.STAGING:
        return f"{name} is not a staging instance: only staging is rebuilt from production."
    if store.plain(env):
        return f"{name} runs plain Odoo without a repository."
    return copy_problem(production_instance(project), env["ODOO_VERSION"], env.get("EDITION", "community"))
