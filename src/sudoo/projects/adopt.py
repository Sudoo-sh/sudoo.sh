"""Existing instances join projects: one project per repository, made from the instances that use it.

Runs when the panel starts (background.py) and whenever an instance appears whose project is not recorded. For each
instance without PROJECT in its .env (or with a project that no longer exists):

    instance of a repository   joins that repository's project; the project is created ("adopted") when it is the
                               first instance of the repository, with the Odoo version and edition of its highest
                               stage instance (production first, then staging, then the newest)
    plain Odoo                 stays standalone: it has no repository, so no branches, and a project is one
                               repository

Only PROJECT=<id> is added to the instance's .env: its name, stage, address, backups, containers and settings stay
exactly as they are, and nothing restarts. The stage of each instance's branch is recorded in the project when the
branch has no stage yet (several instances of one branch: the highest stage), so the project page shows what was
there. Running it again changes nothing (idempotent): instances already in an existing project are skipped.
"""

from __future__ import annotations

import threading
from typing import Any

from sudoo import projects
from sudoo.core import audit, jobs
from sudoo.instances import stages, store
from sudoo.repos import github
from sudoo.repos.gitrepo import origin_url

_DONE = threading.Event()


def repository_of(name: str) -> str | None:
    """ "Owner/Repo" of the instance's repository, or None for plain Odoo."""
    env = store.read_env(name)
    if store.plain(env):
        return None
    return github.repo_name(origin_url(store.addons_dir(name)))


def adopt(user: str = "system") -> dict[str, list[str]]:
    """Put every instance that is in no project into its repository's project. Returns {"joined": instances that
    joined a project, "created": projects created, "standalone": instances of plain Odoo}."""
    report: dict[str, list[str]] = {"joined": [], "created": [], "standalone": []}
    waiting: dict[str, list[tuple[str, dict[str, str]]]] = {}  # repository id -> instances
    spelling: dict[str, str] = {}  # instance -> "Owner/Repo" as its address writes it
    with projects.LOCK:
        for name in store.names():
            if jobs.creating(name):
                continue  # its creation records its project itself
            env = store.read_env(name)
            if env.get("PROJECT") and projects.get(env["PROJECT"]) is not None:
                continue
            repository = repository_of(name)
            if repository is None:
                report["standalone"].append(name)
                continue
            waiting.setdefault(repository.lower(), []).append((name, env))
            spelling[name] = repository
        for repository_id, members in sorted(waiting.items()):
            project = projects.by_repository(repository_id)
            if project is None:
                lead_name, lead = max(members, key=_rank)
                project = projects.create(
                    repository=spelling[lead_name],  # as the most important instance writes it
                    odoo_version=lead["ODOO_VERSION"],
                    edition=lead.get("EDITION", "community"),
                    origin="adopted",
                    user=user,
                )
                report["created"].append(project["id"])
                audit.record(user, None, "project created from existing instances", detail=project["id"])
            for name, _ in members:
                store.update_env(name, PROJECT=project["id"])  # .env only: nothing restarts
                report["joined"].append(name)
                audit.record(user, None, "instance joined a project", name, detail=project["id"])
            _record_stages(project["id"], members)
    _DONE.set()
    return report


def _rank(member: tuple[str, dict[str, str]]) -> tuple[int, int]:
    env = member[1]
    return projects.STAGE_RANK[stages.of(env)], int(env.get("CREATED_AT") or 0)


def _record_stages(project_id: str, members: list[tuple[str, dict[str, str]]]) -> None:
    """Record the stage of each branch that has none yet: the highest stage of its instances."""
    highest: dict[str, str] = {}
    for _, env in members:
        branch, stage = env.get("GIT_BRANCH", ""), stages.of(env)
        if branch and projects.STAGE_RANK[stage] >= projects.STAGE_RANK.get(highest.get(branch, ""), -1):
            highest[branch] = stage

    def record(project: dict[str, Any]) -> dict[str, Any]:
        branches = dict(project.get("branches") or {})
        for branch, stage in highest.items():
            if branch not in branches or branches[branch].get("stage") not in stages.STAGES:
                branches[branch] = {**branches.get(branch, {}), "stage": stage, "changed_by": "system"}
        project["branches"] = branches
        return project

    projects.change(project_id, record)


def adopted() -> bool:
    """Whether adopt() ran in this process."""
    return _DONE.is_set()


def adopt_once() -> None:
    """adopt(), unless it ran already in this process (the web API asks before it lists projects)."""
    if not _DONE.is_set():
        adopt()
