"""Settings changed in the panel, and the live event stream."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from aiohttp import ClientResponse
from aiohttp.test_utils import TestClient, TestServer
from flask import Flask
from flask.testing import FlaskClient

from sudoo.config import Settings, live, settings
from sudoo.core import events

Client = tuple[FlaskClient, dict[str, str]]


@pytest.fixture(autouse=True)
def env_values() -> Iterator[None]:
    """Every test starts from the .env values."""
    yield
    live.change(dict.fromkeys(live.overrides()))


# ---------------------------------------------------------------- settings


def _setting(client: FlaskClient, key: str) -> dict[str, object]:
    found: dict[str, object] = next(s for s in client.get("/api/v1/settings").get_json()["data"] if s["key"] == key)
    return found


def test_a_change_takes_effect_at_once_and_reset_brings_back_env(admin: Client) -> None:
    client, headers = admin
    assert settings.idle_hours == 6
    changed = client.patch("/api/v1/settings", json={"idle_hours": 2.5, "auto_pause": False}, headers=headers)
    assert changed.status_code == 200
    assert settings.idle_hours == 2.5 and settings.auto_pause is False
    shown = _setting(client, "idle_hours")
    assert shown["value"] == 2.5 and shown["env_value"] == 6 and shown["changed_in_panel"] and shown["editable"]
    assert client.delete("/api/v1/settings/idle_hours", headers=headers).status_code == 204
    assert settings.idle_hours == 6 and settings.auto_pause is False


def test_changes_are_validated_and_limited_to_editable_settings(admin: Client) -> None:
    client, headers = admin
    wrong = client.patch("/api/v1/settings", json={"idle_hours": -1}, headers=headers)
    assert wrong.status_code == 400 and "IDLE_HOURS" in wrong.get_json()["detail"]
    fixed = client.patch("/api/v1/settings", json={"panel_port": 1234}, headers=headers)
    assert fixed.status_code == 400 and "only be changed in .env" in fixed.get_json()["detail"]
    assert settings.panel_port == 9000


def test_secrets_are_never_shown(admin: Client) -> None:
    client, _ = admin
    secret = _setting(client, "odoo_admin_password")
    assert secret["value"] is True and secret["secret"] and secret["editable"]  # write-only


def test_only_super_admins_change_settings(operator: Client) -> None:
    client, headers = operator
    assert client.get("/api/v1/settings").status_code == 403
    assert client.patch("/api/v1/settings", json={"idle_hours": 1}, headers=headers).status_code == 403


def test_changes_survive_a_restart(admin: Client) -> None:
    client, headers = admin
    client.patch("/api/v1/settings", json={"delete_after_days": 7}, headers=headers)
    from sudoo.config import LiveSettings

    assert LiveSettings().delete_after_days == 7


# ---------------------------------------------------------------- the event bus


def test_reconnecting_clients_get_the_latest_state_of_what_they_missed() -> None:
    async def scenario() -> None:
        subscription, before = events.BUS.subscribe()
        first = events.publish("job", "j1", {"step": 1})
        events.publish("job", "j1", {"step": 2})
        events.publish("instance", "acme", {"n": 1})
        events.publish("instance", "acme", {"n": 2})
        assert first is not None
        missed = events.BUS.missed(first.id - 1, events.BUS.last_id, None)
        assert missed is not None
        assert [e.data for e in missed if e.topic == "job"] == [{"step": 2}]  # only the latest state
        assert [e.data for e in missed if e.topic == "instance"] == [{"n": 2}]
        events.BUS.unsubscribe(subscription)
        for number in range(1001):  # more than are kept: a client that missed them must reload
            events.publish("metrics", "", {"n": number})
        assert events.BUS.missed(before, events.BUS.last_id, None) is None

    asyncio.run(scenario())


# ---------------------------------------------------------------- the stream


def _frames(raw: str) -> list[dict[str, str]]:
    frames = []
    for block in raw.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
        if "event" in fields:
            frames.append(fields)
    return frames


async def _read_some(client: TestClient, headers: dict[str, str], publish: list[tuple[str, str, object]]) -> str:
    response = await client.get("/api/v1/events?topics=job", headers=headers)
    assert response.status == 200 and response.headers["Content-Type"] == "text/event-stream"
    for topic, key, data in publish:
        events.publish(topic, key, data)
    raw = b""
    while raw.count(b"\n\n") < 1 + len([p for p in publish if p[0] == "job"]):
        raw += await asyncio.wait_for(response.content.readany(), 5)
    response.close()
    return raw.decode()


def test_signed_in_browser_receives_events(app: Flask, admin: Client) -> None:
    from sudoo.web.v1.events import make_app

    client, _ = admin
    cookie = client.get_cookie("sudoo_session")
    assert cookie is not None

    async def scenario() -> str:
        async with TestClient(TestServer(make_app(app))) as stream_client:
            published = [("job", "abc", {"status": "running"}), ("metrics", "", {"cpu": 1})]
            return await _read_some(stream_client, {"Cookie": f"sudoo_session={cookie.value}"}, published)

    frames = _frames(asyncio.run(scenario()))
    assert [f["event"] for f in frames] == ["job"]  # ?topics=job leaves out metrics
    assert json.loads(frames[0]["data"]) == {"key": "abc", "data": {"status": "running"}}


def test_stream_refuses_callers_without_session_or_token(app: Flask) -> None:
    from sudoo.web.v1.events import make_app

    async def scenario() -> tuple[int, str, int]:
        async with TestClient(TestServer(make_app(app))) as stream_client:
            anonymous = await stream_client.get("/api/v1/events")
            forged = await stream_client.get("/api/v1/events", headers={"Cookie": "sudoo_session=forged.value.x"})
            return anonymous.status, anonymous.headers["Content-Type"], forged.status

    status, content_type, forged = asyncio.run(scenario())
    assert status == 401 and content_type.startswith("application/problem+json") and forged == 401


def test_secrets_set_in_the_panel_are_stored_encrypted(admin: Client) -> None:
    import json

    client, headers = admin
    assert (
        client.patch("/api/v1/settings", json={"smtp_password": "app-password-xyz"}, headers=headers).status_code == 200
    )
    stored = (live.env.manager_dir / "settings.json").read_text()
    assert "app-password-xyz" not in stored and "encrypted" in json.loads(stored)["smtp_password"]
    assert settings.smtp_password == "app-password-xyz"
    from sudoo.config import LiveSettings

    assert LiveSettings().smtp_password == "app-password-xyz"  # read back after a restart


def test_plugin_settings_apply_after_a_restart(admin: Client) -> None:
    client, headers = admin
    client.patch("/api/v1/settings", json={"update_triggers": "polling,github-webhook"}, headers=headers)
    shown = _setting(client, "update_triggers")
    assert shown["applies"] == "restart" and shown["pending_restart"]
    assert _setting(client, "panel_port")["applies"] == "env"


def test_secret_settings_never_reach_the_audit_log(app: Flask, admin: Client) -> None:
    client, headers = admin
    values = {"smtp_password": "smtp-value-4821", "github_webhook_secret": "hook-value-" + "9" * 24}
    assert client.patch("/api/v1/settings", json={**values, "idle_hours": 3}, headers=headers).status_code == 200
    raw = client.get("/api/v1/audit?limit=20").get_data(as_text=True)
    assert all(value not in raw for value in values.values())
    entry = next(e for e in client.get("/api/v1/audit?limit=20").get_json()["data"] if e["event"] == "settings changed")
    assert entry["detail"] == "SMTP_PASSWORD=(set), GITHUB_WEBHOOK_SECRET=(set), IDLE_HOURS=3"
    assert client.delete("/api/v1/settings/smtp_password", headers=headers).status_code == 204
    assert client.get("/api/v1/audit?limit=1").get_json()["data"][0]["detail"] == "SMTP_PASSWORD=(env)"


def test_secret_values_written_by_earlier_versions_are_removed_from_the_audit_log(tmp_path: Path) -> None:
    from sudoo.storage.migrate import migrate
    from sudoo.storage.sqlite import SqliteStorage

    db = SqliteStorage(Settings(odoo_root=tmp_path))
    db.put("meta", "schema", {"version": 2})
    old = "SMTP_PASSWORD=a, b=c, IDLE_HOURS=2, ODOO_ADMIN_PASSWORD=(env), GITHUB_WEBHOOK_SECRET=x"
    db.log({"ts": 1, "user": "kim", "event": "settings changed", "ok": True, "detail": old})
    db.log({"ts": 2, "user": "kim", "event": "test e-mail sent", "ok": True, "detail": "SMTP_PASSWORD=kept"})
    migrate(db, tmp_path / "manager")
    details = [entry["detail"] for entry in db.logs()]
    assert details == [
        "SMTP_PASSWORD=kept",  # another event: left as it is
        "SMTP_PASSWORD=(set), IDLE_HOURS=2, ODOO_ADMIN_PASSWORD=(env), GITHUB_WEBHOOK_SECRET=(set)",
    ]
    assert db.get("meta", "schema") == {"version": 3}


def _signed(token: str, signing_secret: str, path: str) -> dict[str, str]:
    """The headers of a signed GET (docs/PANEL_API.md)."""
    timestamp, nonce = str(int(time.time())), uuid.uuid4().hex
    message = "\n".join([timestamp, nonce, "GET", path, hashlib.sha256(b"").hexdigest()]).encode()
    return {
        "Authorization": f"Bearer {token}",
        "X-Sudoo-Timestamp": timestamp,
        "X-Sudoo-Nonce": nonce,
        "X-Sudoo-Signature": "sha256=" + hmac.new(signing_secret.encode(), message, hashlib.sha256).hexdigest(),
    }


async def _ends(response: ClientResponse, seconds: float = 5) -> bool:
    """Whether the server ends the stream within `seconds`."""
    try:
        async with asyncio.timeout(seconds):
            while await response.content.readany():
                pass
    except TimeoutError:
        return False
    return True


@pytest.fixture
def quick_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Open streams check their caller at every heartbeat, a few times a second."""
    from sudoo.web.v1 import events as stream_module

    monkeypatch.setattr(stream_module, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(stream_module, "_RECHECK_SECONDS", 0)


def test_a_stream_ends_when_its_token_is_revoked(app: Flask, admin: Client, quick_checks: None) -> None:
    from sudoo.web.v1.events import make_app

    client, headers = admin
    created = client.post("/api/v1/tokens", json={"name": "stream reader", "scope": "read"}, headers=headers)
    token = created.get_json()["data"]

    async def scenario() -> tuple[int, bool, bool]:
        async with TestClient(TestServer(make_app(app))) as stream_client:
            path = "/api/v1/events?topics=job"
            response = await stream_client.get(path, headers=_signed(token["token"], token["signing_secret"], path))
            await asyncio.wait_for(response.content.readany(), 5)  # the hello
            still_open = not await _ends(response, 0.3)
            client.delete(f"/api/v1/tokens/{token['id']}", headers=headers)
            return response.status, still_open, await _ends(response)

    status, still_open, ended = asyncio.run(scenario())
    assert status == 200 and still_open and ended


def test_a_stream_ends_when_its_session_signs_out(app: Flask, admin: Client, quick_checks: None) -> None:
    from sudoo.web.v1.events import make_app

    client, headers = admin
    cookie = client.get_cookie("sudoo_session")
    assert cookie is not None

    async def scenario() -> tuple[int, bool]:
        async with TestClient(TestServer(make_app(app))) as stream_client:
            response = await stream_client.get("/api/v1/events", headers={"Cookie": f"sudoo_session={cookie.value}"})
            await asyncio.wait_for(response.content.readany(), 5)
            client.delete("/api/v1/session", headers=headers)
            return response.status, await _ends(response)

    assert asyncio.run(scenario()) == (200, True)


def test_one_caller_cannot_take_every_live_connection(
    app: Flask, admin: Client, operator: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo.web.v1 import events as stream_module

    monkeypatch.setattr(stream_module, "MAX_PER_CALLER", 2)
    admin_cookie, operator_cookie = admin[0].get_cookie("sudoo_session"), operator[0].get_cookie("sudoo_session")
    assert admin_cookie is not None and operator_cookie is not None

    async def scenario() -> list[int]:
        async with TestClient(TestServer(stream_module.make_app(app))) as stream_client:
            mine = {"Cookie": f"sudoo_session={admin_cookie.value}"}
            held = [await stream_client.get("/api/v1/events", headers=mine) for _ in range(2)]
            refused = await stream_client.get("/api/v1/events", headers=mine)
            other = await stream_client.get(
                "/api/v1/events", headers={"Cookie": f"sudoo_session={operator_cookie.value}"}
            )
            statuses = [*(r.status for r in held), refused.status, other.status]
            for response in (*held, other):
                response.close()
            return statuses

    assert asyncio.run(scenario()) == [200, 200, 429, 200]
