"""The repository guard's calls to GitHub, through the panel's GitHub App (repos/github_app.py).

Each call asks GitHub for an installation token with only what that call needs, and only among what the
installation granted (the code-reading token of github_app stays read-only):

    pull requests   Contents: write (a branch of the panel's own, "sudoo/repository-guard", and its commit),
                    Pull requests: write (opening, updating, closing the pull request), Workflows: write (GitHub
                    refuses any change under .github/workflows without it)
    branch rules    Administration: write (rulesets, and classic branch protection when rulesets are not available),
                    Members: read when teams are named (a team's id)
    results         Checks: read and Commit statuses (the CI results of a commit)

The panel never pushes to a branch it did not create: the files go to "sudoo/repository-guard", and only a pull
request brings them to the default branch, through the repository's own review rules. Tokens are never cached or
logged; GitHub's errors become GitHubError with a sentence for people.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

from sudoo.repo_guard import files
from sudoo.repos import github_app
from sudoo.repos.github_app import GitHubError

HEAD_BRANCH = "sudoo/repository-guard"

PERMISSIONS: list[dict[str, str]] = [
    {"key": "contents", "level": "write", "feature": "checks",
     "why": "Commit the check files to the panel's own branch (sudoo/repository-guard)."},
    {"key": "pull_requests", "level": "write", "feature": "checks",
     "why": "Open, update and close the pull request that adds or removes the check files."},
    {"key": "workflows", "level": "write", "feature": "checks",
     "why": "GitHub refuses any change under .github/workflows without it."},
    {"key": "checks", "level": "read", "feature": "results",
     "why": "Read the results of the checks, to show them on builds and before a promotion."},
    {"key": "administration", "level": "write", "feature": "rules",
     "why": "Create, change and remove the panel's rulesets (or classic branch protection)."},
]  # fmt: skip
"""What the repository guard needs on top of what the panel's GitHub App already has, and why."""

FEATURE_PERMISSIONS = {
    "checks": {"contents": "write", "pull_requests": "write", "workflows": "write"},
    "results": {"checks": "read"},
    "rules": {"administration": "write"},
}
LEVEL_RANK = {None: 0, "": 0, "read": 1, "write": 2, "admin": 3}
PLAN_WORDS = ("upgrade to github pro", "make this repository public", "not available", "github team", "plan")


def _parts(full_name: str) -> tuple[str, str]:
    owner, _, name = full_name.partition("/")
    if not github_app.ORG_RE.match(owner) or not github_app.REPOSITORY_NAME_RE.match(name):
        raise GitHubError("Not a repository GitHub knows.", 400)
    return owner, name


def installation(owner: str) -> dict[str, Any] | None:
    return next((i for i in github_app.installations() if str(i["account"]).lower() == owner.lower()), None)


def granted(owner: str) -> dict[str, str]:
    found = installation(owner)
    return {str(k): str(v) for k, v in ((found or {}).get("permissions") or {}).items()}


def missing(owner: str, feature: str) -> list[str]:
    """The permissions `feature` ("checks", "results", "rules") needs that the installation on `owner` lacks."""
    have = granted(owner)
    return [
        key
        for key, level in FEATURE_PERMISSIONS[feature].items()
        if LEVEL_RANK.get(have.get(key), 0) < LEVEL_RANK[level]
    ]


def token(owner: str, wanted: dict[str, str], repository: str) -> str:
    """An installation token with exactly `wanted` (and Metadata: read), limited to the one repository `repository`
    ("owner/name"): a leaked token, or a bug, cannot touch the installation's other repositories. Raises GitHubError
    when the app is not installed on `owner` or the installation lacks one of them."""
    name = repository.partition("/")[2]
    if not name:
        raise GitHubError(f'"{repository}" is not a repository name of the form owner/name.', 400)
    found = installation(owner)
    if found is None or found.get("suspended"):
        raise GitHubError(f"The GitHub App is not installed on {owner} (or its installation is suspended).", 409)
    have = {str(k): str(v) for k, v in (found.get("permissions") or {}).items()}
    lacking = [k for k, level in wanted.items() if LEVEL_RANK.get(have.get(k), 0) < LEVEL_RANK[level]]
    if lacking:
        raise GitHubError(
            "The GitHub App lacks "
            + ", ".join(f"{k.replace('_', ' ').capitalize()}: {wanted[k]}" for k in lacking)
            + " on this repository. Add it in the app's permissions on GitHub, then accept it on the installation.",
            403,
        )
    answer = github_app.request(
        "POST", f"/app/installations/{int(found['id'])}/access_tokens", bearer=github_app.app_jwt(),
        body={"permissions": {**wanted, "metadata": "read"}, "repositories": [name]},
    )  # fmt: skip
    return str(answer["token"])


def plan_limited(error: GitHubError) -> bool:
    """Whether GitHub refused because of the repository's plan (rulesets and branch protection on private
    repositories need GitHub Pro, Team or Enterprise)."""
    text = str(error).lower()
    return error.status in (403, 404) and any(word in text for word in PLAN_WORDS)


# ---------------------------------------------------------------- the repository and its files


def repository(full_name: str, bearer: str) -> dict[str, Any]:
    owner, name = _parts(full_name)
    found = github_app.request("GET", f"/repos/{owner}/{name}", bearer=bearer) or {}
    return {
        "full_name": str(found.get("full_name") or full_name),
        "private": bool(found.get("private")),
        "visibility": str(found.get("visibility") or ("private" if found.get("private") else "public")),
        "default_branch": str(found.get("default_branch") or "main"),
        "owner_type": str((found.get("owner") or {}).get("type") or "User"),
    }


def read_file(full_name: str, path: str, ref: str, bearer: str) -> str | None:
    """A file's text on `ref`, or None when it does not exist."""
    owner, name = _parts(full_name)
    try:
        found = github_app.request(
            "GET", f"/repos/{owner}/{name}/contents/{quote(path)}?ref={quote(ref, safe='')}", bearer=bearer
        )
    except GitHubError as error:
        if error.status == 404:
            return None
        raise
    if not isinstance(found, dict) or found.get("type") != "file":
        return None
    return base64.b64decode(str(found.get("content") or "")).decode("utf-8", errors="replace")


def branch_head(full_name: str, branch: str, bearer: str) -> str | None:
    owner, name = _parts(full_name)
    try:
        found = github_app.request("GET", f"/repos/{owner}/{name}/git/ref/heads/{quote(branch, safe='/')}",
                                   bearer=bearer)  # fmt: skip
    except GitHubError as error:
        if error.status == 404:
            return None
        raise
    return str((found.get("object") or {}).get("sha") or "") or None


def commit_files(full_name: str, base: str, changes: dict[str, str | None], message: str, bearer: str) -> str:
    """One commit on top of `base`'s head with `changes` ({path: content, or None to remove it}), put on the panel's
    branch HEAD_BRANCH (created, or moved to it: only the panel writes that branch). Returns the commit's sha."""
    owner, name = _parts(full_name)
    root = f"/repos/{owner}/{name}"
    base_sha = branch_head(full_name, base, bearer)
    if base_sha is None:
        raise GitHubError(f"{full_name} has no branch {base}.", 409)
    base_commit = github_app.request("GET", f"{root}/git/commits/{base_sha}", bearer=bearer)
    tree = [
        (
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            if content is not None
            else {"path": path, "mode": "100644", "type": "blob", "sha": None}
        )
        for path, content in sorted(changes.items())
    ]
    new_tree = github_app.request("POST", f"{root}/git/trees", bearer=bearer,
                                  body={"base_tree": base_commit["tree"]["sha"], "tree": tree})  # fmt: skip
    commit = github_app.request("POST", f"{root}/git/commits", bearer=bearer,
                                body={"message": message, "tree": new_tree["sha"], "parents": [base_sha]})  # fmt: skip
    sha = str(commit["sha"])
    if branch_head(full_name, HEAD_BRANCH, bearer) is None:
        github_app.request("POST", f"{root}/git/refs", bearer=bearer,
                           body={"ref": f"refs/heads/{HEAD_BRANCH}", "sha": sha})  # fmt: skip
    else:  # the panel's own branch: always replaced by the newest proposal
        github_app.request("PATCH", f"{root}/git/refs/heads/{HEAD_BRANCH}", bearer=bearer,
                           body={"sha": sha, "force": True})  # fmt: skip
    return sha


def delete_head_branch(full_name: str, bearer: str) -> None:
    owner, name = _parts(full_name)
    try:
        github_app.request("DELETE", f"/repos/{owner}/{name}/git/refs/heads/{HEAD_BRANCH}", bearer=bearer)
    except GitHubError as error:
        if error.status not in (404, 422):
            raise


def _pull(found: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": int(found["number"]),
        "url": str(found.get("html_url") or ""),
        "state": "merged" if found.get("merged_at") or found.get("merged") else str(found.get("state") or "open"),
        "title": str(found.get("title") or ""),
    }


def open_pull(full_name: str, bearer: str) -> dict[str, Any] | None:
    """The panel's open pull request (from HEAD_BRANCH), or None."""
    owner, name = _parts(full_name)
    found = github_app.request(
        "GET", f"/repos/{owner}/{name}/pulls?state=open&head={quote(owner)}:{quote(HEAD_BRANCH, safe='')}",
        bearer=bearer,
    )  # fmt: skip
    return _pull(found[0]) if found else None


def propose(full_name: str, base: str, title: str, body: str, bearer: str) -> dict[str, Any]:
    """Open the pull request from HEAD_BRANCH into `base`, or update the one already open."""
    owner, name = _parts(full_name)
    current = open_pull(full_name, bearer)
    if current is not None:
        found = github_app.request("PATCH", f"/repos/{owner}/{name}/pulls/{current['number']}", bearer=bearer,
                                   body={"title": title, "body": body})  # fmt: skip
        return {**_pull(found), "updated": True}
    found = github_app.request("POST", f"/repos/{owner}/{name}/pulls", bearer=bearer,
                               body={"title": title, "body": body, "head": HEAD_BRANCH, "base": base})  # fmt: skip
    return {**_pull(found), "updated": False}


def close_pull(full_name: str, number: int, bearer: str) -> dict[str, Any]:
    owner, name = _parts(full_name)
    found = github_app.request("PATCH", f"/repos/{owner}/{name}/pulls/{int(number)}", bearer=bearer,
                               body={"state": "closed"})  # fmt: skip
    return _pull(found)


def pull(full_name: str, number: int, bearer: str) -> dict[str, Any]:
    owner, name = _parts(full_name)
    return _pull(github_app.request("GET", f"/repos/{owner}/{name}/pulls/{int(number)}", bearer=bearer))


# ---------------------------------------------------------------- rulesets and branch protection


def rulesets(full_name: str, bearer: str) -> list[dict[str, Any]]:
    """The repository's own rulesets (id, name, enforcement); raises GitHubError (plan_limited tells why)."""
    owner, name = _parts(full_name)
    found = github_app.request("GET", f"/repos/{owner}/{name}/rulesets?includes_parents=false&per_page=100",
                               bearer=bearer)  # fmt: skip
    return [r for r in found or [] if isinstance(r, dict)]


def ruleset(full_name: str, ruleset_id: int, bearer: str) -> dict[str, Any] | None:
    owner, name = _parts(full_name)
    try:
        found = github_app.request("GET", f"/repos/{owner}/{name}/rulesets/{int(ruleset_id)}", bearer=bearer)
    except GitHubError as error:
        if error.status == 404:
            return None
        raise
    return found if isinstance(found, dict) else None


def save_ruleset(full_name: str, body: dict[str, Any], bearer: str, ruleset_id: int | None = None) -> int:
    owner, name = _parts(full_name)
    if ruleset_id:
        found = github_app.request("PUT", f"/repos/{owner}/{name}/rulesets/{int(ruleset_id)}", bearer=bearer,
                                   body=body)  # fmt: skip
    else:
        found = github_app.request("POST", f"/repos/{owner}/{name}/rulesets", bearer=bearer, body=body)
    return int(found["id"])


def delete_ruleset(full_name: str, ruleset_id: int, bearer: str) -> None:
    owner, name = _parts(full_name)
    try:
        github_app.request("DELETE", f"/repos/{owner}/{name}/rulesets/{int(ruleset_id)}", bearer=bearer)
    except GitHubError as error:
        if error.status != 404:
            raise


def protection(full_name: str, branch: str, bearer: str) -> dict[str, Any] | None:
    """A branch's classic protection, or None when it has none. Raises GitHubError (plan_limited tells why)."""
    owner, name = _parts(full_name)
    try:
        found = github_app.request("GET", f"/repos/{owner}/{name}/branches/{quote(branch, safe='')}/protection",
                                   bearer=bearer)  # fmt: skip
    except GitHubError as error:
        if error.status == 404 and not plan_limited(error):
            return None
        raise
    return found if isinstance(found, dict) else None


def save_protection(full_name: str, branch: str, body: dict[str, Any], signed: bool, bearer: str) -> None:
    owner, name = _parts(full_name)
    root = f"/repos/{owner}/{name}/branches/{quote(branch, safe='')}/protection"
    github_app.request("PUT", root, bearer=bearer, body=body)
    github_app.request("POST" if signed else "DELETE", f"{root}/required_signatures", bearer=bearer)


def delete_protection(full_name: str, branch: str, bearer: str) -> None:
    owner, name = _parts(full_name)
    try:
        github_app.request("DELETE", f"/repos/{owner}/{name}/branches/{quote(branch, safe='')}/protection",
                           bearer=bearer)  # fmt: skip
    except GitHubError as error:
        if error.status != 404:
            raise


def team_id(organization: str, slug: str, bearer: str) -> int:
    if not github_app.ORG_RE.match(organization):
        raise GitHubError("Not an organisation GitHub knows.", 400)
    try:
        found = github_app.request("GET", f"/orgs/{organization}/teams/{quote(slug, safe='')}", bearer=bearer)
    except GitHubError as error:
        if error.status == 404:
            raise GitHubError(f"{organization} has no team {slug}.", 400) from None
        raise
    return int(found["id"])


# ---------------------------------------------------------------- CI results


def commit_results(full_name: str, sha: str, bearer: str) -> dict[str, Any]:
    """The check runs and commit statuses of `sha`: {"state": "success" | "failure" | "pending" | "none", "total",
    "failed", "pending", "runs": [{"name", "state", "url"}], "url"} (url: the commit's checks on GitHub)."""
    owner, name = _parts(full_name)
    if not github_app.SHA_RE.match(sha):
        raise GitHubError("Not a commit GitHub knows.", 400)
    runs: list[dict[str, Any]] = []
    found = github_app.request("GET", f"/repos/{owner}/{name}/commits/{sha}/check-runs?per_page=100", bearer=bearer)
    for run in (found or {}).get("check_runs", []):
        source = (run.get("app") or {}).get("id")
        if run.get("name") == files.REQUIRED_CHECK and source != files.ACTIONS_APP_ID:
            continue  # the required check counts only when GitHub Actions made it: anyone with write access can post one
        conclusion = run.get("conclusion")
        state = (
            "pending" if run.get("status") != "completed"
            else "success" if conclusion in ("success", "neutral", "skipped")
            else "failure"
        )  # fmt: skip
        runs.append({"name": str(run.get("name") or ""), "state": state, "url": str(run.get("html_url") or "")})
    try:
        combined = github_app.request("GET", f"/repos/{owner}/{name}/commits/{sha}/status", bearer=bearer) or {}
    except GitHubError:
        combined = {}
    for status in combined.get("statuses", []):
        context = str(status.get("context") or "")
        if context.startswith("sudoo/"):
            continue  # the panel's own build statuses are shown by the build itself
        if context == files.REQUIRED_CHECK:
            continue  # a commit status never comes from GitHub Actions: it cannot stand in for the required check
        state = {"success": "success", "pending": "pending"}.get(str(status.get("state")), "failure")
        runs.append({"name": context, "state": state, "url": str(status.get("target_url") or "")})
    failed = sum(r["state"] == "failure" for r in runs)
    pending = sum(r["state"] == "pending" for r in runs)
    state = "none" if not runs else "failure" if failed else "pending" if pending else "success"
    return {
        "state": state,
        "total": len(runs),
        "failed": failed,
        "pending": pending,
        "runs": sorted(runs, key=lambda r: (r["state"] != "failure", r["name"])),
        "url": f"{github_app.WEB}/{owner}/{name}/commit/{sha}/checks",
    }
