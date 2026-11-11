"""Add-on source "folder": the Enterprise add-ons are already on the server, in a folder per version.

ENTERPRISE_PATH says where, with {version} (19.0) and {major} (19) replaced, so any existing naming works:

    /opt/odoo/enterprise/{version}      ->  /opt/odoo/enterprise/19.0      (the default)
    /data/odoo-enterprise-{major}       ->  /data/odoo-enterprise-19
    /srv/ent/v{major}/addons            ->  /srv/ent/v19/addons

A version counts as available when the folder contains the module ENTERPRISE_MARKER (web_enterprise).
"""

from __future__ import annotations

from pathlib import Path

from instance_manager.addons.base import Log
from instance_manager.config import Settings


def expand(pattern: str, version: str) -> str:
    """`pattern` with {version} and {major} replaced for `version`."""
    return pattern.format(version=version, major=version.split(".")[0].removeprefix("saas-"))


class FolderSource:
    """Enterprise add-ons in an existing folder per version."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def folder(self, version: str) -> Path:
        return Path(expand(self.settings.enterprise_path, version))

    def available(self, version: str) -> bool:
        return (self.folder(version) / self.settings.enterprise_marker / "__manifest__.py").is_file()

    def prepare(self, version: str, log: Log) -> Path:
        folder = self.folder(version)
        if not self.available(version):
            raise FileNotFoundError(f"No Enterprise add-ons for Odoo {version} in {folder}.")
        log(f"Enterprise add-ons: {folder}\n")
        return folder
