"""Deploy keys (administrators) and repository checks used when creating an instance or adding a submodule."""

from __future__ import annotations

from typing import cast

from flask import Blueprint, Response, abort, jsonify, request

from sudoo.repos import github

from . import validation as check
from .security import admin_only, is_admin, log

blueprint = Blueprint("github", __name__, url_prefix="/api")


def _query(key: str) -> str:
    """A query parameter, checked like a JSON text field."""
    return check.text(request.args, key)


@blueprint.get("/deploy-keys")
@admin_only
def list_keys() -> Response:
    return jsonify(github.keys())


@blueprint.post("/deploy-keys")
@admin_only
def create_key() -> Response:
    rid, public_key = github.create_key(check.repo_reference(check.text(check.body(), "repo")))
    log("deploy key created", detail=rid)
    return jsonify(repo=rid, public_key=public_key, github_url=github.add_key_url(rid))


@blueprint.post("/deploy-keys/delete")
@admin_only
def delete_key() -> Response:
    # repo_reference() has checked the address, so repo_id() is never None here
    rid = cast(str, github.repo_id(check.repo_reference(check.text(check.body(), "repo"))))
    if not github.has_key(rid):
        abort(404, "There is no deploy key for this repository.")
    github.delete_key(rid)
    log("deploy key deleted", detail=rid)
    return jsonify(ok=True)


@blueprint.get("/repo-branches")
@admin_only
def repo_branches() -> Response:
    names, default, error = github.branches(check.repo_address(_query("url")))
    return jsonify(ok=error is None, error=error, branches=names, default=default)


@blueprint.get("/repo-access")
def repo_access() -> Response:
    """Access report for a repository and the submodules of a branch (default branch when none is given)."""
    branch = check.branch(_query("branch"), required=False)
    report = github.access_report(check.repo_address(_query("url")), branch)
    return jsonify(repos=report, all_ok=all(entry["ok"] for entry in report), can_manage_keys=is_admin())
