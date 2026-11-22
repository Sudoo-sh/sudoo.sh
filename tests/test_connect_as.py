"""Connect as: single-use, short-lived tokens; the users list; permissions, production confirmation and audit
entries; the gateway setting Odoo's session cookie. Docker is never run (the session is created by a fake)."""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Iterator
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from sudoo import accounts, exposure
from sudoo.core import audit
from sudoo.core.state import STATE
from sudoo.instances import connect_as, gateway, store

Client = tuple[FlaskClient, dict[str, str]]
USERS = [{"id": 2, "login": "admin", "name": "Mitchell Admin"}, {"id": 7, "login": "sam@example.com", "name": "Sam"}]


def _instance(name: str, stage: str = "development") -> None:
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "DB_NAME": name.replace("-", "_"),
            "STAGE": stage,
            "CREATED_AT": int(time.time()),
            "ODOO_PORT": "18999",
        },
    )


@pytest.fixture(autouse=True)
def setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(connect_as, "odoo_users", lambda name, now=None: [dict(u) for u in USERS])
    monkeypatch.setattr(exposure, "address", lambda name: f"https://{name}.example.com")
    _instance("cdev")
    _instance("cprod", "production")
    yield
    connect_as._GRANTS.clear()
    for name in ("cdev", "cprod"):
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)


@pytest.fixture(scope="module")
def viewer(app: Flask) -> Client:
    password = "orchid-lantern-pebble-73"
    accounts.create("view9", "viewer", password)
    return sign_in(app, "view9", password)


# ---------------------------------------------------------------- tokens


def test_a_token_works_once() -> None:
    token, _ = connect_as.issue("cdev", 7, "sam@example.com", "admin1")
    grant = connect_as.redeem("cdev", token)
    assert grant is not None and grant.uid == 7 and grant.user == "admin1"
    assert connect_as.redeem("cdev", token) is None  # used up


def test_a_token_expires_after_a_minute() -> None:
    token, expires = connect_as.issue("cdev", 7, "sam", "admin1", now=1000.0)
    assert expires == 1000.0 + connect_as.TOKEN_SECONDS == 1060.0
    assert connect_as.redeem("cdev", token, now=1061.0) is None
    late, _ = connect_as.issue("cdev", 7, "sam", "admin1", now=1000.0)
    assert connect_as.redeem("cdev", late, now=1059.0) is not None


def test_a_token_is_for_one_instance_and_used_up_by_a_wrong_try() -> None:
    token, _ = connect_as.issue("cdev", 7, "sam", "admin1")
    assert connect_as.redeem("cprod", token) is None
    assert connect_as.redeem("cdev", token) is None  # tried once, gone


def test_tokens_are_kept_only_as_digests() -> None:
    token, _ = connect_as.issue("cdev", 7, "sam", "admin1")
    assert token not in connect_as._GRANTS and connect_as._digest(token) in connect_as._GRANTS
    assert connect_as.redeem("cdev", "") is None and connect_as.redeem("cdev", "x" * 500) is None


def test_connect_writes_a_session_and_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[tuple[str, int]] = []
    monkeypatch.setattr(connect_as, "create_session", lambda name, uid: made.append((name, uid)) or "abc123")
    token, _ = connect_as.issue("cdev", 7, "sam@example.com", "admin1")
    assert connect_as.connect("cdev", token, "203.0.113.9") == "abc123"
    assert made == [("cdev", 7)]
    entry = audit.latest(1, instance="cdev")[0]
    assert entry["user"] == "admin1" and entry["event"] == "connected as an Odoo user"
    assert "sam@example.com" in entry["detail"] and entry["ip"] == "203.0.113.9"
    with pytest.raises(connect_as.ConnectError, match="expired or was used"):
        connect_as.connect("cdev", token, "203.0.113.9")
    assert audit.latest(1, instance="cdev")[0]["ok"] is False


def test_parse_users() -> None:
    text = "2\x1fadmin\x1fMitchell Admin\n7\x1fsam@example.com\x1fSam\nbroken line\n"
    assert connect_as.parse_users(text) == USERS


def test_the_session_script_never_touches_passwords() -> None:
    assert "password" not in connect_as.SESSION_SCRIPT
    assert "_compute_session_token" in connect_as.SESSION_SCRIPT and "SUDOO_CONNECT_UID" in connect_as.SESSION_SCRIPT


# ---------------------------------------------------------------- the API


def test_users_and_a_link(admin: Client) -> None:
    client, headers = admin
    assert client.get("/api/v1/instances/cdev/odoo-users").get_json()["data"] == USERS
    answer = client.post("/api/v1/instances/cdev/connect", json={"uid": 7}, headers=headers)
    assert answer.status_code == 201, answer.get_json()
    data = answer.get_json()["data"]
    assert data["url"].startswith("https://cdev.example.com/__sudoo/connect?token=")
    assert data["login"] == "sam@example.com"
    token = data["url"].split("token=", 1)[1]
    assert connect_as.redeem("cdev", token) is not None
    entry = audit.latest(1, instance="cdev")[0]
    assert (
        entry["event"] == "connect as link given" and entry["user"] == "admin1" and "sam@example.com" in entry["detail"]
    )


def test_unknown_user_and_bad_uid_are_refused(admin: Client) -> None:
    client, headers = admin
    assert client.post("/api/v1/instances/cdev/connect", json={"uid": 99}, headers=headers).status_code == 404
    assert client.post("/api/v1/instances/cdev/connect", json={"uid": "7"}, headers=headers).status_code == 400
    assert client.post("/api/v1/instances/cdev/connect", json={"uid": True}, headers=headers).status_code == 400


def test_production_needs_the_typed_confirmation(admin: Client) -> None:
    client, headers = admin
    refused = client.post("/api/v1/instances/cprod/connect", json={"uid": 2}, headers=headers)
    assert refused.status_code == 400 and "production" in refused.get_json()["detail"]
    assert audit.latest(1, instance="cprod")[0]["ok"] is False
    answer = client.post(
        "/api/v1/instances/cprod/connect", json={"uid": 2, "confirm_stage": "production"}, headers=headers
    )
    assert answer.status_code == 201
    assert audit.latest(1, instance="cprod")[0]["event"] == "connect as link given (production)"


def test_viewers_and_developers_cannot_connect(viewer: Client, operator: Client) -> None:
    for client, headers in (viewer, operator):
        assert client.get("/api/v1/instances/cdev/odoo-users").status_code == 403
        assert client.post("/api/v1/instances/cdev/connect", json={"uid": 2}, headers=headers).status_code == 403
    assert not connect_as._GRANTS


# ---------------------------------------------------------------- the gateway


class FakeHost(gateway.PanelHost):
    def __init__(self, answer: tuple[str | None, str]) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    def route(self, name: str) -> gateway.Route | None:
        return gateway.Route(1)

    def edge(self) -> gateway.Edge:
        return gateway.DirectEdge()

    def visited(self, name: str) -> None:
        pass

    async def connect(self, name: str, token: str, ip: str) -> tuple[str | None, str]:
        self.asked.append((name, token))
        return self.answer


def _through_gateway(host: FakeHost, path: str) -> tuple[int, dict[str, Any], str]:
    async def scenario() -> tuple[int, dict[str, Any], str]:
        app = web.Application()
        app[gateway.INSTANCE] = "cdev"
        app.router.add_route("*", "/{tail:.*}", gateway._handle)
        async with TestClient(TestServer(app)) as client:
            response = await client.get(path, allow_redirects=False)
            return response.status, dict(response.headers), await response.text()

    before = gateway.HOST
    gateway.use(host)
    try:
        return asyncio.run(scenario())
    finally:
        gateway.use(before)


def test_the_gateway_sets_the_session_cookie_and_redirects() -> None:
    host = FakeHost(("sid-123", ""))
    status, headers, _ = _through_gateway(host, "/__sudoo/connect?token=tok")
    assert status == 302 and headers["Location"] == "/web"
    cookie = headers["Set-Cookie"]
    assert "session_id=sid-123" in cookie and "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Lax" in cookie
    assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
    assert host.asked == [("cdev", "tok")]


def test_the_gateway_explains_a_used_link() -> None:
    status, headers, text = _through_gateway(FakeHost((None, "This link has <expired>.")), "/__sudoo/connect?token=x")
    assert status == 403 and "Set-Cookie" not in headers
    assert "This link has &lt;expired&gt;." in text
