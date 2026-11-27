"""The panel stays up when something fails (core/resilience.py, plugin_guard.py): plugins that fail to import, to
be created or on every call, for each plugin group; dependencies that are down at start; settings with wrong
values; an exception in a request; an exception in a background job; a stuck job command; full queues; and a real
plugin built with the plugin SDK, loaded end to end."""

from __future__ import annotations

import importlib
import json
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from conftest import sign_in
from sudoo import addons, auth, exposure, limits, mail, plugin_guard, plugin_manager, plugins, triggers
from sudoo.config import LiveSettings, Settings, settings
from sudoo.core import audit, jobs, resilience
from sudoo.mail.base import MailError
from sudoo.storage import storage

MODULE = "fake_failing_plugins"
GROUPS = (
    plugins.AUTH_GROUP,
    plugins.SECOND_FACTOR_GROUP,
    plugins.STORAGE_GROUP,
    plugins.MAILER_GROUP,
    plugins.RATE_LIMITER_GROUP,
    plugins.EXPOSURE_GROUP,
    plugins.ADDON_SOURCE_GROUP,
    plugins.UPDATE_TRIGGER_GROUP,
)

SOURCE = '''\
"""Plugins that fail, for tests/test_resilience.py: in their constructor, or on every call."""


class Broken:
    def __init__(self, settings):
        raise ConnectionError("cannot reach db.internal:5432 as https://user:hunter2@db.internal")


class Faulty:
    """Every method and property raises."""

    name = "faulty"
    label = "Faulty"
    manages_passwords = True

    def __init__(self, settings):
        self.settings = settings

    def _fail(self, *args, **kwargs):
        raise RuntimeError("the plugin's own bug")

    authenticate = hit = send = enrolled = challenge = verify = begin_setup = finish_setup = remove = _fail
    start = blueprint = prepare = ensure = address = panel_env = instance_ports = _fail
    get = all = put = delete = change = _fail

    @property
    def available(self):
        raise RuntimeError("the plugin's own bug")

    @property
    def ready(self):
        raise RuntimeError("the plugin's own bug")

    @property
    def https(self):
        raise RuntimeError("the plugin's own bug")
'''


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    """Each test starts with no failure recorded and no plugin created, and leaves none behind."""

    def reset() -> None:
        plugin_guard.forget_all()
        resilience.clear()
        for forget in (auth.authenticator, auth._all_factors, mail.mailer, limits.limiter, exposure.provider,
                       addons.enterprise, triggers.enabled):  # fmt: skip
            forget.cache_clear()
        for key in storage().all(plugin_guard.COLLECTION):
            storage().delete(plugin_guard.COLLECTION, key)

    reset()
    yield
    reset()


@pytest.fixture
def failing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three plugins in every group, installed in the panel's environment: bad-import (its module raises when
    imported), broken (its constructor raises) and faulty (every call raises)."""
    (tmp_path / f"{MODULE}.py").write_text(SOURCE)
    (tmp_path / "fake_bad_import.py").write_text('raise ImportError("a dependency of this plugin is missing")\n')
    meta = tmp_path / f"{MODULE}-1.0.dist-info"
    meta.mkdir()
    (meta / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {MODULE}\nVersion: 1.0\n")
    lines = []
    for group in GROUPS:
        lines += [f"[{group}]", "bad-import = fake_bad_import:Nothing", f"broken = {MODULE}:Broken",
                  f"faulty = {MODULE}:Faulty", ""]  # fmt: skip
    (meta / "entry_points.txt").write_text("\n".join(lines))
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    yield
    sys.modules.pop(MODULE, None)
    sys.modules.pop("fake_bad_import", None)
    importlib.invalidate_caches()


def _failed(group: str, name: str) -> dict[str, Any]:
    found = [s for s in plugin_guard.failures() if s["group"] == group and s["name"] == name]
    assert found, plugin_guard.failures()
    return found[0]


# ---------------------------------------------------------------- loading: import and construction


@pytest.mark.parametrize("name", ["bad-import", "broken", "missing-plugin"])
def test_a_sign_in_plugin_that_cannot_be_loaded_falls_back_to_the_panels_accounts(
    app: Flask, passwords: dict[str, str], failing: None, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setattr(settings, "auth", name)
    from sudoo.auth.local import LocalAccounts

    assert isinstance(auth.authenticator(), LocalAccounts)
    sign_in(app, "admin1", passwords["admin"])  # the administrators can still sign in
    state = _failed(plugins.AUTH_GROUP, name)
    assert state["stage"] == "load" and not state["disabled"]
    assert "hunter2" not in state["error"]  # the error is kept without the secret in it
    assert any(p["kind"] == "plugin" and name in p["key"] for p in resilience.problems())
    assert any(e["event"] == "plugin failed" for e in audit.latest(20))


def test_second_factors_that_cannot_be_loaded_never_drop_the_requirement(
    failing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "second_factors", "totp,bad-import,broken,faulty")
    found = auth.factors()
    assert set(found) == {"totp", "bad-import", "broken", "faulty"}  # totp still works; the others ask for themselves
    assert isinstance(found["broken"], auth.UnavailableFactor)
    assert isinstance(found["faulty"], auth.UnavailableFactor)  # its "available" raised
    account = {"name": "sam", "second_factors": {"broken": {}}}
    assert "broken" in auth.enrolled(account)  # set up before it broke: still asked for, never skipped
    with pytest.raises(Exception, match="not available right now"):
        found["broken"].challenge("sam", account, auth.RequestInfo("127.0.0.1", "", time.time()))
    monkeypatch.setattr(settings, "require_second_factor", "everyone")
    assert auth.must_use_second_factor(account)


def test_a_storage_plugin_that_cannot_reach_its_database_falls_back_to_the_panels_own(
    failing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sudoo.storage as storage_module

    kept = storage_module._STORAGE
    monkeypatch.setattr(storage_module, "_STORAGE", None)
    monkeypatch.setattr(settings, "storage", "broken")  # its constructor: ConnectionError, database unreachable
    try:
        from sudoo.storage.sqlite import SqliteStorage

        assert isinstance(storage(), SqliteStorage)
        assert storage().get("users", "admin1") is not None or storage().all("users") is not None
        assert _failed(plugins.STORAGE_GROUP, "broken")["stage"] == "load"
    finally:
        storage_module._STORAGE = kept


@pytest.mark.parametrize("name", ["bad-import", "broken"])
def test_mailer_exposure_and_rate_limiter_plugins_that_cannot_be_loaded_use_the_defaults(
    failing: None, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    from sudoo.limits.memory import MemoryRateLimiter
    from sudoo.mail.smtp import Smtp

    monkeypatch.setattr(settings, "mailer", name)
    monkeypatch.setattr(settings, "exposure", name)
    monkeypatch.setattr(settings, "rate_limiter", name)
    assert isinstance(mail.mailer(), Smtp)
    assert type(exposure.provider()).__module__.startswith("sudoo.exposure.")
    assert isinstance(limits.limiter(), MemoryRateLimiter)
    for group in (plugins.MAILER_GROUP, plugins.EXPOSURE_GROUP, plugins.RATE_LIMITER_GROUP):
        assert _failed(group, name)["stage"] == "load"


@pytest.mark.parametrize("name", ["bad-import", "broken"])
def test_an_addon_source_that_cannot_be_loaded_turns_only_enterprise_off(
    failing: None, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setattr(settings, "enterprise_source", name)
    assert addons.enterprise() is None
    assert not addons.available("19.0")
    with pytest.raises(RuntimeError, match="failed to load"):
        addons.prepare("19.0", lambda text: None)


def test_one_update_trigger_that_fails_never_blocks_the_others(failing: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "update_triggers", "polling,bad-import,broken,faulty")
    names = [t.__class__.__name__ for t in triggers.enabled()]
    assert names == ["Polling", "Faulty"]  # the two that cannot be loaded are left out
    started: list[str] = []
    polling = triggers.enabled()[0]
    monkeypatch.setattr(polling, "start", lambda request_update: started.append("polling"))
    triggers.start_all()  # faulty.start raises: listed, the others start
    assert started == ["polling"]
    assert triggers.blueprints() is not None  # faulty.blueprint raises: it simply has no webhook
    assert any("update trigger" in p["key"] for p in resilience.problems() if p["kind"] == "startup")


# ---------------------------------------------------------------- calls


def test_a_sign_in_plugin_failing_on_every_call_is_answered_by_local_accounts_then_switched_off(
    app: Flask, passwords: dict[str, str], failing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth", "faulty")
    monkeypatch.setattr(settings, "plugin_failure_limit", 2)
    assert isinstance(auth.authenticator(), plugin_guard.Guarded)
    sign_in(app, "admin1", passwords["admin"])  # the call failed; the panel's accounts answered it
    assert _failed(plugins.AUTH_GROUP, "faulty")["failures"] == 1
    sign_in(app, "admin1", passwords["admin"])
    state = _failed(plugins.AUTH_GROUP, "faulty")
    assert state["disabled"] and state["stage"] == "call"
    from sudoo.auth.local import LocalAccounts

    assert isinstance(auth.authenticator(), LocalAccounts)  # switched off: the built-in default from now on
    assert storage().get(plugin_guard.COLLECTION, f"{plugins.AUTH_GROUP}:faulty")["disabled"]  # kept for restarts
    assert any(e["event"] == "plugin switched off (kept failing)" for e in audit.latest(20))


def test_an_administrator_switches_a_failed_plugin_on_again(
    app: Flask, admin: tuple[Any, dict[str, str]], failing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = admin
    monkeypatch.setattr(settings, "rate_limiter", "faulty")
    monkeypatch.setattr(settings, "plugin_failure_limit", 1)
    limits.limiter.cache_clear()  # signing in used the built-in one
    limits.limiter().hit("reads:test", 10)  # fails once, answered by the built-in limiter, switched off
    answer = client.get("/api/v1/health")
    assert answer.status_code == 200
    body = answer.get_json()["data"]
    assert body["status"] == "degraded"
    assert [p for p in body["plugins"] if p["name"] == "faulty" and p["disabled"]]
    answer = client.post(f"/api/v1/health/plugins/{plugins.RATE_LIMITER_GROUP}/faulty/enable", headers=headers)
    assert answer.status_code == 200
    assert isinstance(limits.limiter(), plugin_guard.Guarded)  # loaded again
    assert client.post("/api/v1/health/plugins/sudoo.auth/never-failed/enable", headers=headers).status_code == 404


def test_every_call_of_each_group_is_guarded(failing: None, monkeypatch: pytest.MonkeyPatch) -> None:
    for setting in ("mailer", "rate_limiter", "exposure", "enterprise_source"):
        monkeypatch.setattr(settings, setting, "faulty")
    with pytest.raises(MailError, match="e-mail plugin faulty failed"):  # every sender handles MailError
        mail.mailer().send("sam@example.com", "Hi", "text")
    assert limits.check_request("ip:10.0.0.1", changes=False) is not None  # the built-in limiter answered
    with pytest.raises(RuntimeError, match="own bug"):  # passed on to the request or job, which ends cleanly
        exposure.provider().ensure(exposure.Target("shop", 8069))
    assert not addons.available("19.0")
    for group in (plugins.MAILER_GROUP, plugins.RATE_LIMITER_GROUP, plugins.EXPOSURE_GROUP, plugins.ADDON_SOURCE_GROUP):
        assert _failed(group, "faulty")["stage"] == "call"


def test_a_success_resets_the_count_of_failures_in_a_row(failing: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "plugin_failure_limit", 2)
    plugin_guard.failed(plugins.MAILER_GROUP, "faulty", "call", RuntimeError("one"))
    plugin_guard.succeeded(plugins.MAILER_GROUP, "faulty")
    assert not plugin_guard.failed(plugins.MAILER_GROUP, "faulty", "call", RuntimeError("two"))
    assert plugin_guard.failed(plugins.MAILER_GROUP, "faulty", "call", RuntimeError("three"))


def test_storage_is_never_switched_off_while_the_panel_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "plugin_failure_limit", 1)
    assert not plugin_guard.failed(plugins.STORAGE_GROUP, "remote-db", "call", RuntimeError("down"))
    assert not plugin_guard.disabled(plugins.STORAGE_GROUP, "remote-db")


def test_guarded_plugins_keep_their_class_for_isinstance(failing: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mailer", "faulty")
    plugin = mail.mailer()
    assert type(plugin).__name__ == "Guarded" and plugin.__class__.__name__ == "Faulty"
    assert plugin.label == "Faulty"


# ---------------------------------------------------------------- start-up


def test_the_panel_starts_with_failing_plugins_and_dependencies_down(
    app: Flask, passwords: dict[str, str], failing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every plugin named in the settings fails, Docker/the hub are down when the background work starts, the live
    events cannot start, mail points at a closed port: the panel still answers its health check, the sign-in and
    the administrators' view of what is wrong."""
    from sudoo import background
    from sudoo.web import create_app
    from sudoo.web.v1 import stream

    for setting, name in (("auth", "broken"), ("mailer", "bad-import"), ("exposure", "broken"),
                          ("rate_limiter", "faulty"), ("update_triggers", "polling,broken,faulty"),
                          ("second_factors", "totp,bad-import")):  # fmt: skip
        monkeypatch.setattr(settings, setting, name)
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", 9)  # nothing listens: the mail server is unreachable
    monkeypatch.setattr(settings, "mail_from", "panel@example.com")

    def docker_is_down() -> None:
        raise ConnectionRefusedError("Cannot connect to the Docker daemon at unix:///var/run/docker.sock")

    monkeypatch.setattr(background, "start", docker_is_down)
    monkeypatch.setattr(stream, "start", lambda application: docker_is_down())
    second = create_app()
    second.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = second.test_client()
    answer = client.get("/healthz")
    assert answer.status_code == 200 and answer.get_data(as_text=True) == "degraded"
    sign_in(second, "admin1", passwords["admin"])
    keys = {p["key"] for p in resilience.problems()}
    assert {"background work", "live events"} <= keys
    with pytest.raises(MailError):  # unreachable mail: an error the callers handle, never a crash
        mail.mailer().send("sam@example.com", "Test", "The mail server is down.")


def test_the_health_check_says_unavailable_when_storage_cannot_be_read(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app.test_client()
    assert client.get("/healthz").get_data(as_text=True) == "ok"
    import sudoo.storage as storage_module

    class Down:
        def __getattr__(self, name: str) -> Any:
            raise OSError("disk I/O error")

    kept = storage_module._STORAGE
    storage_module._STORAGE = Down()  # type: ignore[assignment]
    try:
        answer = client.get("/healthz")
    finally:
        storage_module._STORAGE = kept
    assert answer.status_code == 503 and answer.get_data(as_text=True) == "unavailable"


def test_settings_with_wrong_values_take_their_defaults_instead_of_stopping_the_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDLE_HOURS", "soon")
    monkeypatch.setenv("SMTP_PORT", "not-a-port-hunter2")
    with pytest.raises(ValueError):
        Settings()  # alone, it still refuses
    current = LiveSettings()
    assert current.idle_hours == Settings.model_fields["idle_hours"].default
    assert current.smtp_port == Settings.model_fields["smtp_port"].default
    problems = current.problems()
    assert set(problems) == {"idle_hours", "smtp_port"}
    assert "hunter2" not in json.dumps(problems)  # the reason, never the value
    monkeypatch.setattr(resilience, "live", current)
    assert {p["key"] for p in resilience.problems() if p["kind"] == "config"} == {"idle_hours", "smtp_port"}


# ---------------------------------------------------------------- requests


def test_an_exception_in_a_request_is_a_clean_answer_with_a_request_id(
    app: Flask, admin: tuple[Any, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = admin

    def bug() -> list[dict[str, Any]]:
        raise KeyError("a bug in a view")

    monkeypatch.setattr("sudoo.web.v1.health.resilience.problems", bug)
    answer = client.get("/api/v1/health", headers={"X-Request-ID": "req-12345678"})
    assert answer.status_code == 500
    body = answer.get_json()
    assert body["request_id"] == "req-12345678" and "unexpected" in body["detail"]
    assert "a bug in a view" not in json.dumps(body)  # no internals in the answer
    monkeypatch.undo()
    assert client.get("/api/v1/health").status_code == 200  # the worker goes on serving
    assert client.get("/healthz").status_code == 200


# ---------------------------------------------------------------- background jobs


def test_a_background_job_that_raises_is_started_again_and_never_stops_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []
    monkeypatch.setattr(resilience, "_sleep", waits.append)
    monkeypatch.setattr(settings, "background_restart_seconds", 5)
    monkeypatch.setattr(settings, "background_restart_max_seconds", 12)
    calls = {"faulty": 0, "steady": 0}

    def faulty() -> None:  # the hub is unreachable for its first rounds
        calls["faulty"] += 1
        if calls["faulty"] < 4:
            raise ConnectionError("hub.sudoo.sh: Temporary failure in name resolution")

    def steady() -> None:
        calls["steady"] += 1

    threads = [resilience.start_thread("marketplace-test", faulty), resilience.start_thread("steady-test", steady)]
    for thread in threads:
        thread.join(5)
    assert calls == {"faulty": 4, "steady": 1}
    assert waits == [5, 10, 12]  # doubling, at most the longest pause
    assert not [p for p in resilience.problems() if p["key"] == "marketplace-test"]  # it recovered


def test_a_background_job_that_keeps_failing_is_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    rounds: list[float] = []

    def sleep(seconds: float) -> None:
        rounds.append(seconds)
        if len(rounds) >= 3:
            raise SystemExit  # ends the test's loop (never raised by the panel's own jobs)

    monkeypatch.setattr(resilience, "_sleep", sleep)

    def always() -> None:
        raise ValueError("a bug in a loop")

    with pytest.raises(SystemExit):
        resilience.supervised("always-test", always)()
    found = [p for p in resilience.problems() if p["key"] == "always-test"]
    assert found and found[0]["count"] == 3 and "ValueError" in found[0]["detail"]


def test_a_failing_settings_listener_never_undoes_the_change(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.config import live

    def broken(changed: dict[str, Any]) -> None:
        raise RuntimeError("listener bug")

    heard: list[dict[str, Any]] = []
    monkeypatch.setattr(live, "_listeners", [broken, heard.append])
    before = settings.idle_hours
    try:
        live.change({"idle_hours": before + 1})
        assert settings.idle_hours == before + 1 and heard == [{"idle_hours": before + 1}]
    finally:
        live.change({"idle_hours": None})


def test_an_exception_in_a_job_fails_the_job_only() -> None:
    def bug(job: jobs.Job) -> None:
        raise ZeroDivisionError("a bug in a job")

    first = jobs.start("panel:resilience-test", "Bug", "test", bug)
    second = jobs.start("panel:resilience-test", "Fine", "test", lambda job: None)
    deadline = time.time() + 10
    while second.status in ("queued", "running", "waiting") and time.time() < deadline:
        time.sleep(0.05)
    assert first.status == "failed" and second.status == "success"


def test_a_stuck_job_command_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "job_command_max_hours", 0.5 / 3600)  # half a second
    job = jobs.Job("panel:resilience-test", "Stuck", "test", "system")
    started = time.monotonic()
    with pytest.raises(jobs.JobError, match="took too long"):
        job.run(["sleep", "30"])
    assert time.monotonic() - started < 10


def test_waiting_notifications_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo import notifications

    monkeypatch.setattr(settings, "background_queue_max", 100)
    queue = notifications.Queue()
    monkeypatch.setattr(queue, "_thread", threading.Thread())  # no worker: the deliveries only wait
    for n in range(150):
        queue.add(notifications.Delivery(f"c{n}", {}, due=time.time() + 3600 + n))
    waiting = queue.waiting()
    assert len(waiting) == 100 and waiting[0].channel == "c0"  # those due last were dropped


def test_problems_are_bounded() -> None:
    for n in range(resilience.MAX_PROBLEMS + 50):
        resilience.report("background", f"job-{n}", "failed")
    assert len(resilience.problems()) <= resilience.MAX_PROBLEMS + len(settings.problems())


# ---------------------------------------------------------------- a real plugin built with the plugin SDK

EXAMPLE = Path(__file__).parent / "sdk_plugins" / "webhook-alerts"
NOTIFICATIONS_GROUP = "sudoo.notifications"


@pytest.fixture
def sdk_installed(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[dict[str, Any]], str]]:
    """Install the example plugin of the plugin template (examples/notification-channel) the way a marketplace
    install lays it out (plugin_manager._unpack): package, verified manifest, dist-info, record."""
    installed: list[str] = []

    def install(changes: dict[str, Any]) -> str:
        manifest = {**json.loads((EXAMPLE / "manifest.json").read_text()), **changes}
        base = plugin_manager.plugins_dir() / manifest["id"] / manifest["version"]
        shutil.rmtree(base, ignore_errors=True)
        shutil.copytree(EXAMPLE / "example_webhook_alerts", base / "package" / "example_webhook_alerts")
        (base / "package" / "manifest.json").write_text(json.dumps(manifest))
        plugin_manager._write_meta(base / "meta", manifest)
        storage().put(plugin_manager.COLLECTION, manifest["id"], {
            "id": manifest["id"], "name": manifest["name"], "version": manifest["version"],
            "entry_points": manifest["entry_points"], "permissions": manifest["permissions"],
        })  # fmt: skip
        monkeypatch.setattr(plugin_manager, "LOADED", {manifest["id"]})
        for path in (base / "meta", base / "package"):
            monkeypatch.syspath_prepend(str(path))
        importlib.invalidate_caches()
        installed.append(manifest["id"])
        return str(manifest["id"])

    yield install
    for name in [m for m in sys.modules if m.startswith("example_webhook_alerts")]:
        sys.modules.pop(name, None)
    for plugin_id in installed:
        storage().delete(plugin_manager.COLLECTION, plugin_id)
        shutil.rmtree(plugin_manager.plugins_dir() / plugin_id, ignore_errors=True)
    importlib.invalidate_caches()


def test_a_real_sdk_plugin_loads_with_its_plugin_context(sdk_installed: Callable[[dict[str, Any]], str]) -> None:
    """The reported failure: 'Settings' object has no attribute 'settings'."""
    from sudoo_plugin_sdk import PluginContext
    from sudoo_plugin_sdk.plugins import Notification

    plugin_id = sdk_installed({})
    plugin = plugin_guard.load_safely(NOTIFICATIONS_GROUP, "webhook-alerts", settings)
    assert isinstance(plugin, plugin_guard.Guarded) and type(plugin._plugin).__name__ == "WebhookAlerts"
    context = plugin.context
    assert isinstance(context, PluginContext) and context.plugin_id == plugin_id
    assert plugin.minimum == "warning" and plugin.payload == "slack"  # the manifest's defaults
    assert not plugin.ready  # no webhook address set yet
    assert plugin_guard.failures() == []
    sent: list[tuple[str, bytes]] = []
    plugin._plugin.url = "https://hooks.example.test/alerts"
    plugin._plugin._post = lambda url, body: sent.append((url, body)) or 200
    plugin.send(Notification(event="update.failed", title="Update of shop failed", text="", severity="error"))
    assert sent and json.loads(sent[0][1])["text"].startswith("Error: Update of shop failed")


def test_a_real_sdk_plugin_that_fails_never_takes_the_panel_down(
    sdk_installed: Callable[[dict[str, Any]], str],
) -> None:
    from sudoo_plugin_sdk.plugins import Notification

    manifest = json.loads((EXAMPLE / "manifest.json").read_text())
    settings_list = [dict(s, default="xml") if s["key"] == "payload" else s for s in manifest["settings"]]
    sdk_installed({"settings": settings_list})  # its constructor refuses the format: ValueError
    assert plugin_guard.load_safely(NOTIFICATIONS_GROUP, "webhook-alerts", settings) is None
    assert "Message format" in _failed(NOTIFICATIONS_GROUP, "webhook-alerts")["error"]
    plugin_guard.forget_all()
    for name in [m for m in sys.modules if m.startswith("example_webhook_alerts")]:
        sys.modules.pop(name, None)
    sdk_installed({})
    plugin = plugin_guard.load_safely(NOTIFICATIONS_GROUP, "webhook-alerts", settings)
    plugin._plugin.url = "https://hooks.example.test/alerts"
    plugin._plugin._post = lambda url, body: 500  # the chat service is down
    with pytest.raises(RuntimeError, match="HTTP 500"):  # passed on to the caller, counted
        plugin.send(Notification(event="update.failed", title="x", text="", severity="error"))
    assert _failed(NOTIFICATIONS_GROUP, "webhook-alerts")["failures"] == 1
