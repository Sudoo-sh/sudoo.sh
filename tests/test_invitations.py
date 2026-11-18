"""Invitations: an administrator invites by e-mail, the invitee chooses a password. Links work once and until they
expire, sending again invalidates the old link, revoking deletes it, only hashes are stored, the right people may
invite, wrong links are throttled, and setting a password directly follows INVITATION_PASSWORD_FALLBACK."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

import sudoo.mail
from sudoo import accounts, invitations
from sudoo.config import live
from sudoo.core import audit
from sudoo.storage import storage

ORIGIN = {"Origin": "http://localhost"}
PASSWORD = "tidal-amber-orchard-58"
Client = tuple[FlaskClient, dict[str, str]]


class FakeMailer:
    ready = True

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html or ""})

    def link(self) -> str:
        """The token in the last e-mail's link."""
        found = re.search(r"/invite#([A-Za-z0-9_.-]+)", self.sent[-1]["text"])
        assert found, self.sent[-1]["text"]
        return found.group(1)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    accounts.THROTTLE._failures.clear()
    invitations.SEND_LIMIT.clear()
    yield
    live.change(dict.fromkeys(live.overrides()))
    accounts.THROTTLE._failures.clear()
    invitations.SEND_LIMIT.clear()
    for key in list(storage().all(invitations.INVITATIONS)):
        storage().delete(invitations.INVITATIONS, key)


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> FakeMailer:
    fake = FakeMailer()
    monkeypatch.setattr(sudoo.mail, "mailer", lambda: fake)
    import sudoo.web.v1.invitations as api
    import sudoo.web.v1.users as users

    monkeypatch.setattr(api, "mailer", lambda: fake)
    monkeypatch.setattr(users, "mailer", lambda: fake)
    return fake


def _address() -> str:
    return f"new{time.time_ns() % 10**10}@example.com"


def _invite(admin: Client, email: str, role: str = "developer") -> dict[str, object]:
    client, headers = admin
    answer = client.post("/api/v1/invitations", json={"email": email, "role": role}, headers=headers)
    assert answer.status_code == 201, answer.get_json()
    return dict(answer.get_json()["data"])


def _accept(app: Flask, token: str, password: str = PASSWORD, first: str = "Huda") -> tuple[int, dict[str, object]]:
    answer = app.test_client().post(
        "/api/v1/invitation/accept",
        json={"token": token, "password": password, "first_name": first, "last_name": "Ali"},
        headers=ORIGIN,
    )
    return answer.status_code, answer.get_json()


def test_invite_accept_and_sign_in(app: Flask, admin: Client, mailbox: FakeMailer) -> None:
    email = _address()
    made = _invite(admin, email)
    assert mailbox.sent[-1]["to"] == email and "invited you" in mailbox.sent[-1]["subject"]
    token = mailbox.link()
    stored = storage().get(invitations.INVITATIONS, str(made["id"])) or {}
    assert token.split(".", 1)[1] not in str(stored)  # only a hash is kept
    listed = admin[0].get("/api/v1/invitations").get_json()
    assert listed["meta"]["mail_ready"] and not listed["meta"]["password_allowed"]
    assert [i["email"] for i in listed["data"]] == [email] and listed["data"][0]["expired"] is False

    shown = app.test_client().post("/api/v1/invitation", json={"token": token}, headers=ORIGIN).get_json()["data"]
    assert shown["email"] == email and shown["role"] == "developer"
    status, body = _accept(app, token)
    assert status == 201 and body["data"]["role"] == "developer" and body["data"]["csrf_token"]
    name = accounts.find(email)
    assert name and accounts.check_password(name, PASSWORD) and not accounts.get(name).get("must_change_password")
    assert admin[0].get("/api/v1/invitations").get_json()["data"] == []
    assert any(e["event"].startswith("invitation accepted") for e in audit.latest(10, user=name))


def test_a_link_works_once(app: Flask, admin: Client, mailbox: FakeMailer) -> None:
    _invite(admin, _address())
    token = mailbox.link()
    assert _accept(app, token)[0] == 201
    assert _accept(app, token, first="Again")[0] == 410
    assert invitations.use(token) is None


def test_an_expired_link_does_not_work(app: Flask, admin: Client, mailbox: FakeMailer) -> None:
    made = _invite(admin, _address())
    token = mailbox.link()
    storage().change(invitations.INVITATIONS, str(made["id"]), lambda doc: {**(doc or {}), "expires": time.time() - 1})
    assert _accept(app, token)[0] == 410
    assert admin[0].get("/api/v1/invitations").get_json()["data"][0]["expired"] is True


def test_sending_again_makes_the_old_link_stop_working(app: Flask, admin: Client, mailbox: FakeMailer) -> None:
    client, headers = admin
    made = _invite(admin, _address())
    old = mailbox.link()
    again = client.post(f"/api/v1/invitations/{made['id']}/resend", headers=headers)
    assert again.status_code == 200 and again.get_json()["data"]["sent_count"] == 2
    new = mailbox.link()
    assert new != old
    assert _accept(app, old)[0] == 410
    assert _accept(app, new)[0] == 201


def test_a_revoked_link_does_not_work(app: Flask, admin: Client, mailbox: FakeMailer) -> None:
    client, headers = admin
    made = _invite(admin, _address())
    token = mailbox.link()
    assert client.delete(f"/api/v1/invitations/{made['id']}", headers=headers).status_code == 204
    assert _accept(app, token)[0] == 410
    assert client.delete(f"/api/v1/invitations/{made['id']}", headers=headers).status_code == 404


def test_who_may_invite(app: Flask, admin: Client, operator: Client, mailbox: FakeMailer) -> None:
    client, headers = operator  # a developer
    assert (
        client.post("/api/v1/invitations", json={"email": _address(), "role": "viewer"}, headers=headers).status_code
        == 403
    )
    assert client.get("/api/v1/invitations").status_code == 403
    name = f"adm{time.time_ns() % 10**8}"
    accounts.create(name, "admin", PASSWORD, email=f"{name}@example.com")
    from conftest import sign_in

    other, other_headers = sign_in(app, name, PASSWORD)
    refused = other.post(
        "/api/v1/invitations", json={"email": _address(), "role": "super_admin"}, headers=other_headers
    )
    assert refused.status_code == 403
    made = _invite(admin, _address(), role="super_admin")
    assert other.delete(f"/api/v1/invitations/{made['id']}", headers=other_headers).status_code == 403
    assert other.post(f"/api/v1/invitations/{made['id']}/resend", headers=other_headers).status_code == 403


def test_no_duplicate_invitations_or_accounts(admin: Client, mailbox: FakeMailer) -> None:
    client, headers = admin
    email = _address()
    _invite(admin, email)
    twice = client.post("/api/v1/invitations", json={"email": email, "role": "viewer"}, headers=headers)
    assert twice.status_code == 400
    taken = client.post("/api/v1/invitations", json={"email": "ada@example.com", "role": "viewer"}, headers=headers)
    assert taken.status_code == 400


def test_the_password_rules_apply(app: Flask, admin: Client, mailbox: FakeMailer) -> None:
    _invite(admin, _address())
    token = mailbox.link()
    status, body = _accept(app, token, password="password1234")
    assert status == 400 and "common word" in body["detail"]
    assert _accept(app, token)[0] == 201  # the link still works after a refused password


def test_wrong_links_are_throttled(app: Flask) -> None:
    client = app.test_client()
    for _ in range(accounts.SignInThrottle.MAX_PER_ACCOUNT_AND_ADDRESS):
        assert client.post("/api/v1/invitation", json={"token": "abc.def"}, headers=ORIGIN).status_code == 410
    assert client.post("/api/v1/invitation", json={"token": "abc.def"}, headers=ORIGIN).status_code == 429


def test_sending_is_limited_per_administrator(admin: Client, mailbox: FakeMailer) -> None:
    client, headers = admin
    live.change({"invitations_per_hour": 2})
    _invite(admin, _address())
    _invite(admin, _address())
    third = client.post("/api/v1/invitations", json={"email": _address(), "role": "viewer"}, headers=headers)
    assert third.status_code == 429


def test_without_e_mail_nothing_is_sent(admin: Client, mailbox: FakeMailer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeMailer, "ready", False)
    client, headers = admin
    answer = client.post("/api/v1/invitations", json={"email": _address(), "role": "viewer"}, headers=headers)
    assert answer.status_code == 400 and "not set up" in answer.get_json()["detail"]
    assert client.get("/api/v1/invitations").get_json()["meta"]["password_allowed"] is True


def test_setting_a_password_directly_follows_the_setting(
    admin: Client, mailbox: FakeMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = admin

    def make() -> int:
        body = {"email": _address(), "role": "viewer"}
        return int(client.post("/api/v1/users", json=body, headers=headers).status_code)

    assert make() == 400  # e-mail works: invite instead
    live.change({"invitation_password_fallback": "always"})
    assert make() == 201
    live.change({"invitation_password_fallback": "never"})
    monkeypatch.setattr(FakeMailer, "ready", False)
    assert make() == 400
    live.change({"invitation_password_fallback": "no_mail"})
    assert make() == 201


def test_second_factor_required_after_accepting(app: Flask, admin: Client, mailbox: FakeMailer) -> None:
    live.change({"require_second_factor": "everyone"})
    _invite(admin, _address())
    token = mailbox.link()
    shown = app.test_client().post("/api/v1/invitation", json={"token": token}, headers=ORIGIN).get_json()["data"]
    assert shown["second_factor_required"] is True
    status, body = _accept(app, token)
    assert status == 201 and body["data"]["restricted"] == "second_factor"
