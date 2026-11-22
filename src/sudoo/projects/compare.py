"""Comparing two branches of a project: what merging `head` into `base` brings (commits, files, Odoo modules).

The answer comes from the panel's own copy of the repository (repos/mirror.py), so it works with the GitHub App, a
deploy key or a public repository alike. When git cannot read it and the GitHub App covers the repository, GitHub's
compare API answers instead (commits ahead and behind, changed files; the modules are then found from the changed
paths, without their versions).

Modules are compared through their __manifest__.py at both commits: added, removed, and changed version ("bump":
"up", "down" or "same" when files changed but the version did not). A module whose version goes up runs its
migration scripts on the next upgrade: those found at head in <module>/migrations/<new version>/ are listed.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from sudoo.instances import backups
from sudoo.projects import branches
from sudoo.repos import github_app, mirror

CACHE_SECONDS = 60
_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


class CompareError(Exception):
    """The branches cannot be compared; the message is meant for people."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def github_url(project: Mapping[str, Any], base: str, head: str) -> str:
    return f"https://github.com/{project['repository']}/compare/{quote(base, safe='')}...{quote(head, safe='')}"


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)[:8])


def bump(before: str, after: str) -> str:
    """ "up", "down" or "same": how a module's version changed (compared number by number)."""
    old, new = _version_key(before), _version_key(after)
    return "up" if new > old else "down" if new < old else "same"


def module_of(path: str, folders: Mapping[str, str]) -> str | None:
    """The module a changed file belongs to, given {module folder: module}."""
    parts = path.split("/")
    for end in range(len(parts) - 1, 0, -1):
        found = folders.get("/".join(parts[:end]))
        if found:
            return found
    return None


def _modules(
    path: Any, base_sha: str, head_sha: str, changed: list[str], series: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    head_files = mirror.files_at(path, head_sha)
    before = mirror.manifests(path, base_sha)
    after = mirror.manifests(path, head_sha, head_files)
    folders = {info["path"]: name for name, info in {**before, **after}.items()}
    touched = {module for file in changed if (module := module_of(file, folders))}
    changes: list[dict[str, Any]] = []
    for name in sorted(touched & set(before) & set(after)):
        old, new = before[name]["version"], after[name]["version"]
        changes.append({"name": name, "from": old, "to": new, "bump": bump(old, new) if old != new else "same"})
    pending: list[dict[str, Any]] = []
    for change in changes:
        if change["bump"] != "up":
            continue
        scripts = mirror.migrations(head_files, after[change["name"]]["path"], change["to"], series)
        if scripts:
            pending.append({"module": change["name"], "version": change["to"], "scripts": scripts})
    modules = {
        "added": [
            {"name": n, "version": after[n]["version"]} for n in sorted(set(after) - set(before)) if n in touched
        ],
        "removed": [
            {"name": n, "version": before[n]["version"]} for n in sorted(set(before) - set(after)) if n in touched
        ],
        "changed": changes,
        "known": True,
    }
    return modules, pending


def _from_github(project: Mapping[str, Any], base_sha: str, head_sha: str) -> dict[str, Any]:
    owner = str(project["repository"]).split("/")[0]
    token = github_app.token_for(owner) if github_app.connected() else None
    if token is None:
        raise CompareError("The panel could not read the repository to compare its branches.", 502)
    try:
        answer = github_app.request("GET", f"/repos/{project['repository']}/compare/{base_sha}...{head_sha}",
                                    bearer=token)  # fmt: skip
    except github_app.GitHubError as error:
        raise CompareError(str(error), 502) from None
    files = [str(f.get("filename") or "") for f in answer.get("files") or []]
    commits = []
    for item in reversed((answer.get("commits") or [])[-mirror.LOG_LIMIT :]):
        details = item.get("commit") or {}
        author = details.get("author") or {}
        sha = str(item.get("sha") or "")
        commits.append({"sha": sha[:7], "full_sha": sha, "subject": str(details.get("message") or "").split("\n")[0][:200],
                        "author": str(author.get("name") or ""), "when": str(author.get("date") or "")})  # fmt: skip
    touched = sorted({file.split("/")[-2] for file in files if file.endswith("__manifest__.py") and "/" in file})
    return {
        "ahead": int(answer.get("ahead_by") or 0),
        "behind": int(answer.get("behind_by") or 0),
        "commits": commits,
        "files_changed": len(files),
        "modules": {"added": [], "removed": [], "changed": [{"name": n, "from": "", "to": "", "bump": "unknown"}
                                                              for n in touched], "known": False},  # fmt: skip
        "migrations": [],
        "source": "github",
    }


def compare(project: Mapping[str, Any], base: str, head: str, refresh: bool = False) -> dict[str, Any]:
    """What merging branch `head` into branch `base` brings. Raises CompareError."""
    found, _, error = branches.heads(project, refresh)
    if error:
        raise CompareError(error, 502)
    for name in (base, head):
        if name not in found:
            raise CompareError(f"{project['repository']} has no branch {name} on GitHub.", 404)
    base_sha, head_sha = found[base], found[head]
    key = (str(project["id"]), base_sha, head_sha)
    with _lock:
        cached = _cache.get(key)
    if cached and not refresh and time.time() - cached[0] < CACHE_SECONDS:
        return dict(cached[1])
    series = backups.series(str(project.get("odoo_version") or "")) or ""
    try:
        path = mirror.refresh(f"https://github.com/{project['repository']}", force=refresh)
        ahead, behind = mirror.counts(path, base_sha, head_sha)
        changed = mirror.changed_files(path, base_sha, head_sha)
        modules, pending = _modules(path, base_sha, head_sha, changed, series)
        result: dict[str, Any] = {
            "ahead": ahead,
            "behind": behind,
            "commits": mirror.log(path, base_sha, head_sha),
            "files_changed": len(changed),
            "modules": modules,
            "migrations": pending,
            "source": "git",
        }
    except mirror.GitError as problem:
        try:
            result = _from_github(project, base_sha, head_sha)
        except CompareError:
            raise CompareError(str(problem), 502) from None
    result.update(
        base={"branch": base, "sha": base_sha},
        head={"branch": head, "sha": head_sha},
        github_url=github_url(project, base, head),
        odoo_series=series,
    )
    with _lock:
        _cache[key] = (time.time(), result)
        if len(_cache) > 200:
            _cache.pop(next(iter(_cache)))
    return dict(result)
