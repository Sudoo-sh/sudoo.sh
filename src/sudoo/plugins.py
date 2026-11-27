"""Finding and loading plugins.

A plugin is a class registered under an entry-point group in a package's pyproject.toml, the standard Python way
(also used by pytest, Flask and Sphinx extensions):

    [project.entry-points."sudoo.exposure"]
    cloudflare-quick = "sudoo.exposure.cloudflare:CloudflareQuickTunnel"

A setting names the plugin to use (`EXPOSURE=cloudflare-quick`). The class is created once with the settings
(dependency injection: plugins never read the environment themselves), and every caller gets that one object.
Plugins from other installed packages are found the same way, so new ones need no change in this code. Plugins
installed from the marketplace (plugin_manager.py) are put on the path, with their entry points, the first time a
plugin is loaded; the storage plugin is loaded before that, because the installed plugins are recorded in storage.

`load` raises when the plugin cannot be created. The panel itself loads plugins through plugin_guard.load_safely,
which never raises: a plugin that fails is listed, and the built-in default of its group is used instead.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint, entry_points
from typing import Any

from sudoo.config import Settings

EXPOSURE_GROUP = "sudoo.exposure"
ADDON_SOURCE_GROUP = "sudoo.addon_sources"
UPDATE_TRIGGER_GROUP = "sudoo.update_triggers"
STORAGE_GROUP = "sudoo.storage"
AUTH_GROUP = "sudoo.auth"
SECOND_FACTOR_GROUP = "sudoo.second_factors"
MAILER_GROUP = "sudoo.mailers"
RATE_LIMITER_GROUP = "sudoo.rate_limiters"


class PluginError(RuntimeError):
    """A setting names a plugin that is not installed (or one that may not be loaded)."""


def _with_installed(group: str) -> None:
    """Before the first plugin other than storage is loaded: put the marketplace plugins on the path."""
    if group != STORAGE_GROUP:
        from sudoo import plugin_manager

        plugin_manager.activate()


def available(group: str) -> list[str]:
    """Names of the plugins installed for `group`."""
    _with_installed(group)
    return sorted(entry.name for entry in entry_points(group=group))


def _distribution(entry: EntryPoint) -> str:
    dist = entry.dist
    return "" if dist is None else str(dist.metadata.get("Name", "")).lower().replace("_", "-")


def load(group: str, name: str, settings: Settings) -> Any:
    """Create the plugin `name` of `group`, giving it the settings.

    A name that two distributions provide is refused rather than guessed, and a marketplace plugin's entry point is
    used only when its own verified manifest declares it (plugin_manager.entry_allowed)."""
    _with_installed(group)
    found = [entry for entry in entry_points(group=group) if entry.name == name]
    if len({_distribution(entry) for entry in found}) > 1:
        raise PluginError(f'Several installed plugins provide "{name}" for {group}; uninstall all but one.')
    if found:
        if group != STORAGE_GROUP:  # storage is built in, and the installed plugins are recorded in it
            from sudoo import plugin_manager

            if not plugin_manager.entry_allowed(found[0], group):
                raise PluginError(f'The plugin "{name}" for {group} was not installed by the panel; it is not loaded.')
        return _create(found[0], settings)
    raise PluginError(f'No plugin "{name}" for {group}. Installed: {", ".join(available(group)) or "none"}.')


def _create(entry: EntryPoint, settings: Settings) -> Any:
    """Create the plugin class behind `entry`, once.

    A class written with the plugin SDK (a `SudooPlugin` subclass) is created with its `PluginContext`
    (plugin_context.py); every earlier plugin, built in or from the panel's own environment, receives the
    panel's Settings, the contract this module documents and tests/test_plugins.py pins.
    """
    cls = entry.load()
    base = _sdk_base()
    if base is not None and isinstance(cls, type) and issubclass(cls, base):
        from sudoo import plugin_context

        plugin = cls(plugin_context.build(entry))
        plugin.on_enable()  # the SDK's lifecycle: called at each start of the panel while the plugin is on
        return plugin
    return cls(settings)


def _sdk_base() -> type[Any] | None:
    """The SDK's SudooPlugin class, or None when the SDK cannot be imported: a panel whose SDK is missing or
    broken still loads its built-in plugins (and an SDK plugin then fails on its own import, safely)."""
    try:
        from sudoo_plugin_sdk import SudooPlugin
    except Exception:  # noqa: BLE001 - never a reason for the panel not to start
        return None
    return SudooPlugin
