"""Optional "Explain with AI" for a failed build (explain/service.py). Off until an administrator turns it on.

    GET    /api/v1/explain                                    the settings (never the key, only whether it is set)
    PUT    /api/v1/explain                                    {"enabled"?, "endpoint"?, "model"?, "key"?}
    PUT    /api/v1/projects/<id>/explain                      {"enabled"}: the project opts in or out
    GET    /api/v1/projects/<id>/builds/<build>/explain       {"available", "reason", "enabled_for_project"}
    POST   /api/v1/projects/<id>/builds/<build>/explain/preview   {"lang"} -> exactly what would be sent (nothing is)
    POST   /api/v1/projects/<id>/builds/<build>/explain/confirm   {"preview"} -> the answer, labelled model-generated

The settings need settings.manage; a project's opt-in projects.manage; the build's preview and answer the right to
read the branch's logs (the excerpt is log text). The audit log names the person, project, build, host and size of a
send, never the text, the answer or the key.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo.explain import service
from sudoo.projects import access, builds

from .. import validation as check
from ..security import log, require
from .projects import _project
from .responses import ok

blueprint = Blueprint("explain", __name__)


def _fail(error: service.ExplainError) -> Any:
    abort(error.status, str(error))


def _failed_build(project_id: str, build_id: str, need_logs: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    project = _project(project_id)
    entry = builds.find(build_id, project_id) if build_id.isalnum() and len(build_id) <= 40 else None
    if entry is None or not access.sees_branch(g.user, project, str(entry["branch"])):
        abort(404, "There is no such build in this project.")
    if need_logs and not access.allowed(g.user, "instances.logs", project, str(entry["branch"])):
        abort(403, "You may not read this branch's logs, so you may not send them for an explanation.")
    return project, dict(entry)


@blueprint.get("/explain")
@require("settings.manage")
def read_settings() -> Response:
    return ok(service.resource())


@blueprint.put("/explain")
@require("settings.manage")
def change_settings() -> Response:
    data = check.body()
    enabled = data.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        abort(400, "The field 'enabled' must be true or false.")
    key = data.get("key")
    if key is not None and not isinstance(key, str):
        abort(400, "The field 'key' must be text.")
    current = service.resource()
    try:
        shown = service.configure(
            current["enabled"] if enabled is None else enabled,
            check.text(data, "endpoint", max_length=2048) if "endpoint" in data else None,
            check.text(data, "model", max_length=100) if "model" in data else None,
            key,
        )
    except service.ExplainError as error:
        _fail(error)
    changed = sorted(k for k in ("enabled", "endpoint", "model", "key") if k in data)
    log("AI explanations settings changed", detail=f"{', '.join(changed)}; enabled={shown['enabled']}"[:300])
    return ok(shown)


@blueprint.put("/projects/<project_id>/explain")
def project_option(project_id: str) -> Response:
    _project(project_id, "projects.manage")
    data = check.body()
    if not isinstance(data.get("enabled"), bool):
        abort(400, "The field 'enabled' must be true or false.")
    service.set_project(project_id, data["enabled"])
    log("AI explanations for a project", detail=f"{project_id}: {'on' if data['enabled'] else 'off'}")
    return ok({"project": project_id, "enabled": data["enabled"]})


@blueprint.get("/projects/<project_id>/builds/<build_id>/explain")
def availability(project_id: str, build_id: str) -> Response:
    project, entry = _failed_build(project_id, build_id)
    state = service.availability(project_id, entry)
    return ok({**state, "enabled_for_project": project_id in service.config()["projects"]})


@blueprint.post("/projects/<project_id>/builds/<build_id>/explain/preview")
def preview(project_id: str, build_id: str) -> Response:
    _, entry = _failed_build(project_id, build_id)
    data = check.body()
    try:
        shown = service.preview(g.user["name"], project_id, entry, check.text(data, "lang", max_length=5, default="en"))
    except service.ExplainError as error:
        _fail(error)
    return ok(shown)


@blueprint.post("/projects/<project_id>/builds/<build_id>/explain/confirm")
def confirm(project_id: str, build_id: str) -> Response:
    _, entry = _failed_build(project_id, build_id)
    data = check.body()
    preview_id = check.text(data, "preview", max_length=64)
    try:
        answer = service.confirm(g.user["name"], project_id, entry, preview_id)
    except service.ExplainError as error:
        log("AI explanation refused", ok=False, detail=f"{project_id} build {build_id}: {error}"[:300])
        _fail(error)
    log("AI explanation sent", detail=f"{project_id} build {build_id}: {answer['host']}, {answer['bytes']} bytes"[:300])
    return ok(answer)
