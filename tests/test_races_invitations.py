"""Invitations at the same moment (RACE-P-024, 025): two administrators inviting one address make one invitation
(the second answer is that invitation); two invitations whose addresses give the same user name, accepted together,
both make an account (the second gets a free name and is told), and an acceptance that cannot make the account keeps
its invitation valid."""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

import sudoo.mail
from sudoo import accounts, invitations
from sudoo.storage import storage

ORIGIN = {"Origin": "http://localhost"}
PASSWORD = "tidal-amber-orchard-58"
Client = tuple[FlaskClient, dict[str, str]]


class FakeMailer:
    ready = True

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        with self._lock:
            self.sent.append({"to": to, "text": text})

    def link_for(self, to: str) -> str:
        mail = next(m for m in reversed(self.sent) if m["to"] == to)
        found = re.search(r"/invite#([A-Za-z0-9_.-]+)", mail["text"])
        assert found, mail["text"]
        return found.group(1)


@pytest.fixture(autouse=True)
def mailbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMailer]:
    fake = FakeMailer()
    monkeypatch.setattr(sudoo.mail, "mailer", lambda: fake)
    import sudoo.web.v1.invitations as api

    monkeypatch.setattr(api, "mailer", lambda: fake)
    invitations.SEND_LIMIT.clear()
    yield fake
    invitations.SEND_LIMIT.clear()
    for key in list(storage().all(invitations.INVITATIONS)):
        storage().delete(invitations.INVITATIONS, key)


def _together(count: int, fn: Any) -> list[Any]:
    gate = threading.Barrier(count)
    results: list[Any] = [None] * count

    def run(n: int) -> None:
        gate.wait()
        results[n] = fn(n)

    threads = [threading.Thread(target=run, args=(n,)) for n in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results


def test_two_administrators_inviting_one_address_at_once_make_one_invitation(
    admin: Client, mailbox: FakeMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = admin
    address = f"same{uuid.uuid4().hex[:6]}@example.com"
    real_pending = invitations.pending_for

    def slow(email: str) -> str | None:  # the moment between looking for an invitation and making one
        found = real_pending(email)
        threading.Event().wait(0.1)
        return found

    monkeypatch.setattr(invitations, "pending_for", slow)
    answers = _together(3, lambda n: client.post("/api/v1/invitations", json={"email": address, "role": "viewer"},
                                                 headers=headers))  # fmt: skip
    assert all(a.status_code in (200, 201) for a in answers), [a.get_json() for a in answers]
    assert len({a.get_json()["data"]["id"] for a in answers}) == 1  # each answer is the one invitation
    assert [a.status_code for a in answers].count(201) == 1
    assert len([i for i in invitations.all_invitations().values() if i["email"] == address]) == 1
    assert len([m for m in mailbox.sent if m["to"] == address]) == 1


def test_two_invitations_giving_one_user_name_accepted_at_once_both_make_an_account(
    app: Flask, admin: Client, mailbox: FakeMailer
) -> None:
    client, headers = admin
    local = f"jo{uuid.uuid4().hex[:6]}"
    addresses = [f"{local}@one.example.com", f"{local}@two.example.com"]
    for address in addresses:
        assert client.post("/api/v1/invitations", json={"email": address, "role": "viewer"},
                           headers=headers).status_code == 201  # fmt: skip
    links = [mailbox.link_for(address) for address in addresses]
    answers = _together(
        2,
        lambda n: app.test_client().post("/api/v1/invitation/accept", json={
            "token": links[n], "password": PASSWORD, "first_name": "Jo", "last_name": "Ali"}, headers=ORIGIN),
    )  # fmt: skip
    assert [a.status_code for a in answers] == [201, 201], [a.get_json() for a in answers]
    names = sorted(a.get_json()["meta"]["username"] for a in answers)
    assert names[0] == local and names[1] != local  # the second got a free name, and is told which
    for name in names:
        assert accounts.get(name) is not None
        accounts.delete(name)


def test_an_acceptance_that_cannot_make_the_account_keeps_its_invitation(
    app: Flask, admin: Client, mailbox: FakeMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = admin
    address = f"keep{uuid.uuid4().hex[:6]}@example.com"
    assert client.post("/api/v1/invitations", json={"email": address, "role": "viewer"},
                       headers=headers).status_code == 201  # fmt: skip
    link = mailbox.link_for(address)
    real_create = accounts.create

    def refused(*args: Any, **kwargs: Any) -> str:
        raise ValueError("An account with this name already exists.")

    monkeypatch.setattr(accounts, "create", refused)
    body = {"token": link, "password": PASSWORD, "first_name": "Kim", "last_name": "Ali"}
    assert app.test_client().post("/api/v1/invitation/accept", json=body, headers=ORIGIN).status_code == 400
    monkeypatch.setattr(accounts, "create", real_create)
    answer = app.test_client().post("/api/v1/invitation/accept", json=body, headers=ORIGIN)
    assert answer.status_code == 201, answer.get_json()  # the same link works: the invitation was kept
    accounts.delete(answer.get_json()["meta"]["username"])
