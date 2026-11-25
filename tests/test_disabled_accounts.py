"""The Disabled state of an account: no sign-in, its sessions end, the API tokens it owns stop working; reversible,
audited, needs accounts.manage; nobody disables themselves or the last active super admin; data and roles stay."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import ORIGIN, sign_in
from sudoo import accounts
from sudoo.core import audit

Client = tuple[FlaskClient, dict[str, str]]
PASSWORD = "sage-harbor-compass-58"


@pytest.fixture(autouse=True)
def clean_up() -> Iterator[None]:
    """The accounts a test made are gone, and the sign-in throttle is empty, before the next test file runs."""
    yield
    if accounts.is_disabled(accounts.get("admin1")):
        accounts.enable("admin1")
    for name in ("dis-a", "dis-b", "dis-dev"):
        if accounts.get(name):
            accounts.update(name, disabled=None, disabled_at=None, disabled_by=None)
            accounts.delete(name)
    accounts.THROTTLE._failures.clear()


@pytest.fixture
def people(admin: Client) -> None:
    accounts.create("dis-a", "super_admin", PASSWORD, must_change=False)
    accounts.create("dis-b", "super_admin", PASSWORD, must_change=False)
    accounts.create("dis-dev", "developer", PASSWORD, must_change=False)


def disable(admin: Client, name: str) -> int:
    client, headers = admin
    return client.post(f"/api/v1/users/{name}/disable", headers=headers).status_code


def test_a_disabled_person_cannot_sign_in_and_can_after_enabling(app: Flask, admin: Client, people: None) -> None:
    client, headers = admin
    assert disable(admin, "dis-dev") == 200
    refused = app.test_client().post(
        "/api/v1/session", json={"username": "dis-dev", "password": PASSWORD}, headers=ORIGIN
    )
    assert refused.status_code == 403 and "disabled" in refused.get_json()["detail"]
    assert client.post("/api/v1/users/dis-dev/enable", headers=headers).status_code == 200
    sign_in(app, "dis-dev", PASSWORD)  # asserts 201: back as it was


def test_disabling_ends_the_open_sessions(app: Flask, admin: Client, people: None) -> None:
    person, _ = sign_in(app, "dis-dev", PASSWORD)
    assert person.get("/api/v1/account").status_code == 200
    assert disable(admin, "dis-dev") == 200
    assert person.get("/api/v1/account").status_code == 401
    client, headers = admin
    client.post("/api/v1/users/dis-dev/enable", headers=headers)
    assert person.get("/api/v1/account").status_code == 401  # the old session stays ended


def test_the_tokens_it_owns_stop_working_and_others_do_not(app: Flask, admin: Client, people: None) -> None:
    owner, owner_headers = sign_in(app, "dis-a", PASSWORD)
    made = owner.post("/api/v1/tokens", json={"name": "t-dis", "scope": "read"}, headers=owner_headers).get_json()[
        "data"
    ]
    other, other_headers = admin
    kept = other.post("/api/v1/tokens", json={"name": "t-keep", "scope": "read"}, headers=other_headers).get_json()[
        "data"
    ]

    def call(token: dict[str, str]) -> int:
        stamp, nonce = str(int(time.time())), secrets.token_urlsafe(24)
        message = "\n".join([stamp, nonce, "GET", "/api/v1/instances", hashlib.sha256(b"").hexdigest()]).encode()
        sig = hmac.new(token["signing_secret"].encode(), message, hashlib.sha256).hexdigest()
        headers = {
            "Authorization": f"Bearer {token['token']}",
            "X-Sudoo-Timestamp": stamp,
            "X-Sudoo-Nonce": nonce,
            "X-Sudoo-Signature": "sha256=" + sig,
        }
        return app.test_client().open("/api/v1/instances", headers=headers).status_code

    assert call(made) == 200 and call(kept) == 200
    assert disable(admin, "dis-a") == 200
    assert call(made) == 403
    assert call(kept) == 200
    client, headers = admin
    client.post("/api/v1/users/dis-a/enable", headers=headers)
    assert call(made) == 200  # reversible: the token works again
    for token_id in (made["id"], kept["id"]):
        client.delete(f"/api/v1/tokens/{token_id}", headers=headers)
    assert json.dumps(made)  # tokens deleted above


def test_nobody_disables_themselves_or_the_last_active_super_admin(admin: Client, people: None) -> None:
    assert disable(admin, "admin1") == 400  # themselves
    others = [
        name
        for name, found in accounts.all_users().items()
        if found["role"] == "super_admin" and name != "admin1" and not accounts.is_disabled(found)
    ]  # dis-a, dis-b and any other super admin the run has
    try:
        for name in others:
            accounts.disable(name, "admin1")
        with pytest.raises(ValueError, match="last active super admin"):
            accounts.disable("admin1", "someone")  # the only one left active
        assert disable(admin, "dis-dev") == 200  # a developer is no super admin: allowed
    finally:
        for name in [*others, "dis-dev"]:
            accounts.enable(name)


def test_data_and_role_stay_and_the_change_is_audited(admin: Client, people: None) -> None:
    before = accounts.get("dis-dev") or {}
    assert disable(admin, "dis-dev") == 200
    after = accounts.get("dis-dev") or {}
    assert after["role"] == before["role"] and after["hash"] == before["hash"]
    assert after["disabled_by"] == "admin1" and after["disabled_at"]
    client, headers = admin
    shown = client.get("/api/v1/users", headers=headers).get_json()["data"]
    assert next(u for u in shown if u["name"] == "dis-dev")["disabled"] is True
    client.post("/api/v1/users/dis-dev/enable", headers=headers)
    events = [e["event"] for e in audit.latest(limit=30)]
    assert "account disabled" in events
    assert "disabled" not in (accounts.get("dis-dev") or {})


def test_it_needs_the_accounts_right(operator: Client, people: None) -> None:
    client, headers = operator
    assert client.post("/api/v1/users/dis-dev/disable", headers=headers).status_code == 403
    assert client.post("/api/v1/users/dis-dev/enable", headers=headers).status_code == 403
    assert not accounts.is_disabled(accounts.get("dis-dev"))


def test_a_pending_second_step_for_a_disabled_account_is_refused(app: Flask, admin: Client, people: None) -> None:
    """The sign-in that waits for a second factor stops too (signin._pending)."""
    from sudoo.web import signin

    assert disable(admin, "dis-dev") == 200
    with app.test_request_context("/"):
        from flask import session

        session["pending"] = {
            "user": "dis-dev",
            "version": (accounts.get("dis-dev") or {})["session_version"],
            "at": time.time(),
            "first": "password",
        }
        with pytest.raises(Exception) as caught:
            signin._pending()
        assert getattr(caught.value, "code", None) == 401
    client, headers = admin
    client.post("/api/v1/users/dis-dev/enable", headers=headers)


def test_the_list_says_who_must_use_a_second_factor(admin: Client, people: None) -> None:
    """GET /users: second_factor_required follows REQUIRE_SECOND_FACTOR (admins by default), so the page can show who
    still has none."""
    from sudoo.config import live

    def required() -> dict[str, bool]:
        shown = admin[0].get("/api/v1/users").get_json()["data"]
        return {u["name"]: u["second_factor_required"] for u in shown if u["name"].startswith("dis-")}

    try:
        live.change({"require_second_factor": "admins"})
        assert required() == {"dis-a": True, "dis-b": True, "dis-dev": False}
        live.change({"require_second_factor": "everyone"})
        assert required() == {"dis-a": True, "dis-b": True, "dis-dev": True}
        live.change({"require_second_factor": "none"})
        assert not any(required().values())
    finally:
        live.change({"require_second_factor": None})
