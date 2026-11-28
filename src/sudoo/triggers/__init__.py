"""What tells the panel to update an instance: the update-trigger plugins named by UPDATE_TRIGGERS.

UPDATE_TRIGGERS is a comma-separated list, so several triggers can work together. Built-in: polling (polling.py)
and github-webhook (github_webhook.py). Writing one: docs/PLUGINS.md.
"""

from __future__ import annotations

import threading
import time
from functools import cache
from typing import Any

from flask import Blueprint

from sudoo import plugin_guard, plugins
from sudoo.config import settings
from sudoo.core import jobs, resilience
from sudoo.instances import operations, store
from sudoo.triggers.base import RequestUpdate, UpdateTrigger

__all__ = ["RequestUpdate", "UpdateTrigger", "blueprints", "enabled", "note_pusher", "pusher_of", "request_update",
           "start_all"]  # fmt: skip


_PUSHERS: dict[str, tuple[float, dict[str, str]]] = {}  # instance -> (when, who pushed) for its next update
_PUSHERS_LOCK = threading.Lock()
_PUSHER_SECONDS = 600


def pusher_of(payload: dict[str, Any]) -> dict[str, str]:
    """Who pushed, from a GitHub push payload: {"name", "email"} of the pusher, else of the head commit's committer
    or author (an address may be empty or GitHub's noreply one; projects/push_failed.py decides)."""
    pusher = payload.get("pusher") or {}
    head = payload.get("head_commit") or {}
    for person in (pusher, head.get("committer") or {}, head.get("author") or {}):
        email = str(person.get("email") or "").strip()
        if email:
            return {"name": str(person.get("name") or person.get("username") or "")[:100], "email": email[:254]}
    return {"name": str(pusher.get("name") or "")[:100], "email": ""}


def note_pusher(name: str, pusher: dict[str, str]) -> None:
    """Remember who pushed the code instance `name` is about to be updated to (the next update job carries it)."""
    with _PUSHERS_LOCK:
        _PUSHERS[name] = (time.time(), dict(pusher))


def _take_pusher(name: str) -> dict[str, str] | None:
    with _PUSHERS_LOCK:
        found = _PUSHERS.pop(name, None)
    return found[1] if found and time.time() - found[0] < _PUSHER_SECONDS else None


@cache
def enabled() -> list[UpdateTrigger]:
    """The configured update triggers, each created once. One that cannot be loaded is left out (and shown to the
    administrators, plugin_guard.py); the others work."""
    names = [name.strip() for name in settings.update_triggers.split(",") if name.strip()]
    loaded = [plugin_guard.load_safely(plugins.UPDATE_TRIGGER_GROUP, name, settings) for name in names]
    return [trigger for trigger in loaded if trigger is not None]


plugin_guard.on_reset(plugins.UPDATE_TRIGGER_GROUP, enabled.cache_clear)


def request_update(name: str, reason: str) -> None:
    """Update instance `name` now, if it takes automatic updates and is not busy with something else. A staging
    instance of a project that rebuilds staging on push is rebuilt from production instead (projects/automation.py).
    An instance of a locked branch is never updated automatically (projects/access.py), nor any instance once the days
    to link the panel to a Sudoo.sh account have passed while it is unlinked (link_deadline.py): the skip is
    audit-logged."""
    if not store.exists(name):
        from sudoo.premium import servers

        servers.request_update(name, reason)  # an instance of another server: its agent gets the new code
        return
    if store.read_env(name)["AUTO_UPDATE"] != "true" or jobs.busy(name):
        return
    from sudoo.projects import access, automation  # late: projects/ imports the instances package, as this does

    if access.instance_lock(name) is not None:
        from sudoo.core import audit

        audit.record("system", None, "automatic update skipped (branch locked)", name, detail=reason[:200])
        return
    from sudoo import link_deadline

    if link_deadline.restricted():  # the days to link the panel have passed (link_deadline.py)
        from sudoo.core import audit

        audit.record("system", None, "automatic update skipped (panel not linked)", name, detail=reason[:200])
        return

    if automation.skips_update(name):  # its branch builds nothing on new commits: the commit is recorded
        return
    details: dict[str, Any] = {"build": "push"}
    pusher = _take_pusher(name)
    if pusher:  # who to tell if it fails (projects/push_failed.py); polling: the commit's author, read then
        details["pusher"] = pusher
    special = automation.update_for(name)
    if special is not None:
        title, kind, fn, args = special

        def begin() -> jobs.Job | None:
            if jobs.unfinished(name):  # checked again where the start happens (quotas.LOCK): see below
                return None
            return jobs.start(name, f"{title}: {reason}", kind, fn, *args, details=details, holds_slot=True)

    else:

        def begin() -> jobs.Job | None:
            # a job registered a moment ago by another report of the same commit (a webhook and polling, a second
            # delivery) is not busy() yet: it is queued, not running; under quotas.LOCK the second start sees it
            if jobs.unfinished(name):
                return None
            return jobs.start(
                name, f"Automatic update: {reason}", "update", operations.update_code, name, details=details,
                holds_slot=True,
            )  # fmt: skip

    from sudoo.core import audit
    from sudoo.instances import capacity

    job, why = capacity.start_admitted(name, begin)
    if (
        job is None and why is not None
    ):  # a running limit (or the slot record) refuses it: said so; the next push or poll tries again
        audit.record("system", None, "automatic update skipped (running limit)", name, ok=False, detail=str(why)[:200])


def _name(trigger: UpdateTrigger) -> str:
    return str(getattr(trigger, "name", "") or type(trigger).__name__)


def start_all() -> None:
    """Start each trigger; one that fails to start is shown to the administrators and never stops the others."""
    for trigger in enabled():
        resilience.attempt(f"update trigger {_name(trigger)}", trigger.start, request_update)


def blueprints() -> list[Blueprint]:
    """The HTTP endpoints of the enabled triggers (webhooks); a trigger whose endpoint cannot be made has none."""
    found = []
    for trigger in enabled():
        blueprint = resilience.attempt(f"webhook of update trigger {_name(trigger)}", trigger.blueprint)
        if blueprint is not None:
            found.append(blueprint)
    return found
