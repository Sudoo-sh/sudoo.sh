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

from sudoo import plugins
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
