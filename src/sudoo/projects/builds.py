"""The history of a project's branches: one build per commit an instance was built from, and what else happened.

Odoo.sh keeps a history per branch; so does the panel. A **build** is recorded when an instance of a project gets code
from its branch: created ("create"), updated by a push (an automatic update or a staging rebuild on push, "push"),
updated by someone (Get latest code, Update submodules: "manual"), or rebuilt from production by hand ("rebuild"). It
starts as "building" when its job starts and ends as

    success     the job succeeded
    warning     the job succeeded, but Odoo's log of the job has WARNING or ERROR lines (module loading), the job
                left a warning note (some modules could not be upgraded), or the tests passed with warnings
    failed      the job failed (an update put back keeps the instance on its previous build), or the build's tests
                failed ("At least one test failed when loading the modules (module_x)"; projects/testing.py)
    dropped     the instance moved on: a newer build of it succeeded, it follows another branch now, or it was deleted
                (only the latest build of an instance is live, as on Odoo.sh)

An update that found nothing new on GitHub is no build: its record is removed when its job ends.

Next to the builds, a branch's timeline has **stage** entries (who moved the branch from which stage to which, with
what the new stage does), **restore** entries (a backup restored into one of its instances, or a restore rolled
back) and **commit** entries (a commit pushed to a branch that builds nothing on new commits,
projects/build_options.py).

When a build starts and ends, its commit's status on GitHub follows (projects/commit_status.py).

Storage: collection "build_history", one document per project and branch (key "<project id>:<branch>"):
{"project", "branch", "entries": [oldest first]}. Each branch keeps its last PROJECT_BUILDS_KEPT builds (and the
timeline entries since the oldest of them). Every new or changed entry is published as a live "build" event, with the
entry as the API shows it (web/v1/projects.py).
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from sudoo import projects
from sudoo.config import settings
from sudoo.core import events, jobs
from sudoo.core.jobs import Job
from sudoo.instances import stages, store
from sudoo.repos import gitrepo
from sudoo.storage import Document, storage

COLLECTION = "build_history"
STATUSES = ("building", "success", "warning", "failed", "dropped")
LIVE = ("success", "warning")  # what a newer build of the instance drops
TRIGGERS = ("create", "push", "manual", "rebuild")
MESSAGES_KEPT = 10  # warning and error lines of Odoo's log kept with a build
STAGE_MEANING = {
    stages.DEVELOPMENT: (
        "Development: for trying changes. New instances get demo data (when the project says so), catch every e-mail "
        "and are deleted when their lifetime ends."
    ),
    stages.STAGING: (
        "Staging: for testing with real data. The instance runs on a neutralised copy of production's data (rebuilt "
        "on push when the project says so) and catches every e-mail."
    ),
    stages.PRODUCTION: (
        "Production: the live database. The instance is kept, never paused when idle, backed up every day, and "
        "sends real e-mail."
    ),
}
# Odoo's log lines: "2026-11-22 10:00:00,123 7 WARNING acme odoo.modules.loading: ..."
_ODOO_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ \d+ (WARNING|ERROR|CRITICAL) \S+ (\S+): (.*)$")

_lock = threading.Lock()
_started: dict[str, dict[str, Any]] = {}  # job id -> what the job started with (project, branch, build, code)
_pending: dict[str, list[str]] = {}  # job id -> commits marked "pending" on GitHub (projects/commit_status.py)


# ---------------------------------------------------------------- the documents


def _key(project_id: str, branch: str) -> str:
    return f"{project_id}:{branch}"


def _limit() -> int:
    return max(1, int(settings.project_builds_kept))


def _trimmed(entries: list[Document]) -> list[Document]:
    """The entries a branch keeps: its last PROJECT_BUILDS_KEPT builds and the timeline entries since the oldest of
    them (at most as many again when the branch has fewer builds)."""
    keep = _limit()
    builds = [e for e in entries if e["type"] == "build"]
    if len(builds) > keep:
        oldest = builds[-keep]
        entries = entries[entries.index(oldest) :]
    others = [e for e in entries if e["type"] != "build"]
    if len(others) > keep:
        drop = {e["id"] for e in others[:-keep]}
        entries = [e for e in entries if e["id"] not in drop]
    return entries


def _change_branch(project_id: str, branch: str, fn: Callable[[list[Document]], list[Document]]) -> list[Document]:
    """Atomically change a branch's entries; returns the entries fn changed or added (for the live events)."""
    touched: list[Document] = []

    def apply(found: Document | None) -> Document | None:
        doc = found or {"project": project_id, "branch": branch, "entries": []}
        before = {e["id"]: dict(e) for e in doc["entries"]}
        entries = _trimmed(fn([dict(e) for e in doc["entries"]]))
        touched.extend(e for e in entries if before.get(e["id"]) != e)
        return {**doc, "entries": entries} if entries else None

    with _lock:
        storage().change(COLLECTION, _key(project_id, branch), apply)
    for entry in touched:
        events.publish("build", entry["id"], None)
    return touched


def _add(entry: Document) -> Document:
    entry = {"id": uuid.uuid4().hex[:12], "at": time.time(), **entry}
    _change_branch(entry["project"], entry["branch"], lambda entries: [*entries, entry])
    return entry


def _update(project_id: str, branch: str, entry_id: str, **values: Any) -> None:
    def apply(entries: list[Document]) -> list[Document]:
        return [{**e, **values} if e["id"] == entry_id else e for e in entries]

    _change_branch(project_id, branch, apply)


def _remove(project_id: str, branch: str, entry_id: str) -> None:
    _change_branch(project_id, branch, lambda entries: [e for e in entries if e["id"] != entry_id])
    events.publish("build", entry_id, {"id": entry_id, "project": project_id, "branch": branch, "removed": True})


def histories(project_id: str) -> dict[str, list[Document]]:
    """{branch: entries, oldest first} of a project."""
    prefix = f"{project_id}:"
    return {
        str(doc["branch"]): list(doc.get("entries") or [])
        for key, doc in storage().all(COLLECTION).items()
        if key.startswith(prefix)
    }


def history(project_id: str, branch: str) -> list[Document]:
    """A branch's entries, newest first."""
    doc = storage().get(COLLECTION, _key(project_id, branch)) or {}
    return list(reversed(doc.get("entries") or []))


def builds(project_id: str, per_branch: int) -> dict[str, list[Document]]:
    """{branch: its last `per_branch` builds, newest first} of a project (branches with at least one build)."""
    found: dict[str, list[Document]] = {}
    for branch, entries in histories(project_id).items():
        shown = [e for e in reversed(entries) if e["type"] == "build"][:per_branch]
        if shown:
            found[branch] = shown
    return found


def find(entry_id: str, project_id: str | None = None) -> Document | None:
    """An entry by its id (in one project when given), or None."""
    for key, doc in storage().all(COLLECTION).items():
        if project_id is not None and not key.startswith(f"{project_id}:"):
            continue
        for entry in doc.get("entries") or []:
            if entry["id"] == entry_id:
                return dict(entry)
    return None


def forget_project(project_id: str) -> None:
    """Remove the history of a deleted project."""
    for key in [k for k in storage().all(COLLECTION) if k.startswith(f"{project_id}:")]:
        storage().delete(COLLECTION, key)


# ---------------------------------------------------------------- timeline entries


def stage_changed(project_id: str, branch: str, before: str, after: str, user: str, reason: str = "") -> None:
    """Record that a branch moved from stage `before` to `after` (nothing when they are the same)."""
    if not project_id or not branch or before == after:
        return
    _add(
        {
            "type": "stage",
            "project": project_id,
            "branch": branch,
            "by": user,
            "from": before,
            "to": after,
            "explanation": STAGE_MEANING.get(after, ""),
            **({"reason": reason} if reason else {}),
        }
    )


NOT_BUILT = "New commit; this branch builds nothing on new commits (its Behaviour upon new commits)."


def commit_recorded(project_id: str, branch: str, instance: str, sha: str) -> bool:
    """Record a commit pushed to a branch that builds nothing on new commits (build_options on_new_commit
    "nothing"): a "commit" entry, once per commit. True when it was recorded now."""
    if not project_id or not branch or not sha:
        return False
    entries = history(project_id, branch)
    if any(e["type"] in ("commit", "build") and (e.get("commit") or {}).get("full_sha") == sha for e in entries[:20]):
        return False
    _add(
        {
            "type": "commit",
            "project": project_id,
            "branch": branch,
            "instance": instance,
            "by": "system",
            "commit": {"sha": sha[:7], "full_sha": sha, "subject": "", "author": "", "when": ""},
            "status": "skipped",
            "result": NOT_BUILT,
        }
    )
    return True


# ---------------------------------------------------------------- builds from jobs


def _where(job: Job) -> tuple[str, str]:
    """(project id, branch) of the job's instance; ("", "") outside every project."""
    if job.kind == "create" and not store.exists(job.instance):
        return str(job.details.get("project") or ""), str(job.details.get("branch") or "")
    if not store.exists(job.instance):
        return "", ""
    env = store.read_env(job.instance)
    return env.get("PROJECT", ""), env.get("GIT_BRANCH", "")


def _code(name: str) -> dict[str, str] | None:
    """The commits checked out in the instance (repository and submodules), or None before it has any."""
    if not store.exists(name) or not (store.addons_dir(name) / ".git").exists():
        return None
    return gitrepo.snapshot(store.addons_dir(name))


def job_started(job: Job) -> None:
    """jobs.on_start: a job that builds an instance of a project (job.details["build"]: its trigger) starts a build."""
    if jobs.is_panel_job(job.instance):
        return
    project_id, branch = _where(job)
    if not project_id or projects.get(project_id) is None:
        return
    found: dict[str, Any] = {"project": project_id, "branch": branch}
    trigger = job.details.get("build")
    if trigger in TRIGGERS and branch:
        _drop_unfinished(project_id, job.instance)
        entry = _add(
            {
                "type": "build",
                "project": project_id,
                "branch": branch,
                "instance": job.instance,
                "trigger": trigger,
                "status": "building",
                "by": job.user,
                "job": job.id,
                "action": job.action,
                "queued_at": job.started,
                "started_at": time.time(),
                "finished_at": None,
                "commit": None,
                "result": "",
                "messages": [],
            }
        )
        found |= {"build": entry["id"], "code": _code(job.instance)}
        _report_start(job, project_id, branch, str(trigger), (found["code"] or {}).get("."))
    with _lock:
        _started[job.id] = found


def _report_start(job: Job, project_id: str, branch: str, trigger: str, before: str | None) -> None:
    """ "pending" on GitHub for the commit the build is about to build (projects/commit_status.py): the branch's head
    on GitHub, unless an update would find nothing new there (the instance has that commit already)."""
    from sudoo.projects import branches as project_branches
    from sudoo.projects import commit_status

    def sha_of() -> str | None:
        project = projects.get(project_id)
        if project is None:
            return None
        heads, _, error = project_branches.heads(project, refresh=True)
        sha = None if error else heads.get(branch)
        if not sha or (trigger in ("push", "manual") and sha == before):
            return None
        with _lock:
            _pending.setdefault(job.id, []).append(sha)
        return sha

    commit_status.started(project_id, branch, _stage(project_id, branch, job.instance), sha_of)


def _stage(project_id: str, branch: str, instance: str) -> str:
    """The stage a build is reported under: its instance's, else its branch's."""
    if store.exists(instance):
        return stages.of(store.read_env(instance))
    project = projects.get(project_id)
    return projects.branch_stage(project, branch) if project else stages.DEVELOPMENT


def odoo_messages(log: str) -> tuple[int, int, list[str]]:
    """(warnings, errors, the first lines) of Odoo's log lines in a job's log."""
    warnings = errors = 0
    lines: list[str] = []
    for line in log.splitlines():
        match = _ODOO_LINE.match(line.strip())
        if not match:
            continue
        if match[1] == "WARNING":
            warnings += 1
        else:
            errors += 1
        if len(lines) < MESSAGES_KEPT:
            lines.append(f"{match[1]} {match[2]}: {match[3]}"[:300])
    return warnings, errors, lines


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


DONE = {
    "create": "Instance created",
    "push": "Built from the pushed commit",
    "manual": "Built from the latest commit",
    "rebuild": "Rebuilt from a new copy of production",
}


AT_LEAST_ONE = "At least one test failed when loading the modules."


def outcome(job: Job, trigger: str) -> tuple[str, str, list[str]]:
    """(status, result, messages) of a finished build job. The build's tests (projects/testing.py,
    job.details["tests"]) come first; Odoo's log outside the tests says whether it built with warnings."""
    if job.status == "failed":
        return "failed", (job.hint or "The build failed; the job's log says why.")[:500], []
    tests = job.details.get("tests") or {}
    if tests.get("status") == "failed":
        return "failed", str(tests.get("result") or AT_LEAST_ONE)[:500], list(tests.get("messages") or [])
    log = job.log
    span = job.details.get("tests_log")
    if span:  # the tests' own warnings and errors are the tests' result, not the build's
        log = [*job.log[: span[0]], *job.log[span[1] :]]
    warnings, errors, lines = odoo_messages("".join(log))
    noted = [n["text"] for n in job.notes if n.get("level") == "warning" and n["text"] != tests.get("result")]
    if warnings or errors:
        parts = [_plural(n, w) for n, w in ((warnings, "warning"), (errors, "error")) if n]
        return "warning", f"{' and '.join(parts)} in Odoo's log while loading the modules", lines
    if noted:
        return "warning", noted[0][:500], []
    if tests.get("status") == "warning":
        return "warning", str(tests.get("result"))[:500], list(tests.get("messages") or [])
    done = DONE.get(trigger, "Built")
    return "success", f"{done}. {tests['result']}" if tests.get("result") else done, []


def job_ended(job: Job) -> None:
    """jobs.on_end: finish the job's build; record restores; drop builds of instances that moved on or are gone."""
    with _lock:
        found = _started.pop(job.id, None)
    if not found:
        return
    project_id, branch = found["project"], found["branch"]
    build_id = found.get("build")
    if build_id:
        _finish(job, project_id, branch, build_id, found.get("code"))
    elif job.kind == "restore":
        status, result, _ = outcome(job, "")
        _add(
            {
                "type": "restore",
                "project": project_id,
                "branch": branch,
                "instance": job.instance,
                "by": job.user,
                "job": job.id,
                "action": job.action,
                "status": status if status == "failed" else "success",
                "result": result if status == "failed" else job.action,
            }
        )
    if job.kind == "delete" and job.status == "success":
        _drop(project_id, job.instance, keep=None, branch=None)
    elif job.kind == "update" and not build_id and store.exists(job.instance):  # switch branch, connect, images
        _drop(project_id, job.instance, keep=None, branch=store.read_env(job.instance).get("GIT_BRANCH", ""))


def _finish(job: Job, project_id: str, branch: str, build_id: str, code_before: dict[str, str] | None) -> None:
    trigger = str(job.details.get("build"))
    code_after = _code(job.instance)
    if job.status == "success" and job.kind == "update" and code_before is not None and code_after == code_before:
        _remove(project_id, branch, build_id)  # nothing new on GitHub: no build
        with _lock:
            pending = _pending.pop(job.id, [])
        if pending:  # never left "pending" on GitHub
            from sudoo.projects import commit_status

            stage = _stage(project_id, branch, job.instance)
            commit_status.finished(project_id, branch, stage, "success", "Nothing new to build", pending)
        return
    commit = None
    if store.exists(job.instance):  # a failed update was put back: the commit it tried is the one fetched
        commit = gitrepo.commit(store.addons_dir(job.instance), "@{upstream}" if job.status == "failed" else "HEAD")
    status, result, messages = outcome(job, trigger)
    _update(
        project_id,
        branch,
        build_id,
        status=status,
        result=result,
        messages=messages,
        finished_at=job.finished or time.time(),
        **({"commit": commit} if commit else {}),
    )
    if status in LIVE:
        _drop(project_id, job.instance, keep=build_id, branch=None)
    from sudoo.projects import commit_status

    with _lock:
        pending = _pending.pop(job.id, [])
    shas = [sha for sha in [str((commit or {}).get("full_sha") or ""), *pending] if sha]
    commit_status.finished(
        project_id, branch, _stage(project_id, branch, job.instance), status, result, shas,
        tests_failed=(job.details.get("tests") or {}).get("status") == "failed",
    )  # fmt: skip


def _drop(project_id: str, instance: str, keep: str | None, branch: str | None) -> None:
    """Mark the live (or unfinished) builds of `instance` dropped: every one but `keep`; with `branch`, only those of
    other branches (the instance follows `branch` now)."""
    now = time.time()
    for other in histories(project_id):
        if branch is not None and other == branch:
            continue

        def apply(entries: list[Document]) -> list[Document]:
            return [
                (
                    {**e, "status": "dropped", "dropped_at": now}
                    if e["type"] == "build"
                    and e.get("instance") == instance
                    and e["id"] != keep
                    and e.get("status") in (*LIVE, "building")
                    else e
                )
                for e in entries
            ]

        _change_branch(project_id, other, apply)


def _drop_unfinished(project_id: str, instance: str) -> None:
    """Builds of `instance` still "building" when a new one starts were cut off (the panel restarted)."""
    _drop_building(lambda key: key.startswith(f"{project_id}:"), instance)


def _drop_building(wanted: Callable[[str], bool], instance: str | None) -> None:
    now = time.time()
    for key, doc in storage().all(COLLECTION).items():
        if not wanted(key) or not any(
            e.get("status") == "building" and (instance is None or e.get("instance") == instance)
            for e in doc.get("entries") or []
        ):
            continue

        def apply(entries: list[Document]) -> list[Document]:
            return [
                (
                    {**e, "status": "dropped", "dropped_at": now, "result": e.get("result") or CUT_OFF}
                    if e.get("status") == "building" and (instance is None or e.get("instance") == instance)
                    else e
                )
                for e in entries
            ]

        _change_branch(str(doc["project"]), str(doc["branch"]), apply)


CUT_OFF = "The panel restarted before this build finished."


def listen() -> None:
    """Follow jobs (called once when the panel starts). Builds left "building" by a restart are dropped."""
    _drop_building(lambda key: True, None)
    jobs.on_start(job_started)
    jobs.on_end(job_ended)
