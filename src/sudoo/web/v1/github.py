"""GitHub: the panel's GitHub App, deploy keys and repository checks.

GET    /api/v1/github/app                          the GitHub App: connected?, installations, webhook
POST   /api/v1/github/app/manifest                 start Connect GitHub: the manifest and where to post it
POST   /api/v1/github/app                          finish it: {"code", "state"} from GitHub's redirect -> 201
DELETE /api/v1/github/app                          disconnect (forget the credentials); links to delete it on GitHub
POST   /api/v1/github/app/installations            record {"installation_id"} after installing on GitHub -> 201
POST   /api/v1/github/app/installations/sync       read the installations from GitHub again
DELETE /api/v1/github/app/installations/<id>       forget an installation -> 204
GET    /api/v1/github/repositories?q=              repositories readable through the app (repository picker)
GET    /api/v1/deploy-keys                         deploy keys
POST   /api/v1/deploy-keys                         create for {"repo"}; the public key to add on GitHub -> 201
DELETE /api/v1/deploy-keys/<owner>/<repo>          delete -> 204
GET    /api/v1/repositories/branches?url=          branches of a repository and its default branch
GET    /api/v1/repositories/access?url=&branch=    can the server read the repository and its submodules

Connecting, installing and disconnecting the app are for super admins (settings.manage); everyone who manages
GitHub access sees its state.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from flask import Blueprint, Response, abort, request, session

from sudoo import exposure, triggers
from sudoo.repos import github as gh
from sudoo.repos import github_app as app

from .. import validation as check
from ..security import can, log, require
from .responses import PREFIX, created, no_content, ok

blueprint = Blueprint("github", __name__)

STATE_SECONDS = 3600  # GitHub's code is valid for one hour too


def _github[T](call: Callable[[], T]) -> T:
    """Run a call to GitHub; its refusals become problem details: 400 when what was sent is not valid (an expired
    code, an installation of another app), 409 for a conflict, 502 when GitHub failed (never 401: that would read
    as the end of the caller's own session)."""
    try:
        return call()
    except app.GitHubError as error:
        status = 400 if error.status in (400, 404, 422) else 409 if error.status == 409 else 502
        abort(status, str(error))


def _panel_url() -> str:
    """The panel's address as the browser sees it (its Origin, already checked by security.py), else the public one."""
    origin = request.headers.get("Origin", "")
    if urlsplit(origin).scheme in ("http", "https") and urlsplit(origin).netloc:
        return origin.rstrip("/")
    public = exposure.address(exposure.PANEL)
    if not public:
        abort(409, "The panel has no public address yet. Try again in a minute.")
    return public.rstrip("/")


def _trigger_running() -> bool:
    """Whether the github-app update trigger serves the app's webhook now (UPDATE_TRIGGERS, applied at start)."""
    return any(t.__class__.__name__ == "GitHubAppWebhook" for t in triggers.enabled())


@blueprint.get("/github/app")
@require("github.manage")
def show_app() -> Response:
    described = app.describe()
    public = exposure.address(exposure.PANEL)
    return ok(
        {
            "connected": described is not None,
            "app": described,
            "trigger_running": _trigger_running(),
            "expected_hook_url": public.rstrip("/") + app.HOOK_PATH if public else None,
            "deploy_keys": len(gh.keys()),
        },
        meta={"can_manage": can("settings.manage")},
    )


@blueprint.post("/github/app/manifest")
@require("settings.manage")
def start_connect() -> Response:
    """The manifest the browser posts to GitHub, and a one-time state that GitHub sends back."""
    if app.connected():
        abort(409, "A GitHub App is already connected. Disconnect it first.")
    data = check.body()
    organization = check.text(data, "organization", max_length=39)
    panel = _panel_url()
    hook_base = (exposure.address(exposure.PANEL) or panel).rstrip("/")
    manifest = app.manifest(panel, hook_base, urlsplit(panel).hostname or "")
    state = secrets.token_urlsafe(32)
    action = _github(lambda: app.new_app_url(organization or None, state))
    session["github_app"] = {
        "state": state,
        "expires": time.time() + STATE_SECONDS,
        "hook_url": manifest["hook_attributes"]["url"],
    }
    log("GitHub App connection started", detail=f"organization {organization}" if organization else "personal")
    return ok({"action": action, "manifest": manifest, "state": state})


@blueprint.post("/github/app")
@require("settings.manage")
def finish_connect() -> Response:
    """Exchange the code from GitHub's redirect for the app's credentials, after checking the state."""
    data = check.body()
    code, state = check.text(data, "code", max_length=128), check.text(data, "state", max_length=128)
    started: dict[str, Any] = session.pop("github_app", None) or {}
    if (
        not state
        or not hmac.compare_digest(str(started.get("state", "")), state)
        or float(started.get("expires", 0)) < time.time()
    ):
        log("GitHub App connection refused", detail="state did not match", ok=False)
        abort(
            400, "This answer from GitHub does not belong to a connection started here, or it is too old. Start again."
        )
    described = _github(lambda: app.convert(code))
    app.set_hook_url(str(started.get("hook_url", "")))
    log("GitHub App connected", detail=f"{described.get('slug')} ({described.get('owner', {}).get('login')})")
    return created(app.describe(), f"{PREFIX}/github/app")


@blueprint.delete("/github/app")
@require("settings.manage")
def disconnect() -> Response:
    described = app.disconnect()
    if described is None:
        abort(404, "No GitHub App is connected.")
    log("GitHub App disconnected", detail=described.get("slug"))
    return ok(
        {
            "settings_url": described["settings_url"],
            "installations": [
                {"account": i["account"], "settings_url": i.get("settings_url")} for i in described["installations"]
            ],
        }
    )


@blueprint.post("/github/app/installations")
@require("settings.manage")
def add_installation() -> Response:
    raw = check.body().get("installation_id")
    if isinstance(raw, bool) or not isinstance(raw, int | str):
        abort(400, "installation_id must be a number.")
    number = check.whole_number(str(raw), "installation_id", minimum=1, maximum=10**12)
    record = _github(lambda: app.add_installation(number))
    log("GitHub App installed", detail=f"{record['account']} ({record['repository_selection']} repositories)")
    return created(record, f"{PREFIX}/github/app")


@blueprint.post("/github/app/installations/sync")
@require("settings.manage")
def sync_installations() -> Response:
    found = _github(app.sync_installations)
    log("GitHub App installations read again", detail=", ".join(i["account"] for i in found))
    return ok(found)


@blueprint.delete("/github/app/installations/<int:number>")
@require("settings.manage")
def remove_installation(number: int) -> Response:
    if not app.remove_installation(number):
        abort(404, "This installation is not known to the panel.")
    log("GitHub App installation forgotten", detail=str(number))
    return no_content()


@blueprint.get("/github/repositories")
@require("instances.create")
def repositories() -> Response:
    if not app.connected():
        return ok([], meta={"connected": False})
    found = _github(lambda: app.repositories(check.text(request.args, "q", max_length=100)))
    return ok(found[:200], meta={"connected": True, "total": len(found)})


# --------------------------------------------------------------------------- deploy keys and checks


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
        meta={"can_manage_keys": can("github.manage"), "github_app": app.connected()},
    )
