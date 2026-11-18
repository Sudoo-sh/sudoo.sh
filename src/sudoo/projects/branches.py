"""The branches of a project: what GitHub has, with each branch's stage, instances and latest commit.

Branches come from GitHub (`git ls-remote`, through the GitHub App, a deploy key or anonymously: repos/github.heads),
read at most every BRANCHES_SECONDS per project unless asked again. Branches GitHub no longer has but an instance still
follows (or a stage was recorded for) are listed too, marked as not on GitHub. The latest commit of a branch comes from
the clone of one of its instances when it has one at that commit, else from the GitHub App (when it can read the
repository), remembered per commit.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

from sudoo import projects
from sudoo.instances import store
from sudoo.repos import github, github_app
from sudoo.repos.gitrepo import commits

BRANCHES_SECONDS = 30
COMMIT_LOOKUPS = 40  # commits asked from GitHub for one listing at most (the rest are shown without details)

_lock = threading.Lock()
_heads: dict[str, tuple[float, dict[str, str], str | None, str | None]] = (
    {}
)  # project id -> (when, heads, default, error)
_commits: dict[tuple[str, str], dict[str, str]] = {}  # (repository id, sha) -> commit


def address(project: Mapping[str, Any]) -> str:
    return f"https://github.com/{project['repository']}"


def heads(project: Mapping[str, Any], refresh: bool = False) -> tuple[dict[str, str], str | None, str | None]:
    """({branch: sha}, default branch, error) of the project's repository."""
    with _lock:
        cached = _heads.get(project["id"])
    if cached and not refresh and time.time() - cached[0] < BRANCHES_SECONDS:
        return cached[1], cached[2], cached[3]
    found, default, error = github.heads(address(project))
    with _lock:
        _heads[project["id"]] = (time.time(), found, default, error)
    return found, default, error


def forget(project_id: str) -> None:
    """Read the project's branches from GitHub again next time (after a push, a new or a deleted branch)."""
    with _lock:
        _heads.pop(project_id, None)


def _local_commit(names: list[str], sha: str | None) -> dict[str, str] | None:
    for name in names:
        if not store.exists(name):
            continue
        found = commits(store.addons_dir(name), 1)
        if found and (sha is None or found[0]["full_sha"] == sha):
            return found[0]
    return None


def _github_commit(project: Mapping[str, Any], sha: str) -> dict[str, str] | None:
    key = (str(project["repository_id"]), sha)
    if key in _commits:
        return _commits[key]
    token = github_app.token_for(str(project["repository"]).split("/")[0]) if github_app.connected() else None
    if token is None:
        return None
    try:
        answer = github_app.request("GET", f"/repos/{project['repository']}/commits/{sha}", bearer=token)
    except github_app.GitHubError:
        return None
    details = answer.get("commit") or {}
    author = details.get("author") or {}
    found = {
        "sha": sha[:7],
        "full_sha": sha,
        "subject": str(details.get("message") or "").splitlines()[0][:200] if details.get("message") else "",
        "when": str(author.get("date") or ""),
        "author": str(author.get("name") or ""),
    }
    _commits[key] = found
    if len(_commits) > 5000:
        _commits.pop(next(iter(_commits)))
    return found


def listing(project: Mapping[str, Any], refresh: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The project's branches and facts about the listing: ([{"name", "stage", "default", "on_github", "commit",
    "instances", "auto_created"}], {"default", "error", "read_at"}). Sorted by stage (production first), then name."""
    found, default, error = heads(project, refresh)
    grouped = projects.by_branch(project["id"])
    recorded = project.get("branches") or {}
    names = set(found) | {b for b in grouped if b} | {b for b, v in recorded.items() if v.get("stage")}
    lookups = 0
    result: list[dict[str, Any]] = []
    for name in names:
        members = grouped.get(name, [])
        sha = found.get(name)
        commit = _local_commit(members, sha)
        if commit is None and sha and lookups < COMMIT_LOOKUPS:
            lookups += 1
            commit = _github_commit(project, sha)
        if commit is None and sha:
            commit = {"sha": sha[:7], "full_sha": sha, "subject": "", "when": "", "author": ""}
        result.append(
            {
                "name": name,
                "stage": projects.branch_stage(project, name, members),
                "default": name == default,
                "on_github": None if error else name in found,
                "commit": commit,
                "instances": members,
                "auto_created": recorded.get(name, {}).get("auto_created"),
            }
        )
    result.sort(key=lambda b: (-projects.STAGE_RANK[b["stage"]], not b["default"], b["name"].lower()))
    return result, {"default": default, "error": error, "read_at": time.time()}
