"""The Odoo modules of a repository's branch, read without creating anything (GET /api/v1/repositories/modules).

It reads the panel's own copy of the repository (repos/mirror.py: a bare clone without file contents, reached the
usual way: the GitHub App's token, a deploy key or anonymously), lists the folders with a __manifest__.py at the
branch's commit and fetches only those manifests, in one go. Submodules are read the same way, from their own copy, at
the commit the branch pins (up to SUBMODULE_DEPTH levels, MAX_SUBMODULES in all). The manifests are read as Python
literals (mirror.read_manifest), never executed.

A module is listed as the instance would find it (gitrepo.module_folders): at most four folders deep, not inside
another module, nor in hidden, setup or node_modules folders; one that says "installable": False is left out.

    of_branch(address, branch)  {"repository", "branch", "commit", "modules", "problems"}; raises mirror.GitError
                                (BranchMissing when the branch is not on GitHub)
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

from sudoo.repos import github, mirror
from sudoo.repos.gitrepo import git

SUBMODULE_DEPTH = 2
MAX_SUBMODULES = 20
MAX_DEPTH = 4  # folders above a module, as gitrepo.module_folders looks for them
SKIPPED = {"__pycache__", "node_modules", "setup"}
CACHE_SIZE = 100

_cache: dict[tuple[str, str], dict[str, Any]] = {}
_lock = threading.Lock()


class BranchMissing(mirror.GitError):
    """The repository has no such branch."""


def https_address(address: str) -> str:
    """https://github.com/<owner>/<repo> for any GitHub address (the form the mirror and the GitHub App use)."""
    name = github.repo_name(address)
    if not name:
        raise mirror.GitError(f"{address[:120]} is not a GitHub repository.")
    return f"https://github.com/{name}"


def _git(path: Path, *args: str, stdin: str | None = None, timeout: float = 120) -> str:
    try:
        result = subprocess.run(git(path, *args), input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise mirror.GitError("GitHub did not answer in time.") from None
    if result.returncode != 0:
        from sudoo.core.hints import friendly_hint

        raise mirror.GitError(friendly_hint(result.stderr) or "git could not read the repository.")
    return result.stdout


def _tree(path: Path, sha: str) -> list[tuple[str, str, str]]:
    """(type, object id, path) of every entry of the commit: "blob" files, "commit" submodules."""
    entries: list[tuple[str, str, str]] = []
    for item in _git(path, "ls-tree", "-r", "-z", "--full-tree", sha).split("\0"):
        if "\t" not in item:
            continue
        info, name = item.split("\t", 1)
        parts = info.split()
        if len(parts) == 3:
            entries.append((parts[1], parts[2], name))
    return entries


def _prefetch(path: Path, oids: list[str]) -> None:
    """Download the given files of a copy made without file contents, in one request (git would otherwise fetch them
    one by one when read)."""
    if not oids or _git(path, "config", "--default", "", "remote.origin.promisor").strip() != "true":
        return
    _git(
        path, "-c", "fetch.negotiationAlgorithm=noop", "fetch", "-q", "origin", "--no-tags", "--no-write-fetch-head",
        "--recurse-submodules=no", "--filter=blob:none", "--stdin", stdin="\n".join(oids) + "\n", timeout=600,
    )  # fmt: skip


def _contents(path: Path, oids: list[str]) -> dict[str, str]:
    """{object id: text} of the given files (one `git cat-file --batch`)."""
    if not oids:
        return {}
    try:
        result = subprocess.run(
            git(path, "cat-file", "--batch"), input=("\n".join(oids) + "\n").encode(), capture_output=True, timeout=300
        )
    except subprocess.TimeoutExpired:
        raise mirror.GitError("Reading the repository's manifests took too long.") from None
    found: dict[str, str] = {}
    data, at = result.stdout, 0
    while at < len(data):
        end = data.index(b"\n", at)
        header = data[at:end].decode(errors="replace").split()
        at = end + 1
        if len(header) == 3 and header[2].isdigit():
            size = int(header[2])
            found[header[0]] = data[at : at + size][: mirror.MANIFEST_MAX_BYTES].decode(errors="replace")
            at += size + 1
    return found


def _module_path_ok(module_path: str, modules: set[str]) -> bool:
    parts = module_path.split("/")
    if len(parts) - 1 > MAX_DEPTH:
        return False
    if any(part.startswith(".") or part in SKIPPED for part in parts[:-1]):
        return False
    return not any("/".join(parts[:end]) in modules for end in range(1, len(parts)))  # not inside another module


def _submodules(path: Path, gitmodules: str) -> dict[str, str]:
    """{path: address} of the submodules the .gitmodules file (its object id) declares."""
    by_name: dict[str, dict[str, str]] = {}
    try:
        output = _git(path, "config", "--blob", gitmodules, "--get-regexp", r"^submodule\..*\.(path|url)$", timeout=30)
    except mirror.GitError:
        return {}
    for line in output.splitlines():
        key, _, value = line.partition(" ")
        name, _, field = key[len("submodule.") :].rpartition(".")
        by_name.setdefault(name, {})[field] = value.strip()
    return {entry["path"]: entry["url"] for entry in by_name.values() if entry.get("path") and entry.get("url")}


def _commit_present(path: Path, sha: str) -> bool:
    try:
        _git(path, "cat-file", "-e", f"{sha}^{{commit}}")
        return True
    except mirror.GitError:
        return False


def _read(
    path: Path, sha: str, address: str, prefix: str, depth: int, budget: list[int], problems: list[str]
) -> list[dict[str, Any]]:
    entries = _tree(path, sha)
    manifests = {
        name[: -len("/__manifest__.py")]: oid
        for kind, oid, name in entries
        if kind == "blob" and name.endswith("/__manifest__.py")
    }
    paths = set(manifests)
    kept = {module_path: oid for module_path, oid in manifests.items() if _module_path_ok(prefix + module_path,
                                                                                            {prefix + p for p in paths})}  # fmt: skip
    gitmodules = next((oid for kind, oid, name in entries if kind == "blob" and name == ".gitmodules"), None)
    oids = list(dict.fromkeys([*kept.values(), *([gitmodules] if gitmodules else [])]))
    _prefetch(path, oids)
    texts = _contents(path, oids)
    submodule = prefix.rstrip("/") or None
    found: list[dict[str, Any]] = []
    for module_path, oid in sorted(kept.items()):
        data = mirror.read_manifest(texts.get(oid, ""))
        if data.get("installable", True) is False:
            continue
        found.append(
            {
                "name": module_path.rsplit("/", 1)[-1],
                "title": str(data.get("name") or module_path.rsplit("/", 1)[-1])[:120],
                "summary": str(data.get("summary") or "")[:200],
                "application": data.get("application") is True,
                "path": prefix + module_path,
                "submodule": submodule,
            }
        )
    links = {name: oid for kind, oid, name in entries if kind == "commit"}
    if not links or depth >= SUBMODULE_DEPTH:
        return found
    declared = _submodules(path, gitmodules) if gitmodules else {}
    for sub_path, sub_sha in sorted(links.items()):
        shown = prefix + sub_path
        if budget[0] <= 0:
            problems.append(f"More than {MAX_SUBMODULES} submodules: {shown} and the next ones were not read.")
            break
        budget[0] -= 1
        url = declared.get(sub_path)
        if not url:
            problems.append(f"The submodule {shown} is not declared in .gitmodules.")
            continue
        try:
            sub_address = https_address(github._absolute(address, url))
            sub_mirror = mirror.refresh(sub_address)
            if not _commit_present(sub_mirror, sub_sha):  # a commit no branch points to (yet)
                _git(sub_mirror, "fetch", "-q", "--filter=blob:none", "origin", sub_sha, timeout=600)
            found += _read(sub_mirror, sub_sha, sub_address, shown + "/", depth + 1, budget, problems)
        except (mirror.GitError, AssertionError) as error:
            problems.append(f"The submodule {shown} could not be read: {error}")
    return found


def branch_commit(path: Path, branch: str) -> str:
    if not github.valid_branch(branch):
        raise BranchMissing("This branch name is not valid.")
    try:
        return _git(path, "rev-parse", "--verify", "-q", f"refs/heads/{branch}^{{commit}}").strip()
    except mirror.GitError:
        raise BranchMissing(f"The repository has no branch {branch} on GitHub.") from None


def of_branch(address: str, branch: str, refresh: bool = False) -> dict[str, Any]:
    """The modules of `branch` of the GitHub repository at `address`, its own first, then its submodules' (a name
    found twice: the first one, as Odoo's add-ons path finds it). Remembered per commit."""
    address = https_address(address)
    path = mirror.refresh(address, force=refresh)
    sha = branch_commit(path, branch)
    key = (address.lower(), sha)
    with _lock:
        cached = _cache.get(key)
    if cached is None:
        problems: list[str] = []
        listed = _read(path, sha, address, "", 0, [MAX_SUBMODULES], problems)
        unique: dict[str, dict[str, Any]] = {}
        for module in listed:
            unique.setdefault(module["name"], module)
        cached = {"modules": list(unique.values()), "problems": problems}
        with _lock:
            _cache[key] = cached
            while len(_cache) > CACHE_SIZE:
                _cache.pop(next(iter(_cache)))
    return {
        "repository": github.repo_name(address),
        "branch": branch,
        "commit": sha,
        "modules": [dict(module) for module in cached["modules"]],
        "problems": list(cached["problems"]),
    }
