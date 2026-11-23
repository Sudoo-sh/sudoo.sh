"""Pull-request previews: a development instance for the branch of each open pull request, linked from the pull
request itself, and deleted when the pull request is closed or merged.

How the panel hears of pull requests: the GitHub App's webhook ("pull_request" events: opened, reopened,
synchronize, ready_for_review, closed; triggers/github_app.py) within seconds, and, as a fallback that needs no
webhook, a look at each project's open pull requests every POLL_MINUTES (poll(), from projects/automation.py).

When a pull request opens, and the project's pr_previews option is on (PROJECT_PR_PREVIEWS, off by default):

    same repository    the head branch's instance is the preview when the branch has one (a project that creates
                       instances for new branches made it already); otherwise a development instance of the branch
                       is created, as for a new branch (projects/automation.py), and is the preview
    a fork             only when the project's pr_previews_forks option is on (PROJECT_PR_PREVIEWS_FORKS, off by
                       default: a fork's code is written by people outside the repository, and a preview runs it on
                       this server); the instance follows the fork's branch and belongs to the fork's own project
                       (made for it, creating nothing by itself), so it never mixes with the project's branches

New previews count against the project's pr_preview_limit (PROJECT_PR_PREVIEW_LIMIT, 0: no limit), the server's
MAX_INSTANCES and its running limit (capacity), and are refused while the panel is past its days to link. A refused
preview is listed with the reason. The panel writes one comment on the pull request with the preview's link (it needs
the GitHub App's Pull requests or Issues: Read and write; without it, no comment, the preview works the same) and
changes that comment when a build of the preview starts and ends. A new commit on a same-repository pull request
updates the branch's instance like any push; a fork's preview is updated when its pull request is.

When the pull request is closed or merged, a preview the panel created is deleted, without a backup (its data is
throw-away); an instance that existed before the pull request is left alone.

Each project keeps its previews in its document: "previews": {number: {"number", "title", "url", "branch",
"repository", "head_sha", "fork", "instance", "existing", "comment_id", "status", "reason", "opened_at"}}.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from sudoo import link_deadline, projects
from sudoo.config import settings
from sudoo.core import audit, events, jobs
from sudoo.core.jobs import Job
from sudoo.instances import capacity, operations, planning, stages, store
from sudoo.limits import quotas
from sudoo.projects import build_options
from sudoo.repos import github, github_app

KEY = "previews"
OPENING = ("opened", "reopened", "synchronize", "ready_for_review")
MARKER = "<!-- sudoo-preview -->"


# ---------------------------------------------------------------- records


def records(project: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(k): dict(v) for k, v in (project.get(KEY) or {}).items()}


def _save(project_id: str, number: int, values: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Change (merge `values` into) or remove (None) the record of pull request `number`; the record as kept."""
    kept: dict[str, Any] = {}

    def update(project: dict[str, Any]) -> dict[str, Any]:
        found = records(project)
        if values is None:
            found.pop(str(number), None)
        else:
            found[str(number)] = {**found.get(str(number), {}), **values}
            kept.update(found[str(number)])
        project[KEY] = found
        return project

    projects.change(project_id, update)
    events.publish("project", project_id)
    return kept or None


def preview_of(name: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(project, record) of the preview instance `name`, or None."""
    for project in projects.all_projects():
        for record in records(project).values():
            if record.get("instance") == name:
                return project, record
    return None


def created_count(project: Mapping[str, Any]) -> int:
    """Previews the panel created for the project that still exist (or are being created)."""
    return sum(
        1
        for r in records(project).values()
        if r.get("instance") and not r.get("existing") and (store.exists(r["instance"]) or jobs.creating(r["instance"]))
    )


# ---------------------------------------------------------------- the comment


def _spawn(fn: Callable[[], None]) -> None:
    """Run `fn` in the background (the tests replace this to run it at once)."""
    threading.Thread(target=fn, daemon=True, name="pr-comment").start()


def _link(name: str) -> str | None:
    try:
        from sudoo import exposure

        return exposure.address(name)
    except Exception:  # noqa: BLE001 - a comment without a link is still worth writing
        return None


def _panel_link(name: str) -> str | None:
    try:
        from sudoo import exposure

        base = exposure.address(exposure.PANEL)
    except Exception:  # noqa: BLE001
        base = None
    return f"{base.rstrip('/')}/instances/{name}" if base else None


STATUS_TEXT = {
    "creating": "Being created",
    "building": "Building",
    "ready": "Ready",
    "failed": "The latest build failed",
    "deleted": "Deleted: the pull request was closed",
    "refused": "No preview",
}


def comment_body(record: Mapping[str, Any]) -> str:
    """The text of the panel's comment on a pull request (GitHub Markdown)."""
    name = record.get("instance") or ""
    status = str(record.get("status") or "creating")
    lines = [MARKER, "**Sudoo.sh preview**", ""]
    if status == "refused":
        lines.append(f"No preview: {record.get('reason') or 'refused'}.")
        return "\n".join(lines)
    link = _link(name) if status != "deleted" else None
    lines.append(f"Instance: `{name}`" + (f" - {link}" if link else ""))
    sha = str(record.get("head_sha") or "")[:7]
    lines.append(f"Status: {STATUS_TEXT.get(status, status)}" + (f" (commit `{sha}`)" if sha else ""))
    if record.get("result") and status in ("failed", "ready"):
        lines.append(f"Result: {str(record['result'])[:300]}")
    panel = _panel_link(name) if status != "deleted" else None
    if panel:
        lines.append(f"In the panel: {panel}")
    return "\n".join(lines)


def _comment(project: Mapping[str, Any], number: int) -> None:
    """Write or change the panel's comment on the pull request, in the background; never fails anything."""
    project_id = str(project["id"])
    repository = str(project["repository"])

    def post() -> None:
        found = records(projects.get(project_id) or {}).get(str(number))
        if not found:
            return
        try:
            comment_id = github_app.comment(repository, number, comment_body(found), found.get("comment_id"))
        except Exception as error:  # noqa: BLE001 - GitHub refusing never stops a preview
            print(f"previews: comment on {repository}#{number} not written: {error}", flush=True)
            return
        if comment_id != found.get("comment_id"):
            _save(project_id, number, {"comment_id": comment_id})

    _spawn(post)


# ---------------------------------------------------------------- opening


def refusal(project: Mapping[str, Any]) -> str | None:
    """Why no new preview instance may be created for the project now, or None."""
    if link_deadline.restricted():
        return "the panel is not linked to a Sudoo.sh account and the days to link it have passed"
    limit = int(projects.option(project, "pr_preview_limit"))
    if limit and created_count(project) >= limit:
        return f"the project has {limit} {'preview' if limit == 1 else 'previews'} already (its limit)"
    if settings.max_instances and len(store.names()) + len(jobs.being_created()) >= settings.max_instances:
        return f"the server has its maximum of {settings.max_instances} instances"
    return capacity.refusal()


def _pull_facts(pull: Mapping[str, Any], project: Mapping[str, Any]) -> dict[str, Any] | None:
    head = pull.get("head") or {}
    head_repository = str((head.get("repo") or {}).get("full_name") or "")
    branch = str(head.get("ref") or "")
    try:
        number = int(pull.get("number") or 0)
    except (TypeError, ValueError):
        return None
    if number <= 0 or not github.valid_branch(branch) or not github.repo_name(head_repository):
        return None
    return {
        "number": number,
        "title": str(pull.get("title") or "")[:200],
        "url": str(pull.get("html_url") or f"https://github.com/{project['repository']}/pull/{number}")[:300],
        "branch": branch,
        "repository": head_repository,
        "head_sha": str(head.get("sha") or "")[:40],
        "fork": head_repository.lower() != str(project["repository_id"]),
    }


def _create(project: Mapping[str, Any], facts: Mapping[str, Any]) -> str:
    """Start the creation of the preview instance; its name."""
    stage = stages.DEVELOPMENT
    fork_project: str | None = None
    if facts["fork"]:
        existed = projects.by_repository(facts["repository"])
        found = projects.for_repository(facts["repository"], project["odoo_version"], project["edition"], "system")
        fork_project = str(found["id"])
        if existed is None:  # made for this preview: it never creates instances by itself
            projects.change(fork_project, lambda p: {**p, "seen": [], "options": {"auto_development": False}})
    plan = planning.build(
        repo=f"https://github.com/{facts['repository']}",
        branch=facts["branch"],
        version=projects.branch_version(project, facts["branch"]),
        edition=project["edition"],
        stage=stage,
        user="system",
        switches={**stages.defaults(stage), "delete_after_days": projects.lifetime_days(project, stage)},
        demo=bool(build_options.effective(project, facts["branch"], stage)["demo_data"]),
        install_modules=True,
        project=fork_project or str(project["id"]),
    )
    from sudoo.premium import servers

    servers.start_planned(plan, "system", str(project.get("default_server") or ""))
    return str(plan["name"])


def opened(project: Mapping[str, Any], pull: Mapping[str, Any], action: str = "opened") -> dict[str, Any]:
    """A pull request opened (or got new commits): its preview, created when needed. {"preview": instance or None,
    "reason"?}."""
    if not projects.option(project, "pr_previews"):
        return {"preview": None, "reason": "pull-request previews are off for this project"}
    facts = _pull_facts(pull, project)
    if facts is None:
        return {"preview": None, "reason": "not a pull request the panel can preview"}
    number = int(facts["number"])
    if facts["fork"] and not projects.option(project, "pr_previews_forks"):
        audit.record("system", None, "no preview for a pull request from a fork",
                     detail=f"{project['id']} #{number} {facts['repository']}")  # fmt: skip
        return {"preview": None, "reason": "pull requests from forks get no preview in this project"}
    with quotas.LOCK, projects.LOCK:
        project = projects.get(str(project["id"])) or project
        known = records(project).get(str(number))
        name = (known or {}).get("instance")
        if name and (store.exists(name) or jobs.creating(name)):
            changed = facts["head_sha"] and facts["head_sha"] != known.get("head_sha")  # type: ignore[union-attr]
            _save(str(project["id"]), number, {**facts, "reason": None})
            if changed and facts["fork"] and action == "synchronize":
                from sudoo import triggers

                triggers.request_update(name, f"pull request #{number} updated")
            if not (known or {}).get("comment_id"):
                _comment(project, number)
            return {"preview": name}
        members = [] if facts["fork"] else projects.by_branch(str(project["id"])).get(facts["branch"], [])
        if members:
            name, existing, status = members[0], True, "ready"
        else:
            refused = refusal(project)
            if refused:
                if (known or {}).get("reason") != refused:
                    _save(str(project["id"]), number, {**facts, "instance": None, "existing": False,
                                                        "status": "refused", "reason": refused,
                                                        "opened_at": time.time()})  # fmt: skip
                    audit.record("system", None, "no preview for a pull request",
                                 detail=f"{project['id']} #{number}: {refused}")  # fmt: skip
                    _comment(project, number)
                return {"preview": None, "reason": refused}
            name, existing, status = _create(project, facts), False, "creating"
        _save(str(project["id"]), number, {**facts, "instance": name, "existing": existing, "status": status,
                                            "reason": None, "opened_at": time.time()})  # fmt: skip
    audit.record("system", None, "pull-request preview", name,
                 detail=f"{project['id']} #{number} {facts['branch']}{' (existing)' if existing else ''}")  # fmt: skip
    _comment(project, number)
    return {"preview": name, "existing": existing}


# ---------------------------------------------------------------- closing


def closed(project: Mapping[str, Any], number: int) -> dict[str, Any]:
    """A pull request was closed or merged: its preview is deleted (without a backup) when the panel created it."""
    found = records(project).get(str(number))
    if not found:
        return {"deleted": None}
    name = found.get("instance")
    deleted = None
    if name and not found.get("existing") and (store.exists(name) or jobs.creating(name)):
        jobs.start(name, "Delete (pull request closed)", "delete", operations.delete, name, False, True)
        deleted = name
        audit.record("system", None, "pull-request preview deleted", name, detail=f"{project['id']} #{number}")
    if found.get("comment_id"):
        _save(str(project["id"]), number, {"status": "deleted"})
        _comment(project, number)

        def drop() -> None:  # after the comment is written
            _save(str(project["id"]), number, None)

        _spawn(drop)
    else:
        _save(str(project["id"]), number, None)
    return {"deleted": deleted}


# ---------------------------------------------------------------- events and the fallback


def pull_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A "pull_request" event of the GitHub App's webhook."""
    action = str(payload.get("action") or "")
    pull = payload.get("pull_request") or {}
    repository = str((payload.get("repository") or {}).get("full_name") or "")
    project = projects.by_repository(repository) if repository else None
    if project is None or not isinstance(pull, Mapping):
        return {"preview": None, "reason": "no project for this repository"}
    if action in OPENING:
        if pull.get("state") == "closed":
            return closed(project, int(pull.get("number") or 0))
        return opened(project, pull, action)
    if action == "closed":
        return closed(project, int(pull.get("number") or 0))
    return {"preview": None, "reason": f"the {action or 'unknown'} action changes no preview"}


def poll(project: Mapping[str, Any]) -> dict[str, list[int]]:
    """Compare the project's open pull requests on GitHub with its previews (when webhooks did not arrive)."""
    report: dict[str, list[int]] = {"opened": [], "closed": []}
    if not projects.option(project, "pr_previews") or not github_app.connected():
        return report
    try:
        pulls = github_app.open_pulls(str(project["repository"]))
    except github_app.GitHubError:
        return report  # no access to pull requests, or GitHub not reachable: nothing is closed by mistake
    known = records(project)
    open_numbers = set()
    for pull in pulls:
        number = int(pull.get("number") or 0)
        open_numbers.add(number)
        before = known.get(str(number))
        sha = str((pull.get("head") or {}).get("sha") or "")
        if before is None or (before.get("status") == "refused") or sha != before.get("head_sha"):
            opened(project, pull, "opened" if before is None else "synchronize")
            report["opened"].append(number)
    for number in sorted(int(n) for n in known if int(n) not in open_numbers):
        closed(projects.get(str(project["id"])) or project, number)
        report["closed"].append(number)
    return report


# ---------------------------------------------------------------- builds of previews


def _job_changed(job: Job, ended: bool) -> None:
    if job.kind not in ("create", "update", "restore"):
        return
    found = preview_of(job.instance)
    if found is None:
        return
    project, record = found
    if ended:
        status = "ready" if job.status == "success" else "failed"
        values: dict[str, Any] = {"status": status, "result": (job.hint or "")[:300] if status == "failed" else ""}
        if store.exists(job.instance):
            from sudoo.repos.gitrepo import head

            sha = head(store.addons_dir(job.instance))
            if sha:
                values["head_sha"] = sha
    else:
        values = {"status": "building"}
    _save(str(project["id"]), int(record["number"]), values)
    _comment(project, int(record["number"]))


def job_started(job: Job) -> None:
    try:
        _job_changed(job, False)
    except Exception as error:  # noqa: BLE001 - a preview's comment never stops a job
        print(f"previews: {job.instance} not followed: {error!r}", flush=True)


def job_ended(job: Job) -> None:
    try:
        _job_changed(job, True)
    except Exception as error:  # noqa: BLE001
        print(f"previews: {job.instance} not followed: {error!r}", flush=True)


def listen() -> None:
    """Follow the jobs of previews (called once when the panel starts)."""
    jobs.on_start(job_started)
    jobs.on_end(job_ended)
