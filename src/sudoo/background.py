"""Work that runs on its own inside the panel process: monitor, automatic pause, automatic deletion, daily backups,
downloading images ahead of time (PREPULL_IMAGES), removal of expired rollback snapshots, GitHub polling, the GitHub
App's webhook address, gateway, connectors, alerts, the marketplace heartbeat, the plugin watchdog (files,
licences) and custom domains (DNS checks, certificates and their renewal)."""

from __future__ import annotations

import threading
import time

from sudoo import accounts, alerts, domains, exposure, plugin_manager, triggers
from sudoo.config import live, settings
from sudoo.core import audit, jobs
from sudoo.core.shell import succeeds
from sudoo.core.state import STATE
from sudoo.domains import worker as domain_worker
from sudoo.hub import marketplace
from sudoo.instances import backups, gateway, integrity, monitor, odoo, operations, stages, store
from sudoo.repos import github, github_app


def start() -> None:
    """Start everything. Called once when the web panel starts."""
    github.configure_git()
    threading.Thread(target=accounts.dummy_hash, daemon=True, name="dummy-hash").start()
    for loop in (
        monitor.run_forever,
        STATE.save_visits_forever,
        _pause_idle_instances,
        _delete_expired_instances,
        _daily_backups,
        _prepull_images,
        _remove_expired_snapshots,
        _keep_github_webhook_current,
        marketplace.heartbeat_forever,
        plugin_manager.watch_forever,
        domain_worker.run_forever,
    ):
        threading.Thread(target=loop, daemon=True, name=loop.__name__).start()
    alerts.start()
    gateway.start(wake=start_wake_job)
    live.on_change(_domain_settings_changed)
    threading.Thread(target=exposure.ensure_all, daemon=True, name="connectors").start()
    triggers.start_all()


_DOMAIN_SETTINGS = {"custom_domains", "domain_http_probe", "acme_directory", "domain_dns_target"}


def _domain_settings_changed(changed: dict[str, object]) -> None:
    """Custom domains turned on or off, or their checks changed: serve accordingly, look at every domain now."""
    if _DOMAIN_SETTINGS & set(changed):
        domains.WAKE.set()
        try:
            gateway.refresh_domains()
        except Exception as error:  # noqa: BLE001 - the gateway catches up at its next round
            print(f"domains: gateway not refreshed: {error!r}", flush=True)


def start_wake_job(name: str) -> jobs.Job:
    """Start an instance because someone opened it (called by the gateway)."""
    return jobs.start(name, "Start (someone opened it)", "wake", operations.wake, name, user="visitor")


def _pause_idle_instances() -> None:
    """Every few minutes: pause running instances that nobody opened for IDLE_HOURS (0 turns this off)."""
    while True:
        time.sleep(settings.pause_check_interval_seconds)
        pause_idle_instances()


def pause_idle_instances(now: float | None = None) -> list[str]:
    """Start the pause of each running instance idle for IDLE_HOURS whose automatic pause is on; return their names.
    A production instance is never paused automatically (stages.pauses_when_idle)."""
    started: list[str] = []
    if settings.idle_hours <= 0:
        return started
    for name in store.names():
        if (
            not stages.pauses_when_idle(store.read_env(name))
            or jobs.busy(name)
            or not monitor.STATUS.get(name, {}).get("running")
        ):
            continue
        last_visit = STATE.get(name).get("last_visit")
        if not last_visit:  # no visit recorded yet: start counting now
            STATE.record_visit(name)
        elif (time.time() if now is None else now) - last_visit > settings.idle_hours * 3600:
            jobs.start(name, f"Paused (no visits for {settings.idle_hours:g} hours)", "pause", operations.pause, name)
            started.append(name)
    return started


def _delete_expired_instances() -> None:
    """Every few minutes: delete instances whose lifetime (DELETE_AFTER_DAYS) is over. The deletion backs up first;
    an instance whose deletion failed operations.EXPIRY_ATTEMPTS times is switched off and left alone (expire)."""
    while True:
        time.sleep(settings.expiry_check_interval_seconds)
        delete_expired_instances()


def delete_expired_instances(now: float | None = None) -> list[str]:
    """Start the deletion of each expired instance that is not busy and not given up on; return their names."""
    started = []
    for name in store.names():
        env = store.read_env(name)
        expires = operations.expires_at(env)
        if not expires or (time.time() if now is None else now) <= expires or jobs.busy(name):
            continue
        if STATE.get(name).get("expiry_stopped"):
            continue
        jobs.start(
            name, f"Deleted automatically ({env['DELETE_AFTER_DAYS']} days after creation)", "delete",
            operations.expire, name,
        )  # fmt: skip
        started.append(name)
    return started


def _daily_backups() -> None:
    """Every minute: once DAILY_BACKUP_TIME has passed, back up each instance that is due (daily_backups)."""
    tried: dict[str, float] = {}
    while True:
        time.sleep(60)
        daily_backups(tried)


def daily_backups(tried: dict[str, float], now: float | None = None) -> list[str]:
    """Start the daily backup of each instance due for the current slot (stages.daily_backup_due: production every
    day, the others when DAILY_BACKUPS is on and they were used since their last one); return their names. `tried`
    remembers the slot each instance was tried for: one that is busy is tried again a minute later, one whose
    backup failed waits for the next day."""
    slot = backups.daily_slot(now)
    started: list[str] = []
    for name in store.names():
        if tried.get(name) == slot or jobs.busy(name):
            continue
        if stages.daily_backup_due(name, store.read_env(name), STATE.get(name).get("used_since"), slot):
            tried[name] = slot
            jobs.start(name, "Daily backup", "backup", operations.daily_backup, name)
            started.append(name)
    return started


PREPULL_CHECK_SECONDS = 1800
_PREPULL_NOW = threading.Event()
_PREPULL_INPUTS = {"prepull_images", "odoo_versions", "odoo_min_version", "odoo_image", "postgres_image"}


def _prepull_images() -> None:
    """Every half hour, and as soon as one of its settings changes, when PREPULL_IMAGES is on: download the images a
    new instance needs that are not on this server yet (the Odoo image of every offered version, the PostgreSQL
    image), so creating one does not wait for them. Images already here are left alone: creating an instance checks
    for a newer one anyway."""
    live.on_change(lambda changed: _PREPULL_NOW.set() if _PREPULL_INPUTS & set(changed) else None)
    _PREPULL_NOW.wait(60)  # let the panel start first
    while True:
        _PREPULL_NOW.clear()
        if settings.prepull_images:
            prepull_images()
        _PREPULL_NOW.wait(PREPULL_CHECK_SECONDS)


def prepull_images() -> list[str]:
    """Download the missing images (one after the other); return those that were downloaded."""
    wanted = [f"{settings.odoo_image}:{version}" for version in odoo.versions()] + [settings.postgres_image]
    pulled = []
    for image in wanted:
        if not settings.prepull_images or integrity.present(image):
            continue
        started = time.monotonic()
        if succeeds(["docker", "pull", "--quiet", image], timeout=3600):
            pulled.append(image)
            print(f"Downloaded {image} ahead of time in {time.monotonic() - started:.0f} s", flush=True)
        else:
            print(f"Could not download {image} ahead of time; trying again later", flush=True)
    return pulled


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


WEBHOOK_CHECK_SECONDS = 120
_webhook_problem: list[str] = [""]  # the last failure, audit-logged once rather than every two minutes


def _keep_github_webhook_current() -> None:
    """Every two minutes: when the panel's public address changed (a Cloudflare quick tunnel gets a new one when its
    connector restarts), point the GitHub App's webhook at the new address."""
    while True:
        time.sleep(WEBHOOK_CHECK_SECONDS)
        keep_github_webhook_current()


def keep_github_webhook_current() -> str | None:
    """Update the GitHub App's webhook address if needed; the new address, or None when nothing changed."""
    if not github_app.connected():
        return None
    address = exposure.address(exposure.PANEL)
    if not address:
        return None
    try:
        changed = github_app.update_hook_url(address)
    except github_app.GitHubError as error:
        if str(error) != _webhook_problem[0]:
            _webhook_problem[0] = str(error)
            audit.record("panel", None, "GitHub App webhook address not updated", ok=False, detail=str(error))
        return None
    _webhook_problem[0] = ""
    if not changed:
        return None
    url = address.rstrip("/") + github_app.HOOK_PATH
    audit.record("panel", None, "GitHub App webhook address updated", detail=url)
    return url
