"""Instances, jobs and Odoo versions.

    GET    /api/v1/instances?limit=&offset=          list
    GET    /api/v1/instances/<name>                  one instance, with commits, submodules and recent jobs
    GET    /api/v1/instances/<name>/credentials      Odoo admin and master passwords, ports (administrators)
    GET    /api/v1/instances/<name>/logs?lines=      the end of Odoo's log
    POST   /api/v1/instances                         create                         -> 202, Location: job
    PATCH  /api/v1/instances/<name>                  change automation settings and lifetime
    POST   /api/v1/instances/<name>/actions/<action> restart, update, pause, ...    -> 202, Location: job
    DELETE /api/v1/instances/<name>                  delete after a backup; body {"confirm": "<name>"} -> 202
    GET    /api/v1/instance-plan?repo=&branch=&name= the name and database a new instance would get
    GET    /api/v1/instance-quota                    how many instances the caller may still create, and when
    GET    /api/v1/jobs?instance=&limit=&offset=     recent jobs, newest first
    GET    /api/v1/jobs/<id>?include=log             a job
    GET    /api/v1/jobs/<id>/log?offset=             its technical log from a line on
    GET    /api/v1/versions                          Odoo versions and Enterprise availability

Creating instances and running actions reuse the panel's views (api_instances.py), so every input check, role
check and audit-log entry is the same whoever calls.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g, request

from sudoo import addons
from sudoo.config import settings
from sudoo.core import events, jobs, vault
from sudoo.instances import odoo, store
from sudoo.limits import quotas

from .. import api_instances
from .. import validation as check
from ..security import admin_only, log, message_for_user
from .resources import instance_resource, job_resource
from .responses import PREFIX, ok, page

blueprint = Blueprint("instances", __name__)


def _accepted(job_id: str) -> Response:
    """202 Accepted for work that continues in the background; Location points to the job."""
    response = ok(job_resource(jobs.JOBS[job_id]), status=202)
    response.headers["Location"] = f"{PREFIX}/jobs/{job_id}"
    return response


def _limits(default: int = 100) -> tuple[int, int]:
    limit = check.whole_number(request.args.get("limit", str(default)), "limit", minimum=1, maximum=500)
    return limit, check.whole_number(request.args.get("offset", "0"), "offset")


# ---------------------------------------------------------------- instances


@blueprint.get("/instances")
def list_instances() -> Response:
    limit, offset = _limits()
    return page(store.names(), limit, offset, instance_resource)


@blueprint.get("/instances/<name>")
def instance(name: str) -> Response:
    return ok(instance_resource(api_instances.existing(name), full=True))


@blueprint.get("/instances/<name>/credentials")
@admin_only
def credentials(name: str) -> Response:
    env = store.read_env(api_instances.existing(name))
    log("instance credentials viewed", instance=name)
    return ok(
        {
            "login": "admin",
            "admin_password": env["ODOO_ADMIN_PASSWORD"],
            "master_password": vault.decrypt(env["MASTER_PASSWORD"], store.master_password_label(name)),
            "ports": {key.lower(): int(env[key]) for key in env if key.endswith("_PORT") and env[key].isdigit()},
        }
    )


@blueprint.get("/instances/<name>/logs")
def odoo_log(name: str) -> Response:
    lines = check.whole_number(request.args.get("lines", "300"), "lines", minimum=1, maximum=5000)
    return ok({"text": odoo.recent_log(api_instances.existing(name), lines) or ""})


@blueprint.post("/instances")
def create() -> Response:
    started: dict[str, Any] = api_instances.create().get_json()
    response = _accepted(started["job"])
    response.headers["Content-Location"] = f"{PREFIX}/instances/{started['name']}"
    return response


@blueprint.patch("/instances/<name>")
@admin_only
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
    log("instance settings changed", instance=name, detail=", ".join(sorted(values)))
    events.publish("instance", name)
    return ok(instance_resource(name, full=True))


@blueprint.post("/instances/<name>/actions/<action>")
def action(name: str, action: str) -> Response:
    return _accepted(api_instances.run_action(name, action).get_json()["job"])


@blueprint.delete("/instances/<name>")
def delete(name: str) -> Response:
    return _accepted(api_instances.run_action(name, "delete").get_json()["job"])


@blueprint.get("/instance-plan")
@admin_only
def plan() -> Response:
    """What a new instance would be called, before it is created."""
    planned = api_instances.plan().get_json()
    return ok(
        {
            "name": planned["name"],
            "database": planned["db_name"],
            "login": "admin",
            "defaults": {
                "delete_after_days": settings.delete_after_days,
                "auto_update": settings.auto_update,
                "auto_upgrade": settings.auto_upgrade,
                "auto_pause": settings.auto_pause,
            },
        }
    )


@blueprint.get("/instance-quota")
def quota() -> Response:
    return ok(quotas.usage(g.user["name"]))


# ---------------------------------------------------------------- jobs


@blueprint.get("/jobs")
def list_jobs() -> Response:
    limit, offset = _limits(default=30)
    instance_name = request.args.get("instance")
    if instance_name:
        check.instance_name(instance_name)
    found = [j for j in reversed(jobs.JOBS.values()) if not instance_name or j.instance == instance_name]
    return page(found, limit, offset, job_resource)


def _job(job_id: str) -> jobs.Job:
    return jobs.JOBS.get(job_id) or abort(404, "This job does not exist or is too old to be kept.")


@blueprint.get("/jobs/<job_id>")
def job(job_id: str) -> Response:
    found = job_resource(_job(job_id), include_log=request.args.get("include") == "log")
    if found["error"]:
        found["error"] = message_for_user(found["error"])
    return ok(found)


@blueprint.get("/jobs/<job_id>/log")
def job_log(job_id: str) -> Response:
    """The job's log from line `offset` on; `meta.next_offset` is where to continue."""
    found = _job(job_id)
    offset = check.whole_number(request.args.get("offset", "0"), "offset")
    lines = found.log[:]
    return ok({"text": "".join(lines[offset:])}, meta={"offset": offset, "next_offset": len(lines)})


# ---------------------------------------------------------------- versions


@blueprint.get("/versions")
def versions() -> Response:
    return ok([{"version": v, "enterprise": addons.available(v)} for v in odoo.versions()])
