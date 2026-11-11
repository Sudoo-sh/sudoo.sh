"""Public addresses of the panel and the instances, through the exposure plugin chosen by the EXPOSURE setting.

The rest of the panel calls only the functions here; which plugin does the work is decided once, by configuration.
Built-in plugins: cloudflare-quick (cloudflare.py) and host-ports (ports.py). Writing one: docs/PLUGINS.md.
"""

from __future__ import annotations

import time
from functools import cache

from instance_manager import plugins
from instance_manager.config import settings
from instance_manager.exposure.base import Exposure, Target
from instance_manager.instances import store

PANEL = "panel"

__all__ = ["PANEL", "Exposure", "Target", "address", "ensure", "ensure_all", "provider", "remove", "wait_for_address"]


@cache
def provider() -> Exposure:
    """The configured exposure plugin, created once."""
    plugin: Exposure = plugins.load(plugins.EXPOSURE_GROUP, settings.exposure, settings)
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
