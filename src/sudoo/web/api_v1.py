"""The API for other systems: /api/v1, authenticated by an API token and a signature on every request.

How to call it, with examples: docs/PANEL_API.md. Authentication happens in security.py (api_tokens.verify) before
any view runs; a request then acts as the user "api:<token name>": an administrator for write tokens, a read-only
caller for read tokens. Creating instances and running actions reuse the panel's own views (api_instances.py), so
every input check, role check and audit-log entry is the same as for a person using the panel.

Response conventions:
    success      {"data": <resource or list>, "meta": {...}}      200, or 202 Accepted for background jobs
    background   202 with a Location header pointing to the job (GET /api/v1/jobs/<id>)
    errors       RFC 9457 problem details, Content-Type application/problem+json:
                 {"type", "title", "status", "detail", "instance", "request_id"}
    times        ISO 8601 in UTC, e.g. 2026-11-12T08:30:00Z
    every reply  X-Request-ID (the caller's own when it sent a valid one)

    GET    /api/v1/instances?limit=&offset=          list
    GET    /api/v1/instances/<name>                  one instance, with commits, submodules and recent jobs
    POST   /api/v1/instances                         create                         -> 202, Location: job
    PATCH  /api/v1/instances/<name>                  change automation settings and lifetime
    POST   /api/v1/instances/<name>/actions/<action> restart, update, pause, ...    -> 202, Location: job
    DELETE /api/v1/instances/<name>                  delete after a backup; body {"confirm": "<name>"} -> 202
    GET    /api/v1/jobs/<id>?include=log             a background job
    GET    /api/v1/versions                          Odoo versions and Enterprise availability
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from flask import Blueprint, Response, abort, g, jsonify, request

from sudoo import addons
from sudoo.core import jobs
from sudoo.core.state import STATE
from sudoo.instances import monitor, odoo, operations, store
from sudoo.repos.gitrepo import commits, submodules

from . import api_instances
from . import validation as check
from .security import log

PREFIX = "/api/v1"
blueprint = Blueprint("api_v1", __name__, url_prefix=PREFIX)


# ---------------------------------------------------------------- response helpers


def iso(timestamp: float | int | str | None) -> str | None:
    """A Unix time as ISO 8601 in UTC, or None."""
    if timestamp in (None, ""):
        return None
    return datetime.fromtimestamp(float(timestamp), UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def request_id() -> str:
    """The id of this request: the caller's X-Request-ID when it is sensible, otherwise a new one."""
    if "request_id" not in g:
        given = request.headers.get("X-Request-ID", "")
        valid = 8 <= len(given) <= 64 and all(c.isalnum() or c in "-_." for c in given)
        g.request_id = given if valid else uuid.uuid4().hex
    return str(g.request_id)


def ok(data: Any, status: int = 200, meta: dict[str, Any] | None = None) -> Response:
    response = jsonify({"data": data, **({"meta": meta} if meta else {})})
    response.status_code = status
    return response


def accepted(job_id: str) -> Response:
    """202 Accepted for work that continues in the background; Location points to the job."""
    response = ok(job_resource(jobs.JOBS[job_id]), status=202)
    response.headers["Location"] = f"{PREFIX}/jobs/{job_id}"
    return response


def problem(status: int, detail: str) -> Response:
    """An RFC 9457 problem details response."""
    response = jsonify(
        type="about:blank",
        title=HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        instance=request.path,
        request_id=request_id(),
    )
    response.status_code = status
    response.mimetype = "application/problem+json"
    if status == 429:
        response.headers["Retry-After"] = "900"
    return response


def add_request_id(response: Response) -> Response:
    response.headers["X-Request-ID"] = request_id()
    return response


# ---------------------------------------------------------------- resources


def job_reference(job: jobs.Job) -> dict[str, Any]:
    return {"id": job.id, "action": job.action, "status": job.status, "links": {"self": f"{PREFIX}/jobs/{job.id}"}}


def job_resource(job: jobs.Job, include_log: bool = False) -> dict[str, Any]:
    resource = {
        "id": job.id,
        "instance": job.instance,
        "action": job.action,
        "status": job.status,  # queued, running, success, failed
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
        "usage": {"cpu_percent": summary["cpu"], "memory_bytes": summary["memory"]},
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


# ---------------------------------------------------------------- endpoints


@blueprint.get("/instances")
def list_instances() -> Response:
    limit = check.whole_number(request.args.get("limit", "100"), "limit", minimum=1, maximum=500)
    offset = check.whole_number(request.args.get("offset", "0"), "offset")
    names = store.names()
    page = names[offset : offset + limit]
    return ok(
        [instance_resource(name) for name in page],
        meta={"total": len(names), "count": len(page), "limit": limit, "offset": offset},
    )


@blueprint.get("/instances/<name>")
def instance(name: str) -> Response:
    return ok(instance_resource(api_instances.existing(name), full=True))


@blueprint.post("/instances")
def create() -> Response:
    started: dict[str, Any] = api_instances.create().get_json()
    response = accepted(started["job"])
    response.headers["Content-Location"] = f"{PREFIX}/instances/{started['name']}"
    return response


@blueprint.patch("/instances/<name>")
def change(name: str) -> Response:
    """Change only the settings that are sent; the others keep their value."""
    api_instances.existing(name)
    data = check.body()
    switches = {"auto_update": "AUTO_UPDATE", "auto_upgrade": "AUTO_UPGRADE", "auto_pause": "AUTO_PAUSE"}
    values: dict[str, object] = {
        key: str(check.flag(data, field)).lower() for field, key in switches.items() if field in data
    }
    if "delete_after_days" in data:
        values["DELETE_AFTER_DAYS"] = check.days(data, "delete_after_days")
    if not values:
        abort(400, "Send at least one of: auto_update, auto_upgrade, auto_pause, delete_after_days.")
    store.update_env(name, **values)
    log("settings changed through the API", instance=name, detail=", ".join(sorted(values)))
    return ok(instance_resource(name, full=True))


@blueprint.post("/instances/<name>/actions/<action>")
def action(name: str, action: str) -> Response:
    return accepted(api_instances.run_action(name, action).get_json()["job"])


@blueprint.delete("/instances/<name>")
def delete(name: str) -> Response:
    return accepted(api_instances.run_action(name, "delete").get_json()["job"])


@blueprint.get("/jobs/<job_id>")
def job(job_id: str) -> Response:
    found = jobs.JOBS.get(job_id) or abort(404, "This job does not exist or is too old to be kept.")
    return ok(job_resource(found, include_log=request.args.get("include") == "log"))


@blueprint.get("/versions")
def versions() -> Response:
    return ok([{"version": v, "enterprise": addons.available(v)} for v in odoo.versions()])
