"""API tokens with the instances scope (connected apps such as the Odoo module), and X-Sudoo-Acting-For: the calls
such a token may make, everything it is refused, the signed acting-for line, and who the panel records as the
caller. Docker is never run: jobs are recorded instead of started."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.datastructures import Headers
from werkzeug.test import TestResponse

from sudoo import accounts, roles
from sudoo.config import live
from sudoo.core import audit, jobs
from sudoo.core.state import STATE
from sudoo.instances import integrity, store
from sudoo.web import security

Client = tuple[FlaskClient, dict[str, str]]
SHOP = "app-shop"
PERSON = "sara@example.com"


class Caller:
    """Signs requests as docs/PANEL_API.md describes, with the acting-for line when one is given."""

    def __init__(self, client: FlaskClient, token: str, signing_secret: str, name: str) -> None:
        self.client, self.token, self.secret, self.name = client, token, signing_secret.encode(), name

    def headers(
        self, method: str, path: str, body: bytes, acting_for: str | None = None, signed_for: str | None = None
    ) -> dict[str, str]:
        """`signed_for` (default: `acting_for`) is what the signature covers as the sixth line; "" signs none."""
        timestamp, nonce = str(int(time.time())), secrets.token_urlsafe(24)
        lines = [timestamp, nonce, method, path, hashlib.sha256(body).hexdigest()]
        covered = acting_for if signed_for is None else signed_for
        if covered:
            lines.append(covered)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Sudoo-Timestamp": timestamp,
            "X-Sudoo-Nonce": nonce,
            "X-Sudoo-Signature": "sha256="
            + hmac.new(self.secret, "\n".join(lines).encode(), hashlib.sha256).hexdigest(),
        }
        if acting_for is not None:
            headers["X-Sudoo-Acting-For"] = acting_for
        return headers

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        acting_for: str | None = None,
        signed_for: str | None = None,
    ) -> TestResponse:
        data = json.dumps(body).encode() if body is not None else b""
        headers = self.headers(method, path, data, acting_for, signed_for)
        return self.client.open(path, method=method, data=data, headers=headers, content_type="application/json")


def _caller(app: Flask, admin: Client, scope: str) -> Caller:
    client, headers = admin
    name = f"{scope} {secrets.token_hex(3)}"
    created = client.post("/api/v1/tokens", json={"name": name, "scope": scope}, headers=headers).get_json()["data"]
    return Caller(app.test_client(), created["token"], created["signing_secret"], name)


@pytest.fixture
def connected(app: Flask, admin: Client) -> Caller:
    return _caller(app, admin, "instances")


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    accounts.THROTTLE.clear()
    yield
    live.change(dict.fromkeys(live.overrides()))
    accounts.THROTTLE.clear()


def _instance(name: str, created_by: str = "sam", days: int = 7) -> None:
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(folder / "addons")], check=True)
    subprocess.run(
        ["git", "-C", str(folder / "addons"), "remote", "add", "origin", "https://github.com/acme/shop"], check=True
    )
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39201,
            "ODOO_PORT": 39202,
            "GIT_BRANCH": "main",
            "AUTO_UPDATE": "false",
            "AUTO_UPGRADE": "false",
            "AUTO_PAUSE": "false",
            "DELETE_AFTER_DAYS": str(days),
            "CREATED_AT": int(time.time()),
            "CREATED_BY": created_by,
        },
    )


@pytest.fixture(autouse=True)
def instance() -> Iterator[None]:
    _instance(SHOP)
    yield
    for name in (SHOP, "app-other"):
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
        integrity.forget(name)


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Jobs are recorded, not run: (instance, action, user, arguments) of each."""
    calls: list[dict[str, Any]] = []

    def fake_start(
        instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any
    ) -> jobs.Job:
        calls.append({"instance": instance, "action": action, "user": user, "args": args})
        return jobs.Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    return calls


NEW = {"version": "18.0", "edition": "community", "name": "for-sara", "delete_after_days": 7}


# ---------------------------------------------------------------- what the scope may do


def test_the_scope_is_a_role_only_tokens_have(admin: Client) -> None:
    assert roles.permissions(roles.CONNECTED_APP) == {
        "instances.view",
        "instances.create",
        "instances.update",
        "instances.operate",
        "instances.delete",
    }
    assert roles.CONNECTED_APP not in roles.ROLES and roles.normalize(roles.CONNECTED_APP) == "viewer"
    client, headers = admin
    listed = client.get("/api/v1/roles").get_json()["data"]["roles"]
    assert roles.CONNECTED_APP not in {role["name"] for role in listed}
    answer = client.post(
        "/api/v1/users", json={"email": "app@example.com", "role": roles.CONNECTED_APP}, headers=headers
    )
    assert answer.status_code == 400


def test_allowed_reads(connected: Caller, monkeypatch: pytest.MonkeyPatch) -> None:
    assert connected.call("GET", "/api/v1/versions").status_code == 200
    listed = connected.call("GET", "/api/v1/instances?limit=10")
    assert listed.status_code == 200 and SHOP in [i["name"] for i in listed.get_json()["data"]]
    assert connected.call("GET", f"/api/v1/instances/{SHOP}").get_json()["data"]["name"] == SHOP
    assert connected.call("GET", "/api/v1/instance-quota").status_code == 200
    job = jobs.Job(SHOP, "Restart", "restart", "sam")
    job.log.append("technical details\n")
    monkeypatch.setitem(jobs.JOBS, job.id, job)
    found = connected.call("GET", f"/api/v1/jobs/{job.id}?include=log")
    assert found.status_code == 200 and "log" not in found.get_json()["data"]  # no instances.logs


@pytest.mark.parametrize("action", ["start", "pause", "restart", "backup"])
def test_allowed_actions(connected: Caller, started: list[dict[str, Any]], action: str) -> None:
    answer = connected.call("POST", f"/api/v1/instances/{SHOP}/actions/{action}", {})
    assert answer.status_code == 202 and started[-1]["instance"] == SHOP


def test_create_extend_and_delete(connected: Caller, started: list[dict[str, Any]]) -> None:
    answer = connected.call("POST", "/api/v1/instances", NEW)
    assert answer.status_code == 202 and started[-1]["instance"] == "for-sara"
    extended = connected.call("PATCH", f"/api/v1/instances/{SHOP}", {"delete_after_days": 30})
    assert extended.status_code == 200 and extended.get_json()["data"]["settings"]["delete_after_days"] == 30
    assert connected.call("PATCH", f"/api/v1/instances/{SHOP}", {"delete_after_days": 30}).status_code == 200
    deleted = connected.call("DELETE", f"/api/v1/instances/{SHOP}", {"confirm": SHOP})
    assert deleted.status_code == 202 and started[-1]["action"] == "Delete instance"


# ---------------------------------------------------------------- what it is refused


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", f"/api/v1/instances/{SHOP}/credentials", None),
        ("GET", f"/api/v1/instances/{SHOP}/logs", None),
        ("GET", "/api/v1/jobs/abc/log", None),
        ("GET", "/api/v1/jobs", None),
        ("GET", "/api/v1/backups", None),
        ("GET", "/api/v1/users", None),
        ("POST", "/api/v1/users", {"email": "x@example.com", "role": "admin"}),
        ("GET", "/api/v1/tokens", None),
        ("POST", "/api/v1/tokens", {"name": "another", "scope": "write"}),
        ("GET", "/api/v1/settings", None),
        ("PATCH", "/api/v1/settings", {"idle_hours": 1}),
        ("POST", "/api/v1/server/restart", {}),
        ("GET", "/api/v1/plugins", None),
        ("GET", "/api/v1/audit", None),
        ("GET", "/api/v1/github/repositories", None),
        ("GET", "/api/v1/github/keys", None),
        ("GET", "/api/v1/metrics", None),
        ("GET", "/api/v1/instance-plan", None),
        ("GET", "/api/v1/docs", None),
        ("GET", "/api/v1/session", None),
    ],
)
def test_everything_else_is_refused(connected: Caller, method: str, path: str, body: dict[str, Any] | None) -> None:
    answer = connected.call(method, path, body)
    assert answer.status_code == 403, path
    assert "instances scope" in answer.get_json()["detail"]


@pytest.mark.parametrize(
    "action",
    [
        "update",
        "update-submodules",
        "upgrade",
        "switch-branch",
        "repair",
        "update-images",
        "restore",
        "rollback",
        "connect-repository",
        "add-submodule",
        "remove-submodule",
        "switch-off",
    ],
)
def test_other_actions_are_refused(connected: Caller, started: list[dict[str, Any]], action: str) -> None:
    answer = connected.call("POST", f"/api/v1/instances/{SHOP}/actions/{action}", {})
    assert answer.status_code == 403 and "instances scope" in answer.get_json()["detail"]
    assert not started
    assert audit.latest(1, user=f"api:{connected.name}")[0]["event"] == f"refused: {action} on {SHOP}"


def test_create_from_a_backup_is_refused(connected: Caller, started: list[dict[str, Any]]) -> None:
    answer = connected.call("POST", "/api/v1/instances", {**NEW, "from_backup": "x__x__20261110-153001__manual.zip"})
    assert answer.status_code == 403 and "restore" in answer.get_json()["detail"]
    assert not started


def test_delete_without_backup_is_refused(connected: Caller, started: list[dict[str, Any]]) -> None:
    answer = connected.call("DELETE", f"/api/v1/instances/{SHOP}", {"confirm": SHOP, "without_backup": True})
    assert answer.status_code == 403 and not started


@pytest.mark.parametrize(
    "body",
    [
        {"delete_after_days": 3},  # ends sooner
        {"delete_after_days": 0},  # "keep": never expires
        {"auto_update": True},
        {"auto_pause": False},
        {"delete_after_days": 30, "auto_upgrade": True},
    ],
)
def test_only_a_longer_lifetime_can_be_changed(connected: Caller, body: dict[str, Any]) -> None:
    answer = connected.call("PATCH", f"/api/v1/instances/{SHOP}", body)
    assert answer.status_code == 403 and "extend" in answer.get_json()["detail"]
    assert store.read_env(SHOP)["DELETE_AFTER_DAYS"] == "7" and store.read_env(SHOP)["AUTO_PAUSE"] == "false"


def test_read_and_write_scopes_are_unchanged(app: Flask, admin: Client, started: list[dict[str, Any]]) -> None:
    writer = _caller(app, admin, "write")
    assert writer.call("GET", "/api/v1/tokens").status_code == 200
    assert writer.call("POST", f"/api/v1/instances/{SHOP}/actions/switch-off", {}).status_code == 202
    reader = _caller(app, admin, "read")
    assert reader.call("GET", "/api/v1/jobs").status_code == 200
    assert reader.call("POST", f"/api/v1/instances/{SHOP}/actions/start", {}).status_code == 403


# ---------------------------------------------------------------- X-Sudoo-Acting-For


@pytest.mark.parametrize(
    ("acting_for", "signed_for"),
    [
        (PERSON, ""),  # the header is sent, the signature does not cover it
        (PERSON, "kim@example.com"),  # the header was changed on the way
        (None, PERSON),  # the header was removed on the way
    ],
)
def test_acting_for_is_part_of_the_signature(connected: Caller, acting_for: str | None, signed_for: str) -> None:
    answer = connected.call("GET", "/api/v1/instances", acting_for=acting_for, signed_for=signed_for)
    assert answer.status_code == 401 and "Invalid X-Sudoo-Signature" in answer.get_json()["detail"]


@pytest.mark.parametrize("value", ["", "a" * 121, "sara lee", "sara/lee", "saraé", "a;b"])
def test_invalid_acting_for_is_refused(connected: Caller, value: str) -> None:
    answer = connected.call("GET", "/api/v1/instances", acting_for=value)
    assert answer.status_code == 400 and "X-Sudoo-Acting-For" in answer.get_json()["detail"]


def test_acting_for_names_the_person(connected: Caller, started: list[dict[str, Any]]) -> None:
    caller = f"api:{connected.name} for {PERSON}"
    assert connected.call("GET", "/api/v1/instances", acting_for="a" * 120).status_code == 200
    answer = connected.call("POST", "/api/v1/instances", NEW, acting_for=PERSON)
    assert answer.status_code == 202
    plan = started[-1]["args"][0]
    assert plan["created_by"] == caller and started[-1]["user"] == caller
    assert answer.get_json()["data"]["requested_by"] == caller
    logged = audit.latest(5, user=caller)
    assert any(entry["event"] == "Create instance" and entry["instance"] == "for-sara" for entry in logged)
    refused = connected.call("GET", "/api/v1/users", acting_for=PERSON)
    assert refused.status_code == 403
    assert audit.latest(1, user=caller)[0]["event"] == "API call refused (instances scope)"


def test_quota_counts_per_person(connected: Caller, started: list[dict[str, Any]]) -> None:
    _instance("app-other", created_by=f"api:{connected.name} for {PERSON}")
    live.change({"max_instances": 0, "max_instances_per_user": 1, "create_cooldown_minutes": 0})
    quota = connected.call("GET", "/api/v1/instance-quota", acting_for=PERSON).get_json()["data"]
    assert quota["own_instances"] == 1
    refused = connected.call("POST", "/api/v1/instances", NEW, acting_for=PERSON)
    assert refused.status_code == 403 and "most one user" in refused.get_json()["detail"]
    assert connected.call("POST", "/api/v1/instances", NEW, acting_for="kim@example.com").status_code == 202
    assert connected.call("GET", "/api/v1/instance-quota").get_json()["data"]["own_instances"] == 0


def test_the_event_stream_checks_the_scope_and_acting_for(app: Flask, admin: Client) -> None:
    """The stream authenticates with security.api_caller: acting-for is signed there too, and an instances token
    may not open it."""
    reader = _caller(app, admin, "read")
    signed = Headers(reader.headers("GET", "/api/v1/events", b"", acting_for=PERSON))
    user = security.api_caller(signed, "GET", "/api/v1/events", b"", "127.0.0.1")
    assert user["name"] == f"api:{reader.name} for {PERSON}" and user["role"] == "viewer"
    connected = _caller(app, admin, "instances")
    with pytest.raises(PermissionError) as refused:
        security.api_caller(
            Headers(connected.headers("GET", "/api/v1/events", b"")), "GET", "/api/v1/events", b"", "127.0.0.1"
        )
    assert refused.value.args[1] == 403
