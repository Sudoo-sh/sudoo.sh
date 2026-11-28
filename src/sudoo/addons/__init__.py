"""Odoo Enterprise add-ons, through the add-on source plugin chosen by the ENTERPRISE_SOURCE setting.

ENTERPRISE_SOURCE=none turns Enterprise off. Built-in sources: folder (folder.py) and git (git.py). Writing one:
docs/PLUGINS.md.
"""

from __future__ import annotations

import os
import re
import threading
from functools import cache
from pathlib import Path

from sudoo import plugin_guard, plugins
from sudoo.addons.base import AddonSource, Log
from sudoo.config import settings

__all__ = ["AddonSource", "available", "enterprise", "owns", "prepare"]

_PREPARED: set[str] = set()  # folders prepare() returned since the panel started
_PREPARED_LOCK = threading.Lock()
_VERSION = r"[0-9A-Za-z][0-9A-Za-z.~_-]*"  # what {version} may stand for: one path component, never ".."


@cache
def enterprise() -> AddonSource | None:
    """The configured source of Enterprise add-ons, created once; None when Enterprise is turned off."""
    if settings.enterprise_source == "none":
        return None
    # a source that cannot be loaded turns Enterprise off, with the reason shown to the administrators
    source: AddonSource | None = plugin_guard.load_safely(
        plugins.ADDON_SOURCE_GROUP, settings.enterprise_source, settings
    )
    return source


plugin_guard.on_reset(plugins.ADDON_SOURCE_GROUP, enterprise.cache_clear)


def available(version: str) -> bool:
    """True when Enterprise can be offered for `version` (False when the source fails to answer)."""
    source = enterprise()
    if source is None:
        return False
    try:
        return bool(source.available(version))
    except Exception:  # noqa: BLE001 - counted and shown by the plugin's guard; Enterprise is not offered meanwhile
        return False


def prepare(version: str, log: Log) -> Path:
    """Make the Enterprise add-ons of `version` ready and return their folder on the server."""
    source = enterprise()
    if source is None:
        if settings.enterprise_source != "none":
            raise RuntimeError(
                f'The Enterprise add-on source "{settings.enterprise_source}" failed to load, so Enterprise is off; '
                "an administrator sees why on the panel's health banner."
            )
        raise RuntimeError("Enterprise is turned off on this server (ENTERPRISE_SOURCE=none).")
    folder = source.prepare(version, log)
    with _PREPARED_LOCK:
        _PREPARED.add(os.path.normpath(folder))
    return folder


def _pattern(template: str) -> re.Pattern[str]:
    """A regular expression for the folders `template` names, {version} and {major} standing for any version."""
    parts = re.split(r"(\{version\}|\{major\})", template)
    wanted = {"{version}": _VERSION, "{major}": "[0-9]+"}
    return re.compile("".join(wanted.get(part) or re.escape(part) for part in parts))


def owns(folder: Path) -> bool:
    """Whether `folder` is a folder of Enterprise add-ons on this server, the only host folder besides its own that an
    instance may get: one ENTERPRISE_PATH names for some version, a version folder of the git source
    (<ODOO_ROOT>/enterprise-git/<version>), one prepare() returned since the panel started, or one the configured
    source plugin says is its own (its optional owns(folder) method). A source plugin without that method is trusted
    with any folder outside the system folders the compose check refuses anyway."""
    candidates = {os.path.normpath(folder), os.path.realpath(folder)}
    with _PREPARED_LOCK:
        if candidates & _PREPARED:
            return True
    built_in = (
        _pattern(os.path.normpath(settings.enterprise_path)),
        _pattern(os.path.join(os.path.normpath(settings.odoo_root), "enterprise-git", "{version}")),
    )
    if any(pattern.fullmatch(candidate) for pattern in built_in for candidate in candidates):
        return True
    try:
        source = enterprise()
    except Exception:  # noqa: BLE001 - a source that cannot be set up vouches for nothing
        return False
    from sudoo.addons.folder import FolderSource
    from sudoo.addons.git import GitSource

    if source is None or isinstance(source, FolderSource | GitSource):
        return False
    check = getattr(source, "owns", None)
    return bool(check(folder)) if callable(check) else True
