"""Work that runs on its own inside the panel process: monitor, automatic pause, automatic deletion, GitHub polling,
gateway, connectors."""

from __future__ import annotations

import threading
import time

from instance_manager import exposure, triggers
from instance_manager.config import settings
from instance_manager.core import jobs
from instance_manager.core.state import STATE
from instance_manager.instances import gateway, monitor, operations, store
from instance_manager.repos import github


def start() -> None:
    """Start everything. Called once when the web panel starts."""
    github.configure_git()
    for loop in (
        monitor.run_forever,
        STATE.save_visits_forever,
        _pause_idle_instances,
        _delete_expired_instances,
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
