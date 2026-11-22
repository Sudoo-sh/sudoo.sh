"""Promoting code between a project's branches: the pre-promotion checklist, comparing branches, the scheduled refresh
of staging from production, and pull-request previews.

    GET    /api/v1/projects/<id>/branches/<branch>/promotion-check?refresh=&mode=
                                                     the checklist of the branch going into production (mode=merge:
                                                     being merged into production's branch): {"mode", "items": [{"key", "status" (ok, warning,
                                                     blocked), "text", ...}], "blocked", "blocking", "production",
                                                     "compare"} (projects/promotion.py); refresh=1 reads GitHub again
    GET    /api/v1/projects/<id>/compare?base=<branch>&head=<branch>&refresh=
                                                     what merging head into base brings: commits ahead and behind
                                                     (the commits of head, at most 50), changed files, modules added,
                                                     removed and with a new version, pending migration scripts, the
                                                     GitHub compare address (projects/compare.py)
    GET    /api/v1/projects/<id>/staging-refresh     the scheduled refresh of staging from production: frequency,
                                                     time, weekday, branches, last and next run; meta.branches: the
                                                     project's staging branches
    PUT    /api/v1/projects/<id>/staging-refresh     {"frequency" (off, daily, weekly), "time" ("HH:MM"), "weekday"
                                                     (0 Monday - 6 Sunday), "branches" (empty: every staging branch)}
    POST   /api/v1/projects/<id>/staging-refresh/run refresh the chosen staging branches now -> {"started", "skipped"}
    GET    /api/v1/projects/<id>/previews            the project's pull-request previews and whether the GitHub App
                                                     may comment on pull requests (projects/previews.py)

Reading needs instances.view in the project (and in both branches compared); changing the schedule needs
projects.manage and instances.restore (staging gets production's data), running it now instances.restore. Turning
previews on and off is a project option (PATCH /projects/<id>: pr_previews, pr_preview_limit, pr_previews_forks).
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g, request

from sudoo import projects
from sudoo.instances import store
from sudoo.projects import access, compare, previews, promotion, refresh
from sudoo.repos import github_app

from .. import validation as check
from ..security import log
from .projects import _branch, _project, _refuse_unless
from .responses import iso, ok

blueprint = Blueprint("promotion", __name__)


def _visible_branch(project: dict[str, Any], branch: str) -> str:
    branch = _branch(branch)
    if not access.sees_branch(g.user, project, branch):
        abort(404, "There is no such branch.")
    return branch


@blueprint.get("/projects/<project_id>/branches/<path:branch>/promotion-check")
def promotion_check(project_id: str, branch: str) -> Response:
    found = _project(project_id)
    branch = _visible_branch(found, branch)
    merge = request.args.get("mode") == "merge"
    result = promotion.check(found, branch, g.user, refresh=request.args.get("refresh") == "1", merge=merge)
    result["checked_at"] = iso(result["checked_at"])
    return ok(result)


@blueprint.get("/projects/<project_id>/compare")
def compare_branches(project_id: str) -> Response:
    found = _project(project_id)
    base = _visible_branch(found, request.args.get("base", ""))
    head = _visible_branch(found, request.args.get("head", ""))
    if base == head:
        abort(400, "Choose two different branches to compare.")
    try:
        result = compare.compare(found, base, head, refresh=request.args.get("refresh") == "1")
    except compare.CompareError as error:
        abort(error.status, str(error))
    return ok(result)


def _schedule_resource(project: dict[str, Any]) -> dict[str, Any]:
    found = refresh.schedule(project)
    return {
        "frequency": found["frequency"],
        "time": found["time"],
        "weekday": found["weekday"],
        "branches": list(found["branches"]),
        "last_run": iso(found.get("refreshed_at")),  # the last refresh; saving the schedule is none
        "next_run": iso(refresh.next_run(found)),
        "changed_by": found.get("changed_by"),
    }


def _staging_branches(project: dict[str, Any]) -> list[dict[str, Any]]:
    targets = refresh.targets({**project, refresh.KEY: {**refresh.schedule(project), "branches": []}})
    return [{"name": branch, "instances": members} for branch, members in targets.items()]


@blueprint.get("/projects/<project_id>/staging-refresh")
def read_schedule(project_id: str) -> Response:
    found = _project(project_id)
    from sudoo.config import settings

    return ok(_schedule_resource(found), meta={"branches": _staging_branches(found), "timezone": settings.timezone})


@blueprint.put("/projects/<project_id>/staging-refresh")
def change_schedule(project_id: str) -> Response:
    found = _project(project_id, "projects.manage")
    _refuse_unless("instances.restore", found, f"schedule the refresh of {project_id}'s staging from production")
    try:
        values = refresh.clean(check.body())
    except ValueError as error:
        abort(400, str(error))
    refresh.save(project_id, values, g.user["name"])
    log("staging refresh scheduled", detail=f"{project_id}: {values['frequency']} {values['time']} "
        f"{','.join(values['branches']) or 'every staging branch'}"[:300])  # fmt: skip
    updated = projects.get(project_id) or found
    return ok(_schedule_resource(updated), meta={"branches": _staging_branches(updated)})


@blueprint.post("/projects/<project_id>/staging-refresh/run")
def run_schedule(project_id: str) -> Response:
    found = _project(project_id, "instances.restore")
    from .instances import refuse_unlinked
    from .projects import _refuse_locked_production

    refuse_unlinked("refreshing staging", f"staging refresh of {project_id} refused (panel not linked)")
    from sudoo.projects import staging

    _refuse_locked_production(found, staging.production_instance(found))
    result = refresh.run(found, g.user["name"])
    return ok(result, status=202 if result["started"] else 200)


@blueprint.get("/projects/<project_id>/previews")
def list_previews(project_id: str) -> Response:
    found = _project(project_id)
    shown = []
    for record in sorted(previews.records(found).values(), key=lambda r: -int(r.get("number") or 0)):
        name = record.get("instance")
        if name and not access.sees_instance(g.user, str(name)):
            name = None
        shown.append(
            {
                "number": record.get("number"),
                "title": record.get("title") or "",
                "url": record.get("url"),
                "branch": record.get("branch"),
                "repository": record.get("repository"),
                "fork": bool(record.get("fork")),
                "head_sha": record.get("head_sha") or None,
                "instance": name,
                "instance_exists": bool(name and store.exists(str(name))),
                "existing": bool(record.get("existing")),
                "status": record.get("status") or "creating",
                "reason": record.get("reason") or None,
                "commented": bool(record.get("comment_id")),
                "opened_at": iso(record.get("opened_at")),
            }
        )
    owner = str(found["repository"]).split("/")[0]
    try:
        can_comment = github_app.can_comment(owner)
    except Exception:  # noqa: BLE001 - an unreadable app file: no comments
        can_comment = False
    return ok(
        shown,
        meta={
            "enabled": bool(projects.option(found, "pr_previews")),
            "limit": int(projects.option(found, "pr_preview_limit")),
            "forks": bool(projects.option(found, "pr_previews_forks")),
            "app_connected": github_app.connected(),
            "can_comment": can_comment,
            "created": previews.created_count(found),
        },
    )
