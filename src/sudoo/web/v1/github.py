"""GitHub: deploy keys and repository checks.

GET    /api/v1/deploy-keys                      deploy keys
POST   /api/v1/deploy-keys                      create for {"repo"}; the public key to add on GitHub -> 201
DELETE /api/v1/deploy-keys/<owner>/<repo>       delete -> 204
GET    /api/v1/repositories/branches?url=       branches of a repository and its default branch
GET    /api/v1/repositories/access?url=&branch= can the server read the repository and its submodules
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, request

from sudoo.repos import github as gh

from .. import validation as check
from ..security import can, log, require
from .responses import PREFIX, created, no_content, ok

blueprint = Blueprint("github", __name__)


@blueprint.get("/deploy-keys")
@require("github.manage")
def list_keys() -> Response:
    return ok(gh.keys())


@blueprint.post("/deploy-keys")
@require("github.manage")
def create_key() -> Response:
    rid, public_key = gh.create_key(check.repo_reference(check.text(check.body(), "repo")))
    log("deploy key created", detail=rid)
    return created(
        {"repo": rid, "public_key": public_key, "github_url": gh.add_key_url(rid)}, f"{PREFIX}/deploy-keys/{rid}"
    )


@blueprint.delete("/deploy-keys/<owner>/<repo>")
@require("github.manage")
def delete_key(owner: str, repo: str) -> Response:
    rid = gh.repo_id(f"https://github.com/{owner}/{repo}")
    if rid is None or not gh.has_key(rid):
        abort(404, "There is no deploy key for this repository.")
    gh.delete_key(rid)
    log("deploy key deleted", detail=rid)
    return no_content()


@blueprint.get("/repositories/branches")
@require("instances.view")
def branches() -> Response:
    names, default, error = gh.branches(check.repo_address(check.text(request.args, "url")))
    if error:
        abort(400, error)
    return ok({"branches": names, "default": default})


@blueprint.get("/repositories/access")
@require("instances.view")
def access() -> Response:
    branch = check.branch(check.text(request.args, "branch"), required=False)
    report = gh.access_report(check.repo_address(check.text(request.args, "url")), branch)
    return ok(
        {"repositories": report, "all_ok": all(entry["ok"] for entry in report)},
        meta={"can_manage_keys": can("github.manage")},
    )
