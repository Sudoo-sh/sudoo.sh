"""Accounts and sign-in under requests at the same moment (RACE-P-001, 002, 003, 018, 019): a session ended
meanwhile stays ended, the sign-in limit holds for guesses sent together, a second factor an administrator removed
does not come back, a password reset is not undone by a sign-in, and one e-mail address belongs to one account.

Each test makes the two requests meet where they used to overwrite each other: one request waits at the point
before its write until the other one is done."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from sudoo import accounts, auth
from sudoo.auth import email_code
from sudoo.core import vault

ORIGIN = {"Origin": "http://localhost"}
PASSWORD = "harbor-quartz-meadow-61"


class ReadyMailer:
    ready = True

    def send(self, *args: Any, **kwargs: Any) -> None:
        pass


@pytest.fixture(autouse=True)
def fresh_throttle(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts with no failed sign-ins recorded (the limit is per address and the tests share one); the
    e-mail code is offered (a mailer that is ready, sending nothing)."""
    monkeypatch.setattr(accounts, "THROTTLE", accounts.SignInThrottle(f"sign_in_throttle_{uuid.uuid4().hex[:8]}"))
    monkeypatch.setattr(email_code, "mailer", lambda: ReadyMailer())
    yield


def _user(role: str = "viewer", email: bool = True) -> str:
    name = f"race{time.time_ns() % 10**10}"
    accounts.create(name, role, PASSWORD, email=f"{name}@example.com" if email else None)
    return name


def _signed_in(app: Flask, name: str, password: str = PASSWORD) -> tuple[FlaskClient, dict[str, str]]:
    client = app.test_client()
    answer = client.post("/api/v1/session", json={"username": name, "password": password}, headers=ORIGIN)
    assert answer.status_code == 201, answer.get_json()
    return client, {**ORIGIN, "X-CSRF-Token": answer.get_json()["data"]["csrf_token"]}


def _in_thread(fn: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    result: list[Any] = []
    thread = threading.Thread(target=lambda: result.append(fn()), daemon=True)
    thread.start()
    return thread, result


# ---------------------------------------------------------------- RACE-P-001


def test_a_session_ended_by_an_administrator_stays_ended_when_it_signs_out_elsewhere_meanwhile(
    app: Flask, admin: tuple[FlaskClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stolen cookie asks "sign out everywhere else" while an administrator ends every session of the account
    (here: removes its second factors). The stolen session must not come out of it with a fresh, valid version."""
    admin_client, admin_headers = admin
    name = _user()
    client, headers = _signed_in(app, name)
    real = accounts.end_sessions
    done = threading.Event()

    def administrator_first(username: str, *args: Any, **kwargs: Any) -> int:
        if threading.current_thread() is not threading.main_thread() or done.is_set():
            return real(username, *args, **kwargs)
        thread, _ = _in_thread(
            lambda: admin_client.delete(f"/api/v1/users/{name}/second-factors", headers=admin_headers)
        )
        thread.join(10)
        done.set()
        return real(username, *args, **kwargs)

    monkeypatch.setattr(accounts, "end_sessions", administrator_first)
    client.delete("/api/v1/account/sessions", headers=headers)
    assert done.is_set()
    assert client.get("/api/v1/session").status_code == 401  # the administrator ended it: it stays ended


def test_a_password_reset_by_an_administrator_wins_over_a_password_change_in_flight(
    app: Flask, admin: tuple[FlaskClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The holder of the old password changes it while an administrator resets it: the reset ends that session and
    its password; the change must not overwrite the reset and keep the session."""
    admin_client, admin_headers = admin
    name = _user()
    client, headers = _signed_in(app, name)
    real = accounts.set_password
    reset: list[str] = []

    def administrator_first(username: str, password: str, *args: Any, **kwargs: Any) -> int:
        if threading.current_thread() is threading.main_thread() and not reset:
            thread, result = _in_thread(
                lambda: admin_client.post(
                    f"/api/v1/users/{name}/password", json={"password": "violet-anchor-ridge-28"}, headers=admin_headers
                )
            )
            thread.join(10)
            reset.append(result[0].get_json()["data"]["password"])
        return real(username, password, *args, **kwargs)

    monkeypatch.setattr(accounts, "set_password", administrator_first)
    client.put("/api/v1/session/password", json={"current": PASSWORD, "new": "copper-lantern-fjord-94"},
               headers=headers)  # fmt: skip
    assert reset
    assert client.get("/api/v1/session").status_code == 401
    assert accounts.check_password(name, reset[0]) is not None  # the administrator's password is the one kept
    assert accounts.check_password(name, "copper-lantern-fjord-94") is None


# ---------------------------------------------------------------- RACE-P-002


def test_wrong_passwords_sent_at_the_same_moment_are_limited_like_ones_sent_one_by_one(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _user()
    real = accounts.verify_password

    def slow(password: str, stored: str) -> bool:
        time.sleep(0.2)  # the slow hash: every guess sent together is being checked at the same time
        return real(password, stored)

    monkeypatch.setattr(accounts, "verify_password", slow)
    guesses = 12
    gate = threading.Barrier(guesses)
    codes: list[int] = []

    def guess(n: int) -> None:
        client = app.test_client()
        gate.wait()
        answer = client.post("/api/v1/session", json={"username": name, "password": f"wrong-{n}"}, headers=ORIGIN)
        codes.append(answer.status_code)

    threads = [threading.Thread(target=guess, args=(n,)) for n in range(guesses)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    limit = accounts.SignInThrottle.MAX_PER_ACCOUNT_AND_ADDRESS
    assert codes.count(401) <= limit, codes  # checked: at most the limit; the others are refused unchecked
    assert codes.count(429) == guesses - codes.count(401)


def test_a_right_password_after_the_limit_still_waits_and_a_reserved_try_is_given_back(app: Flask) -> None:
    """The reservation of a try never outlives its request: a right password signs in, and the next sign-in is
    not refused for tries that were in flight."""
    name = _user()
    for _ in range(3):
        client = app.test_client()
        assert client.post("/api/v1/session", json={"username": name, "password": "nope"},
                           headers=ORIGIN).status_code == 401  # fmt: skip
    for _ in range(4):
        _signed_in(app, name)  # a success clears the failures of the account; nothing is left reserved
    assert accounts.THROTTLE.seconds_to_wait("127.0.0.1", name) == 0


# ---------------------------------------------------------------- RACE-P-003


def test_a_second_factor_removed_by_an_administrator_does_not_come_back_with_a_change_in_flight(
    app: Flask, admin: tuple[FlaskClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The person removes their e-mail code while an administrator removes every second factor of the account: the
    person's request wrote back the authenticator app it had read before, so the app came back."""
    admin_client, admin_headers = admin
    name = _user()
    client, headers = _signed_in(app, name)
    secret = vault.encrypt("JBSWY3DPEHPK3PXP", f"totp:{name}")
    accounts.update(name, second_factors={"email": {"added": 1}, "totp": {"secret": secret, "last_step": 0}})
    real = email_code.EmailCode.remove
    done = threading.Event()

    def administrator_first(self: Any, account: dict[str, Any]) -> dict[str, Any]:
        if not done.is_set():
            thread, _ = _in_thread(
                lambda: admin_client.delete(f"/api/v1/users/{name}/second-factors", headers=admin_headers)
            )
            thread.join(10)
            done.set()
        return real(self, account)

    monkeypatch.setattr(email_code.EmailCode, "remove", administrator_first)
    client.delete("/api/v1/account/second-factors/email", json={"password": PASSWORD}, headers=headers)
    assert done.is_set()
    assert auth.enrolled(accounts.get(name) or {}) == []  # the administrator removed them all


def test_a_used_authenticator_code_is_not_given_back_by_a_change_in_flight(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A code of the app is used (its step remembered) while the person removes the e-mail code: the removal wrote
    back the app as it was before, with the older step, so the same code worked a second time."""
    name = _user()
    client, headers = _signed_in(app, name)
    secret = vault.encrypt("JBSWY3DPEHPK3PXP", f"totp:{name}")
    accounts.update(name, second_factors={"email": {"added": 1}, "totp": {"secret": secret, "last_step": 0}})
    real = email_code.EmailCode.remove
    step = int(time.time() // 30)

    def code_used_meanwhile(self: Any, account: dict[str, Any]) -> dict[str, Any]:
        current = accounts.get(name) or {}
        factors = current["second_factors"]
        accounts.update(name, second_factors={**factors, "totp": {**factors["totp"], "last_step": step}})
        return real(self, account)

    monkeypatch.setattr(email_code.EmailCode, "remove", code_used_meanwhile)
    assert client.delete("/api/v1/account/second-factors/email", json={"password": PASSWORD},
                         headers=headers).status_code == 204  # fmt: skip
    totp = (accounts.get(name) or {})["second_factors"]["totp"]
    assert totp["last_step"] == step


def test_two_removals_at_once_leave_the_second_factor_an_account_must_have(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the change applied onto the stored factors, two removals of different factors at once could leave none
    on an account that must have one: the second removal is refused, judged on the stored account."""
    name = _user("admin")  # administrators must use a second factor (REQUIRE_SECOND_FACTOR=admins)
    monkeypatch.setattr(auth, "must_use_second_factor", lambda account: account.get("role") == "admin")
    client, headers = _signed_in(app, name)
    secret = vault.encrypt("JBSWY3DPEHPK3PXP", f"totp:{name}")
    accounts.update(name, second_factors={"email": {"added": 1}, "totp": {"secret": secret, "last_step": 0}})
    other = app.test_client()
    with client.session_transaction() as cookie, other.session_transaction() as copy:
        copy.update(dict(cookie))
    real = email_code.EmailCode.remove
    answers: list[int] = []

    def other_removal_first(self: Any, account: dict[str, Any]) -> dict[str, Any]:
        if not answers:
            thread, result = _in_thread(
                lambda: other.delete("/api/v1/account/second-factors/totp", json={"password": PASSWORD},
                                     headers=headers)  # fmt: skip
            )
            thread.join(10)
            answers.append(result[0].status_code)
        return real(self, account)

    monkeypatch.setattr(email_code.EmailCode, "remove", other_removal_first)
    second = client.delete("/api/v1/account/second-factors/email", json={"password": PASSWORD}, headers=headers)
    assert answers == [204] and second.status_code in (400, 401)
    assert auth.enrolled(accounts.get(name) or {}) == ["email"]


# ---------------------------------------------------------------- RACE-P-018


def test_a_sign_in_with_the_old_password_does_not_undo_a_reset_made_meanwhile(monkeypatch: pytest.MonkeyPatch) -> None:
    """An account whose hash is of an earlier kind gets a new hash at its next sign-in. A reset that lands between
    the check and that new hash must stay: the old password must not work again."""
    name = _user()
    old = dict(accounts._SCRYPT)
    monkeypatch.setitem(accounts._SCRYPT, "n", 2**12)  # an earlier, weaker kind of hash
    accounts.set_password(name, "amber-falcon-tundra-35")
    monkeypatch.setitem(accounts._SCRYPT, "n", old["n"])
    real = accounts._needs_rehash
    done = threading.Event()

    def reset_meanwhile(stored: str) -> bool:
        if not done.is_set():
            thread, _ = _in_thread(lambda: accounts.set_password(name, "slate-orchid-canyon-52"))
            thread.join(10)
            done.set()
        return real(stored)

    monkeypatch.setattr(accounts, "_needs_rehash", reset_meanwhile)
    accounts.check_password(name, "amber-falcon-tundra-35")
    assert done.is_set()
    monkeypatch.setattr(accounts, "_needs_rehash", real)
    assert accounts.check_password(name, "amber-falcon-tundra-35") is None
    assert accounts.check_password(name, "slate-orchid-canyon-52") is not None


# ---------------------------------------------------------------- RACE-P-019


def test_two_accounts_created_at_once_cannot_share_an_e_mail_address() -> None:
    address = f"shared{time.time_ns() % 10**10}@example.com"
    gate = threading.Barrier(6)
    made: list[str] = []
    refused: list[str] = []

    def create(n: int) -> None:
        name = f"same{n}x{time.time_ns() % 10**8}"
        gate.wait()
        try:
            accounts.create(name, "viewer", PASSWORD, email=address)
            made.append(name)
        except ValueError:
            refused.append(name)

    threads = [threading.Thread(target=create, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    users = [n for n, u in accounts.all_users().items() if str(u.get("email") or "").lower() == address]
    assert len(users) == 1 and len(made) == 1 and len(refused) == 5, (made, refused)
