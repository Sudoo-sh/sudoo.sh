"""The interface every add-on source plugin implements: where the Odoo Enterprise add-ons of a version come from.

The panel mounts the folder a source returns, read-only, into every Enterprise instance of that version. Sources
decide how that folder is found or made: an existing folder (folder.py), a git branch per version (git.py), or
anything a plugin package provides.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

Log = Callable[[str], None]


@runtime_checkable
class AddonSource(Protocol):
    """Provides the Enterprise add-ons of each Odoo version."""

    def available(self, version: str) -> bool:
        """True when the add-ons of `version` can be used (now, or after prepare())."""
        ...

    def prepare(self, version: str, log: Log) -> Path:
        """Make the add-ons of `version` ready on this server and return their folder (on the host)."""
        ...
