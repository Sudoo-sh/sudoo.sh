"""Work that runs on its own inside the panel process: monitor, automatic pause, automatic deletion, daily backups,
removal of expired rollback snapshots, GitHub polling, gateway, connectors, the marketplace heartbeat and the plugin
watchdog (files, licences)."""

from __future__ import annotations

import threading
import time

from sudoo import exposure, plugin_manager, triggers
from sudoo.config import settings
from sudoo.core import jobs
from sudoo.core.state import STATE
from sudoo.hub import marketplace
from sudoo.instances import backups, gateway, monitor, operations, store
from sudoo.repos import github


def start() -> None:
    """Start everything. Called once when the web panel starts."""
    github.configure_git()
    for loop in (
        monitor.run_forever,
        STATE.save_visits_forever,
        _pause_idle_instances,
        _delete_expired_instances,
        _daily_backups,
        _remove_expired_snapshots,
        marketplace.heartbeat_forever,
        plugin_manager.watch_forever,
    ):
        threading.Thread(target=loop, daemon=True, name=loop.__name__).start()
    gateway.start(wake=start_wake_job)
    threading.Thread(target=exposure.ensure_all, daemon=True, name="connectors").start()
    triggers.start_all()


def start_wake_job(name: str) -> jobs.Job:
    """Start an instance because someone opened it (called by the gateway)."""
    return jobs.start(name, "Start (someone opened it)", "wake", operations.wake, name, user="visitor")


def _pause_idle_instances() -> None:
    """Every few minutes: pause running instances that nobody opened for IDLE_HOURS (0 turns this off)."""
    while True:
        time.sleep(settings.pause_check_interval_seconds)
        if settings.idle_hours <= 0:
            continue
        for name in store.names():
            if (
                store.read_env(name)["AUTO_PAUSE"] != "true"
                or jobs.busy(name)
                or not monitor.STATUS.get(name, {}).get("running")
            ):
                continue
            last_visit = STATE.get(name).get("last_visit")
            if not last_visit:  # no visit recorded yet: start counting now
                STATE.record_visit(name)
            elif time.time() - last_visit > settings.idle_hours * 3600:
                jobs.start(
                    name, f"Paused (no visits for {settings.idle_hours:g} hours)", "pause", operations.pause, name
                )


def _delete_expired_instances() -> None:
    """Every few minutes: delete instances whose lifetime (DELETE_AFTER_DAYS) is over. The deletion backs up first."""
    while True:
        time.sleep(settings.expiry_check_interval_seconds)
        for name in store.names():
            env = store.read_env(name)
            expires = operations.expires_at(env)
            if expires and time.time() > expires and not jobs.busy(name):
                jobs.start(
                    name,
                    f"Deleted automatically ({env['DELETE_AFTER_DAYS']} days after creation)",
                    "delete",
                    operations.delete,
                    name,
                )


def _daily_backups() -> None:
    """Every minute: once DAILY_BACKUP_TIME has passed, back up each instance that was used since its last daily
    backup (STATE "used_since" earlier than that time). An instance that is busy is tried again a minute later; one
    whose backup failed waits for the next day."""
    tried: dict[str, float] = {}
    while True:
        time.sleep(60)
        if not settings.daily_backups:
            continue
        slot = backups.daily_slot()
        for name in store.names():
            if tried.get(name) == slot or jobs.busy(name):
                continue
            if backups.needs_daily(STATE.get(name).get("used_since"), slot):
                tried[name] = slot
                jobs.start(name, "Daily backup", "backup", operations.daily_backup, name)


SNAPSHOT_CHECK_SECONDS = 300


def _remove_expired_snapshots() -> None:
    """Every five minutes: remove the before-restore and before-rollback backups older than ROLLBACK_HOURS, which
    ends the instance's Rollback offer. Nothing is removed while any job runs: a restore, rollback or creation may be
    about to read a snapshot (of its own instance or of another one); they are removed in a later round."""
    while True:
        time.sleep(SNAPSHOT_CHECK_SECONDS)
        remove_expired_snapshots()


def remove_expired_snapshots(now: float | None = None) -> list[str]:
    """Remove expired safety backups, unless a job is running; return the removed file names."""
    removed: list[str] = []
    for backup in backups.expired_snapshots(now):
        if jobs.ACTIVE_JOB or any(jobs.busy(name) for name in store.names()):
            break
        (settings.backups_dir / backup["file"]).unlink(missing_ok=True)
        removed.append(backup["file"])
    return removed
