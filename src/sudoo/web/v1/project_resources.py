"""A project's resources, as Odoo.sh sizes a project: workers, timeouts and memory per stage, and per branch.

    GET    /api/v1/projects/<id>/resources           per stage: the defaults, the project's own values, what applies
                                                     and what is computed; the maxima; every instance with what
                                                     applies to it and its storage (database + files, quota); the
                                                     memory this server reserves for instances
    PUT    /api/v1/projects/<id>/resources           {"stage", "limits": {knob: value or null}, "confirm_production"?,
                                                     "preview"?}: replaces the project's values for that stage and
                                                     applies them to the instances of that stage (a job each)
    GET    /api/v1/projects/<id>/branches/<branch>/resources
                                                     the branch's own values, what applies to it and its instances
    PUT    /api/v1/projects/<id>/branches/<branch>/resources
                                                     {"limits", "confirm_production"?, "preview"?}: the same for one
                                                     branch

Reading needs instances.view in the project; changing needs projects.resources (projects/access.py: role ∩ level,
locked branches). Values above the administrator's maxima (RESOURCES_MAX_*) are refused (400). "preview": true
changes nothing and answers what the change would reserve on the server ("plan"). A change that raises the memory
reserved by all instances above what the server can spare is refused (409, capacity.refusal). Applying restarts the
instances that run; production only with "confirm_production": true, after a backup, and the previous values are put
back when Odoo does not answer (operations.apply_resources). An instance with a job running, or on a locked branch
the caller may not change, keeps its containers until it next starts ("waiting").
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo import projects
from sudoo.core import events, jobs
from sudoo.instances import disk_use, operations, resources, stages
from sudoo.projects import access

from .. import validation as check
from ..security import log
from .projects import _branch, _project, _refuse_unless
from .resources import job_resource
from .responses import PREFIX, ok

blueprint = Blueprint("project_resources", __name__)


def _server() -> dict[str, Any]:
    total, usable = resources._server_memory()
    return {
        "cores": resources.cores(),
        "memory_bytes": total or None,
        "usable_bytes": usable or None,
        "reserved_bytes": resources.reservations()["total_bytes"],
    }


def _stage_view(own: Mapping[str, Any], below: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    found = {**below, "own": own}
    values = resources.merged(dict(below)) | dict(own)
    return {
        "own": dict(own),
        "inherited": resources.merged(dict(below)),
        "applies": values | resources.computed_for(found),
        "computed": sorted(resources.computed_for(found)),
        "sources": {knob: "own" if knob in own else _top(found, knob) for knob in resources.KNOBS},
    }


def _top(found: Mapping[str, Mapping[str, Any]], knob: str) -> str:
    return next(layer for layer in reversed(list(found)) if knob in found[layer] or layer == "stage")


def _instance_view(name: str) -> dict[str, Any]:
    stage = resources.stage_of(name)
    return {
        "name": name,
        "branch": projects.branch_of(name),
        "stage": stage,
        "limits": resources.effective(name, stage),
        "pending": resources.pending(name),
        "storage": disk_use.usage(name, stage),
    }


def _local(names: list[str]) -> list[str]:
    """The instances of this server (an instance on another server is sized there)."""
    from sudoo.instances import store

    return [name for name in names if store.exists(name)]


def project_resources(project: Mapping[str, Any]) -> dict[str, Any]:
    names = _local(projects.instances(project["id"]))
    bounds = resources.maxima()
    return {
        "project": project["id"],
        "stages": {
            stage: _stage_view(
                resources.clamp(resources.project_own(project, stage), bounds),
                {"stage": resources.stage_defaults(stage)},
            )
            | {"quota_bytes": disk_use.quota_bytes(stage) or None}
            for stage in stages.STAGES
        },
        "maxima": bounds,
        "odoo_defaults": resources.ODOO_DEFAULTS,
        "instances": [_instance_view(name) for name in names],
        "storage": disk_use.totals(names),
        "server": _server(),
        "changed": project.get("resources_changed"),
        "links": {"self": f"{PREFIX}/projects/{project['id']}/resources"},
    }


def _below(project: Mapping[str, Any], branch: str, stage: str | None = None) -> dict[str, dict[str, Any]]:
    """The layers under a branch's own values: its stage's defaults and its project's values for that stage."""
    stage = stage or projects.branch_stage(project, branch)
    return {
        "stage": resources.stage_defaults(stage),
        "project": resources.clamp(resources.project_own(project, stage)),
    }


def branch_resources(project: Mapping[str, Any], branch: str) -> dict[str, Any]:
    names = _local(projects.by_branch(project["id"]).get(branch, []))
    stage = projects.branch_stage(project, branch, names)
    bounds = resources.maxima()
    entry = (project.get("branches") or {}).get(branch) or {}
    below = _below(project, branch, stage)
    return {
        "project": project["id"],
        "branch": branch,
        "stage": stage,
        **_stage_view(resources.clamp(resources.branch_own(project, branch), bounds), below),
        "maxima": bounds,
        "odoo_defaults": resources.ODOO_DEFAULTS,
        "instances": [_instance_view(name) for name in names],
        "quota_bytes": disk_use.quota_bytes(stage) or None,
        "server": _server(),
        "changed": (
            {"by": entry.get("resources_changed_by"), "at": entry.get("resources_changed_at")}
            if entry.get("resources_changed_by")
            else None
        ),
        "links": {"self": f"{PREFIX}/projects/{project['id']}/branches/{branch}/resources"},
    }


# ---------------------------------------------------------------- changing


def _limits(data: Mapping[str, Any]) -> dict[str, Any]:
    raw = data.get("limits")
    if not isinstance(raw, dict):
        abort(400, 'Send the values to set: {"limits": {"odoo_workers": 2, "odoo_limit_time_real": 300}}.')
    try:
        return resources.clean(raw, bounded=True)
    except resources.Invalid as error:
        abort(400, str(error))


def _apply(
    names: list[str],
    after: Mapping[str, tuple[str, dict[str, Any]]],
    data: Mapping[str, Any],
    save: Any,
    layer: str,
    scope: tuple[str, str],
    previous: dict[str, Any],
    what: str,
    view: dict[str, Any],
) -> dict[str, Any] | Response:
    """Check the change (maxima were checked by _limits), then save it and start a job for each instance it
    changes. Returns the answer's extra fields, or the preview."""
    before = {name: resources.effective(name) for name in names}
    changed = [name for name in names if before[name] != after[name][1]]
    found = resources.plan({name: after[name] for name in changed})
    if check.flag(data, "preview") is True:
        return ok({"plan": found, "changes": changed, "view": view})
    if found["refusal"]:
        log(f"refused: {what}", ok=False, detail=found["refusal"])
        abort(409, found["refusal"])
    if (
        any(after[name][0] == stages.PRODUCTION for name in changed)
        and check.flag(data, "confirm_production") is not True
    ):
        abort(400, 'This change restarts production instances. Confirm it ("confirm_production": true).')
    save()
    started, waiting = [], []
    for name in changed:
        if jobs.busy(name) or access.instance_refusal(g.user, "projects.resources", name):
            waiting.append(name)
            continue
        job = jobs.start(
            name, "Apply resources", "restart", operations.apply_resources, name, previous, layer, scope,
            user=g.user["name"],
        )  # fmt: skip
        started.append(job_resource(job))
    return {"plan": found, "jobs": started, "waiting": waiting}


@blueprint.get("/projects/<project_id>/resources")
def read_project_resources(project_id: str) -> Response:
    return ok(project_resources(_project(project_id)))


@blueprint.put("/projects/<project_id>/resources")
def change_project_resources(project_id: str) -> Response:
    found = _project(project_id, "projects.resources")
    data = check.body()
    stage = data.get("stage")
    if stage not in stages.STAGES:
        abort(400, f"Send the stage to change: one of {', '.join(stages.STAGES)}.")
    values = _limits(data)
    names = [name for name in _local(projects.instances(project_id)) if resources.stage_of(name) == stage]
    trial = dict(found) | {"resources": {**(found.get("resources") or {}), stage: values}}
    after = {}
    for name in names:
        layers = resources.layers(name, stage) | {"project": resources.clamp(resources.project_own(trial, stage))}
        after[name] = (stage, resources.resolve(layers))
    previous = resources.project_own(found, stage)
    answer = _apply(
        names, after, data, lambda: resources.save_project(project_id, stage, values, g.user["name"]), "project",
        (project_id, stage), previous, f"change the {stage} resources of {project_id}",
        _stage_view(values, {"stage": resources.stage_defaults(stage)}),
    )  # fmt: skip
    if isinstance(answer, Response):
        return answer
    detail = ", ".join(f"{k}={v}" for k, v in sorted(values.items())) or "stage defaults"
    log("project resources changed", detail=f"{project_id} {stage}: {detail}"[:300])
    events.publish("project", project_id)
    return ok(project_resources(projects.get(project_id) or found) | answer)


@blueprint.get("/projects/<project_id>/branches/<path:branch>/resources")
def read_branch_resources(project_id: str, branch: str) -> Response:
    found = _project(project_id)
    branch = _branch(branch)
    _refuse_unless("instances.view", found, f"resources of {project_id} {branch}", branch)
    return ok(branch_resources(found, branch))


@blueprint.put("/projects/<project_id>/branches/<path:branch>/resources")
def change_branch_resources(project_id: str, branch: str) -> Response:
    found = _project(project_id)
    branch = _branch(branch)
    _refuse_unless("projects.resources", found, f"change the resources of {project_id} {branch}", branch)
    data = check.body()
    values = _limits(data)
    names = _local(projects.by_branch(project_id).get(branch, []))
    after = {}
    for name in names:
        stage = resources.stage_of(name)
        after[name] = (stage, resources.resolve(resources.layers(name, stage) | {"branch": resources.clamp(values)}))
    previous = resources.branch_own(found, branch)
    answer = _apply(
        names, after, data, lambda: resources.save_branch(project_id, branch, values, g.user["name"]), "branch",
        (project_id, branch), previous, f"change the resources of {project_id} {branch}",
        _stage_view(values, _below(found, branch)),
    )  # fmt: skip
    if isinstance(answer, Response):
        return answer
    detail = ", ".join(f"{k}={v}" for k, v in sorted(values.items())) or "project values"
    log("branch resources changed", detail=f"{project_id} {branch}: {detail}"[:300])
    events.publish("project", project_id)
    return ok(branch_resources(projects.get(project_id) or found, branch) | answer)
