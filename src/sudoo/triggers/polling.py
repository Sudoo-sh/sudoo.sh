"""Update trigger "polling": every POLL_MINUTES the panel asks GitHub whether each instance's branch has new commits.

Needs nothing from GitHub and nothing reachable from outside, so it works behind tunnels whose address changes. The
cost is a delay of up to POLL_MINUTES after a push. When a branch has been deleted on GitHub, the instance is marked
(a warning on its page) instead of being polled.
"""

from __future__ import annotations

import subprocess
import threading
import time

from flask import Blueprint

from sudoo.config import Settings
from sudoo.core import jobs
from sudoo.core.shell import capture
from sudoo.core.state import STATE
from sudoo.instances import store
from sudoo.repos.gitrepo import branch_on_github, git
from sudoo.triggers.base import RequestUpdate


class Polling:
    """Checks every instance's branch for new commits at a fixed interval."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def start(self, request_update: RequestUpdate) -> None:
        threading.Thread(target=self._run, args=(request_update,), daemon=True, name="polling").start()

    def blueprint(self) -> Blueprint | None:
        return None

    def _run(self, request_update: RequestUpdate) -> None:
        while True:
            time.sleep(self.settings.poll_minutes * 60)
            for name in store.names():
                if self.idle_and_new(name):
                    request_update(name, "new code on GitHub")

    def idle_and_new(self, name: str) -> bool:
        """has_new_commits(), only while no job works on the instance, and holding its lock while git fetches: a
        fetch never runs in a checkout a job is fetching, resetting or removing. A busy instance is polled next time."""
        lock = jobs.INSTANCE_LOCKS[name]
        if jobs.unfinished(name) or not lock.acquire(blocking=False):
            return False
        try:
            return self.has_new_commits(name)
        finally:
            lock.release()  # before the update is asked for: it runs as a job that takes this lock

    @staticmethod
    def has_new_commits(name: str) -> bool:
        """True when the instance's branch on GitHub is ahead of its code. Marks branches deleted on GitHub."""
        env, addons = store.read_env(name), store.addons_dir(name)
        if env["AUTO_UPDATE"] != "true" or not capture(git(addons, "remote", "get-url", "origin")):
            return False
        exists = branch_on_github(addons, env["GIT_BRANCH"])
        if exists is not None and STATE.get(name).get("branch_missing") != (not exists):
            STATE.update(name, branch_missing=not exists)  # shown as a warning on the instance's page
        if not exists:
            return False
        fetched = subprocess.run(
            git(addons, "fetch", "-q", "origin", env["GIT_BRANCH"]), capture_output=True, timeout=120
        )
        if fetched.returncode != 0:
            return False
        latest = capture(git(addons, "rev-parse", "FETCH_HEAD"))
        failed = STATE.get(name).get("update_failed") or {}
        # an update to this commit failed and was rolled back (operations.update_code): not again every few minutes
        return latest != capture(git(addons, "rev-parse", "HEAD")) and latest != failed.get("target")
