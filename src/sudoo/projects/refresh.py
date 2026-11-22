"""Scheduled refresh of staging from production: a project's staging instances rebuilt from a new copy of production
every day or every week, at a chosen time.

Each project keeps its schedule in its document ("staging_refresh"):

    frequency   "off", "daily" or "weekly"
    time        "HH:MM" in the panel's TIMEZONE
    weekday     0 (Monday) to 6 (Sunday), for "weekly"
    branches    the staging branches refreshed; empty: every staging branch of the project
    last_run    when the schedule last looked (set when it is saved, so a time already past today waits for the next one)
    refreshed_at  when staging was last refreshed (a scheduled run or Refresh now that started a rebuild); shown as the
                last refresh, which saving the schedule never is
    changed_by, changed_at

When a slot comes (the loop looks every CHECK_SECONDS), every staging instance of the chosen branches is rebuilt the
way "Rebuild from production" does it (projects/staging.py: a new neutralised copy of production, the branch's latest
code, its modules upgraded). An instance is skipped, with the reason, when its branch or production's branch is
locked, it is busy with another job, it cannot be rebuilt (no production, another Odoo version) or production's data
would reach people production's branch is hidden from. Each branch's history gets a "refresh" entry saying what was
started and what was skipped; the rebuild itself is a build of the branch as usual. The entry is published as a live
"build" event, which the project's notification channels receive as "staging_refreshed" (notifications/), and the
run is audit-logged.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sudoo import link_deadline, projects
from sudoo.config import settings
from sudoo.core import audit, events, jobs
from sudoo.instances import stages, store
from sudoo.projects import access, builds, staging

KEY = "staging_refresh"
FREQUENCIES = ("off", "daily", "weekly")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
CHECK_SECONDS = 60
ACTION = "Scheduled refresh from production"
DEFAULT: dict[str, Any] = {"frequency": "off", "time": "03:00", "weekday": 6, "branches": []}


def schedule(project: Mapping[str, Any]) -> dict[str, Any]:
    """The project's schedule, with the defaults for what it has not set."""
    return {**DEFAULT, **(project.get(KEY) or {})}


def clean(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A schedule as sent by someone, checked; raises ValueError with a message for people."""
    frequency = raw.get("frequency")
    if frequency not in FREQUENCIES:
        raise ValueError(f"frequency: {', '.join(FREQUENCIES)}.")
    at = raw.get("time", DEFAULT["time"])
    if not isinstance(at, str) or not TIME_RE.match(at):
        raise ValueError('time: "HH:MM" (00:00 to 23:59).')
    weekday = raw.get("weekday", DEFAULT["weekday"])
    if not isinstance(weekday, int) or isinstance(weekday, bool) or not 0 <= weekday <= 6:
        raise ValueError("weekday: 0 (Monday) to 6 (Sunday).")
    chosen = raw.get("branches", [])
    if not isinstance(chosen, list) or len(chosen) > 50 or not all(isinstance(b, str) for b in chosen):
        raise ValueError("branches: a list of branch names (at most 50); empty: every staging branch.")
    from sudoo.repos import github

    for branch in chosen:
        if not github.valid_branch(branch):
            raise ValueError(f"Not a branch name: {branch[:60]!r}.")
    return {"frequency": frequency, "time": at, "weekday": weekday, "branches": sorted(set(chosen))}


def save(project_id: str, values: Mapping[str, Any], user: str, now: float | None = None) -> dict[str, Any]:
    """Keep a project's schedule (already cleaned); the slots already past wait for the next one."""
    stamp = time.time() if now is None else now

    def update(project: dict[str, Any]) -> dict[str, Any]:
        kept = {"refreshed_at": project[KEY]["refreshed_at"]} if (project.get(KEY) or {}).get("refreshed_at") else {}
        project[KEY] = {**values, **kept, "last_run": stamp, "changed_by": user, "changed_at": int(stamp)}
        return project

    found = projects.change(project_id, update) or {}
    return schedule(found)


def last_slot(found: Mapping[str, Any], now: float) -> float | None:
    """When the latest slot at or before `now` was (a timestamp), or None when the schedule is off."""
    if found["frequency"] not in ("daily", "weekly"):
        return None
    hours, minutes = (int(part) for part in str(found["time"]).split(":"))
    local = datetime.fromtimestamp(now, settings.zone)
    slot = local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    if slot > local:
        slot -= timedelta(days=1)
    if found["frequency"] == "weekly":
        slot -= timedelta(days=(slot.weekday() - int(found["weekday"])) % 7)
    return slot.timestamp()


def next_run(found: Mapping[str, Any], now: float | None = None) -> float | None:
    """When the next slot after `now` is, or None when the schedule is off."""
    now = time.time() if now is None else now
    last = last_slot(found, now)
    if last is None:
        return None
    step = 7 if found["frequency"] == "weekly" else 1
    return (datetime.fromtimestamp(last, settings.zone) + timedelta(days=step)).timestamp()


def due(project: Mapping[str, Any], now: float) -> bool:
    found = schedule(project)
    slot = last_slot(found, now)
    return slot is not None and float(found.get("last_run") or 0) < slot


def targets(project: Mapping[str, Any]) -> dict[str, list[str]]:
    """{staging branch: its staging instances} the schedule refreshes."""
    chosen = set(schedule(project)["branches"])
    grouped = projects.by_branch(project["id"])
    found: dict[str, list[str]] = {}
    for branch, members in sorted(grouped.items()):
        if not branch or (chosen and branch not in chosen):
            continue
        if projects.branch_stage(project, branch, members) != stages.STAGING:
            continue
        found[branch] = [n for n in members if store.exists(n) and stages.of(store.read_env(n)) == stages.STAGING]
    return found


def skip_reason(project: Mapping[str, Any], branch: str, name: str) -> str | None:
    """Why instance `name` of `branch` is not refreshed now, or None."""
    if access.lock_of(project, branch) is not None:
        return "the branch is locked"
    production = staging.production_instance(project)
    production_branch = projects.branch_of(production) if production else None
    if production_branch and access.lock_of(project, production_branch) is not None:
        return "production's branch is locked"
    if jobs.busy(name):
        return "it is busy with another job"
    refused = staging.rebuild_refusal(name)
    if refused:
        return refused
    if production_branch and access.copy_reveals(project, production_branch, branch):
        return "production's data would reach people production's branch is hidden from"
    return None


def run(project: Mapping[str, Any], user: str = "system") -> dict[str, Any]:
    """Refresh the project's chosen staging instances now; {"started": [...], "skipped": [{"instance", "reason"}]}."""
    started: list[str] = []
    skipped: list[dict[str, str]] = []
    restricted = link_deadline.restricted()
    for branch, members in targets(project).items():
        mine_started: list[str] = []
        mine_skipped: list[dict[str, str]] = []
        for name in members:
            reason = (
                "the panel is not linked to a Sudoo.sh account and the days to link it have passed"
                if restricted
                else skip_reason(project, branch, name)
            )
            if reason:
                mine_skipped.append({"instance": name, "reason": reason})
                continue
            jobs.start(name, ACTION, "restore", staging.rebuild, name, user=user, details={"build": "rebuild"})
            mine_started.append(name)
        if not members:
            mine_skipped.append({"instance": "", "reason": "the branch has no staging instance"})
        _record(project, branch, mine_started, mine_skipped, user)
        started += mine_started
        skipped += mine_skipped
    if started:

        def refreshed(found: dict[str, Any]) -> dict[str, Any]:
            found[KEY] = {**schedule(found), "refreshed_at": time.time()}
            return found

        projects.change(str(project["id"]), refreshed)
    audit.record(user, None, "staging refreshed from production", detail=(
        f"{project['id']}: started {', '.join(started) or 'none'}"
        + (f"; skipped {', '.join(s['instance'] + ' (' + s['reason'] + ')' for s in skipped)}" if skipped else "")
    )[:300])  # fmt: skip
    events.publish("project", str(project["id"]))
    return {"started": started, "skipped": skipped}


def _record(project: Mapping[str, Any], branch: str, started: list[str], skipped: list[dict[str, str]],
            user: str) -> None:  # fmt: skip
    if started:
        result = f"Staging refreshed from production: {', '.join(started)} rebuilt from a new copy."
    else:
        result = "Staging not refreshed"
    if skipped:
        result += (" " if started else ": ") + "; ".join(
            (f"{s['instance']}: " if s["instance"] else "") + s["reason"] for s in skipped
        ) + "."  # fmt: skip
    elif not started:
        result += "."
    builds.add_entry(
        {
            "type": "refresh",
            "project": str(project["id"]),
            "branch": branch,
            "by": user,
            "instance": started[0] if started else (skipped[0]["instance"] if skipped else ""),
            "status": "success" if started else "failed",
            "action": ACTION,
            "result": result[:1000],
            "started": started,
            "skipped": skipped,
        }
    )


def run_due(now: float | None = None) -> list[str]:
    """Refresh every project whose slot has come; the ids of those refreshed."""
    now = time.time() if now is None else now
    done: list[str] = []
    for project in projects.all_projects():
        if not due(project, now):
            continue

        def mark(found: dict[str, Any]) -> dict[str, Any]:
            found[KEY] = {**schedule(found), "last_run": now}
            return found

        projects.change(str(project["id"]), mark)  # before running: a failure never repeats it every minute
        try:
            run(project)
        except Exception as error:  # noqa: BLE001 - one project's trouble never stops the others
            print(f"projects: staging of {project['id']} not refreshed: {error!r}", flush=True)
            continue
        done.append(str(project["id"]))
    return done


def run_forever() -> None:
    """Background loop: refresh the projects whose slot has come, every CHECK_SECONDS."""
    while True:
        time.sleep(CHECK_SECONDS)
        try:
            run_due()
        except Exception as error:  # noqa: BLE001 - the loop keeps going
            print(f"projects: staging refresh check failed: {error!r}", flush=True)
