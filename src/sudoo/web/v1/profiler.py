"""The code profiler of an instance (instances/profiler.py): the Tools tab.

    GET    /api/v1/instances/<name>/profiler                      what can be done: enabled, running, production,
                                                                  the recording being made, the past ones, limits
    POST   /api/v1/instances/<name>/profiler                      start: {"minutes" (1 to PROFILER_MAX_MINUTES),
                                                                  "confirm_production"} -> 202
    POST   /api/v1/instances/<name>/profiler/stop                 stop early: py-spy writes what it has -> 202
    GET    /api/v1/instances/<name>/profiles/<id>                 a recording and its call tree (the flame graph)
    GET    /api/v1/instances/<name>/profiles/<id>/download        the speedscope file (opens in speedscope.app)
    DELETE /api/v1/instances/<name>/profiles/<id>                 delete it -> 204

Every endpoint needs instances.profile in the instance (roles.py: PROFILER_ROLES, project access, locked branches).
A production instance is profiled only when PROFILER_PRODUCTION allows it and the caller confirms it, every time.
Starting, stopping, downloading and deleting are audit-logged.
"""

from __future__ import annotations

from typing import Any, NoReturn

from flask import Blueprint, Response, abort, g, send_file

from sudoo.config import settings
from sudoo.instances import monitor, profiler, stages, store
from sudoo.instances.profiler import ProfilerError

from .. import validation as check
from ..security import client_ip, log, require
from .instances import existing
from .responses import PREFIX, iso, no_content, ok

blueprint = Blueprint("profiler", __name__)

ASLEEP = ("paused", "switched_off", "stopped")


def _fail(error: ProfilerError, name: str, what: str) -> NoReturn:
    if error.status in (403, 409):
        log(f"refused: {what}", instance=name, ok=False, detail=str(error))
    abort(error.status, str(error))


def recording_resource(name: str, meta: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(meta["id"])
    base = f"{PREFIX}/instances/{name}/profiles/{profile_id}"
    return {
        **{k: meta.get(k) for k in ("id", "user", "minutes", "seconds", "samples", "threads", "rate", "size")},
        "started_at": iso(meta.get("started_at")),
        "ended_at": iso(meta.get("ended_at")),
        "stopped_early": bool(meta.get("stopped_early")),
        "production": bool(meta.get("production")),
        "links": {"self": base, "download": f"{base}/download"},
    }


def _run_resource(run: profiler.Run) -> dict[str, Any]:
    found = run.resource()
    return {
        **found,
        "started_at": iso(found["started_at"]),
        "recording_since": iso(found["recording_since"]),
        "ends_at": iso(found["ends_at"]),
    }


def _state(name: str) -> dict[str, Any]:
    production = stages.is_production(store.read_env(name))
    run = profiler.current(name)
    status = monitor.status(name)
    return {
        "enabled": bool(settings.profiler_enabled),
        "production": production,
        "production_allowed": bool(settings.profiler_production),
        "running": status not in ASLEEP,
        "status": status,
        "max_minutes": int(settings.profiler_max_minutes),
        "keep": int(settings.profiler_keep),
        "rate": int(settings.profiler_rate),
        "current": _run_resource(run) if run else None,
        "last_error": profiler.LAST_ERROR.get(name),
        "recordings": [recording_resource(name, meta) for meta in profiler.recordings(name)],
    }


@blueprint.get("/instances/<name>/profiler")
@require("instances.profile")
def state(name: str) -> Response:
    return ok(_state(existing(name)))


@blueprint.post("/instances/<name>/profiler")
@require("instances.profile")
def start(name: str) -> Response:
    existing(name)
    data = check.body()
    raw = data.get("minutes", 1)
    if not isinstance(raw, int) or isinstance(raw, bool):
        abort(400, "minutes must be a whole number.")
    production = stages.is_production(store.read_env(name))
    if production:
        if not settings.profiler_production:
            _fail(ProfilerError(403, "production-off", "Profiles of production instances are switched off on this "
                                "panel."), name, "profiler in production")  # fmt: skip
        if not check.flag(data, "confirm_production"):
            abort(400, f"{name} is a production instance: confirm that you want to record a profile of it "
                  '("confirm_production": true).')  # fmt: skip
    if monitor.status(name) in ASLEEP:
        abort(409, "The instance is not running: start it, then record a profile.")
    try:
        run = profiler.start(name, g.user["name"], client_ip(), raw, production)
    except ProfilerError as error:
        _fail(error, name, "profiler")
    log("profiler started", instance=name,
        detail=f"{run.id}: {raw} min" + (" (production, confirmed)" if production else ""))  # fmt: skip
    return ok(_run_resource(run), status=202)


@blueprint.post("/instances/<name>/profiler/stop")
@require("instances.profile")
def stop(name: str) -> Response:
    existing(name)
    run = profiler.stop(name)
    if run is None:
        abort(409, "No profile of this instance is being recorded.")
    log("profiler stopped", instance=name, detail=run.id)
    return ok(_run_resource(run), status=202)


@blueprint.get("/instances/<name>/profiles/<profile_id>")
@require("instances.profile")
def read(name: str, profile_id: str) -> Response:
    existing(name)
    try:
        meta = profiler.recording(name, profile_id)
        found = profiler.tree(name, profile_id)
    except ProfilerError as error:
        abort(error.status, str(error))
    return ok({**recording_resource(name, meta), "tree": found})


@blueprint.get("/instances/<name>/profiles/<profile_id>/download")
@require("instances.profile")
def download(name: str, profile_id: str) -> Response:
    existing(name)
    try:
        path = profiler.raw_file(name, profile_id)
    except ProfilerError as error:
        abort(error.status, str(error))
    log("profile downloaded", instance=name, detail=profile_id)
    response = send_file(path, mimetype="application/json", as_attachment=True,
                         download_name=f"{name}-{profile_id}.speedscope.json", max_age=0)  # fmt: skip
    response.headers["Cache-Control"] = "no-store"
    return response


@blueprint.delete("/instances/<name>/profiles/<profile_id>")
@require("instances.profile")
def delete(name: str, profile_id: str) -> Response:
    existing(check.instance_name(name))
    try:
        profiler.delete(name, profile_id)
    except ProfilerError as error:
        abort(error.status, str(error))
    log("profile deleted", instance=name, detail=profile_id)
    return no_content()
