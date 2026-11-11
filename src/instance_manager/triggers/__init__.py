"""What tells the panel to update an instance: the update-trigger plugins named by UPDATE_TRIGGERS.

UPDATE_TRIGGERS is a comma-separated list, so several triggers can work together. Built-in: polling (polling.py)
and github-webhook (github_webhook.py). Writing one: docs/PLUGINS.md.
"""

from __future__ import annotations

from functools import cache

from flask import Blueprint

from instance_manager import plugins
from instance_manager.config import settings
from instance_manager.core import jobs
from instance_manager.instances import operations, store
from instance_manager.triggers.base import RequestUpdate, UpdateTrigger

__all__ = ["RequestUpdate", "UpdateTrigger", "blueprints", "enabled", "request_update", "start_all"]


@cache
def enabled() -> list[UpdateTrigger]:
    """The configured update triggers, each created once."""
    names = [name.strip() for name in settings.update_triggers.split(",") if name.strip()]
    return [plugins.load(plugins.UPDATE_TRIGGER_GROUP, name, settings) for name in names]


def request_update(name: str, reason: str) -> None:
    """Update instance `name` now, if it takes automatic updates and is not busy with something else."""
    if not store.exists(name) or store.read_env(name)["AUTO_UPDATE"] != "true" or jobs.busy(name):
        return
    jobs.start(name, f"Automatic update: {reason}", "update", operations.update_code, name)


def start_all() -> None:
    for trigger in enabled():
        trigger.start(request_update)


def blueprints() -> list[Blueprint]:
    """The HTTP endpoints of the enabled triggers (webhooks)."""
    return [blueprint for trigger in enabled() if (blueprint := trigger.blueprint()) is not None]
