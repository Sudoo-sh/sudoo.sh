"""What tells the panel to update an instance: the update-trigger plugins named by UPDATE_TRIGGERS.

UPDATE_TRIGGERS is a comma-separated list, so several triggers can work together. Built-in: polling (polling.py)
and github-webhook (github_webhook.py). Writing one: docs/PLUGINS.md.
"""

from __future__ import annotations

from functools import cache

from flask import Blueprint

from sudoo import plugins
from sudoo.config import settings
from sudoo.core import jobs
from sudoo.instances import operations, store
from sudoo.triggers.base import RequestUpdate, UpdateTrigger

__all__ = ["RequestUpdate", "UpdateTrigger", "blueprints", "enabled", "request_update", "start_all"]


@cache
def enabled() -> list[UpdateTrigger]:
    """The configured update triggers, each created once."""
    names = [name.strip() for name in settings.update_triggers.split(",") if name.strip()]
    return [plugins.load(plugins.UPDATE_TRIGGER_GROUP, name, settings) for name in names]


def request_update(name: str, reason: str) -> None:
    """Update instance `name` now, if it takes automatic updates and is not busy with something else. A staging
    instance of a project that rebuilds staging on push is rebuilt from production instead (projects/automation.py).
    An instance of a locked branch is never updated automatically (projects/access.py), nor any instance once the days
    to link the panel to a Sudoo.sh account have passed while it is unlinked (link_deadline.py): the skip is
    audit-logged."""
    if not store.exists(name):
        _request_remote_update(name, reason)
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

    special = automation.update_for(name)
    if special is not None:
        title, kind, fn, args = special
        jobs.start(name, f"{title}: {reason}", kind, fn, *args)
        return
    jobs.start(name, f"Automatic update: {reason}", "update", operations.update_code, name)


def _request_remote_update(name: str, reason: str) -> None:
    """An instance of another server (servers/): its agent gets the new code, if it takes automatic updates and its
    branch is not locked; nothing happens while that server is offline (the next push or poll asks again)."""
    from sudoo.servers import inventory, links

    found = inventory.remote(name)
    if found is None or found["env"].get("AUTO_UPDATE") != "true" or not links.connected(found["server"]):
        return
    from sudoo.projects import access

    if access.instance_lock(name) is not None:
        return
    try:
        links.call(found["server"], "http", {"method": "POST", "path": f"/api/v1/instances/{name}/actions/update",
                                             "query": "", "body": "{}", "user": {
                                                 "name": "system", "role": "super_admin", "via": "panel",
                                                 "permissions": ["instances.view", "instances.update"]}})  # fmt: skip
    except Exception as error:  # noqa: BLE001 - the next trigger asks again
        print(f"triggers: {name} on another server not updated: {error!r}", flush=True)


def start_all() -> None:
    for trigger in enabled():
        trigger.start(request_update)


def blueprints() -> list[Blueprint]:
    """The HTTP endpoints of the enabled triggers (webhooks)."""
    return [blueprint for trigger in enabled() if (blueprint := trigger.blueprint()) is not None]
