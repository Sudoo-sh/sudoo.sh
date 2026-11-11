"""Plugin loading, the built-in exposure plugins and add-on sources, and the Odoo version list."""

from __future__ import annotations

from pathlib import Path

import pytest

from instance_manager import plugins
from instance_manager.addons.folder import FolderSource, expand
from instance_manager.addons.git import GitSource
from instance_manager.config import Settings
from instance_manager.exposure.base import Exposure, Target
from instance_manager.exposure.cloudflare import CloudflareQuickTunnel
from instance_manager.exposure.ports import HostPorts
from instance_manager.instances import odoo


def test_built_in_plugins_are_registered() -> None:
    assert plugins.available(plugins.EXPOSURE_GROUP) == ["cloudflare-quick", "host-ports"]
    assert plugins.available(plugins.ADDON_SOURCE_GROUP) == ["folder", "git"]


def test_plugins_are_created_with_the_settings() -> None:
    settings = Settings(exposure="host-ports", public_host="demo.example.com")
    plugin = plugins.load(plugins.EXPOSURE_GROUP, "host-ports", settings)
    assert isinstance(plugin, HostPorts) and isinstance(plugin, Exposure)
    assert plugin.settings is settings


def test_unknown_plugin_names_the_installed_ones() -> None:
    with pytest.raises(plugins.PluginError, match="cloudflare-quick, host-ports"):
        plugins.load(plugins.EXPOSURE_GROUP, "carrier-pigeon", Settings())


def test_host_ports() -> None:
    with pytest.raises(ValueError, match="PUBLIC_HOST"):
        HostPorts(Settings())
    plugin = HostPorts(Settings(public_host="10.0.0.5", public_scheme="https"))
    assert plugin.listen_host == "0.0.0.0" and plugin.https
    assert plugin.address(Target("shop", 8071)) == "https://10.0.0.5:8071"
    assert plugin.instance_ports() == {}


def test_cloudflare_quick_tunnel() -> None:
    plugin = CloudflareQuickTunnel(Settings(connector_prefix="oim-connector-"))
    assert plugin.listen_host == "127.0.0.1" and plugin.https
    assert plugin.container(Target("shop", 8071)) == "oim-connector-shop"
    assert plugin.instance_ports() == {"CONNECTOR_METRICS_PORT": 20300}
    # no connector is listening on this status port, so there is no address yet
    assert plugin.address(Target("shop", 8071, {"CONNECTOR_METRICS_PORT": "1"})) is None


@pytest.mark.parametrize(
    ("pattern", "version", "expected"),
    [("/e/{version}", "19.0", "/e/19.0"), ("/e/v{major}", "19.0", "/e/v19"), ("ent-{major}", "saas-18.2", "ent-18")],
)
def test_enterprise_path_patterns(pattern: str, version: str, expected: str) -> None:
    assert expand(pattern, version) == expected


def test_folder_source(tmp_path: Path) -> None:
    source = FolderSource(Settings(enterprise_path=str(tmp_path / "odoo-{major}")))
    assert not source.available("19.0")
    (tmp_path / "odoo-19" / "web_enterprise").mkdir(parents=True)
    (tmp_path / "odoo-19" / "web_enterprise" / "__manifest__.py").write_text("{}")
    assert source.available("19.0")
    assert source.prepare("19.0", lambda _: None) == tmp_path / "odoo-19"
    with pytest.raises(FileNotFoundError):
        source.prepare("18.0", lambda _: None)


def test_git_source_needs_a_repository() -> None:
    with pytest.raises(ValueError, match="ENTERPRISE_GIT_URL"):
        GitSource(Settings())
    source = GitSource(Settings(enterprise_git_url="git@example.com:me/ent.git", enterprise_git_branch="ent-{major}"))
    assert source.branch("20.0") == "ent-20"


def test_versions_from_the_setting() -> None:
    assert odoo.versions() == ["18.0", "19.0", "20.0"]  # ODOO_VERSIONS set in conftest.py


def test_versions_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(odoo, "settings", Settings(odoo_versions="auto", odoo_min_version="17.0"))
    monkeypatch.setattr(odoo, "_VERSIONS_CACHE", ([], 0.0))
    monkeypatch.setattr(odoo, "_docker_hub_tags", lambda image: ["latest", "16.0", "19.0", "17.0", "saas-18.2", "20.0"])
    assert odoo.versions() == ["17.0", "19.0", "20.0"]
