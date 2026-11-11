"""Work that runs on its own inside the panel process: monitor, automatic pause, automatic deletion, GitHub polling,
gateway, connectors."""
import subprocess
import threading
import time

from . import gateway, github, instances, jobs, monitor, operations, settings, tunnels
from .gitrepo import branch_on_github, git
from .shell import capture
from .state import STATE


def start():
    """Start everything. Called once when the web panel starts."""
    github.configure_git()
    for loop in (monitor.run_forever, STATE.save_visits_forever, _pause_idle_instances, _delete_expired_instances,
                 _poll_github):
        threading.Thread(target=loop, daemon=True, name=loop.__name__).start()
    gateway.start(wake=start_wake_job)
    threading.Thread(target=tunnels.ensure_all, daemon=True, name="connectors").start()


def start_wake_job(name):
    """Start an instance because someone opened it (called by the gateway)."""
    return jobs.start(name, "Start (someone opened it)", "wake", operations.wake, name, user="visitor")


def _pause_idle_instances():
    """Every few minutes: pause running instances that nobody opened for IDLE_HOURS (0 turns this off)."""
    while True:
        time.sleep(settings.PAUSE_CHECK_INTERVAL_SECONDS)
        if settings.IDLE_HOURS <= 0:
            continue
        for name in instances.names():
            if (instances.read_env(name)["AUTO_PAUSE"] != "true" or jobs.busy(name)
                    or not monitor.STATUS.get(name, {}).get("running")):
                continue
            last_visit = STATE.get(name).get("last_visit")
            if not last_visit:                      # no visit recorded yet: start counting now
                STATE.record_visit(name)
            elif time.time() - last_visit > settings.IDLE_HOURS * 3600:
                jobs.start(name, f"Paused (no visits for {settings.IDLE_HOURS:g} hours)", "pause",
                           operations.pause, name)


def _delete_expired_instances():
    """Every few minutes: delete instances whose lifetime (DELETE_AFTER_DAYS) is over. The deletion backs up first."""
    while True:
        time.sleep(settings.EXPIRY_CHECK_INTERVAL_SECONDS)
        for name in instances.names():
            env = instances.read_env(name)
            expires = operations.expires_at(env)
            if expires and time.time() > expires and not jobs.busy(name):
                jobs.start(name, f"Deleted automatically ({env['DELETE_AFTER_DAYS']} days after creation)", "delete",
                           operations.delete, name)


def _poll_github():
    """Every POLL_MINUTES: update instances whose GitHub branch has new commits."""
    while True:
        time.sleep(settings.POLL_MINUTES * 60)
        for name in instances.names():
            env, addons = instances.read_env(name), instances.addons_dir(name)
            if env["AUTO_UPDATE"] != "true" or jobs.busy(name) or not capture(git(addons, "remote", "get-url", "origin")):
                continue
            exists = branch_on_github(addons, env["GIT_BRANCH"])
            if exists is not None and STATE.get(name).get("branch_missing") != (not exists):
                STATE.update(name, branch_missing=not exists)      # shown as a warning on the instance's page
            if not exists:
                continue
            fetched = subprocess.run(git(addons, "fetch", "-q", "origin", env["GIT_BRANCH"]),
                                     capture_output=True, timeout=120)
            if fetched.returncode == 0 and (capture(git(addons, "rev-parse", "HEAD"))
                                            != capture(git(addons, "rev-parse", "FETCH_HEAD"))):
                jobs.start(name, "Automatic update: new code on GitHub", "update", operations.update_code, name)
