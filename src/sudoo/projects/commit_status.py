"""Builds reported on GitHub as commit statuses: the check next to a commit says how its build went.

When a build of a project's branch starts (projects/builds.py), the panel sets the status "sudoo/<stage>" of the
commit being built to "pending"; when it ends, to "success" (success or warning), "failure" (a test failed) or
"error" (the build failed otherwise). The status links to the branch's history in the panel
(<panel address>/projects/<id>/branches/<branch>).

It needs the GitHub App (repos/github_app.py) installed on the repository's account with Commit statuses: Read and
write (apps made from the panel's manifest ask for it; older apps get it on GitHub: the app's Permissions and events,
then an owner accepts it on the installation), and the project's commit_statuses option (PROJECT_COMMIT_STATUSES, on
by default). When a failed build's cause was recognised (core/failure_rules.py) the status says "<step category>:
<cause title>" (at most 140 characters); otherwise the build's result text, as before. GitHub is told in a background thread: a slow or refusing GitHub never holds or fails a build.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

from sudoo import projects
from sudoo.repos import github_app

STATES = {"success": "success", "warning": "success", "failed": "failure"}


def available(project: Mapping[str, Any]) -> bool:
    """Whether the GitHub App may write commit statuses in the project's repository."""
    try:
        return github_app.can_post_statuses(str(project["repository"]).split("/")[0])
    except Exception:  # noqa: BLE001 - an unreadable app file: no statuses
        return False


def enabled(project: Mapping[str, Any]) -> bool:
    return bool(projects.option(project, "commit_statuses")) and available(project)


def target_url(project_id: str, branch: str) -> str | None:
    """The branch's history in the panel, or None when the panel has no public address yet."""
    try:
        from sudoo import exposure

        base = exposure.address(exposure.PANEL)
    except Exception:  # noqa: BLE001 - a status without a link is still worth posting
        base = None
    if not base:
        return None
    return f"{base.rstrip('/')}/projects/{quote(project_id)}/branches/{quote(branch, safe='')}"


def context(stage: str) -> str:
    return f"sudoo/{stage}"


def _spawn(fn: Callable[[], None]) -> None:
    """Run `fn` in the background (the tests replace this to run it at once)."""
    threading.Thread(target=fn, daemon=True, name="commit-status").start()


def _post(project: Mapping[str, Any], sha: str, state: str, stage: str, description: str, branch: str) -> bool:
    try:
        github_app.post_status(str(project["repository"]), sha, state, context(stage), description,
                               target_url(str(project["id"]), branch))  # fmt: skip
    except Exception as error:  # noqa: BLE001 - GitHub refusing never fails a build
        print(f"commit status of {project['id']} {branch} {sha[:7]} not set: {error}", flush=True)
        return False
    return True


def started(project_id: str, branch: str, stage: str, sha_of: Callable[[], str | None]) -> None:
    """A build of `branch` started: "pending" on the commit `sha_of()` returns (read in the background; None: no
    status, the build will not change the code)."""
    project = projects.get(project_id)
    if project is None or not enabled(project):
        return

    def post() -> None:
        sha = sha_of()
        if sha:
            _post(project, sha, "pending", stage, "Building", branch)

    _spawn(post)


DESCRIPTION_MAX = 140  # what GitHub keeps of a status description (repos/github_app.post_status cuts there)


# A step's title can carry names a person chose (a branch, a database, a module, an instance, a backup's origin), and
# a commit status is read by everyone who can see the repository. So the status names only a fixed category, chosen by
# the start of the title; a title that starts otherwise gets no step at all.
_STEP_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Downloading the latest code", "Code download"),
    ("Downloading Odoo", "Odoo image"),
    ("Using the Odoo", "Odoo image"),
    ("Preparing the Odoo", "Odoo image"),
    ("Creating a new database", "Database"),
    ("Creating the Odoo", "Database"),
    ("Loading the", "Database"),
    ("Copying the data", "Database"),
    ("Installing", "Modules"),
    ("Upgrading", "Modules"),
    ("Checking the repository's modules", "Modules"),
    ("Starting", "Start"),
)


def step_category(step: str | None) -> str | None:
    """A fixed word for what the failed step was doing, from the start of its title; None when unknown."""
    text = (step or "").strip()
    return next((category for prefix, category in _STEP_CATEGORIES if text.startswith(prefix)), None)


def failure_description(failure: Mapping[str, Any] | None, result: str) -> str:
    """ "<step category>: <cause title>" when core/failure_rules.py recognised the cause of the failed build, else
    `result` as before. Only a fixed category word and the rule's fixed title: no step title (it can hold names), no
    log text, no captured value."""
    rule = (failure or {}).get("rule")
    title = str((rule or {}).get("title") or "").strip() if isinstance(rule, Mapping) else ""
    if not title:
        return result
    category = step_category((failure or {}).get("step"))
    text = f"{category}: {title}" if category else title
    return text if len(text) <= DESCRIPTION_MAX else text[: DESCRIPTION_MAX - 1].rstrip() + "…"


def finished(project_id: str, branch: str, stage: str, status: str, result: str, shas: list[str],
             tests_failed: bool = False, failure: Mapping[str, Any] | None = None) -> None:  # fmt: skip
    """A build ended with `status` (projects/builds.STATUSES): its state on each commit of `shas` (the commit built,
    and the one marked pending when that was another)."""
    project = projects.get(project_id)
    if project is None or status not in STATES or not shas or not enabled(project):
        return
    state = STATES[status] if status != "failed" else "failure" if tests_failed else "error"
    description = (failure_description(failure, result) if status == "failed" else result) or status

    def post() -> None:
        for sha in dict.fromkeys(shas):
            _post(project, sha, state, stage, description, branch)

    _spawn(post)
