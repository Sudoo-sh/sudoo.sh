"""Odoo Enterprise add-ons, through the add-on source plugin chosen by the ENTERPRISE_SOURCE setting.

ENTERPRISE_SOURCE=none turns Enterprise off. Built-in sources: folder (folder.py) and git (git.py). Writing one:
docs/PLUGINS.md.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from instance_manager import plugins
from instance_manager.addons.base import AddonSource, Log
from instance_manager.config import settings

__all__ = ["AddonSource", "available", "enterprise", "prepare"]


@cache
def enterprise() -> AddonSource | None:
    """The configured source of Enterprise add-ons, created once; None when Enterprise is turned off."""
    if settings.enterprise_source == "none":
        return None
    source: AddonSource = plugins.load(plugins.ADDON_SOURCE_GROUP, settings.enterprise_source, settings)
    return source


def available(version: str) -> bool:
    """True when Enterprise can be offered for `version`."""
    source = enterprise()
    return source is not None and source.available(version)


def prepare(version: str, log: Log) -> Path:
    """Make the Enterprise add-ons of `version` ready and return their folder on the server."""
    source = enterprise()
    if source is None:
        raise RuntimeError("Enterprise is turned off on this server (ENTERPRISE_SOURCE=none).")
    return source.prepare(version, log)
