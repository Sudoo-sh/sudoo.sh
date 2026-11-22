"""What raises alerts: the end of every job (on_job_end, registered with jobs.on_end) and a look at the server and
the instances every METRICS_INTERVAL_SECONDS (check), from what the monitor measured.

Conditions that last (an instance down, memory high, no public link) count from when they were first seen and are
alerted once they lasted their ALERT_..._MINUTES; when they are over, a recovery notice follows (alerts.recover).
The disk is alerted as soon as its free share falls below ALERT_DISK_FREE_PERCENT. An instance's storage (its last
measurement, instances/disk_use.py) is alerted once at STORAGE_WARNING_PERCENT of its quota and once over it; a
production instance over its quota is alerted again every day until it is back under it. An instance that is paused,
switched off, busy with a job or deleted is not watched: an open alert about it is closed without a notice.

Drift (changes made outside the panel) is reported by instances/integrity.py through alerts.send.
"""

from __future__ import annotations

import time
from urllib.parse import quote

from sudoo.config import settings
from sudoo.core import jobs
from sudoo.core.hints import GENERAL_HINT

from . import Alert, close, recover, send

_SINCE: dict[str, float] = {}  # condition key -> when it was first seen (in memory: a restart starts counting again)
_ACTIVE: set[str] = set()  # condition keys alerted and not over yet
_OVER_SENT: dict[str, float] = {}  # instance -> when "over its storage quota" was last sent (production: daily)
DAY = 86400


def _instance_path(name: str, job_id: str | None = None) -> str:
    path = f"/i/{quote(name)}"
    return f"{path}/activity?job={job_id}" if job_id else path


def _failed_step(job: jobs.Job) -> str | None:
    failed = [step["title"] for step in job.steps if step.get("status") == "failed"]
    return failed[-1] if failed else None


def on_job_end(job: jobs.Job) -> None:
    """A job ended: alert when it failed (an update that was rolled back and a backup have their own alerts)."""
    if job.status != "failed" or jobs.is_panel_job(job.instance):
        return
    from sudoo.core.state import STATE

    name = job.instance
    path = _instance_path(name, job.id)
    if job.kind == "update":
        failed = STATE.get(name).get("update_failed") or {}
        if failed.get("job") == job.id:
            rolled_back = bool(failed.get("rolled_back"))
            send(
                Alert(
                    "update_failed",
                    f"Update of {name} failed and was rolled back" if rolled_back
                    else f"Update of {name} failed, and so did its rollback",
                    str(failed.get("message") or job.hint or GENERAL_HINT),
                    instance=name,
                    path=path,
                    key=f"update_failed:{name}:{failed.get('target') or job.id}:{rolled_back}",
                )
            )  # fmt: skip
            return
    step = _failed_step(job)
    details = [f"Failed at: {step}"] if step else []
    if job.kind == "backup":
        send(Alert("backup_failed", f"Backup of {name} failed", job.hint or GENERAL_HINT, instance=name, path=path,
                   details=details))  # fmt: skip
        return
    send(
        Alert(
            "job_failed",
            f"{job.action} failed on {name}",
            job.hint or GENERAL_HINT,
            instance=name,
            path=path,
            details=details + [f"Started by: {job.user}"],
            key=f"job_failed:{name}:{job.kind}",
        )
    )


def _lasting(key: str, now: float, minutes: float) -> bool:
    """Whether the condition `key`, seen now, has lasted `minutes`."""
    since = _SINCE.setdefault(key, now)
    return now - since >= minutes * 60


def _raise(key: str, alert: Alert, now: float) -> None:
    if key not in _ACTIVE:
        _ACTIVE.add(key)
        alert.key = key
        send(alert, now)


def _over(key: str, alert: Alert | None, now: float) -> None:
    """The condition is not seen any more: a recovery notice (alert) when it was alerted, or a silent close."""
    _SINCE.pop(key, None)
    if key in _ACTIVE:
        _ACTIVE.discard(key)
        if alert is None:
            close(key, now)
        else:
            recover(key, alert, now)


def _forget(key: str, now: float) -> None:
    """Not watched now (paused, deleted): start counting again next time, and close an open alert silently."""
    _over(key, None, now)


def check(now: float | None = None) -> None:
    """Look at the server and every instance once (the monitor's latest measurements)."""
    if not settings.alerts:
        return
    now = time.time() if now is None else now
    _check_server(now)
    _check_instances(now)


def _check_server(now: float) -> None:
    from sudoo.instances import monitor

    host = monitor.HOST
    total, used = host.get("disk_total"), host.get("disk_used") or 0
    if total:
        free = 100 * (total - used) / total
        key = "disk:"
        if free < settings.alert_disk_free_percent:
            _raise(key, Alert("disk", f"Disk almost full: {free:.1f}% free",
                              f"The disk holding the instances has {_size(total - used)} free of {_size(total)} "
                              f"(the alert starts below {settings.alert_disk_free_percent:g}%). Delete instances or "
                              "backups that are no longer needed, or give the server more disk.",
                              path="/backups"), now)  # fmt: skip
        else:
            _over(key, Alert("disk", f"Disk space is back: {free:.1f}% free", "The disk has room again.",
                             path="/backups"), now)  # fmt: skip
    memory_total, memory_used = host.get("memory_total"), host.get("memory_used") or 0
    if memory_total:
        percent = 100 * memory_used / memory_total
        key = "memory:"
        if percent >= settings.capacity_warning_percent:
            if _lasting(key, now, settings.alert_memory_minutes):
                _raise(key, Alert("memory", f"Memory high: {percent:.0f}% used",
                                  f"Server memory has stayed above {settings.capacity_warning_percent:g}% for "
                                  f"{settings.alert_memory_minutes:g} minutes. Instances may slow down: pause the "
                                  "ones nobody uses, or give the server more memory.", path="/"), now)  # fmt: skip
        else:
            _over(key, Alert("memory", f"Memory back to normal: {percent:.0f}% used",
                             "Server memory is below the warning again.", path="/"), now)  # fmt: skip
    _check_link("panel", "/", now)


def _check_link(name: str, path: str, now: float) -> None:
    """Alert when `name` (the panel or an instance) has had no public link for ALERT_PUBLIC_LINK_MINUTES."""
    from sudoo import exposure

    key = f"public_link:{name}"
    try:
        address = exposure.address(exposure.PANEL if name == "panel" else name)
    except Exception:  # noqa: BLE001 - the exposure cannot tell now: no change
        return
    what = "The panel" if name == "panel" else f"Instance {name}"
    if address:
        _over(key, Alert("public_link", f"{what} has its public link again", f"{what} is reachable at {address}.",
                         instance=None if name == "panel" else name, path=path), now)  # fmt: skip
    elif _lasting(key, now, settings.alert_public_link_minutes):
        _raise(key, Alert("public_link", f"{what} has no public link",
                          f"{what} has had no public link for {settings.alert_public_link_minutes:g} minutes, so "
                          "nobody can open it from outside. Its connector may have stopped; restarting the instance "
                          "starts it again.", instance=None if name == "panel" else name, path=path), now)  # fmt: skip


def _check_instances(now: float) -> None:
    from sudoo.instances import monitor, store

    names = set(store.names())
    for key in [k for k in _ACTIVE | set(_SINCE) if k.startswith(("instance_down:", "public_link:", "storage"))]:
        if key.split(":", 1)[1] not in names | {"panel"}:
            _forget(key, now)  # deleted
    for name in sorted(names):
        _check_storage(name, now)
        status = monitor.status(name)
        down, link = f"instance_down:{name}", f"public_link:{name}"
        if status == "online":
            _over(down, Alert("instance_down", f"{name} is back online", f"Instance {name} answers again.",
                              instance=name, path=_instance_path(name)), now)  # fmt: skip
            _check_link(name, _instance_path(name), now)
        elif status in ("starting", "stopped"):  # it should run: not paused, not switched off, no job on it
            _forget(link, now)
            if _lasting(down, now, settings.alert_instance_down_minutes):
                what = "is stopped" if status == "stopped" else "runs but Odoo does not answer"
                _raise(down, Alert("instance_down", f"{name} is down",
                                   f"Instance {name} {what}, for {settings.alert_instance_down_minutes:g} minutes "
                                   "now. Open it to see its log, or restart it.", instance=name,
                                   path=_instance_path(name)), now)  # fmt: skip
        elif status == "busy":  # a job decides: count again afterwards, keep what was alerted open
            if down not in _ACTIVE:
                _SINCE.pop(down, None)
            if link not in _ACTIVE:
                _SINCE.pop(link, None)
        elif status != "unknown":  # paused, switched off
            _forget(down, now)
            _forget(link, now)


def _check_storage(name: str, now: float) -> None:
    """Alert when the instance's database and files reach STORAGE_WARNING_PERCENT of its quota, and when they are
    over it (production: again every day). Nothing is refused."""
    from sudoo.instances import disk_use, stages, store

    stage = stages.of(store.read_env(name))
    found = disk_use.usage(name, stage)
    warning, over = f"storage:{name}", f"storage_over:{name}"
    level, path = found["level"], _instance_path(name)
    if level == "none":
        _forget(warning, now)
        _forget(over, now)
        _OVER_SENT.pop(name, None)
        return
    used, quota = _size(found["total_bytes"] or 0), _size(found["quota_bytes"] or 0)
    if level in ("warning", "over"):
        _raise(warning, Alert("storage", f"{name} is near its storage quota",
                              f"Instance {name} uses {used} (database and files) of its {quota} quota "
                              f"({found['percent']:g}%). Nothing is stopped: delete data Odoo no longer needs, or ask "
                              "an administrator to raise the quota.", instance=name, path=path), now)  # fmt: skip
    else:
        _over(warning, Alert("storage", f"{name} is back under its storage quota",
                             f"Instance {name} uses {used} of its {quota} quota.", instance=name, path=path), now)  # fmt: skip
    if level != "over":
        _over(over, None, now)
        _OVER_SENT.pop(name, None)
        return
    if stage == stages.PRODUCTION and now - _OVER_SENT.get(name, now) >= DAY:
        _ACTIVE.discard(over)  # production over its quota: told again every day
    if over not in _ACTIVE:
        _OVER_SENT[name] = now
    _raise(over, Alert("storage", f"{name} is over its storage quota",
                       f"Instance {name} uses {used} (database and files), more than its {quota} quota. It keeps "
                       "running; delete data Odoo no longer needs, or ask an administrator to raise the quota.",
                       instance=name, path=path), now)  # fmt: skip


def _size(count: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if count < 1024:
            return f"{count:.0f} {unit}"
        count /= 1024
    return f"{count:.1f} TB"


def run_forever() -> None:
    """Background loop: check every METRICS_INTERVAL_SECONDS (after the monitor's first measurement)."""
    time.sleep(30)
    while True:
        try:
            check()
        except Exception as exc:  # noqa: BLE001 - the watch must keep running; the error is logged
            print("alerts watch:", repr(exc), flush=True)
        time.sleep(max(settings.metrics_interval_seconds, 5))
