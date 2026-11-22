"""Build options of a project's branches: what a new commit does, which modules a new database gets, which tests run.

Odoo.sh calls them "New build options"; the panel keeps them per project (its options, projects/__init__.OPTIONS,
whose defaults are panel settings) and lets each branch change them (project["branches"][branch]["options"]):

    on_new_commit   "update"     the new code in the same database, the changed modules upgraded (the usual update)
                    "new_build"  development: a new database built from scratch; staging: rebuilt from a new
                                 neutralised copy of production (projects/staging.py)
                    "nothing"    the commit is recorded in the branch's history, nothing is built
    module_install  "repository" every module of the repository (development only)
                    "all"        every module Odoo has (localisations, test and hardware modules left out), with the
                                 repository's; tests are off
                    "list"       only `modules` (comma-separated module names)
    test_suite      "off" | "selection" (the tests of the repository's installed modules) | "custom" (`test_tags`, Odoo's
                    --test-tags syntax)
    demo_data       new development databases get Odoo's demo data

A branch's value comes from its own options, else the project's, else the panel's setting. on_new_commit of a staging
branch follows the project's staging_on_push ("rebuild": new_build, "keep_data": update) until the branch has its own;
tests of a staging branch run only when the project's staging_tests is on (or the branch has its own test_suite).
Production is never built by these options: its new code always comes through the safe update.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from sudoo import projects
from sudoo.core.odoo_args import parse_modules, parse_test_tags
from sudoo.instances import stages

ON_NEW_COMMIT = ("new_build", "update", "nothing")
MODULE_INSTALL = ("repository", "all", "list")
TEST_SUITE = ("off", "selection", "custom")
CHOICES: dict[str, tuple[str, ...]] = {
    "on_new_commit": ON_NEW_COMMIT,
    "module_install": MODULE_INSTALL,
    "test_suite": TEST_SUITE,
}
KEYS = ("on_new_commit", "module_install", "modules", "test_suite", "test_tags", "demo_data")
STAGING_ON_PUSH = {"rebuild": "new_build", "keep_data": "update"}

# a branch key and the project option that gives its default
PROJECT_OPTION = {
    "on_new_commit": "development_on_new_commit",
    "module_install": "module_install",
    "modules": "modules",
    "test_suite": "test_suite",
    "test_tags": "test_tags",
    "demo_data": "development_demo_data",
}


def clean(key: str, value: Any) -> Any:
    """`value` of option `key` checked and normalised (None: follow the project). Raises ValueError with a sentence
    for people. Also used for the project's own options of the same kind."""
    if value is None:
        return None
    if key in CHOICES or key in ("development_on_new_commit",):
        allowed = CHOICES.get(key, ON_NEW_COMMIT)
        if value not in allowed:
            raise ValueError(f"{key}: {' or '.join(allowed)}.")
        return value
    if key == "modules":
        if not isinstance(value, (str, list)):
            raise ValueError("modules: module names, comma-separated or as a list.")
        return ",".join(parse_modules(value))
    if key == "test_tags":
        return parse_test_tags(value)
    if key in ("demo_data", "staging_tests", "commit_statuses"):
        if not isinstance(value, bool):
            raise ValueError(f"{key}: true or false.")
        return value
    raise ValueError(f"Unknown option {key[:40]!r}. Options: {', '.join(KEYS)}.")


def own(project: Mapping[str, Any], branch: str) -> dict[str, Any]:
    """The branch's own options (only those it sets)."""
    found = ((project.get("branches") or {}).get(branch) or {}).get("options") or {}
    return {key: found[key] for key in KEYS if found.get(key) is not None}


def project_default(project: Mapping[str, Any], key: str, stage: str) -> Any:
    """What a branch of `stage` gets for `key` when it sets nothing itself."""
    if key == "on_new_commit" and stage == stages.STAGING:
        return STAGING_ON_PUSH.get(str(projects.option(project, "staging_on_push")), "update")
    if key == "test_suite" and stage == stages.STAGING and not projects.option(project, "staging_tests"):
        return "off"
    return projects.option(project, PROJECT_OPTION[key])


def effective(project: Mapping[str, Any], branch: str, stage: str | None = None) -> dict[str, Any]:
    """The options that apply to `branch` now, with "sources": where each comes from ("branch" or "project"; a
    project option without its own value follows the panel's setting). Production: updates only, no tests."""
    stage = stage or projects.branch_stage(project, branch)
    mine = own(project, branch)
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for key in KEYS:
        if key in mine:
            values[key], sources[key] = mine[key], "branch"
        else:
            values[key], sources[key] = project_default(project, key, stage), "project"
    if stage == stages.PRODUCTION:
        values |= {"on_new_commit": "update", "test_suite": "off"}
        sources |= {"on_new_commit": "production", "test_suite": "production"}
    if values["module_install"] == "all" and values["test_suite"] != "off":
        values["test_suite"], sources["test_suite"] = "off", "module_install"  # Odoo.sh: no tests with every module
    if values["module_install"] == "list" and not values["modules"]:
        values["module_install"], sources["module_install"] = "repository", "empty_list"
    if values["test_suite"] == "custom" and not values["test_tags"]:
        values["test_suite"], sources["test_suite"] = "off", "empty_tags"
    return {**values, "sources": sources}


def problem(values: Mapping[str, Any]) -> str | None:
    """Why a combination of options cannot be kept (a list without modules, custom tests without tags), or None."""
    if values.get("module_install") == "list" and not values.get("modules"):
        return 'module_install "list" needs modules: the module names to install, comma-separated.'
    if values.get("test_suite") == "custom" and not values.get("test_tags"):
        return 'test_suite "custom" needs test_tags (Odoo\'s --test-tags syntax, such as /sale_custom,-slow).'
    return None


def change(project_id: str, branch: str, values: Mapping[str, Any], user: str) -> dict[str, Any]:
    """Set (a value) or reset (None) the branch's own options; returns its own options afterwards. Values must be
    cleaned already (clean())."""
    result: dict[str, Any] = {}

    def update(project: dict[str, Any]) -> dict[str, Any]:
        found = dict(project.get("branches") or {})
        entry = dict(found.get(branch) or {})
        mine = {k: v for k, v in {**(entry.get("options") or {}), **values}.items() if v is not None}
        if mine:
            entry["options"] = mine
        else:
            entry.pop("options", None)
        entry |= {"options_changed_by": user, "options_changed_at": int(time.time())}
        found[branch] = entry
        project["branches"] = found
        result.update(mine)
        return project

    projects.change(project_id, update)
    return result
