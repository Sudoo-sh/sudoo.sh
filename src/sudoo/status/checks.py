"""The status checks: what each target's state is now (status/__init__.py says what is kept), run every
STATUS_CHECK_INTERVAL_SECONDS by run_forever, and what a project's Status tab and its public page show.

A production instance going down (two checks in a row) and coming back up is a notification (notifications/:
"production_down", "production_up").
"""

from __future__ import annotations

import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from sudoo.config import settings

from . import (
    DAY,
    DEGRADED,
    DOWN,
    SERVER,
    UP,
    WINDOWS,
    Change,
    Result,
    days,
    hourly_ms,
    incidents,
    meta,
    overall,
    prune,
    record,
)
from . import forget as forget_target

CONFIRM_DOWN = 2  # checks in a row an instance must fail before "production down" is notified
_LAST_PRUNE = 0.0


# ---------------------------------------------------------------- what is checked


def production_instances(project_id: str, known: Mapping[str, list[str]] | None = None) -> list[tuple[str, str]]:
    """(instance, branch) of each production instance of the project on this server (`known`: projects.index(),
    when every project is looked at in a row)."""
    from sudoo import projects
    from sudoo.instances import stages, store

    found = []
    for name in projects.instances(project_id, known):
        if store.exists(name) and stages.is_production(store.read_env(name)):
            found.append((name, projects.branch_of(name)))
    return found


def targets(project_id: str, known: Mapping[str, list[str]] | None = None) -> list[dict[str, str]]:
    """The targets a project's status shows: each production instance, its backups (and off-site copies), the
    server. {"id", "kind", "name", "branch"?}"""
    from . import offsite_last

    found: list[dict[str, str]] = []
    for name, branch in production_instances(project_id, known):
        found.append({"id": f"instance:{name}", "kind": "instance", "name": name, "branch": branch})
        found.append({"id": f"backups:{name}", "kind": "backups", "name": name, "branch": branch})
        if settings.offsite_backups and offsite_last is not None:
            found.append({"id": f"offsite:{name}", "kind": "offsite", "name": name, "branch": branch})
    found.append({"id": SERVER, "kind": "server", "name": "server"})
    return found


# ---------------------------------------------------------------- the checks


def _gateway_address(name: str) -> str:
    from sudoo import exposure
    from sudoo.instances import store

    provider = exposure.provider()
    host = str(provider.listen_host or "127.0.0.1")
    if host in ("0.0.0.0", "::", ""):  # noqa: S104 - compared, not bound
        host = "127.0.0.1"
    scheme = "https" if getattr(provider, "gateway_tls", False) else "http"
    port = store.read_env(name)["GATEWAY_PORT"]
    return f"{scheme}://{host}:{port}/web/health"


_NO_VERIFY = ssl.create_default_context()
_NO_VERIFY.check_hostname = False
_NO_VERIFY.verify_mode = ssl.CERT_NONE  # the gateway's certificate names the public address, not 127.0.0.1


def check_instance(name: str) -> Result:
    """GET /web/health through the instance's gateway, as a visitor would (it is not counted as a visit)."""
    from sudoo.core import jobs
    from sudoo.core.state import STATE

    if STATE.get(name).get("switched_off"):
        return Result(DOWN, "Switched off")
    url = _gateway_address(name)
    started = time.monotonic()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Sudoo.sh-status"})
        with urllib.request.urlopen(  # noqa: S310 - the panel's own gateway on this server
            request,
            timeout=settings.status_check_timeout_seconds,
            context=_NO_VERIFY if url.startswith("https") else None,
        ) as answer:
            answer.read(4096)
            status = int(answer.status)
    except urllib.error.HTTPError as error:
        status = error.code
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        timed_out = isinstance(reason, TimeoutError) or "timed out" in str(reason)
        what = (
            f"No answer within {settings.status_check_timeout_seconds:g} seconds"
            if timed_out
            else "The gateway could not be reached"
        )
        return Result(DEGRADED if jobs.busy(name) else DOWN, what + (" (a job is running)" if jobs.busy(name) else ""))
    ms = round((time.monotonic() - started) * 1000, 1)
    if status >= 500:
        if jobs.busy(name):
            return Result(DEGRADED, f"HTTP {status} while a job is running", ms)
        return Result(DOWN, f"HTTP {status}", ms)
    if ms > settings.status_slow_ms:
        return Result(DEGRADED, f"Slow answer: {ms:.0f} ms", ms)
    return Result(UP, f"HTTP {status}", ms)


def check_server(now: float) -> Result:
    """The server: the monitor measured it recently (its heartbeat); disk or memory short is degraded."""
    from sudoo.instances import monitor

    history = monitor.HOST.get("history") or []
    last = history[-1][0] if history else None
    fresh = max(3 * settings.metrics_interval_seconds, 120)
    if last is None or now - last > fresh:
        return Result(DOWN, "The server's measurements stopped")
    total, used = monitor.HOST.get("disk_total"), monitor.HOST.get("disk_used") or 0
    if total and 100 * (total - used) / total < settings.alert_disk_free_percent:
        return Result(DEGRADED, f"Disk almost full: {100 * (total - used) / total:.1f}% free")
    memory_total, memory_used = monitor.HOST.get("memory_total"), monitor.HOST.get("memory_used") or 0
    if memory_total and 100 * memory_used / memory_total >= settings.capacity_warning_percent:
        return Result(DEGRADED, f"Memory high: {100 * memory_used / memory_total:.0f}% used")
    return Result(UP, "Measured")


def backup_deadline(now: float) -> float:
    """The time the latest backup must be newer than: the last daily slot whose grace period is over."""
    from sudoo.instances import backups

    slot = backups.daily_slot(now)
    if now - slot < settings.status_backup_grace_hours * 3600:
        slot -= DAY
    return slot


def check_backups(name: str, now: float) -> Result:
    """The production instance's backups: one taken since the last daily slot (plus the grace hours)."""
    from sudoo.instances import backups, store

    deadline = backup_deadline(now)
    try:
        created = float(store.read_env(name).get("CREATED_AT") or 0)
    except ValueError:
        created = 0
    newest = next(iter(backups.own_backups(name)), None)
    if newest is not None and float(newest["created"]) >= deadline:
        return Result(UP, f"Latest backup {_age(now - float(newest['created']))} ago")
    if created and created >= deadline:
        return Result(UP, "No backup due yet")
    if newest is None:
        return Result(DOWN, "No backup yet")
    return Result(DOWN, f"Latest backup {_age(now - float(newest['created']))} ago: the daily backup is late")


def check_offsite(name: str, now: float) -> Result:
    from . import offsite_last

    last = offsite_last(name) if offsite_last is not None else None
    limit = settings.alert_offsite_missing_hours * 3600
    if last is None:
        return Result(DOWN, "No off-site copy yet")
    if now - last > limit:
        return Result(DOWN, f"Latest off-site copy {_age(now - last)} ago")
    return Result(UP, f"Latest off-site copy {_age(now - last)} ago")


def _age(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min"
    if seconds < 2 * DAY:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / DAY)} days"


# ---------------------------------------------------------------- one round


def run_once(now: float | None = None) -> list[Change]:
    """Check every target of every project once (the server once); notify production going down and back up."""
    from sudoo import projects

    now = time.time() if now is None else now
    changes = [record(SERVER, check_server(now), now)]
    checked: set[str] = {SERVER}
    known = projects.index()  # every project's instances from one look, not one look per project
    for project in projects.all_projects():
        for target in targets(project["id"], known):
            if target["id"] in checked:
                continue
            checked.add(target["id"])
            name = target["name"]
            if target["kind"] == "instance":
                result = check_instance(name)
            elif target["kind"] == "backups":
                result = check_backups(name, now)
            else:
                result = check_offsite(name, now)
            change = record(target["id"], result, now)
            changes.append(change)
            if target["kind"] == "instance":
                _notify(project, target, change, result)
    _forget_gone(checked)
    return changes


_NOTIFIED_DOWN: set[str] = set()  # production instances whose "down" was notified (and not "up" yet)


def _notify(project: Mapping[str, Any], target: Mapping[str, str], change: Change, result: Result) -> None:
    from sudoo import notifications

    key = target["id"]
    found = meta(key) or {}
    if result.state == DOWN and int(found.get("fails") or 0) >= CONFIRM_DOWN and key not in _NOTIFIED_DOWN:
        _NOTIFIED_DOWN.add(key)
        notifications.emit(
            "production_down",
            project["id"],
            title=f"{target['name']} is down",
            text=f"Production of {project['name']} does not answer: {result.detail}.",
            instance=target["name"],
            branch=target.get("branch"),
            severity="problem",
            path=f"/projects/{project['id']}/status",
        )
    elif result.state != DOWN and key in _NOTIFIED_DOWN:
        _NOTIFIED_DOWN.discard(key)
        notifications.emit(
            "production_up",
            project["id"],
            title=f"{target['name']} is back up",
            text=f"Production of {project['name']} answers again ({result.detail}).",
            instance=target["name"],
            branch=target.get("branch"),
            severity="recovered",
            path=f"/projects/{project['id']}/status",
        )


def _forget_gone(checked: set[str]) -> None:
    from sudoo.storage import storage

    from . import META

    for target in list(storage().all(META)):
        if target not in checked:
            forget_target(target)
            _NOTIFIED_DOWN.discard(target)


def run_forever() -> None:
    """Background loop: a round every STATUS_CHECK_INTERVAL_SECONDS (while STATUS_CHECKS is on), pruning hourly."""
    global _LAST_PRUNE
    time.sleep(20)  # after the monitor's first measurement
    while True:
        started = time.time()
        try:
            if settings.status_checks:
                run_once(started)
            if started - _LAST_PRUNE > 3600:
                _LAST_PRUNE = started
                prune(started)
        except Exception as exc:  # noqa: BLE001 - the checks must keep running; the error is logged
            print("status checks:", repr(exc), flush=True)
        time.sleep(max(1.0, settings.status_check_interval_seconds - (time.time() - started)))


# ---------------------------------------------------------------- what the Status tab shows


def _target_view(target: Mapping[str, str], now: float, public: bool = False) -> dict[str, Any]:
    found = meta(target["id"]) or {}
    view: dict[str, Any] = {
        "id": target["id"],
        "kind": target["kind"],
        "name": target["name"],
        "state": found.get("state"),
        "uptime": {window: _uptime(target["id"], seconds, now) for window, seconds in WINDOWS.items()},
        "days": [{"day": d["day"], "uptime": d["uptime"]} for d in days(target["id"], 30, now)],
    }
    if public:
        return view
    view.update(
        branch=target.get("branch"),
        since=found.get("since"),
        checked_at=found.get("at"),
        detail=found.get("detail"),
        response_ms=found.get("ms"),
        days=days(target["id"], 30, now),
        hourly_ms=hourly_ms(target["id"], now) if target["kind"] == "instance" else None,
    )
    return view


def _uptime(target: str, seconds: float, now: float) -> float | None:
    from . import uptime

    return uptime(target, seconds, now)


def project_status(project: Mapping[str, Any], now: float | None = None) -> dict[str, Any]:
    """A project's Status tab: the banner, each target with its uptime and days, the incidents of 30 days."""
    now = time.time() if now is None else now
    shown = targets(project["id"])
    views = [_target_view(target, now) for target in shown]
    names = {t["id"]: t for t in shown}
    found = incidents(names, limit=100, since=now - 30 * DAY)
    return {
        "project": project["id"],
        "state": overall({v["id"]: v["state"] for v in views}, [t["id"] for t in shown if t["kind"] == "instance"]),
        "checks": bool(settings.status_checks),
        "interval_seconds": settings.status_check_interval_seconds,
        "slow_ms": settings.status_slow_ms,
        "production": any(t["kind"] == "instance" for t in shown),
        "targets": views,
        "incidents": [
            {
                **incident,
                "kind": names[incident["target"]]["kind"],
                "name": names[incident["target"]]["name"],
                "duration": round((incident["end"] or now) - incident["start"]),
            }
            for incident in found
        ],
    }


def public_status(project: Mapping[str, Any], now: float | None = None) -> dict[str, Any]:
    """The public page: the project's name, the banner, each target's name, state and uptime. Nothing else."""
    now = time.time() if now is None else now
    shown = targets(project["id"])
    views = [_target_view(target, now, public=True) for target in shown]
    return {
        "name": project.get("name"),
        "state": overall({v["id"]: v["state"] for v in views}, [t["id"] for t in shown if t["kind"] == "instance"]),
        "targets": [{k: v for k, v in view.items() if k != "id"} for view in views],
        "at": round(now),
    }
