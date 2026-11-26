"""Access requests: only the allowed domains can ask, the form answers the same for every address, the e-mailed link
confirms once, an administrator approves (an invitation with the chosen role) or rejects (with a reason) once,
requests are throttled, and the permission is accounts.manage."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

import sudoo.mail
from conftest import ORIGIN
from sudoo import access_requests, accounts, invitations
from sudoo.config import live
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]


class FakeMailer:
    ready = True

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        self.sent.append({"to": to, "subject": subject, "text": text})


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def wipe() -> None:
        live.change(dict.fromkeys(live.overrides()))
        accounts.THROTTLE._failures.clear()
        invitations.SEND_LIMIT.clear()
        access_requests.BY_ADDRESS.clear()
        access_requests.BY_CLIENT.clear()
        for collection in (access_requests.COLLECTION, invitations.INVITATIONS):
            for key in list(storage().all(collection)):
                storage().delete(collection, key)

    wipe()
    yield
    wipe()


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> FakeMailer:
    fake = FakeMailer()
    monkeypatch.setattr(sudoo.mail, "mailer", lambda: fake)
    import sudoo.web.v1.access_requests as api
    import sudoo.web.v1.invitations as invites

    monkeypatch.setattr(api, "mailer", lambda: fake)
    monkeypatch.setattr(invites, "mailer", lambda: fake)
    return fake


def _address() -> str:
    return f"ask{time.time_ns() % 10**10}@corp.example"


def _ask(app: Flask, email: str) -> tuple[int, dict[str, object]]:
    answer = app.test_client().post(
        "/api/v1/access-requests", json={"email": email, "first_name": "Huda", "last_name": "Ali"}, headers=ORIGIN
    )
    return answer.status_code, answer.get_json()


def _token(mailbox: FakeMailer) -> str:
    found = re.search(r"/request-access#([A-Za-z0-9_.-]+)", mailbox.sent[-1]["text"])
    assert found, mailbox.sent[-1]["text"]
    return found.group(1)


def _confirm(app: Flask, token: str) -> int:
    return app.test_client().post("/api/v1/access-requests/confirm", json={"token": token}, headers=ORIGIN).status_code


def test_off_by_default_and_same_answer_for_everyone(app: Flask, mailbox: FakeMailer) -> None:
    email = _address()
    off = _ask(app, email)
    assert off[0] == 202 and not mailbox.sent  # the feature is off: nothing is sent
    live.change({"access_request_domains": "corp.example"})
    allowed = _ask(app, email)
    other_domain = _ask(app, "someone@elsewhere.example")
    accounts.create("req-taken", "developer", "tidal-amber-orchard-58", email="taken@corp.example", must_change=False)
    try:
        taken = _ask(app, "taken@corp.example")
    finally:
        accounts.delete("req-taken")
    assert allowed == off == other_domain == taken  # status and body are the same
    assert [m["to"] for m in mailbox.sent] == [email]  # only the allowed, unknown address got a mail
    assert _ask(app, email.replace("@", "@sub."))[0] == 202 and len(mailbox.sent) == 1  # a subdomain is not the domain


def test_existing_account_or_invitation_gets_no_mail(app: Flask, mailbox: FakeMailer) -> None:
    live.change({"access_request_domains": "corp.example"})
    accounts.create("req-known", "developer", "tidal-amber-orchard-58", email="known@corp.example", must_change=False)
    try:
        assert _ask(app, "known@corp.example")[0] == 202
        invitations.create("invited@corp.example", "developer", "admin1")
        assert _ask(app, "invited@corp.example")[0] == 202
        assert not mailbox.sent
    finally:
        accounts.delete("req-known")


def test_confirm_once_then_admin_approves_with_a_role(app: Flask, admin: Client, mailbox: FakeMailer) -> None:
    live.change({"access_request_domains": "corp.example, other.example"})
    email = _address()
    _ask(app, email)
    token = _token(mailbox)
    assert storage().all(access_requests.COLLECTION)
    assert token.split(".", 1)[1] not in str(storage().all(access_requests.COLLECTION))  # only a hash is kept
    assert admin[0].get("/api/v1/access-requests").get_json()["data"] == []  # unconfirmed: not listed
    assert _confirm(app, "x." + token.split(".", 1)[1]) == 400
    assert _confirm(app, token) == 200
    assert _confirm(app, token) == 400  # used up
    assert any("waiting" in m["subject"] or "asked for" in m["subject"] for m in mailbox.sent[1:])  # admins told
    listed = admin[0].get("/api/v1/access-requests").get_json()
    assert [r["email"] for r in listed["data"]] == [email] and listed["data"][0]["status"] == "pending"
    assert listed["meta"]["domains"] == ["corp.example", "other.example"]
    request_id = listed["data"][0]["id"]
    done = admin[0].post(f"/api/v1/access-requests/{request_id}/approve", json={"role": "developer"}, headers=admin[1])
    assert done.status_code == 200 and done.get_json()["data"]["status"] == "approved"
    assert invitations.pending_for(email)
    assert mailbox.sent[-1]["to"] == email and "invited you" in mailbox.sent[-1]["subject"]
    again = admin[0].post(f"/api/v1/access-requests/{request_id}/approve", json={"role": "developer"}, headers=admin[1])
    assert again.status_code == 409


def test_reject_needs_a_reason_and_keeps_it(app: Flask, admin: Client, mailbox: FakeMailer) -> None:
    live.change({"access_request_domains": "corp.example"})
    email = _address()
    _ask(app, email)
    _confirm(app, _token(mailbox))
    request_id = admin[0].get("/api/v1/access-requests").get_json()["data"][0]["id"]
    url = f"/api/v1/access-requests/{request_id}/reject"
    assert admin[0].post(url, json={"reason": " "}, headers=admin[1]).status_code == 400
    done = admin[0].post(url, json={"reason": "Not on the team"}, headers=admin[1])
    assert done.status_code == 200 and done.get_json()["data"]["reason"] == "Not on the team"
    assert admin[0].post(url, json={"reason": "again"}, headers=admin[1]).status_code == 409
    assert not invitations.pending_for(email)
    sent_before = len(mailbox.sent)
    assert _ask(app, email)[0] == 202 and len(mailbox.sent) == sent_before + 1  # may ask again after a rejection


def test_permissions_and_super_admin_role(app: Flask, admin: Client, operator: Client, mailbox: FakeMailer) -> None:
    live.change({"access_request_domains": "corp.example"})
    _ask(app, _address())
    _confirm(app, _token(mailbox))
    assert operator[0].get("/api/v1/access-requests").status_code == 403
    assert app.test_client().get("/api/v1/access-requests").status_code in (401, 403)
    request_id = admin[0].get("/api/v1/access-requests").get_json()["data"][0]["id"]
    assert (
        operator[0]
        .post(f"/api/v1/access-requests/{request_id}/approve", json={"role": "developer"}, headers=operator[1])
        .status_code
        == 403
    )
    bad = admin[0].post(f"/api/v1/access-requests/{request_id}/approve", json={"role": "nonsense"}, headers=admin[1])
    assert bad.status_code == 400
    assert (
        admin[0].post("/api/v1/access-requests/nope/approve", json={"role": "developer"}, headers=admin[1]).status_code
        == 404
    )


def test_throttle_per_address_gives_the_same_answer(app: Flask, mailbox: FakeMailer) -> None:
    live.change({"access_request_domains": "corp.example"})
    email = _address()
    answers = []
    for _ in range(access_requests.PER_ADDRESS_PER_HOUR + 2):
        answers.append(_ask(app, email))
        # an unconfirmed request blocks another e-mail anyway, so a throttled one looks the same
    assert all(a == answers[0] for a in answers) and len(mailbox.sent) == 1


def test_old_unconfirmed_links_do_not_work(app: Flask, mailbox: FakeMailer) -> None:
    live.change({"access_request_domains": "corp.example"})
    _ask(app, _address())
    token = _token(mailbox)
    key = next(iter(storage().all(access_requests.COLLECTION)))
    item = dict(storage().get(access_requests.COLLECTION, key) or {})
    item["created"] = time.time() - (access_requests.CONFIRM_HOURS + 1) * 3600
    storage().put(access_requests.COLLECTION, key, item)
    assert _confirm(app, token) == 400


def test_unconfirmed_requests_are_only_counted_for_administrators(
    app: Flask, admin: Client, mailbox: FakeMailer
) -> None:
    live.change({"access_request_domains": "corp.example"})
    assert admin[0].get("/api/v1/access-requests").get_json()["meta"]["awaiting_confirmation"] == 0
    _ask(app, _address())
    _ask(app, _address())
    listed = admin[0].get("/api/v1/access-requests").get_json()
    assert listed["data"] == [] and listed["meta"]["awaiting_confirmation"] == 2  # a count: no names, no action
    _confirm(app, _token(mailbox))
    after = admin[0].get("/api/v1/access-requests").get_json()
    assert len(after["data"]) == 1 and after["meta"]["awaiting_confirmation"] == 1


def test_confirming_a_link_does_not_touch_a_signed_in_session(app: Flask, admin: Client, mailbox: FakeMailer) -> None:
    live.change({"access_request_domains": "corp.example"})
    client, headers = admin
    assert client.get("/api/v1/account").status_code == 200
    before = client.get("/api/v1/account").get_json()["data"]["name"]
    _ask(app, _address())
    answer = client.post("/api/v1/access-requests/confirm", json={"token": _token(mailbox)}, headers=headers)
    assert answer.status_code == 200
    assert client.get("/api/v1/account").status_code == 200  # still the same person, still signed in
    assert client.get("/api/v1/account").get_json()["data"]["name"] == before
