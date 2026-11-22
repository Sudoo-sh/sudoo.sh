"""Git operations on an instance's code (its addons/ folder) and Odoo module discovery."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from sudoo.core.cache import VersionedMemo, settled, signature
from sudoo.core.shell import capture

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


_ORIGINS: VersionedMemo[str] = VersionedMemo(seconds=3600)


def _address_rules() -> tuple[tuple[str, str], ...]:
    """git's address rules for this process (github.configure_git), which change how `get-url` answers."""
    return tuple(sorted((key, value) for key, value in os.environ.items() if key.startswith("GIT_CONFIG_")))


def origin_url(addons: Path) -> str:
    """The repository's address. A webhook delivery asks for it for every instance, so it is remembered while
    .git/config and git's address rules stay the same (core/cache.py)."""
    config = signature(addons / ".git" / "config")
    version = (config if settled(config) else time.monotonic_ns(), _address_rules())
    return _ORIGINS.get(str(addons), version, lambda: capture(git(addons, "remote", "get-url", "origin")))


def head(addons: Path) -> str:
    return capture(git(addons, "rev-parse", "HEAD"))


def commits(addons: Path, count: int = 8) -> list[dict[str, str]]:
    """Recent commits: [{"sha" (short), "full_sha", "subject", "when" (ISO 8601, UTC), "author"}]. The fields are
    split by the unit separator, which a subject or a name never contains (a "|" may)."""
    result: list[dict[str, str]] = []
    output = capture(git(addons, "log", f"-{count}", "--format=%h%x1f%H%x1f%cI%x1f%an%x1f%s"))
    for line in output.splitlines():
        sha, full, when, author, subject = (line.split("\x1f", 4) + ["", "", "", ""])[:5]
        result.append({"sha": sha, "full_sha": full, "subject": subject, "when": _utc(when), "author": author})
    return result


def commit(addons: Path, ref: str = "HEAD") -> dict[str, str] | None:
    """One commit, as commits() shows it ("@{upstream}": the latest one fetched from GitHub), or None."""
    output = capture(git(addons, "log", "-1", "--format=%h%x1f%H%x1f%cI%x1f%an%x1f%s", ref, "--"))
    if not output.strip():
        return None
    sha, full, when, author, subject = (output.strip().split("\x1f", 4) + ["", "", "", ""])[:5]
    return {"sha": sha, "full_sha": full, "subject": subject, "when": _utc(when), "author": author}


def _utc(when: str) -> str:
    """git's ISO 8601 date in UTC (2026-11-18T06:34:19Z), or as given when git wrote something else."""
    try:
        return datetime.fromisoformat(when).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return when


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
