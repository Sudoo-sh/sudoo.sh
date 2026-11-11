"""Finding and loading plugins.

A plugin is a class registered under an entry-point group in a package's pyproject.toml, the standard Python way
(also used by pytest, Flask and Sphinx extensions):

    [project.entry-points."instance_manager.exposure"]
    cloudflare-quick = "instance_manager.exposure.cloudflare:CloudflareQuickTunnel"

A setting names the plugin to use (`EXPOSURE=cloudflare-quick`). The class is created once with the settings
(dependency injection: plugins never read the environment themselves), and every caller gets that one object.
Plugins from other installed packages are found the same way, so new ones need no change in this code.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from instance_manager.config import Settings

EXPOSURE_GROUP = "instance_manager.exposure"
ADDON_SOURCE_GROUP = "instance_manager.addon_sources"


class PluginError(RuntimeError):
    """A setting names a plugin that is not installed."""


def available(group: str) -> list[str]:
    """Names of the plugins installed for `group`."""
    return sorted(entry.name for entry in entry_points(group=group))


def load(group: str, name: str, settings: Settings) -> Any:
    """Create the plugin `name` of `group`, giving it the settings."""
    for entry in entry_points(group=group):
        if entry.name == name:
            return entry.load()(settings)
    raise PluginError(f'No plugin "{name}" for {group}. Installed: {", ".join(available(group)) or "none"}.')
