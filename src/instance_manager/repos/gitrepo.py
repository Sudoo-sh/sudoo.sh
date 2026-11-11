"""Git operations on an instance's code (its addons/ folder) and Odoo module discovery."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from instance_manager.core.shell import capture

_SKIP_DIRS = {"__pycache__", "node_modules", "setup"}


def git(path: str | Path, *args: str) -> list[str]:
    """`git -C <path> <args>`."""
    return ["git", "-C", str(path), *args]


def branch_on_github(addons: Path, branch: str) -> bool | None:
    """True when `branch` exists on GitHub, False when GitHub says it does not, None when GitHub was not reachable."""
    try:
        result = subprocess.run(
            git(addons, "ls-remote", "--heads", "origin", f"refs/heads/{branch}"),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def origin_url(addons: Path) -> str:
    return capture(git(addons, "remote", "get-url", "origin"))


def head(addons: Path) -> str:
    return capture(git(addons, "rev-parse", "HEAD"))


def commits(addons: Path, count: int = 8) -> list[dict[str, str]]:
    """Recent commits: [{"sha", "subject", "when", "author"}]."""
    result: list[dict[str, str]] = []
    for line in capture(git(addons, "log", f"-{count}", "--format=%h|%s|%cr|%an")).splitlines():
        sha, subject, when, author = (line.split("|", 3) + ["", "", ""])[:4]
        result.append({"sha": sha, "subject": subject, "when": when, "author": author})
    return result


def submodules(addons: Path) -> list[dict[str, str]]:
    """Top-level submodules: [{"path", "sha", "url"}]."""
    result: list[dict[str, str]] = []
    for line in capture(git(addons, "submodule", "status")).splitlines():
        parts = line[1:].split()
        if len(parts) >= 2:
            url = capture(git(addons, "config", "-f", ".gitmodules", f"submodule.{parts[1]}.url"))
            result.append({"path": parts[1], "sha": parts[0][:8], "url": url})
    return result


def snapshot(addons: Path) -> dict[str, str]:
    """{path relative to addons: checked-out commit} for the repository and all submodules."""
    heads = {".": head(addons)}
    for line in capture(git(addons, "submodule", "status", "--recursive")).splitlines():
        parts = line[1:].split()
        if len(parts) >= 2:
            heads[parts[1]] = parts[0]
    return heads


def changed_modules(addons: Path, before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Names of Odoo modules with files changed between two snapshots."""
    modules: set[str] = set()
    for path, new in after.items():
        old = before.get(path)
        if not old or not new or old == new:
            continue
        repo = addons / path
        for changed_file in capture(git(repo, "diff", "--name-only", old, new)).splitlines():
            folder = (repo / changed_file).parent
            while folder != addons and addons in folder.parents:
                if (folder / "__manifest__.py").exists():
                    modules.add(folder.name)
                    break
                folder = folder.parent
    return sorted(modules)


def module_folders(addons: Path, max_depth: int = 4) -> list[Path]:
    """Folders under `addons` that directly contain Odoo modules (these make up Odoo's addons_path)."""
    found: list[Path] = []
    base_depth = len(addons.parts)
    for dirpath, dirnames, filenames in os.walk(addons):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS)
        if "__manifest__.py" in filenames:
            dirnames[:] = []  # a module: do not look inside it
            continue
        if any((Path(dirpath) / d / "__manifest__.py").exists() for d in dirnames):
            found.append(Path(dirpath))
        if len(Path(dirpath).parts) - base_depth >= max_depth:
            dirnames[:] = []
    return found


def own_modules(addons: Path) -> list[str]:
    """Modules that belong to the repository itself, not to its submodules."""
    submodule_paths = [
        line.split(" ", 1)[1]
        for line in capture(git(addons, "config", "-f", ".gitmodules", "--get-regexp", r"\.path$")).splitlines()
        if " " in line
    ]
    modules: list[str] = []
    for folder in module_folders(addons):
        relative = str(folder.relative_to(addons))
        if any(relative == s or relative.startswith(s + "/") for s in submodule_paths):
            continue
        modules += sorted(p.name for p in folder.iterdir() if (p / "__manifest__.py").exists())
    return modules
