"""Premium features (premium/): listed by the hub for the plan, downloaded, verified with the premium key (never a hub
key), bound to this server, loaded through their extension points, refused on another machine, switched off when the
plan drops them, and answered with 402 while absent. Against the fake hub's premium contract (fake_hub.py)."""

from __future__ import annotations

import io
import json
import shutil
import sys
import time
import zipfile
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import RESTARTS
from fake_hub import FakeHub, PremiumBuild, make_package, make_premium_package
from sudoo import entitlements, plugin_manager, premium
from sudoo.config import live
from sudoo.core import jobs
from sudoo.hub import client, marketplace
from sudoo.premium import keys, loader
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
PLAN = {
    "plan": "pro",
    "max_servers": 1,
    "max_production_branches": 2,
    "teams": True,
    "features": {"per_project_access": True, "custom_domains": False, "offsite_backups": True},
    "premium": ["teams"],
}


def _forget_modules() -> None:
    for name in [m for m in sys.modules if m.startswith("sudoo_premium_")]:
        del sys.modules[name]
    sys.path[:] = [p for p in sys.path if "/premium/" not in p]


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHub]:
    fake = FakeHub().start()
    monkeypatch.setattr(client, "hub_url", lambda: fake.url)
    monkeypatch.setattr(keys, "SHIPPED_PREMIUM_KEYS", (fake.premium_key.public,))
    for collection in (loader.COLLECTION, loader.STATE_COLLECTION, client.COLLECTION):
        for key in storage().all(collection):
            storage().delete(collection, key)
    shutil.rmtree(loader.premium_dir(), ignore_errors=True)
    loader.LOADED = set()
    live.change({"marketplace": True})
    fake.entitlements = dict(PLAN)
    fake.premium["teams"] = PremiumBuild("teams", "1.0.0", make_premium_package("teams", "1.0.0"), features=["teams"])
    yield fake
    live.change({"marketplace": None})
    fake.stop()
    loader.LOADED = set()
    plugin_manager._RESTART_PENDING.clear()
    _forget_modules()


def _sync() -> jobs.Job:
    job = loader.start_sync("test")
    assert job is not None
    deadline = time.time() + 20
    while job.finished is None and time.time() < deadline:
        time.sleep(0.05)
    assert job.finished is not None
    return job


def _restart() -> set[str]:
    """What a restart of the backend does: activate again from the records."""
    plugin_manager._RESTART_PENDING.clear()
    loader.LOADED = None
    _forget_modules()
    return loader.activate()


def test_a_package_the_plan_includes_installs_binds_and_loads(hub: FakeHub, admin: Client) -> None:
    marketplace.heartbeat()
    assert premium.included_ids() == {"teams"}
    assert loader.needs_sync()
    before = len(RESTARTS)
    job = _sync()
    assert job.status == "success", job.output
    rec = loader.record("teams")
    assert rec and rec["version"] == "1.0.0" and rec["python_tag"] == "py3" and not rec.get("problem")
    assert loader.problem(rec) is None
    assert plugin_manager.restart_pending()  # the backend restarts to load it
    del before
    assert _restart() == {"teams"}
    # its extension points: a blueprint and a background job, nothing outside the premium groups
    found = dict(premium.hooks(premium.BLUEPRINTS))
    assert "teams" in found
    app = Flask("check")
    app.register_blueprint(found["teams"])
    assert app.test_client().get("/premium-hello/teams").get_json() == {"data": {"hello": "teams"}}
    starts = dict(premium.hooks(premium.JOBS))
    starts["teams"]()
    assert sys.modules["sudoo_premium_teams"].STARTED == [1]
    # the web app sees it, with its hash-checked files
    web, _ = admin
    shown = {p["id"]: p for p in web.get("/api/v1/premium").get_json()["data"]["packages"]}
    assert shown["teams"]["status"] == "active" and shown["teams"]["ui"]["routes"][0]["path"] == "/teams"
    assert shown["domains"]["status"] == "not_included"
    page = web.get("/premium/ui/teams/ui/page.js")
    assert page.status_code == 200 and page.mimetype == "text/javascript" and b"Page" in page.data
    assert web.get("/premium/ui/teams/manifest.json").status_code == 404
    # nothing more to do until the next check
    assert not loader.needs_sync()


def test_a_copy_on_another_machine_is_not_loaded(hub: FakeHub, monkeypatch: pytest.MonkeyPatch) -> None:
    marketplace.heartbeat()
    assert _sync().status == "success"
    assert _restart() == {"teams"}
    real_machine_id = client.machine_id
    monkeypatch.setattr(client, "machine_id", lambda: "another-machine")  # the folder copied to another server
    rec = loader.record("teams")
    assert rec and "another machine" in (loader.problem(rec) or "")
    plugin_manager._RESTART_PENDING.clear()
    loader.watch()  # the integrity pass sees it at once and the restart unloads it
    assert plugin_manager.restart_pending()
    assert _restart() == set()
    assert "another machine" in (loader.record("teams") or {}).get("problem", "")
    # another installation id is refused the same way
    monkeypatch.setattr(client, "machine_id", real_machine_id)
    monkeypatch.setattr(loader, "server_id", lambda: "someone-else")
    assert "another server" in (loader.problem(loader.record("teams") or {}) or "")


def test_changed_files_and_expired_bindings_are_refused(hub: FakeHub) -> None:
    marketplace.heartbeat()
    assert _sync().status == "success"
    rec = loader.record("teams") or {}
    assert loader.problem(rec, now=time.time() + 40 * 86400) and "ended" in str(
        loader.problem(rec, now=time.time() + 40 * 86400)
    )
    module = loader.folder(rec) / "package" / "src" / "sudoo_premium_teams" / "__init__.py"
    module.chmod(0o600)
    module.write_text("print('changed')\n")
    assert _restart() == set()
    assert (loader.record("teams") or {}).get("tampered")


def test_the_premium_key_is_separate_from_the_hub_key(hub: FakeHub) -> None:
    marketplace.heartbeat()
    offer = loader.offers()[0]
    package = hub.premium["teams"].package
    sha = offer["package_sha256"]
    statement = "\n".join(("sudoo-plugin-v1", "teams", "1.0.0", sha)).encode()  # a marketplace-style signature
    forged = {**offer, "id": "teams", "sha256": sha, "signature": hub.key.sign(statement), "signing_key_id": hub.key_id}
    assert loader.signature_problem(forged) == "it is signed with a key this panel does not trust"
    with_premium_key = {
        **forged,
        "signature": hub.premium_key.sign(statement),
        "signing_key_id": hub.premium_key.key_id,
    }
    assert loader.signature_problem(with_premium_key) == "its signature is not valid"
    assert len(package) == offer["size"]


def test_compiled_modules_only_in_premium_packages_built_for_this_panel(hub: FakeHub) -> None:
    so = f"src/sudoo_premium_teams/core{loader.ext_suffix()}"
    source = {"src/sudoo_premium_teams/__init__.py": b"blueprint = None\n", so: b"\x7fELF"}
    tag, platform = loader.python_tag(), loader.platform_tag()
    built = make_premium_package("teams", "1.0.0", python_tag=tag, platform=platform, files=source)
    offer = {"id": "teams", "version": "1.0.0", "python_tag": tag, "platform": platform}
    with zipfile.ZipFile(io.BytesIO(built)) as archive:
        assert "sudoo_premium_teams" in loader.check_package(archive, offer)["modules"]
    pure = make_premium_package("teams", "1.0.0", files=source)
    with zipfile.ZipFile(io.BytesIO(pure)) as archive, pytest.raises(loader.PremiumRefused, match="compiled"):
        loader.check_package(archive, {**offer, "python_tag": "py3", "platform": "any"})
    wrong = {"src/sudoo_premium_teams/__init__.py": b"", "src/sudoo_premium_teams/core.cpython-99-x.so": b""}
    other = make_premium_package("teams", "1.0.0", python_tag=tag, platform=platform, files=wrong)
    with zipfile.ZipFile(io.BytesIO(other)) as archive, pytest.raises(loader.PremiumRefused, match="compiled"):
        loader.check_package(archive, offer)
    outside = make_premium_package("teams", "1.0.0", files={"src/evil/__init__.py": b""})
    with zipfile.ZipFile(io.BytesIO(outside)) as archive, pytest.raises(loader.PremiumRefused, match="sudoo_premium"):
        loader.check_package(archive, {**offer, "python_tag": "py3", "platform": "any"})
    groups = make_premium_package("teams", "1.0.0", entry_points={"sudoo.exposure": {"x": "sudoo_premium_teams:x"}})
    with zipfile.ZipFile(io.BytesIO(groups)) as archive, pytest.raises(loader.PremiumRefused, match="extension point"):
        loader.check_package(archive, {**offer, "python_tag": "py3", "platform": "any"})


def test_a_marketplace_plugin_cannot_use_premium_extension_points() -> None:
    manifest = {"entry_points": {"sudoo.premium.blueprints": {"x": "hello_plugin:Hello"}}}
    package = make_package("com.example.hello", "1.0.0", manifest=manifest)
    with zipfile.ZipFile(io.BytesIO(package)) as archive, pytest.raises(plugin_manager.PluginRefused):
        plugin_manager.check_package(archive, "com.example.hello", "1.0.0")


def test_the_plan_dropping_a_package_switches_it_off_and_keeps_it(hub: FakeHub) -> None:
    marketplace.heartbeat()
    assert _sync().status == "success"
    assert _restart() == {"teams"}
    hub.entitlements = {**PLAN, "premium": []}
    marketplace.heartbeat()
    assert premium.included_ids() == set()
    storage().put(loader.STATE_COLLECTION, "state", {})  # the retry wait is not what is tested here
    assert loader.needs_sync()
    assert _sync().status == "success"
    rec = loader.record("teams") or {}
    assert rec["switched_off"]["reason"] == "plan" and loader.folder(rec).is_dir()  # files and data kept
    assert any(p.name.startswith("panel__") for p in plugin_manager.panel_backups_dir().iterdir())
    assert _restart() == set()
    hub.entitlements = dict(PLAN)  # the plan includes it again: back on by itself
    marketplace.heartbeat()
    storage().put(loader.STATE_COLLECTION, "state", {})
    assert _sync().status == "success"
    assert not (loader.record("teams") or {}).get("switched_off") and _restart() == {"teams"}


def test_absent_packages_answer_402_with_the_plans(admin: Client) -> None:
    web, headers = admin
    for method, path in (("get", "/api/v1/offsite"), ("post", "/api/v1/support-access"), ("get", "/api/v1/servers"),
                         ("post", "/api/v1/teams")):  # fmt: skip
        answer = getattr(web, method)(path, json={}, headers=headers)
        assert answer.status_code == 402, path
        assert "installs automatically" in answer.get_json()["detail"]
    assert entitlements.current().plan == "free"
    shown = web.get("/api/v1/premium").get_json()["data"]
    assert {p["status"] for p in shown["packages"]} == {"not_included"}
    assert web.post("/api/v1/premium/sync", headers=headers).status_code == 202


def test_the_lease_lists_the_premium_packages() -> None:
    kept = entitlements.normalize({"plan": "business", "premium": ["teams", "servers", 3]})
    assert kept["premium"] == ["teams", "servers"]
    assert json.dumps(kept)


def test_the_heartbeat_reports_premium_packages_and_public_addresses(
    hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        marketplace, "_route_source", lambda family, target: "203.0.113.9" if "." in target else "fd00::1"
    )
    marketplace.heartbeat()
    assert _sync().status == "success"
    marketplace.heartbeat()
    sent = hub.heartbeats[-1]
    assert sent["premium"] == [{"id": "teams", "version": "1.0.0"}]
    assert "public_ipv6" not in sent  # a private address is never sent
    live.change({"public_ipv4": "8.8.4.4"})
    try:
        assert marketplace.public_addresses() == {"public_ipv4": "8.8.4.4"}
    finally:
        live.change({"public_ipv4": None})
