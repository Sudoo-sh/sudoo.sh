"""A project's Status tab and its public status page (status/).

    GET  /api/v1/projects/<id>/status          the banner, every target (production instance, server, backups,
                                               off-site copies) with its state, uptime of a day, a week and a month,
                                               30 days of uptime and response time, and the incidents of 30 days;
                                               "public": the public page's state and link
    PUT  /api/v1/projects/<id>/status/public   {"enabled": bool, "new_link"?: bool}: turn the public page on or off,
                                               or give it a new link (the old one stops working)
    GET  /api/v1/public-status/<token>         without a session: the public page (the project's name, each target's
                                               name, state and uptime; nothing else). 404 for an unknown token, a
                                               page that is off, or STATUS_PUBLIC_PAGES off

Reading needs instances.view in the project; the public page's switch needs projects.manage.
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, g

from sudoo import projects
from sudoo.status import checks, pages

from .. import validation as check
from ..security import log
from .projects import _project
from .responses import ok

blueprint = Blueprint("project_status", __name__)


@blueprint.get("/projects/<project_id>/status")
def read_status(project_id: str) -> Response:
    found = _project(project_id)
    answer = checks.project_status(found)
    may_manage = "projects.manage" in _permissions(found)
    answer["public"] = pages.view(project_id) if may_manage else None
    return ok(answer)


def _permissions(project: dict[str, object]) -> set[str]:
    from sudoo.projects import access

    return {p for p in ("projects.manage",) if access.refusal(g.user, p, project) is None}


@blueprint.put("/projects/<project_id>/status/public")
def change_public_page(project_id: str) -> Response:
    _project(project_id, "projects.manage")
    data = check.body()
    enabled = check.flag(data, "enabled")
    new_link = check.flag(data, "new_link")
    view = pages.change(project_id, enabled, new_link, g.user["name"])
    log("public status page " + ("new link" if new_link and enabled else "on" if enabled else "off"), detail=project_id)
    return ok(view)


@blueprint.get("/public-status/<token>")
def public_page(token: str) -> Response:
    project_id = pages.project_of(token)
    found = projects.get(project_id) if project_id else None
    if found is None:
        abort(404, "There is no such status page.")
    response = ok(checks.public_status(found))
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return response
