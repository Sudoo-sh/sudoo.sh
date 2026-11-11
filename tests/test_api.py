"""The API for other systems (/api/v1): tokens, signatures, scopes; and the GitHub webhook trigger."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from sudoo.config import Settings
from sudoo.triggers.github_webhook import GitHubWebhook

Client = tuple[FlaskClient, dict[str, str]]


@pytest.fixture(autouse=True)
def fresh_throttle() -> None:
    """Refused calls are counted per address (5 in 15 minutes block it); every test starts from zero."""
    from sudoo import accounts

    accounts.THROTTLE._failures.clear()


class Caller:
    """Signs requests the way docs/PANEL_API.md describes (the same as examples/panel_api_client.py)."""

    def __init__(self, client: FlaskClient, token: str, signing_secret: str) -> None:
        self.client, self.token, self.secret = client, token, signing_secret.encode()

    def headers(self, method: str, path: str, body: bytes, **override: str) -> dict[str, str]:
        timestamp, nonce = override.get("timestamp", str(int(time.time()))), override.get(
            "nonce", secrets.token_urlsafe(24)
        )
        message = "\n".join([timestamp, nonce, method, path, hashlib.sha256(body).hexdigest()]).encode()
        return {
            "Authorization": f"Bearer {override.get('token', self.token)}",
            "X-Sudoo-Timestamp": timestamp,
            "X-Sudoo-Nonce": nonce,
            "X-Sudoo-Signature": "sha256=" + hmac.new(self.secret, message, hashlib.sha256).hexdigest(),
        }

    def call(self, method: str, path: str, body: dict[str, Any] | None = None, **override: str) -> TestResponse:
        data = json.dumps(body).encode() if body is not None else b""
        headers = self.headers(method, path, data, **override)
        return self.client.open(path, method=method, data=data, headers=headers, content_type="application/json")


def _token(admin: Client, name: str, scope: str) -> tuple[str, str, str]:
    client, headers = admin
    created = client.post("/api/tokens", json={"name": name, "scope": scope}, headers=headers).get_json()
    token_id = next(t["id"] for t in client.get("/api/tokens").get_json() if t["name"] == name)
    return created["token"], created["signing_secret"], token_id


@pytest.fixture
def reader(app: Flask, admin: Client) -> Caller:
    token, secret, _ = _token(admin, f"reader {secrets.token_hex(3)}", "read")
    return Caller(app.test_client(), token, secret)


@pytest.fixture
def writer(app: Flask, admin: Client) -> Caller:
    token, secret, _ = _token(admin, f"writer {secrets.token_hex(3)}", "write")
    return Caller(app.test_client(), token, secret)


def test_token_secrets_are_shown_once(admin: Client) -> None:
    client, _ = admin
    _token(admin, "shown once", "read")
    listed = next(t for t in client.get("/api/tokens").get_json() if t["name"] == "shown once")
    assert "token" not in listed and "hash" not in listed and "signing_secret" not in listed


def test_signed_read(reader: Caller) -> None:
    response = reader.call("GET", "/api/v1/instances")
    assert response.status_code == 200
    assert response.get_json() == {"data": [], "meta": {"total": 0, "count": 0, "limit": 100, "offset": 0}}
    assert reader.call("GET", "/api/v1/versions").get_json()["data"][0] == {"version": "18.0", "enterprise": False}


def test_read_token_cannot_change_anything(reader: Caller) -> None:
    response = reader.call("POST", "/api/v1/instances", {"name": "x", "version": "20.0", "edition": "community"})
    assert response.status_code == 403 and "can only read" in response.get_json()["detail"]


def test_write_token_goes_through_the_same_input_checks(writer: Caller) -> None:
    response = writer.call("POST", "/api/v1/instances", {"name": "x", "version": "99.0", "edition": "community"})
    assert response.status_code == 400 and "Odoo version" in response.get_json()["detail"]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"token": "sudoo_000000000000_" + "a" * 43}, "Unknown or revoked"),
        ({"token": "not-a-token"}, "Bearer sudoo_"),
        ({"timestamp": str(int(time.time()) - 3600)}, "5 minutes"),
        ({"nonce": "short"}, "X-Sudoo-Nonce"),
    ],
)
def test_refused_calls(reader: Caller, override: dict[str, str], message: str) -> None:
    response = reader.call("GET", "/api/v1/instances", **override)
    assert response.status_code == 401 and message in response.get_json()["detail"]


def test_changed_request_is_refused(reader: Caller) -> None:
    headers = reader.headers("GET", "/api/v1/instances", b"")
    response = reader.client.get("/api/v1/versions", headers=headers)  # signed for another path
    assert response.status_code == 401 and "Invalid X-Sudoo-Signature" in response.get_json()["detail"]


def test_replayed_request_is_refused(reader: Caller) -> None:
    headers = reader.headers("GET", "/api/v1/instances", b"")
    assert reader.client.get("/api/v1/instances", headers=headers).status_code == 200
    replay = reader.client.get("/api/v1/instances", headers=headers)
    assert replay.status_code == 401 and "already received" in replay.get_json()["detail"]


def test_revoked_token_stops_working(app: Flask, admin: Client) -> None:
    token, secret, token_id = _token(admin, "to revoke", "read")
    caller = Caller(app.test_client(), token, secret)
    assert caller.call("GET", "/api/v1/instances").status_code == 200
    client, headers = admin
    assert client.post(f"/api/tokens/{token_id}/revoke", json={}, headers=headers).status_code == 200
    assert caller.call("GET", "/api/v1/instances").status_code == 401


def test_repeated_refusals_block_the_address(reader: Caller) -> None:
    for _ in range(5):
        reader.call("GET", "/api/v1/instances", token="sudoo_000000000000_" + "a" * 43)
    assert reader.call("GET", "/api/v1/instances").status_code == 429  # even a correct call, for 15 minutes


def test_errors_are_rfc9457_problem_details(reader: Caller) -> None:
    response = reader.call("GET", "/api/v1/instances/does-not-exist")
    assert response.status_code == 404
    assert response.mimetype == "application/problem+json"
    body = response.get_json()
    assert body["type"] == "about:blank" and body["title"] == "Not Found" and body["status"] == 404
    assert body["instance"] == "/api/v1/instances/does-not-exist"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_request_id_is_echoed(reader: Caller) -> None:
    path, body = "/api/v1/versions", b""
    headers = {**reader.headers("GET", path, body), "X-Request-ID": "ci-run-1234-step-5"}
    assert reader.client.get(path, headers=headers).headers["X-Request-ID"] == "ci-run-1234-step-5"


def test_pagination_is_checked(reader: Caller) -> None:
    response = reader.call("GET", "/api/v1/instances?limit=1000")
    assert response.status_code == 400 and "limit" in response.get_json()["detail"]


def test_too_many_refusals_answer_retry_after(reader: Caller) -> None:
    for _ in range(5):
        reader.call("GET", "/api/v1/instances", token="sudoo_000000000000_" + "a" * 43)
    response = reader.call("GET", "/api/v1/instances")
    assert response.status_code == 429 and response.headers["Retry-After"] == "900"


def test_iso_times() -> None:
    from sudoo.web.api_v1 import iso

    assert iso(0) == "1970-01-01T00:00:00Z" and iso(None) is None


def test_session_cookie_does_not_open_the_api(admin: Client) -> None:
    client, _ = admin
    assert client.get("/api/v1/instances").status_code == 401


# ---------------------------------------------------------------- GitHub webhook trigger

SECRET = "x" * 32


@pytest.fixture
def webhook(monkeypatch: pytest.MonkeyPatch) -> tuple[FlaskClient, list[tuple[str, str]]]:
    trigger = GitHubWebhook(Settings(github_webhook_secret=SECRET))
    requested: list[tuple[str, str]] = []
    trigger.start(lambda name, reason: requested.append((name, reason)))
    monkeypatch.setattr(
        trigger, "instances_following", lambda repo, branch: ["shop"] if (repo, branch) == ("acme/shop", "main") else []
    )
    app = Flask(__name__)
    blueprint = trigger.blueprint()
    assert blueprint is not None
    app.register_blueprint(blueprint)
    return app.test_client(), requested


def _push(client: FlaskClient, payload: dict[str, Any], secret: str = SECRET, event: str = "push") -> TestResponse:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Event": event}
    return client.post("/hooks/github", data=body, headers=headers, content_type="application/json")


def test_webhook_needs_a_secret() -> None:
    with pytest.raises(ValueError, match="GITHUB_WEBHOOK_SECRET"):
        GitHubWebhook(Settings())


def test_webhook_push_updates_the_matching_instances(webhook: tuple[FlaskClient, list[tuple[str, str]]]) -> None:
    client, requested = webhook
    response = _push(client, {"ref": "refs/heads/main", "repository": {"full_name": "Acme/Shop"}})
    assert response.status_code == 200 and response.get_json()["instances"] == ["shop"]
    assert requested == [("shop", "push to acme/shop main")]


def test_webhook_refuses_a_wrong_signature(webhook: tuple[FlaskClient, list[tuple[str, str]]]) -> None:
    client, requested = webhook
    response = _push(client, {"ref": "refs/heads/main", "repository": {"full_name": "acme/shop"}}, secret="wrong" * 8)
    assert response.status_code == 401 and not requested


def test_webhook_answers_github_ping(webhook: tuple[FlaskClient, list[tuple[str, str]]]) -> None:
    client, requested = webhook
    assert _push(client, {"zen": "hi"}, event="ping").get_json()["ok"] and not requested
