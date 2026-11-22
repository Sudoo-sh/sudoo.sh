"""The pre-promotion checklist: what to know before a branch becomes production, or is merged into production.

Each item is green ("ok"), amber ("warning": worth reading, never blocks) or red ("blocked"):

    build       the branch's latest build: success green; with warnings, still building or never built amber;
                failed (or its tests failed) red
    backup      production's newest backup of its own: younger than PROJECT_PROMOTION_BACKUP_HOURS green, older or
                none amber ("Back up now" in the web app); no production instance yet: green
    modules     modules added, removed and with a new version between production and the branch (their
                __manifest__.py at both commits, projects/compare.py): removed modules or a version going down amber
    migrations  modules whose version goes up and have migration scripts for it: amber (they run on the next
                upgrade of production; staging should have run them first)
    odoo        the branch's instances and production run the same Odoo series, and the branch's modules are of it:
                a difference is red (a database cannot go back or forward by changing code)
    lock        the branch, or production's branch, is locked: red for someone the lock does not let through, amber
                for whom it does
    capacity    the project's and the plan's production limits (red: the move would be refused anyway), the server's
                running limit (amber: production instances never pause); left out for a branch merged into
                production on GitHub ("merge": it does not become a production branch)

Red items block the move to production (PUT .../stage) while PROJECT_PROMOTION_BLOCKS is on, unless the caller, who
has projects.manage in the branch, sends "promote_anyway": true; that is audit-logged with the red items. The quick
check made by the stage change itself leaves out what needs git (modules, migrations): they are amber at most.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from sudoo import projects
from sudoo.config import settings
from sudoo.core import jobs
from sudoo.instances import backups, capacity, stages, store
from sudoo.projects import access, builds, compare, staging

OK, WARNING, BLOCKED = "ok", "warning", "blocked"
ORDER = ("build", "backup", "modules", "migrations", "odoo", "lock", "capacity")


def _item(key: str, status: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "status": status, "text": text, **extra}


def latest_build(project_id: str, branch: str) -> dict[str, Any] | None:
    """The branch's newest build that is not dropped (the newest one at all when every build was dropped)."""
    found = [e for e in builds.history(project_id, branch) if e.get("type") == "build"]
    return next((e for e in found if e.get("status") != "dropped"), found[0] if found else None)


def build_item(project_id: str, branch: str) -> dict[str, Any]:
    entry = latest_build(project_id, branch)
    if entry is None:
        return _item("build", WARNING, f"{branch} has never been built in the panel: nothing shows that its code runs.")
    tests = entry.get("tests") or {}
    shown = {"build": {"id": entry["id"], "status": entry.get("status"), "at": entry.get("at"),
                       "commit": entry.get("commit"), "tests": tests or None}}  # fmt: skip
    status = str(entry.get("status") or "")
    if status == "failed" or tests.get("status") == "failed":
        what = "its tests failed" if tests.get("status") == "failed" else "it failed"
        return _item("build", BLOCKED, f"The latest build of {branch} did not pass: {what}.", **shown)
    if status == "building":
        return _item("build", WARNING, f"{branch} is being built now; wait for its result.", **shown)
    if status == "warning":
        return _item("build", WARNING, f"The latest build of {branch} succeeded with warnings.", **shown)
    if tests.get("status") == "success":
        return _item("build", OK, f"The latest build of {branch} succeeded and its tests passed.", **shown)
    return _item("build", OK, f"The latest build of {branch} succeeded.", **shown)


def backup_item(production: str | None, now: float | None = None) -> dict[str, Any]:
    if production is None:
        return _item("backup", OK, "There is no production instance yet, so there is no production data to protect.")
    hours = int(settings.project_promotion_backup_hours)
    found = backups.own_backups(production)
    newest = found[0] if found else None
    extra = {"instance": production, "backup": {"file": newest["file"], "created": newest["created"]} if newest else None,
             "max_hours": hours}  # fmt: skip
    if jobs.busy(production):
        extra["busy"] = True
    if newest is None:
        return _item("backup", WARNING, f"{production} (production) has no backup. Back it up before the change.",
                     **extra)  # fmt: skip
    age = ((now or time.time()) - float(newest["created"])) / 3600
    if hours and age > hours:
        return _item("backup", WARNING, f"The newest backup of {production} is {age:.0f} hours old (more than {hours}). "
                     "Back it up now.", **extra)  # fmt: skip
    return _item("backup", OK, f"{production} (production) was backed up {max(age, 0):.0f} hours ago.", **extra)


def module_items(result: Mapping[str, Any] | None, problem: str | None) -> list[dict[str, Any]]:
    """The modules and migrations items from a comparison (compare.compare), or why there is none."""
    if result is None:
        text = problem or "The branch was not compared with production."
        return [_item("modules", WARNING, f"The modules could not be compared: {text}"),
                _item("migrations", WARNING, "Migration scripts could not be looked for.")]  # fmt: skip
    modules = result["modules"]
    added, removed, changed = modules["added"], modules["removed"], modules["changed"]
    going_down = [c for c in changed if c["bump"] == "down"]
    bumped = [c for c in changed if c["bump"] == "up"]
    parts = []
    if added:
        parts.append(f"{len(added)} added")
    if removed:
        parts.append(f"{len(removed)} removed")
    if bumped:
        parts.append(f"{len(bumped)} with a new version")
    unchanged_version = [c for c in changed if c["bump"] == "same"]
    if unchanged_version:
        parts.append(f"{len(unchanged_version)} changed without a new version")
    data = {"modules": modules, "ahead": result.get("ahead"), "behind": result.get("behind")}
    if removed or going_down:
        names = ", ".join([m["name"] for m in removed] + [c["name"] for c in going_down])
        text = (f"Modules leave production or go back to an older version ({names}): their data stays in the database "
                "and Odoo does not undo it.")  # fmt: skip
        first = _item("modules", WARNING, text, **data)
    elif parts:
        first = _item("modules", OK, f"Modules: {', '.join(parts)}.", **data)
    else:
        first = _item("modules", OK, "No module changes between production and this branch.", **data)
    pending = list(result.get("migrations") or [])
    if pending:
        count = sum(len(m["scripts"]) for m in pending)
        second = _item("migrations", WARNING, f"{count} migration {'script runs' if count == 1 else 'scripts run'} on the "
                       f"next upgrade ({', '.join(m['module'] + ' ' + m['version'] for m in pending)}). Run them on "
                       "staging first.", migrations=pending)  # fmt: skip
    elif bumped:
        second = _item("migrations", OK, "Modules with a new version have no migration scripts; Odoo upgrades them "
                       "as usual.", migrations=[])  # fmt: skip
    else:
        second = _item("migrations", OK, "No pending migrations.", migrations=[])
    return [first, second]


def _series_of(names: list[str]) -> set[str]:
    return {s for name in names if store.exists(name) and (s := backups.series(store.read_env(name)["ODOO_VERSION"]))}


def odoo_item(project: Mapping[str, Any], branch: str, production: str | None,
              result: Mapping[str, Any] | None) -> dict[str, Any]:  # fmt: skip
    members = projects.by_branch(project["id"]).get(branch, [])
    mine = _series_of(members) or {backups.series(str(project.get("odoo_version") or "")) or ""}
    target = _series_of([production]) if production else {backups.series(str(project.get("odoo_version"))) or ""}
    data = {"branch_series": sorted(mine), "production_series": sorted(target)}
    if mine != target:
        return _item("odoo", BLOCKED, f"{branch} runs Odoo {', '.join(sorted(mine))}, production Odoo "
                     f"{', '.join(sorted(target))}: upgrade the database first (Upgrade tab).", **data)  # fmt: skip
    series = next(iter(target))
    if result is not None and series:
        foreign = sorted(
            c["name"]
            for c in [*result["modules"]["changed"], *result["modules"]["added"]]
            if (version := c.get("to") or c.get("version") or "").count(".") >= 3
            and backups.series(version) not in (None, series)
        )
        if foreign:
            return _item("odoo", BLOCKED, f"Modules of {branch} are written for another Odoo version than {series} "
                         f"({', '.join(foreign[:8])}).", **data)  # fmt: skip
    return _item("odoo", OK, f"{branch} and production both run Odoo {series}.", **data)


def lock_item(project: Mapping[str, Any], branch: str, production: str | None,
              user: Mapping[str, Any] | None) -> dict[str, Any]:  # fmt: skip
    locked = [(b, lock) for b in dict.fromkeys([branch, projects.branch_of(production) if production else ""])
              if b and (lock := access.lock_of(project, b)) is not None]  # fmt: skip
    if not locked:
        return _item("lock", OK, "Neither this branch nor production's is locked.")
    names = ", ".join(b for b, _ in locked)
    if user is not None and any(not access.lock_allows(user, lock) for _, lock in locked):
        return _item("lock", BLOCKED, f"Locked ({names}): only the people and teams the lock allows may change it.",
                     branches=[b for b, _ in locked])  # fmt: skip
    return _item("lock", WARNING, f"Locked ({names}); the lock lets you through. Tell its other users.",
                 branches=[b for b, _ in locked])  # fmt: skip


def capacity_item(project: Mapping[str, Any], branch: str) -> dict[str, Any]:
    refused = projects.production_refusal(project, branch)  # what the stage change itself checks
    if refused:
        return _item("capacity", BLOCKED, refused)
    running = capacity.refusal()
    if running:
        return _item("capacity", WARNING, f"{running} Production instances never pause.")
    found = capacity.current()
    if found and (found.get("overloaded") or found.get("warning")):
        text = found.get("alert") or found.get("message") or "The server is near its capacity."
        return _item("capacity", WARNING, f"{text} Production instances never pause.")
    return _item("capacity", OK, "The plan, the project and the server have room for this production branch.")


def check(project: Mapping[str, Any], branch: str, user: Mapping[str, Any] | None = None,
          deep: bool = True, refresh: bool = False, merge: bool = False) -> dict[str, Any]:  # fmt: skip
    """The checklist of `branch` going into production: {"branch", "mode", "production" (branch, instance), "items",
    "blocked" (red items' keys), "blocking" (whether they stop the move), "compare"}. `deep`: compare the code with
    production's (git); without it the modules and migrations are left out. `merge`: the branch is merged into
    production's branch on GitHub rather than becoming a production branch itself (no capacity item)."""
    production = staging.production_instance(project)
    production_branch = projects.branch_of(production) if production else None
    if production_branch is None:
        production_branch = next((b for b in projects.production_branches(project) if b != branch), None)
    result: dict[str, Any] | None = None
    problem: str | None = None
    if deep and production_branch and production_branch != branch:
        try:
            result = compare.compare(project, production_branch, branch, refresh=refresh)
        except compare.CompareError as error:
            problem = str(error)
    items = [build_item(str(project["id"]), branch), backup_item(production)]
    if deep:
        if production_branch and production_branch != branch:
            items += module_items(result, problem)
        else:
            items += [_item("modules", OK, "There is no other production branch to compare with."),
                      _item("migrations", OK, "No pending migrations.", migrations=[])]  # fmt: skip
    items += [odoo_item(project, branch, production, result), lock_item(project, branch, production, user)]
    if not merge and projects.branch_stage(project, branch) != stages.PRODUCTION:
        items.append(capacity_item(project, branch))
    blocked = [item["key"] for item in items if item["status"] == BLOCKED]
    return {
        "branch": branch,
        "mode": "merge" if merge else "move",
        "production": {"branch": production_branch, "instance": production},
        "items": items,
        "blocked": blocked,
        "blocking": bool(blocked) and bool(settings.project_promotion_blocks),
        "compare": (
            {k: result[k] for k in ("ahead", "behind", "files_changed", "github_url", "source")} if result else None
        ),
        "checked_at": time.time(),
    }
