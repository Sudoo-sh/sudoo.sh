"""Public addresses of the panel and the instances, through the exposure plugin chosen by the EXPOSURE setting.

The rest of the panel calls only the functions here; which plugin does the work is decided once, by configuration.
Built-in plugins: direct (direct.py, the default of new installations), proxy (proxy.py), tunnel (cloudflare.py; its
earlier name cloudflare-quick still works) and host-ports (ports.py, an earlier option). Writing one: docs/PLUGINS.md.
"""

from __future__ import annotations

import time
from functools import cache

from sudoo import plugin_guard, plugins
from sudoo.config import settings
from sudoo.exposure.base import Exposure, Target
from sudoo.instances import store

PANEL = "panel"

__all__ = [
    "PANEL",
    "Exposure",
    "Target",
    "address",
    "default_address",
    "ensure",
    "ensure_all",
    "provider",
    "remove",
    "wait_for_address",
]


@cache
def provider() -> Exposure:
    """The configured exposure plugin, created once; the default one (EXPOSURE's default) when it cannot be loaded
    (plugin_guard.py)."""
    plugin: Exposure = plugin_guard.load_safely(
        plugins.EXPOSURE_GROUP, settings.exposure, settings, plugin_guard.default_of("exposure")
    )
    return plugin


def target(name: str) -> Target:
    """The panel or the instance `name` as an exposure target."""
    if name == PANEL:
        return Target(PANEL, settings.panel_port, provider().panel_env())
    env = store.read_env(name)
    return Target(name, int(env["GATEWAY_PORT"]), env)


def ensure(name: str) -> None:
    provider().ensure(target(name))


def remove(name: str) -> None:
    provider().remove(target(name))


def address(name: str) -> str | None:
    """The public address: an instance's primary custom domain once it is served (domains/), else the plugin's."""
    if name != PANEL:
        from sudoo.instances import custom_domains as domains

        custom = domains.primary_url(name)
        if custom:
            return custom
    return default_address(name)


def default_address(name: str) -> str | None:
    """The address the exposure plugin gives (without custom domains)."""
    return provider().address(target(name))


def wait_for_address(name: str, timeout: float = 90) -> str | None:
    """Wait until `name` has a public address; return it, or None if it did not come in time."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = address(name)
        if found:
            return found
        time.sleep(3)
    return None


def ensure_all() -> None:
    """Expose the panel and every instance (when the panel starts)."""
    ensure(PANEL)
    for name in store.names():
        ensure(name)
