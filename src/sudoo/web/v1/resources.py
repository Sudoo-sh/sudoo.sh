"""The resources of /api/v1 as JSON documents: the same shapes in answers and in live events.

No resource contains a password, a key or a port; the instance's credentials are a separate resource that only
administrators can read (GET /api/v1/instances/<name>/credentials).
"""

from __future__ import annotations

from typing import Any

from sudoo.core import jobs
from sudoo.core.state import STATE
from sudoo.instances import metrics, monitor, operations, store
from sudoo.repos.gitrepo import commits, submodules

from .. import api_instances
from .responses import PREFIX, iso


def job_reference(job: jobs.Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "action": job.action,
        "status": job.status,
        "progress_percent": job.progress_percent(),
        "links": {"self": f"{PREFIX}/jobs/{job.id}"},
    }


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
        "links": {"self": f"{PREFIX}/jobs/{job.id}", "instance": f"{PREFIX}/instances/{job.instance}"},
    }
    if include_log:
        resource["log"] = "".join(job.log)
    return resource


def _usage(name: str, summary: dict[str, Any]) -> dict[str, Any]:
    """The latest figures (metrics.py); the monitor's last reading until the first sample."""
    figures = metrics.INSTANCES.get(name)
    if not figures:
        return {"cpu_percent": summary["cpu"], "memory_bytes": summary["memory"]}
    return {key: value for key, value in figures.items() if key != "running"}


def instance_resource(name: str, full: bool = False) -> dict[str, Any]:
    """An instance as the API shows it: grouped, ISO times, no passwords or ports."""
    summary, env = api_instances.summary(name), store.read_env(name)
    commit = summary["commit"]
    current = jobs.ACTIVE_JOB.get(name)
    resource: dict[str, Any] = {
        "name": name,
        "status": summary["status"],  # online, starting, busy, paused, switched_off, stopped, unknown
        "url": summary["url"],
        "odoo": {"version": env["ODOO_VERSION"], "edition": env["EDITION"], "database": env["DB_NAME"]},
        "git": {
            "repository": summary["repo"] or None,
            "branch": env["GIT_BRANCH"],
            "branch_deleted_on_github": summary["branch_missing"],
            "commit": (
                {"sha": commit["sha"], "subject": commit["subject"], "author": commit["author"], "age": commit["when"]}
                if commit
                else None
            ),
        },
        "settings": {
            "auto_update": env["AUTO_UPDATE"] == "true",
            "auto_upgrade": env["AUTO_UPGRADE"] == "true",
            "auto_pause": env["AUTO_PAUSE"] == "true",
            "delete_after_days": int(env["DELETE_AFTER_DAYS"]),
        },
        "usage": _usage(name, summary),
        "uptime": summary["uptime"] or None,
        "gateway_error": summary["gateway_error"],
        "created_at": iso(env["CREATED_AT"]),
        "last_visit_at": iso(STATE.get(name).get("last_visit")),
        "pauses_at": iso(summary["pauses_at"]),
        "expires_at": iso(operations.expires_at(env)),
        "current_job": job_reference(current) if current else None,
        "links": {"self": f"{PREFIX}/instances/{name}"},
    }
    if full:
        addons_dir = store.addons_dir(name)
        disk = monitor.disk_usage(name)
        resource |= {
            "disk": {"database_bytes": disk.get("db"), "files_bytes": disk.get("files")},
            "recent_commits": commits(addons_dir),
            "submodules": submodules(addons_dir),
            "recent_jobs": [
                job_reference(job)
                for job in reversed(jobs.JOBS.values())
                if job.instance == name
            ][:15],  # fmt: skip
        }
    return resource
