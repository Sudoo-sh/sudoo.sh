"""A project's repository guard (repo_guard/): CI checks added through a pull request, their results, branch rules.

    GET    /api/v1/projects/<id>/guard                 the choices, the panel's pull request, the rules it applied,
                                                       the GitHub App's permissions for it; "may_change"
    PATCH  /api/v1/projects/<id>/guard                 {"block_promotion": bool}
    POST   /api/v1/projects/<id>/guard/checks/preview  {"checks"}: the files the pull request would add, change or
                                                       remove (nothing changes)
    PUT    /api/v1/projects/<id>/guard/checks          {"checks", "dry_run"?}: save and open, update or close the
                                                       panel's pull request
    POST   /api/v1/projects/<id>/guard/pull/refresh    read the pull request's state from GitHub again
    GET    /api/v1/projects/<id>/guard/results?sha=..  the CI results of up to 30 commits (comma-separated)
    POST   /api/v1/projects/<id>/guard/rules/preview   {"rules", "turn_off"?}: current and new rules, the difference
    PUT    /api/v1/projects/<id>/guard/rules           {"rules", "confirm", "dry_run"?}: apply them; "confirm" is the
                                                       repository's name when production is affected
    DELETE /api/v1/projects/<id>/guard/rules           {"confirm", "dry_run"?}: remove the rules the panel made

Reading needs instances.view in the project; every change needs projects.access there (the project's Admins, and
super admins), is audit-logged, and is refused for Sudoo.sh support (no access changes).
"""

from __future__ import annotations

import re
from typing import Any, NoReturn

from flask import Blueprint, Response, abort, g, request

from sudoo import repo_guard
from sudoo.projects import access
from sudoo.repos.github_app import GitHubError

from .. import validation as check
from ..security import log
from .projects import _project
from .responses import ok

blueprint = Blueprint("repo_guard", __name__)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_SHAS = 30


def _fail(error: Exception) -> NoReturn:
    if isinstance(error, repo_guard.GuardError):
        abort(error.status, str(error))
    status = getattr(error, "status", 502)
    abort(409 if status in (401, 403, 404, 409, 422) else 400 if status == 400 else 502, str(error))


def _changer(project_id: str) -> dict[str, Any]:
    found = _project(project_id, "projects.access")
    if g.user.get("support"):
        abort(403, "Sudoo.sh support cannot change a repository's rules.")
    return found


@blueprint.get("/projects/<project_id>/guard")
def read_guard(project_id: str) -> Response:
    found = _project(project_id)
    view = repo_guard.describe(found)
    view["may_change"] = access.refusal(g.user, "projects.access", found) is None and not g.user.get("support")
    return ok(view)


@blueprint.patch("/projects/<project_id>/guard")
def change_guard(project_id: str) -> Response:
    found = _changer(project_id)
    data = check.body()
    block = check.flag(data, "block_promotion")
    view = repo_guard.set_block_promotion(found, block, g.user["name"])
    log(f"repository guard: failing checks {'block' if block else 'do not block'} promotion", detail=project_id)
    return ok(view)


def _checks(project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        return repo_guard.clean_checks(data.get("checks") or {}, repo_guard.config(project_id)["checks"])
    except repo_guard.GuardError as error:
        _fail(error)


def _rules(project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        return repo_guard.clean_rules(data.get("rules") or {}, repo_guard.config(project_id)["rules"])
    except repo_guard.GuardError as error:
        _fail(error)


@blueprint.post("/projects/<project_id>/guard/checks/preview")
def preview_checks(project_id: str) -> Response:
    found = _changer(project_id)
    chosen = _checks(project_id, check.body())
    try:
        return ok(repo_guard.preview_checks(found, chosen))
    except (GitHubError, repo_guard.GuardError) as error:
        _fail(error)


@blueprint.put("/projects/<project_id>/guard/checks")
def change_checks(project_id: str) -> Response:
    found = _changer(project_id)
    data = check.body()
    chosen = _checks(project_id, data)
    dry_run = check.flag(data, "dry_run")
    try:
        result = repo_guard.sync_checks(found, chosen, g.user["name"], dry_run=dry_run)
    except (GitHubError, repo_guard.GuardError) as error:
        log("repository guard: checks not changed", ok=False, detail=f"{project_id}: {error}")
        _fail(error)
    if not dry_run:
        pull = result.get("pull") or {}
        what = "on" if chosen["enabled"] else "off"
        log(f"repository guard: checks {what} ({result['result']})",
            detail=f"{project_id}: {pull.get('url') or 'no pull request'}")  # fmt: skip
    return ok(result)


@blueprint.post("/projects/<project_id>/guard/pull/refresh")
def refresh_pull(project_id: str) -> Response:
    found = _project(project_id, "projects.manage")
    try:
        return ok(repo_guard.refresh_pull(found))
    except (GitHubError, repo_guard.GuardError) as error:
        _fail(error)


@blueprint.get("/projects/<project_id>/guard/results")
def read_results(project_id: str) -> Response:
    found = _project(project_id)
    shas = [s for s in (request.args.get("sha") or "").split(",") if s]
    if len(shas) > MAX_SHAS or not all(SHA_RE.match(s) for s in shas):
        abort(400, f"sha: up to {MAX_SHAS} full commit hashes, comma-separated.")
    return ok(repo_guard.results(found, shas))


@blueprint.post("/projects/<project_id>/guard/rules/preview")
def preview_rules(project_id: str) -> Response:
    found = _changer(project_id)
    data = check.body()
    chosen = _rules(project_id, data)
    try:
        return ok(repo_guard.preview_rules(found, chosen, turn_off=check.flag(data, "turn_off")))
    except (GitHubError, repo_guard.GuardError) as error:
        _fail(error)


@blueprint.put("/projects/<project_id>/guard/rules")
def apply_rules(project_id: str) -> Response:
    found = _changer(project_id)
    data = check.body()
    chosen = _rules(project_id, data)
    dry_run = check.flag(data, "dry_run")
    try:
        result = repo_guard.apply_rules(found, chosen, g.user["name"], check.text(data, "confirm"), dry_run=dry_run)
    except (GitHubError, repo_guard.GuardError) as error:
        if not dry_run:
            log("repository guard: branch rules not applied", ok=False, detail=f"{project_id}: {error}")
        _fail(error)
    if not dry_run:
        log("repository guard: branch rules applied",
            detail=f"{project_id}: {result['changes']} changes with {result['method']}")  # fmt: skip
    return ok(result)


@blueprint.delete("/projects/<project_id>/guard/rules")
def remove_rules(project_id: str) -> Response:
    found = _changer(project_id)
    data = check.body()
    dry_run = check.flag(data, "dry_run")
    chosen = repo_guard.config(project_id)["rules"]
    try:
        result = repo_guard.apply_rules(found, chosen, g.user["name"], check.text(data, "confirm"), turn_off=True,
                                        dry_run=dry_run)  # fmt: skip
    except (GitHubError, repo_guard.GuardError) as error:
        if not dry_run:
            log("repository guard: branch rules not removed", ok=False, detail=f"{project_id}: {error}")
        _fail(error)
    if not dry_run:
        log("repository guard: branch rules removed", detail=f"{project_id}: {result['changes']} removed")
    return ok(result)
