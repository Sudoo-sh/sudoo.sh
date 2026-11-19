"""The resources of /api/v1 as JSON documents: the same shapes in answers and in live events.

No resource contains a password, a key or a port; the instance's credentials are a separate resource that only
roles with "instances.credentials" can read (GET /api/v1/instances/<name>/credentials).
"""

from __future__ import annotations

import time
from typing import Any

from sudoo import exposure, projects
from sudoo.config import settings
from sudoo.core import jobs
from sudoo.core.cache import VersionedMemo
from sudoo.core.state import STATE
from sudoo.instances import backups, gateway, metrics, monitor, operations, stages, store
from sudoo.repos.gitrepo import commits, origin_url, submodules

from .responses import PREFIX, iso


def job_reference(job: jobs.Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "action": job.action,
        "status": job.status,
        "progress_percent": job.progress_percent(),
        "links": {"self": f"{PREFIX}/jobs/{job.id}"},
    }


def current_job(job: jobs.Job) -> dict[str, Any]:
    """An instance's job in progress: its reference with the steps so far."""
    return job_reference(job) | {"steps": [{"title": step["title"], "status": step["status"]} for step in job.steps]}


def job_resource(job: jobs.Job, include_log: bool = False) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "id": job.id,
        "instance": job.instance,
        "action": job.action,
        "kind": job.kind,
        "status": job.status,  # queued, running, success, failed
        "progress_percent": job.progress_percent(),  # None when the number of steps is not known in advance
        "requested_by": job.user,
        "started_at": iso(job.started),
        "finished_at": iso(job.finished),
        "steps": [{"title": step["title"], "status": step["status"]} for step in job.steps],
        "notes": job.notes,
        "error": job.hint if job.status == "failed" else None,
        "links": {
            "self": f"{PREFIX}/jobs/{job.id}",
            "instance": None if jobs.is_panel_job(job.instance) else f"{PREFIX}/instances/{job.instance}",
        },
    }
    if include_log:
        resource["log"] = "".join(job.log)
    return resource


def _usage(name: str) -> dict[str, Any]:
    figures = metrics.INSTANCES.get(name)
    if figures:
        return {key: value for key, value in figures.items() if key != "running"}
    measured = monitor.STATUS.get(name, {})
    return {"cpu_percent": measured.get("cpu", 0), "memory_bytes": measured.get("memory", 0)}


def rollback(name: str) -> dict[str, Any] | None:
    """The instance's Rollback offer after a restore: {"backups", "taken_at", "until"}, or None (no restore in the
    last ROLLBACK_HOURS, or it was rolled back already)."""
    snapshot = backups.rollback_snapshot(name)
    if not snapshot:
        return None
    return {
        "backups": [b["file"] for b in snapshot],
        "taken_at": iso(snapshot[0]["created"]),
        "until": iso(snapshot[0]["created"] + settings.rollback_hours * 3600),
    }


def update_failure(found: dict[str, Any] | None) -> dict[str, Any] | None:
    """The instance's last update failed (operations.update_code): when, the job, the sentence that says what happened,
    and whether the instance was put back to its previous version; None after an update that worked."""
    if not found:
        return None
    return {
        "message": found.get("message", ""),
        "failed_at": iso(found.get("at")),
        "rolled_back": bool(found.get("rolled_back")),
        "job": f"{PREFIX}/jobs/{found['job']}" if found.get("job") else None,
    }


def drift(found: dict[str, Any] | None) -> dict[str, Any] | None:
    """Changes made to the instance outside the panel (instances/integrity.py), or None: when they were found, what
    they are, and whether the panel puts its configuration back ("restore") or only reports them ("warn")."""
    if not found:
        return None
    return {"detected_at": iso(found.get("found_at")), "items": list(found.get("items", [])), "action": found["action"]}


# An instance's commits and submodules come from git (several commands); its code changes through jobs, so they are
# read again after every job on it, while one runs, and at least every 30 seconds (changes made by hand).
_CODE: VersionedMemo[tuple[list[dict[str, str]], list[dict[str, str]]]] = VersionedMemo(seconds=30)


def code(name: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(recent commits, submodules) of the instance."""
    addons_dir = store.addons_dir(name)
    version = jobs.generation(name) if not jobs.busy(name) else -time.monotonic_ns()
    return _CODE.get(name, version, lambda: (commits(addons_dir), submodules(addons_dir)))


def backup_resource(backup: dict[str, Any]) -> dict[str, Any]:
    """A backup as GET /api/v1/backups lists it: the file's facts and what its manifest says."""
    return backup | backups.details(backup["file"])


def project_reference(project_id: str | None) -> dict[str, Any] | None:
    """{"id", "name", "links"} of the project an instance belongs to, or None."""
    found = projects.get(project_id or "")
    if found is None:
        return None
    return {"id": found["id"], "name": found["name"], "links": {"self": f"{PREFIX}/projects/{found['id']}"}}


def branch_lock(project_id: str | None, branch: str | None) -> dict[str, Any] | None:
    """The lock of the branch an instance follows: {"locked_by", "locked_at", "reason", "allow"}, or None."""
    from sudoo.projects import access

    found = projects.get(project_id or "")
    return access.describe_lock(access.lock_of(found, branch)) if found and branch else None


def creating_resource(job: jobs.Job, full: bool = False) -> dict[str, Any]:
    """An instance whose creation is queued or running and whose files do not exist yet: the instance resource with
    what the creation's plan says (job.details), status "creating" and the job as current_job; the rest is empty."""
    plan = job.details
    repo = plan.get("repo") or None
    resource: dict[str, Any] = {
        "name": job.instance,
        "status": "creating",
        "stage": plan.get("stage") or stages.DEFAULT,
        "project": project_reference(plan.get("project")),
        "lock": branch_lock(plan.get("project"), plan.get("branch")),
        "url": None,
        "odoo": {
            "version": plan.get("version", ""),
            "edition": plan.get("edition", "community"),
            "database": plan.get("db_name", ""),
        },
        "source": "repository" if repo else "plain",
        "git": {
            "repository": repo,
            "branch": plan.get("branch") or None,
            "branch_deleted_on_github": False,
            "commit": None,
        },
        "settings": {
            "auto_update": bool(plan.get("auto_update")),
            "auto_upgrade": bool(plan.get("auto_upgrade")),
            "auto_pause": bool(plan.get("auto_pause")),
            "delete_after_days": int(plan.get("delete_after_days") or 0),
        },
        "usage": {"cpu_percent": 0, "memory_bytes": 0},
        "uptime": None,
        "gateway_error": None,
        "drift": None,
        "update_failure": None,
        "created_at": iso(job.started),
        "created_by": plan.get("created_by") or job.user,
        "last_visit_at": None,
        "pauses_at": None,
        "expires_at": None,
        "current_job": current_job(job),
        "links": {"self": f"{PREFIX}/instances/{job.instance}"},
    }
    if full:
        resource |= {
            "disk": {"database_bytes": None, "files_bytes": None},
            "recent_commits": [],
            "submodules": [],
            "backups": [],
            "restorable_backup": None,
            "rollback": None,
            "recent_jobs": [job_reference(job)],
        }
    return resource


def instance_resource(name: str, full: bool = False) -> dict[str, Any]:
    """An instance as the API shows it: grouped, ISO times, no passwords or ports. While it is being created, its
    status is "creating"; before its files exist, what its creation's plan says (creating_resource)."""
    creation = jobs.creating(name)
    if creation and not store.exists(name):
        return creating_resource(creation, full)
    env, measured, state = store.read_env(name), monitor.STATUS.get(name, {}), STATE.get(name)
    status = "creating" if creation else monitor.status(name)
    commit = (measured.get("commit") or "").split("|")
    last_visit = state.get("last_visit")
    pausing = stages.pauses_when_idle(env) and settings.idle_hours > 0 and status == "online" and last_visit
    current = jobs.ACTIVE_JOB.get(name) or creation
    resource: dict[str, Any] = {
        "name": name,
        "status": status,  # creating, online, starting, busy, paused, switched_off, stopped, unknown
        "stage": stages.of(env),  # production, staging, development (instances/stages.py)
        "project": project_reference(env.get("PROJECT")),  # None: standalone (plain Odoo)
        "lock": branch_lock(env.get("PROJECT"), env.get("GIT_BRANCH")),  # the branch's lock (projects/access.py)
        "url": exposure.address(name),
        "odoo": {"version": env["ODOO_VERSION"], "edition": env["EDITION"], "database": env["DB_NAME"]},
        # "plain": Odoo without a repository (store.plain; instances made before SOURCE was recorded: no remote)
        "source": "plain" if store.plain(env) or not origin_url(store.addons_dir(name)) else "repository",
        "git": {
            "repository": measured.get("repo") or None,
            "branch": env["GIT_BRANCH"] or None,
            "branch_deleted_on_github": bool(state.get("branch_missing")),
            "commit": (
                dict(zip(("sha", "subject", "age", "author"), commit, strict=True)) if len(commit) == 4 else None
            ),
        },
        "settings": {
            "auto_update": env["AUTO_UPDATE"] == "true",
            "auto_upgrade": env["AUTO_UPGRADE"] == "true",
            "auto_pause": env["AUTO_PAUSE"] == "true",
            "delete_after_days": int(env["DELETE_AFTER_DAYS"]),
        },
        "usage": _usage(name),
        "uptime": measured.get("uptime") or None,
        "gateway_error": gateway.LISTENER_ERRORS.get(name),
        "drift": drift(state.get("drift")),
        "update_failure": update_failure(state.get("update_failed")),
        "created_at": iso(env["CREATED_AT"]),
        "created_by": env.get("CREATED_BY") or None,
        "last_visit_at": iso(last_visit),
        "pauses_at": iso(float(last_visit or 0) + settings.idle_hours * 3600) if pausing else None,
        "expires_at": iso(operations.expires_at(env)),
        "current_job": current_job(current) if current else None,
        "links": {"self": f"{PREFIX}/instances/{name}"},
    }
    from sudoo.instances import managed_names

    resource["managed_name"] = managed_names.resource(name)  # its name under Sudoo.sh's domain (every plan), or None
    if full:
        recent_commits, found_submodules = code(name)
        disk = monitor.disk_usage(name)
        latest = backups.latest(name)
        resource |= {
            "disk": {"database_bytes": disk.get("db"), "files_bytes": disk.get("files")},
            "recent_commits": recent_commits,
            "submodules": found_submodules,
            "backups": [
                {
                    "file": b["file"],
                    "kind": b["kind"],
                    "database": b["db"],
                    "size_bytes": b["size"],
                    "created_at": iso(b["created"]),
                }
                for b in backups.own_backups(name)
            ],
            "restorable_backup": (latest or {}).get("file"),
            "rollback": rollback(name),
            "recent_jobs": [
                job_reference(job)
                for job in reversed(jobs.JOBS.values())
                if job.instance == name
            ][:15],  # fmt: skip
        }
    return resource
