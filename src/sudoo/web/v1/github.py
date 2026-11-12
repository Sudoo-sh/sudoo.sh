"""GitHub: deploy keys (administrators) and repository checks.

GET    /api/v1/deploy-keys                      deploy keys
POST   /api/v1/deploy-keys                      create for {"repo"}; the public key to add on GitHub -> 201
DELETE /api/v1/deploy-keys/<owner>/<repo>       delete -> 204
GET    /api/v1/repositories/branches?url=       branches of a repository and its default branch
GET    /api/v1/repositories/access?url=&branch= can the server read the repository and its submodules
"""

from __future__ import annotations

from flask import Blueprint, Response, abort

from sudoo.repos import github as gh

from .. import api_github
from ..security import admin_only, is_admin, log
from .responses import PREFIX, created, no_content, ok

blueprint = Blueprint("github", __name__)


@blueprint.get("/deploy-keys")
@admin_only
def list_keys() -> Response:
    return ok(api_github.list_keys().get_json())


@blueprint.post("/deploy-keys")
def create_key() -> Response:
    made = api_github.create_key().get_json()
    return created(made, f"{PREFIX}/deploy-keys/{made['repo']}")


@blueprint.delete("/deploy-keys/<owner>/<repo>")
@admin_only
def delete_key(owner: str, repo: str) -> Response:
    rid = gh.repo_id(f"https://github.com/{owner}/{repo}")
    if rid is None or not gh.has_key(rid):
        abort(404, "There is no deploy key for this repository.")
    gh.delete_key(rid)
    log("deploy key deleted", detail=rid)
    return no_content()


@blueprint.get("/repositories/branches")
def branches() -> Response:
    found = api_github.repo_branches().get_json()
    if not found["ok"]:
        abort(400, found["error"])
    return ok({"branches": found["branches"], "default": found["default"]})


@blueprint.get("/repositories/access")
def access() -> Response:
    found = api_github.repo_access().get_json()
    return ok({"repositories": found["repos"], "all_ok": found["all_ok"]}, meta={"can_manage_keys": is_admin()})
