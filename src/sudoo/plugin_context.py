"""The PluginContext a plugin built with the plugin SDK is created with (docs/PLUGINS.md section 9).

A plugin written with `sudoo-plugin-sdk` subclasses `SudooPlugin` and is created once with a `PluginContext`;
the panel's earlier plugins (built in, or added to the panel's own environment) keep receiving the `Settings`
object, the contract `plugins.py` documents and `tests/test_plugins.py` pins. The context is built from the
plugin's install record and its verified manifest:

settings    the values of the settings the manifest declares. Their defaults, until the panel grows a settings
            page for plugins; no administrator-set values exist yet.
services    the core service layer (SDK ROADMAP section 11) does not exist in this panel version, so every
            access answers a typed ServiceError 501 instead of crashing on a missing attribute. Plugins that
            only need their settings, logger, storage, secrets or mail work today.
storage     the plugin's own records in the panel's storage, one collection prefix per plugin (storage.read;
            changing them also needs storage.write).
secrets     the panel's vault, one encrypted namespace per plugin (secrets).
mail        the panel's mailer, resolved on each call (mail.send).
data_dir    a folder only this plugin writes to, kept across updates and removed on uninstall.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from importlib.metadata import EntryPoint, PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, NoReturn

from sudoo_plugin_sdk import PluginContext, PluginSettings, ServiceError
from sudoo_plugin_sdk.context import (
    BackupService,
    DocsService,
    EventService,
    InstanceService,
    JobService,
    RouteService,
    ServerService,
    VersionService,
)

from sudoo import plugin_manager
from sudoo.core import vault
from sudoo.mail.base import Mailer
from sudoo.storage import storage

Document = dict[str, Any]

# A plugin's records live under "plugin-data:<id>:<collection>" and its encrypted values under
# "plugin-secrets:<id>". The separator is a colon because a plugin id can contain dots and dashes but never a
# colon (plugin_manager.PLUGIN_ID_RE), so no pair of ids can share or overlap a dotted prefix.
STORAGE_PREFIX = "plugin-data"
SECRETS_PREFIX = "plugin-secrets"


class _NoServices:
    """`context.services` while the panel has no core service layer: a typed 501, never an AttributeError."""

    def _unavailable(self, name: str) -> NoReturn:
        raise ServiceError(
            501,
            "Not available",
            f"The panel's core service layer (context.services.{name}) is planned for the plugin manager of "
            f"Sudoo.sh 2.0; this panel offers plugin SDK {plugin_manager.SDK_VERSION} without it.",
        )

    @property
    def instances(self) -> InstanceService:
        return self._unavailable("instances")

    @property
    def jobs(self) -> JobService:
        return self._unavailable("jobs")

    @property
    def backups(self) -> BackupService:
        return self._unavailable("backups")

    @property
    def versions(self) -> VersionService:
        return self._unavailable("versions")

    @property
    def docs(self) -> DocsService:
        return self._unavailable("docs")

    @property
    def server(self) -> ServerService:
        return self._unavailable("server")

    @property
    def events(self) -> EventService:
        return self._unavailable("events")

    @property
    def routes(self) -> RouteService:
        return self._unavailable("routes")


class _PluginStorage:
    """The plugin's own records in the panel's storage, kept separate from every other plugin's."""

    def __init__(self, plugin_id: str, read: bool, write: bool) -> None:
        self._id = plugin_id
        self._read = read
        self._write = write

    def _collection(self, collection: str) -> str:
        return f"{STORAGE_PREFIX}:{self._id}:{collection}"

    def _check_read(self) -> None:
        if not self._read:
            raise PermissionError(f"{self._id} was not granted the permission storage.read.")

    def _check_write(self) -> None:
        if not self._write:
            raise PermissionError(f"{self._id} was not granted the permission storage.write.")

    def get(self, collection: str, key: str) -> Document | None:
        self._check_read()
        return storage().get(self._collection(collection), key)

    def all(self, collection: str) -> dict[str, Document]:
        self._check_read()
        return storage().all(self._collection(collection))

    def put(self, collection: str, key: str, document: Document) -> None:
        self._check_write()
        storage().put(self._collection(collection), key, document)

    def delete(self, collection: str, key: str) -> bool:
        self._check_write()
        return storage().delete(self._collection(collection), key)

    def change(self, collection: str, key: str, fn: Callable[[Document | None], Document | None]) -> Document | None:
        self._check_read()  # fn is given the current document: changing reads too
        self._check_write()
        return storage().change(self._collection(collection), key, fn)


class _PluginSecrets:
    """The plugin's secrets, encrypted with the panel's vault under the plugin's own label."""

    def __init__(self, plugin_id: str) -> None:
        self._label = f"{SECRETS_PREFIX}:{plugin_id}"
        self._collection = f"{SECRETS_PREFIX}:{plugin_id}"

    def get(self, name: str) -> str | None:
        found = storage().get(self._collection, name)
        return None if found is None else vault.decrypt(str(found["value"]), self._label)

    def put(self, name: str, value: str) -> None:
        storage().put(self._collection, name, {"value": vault.encrypt(value, self._label)})

    def delete(self, name: str) -> bool:
        return storage().delete(self._collection, name)


class _PluginMail:
    """The panel's mailer (permission mail.send), resolved on each call so a mailer plugin's context never
    loads the plugin itself; a plugin that IS the configured mailer is refused instead of recursing."""

    def __init__(self, plugin_id: str) -> None:
        self._id = plugin_id

    def _mailer(self) -> Mailer:
        from importlib.metadata import entry_points

        from sudoo import mail, plugins
        from sudoo.config import settings

        for entry in entry_points(group=plugins.MAILER_GROUP):
            if entry.name == settings.mailer and plugin_manager.plugin_id_of(entry) == self._id:
                raise RuntimeError(f"{self._id} is the panel's mailer; it cannot send through the mail capability.")
        return mail.mailer()

    @property
    def ready(self) -> bool:
        return self._mailer().ready

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        self._mailer().send(to, subject, text, html)


def _declared_settings(rec: dict[str, Any]) -> dict[str, Any]:
    """Each setting the plugin's verified manifest declares, at its default (no settings page for plugins yet).
    A manifest that cannot be read or parsed means changed files, so the plugin is refused, not defaulted."""
    from sudoo.plugins import PluginError

    try:
        manifest = json.loads((plugin_manager.folder(rec) / "package" / "manifest.json").read_text())
    except (OSError, ValueError) as error:
        raise PluginError(
            f"The manifest of the plugin {rec['id']} cannot be read; its files may have changed."
        ) from error
    values: dict[str, Any] = {}
    for item in manifest.get("settings") or []:
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            values[item["key"]] = item.get("default")
    return values


def _data_dir(rec: dict[str, Any]) -> Path:
    data_dir = plugin_manager.plugins_dir() / str(rec["id"]) / "data"
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return data_dir


def _sudoo_version() -> str:
    try:
        return package_version("sudoo")
    except PackageNotFoundError:
        return "0.0.0"


def build(entry: EntryPoint) -> PluginContext:
    """The PluginContext for the SDK-built plugin behind `entry`, from its install record (plugin_manager.py)."""
    from sudoo.plugins import PluginError

    plugin_id = plugin_manager.plugin_id_of(entry)
    rec = plugin_manager.record(plugin_id) if plugin_id is not None else None
    if plugin_id is None or rec is None:
        raise PluginError(
            "A plugin built with the plugin SDK must be installed from the marketplace; "
            "the panel's own environment has no plugin record for it."
        )
    permissions = frozenset(str(permission) for permission in rec.get("permissions") or [])
    return PluginContext(
        plugin_id=plugin_id,
        version=str(rec["version"]),
        settings=PluginSettings(_declared_settings(rec)),
        services=_NoServices(),
        logger=logging.getLogger(f"sudoo.plugins.{plugin_id}"),
        data_dir=_data_dir(rec),
        permissions=permissions,
        storage=(
            _PluginStorage(plugin_id, read="storage.read" in permissions, write="storage.write" in permissions)
            if {"storage.read", "storage.write"} & permissions
            else None
        ),
        secrets=_PluginSecrets(plugin_id) if "secrets" in permissions else None,
        mail=_PluginMail(plugin_id) if "mail.send" in permissions else None,
        sudoo_version=_sudoo_version(),
    )
