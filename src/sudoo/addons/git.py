"""Add-on source "git": the Enterprise add-ons come from a git repository with one branch per Odoo version.

ENTERPRISE_GIT_URL is the repository (usually a private copy you control, so you can give the server a read-only
deploy key under GitHub access); ENTERPRISE_GIT_BRANCH names the branch of a version, with {version} and {major}
replaced, for example "{version}" -> 19.0 or "enterprise-{major}" -> enterprise-19.

The branch is downloaded once into <ODOO_ROOT>/enterprise-git/<version> (latest commit only) and brought up to date
each time an Enterprise instance of that version is created.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from sudoo.addons.base import Log
from sudoo.addons.folder import expand
from sudoo.config import Settings

_BRANCH_CACHE_SECONDS = 600


class GitSource:
    """Enterprise add-ons from one branch per version of a git repository."""

    def __init__(self, settings: Settings) -> None:
        if not settings.enterprise_git_url:
            raise ValueError(
                "ENTERPRISE_SOURCE=git needs ENTERPRISE_GIT_URL: the repository with one branch per version."
            )
        self.settings = settings
        self._branches: tuple[set[str], float] = (set(), 0.0)

    def folder(self, version: str) -> Path:
        return self.settings.odoo_root / "enterprise-git" / version

    def branch(self, version: str) -> str:
        return expand(self.settings.enterprise_git_branch, version)

    def available(self, version: str) -> bool:
        if (self.folder(version) / self.settings.enterprise_marker / "__manifest__.py").is_file():
            return True
        return self.branch(version) in self._remote_branches()

    def prepare(self, version: str, log: Log) -> Path:
        folder, branch = self.folder(version), self.branch(version)
        if (folder / ".git").is_dir():
            self._git(log, "-C", str(folder), "fetch", "--depth", "1", "origin", branch)
            self._git(log, "-C", str(folder), "reset", "--hard", "FETCH_HEAD")
        else:
            folder.parent.mkdir(parents=True, exist_ok=True)
            self._git(
                log, "clone", "--depth", "1", "--branch", branch, "--", self.settings.enterprise_git_url, str(folder)
            )
        if not (folder / self.settings.enterprise_marker).is_dir():
            raise FileNotFoundError(
                f"Branch {branch} has no {self.settings.enterprise_marker} module at its top level."
            )
        return folder

    def _remote_branches(self) -> set[str]:
        names, fetched = self._branches
        if time.time() - fetched < _BRANCH_CACHE_SECONDS:
            return names
        result = subprocess.run(["git", "ls-remote", "--heads", "--", self.settings.enterprise_git_url],
                                capture_output=True, text=True, timeout=60)  # fmt: skip
        names = {line.split("refs/heads/", 1)[1] for line in result.stdout.splitlines() if "refs/heads/" in line}
        self._branches = (names, time.time())
        return names

    @staticmethod
    def _git(log: Log, *args: str) -> None:
        log(f"$ git {' '.join(args)}\n")
        result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=1800)
        log(result.stdout + result.stderr)
        if result.returncode != 0:
            raise RuntimeError(f"git {args[0] if args[0] != '-C' else args[2]} failed")
