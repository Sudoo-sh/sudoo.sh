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
        accounts.enable("admin1", "test cleanup")
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
    return client.post(f"/api/v1/users/{name}/disable", json={"reason": "left the team"}, headers=headers).status_code


def test_a_disabled_person_cannot_sign_in_and_can_after_enabling(app: Flask, admin: Client, people: None) -> None:
    client, headers = admin
    assert disable(admin, "dis-dev") == 200
    refused = app.test_client().post(
        "/api/v1/session", json={"username": "dis-dev", "password": PASSWORD}, headers=ORIGIN
    )
    assert refused.status_code == 403 and "disabled" in refused.get_json()["detail"]
    assert (
        client.post("/api/v1/users/dis-dev/enable", json={"reason": "back from leave"}, headers=headers).status_code
        == 200
    )
    sign_in(app, "dis-dev", PASSWORD)  # asserts 201: back as it was


def test_disabling_ends_the_open_sessions(app: Flask, admin: Client, people: None) -> None:
    person, _ = sign_in(app, "dis-dev", PASSWORD)
    assert person.get("/api/v1/account").status_code == 200
    assert disable(admin, "dis-dev") == 200
    assert person.get("/api/v1/account").status_code == 401
    client, headers = admin
    client.post("/api/v1/users/dis-dev/enable", json={"reason": "back from leave"}, headers=headers)
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
    client.post("/api/v1/users/dis-a/enable", json={"reason": "back from leave"}, headers=headers)
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
            accounts.disable(name, "admin1", "test")
        with pytest.raises(ValueError, match="last active super admin"):
            accounts.disable("admin1", "someone", "test")  # the only one left active
        assert disable(admin, "dis-dev") == 200  # a developer is no super admin: allowed
    finally:
        for name in [*others, "dis-dev"]:
            accounts.enable(name, "test cleanup")


def test_data_and_role_stay_and_the_change_is_audited(admin: Client, people: None) -> None:
    before = accounts.get("dis-dev") or {}
    assert disable(admin, "dis-dev") == 200
    after = accounts.get("dis-dev") or {}
    assert after["role"] == before["role"] and after["hash"] == before["hash"]
    assert after["disabled_by"] == "admin1" and after["disabled_at"]
    client, headers = admin
    shown = client.get("/api/v1/users", headers=headers).get_json()["data"]
    assert next(u for u in shown if u["name"] == "dis-dev")["disabled"] is True
    client.post("/api/v1/users/dis-dev/enable", json={"reason": "back from leave"}, headers=headers)
    events = [e["event"] for e in audit.latest(limit=30)]
    assert "account disabled" in events
    assert "disabled" not in (accounts.get("dis-dev") or {})


def test_it_needs_the_accounts_right(operator: Client, people: None) -> None:
    client, headers = operator
    assert client.post("/api/v1/users/dis-dev/disable", headers=headers).status_code == 403
    assert (
        client.post("/api/v1/users/dis-dev/enable", json={"reason": "back from leave"}, headers=headers).status_code
        == 403
    )
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
    client.post("/api/v1/users/dis-dev/enable", json={"reason": "back from leave"}, headers=headers)


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


def test_a_reason_is_required_to_disable_and_to_enable_and_is_written_in_the_audit_log(
    admin: Client, people: None
) -> None:
    from sudoo.core import audit

    client, headers = admin
    for body in ({}, {"reason": ""}, {"reason": "   "}):
        refused = client.post("/api/v1/users/dis-dev/disable", json=body, headers=headers)
        assert refused.status_code == 400 and "why" in refused.get_json()["detail"].lower()
    assert not accounts.is_disabled(accounts.get("dis-dev"))
    shown = client.post("/api/v1/users/dis-dev/disable", json={"reason": "shared a login"}, headers=headers)
    assert shown.get_json()["data"]["disabled_reason"] == "shared a login"
    for body in ({}, {"reason": " "}):  # one click is not enough to bring it back
        assert client.post("/api/v1/users/dis-dev/enable", json=body, headers=headers).status_code == 400
        assert accounts.is_disabled(accounts.get("dis-dev"))
    again = client.post("/api/v1/users/dis-dev/enable", json={"reason": "it was a mistake"}, headers=headers)
    assert again.status_code == 200 and again.get_json()["data"]["disabled_reason"] is None
    details = " | ".join(str(e.get("detail")) for e in audit.latest(limit=30))
    assert "dis-dev: shared a login" in details
    assert "dis-dev: it was a mistake (it was disabled because: shared a login)" in details


def test_a_reason_longer_than_the_limit_is_refused(admin: Client, people: None) -> None:
    client, headers = admin
    long = client.post("/api/v1/users/dis-dev/disable", json={"reason": "x" * 301}, headers=headers)
    assert long.status_code == 400 and not accounts.is_disabled(accounts.get("dis-dev"))


def test_a_second_disable_or_enable_changes_nothing_and_says_so(admin: Client, people: None) -> None:
    from sudoo.core import audit

    client, headers = admin
    first = client.post("/api/v1/users/dis-dev/disable", json={"reason": "first reason"}, headers=headers)
    assert first.get_json()["meta"] == {"changed": True}
    second = client.post("/api/v1/users/dis-dev/disable", json={"reason": "second reason"}, headers=headers)
    assert second.status_code == 200 and second.get_json()["meta"] == {"changed": False}
    assert second.get_json()["data"]["disabled_reason"] == "first reason"  # the first reason stays
    back = client.post("/api/v1/users/dis-dev/enable", json={"reason": "back"}, headers=headers)
    assert back.get_json()["meta"] == {"changed": True}
    again = client.post("/api/v1/users/dis-dev/enable", json={"reason": "again"}, headers=headers)
    assert again.status_code == 200 and again.get_json()["meta"] == {"changed": False}
    details = " | ".join(str(e.get("detail")) for e in audit.latest(limit=30))
    assert "second reason" not in details and "again" not in details.replace("back again", "")
    assert "dis-dev: back (it was disabled because: first reason)" in details


def test_enable_returns_the_reason_it_was_disabled_for_read_under_the_lock(admin: Client, people: None) -> None:
    accounts.disable("dis-dev", "admin1", "left")
    assert accounts.enable("dis-dev", "back") == "left"
    assert accounts.enable("dis-dev", "again") is None
    accounts.disable("dis-dev", "admin1", "x")
    accounts.update("dis-dev", disabled_reason=None)
    assert accounts.enable("dis-dev", "back") == ""


def test_a_disabled_super_admin_does_not_count_as_another_super_admin(admin: Client, people: None) -> None:
    client, headers = admin
    accounts.disable("dis-a", "admin1", "left")
    accounts.disable("dis-b", "admin1", "left")
    # admin1 is the only super admin that can sign in: it cannot step down, and it cannot be deleted
    demoted = client.patch("/api/v1/users/admin1", json={"role": "admin"}, headers=headers)
    assert demoted.status_code == 400
    with pytest.raises(ValueError, match="last super admin"):
        accounts.delete("admin1")
    # a disabled super admin can still be deleted while an active one remains
    accounts.delete("dis-a")
    assert accounts.get("dis-a") is None


def test_a_disabled_super_admin_is_not_a_break_glass_account(admin: Client, people: None) -> None:
    from sudoo.auth import methods

    assert methods.has_break_glass(accounts.get("dis-a") or {})
    accounts.disable("dis-a", "admin1", "left")
    assert not methods.has_break_glass(accounts.get("dis-a") or {})


def test_disabling_the_last_super_admin_that_signs_in_with_a_password_is_refused(admin: Client, people: None) -> None:
    from sudoo.auth import methods

    client, headers = admin
    original = {name: (accounts.get(name) or {}).get(methods.FIELD) for name in ("admin1", "dis-b")}
    try:
        for name in original:  # these two sign in with an e-mail code only, so dis-a is the only password account
            accounts.update(name, **{methods.FIELD: ["email_code"]})
        refused = client.post("/api/v1/users/dis-a/disable", json={"reason": "left"}, headers=headers)
        assert refused.status_code == 400
        assert not accounts.is_disabled(accounts.get("dis-a"))
    finally:
        for name, value in original.items():
            accounts.update(name, **{methods.FIELD: value})


def test_the_service_refuses_the_changes_that_remove_the_last_password_super_admin(admin: Client, people: None) -> None:
    """The guard is in accounts, not only in the routes: any caller gets it, under the same lock as the change."""
    from sudoo.auth import methods

    original = {name: (accounts.get(name) or {}).get(methods.FIELD) for name in ("admin1", "dis-b")}
    try:
        for name in original:
            accounts.update(name, **{methods.FIELD: ["email_code"]})  # dis-a is the only password super admin
        with pytest.raises(ValueError, match="keep signing in with a password"):
            accounts.update_checked("dis-a", **{methods.FIELD: ["email_code"]})
        with pytest.raises(ValueError, match="keep signing in with a password"):
            accounts.update_checked("dis-a", role="admin")
        with pytest.raises(ValueError, match="keep signing in with a password"):
            accounts.disable("dis-a", "admin1", "left")
        with pytest.raises(ValueError, match="keep signing in with a password"):
            accounts.delete("dis-a")
        assert methods.has_break_glass(accounts.get("dis-a") or {})
    finally:
        for name, value in original.items():
            accounts.update(name, **{methods.FIELD: value})


def test_two_changes_at_once_cannot_each_leave_the_other_super_admin_as_the_last(admin: Client, people: None) -> None:
    """Both password super admins step to e-mail sign-in at the same moment (a third one has no password): exactly
    one of the two changes may go through, whatever the order."""
    import threading

    from sudoo.auth import methods

    original = {name: (accounts.get(name) or {}).get(methods.FIELD) for name in ("admin1", "dis-a", "dis-b")}
    try:
        accounts.update("admin1", **{methods.FIELD: ["email_code"]})
        accounts.update("dis-b", **{methods.FIELD: ["email_code"]})  # dis-a is the only password super admin
        accounts.update("dis-b", **{methods.FIELD: ["password"]})  # now dis-a and dis-b both are
        results: list[str] = []
        barrier = threading.Barrier(2)

        def step_down(name: str) -> None:
            barrier.wait()
            try:
                accounts.update_checked(name, **{methods.FIELD: ["email_code"]})
                results.append("changed")
            except ValueError:
                results.append("refused")

        threads = [threading.Thread(target=step_down, args=(name,)) for name in ("dis-a", "dis-b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == ["changed", "refused"]
        assert sum(methods.has_break_glass(accounts.get(n) or {}) for n in ("admin1", "dis-a", "dis-b")) == 1
    finally:
        for name, value in original.items():
            accounts.update(name, **{methods.FIELD: value})


def test_a_reason_of_the_full_length_is_kept_whole_in_the_account_and_the_audit_log(
    admin: Client, people: None
) -> None:
    client, headers = admin
    why_off = ("off-" + "x" * 292) + "END1"  # 300 characters, the most the route accepts
    why_on = ("on-" + "y" * 293) + "END2"
    assert len(why_off) == len(why_on) == accounts.REASON_MAX
    assert client.post("/api/v1/users/dis-dev/disable", json={"reason": why_off}, headers=headers).status_code == 200
    assert (accounts.get("dis-dev") or {})["disabled_reason"] == why_off
    assert client.post("/api/v1/users/dis-dev/enable", json={"reason": why_on}, headers=headers).status_code == 200
    details = [str(e.get("detail")) for e in audit.latest(limit=30)]
    assert f"dis-dev: {why_off}" in details  # the disable line, to the last character
    assert f"dis-dev: {why_on} (it was disabled because: {why_off})" in details  # both reasons in the enable line


def test_the_service_refuses_a_reason_that_is_too_long_instead_of_cutting_it(people: None) -> None:
    with pytest.raises(ValueError, match="at most 300"):
        accounts.disable("dis-dev", "admin1", "z" * (accounts.REASON_MAX + 1))
    assert not accounts.is_disabled(accounts.get("dis-dev"))
