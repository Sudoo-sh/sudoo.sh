"""Checks of a project's link to GitHub, like "Verify Deploy Key" and "Verify Webhook" on Odoo.sh.

check(project) answers, for the project's repository, one entry per question, each {"id", "status", "message",
"fix"}; status is "ok", "warning" (works, but something is missing or worth a look), "error" (does not
work) or "skipped" (does not apply: no GitHub App connected, or an earlier check failed); fix is {"url", "label"}
(a GitHub page, or a panel path starting with "/") or None.

    repository     the server can read the repository (git ls-remote: through the App, a deploy key or anonymously)
    installation   the GitHub App is installed on the repository's owner, not suspended, and given the repository
    permissions    the installation granted Contents: read and Metadata: read (needed), Commit statuses: write (only
                   to show builds on commits) and subscribes to the push, create and delete events (new commits and
                   branches reach the panel at once instead of at the next poll)
    webhook        the App's webhook address is this panel's, and its latest deliveries reached it (GitHub keeps the
                   last deliveries of an App's webhook; GET /app/hook/deliveries)
    submodules     every submodule of the default branch can be read, and private ones that the App does not cover
                   have a deploy key

Nothing is changed, on GitHub or in the panel. Each GitHub call has its own short timeout, and a check that cannot
ask GitHub says so instead of failing the others.
"""

from __future__ import annotations

from typing import Any

from sudoo import exposure
from sudoo.repos import github, github_app

NEEDED = {"contents": "read", "metadata": "read"}
EVENTS = ("push", "create", "delete")
DELIVERIES = 50  # latest deliveries of the App's webhook looked at


def _entry(check_id: str, status: str, message: str, fix: dict[str, str] | None = None) -> dict[str, Any]:
    return {"id": check_id, "status": status, "message": message, "fix": fix}


def _address(repository: str) -> str:
    return f"https://github.com/{repository}"


def _repository(repository: str) -> dict[str, Any]:
    readable, problem = github._readable(_address(repository))
    if readable:
        via = github.access_via(repository.lower())
        how = {"deploy-key": "with its deploy key", "github-app": "through the GitHub App", "public": "as a public "
               "repository"}[via]  # fmt: skip
        return _entry("repository", "ok", f"The server can read {repository} {how}.")
    found = github.diagnose(_address(repository))
    fix = {"url": found["url"], "label": found["label"]} if found and found.get("url") else None
    return _entry("repository", "error", problem or f"The server cannot read {repository}.", fix)


def _installation(repository: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The installation check, and the installation as GitHub describes it now (None when there is none)."""
    owner = repository.split("/")[0]
    if not github_app.connected():
        if github.has_key(repository.lower()):
            return _entry("installation", "skipped", "No GitHub App is connected; the repository is read with its "
                          "deploy key.", {"url": "/github", "label": "Open GitHub access"}), None  # fmt: skip
        return _entry("installation", "warning", "No GitHub App is connected. Connect one so private repositories, "
                      "new branches and commits reach the panel.", {"url": "/github", "label": "Connect GitHub"}), None  # fmt: skip
    github_app.sync_if_stale()
    number = github_app.installation_for(owner)
    explained = github_app.explain(repository) if number is None else None
    if number is None:
        suspended = next((i for i in github_app.installations() if str(i["account"]).lower() == owner.lower()), None)
        message = explained["message"] if explained else f"The GitHub App is not installed on {owner}."
        fix = {"url": explained["url"], "label": explained["label"]} if explained and explained["url"] else None
        status = "error" if not github.has_key(repository.lower()) else "warning"
        return _entry("installation", status, message, fix), suspended
    try:
        found = github_app.request("GET", f"/app/installations/{number}", bearer=github_app.app_jwt(), timeout=10)
    except github_app.GitHubError as error:
        return _entry("installation", "warning", f"GitHub did not describe the installation on {owner}: {error}"), None
    if found.get("repository_selection") != "all":
        try:
            names = {str(r["full_name"]).lower() for r in github_app.installation_repositories(
                {"id": number, "account": owner})}  # fmt: skip
        except github_app.GitHubError:
            names = None
        if names is not None and repository.lower() not in names:
            return _entry("installation", "error", f"The GitHub App is installed on {owner}, but {repository} is not "
                          "among the repositories it may read.", {"url": str(found.get("html_url") or ""),
                                                                   "label": "Choose repositories"}), found  # fmt: skip
    selection = "all repositories" if found.get("repository_selection") == "all" else "selected repositories"
    return _entry("installation", "ok", f"The GitHub App is installed on {owner} ({selection}) and covers "
                  f"{repository}.", {"url": str(found.get("html_url") or ""), "label": "Open the installation"}), found  # fmt: skip


def _permissions(installation: dict[str, Any] | None) -> dict[str, Any]:
    if installation is None:
        return _entry("permissions", "skipped", "Checked once the GitHub App covers the repository.")
    granted = {str(k): str(v) for k, v in (installation.get("permissions") or {}).items()}
    events = set(installation.get("events") or [])
    fix = {"url": str(installation.get("html_url") or ""), "label": "Review permissions"}
    missing = [f"{name}: {level}" for name, level in NEEDED.items() if granted.get(name) not in (level, "write")]
    if missing:
        return _entry("permissions", "error", f"The installation lacks {', '.join(missing)}: the panel cannot read "
                      "the code.", fix)  # fmt: skip
    notes = []
    if granted.get("statuses") != "write":
        notes.append("Commit statuses: write is not granted, so builds are not shown on GitHub's commits")
    lacking = [e for e in EVENTS if e not in events]
    if lacking:
        notes.append(f"the events {', '.join(lacking)} are not subscribed, so changes arrive at the next poll only")
    if notes:
        return _entry("permissions", "warning", "Contents and Metadata can be read; " + "; ".join(notes) + ".", fix)
    return _entry("permissions", "ok", "Contents: read, Metadata: read and Commit statuses: write are granted; push, "
                  "create and delete events are subscribed.")  # fmt: skip


def _webhook(repository: str, installation: dict[str, Any] | None) -> dict[str, Any]:
    if not github_app.connected():
        return _entry("webhook", "skipped", "Without a GitHub App the panel polls GitHub for new commits (or uses "
                      "the repository's own webhook, when one was set up).")  # fmt: skip
    described = github_app.describe() or {}
    public = exposure.address(exposure.PANEL)
    expected = public.rstrip("/") + github_app.HOOK_PATH if public else None
    settings_fix = {"url": str(described.get("settings_url") or ""), "label": "Open the app's settings"}
    try:
        config = github_app.request("GET", "/app/hook/config", bearer=github_app.app_jwt(), timeout=10) or {}
    except github_app.GitHubError as error:
        return _entry("webhook", "warning", f"GitHub did not show the app's webhook: {error}", settings_fix)
    url = str(config.get("url") or "")
    if not url:
        return _entry("webhook", "error", "The GitHub App has no webhook address: new commits reach the panel only at "
                      "the next poll.", settings_fix)  # fmt: skip
    if expected and url.rstrip("/") != expected:
        return _entry("webhook", "error", f"The app's webhook goes to {url}, not to this panel ({expected}).",
                      settings_fix)  # fmt: skip
    try:
        deliveries = github_app.request("GET", f"/app/hook/deliveries?per_page={DELIVERIES}",
                                        bearer=github_app.app_jwt(), timeout=10) or []  # fmt: skip
    except github_app.GitHubError as error:
        return _entry("webhook", "warning", f"The webhook address is right; GitHub did not list its deliveries: "
                      f"{error}", settings_fix)  # fmt: skip
    repository_id = _repository_id(repository, installation)
    own = [d for d in deliveries if repository_id is not None and d.get("repository_id") == repository_id]
    chosen = own or deliveries
    if not chosen:
        return _entry("webhook", "ok", "The webhook goes to this panel. GitHub has not sent anything yet: push a "
                      "commit to see a delivery.")  # fmt: skip
    last = chosen[0]
    code = int(last.get("status_code") or 0)
    when = str(last.get("delivered_at") or "")
    which = "this repository's" if own else "the app's"
    failed = [d for d in chosen if not 200 <= int(d.get("status_code") or 0) < 300]
    if not 200 <= code < 300:
        return _entry("webhook", "error", f"The latest of {which} deliveries ({last.get('event')}, {when}) failed: "
                      f"{last.get('status') or code}.", {"url": f"{described.get('settings_url') or ''}/advanced",
                                                         "label": "See recent deliveries"})  # fmt: skip
    if failed:
        return _entry("webhook", "warning", f"The latest of {which} deliveries ({last.get('event')}, {when}) "
                      f"arrived; {len(failed)} of the last {len(chosen)} failed.", settings_fix)  # fmt: skip
    return _entry("webhook", "ok", f"The webhook goes to this panel and the latest of {which} deliveries "
                  f"({last.get('event')}, {when}) arrived.")  # fmt: skip


def _repository_id(repository: str, installation: dict[str, Any] | None) -> int | None:
    if installation is None:
        return None
    try:
        token = github_app.installation_token(int(installation["id"]))
        found = github_app.request("GET", f"/repos/{repository}", bearer=token, timeout=10) or {}
        return int(found["id"]) if found.get("id") is not None else None
    except (github_app.GitHubError, KeyError, ValueError):
        return None


def _submodules(repository: str, readable: bool) -> dict[str, Any]:
    if not readable:
        return _entry("submodules", "skipped", "Checked once the server can read the repository.")
    report = github.access_report(_address(repository))
    subs = [entry for entry in report if entry.get("role") == "submodule"]
    if not subs:
        return _entry("submodules", "ok", "The default branch has no submodules.")
    broken = [entry for entry in subs if not entry["ok"]]
    if broken:
        first = broken[0]
        fix = first.get("fix") or ({"url": "/github", "label": "Add a deploy key"} if first.get("repo") else None)
        names = ", ".join(str(e.get("repo") or e["url"]) for e in broken)
        return _entry("submodules", "error", f"{len(broken)} of {len(subs)} submodules cannot be read: {names}. "
                      "Give the GitHub App those repositories, or add a deploy key for each.", fix)  # fmt: skip
    keyed = sum(1 for e in subs if e.get("via") == "deploy-key")
    return _entry("submodules", "ok", f"All {len(subs)} submodules can be read" + (f" ({keyed} with a deploy key)."
                  if keyed else "."))  # fmt: skip


def check(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Every check for the project's repository, in order."""
    repository = str(project["repository"])
    first = _repository(repository)
    installation_check, installation = _installation(repository)
    results = [
        first,
        installation_check,
        _permissions(installation if installation_check["status"] == "ok" else None),
        _webhook(repository, installation),
        _submodules(repository, first["status"] == "ok"),
    ]
    return results


def overall(results: list[dict[str, Any]]) -> str:
    """ "error" when any check failed, else "warning" when any warns, else "ok"."""
    statuses = {r["status"] for r in results}
    return "error" if "error" in statuses else "warning" if "warning" in statuses else "ok"
