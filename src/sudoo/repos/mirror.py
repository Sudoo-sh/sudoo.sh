"""A copy of each project's repository kept by the panel itself, to compare branches without touching any instance.

One bare clone per repository in <ODOO_ROOT>/manager/mirrors/<owner>__<repo>.git, made without file contents
(`--filter=blob:none`): git downloads only the history and the folder listings, and fetches the contents of the few
files the panel reads (the modules' __manifest__.py) when they are read. It is fetched again at most every
FETCH_SECONDS. git reaches GitHub the usual way (github.configure_git: the GitHub App's token, a deploy key or
anonymously), so a repository the panel can build is a repository it can compare. Nothing in it is ever checked out
or run: the manifests are read as Python literals (ast.literal_eval), never executed.

    refresh(address)               the clone, made or fetched; GitError when git cannot read the repository
    counts(path, base, head)       (ahead, behind): commits of head not in base, and of base not in head
    log(path, base, head)          the commits of head not in base, newest first
    changed_files(path, base, head)    the files that differ between the two (paths)
    manifests(path, sha)           {module: {"path", "version", "name"}} of every module at that commit
    migrations(path, sha, folder, version)  the migration scripts of a module for one version
"""

from __future__ import annotations

import ast
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from sudoo.config import settings
from sudoo.repos.gitrepo import git

FETCH_SECONDS = 30
LOG_LIMIT = 50
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MANIFEST_MAX_BYTES = 200_000
_SKIP = ("/setup/", "/node_modules/", "/__pycache__/")

_locks: dict[str, threading.Lock] = {}
_fetched: dict[str, float] = {}
_guard = threading.Lock()


class GitError(Exception):
    """git could not read the repository or a commit; the message is meant for people."""


def folder(address: str) -> Path:
    """Where the copy of the repository at `address` lives."""
    from sudoo.repos import github

    name = github.repo_id(address) or re.sub(r"[^a-z0-9_.-]+", "_", address.lower()).strip("_")[-80:]
    return settings.manager_dir / "mirrors" / (name.replace("/", "__") + ".git")


def _run(args: list[str], timeout: float = 120) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GitError("GitHub did not answer in time.") from None
    except OSError as error:
        raise GitError(f"git could not run ({error.__class__.__name__}).") from None
    if result.returncode != 0:
        from sudoo.core.hints import friendly_hint

        raise GitError(friendly_hint(result.stderr) or "git could not read the repository.")
    return result.stdout


def refresh(address: str, force: bool = False) -> Path:
    """The copy of the repository, made the first time and fetched again when it is older than FETCH_SECONDS."""
    path = folder(address)
    key = str(path)
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        if not force and path.exists() and time.time() - _fetched.get(key, 0) < FETCH_SECONDS:
            return path
        if not (path / "HEAD").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            _run(["git", "clone", "-q", "--bare", "--filter=blob:none", "--", address, str(path)], timeout=600)
        else:
            _run(git(path, "fetch", "-q", "--prune", "origin", "+refs/heads/*:refs/heads/*"), timeout=600)
        _fetched[key] = time.time()
    return path


def _sha(value: str) -> str:
    if not SHA_RE.match(value or ""):
        raise GitError("Not a commit.")
    return value


def counts(path: Path, base: str, head: str) -> tuple[int, int]:
    """(ahead, behind): commits in `head` that `base` lacks, and in `base` that `head` lacks."""
    output = _run(git(path, "rev-list", "--left-right", "--count", f"{_sha(base)}...{_sha(head)}"), timeout=60)
    behind, ahead = (int(part) for part in output.split()[:2])
    return ahead, behind


def log(path: Path, base: str, head: str, limit: int = LOG_LIMIT) -> list[dict[str, str]]:
    """The commits of `head` that `base` lacks, newest first: [{"sha", "full_sha", "subject", "author", "when"}]."""
    output = _run(
        git(path, "log", f"-{limit}", "--format=%h%x1f%H%x1f%cI%x1f%an%x1f%s", f"{_sha(base)}..{_sha(head)}", "--"),
        timeout=60,
    )
    from sudoo.repos.gitrepo import _utc

    found: list[dict[str, str]] = []
    for line in output.splitlines():
        sha, full, when, author, subject = (line.split("\x1f", 4) + ["", "", "", ""])[:5]
        found.append({"sha": sha, "full_sha": full, "subject": subject[:200], "author": author, "when": _utc(when)})
    return found


def changed_files(path: Path, base: str, head: str) -> list[str]:
    """The paths that differ between `base` and `head` (what merging head into base changes: from their common
    ancestor)."""
    output = _run(git(path, "diff", "--name-only", "--no-renames", f"{_sha(base)}...{_sha(head)}", "--"), timeout=60)
    return [line for line in output.splitlines() if line]


def _files(path: Path, sha: str) -> list[str]:
    return _run(git(path, "ls-tree", "-r", "--name-only", _sha(sha)), timeout=60).splitlines()


def read_manifest(text: str) -> dict[str, Any]:
    """A module's __manifest__.py as data (a Python dict literal); {} when it is not one."""
    try:
        found = ast.literal_eval(text.strip())
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return {}
    return found if isinstance(found, dict) else {}


def manifests(path: Path, sha: str, files: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """{module: {"path" (its folder), "version", "name"}} of every module of the repository at commit `sha` (submodules
    are other repositories: not included)."""
    found: dict[str, dict[str, Any]] = {}
    for file in files if files is not None else _files(path, sha):
        if not file.endswith("/__manifest__.py") or any(skip in f"/{file}" for skip in _SKIP):
            continue
        module_path = file[: -len("/__manifest__.py")]
        module = module_path.rsplit("/", 1)[-1]
        if module in found:
            continue
        try:
            text = _run(git(path, "show", f"{sha}:{file}"), timeout=60)
        except GitError:
            continue
        data = read_manifest(text[:MANIFEST_MAX_BYTES])
        found[module] = {
            "path": module_path,
            "version": str(data.get("version") or ""),
            "name": str(data.get("name") or module)[:120],
        }
    return found


def migrations(files: list[str], module_path: str, version: str, odoo_series: str = "") -> list[str]:
    """The migration scripts of a module for `version` among `files` (the commit's paths): Odoo runs those in
    <module>/migrations/<version>/ (or upgrades/), the version written in full (18.0.1.2.0) or without the Odoo series
    (1.2.0)."""
    short = version[len(odoo_series) + 1 :] if odoo_series and version.startswith(odoo_series + ".") else version
    wanted = {version, short}
    found: list[str] = []
    for file in files:
        for folder_name in ("migrations", "upgrades"):
            prefix = f"{module_path}/{folder_name}/"
            if not file.startswith(prefix):
                continue
            parts = file[len(prefix) :].split("/")
            if len(parts) == 2 and parts[0] in wanted and parts[1].endswith(".py"):
                found.append(file)
    return sorted(found)


def files_at(path: Path, sha: str) -> list[str]:
    """Every file path of the repository at commit `sha`."""
    return _files(path, sha)
