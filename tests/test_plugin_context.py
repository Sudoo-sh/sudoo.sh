"""Plugins built with the plugin SDK are created with a PluginContext (plugin_context.py); built-ins keep the
panel's Settings object (test_plugins.py::test_plugins_are_created_with_the_settings)."""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sudoo_plugin_sdk import PluginContext, ServiceError, SudooPlugin

from sudoo import plugin_manager, plugins
from sudoo.config import Settings
from sudoo.plugin_context import SECRETS_PREFIX, STORAGE_PREFIX
from sudoo.storage import storage

PLUGIN_ID = "com.example.fake-sdk"
MODULE = "fake_sdk_plugin"
ENTRY = "fake-sdk"

SOURCE = '''\
"""A plugin written with the plugin SDK, for the panel's load-path tests."""

from __future__ import annotations

from sudoo_plugin_sdk import PluginContext, SudooPlugin


class FakeSdkMailer(SudooPlugin):
    """A mailer written against the SDK: created with a PluginContext, not the panel's Settings."""

    def __init__(self, context: PluginContext) -> None:
        super().__init__(context)
        self.api_key = self.settings.string("api_key")
        self.region = self.settings.string("region", "eu")

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        raise RuntimeError("a test plugin never sends")
'''

MANIFEST = {
    "id": PLUGIN_ID,
    "name": "Fake SDK mailer",
    "version": "0.1.0",
    "type": "mailer",
    "entry_points": {plugins.MAILER_GROUP: {ENTRY: f"{MODULE}:FakeSdkMailer"}},
    "settings": [
        {"key": "api_key", "type": "secret", "label": "API key"},
        {"key": "region", "type": "choice", "label": "Region", "default": "eu"},
    ],
    "permissions": ["storage.read", "storage.write", "secrets", "mail.send"],
}


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[list[str]], None]]:
    """Install the fake SDK plugin exactly like a marketplace install (the layout plugin_manager._unpack makes)."""

    def install(permissions: list[str]) -> None:
        base = plugin_manager.plugins_dir() / PLUGIN_ID / MANIFEST["version"]
        package = base / "package"
        package.mkdir(parents=True)
        (package / f"{MODULE}.py").write_text(SOURCE)
        manifest = {**MANIFEST, "permissions": permissions}
        (package / "manifest.json").write_text(json.dumps(manifest))
        plugin_manager._write_meta(base / "meta", manifest)
        storage().put(
            plugin_manager.COLLECTION,
            PLUGIN_ID,
            {
                "id": PLUGIN_ID,
                "name": MANIFEST["name"],
                "version": MANIFEST["version"],
                "entry_points": manifest["entry_points"],
                "permissions": permissions,
            },
        )
        monkeypatch.setattr(plugin_manager, "LOADED", {PLUGIN_ID})
        for path in (base / "meta", package):
            monkeypatch.syspath_prepend(str(path))
        importlib.invalidate_caches()

    yield install
    sys.modules.pop(MODULE, None)
    storage().delete(plugin_manager.COLLECTION, PLUGIN_ID)
    for collection in (f"{STORAGE_PREFIX}:{PLUGIN_ID}:state", f"{SECRETS_PREFIX}:{PLUGIN_ID}"):
        for key in storage().all(collection):
            storage().delete(collection, key)
    shutil.rmtree(plugin_manager.plugins_dir() / PLUGIN_ID, ignore_errors=True)


def test_sdk_plugin_is_created_with_a_context(installed: Callable[[list[str]], None]) -> None:
    installed(["storage.read", "storage.write", "secrets", "mail.send"])
    plugin = plugins.load(plugins.MAILER_GROUP, ENTRY, Settings())
    assert isinstance(plugin, SudooPlugin)
    context = plugin.context
    assert isinstance(context, PluginContext)
    assert context.plugin_id == PLUGIN_ID and context.version == "0.1.0"
    assert plugin.region == "eu"  # the manifest's default; no administrator-set values exist yet
    assert plugin.api_key == "" and not plugin.ready
    assert context.permissions == frozenset({"storage.read", "storage.write", "secrets", "mail.send"})
    assert context.logger.name == f"sudoo.plugins.{PLUGIN_ID}"
    assert context.data_dir.is_dir() and context.data_dir.parent == plugin_manager.plugins_dir() / PLUGIN_ID
    assert context.sudoo_version  # the panel's own version


def test_context_storage_is_the_panels_own_namespaced_per_plugin(installed: Callable[[list[str]], None]) -> None:
    installed(["storage.read", "storage.write"])
    plugin = plugins.load(plugins.MAILER_GROUP, ENTRY, Settings())
    assert plugin.context.storage is not None and plugin.context.secrets is None and plugin.context.mail is None
    plugin.context.storage.put("state", "runs", {"n": 1})
    assert plugin.context.storage.get("state", "runs") == {"n": 1}
    assert storage().get(f"{STORAGE_PREFIX}:{PLUGIN_ID}:state", "runs") == {"n": 1}  # the plugin's own collection


def test_context_storage_writes_need_storage_write(installed: Callable[[list[str]], None]) -> None:
    installed(["storage.read"])
    plugin = plugins.load(plugins.MAILER_GROUP, ENTRY, Settings())
    assert plugin.context.storage is not None
    assert plugin.context.storage.get("state", "runs") is None
    with pytest.raises(PermissionError, match="storage.write"):
        plugin.context.storage.put("state", "runs", {"n": 1})


def test_context_storage_change_reads_too(installed: Callable[[list[str]], None]) -> None:
    installed(["storage.write"])
    plugin = plugins.load(plugins.MAILER_GROUP, ENTRY, Settings())
    assert plugin.context.storage is not None
    with pytest.raises(PermissionError, match="storage.read"):
        plugin.context.storage.get("state", "runs")
    with pytest.raises(PermissionError, match="storage.read"):  # change hands the current document to fn
        plugin.context.storage.change("state", "runs", lambda document: document)


def test_context_secrets_are_encrypted_in_the_vault(installed: Callable[[list[str]], None]) -> None:
    installed(["secrets"])
    plugin = plugins.load(plugins.MAILER_GROUP, ENTRY, Settings())
    assert plugin.context.secrets is not None
    plugin.context.secrets.put("token", "s3cr3t")
    kept = storage().get(f"{SECRETS_PREFIX}:{PLUGIN_ID}", "token")
    assert kept is not None and kept["value"] != "s3cr3t"  # never kept as plain text
    assert plugin.context.secrets.get("token") == "s3cr3t"
    assert plugin.context.secrets.delete("token") and plugin.context.secrets.get("token") is None


def test_context_without_permissions_has_no_capabilities(installed: Callable[[list[str]], None]) -> None:
    installed([])
    plugin = plugins.load(plugins.MAILER_GROUP, ENTRY, Settings())
    assert plugin.context.permissions == frozenset()
    assert plugin.context.storage is None and plugin.context.secrets is None and plugin.context.mail is None


def test_context_services_answer_a_typed_501_until_the_service_layer_exists(
    installed: Callable[[list[str]], None],
) -> None:
    installed([])
    plugin = plugins.load(plugins.MAILER_GROUP, ENTRY, Settings())
    with pytest.raises(ServiceError, match="Not available") as raised:
        _ = plugin.context.services.instances
    assert raised.value.status == 501


def test_sdk_plugin_with_an_unreadable_manifest_is_refused(installed: Callable[[list[str]], None]) -> None:
    installed([])
    (plugin_manager.folder(plugin_manager.record(PLUGIN_ID)) / "package" / "manifest.json").write_text("{")
    with pytest.raises(plugins.PluginError, match="cannot be read"):
        plugins.load(plugins.MAILER_GROUP, ENTRY, Settings())


def test_mailer_plugin_cannot_send_through_itself(
    installed: Callable[[list[str]], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    installed(["mail.send"])
    plugin = plugins.load(plugins.MAILER_GROUP, ENTRY, Settings(mailer=ENTRY))
    monkeypatch.setattr("sudoo.config.settings.mailer", ENTRY)
    assert plugin.context.mail is not None
    with pytest.raises(RuntimeError, match="is the panel's mailer"):
        _ = plugin.context.mail.ready


def test_sdk_plugin_from_the_panels_own_environment_is_refused(
    installed: Callable[[list[str]], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    installed([])
    monkeypatch.setattr(plugin_manager, "plugin_id_of", lambda entry: None)
    with pytest.raises(plugins.PluginError, match="marketplace"):
        plugins.load(plugins.MAILER_GROUP, ENTRY, Settings())
