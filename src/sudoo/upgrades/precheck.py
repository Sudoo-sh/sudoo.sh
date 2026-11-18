"""The pre-check of a database upgrade: what is installed, and whether the repository's modules are ready for the
target version.

The modules that count are the repository's own (not its submodules') that are installed in the database. Each is
looked for in the upgrade branch (named after the target version, such as 19.0), read from a shallow clone made for
the check and removed after it:

    ready             in the branch, installable, its manifest version starts with the target series ("19.0.1.0.0")
    version_mismatch  in the branch, but its version is of another series ("18.0.1.2.0": not ported yet)
    unknown_version   in the branch, its version names no series ("1.0"): nothing says it was ported
    not_installable   in the branch with "installable": False
    missing           not in the branch (or there is no upgrade branch)

Nothing here blocks a test: the report says what a developer still has to port. Custom modules must be ported by
developers; no upgrade method does it for them.
"""

from __future__ import annotations

import ast
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from sudoo.config import settings
from sudoo.core.shell import succeeds
from sudoo.instances import backups, odoo, store
from sudoo.repos import github
from sudoo.repos.gitrepo import module_folders, origin_url, own_modules
from sudoo.upgrades import method_for, method_problem, upgrade_branch

CLONE_SECONDS = 300


def manifest(path: Path) -> dict[str, Any]:
    """A module's __manifest__.py as a dict ({} when it cannot be read: it is never executed)."""
    try:
        found = ast.literal_eval((path / "__manifest__.py").read_text(errors="replace"))
    except (OSError, ValueError, SyntaxError, MemoryError, RecursionError):
        return {}
    return found if isinstance(found, dict) else {}


def modules_of(addons: Path) -> dict[str, dict[str, Any]]:
    """{module: manifest} of the repository's own modules in a checkout."""
    names = set(own_modules(addons))
    found: dict[str, dict[str, Any]] = {}
    for folder in module_folders(addons):
        for path in sorted(folder.iterdir()):
            if path.name in names and path.name not in found and (path / "__manifest__.py").exists():
                found[path.name] = manifest(path)
    return found


def status(info: dict[str, Any] | None, target: str) -> str:
    if info is None:
        return "missing"
    if info.get("installable") is False:
        return "not_installable"
    version = str(info.get("version") or "")
    series = re.match(r"^(\d+\.\d+)\.\d+\.\d+", version)
    if version.count(".") >= 4 and series:
        return "ready" if series[1] == target else "version_mismatch"
    return "unknown_version"


def _branch_modules(address: str, branch: str) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    """({module: manifest} of the branch, error); None when the branch could not be read."""
    work = settings.odoo_root / "upgrades" / f"check-{secrets.token_hex(4)}"
    work.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        cloned = succeeds(
            ["git", "clone", "-q", "--depth", "1", "--single-branch", "--no-recurse-submodules", "-b", branch, "--",
             address, str(work)],  # fmt: skip
            timeout=CLONE_SECONDS,
        )
        if not cloned or not (work / ".git").exists():
            return None, f"The branch {branch} could not be downloaded from GitHub."
        return modules_of(work), None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def report(name: str, target: str) -> dict[str, Any]:
    """The pre-check of upgrading instance `name` to Odoo `target`."""
    env = store.read_env(name)
    addons = store.addons_dir(name)
    address = "" if store.plain(env) else origin_url(addons)
    branch = upgrade_branch(target)
    warnings: list[dict[str, str]] = []
    blocking: list[dict[str, str]] = []
    problem = method_problem(env, target)
    if problem:
        blocking.append({"code": "method", "text": problem})

    database = (odoo.databases(name) or [""])[0]
    installed: list[str] | None = None
    if database and succeeds(["docker", "exec", store.container(name, "db"), "pg_isready", "-q", "-U", "odoo"]):
        found = backups.installed_modules(name, database)
        installed = sorted(found) or None
    if installed is None:
        warnings.append({"code": "installed_unknown", "text": "The installed modules could not be read: the "
                         "database is not running. Start the instance and check again."})  # fmt: skip

    branch_exists: bool | None = None
    target_modules: dict[str, dict[str, Any]] | None = None
    if address:
        heads, _, error = github.heads(address)
        if error:
            warnings.append({"code": "branch_unknown", "text": f"The repository's branches could not be read: {error}"})
        else:
            branch_exists = branch in heads
            if branch_exists:
                target_modules, error = _branch_modules(address, branch)
                if error:
                    warnings.append({"code": "branch_unknown", "text": error})
            else:
                warnings.append({"code": "no_branch", "text": f"The repository has no branch {branch}: the test runs "
                                 f"on plain Odoo {target} with standard modules only, and the repository's modules "
                                 "are missing from it."})  # fmt: skip

    current = modules_of(addons) if address else {}
    modules: list[dict[str, Any]] = []
    for module in sorted(current):
        if installed is not None and module not in installed:
            continue  # in the repository but not installed: not part of this database's upgrade
        info = (target_modules or {}).get(module) if target_modules is not None else None
        modules.append(
            {
                "name": module,
                "version": str(current[module].get("version") or ""),
                "target_version": str(info.get("version") or "") if info else None,
                "status": status(info, target),
            }
        )
    not_ready = [m["name"] for m in modules if m["status"] != "ready"]
    if not_ready:
        warnings.append({"code": "modules_not_ready", "text": f"{len(not_ready)} of the repository's modules are not "
                         f"ready for Odoo {target}: {', '.join(not_ready[:10])}. A developer has to port them."})  # fmt: skip
    return {
        "instance": name,
        "from_version": env.get("ODOO_VERSION"),
        "target": target,
        "edition": env.get("EDITION", "community"),
        "method": method_for(env),
        "repository": address or None,
        "branch": branch if address else None,
        "branch_exists": branch_exists,
        "installed": installed or [],
        "installed_count": len(installed or []),
        "modules": modules,
        "warnings": warnings,
        "blocking": blocking,
        "checked_at": round(time.time()),
    }
