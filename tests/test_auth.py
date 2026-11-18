"""Storage, second factors (authenticator app, e-mail code), enforced setup, rate limits and creation limits."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from sudoo import accounts
from sudoo.auth import email_code, totp
from sudoo.config import Settings, live
from sudoo.limits import quotas
from sudoo.storage.migrate import migrate_json_files
from sudoo.storage.sqlite import SqliteStorage

ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    accounts.THROTTLE._failures.clear()
    yield
    live.change(dict.fromkeys(live.overrides()))
    accounts.THROTTLE._failures.clear()


def _user(role: str = "admin") -> tuple[str, str]:
    name = f"u{time.time_ns() % 10**10}"
    return name, accounts.create(name, role, "orchid-pebble-summit-73")


def _sign_in(app: Flask, name: str, password: str) -> tuple[FlaskClient, Any]:
    client = app.test_client()
    answer = client.post("/api/v1/session", json={"username": name, "password": password}, headers=ORIGIN)
    return client, answer


def _csrf(client: FlaskClient) -> dict[str, str]:
    return {**ORIGIN, "X-CSRF-Token": client.get("/api/v1/session").get_json()["data"]["csrf_token"]}


# ---------------------------------------------------------------- storage


def test_sqlite_storage_documents_changes_and_log(tmp_path: Path) -> None:
    db = SqliteStorage(Settings(odoo_root=tmp_path))
    db.put("things", "a", {"n": 1})
    assert db.get("things", "a") == {"n": 1} and db.all("things") == {"a": {"n": 1}}
    db.change("things", "a", lambda doc: {"n": (doc or {})["n"] + 1})
    assert db.get("things", "a") == {"n": 2}
    with pytest.raises(ValueError):
        db.change("things", "a", lambda doc: (_ for _ in ()).throw(ValueError("no")))
    assert db.get("things", "a") == {"n": 2}  # nothing changed
    assert db.delete("things", "a") and db.get("things", "a") is None
    for number in range(3):
        db.log({"ts": number, "user": "sam" if number else "kim", "event": f"e{number}", "ok": True, "instance": "x"})
    assert [e["event"] for e in db.logs(limit=2)] == ["e2", "e1"]
    assert [e["event"] for e in db.logs(user="kim")] == ["e0"]
    assert (tmp_path / "manager" / "sudoo.db").stat().st_mode & 0o777 == 0o600


def test_json_files_of_earlier_versions_are_moved_in(tmp_path: Path) -> None:
    folder = tmp_path / "manager"
    folder.mkdir()
    (folder / "users.json").write_text(json.dumps({"sam": {"role": "admin"}}))
    (folder / "audit.log").write_text(json.dumps({"ts": 1, "user": "sam", "event": "signed in", "ok": True}) + "\n")
    db = SqliteStorage(Settings(odoo_root=tmp_path))
    migrate_json_files(db, folder)
    assert db.get("users", "sam") == {"role": "admin"} and db.logs()[0]["event"] == "signed in"
    assert (folder / "users.json.migrated").exists() and not (folder / "users.json").exists()


# ---------------------------------------------------------------- authenticator app


def test_totp_matches_rfc_6238() -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
    assert totp.code_at(secret, 59 // 30) == "287082"  # RFC 6238 test vector, last 6 digits
    assert totp.code_at(secret, 1111111109 // 30) == "081804"


def test_authenticator_app_setup_and_sign_in(app: Flask) -> None:
    name, password = _user()
    client, answer = _sign_in(app, name, password)
    assert answer.status_code == 201
    headers = _csrf(client)
    started = client.post("/api/v1/account/second-factors/totp", json={}, headers=headers).get_json()["data"]
    assert started["otpauth_uri"].startswith("otpauth://totp/Sudoo.sh:")
    wrong = client.put("/api/v1/account/second-factors/totp", json={"code": "000000"}, headers=headers)
    assert wrong.status_code == 400
    code = totp.code_at(started["secret"], int(time.time() // 30))
    assert client.put("/api/v1/account/second-factors/totp", json={"code": code}, headers=headers).status_code == 200

    # signing in now takes the code; the same code works once
    fresh, step1 = _sign_in(app, name, password)
    assert step1.status_code == 200 and step1.get_json()["data"]["second_factor"]["methods"][0]["name"] == "totp"
    assert fresh.get("/api/v1/instances").status_code == 401  # nothing opens between the steps
    reused = fresh.post("/api/v1/session/second-factor", json={"method": "totp", "code": code}, headers=ORIGIN)
    assert reused.status_code == 401
    accounts.update(name, second_factors={"totp": {**accounts.get(name)["second_factors"]["totp"], "last_step": 0}})
    step2 = fresh.post("/api/v1/session/second-factor", json={"method": "totp", "code": code}, headers=ORIGIN)
    assert step2.status_code == 201 and fresh.get("/api/v1/instances").status_code == 200


# ---------------------------------------------------------------- e-mail code


class FakeMailer:
    ready = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        code = subject.split(" ", 1)[0]
        assert html and code in html and code in text and "<script" not in html
        self.html = html
        self.sent.append((to, code))


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> FakeMailer:
    import sudoo.mail

    fake = FakeMailer()
    monkeypatch.setattr(email_code, "mailer", lambda: fake)
    monkeypatch.setattr(sudoo.mail, "mailer", lambda: fake)
    return fake


def test_email_code_setup_and_sign_in(app: Flask, mailbox: FakeMailer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sudoo.auth.codes.RESEND_SECONDS", 0)
    name, password = _user()
    client, _ = _sign_in(app, name, password)
    headers = _csrf(client)
    assert (
        client.post("/api/v1/account/second-factors/email", json={"email": "bad"}, headers=headers).status_code == 400
    )
    started = client.post("/api/v1/account/second-factors/email", json={"email": "sam@example.com"}, headers=headers)
    assert started.get_json()["data"]["sent_to"] == "s***@example.com"
    code = mailbox.sent[-1][1]
    assert "Your confirmation code" in mailbox.html and "#714B67" in mailbox.html
    assert client.put("/api/v1/account/second-factors/email", json={"code": code}, headers=headers).status_code == 200
    assert accounts.get(name)["email"] == "sam@example.com"

    fresh, step1 = _sign_in(app, name, password)
    assert step1.get_json()["data"]["second_factor"]["sent_to"] == "s***@example.com"  # sent at once
    code = mailbox.sent[-1][1]
    for _ in range(5):  # five wrong codes: blocked, even with the right one afterwards
        fresh.post("/api/v1/session/second-factor", json={"method": "email", "code": "111111"}, headers=ORIGIN)
    blocked = fresh.post("/api/v1/session/second-factor", json={"method": "email", "code": code}, headers=ORIGIN)
    assert blocked.status_code == 429


def test_required_second_factor_limits_the_session_to_setting_one_up(app: Flask, admin: tuple[Any, Any]) -> None:
    live.change({"require_second_factor": "admins"})
    name, password = _user("admin")
    client, answer = _sign_in(app, name, password)
    assert answer.status_code == 201 and answer.get_json()["data"]["restricted"] == "second_factor"
    assert client.get("/api/v1/instances").status_code == 403
    assert client.get("/api/v1/account").get_json()["data"]["second_factor_required"]
    developer, developer_password = _user("developer")
    other, _ = _sign_in(app, developer, developer_password)
    assert other.get("/api/v1/instances").status_code == 200  # "admins": developers are not asked


# ---------------------------------------------------------------- limits


def test_rate_limit(app: Flask, admin: tuple[FlaskClient, dict[str, str]]) -> None:
    client, _ = admin
    live.change({"rate_limit_reads_per_minute": 3})
    from sudoo.limits import limiter

    limiter.cache_clear()
    answers = [client.get("/api/v1/versions") for _ in range(4)]
    assert [a.status_code for a in answers] == [200, 200, 200, 429]
    assert answers[0].headers["RateLimit-Limit"] == "3"
    assert int(answers[3].headers["Retry-After"]) <= 60
    assert answers[3].mimetype == "application/problem+json"
    limiter.cache_clear()


def test_creation_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    owners = {"a": "sam", "b": "sam", "c": "kim"}
    monkeypatch.setattr(quotas.store, "names", lambda: list(owners))
    monkeypatch.setattr(quotas.store, "read_env", lambda name: {"CREATED_BY": owners[name]})
    live.change({"max_instances": 3})
    with pytest.raises(quotas.QuotaError, match="maximum of 3"):
        quotas.claim("sam")
    live.change({"max_instances": 0, "max_instances_per_user": 2})
    with pytest.raises(quotas.QuotaError, match="most one user"):
        quotas.claim("sam")
    live.change({"create_cooldown_minutes": 60})
    quotas.claim("kim")
    with pytest.raises(quotas.QuotaError) as waiting:
        quotas.claim("kim")
    assert waiting.value.status == 429 and 3500 < (waiting.value.retry_after or 0) <= 3601
    assert quotas.usage("kim")["next_creation_at"] is not None


def test_creations_in_progress_count_toward_the_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.core import jobs

    owners = {"a": "ann"}
    monkeypatch.setattr(quotas.store, "names", lambda: list(owners))
    monkeypatch.setattr(quotas.store, "read_env", lambda name: {"CREATED_BY": owners[name]})
    pending = [("b", "ann"), ("a", "ann")]  # "a" is being created and listed already: counted once
    monkeypatch.setattr(jobs, "pending_creations", lambda: pending)
    live.change({"max_instances": 3})
    quotas.claim("bob")  # a and b: 2 of 3
    pending.append(("c", "bob"))
    with pytest.raises(quotas.QuotaError, match="maximum of 3"):
        quotas.claim("bob")
    live.change({"max_instances": 0, "max_instances_per_user": 2})
    with pytest.raises(quotas.QuotaError, match="You have 2 instances"):
        quotas.claim("ann")
    quotas.claim("bob")
    usage = quotas.usage("ann")
    assert usage["instances"] == 3 and usage["own_instances"] == 2


def test_pending_creations_are_the_queued_and_running_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.core import jobs

    made = {status: jobs.Job(f"q-{status}", "Create instance", "create", "ann") for status in ("queued", "running")}
    made["failed"] = jobs.Job("q-failed", "Create instance", "create", "ann")
    made["failed"].status = "failed"
    made["running"].status = "running"
    other = jobs.Job("q-other", "Restart", "restart", "ann")
    for job in (*made.values(), other):
        monkeypatch.setitem(jobs.JOBS, job.id, job)
    found = [entry for entry in jobs.pending_creations() if entry[0].startswith("q-")]
    assert sorted(found) == [("q-queued", "ann"), ("q-running", "ann")]


def test_email_shows_where_the_request_came_from_and_escapes_values() -> None:
    from sudoo.auth.base import RequestInfo
    from sudoo.mail.catalog import code_block, details_block
    from sudoo.mail.render import compose

    origin = RequestInfo("203.0.113.9", "Mozilla/5.0 (Windows NT 10.0) Chrome/140.0 Safari/537.36", 0)
    assert origin.browser == "Chrome on Windows"
    message = compose(
        "sign_in_code",
        {"name": "<b>sam</b>", "code": "123456", "minutes": 10, "code_box": code_block("123456", 10),
         "request": details_block([("When", "now"), ("From", origin.ip), ("Device", origin.browser)])},
    )  # fmt: skip
    html, text = message.html, message.text
    assert message.subject == "123456 is your Sudoo.sh sign-in code"
    assert "123456" in html and "user-select:all" in html and "203.0.113.9" in html and "Chrome on Windows" in html
    assert "&lt;b&gt;sam&lt;/b&gt;" in html and "<b>sam</b>" in text  # escaped in HTML only


# ---------------------------------------------------------------- sessions and the own account


def _cookie(client: FlaskClient) -> str:
    cookie = client.get_cookie("sudoo_session")
    assert cookie is not None
    return cookie.value


def _with_cookie(app: Flask, value: str) -> FlaskClient:
    """A new browser holding a copy of a session cookie."""
    client = app.test_client()
    client.set_cookie("sudoo_session", value)
    return client


def test_a_session_of_a_deleted_account_does_not_open_an_account_created_later_with_its_name(app: Flask) -> None:
    name, password = _user("viewer")
    client, _ = _sign_in(app, name, password)
    copy = _cookie(client)
    assert _with_cookie(app, copy).get("/api/v1/session").status_code == 200
    accounts.delete(name)
    accounts.create(name, "admin", "orchid-pebble-summit-73", must_change=False)  # someone else, same user name
    assert _with_cookie(app, copy).get("/api/v1/session").status_code == 401


def test_session_versions_are_random_and_change_on_every_ending() -> None:
    first, _ = _user()
    second, _ = _user()
    versions = {accounts.get(first)["session_version"], accounts.get(second)["session_version"]}  # type: ignore[index]
    assert len(versions) == 2 and 1 not in versions
    before = accounts.get(first)["session_version"]  # type: ignore[index]
    assert accounts.end_sessions(first) not in (before, before + 1)


def test_an_account_of_an_earlier_version_keeps_its_sessions(app: Flask) -> None:
    name, password = _user()
    accounts.update(name, session_version=1)  # counted from 1, as before
    client, answer = _sign_in(app, name, password)
    assert answer.status_code == 201 and client.get("/api/v1/session").status_code == 200
    assert accounts.end_sessions(name) != 1 and client.get("/api/v1/session").status_code == 401


def test_signing_out_ends_the_session_on_the_server(app: Flask) -> None:
    name, password = _user()
    client, _ = _sign_in(app, name, password)
    copy = _cookie(client)
    other, _ = _sign_in(app, name, password)
    assert client.delete("/api/v1/session", headers=_csrf(client)).status_code == 204
    assert _with_cookie(app, copy).get("/api/v1/session").status_code == 401  # a copy of the cookie stops working
    assert other.get("/api/v1/session").status_code == 200  # the account's other sessions continue


def test_ended_sessions_are_forgotten_once_they_would_have_expired() -> None:
    accounts.end_session("old-session", started=time.time() - 13 * 3600)  # SESSION_MAX_HOURS is 12
    accounts.end_session("new-session", started=time.time())
    assert not accounts.session_ended("old-session") and accounts.session_ended("new-session")


def test_replacing_a_second_factor_needs_the_password_and_ends_other_sessions(app: Flask) -> None:
    name, password = _user()
    client, _ = _sign_in(app, name, password)
    other, _ = _sign_in(app, name, password)
    headers = _csrf(client)
    path = "/api/v1/account/second-factors/totp"
    started = client.post(path, json={}, headers=headers).get_json()["data"]
    code = totp.code_at(started["secret"], int(time.time() // 30))
    assert client.put(path, json={"code": code}, headers=headers).status_code == 200
    enrolled = accounts.get(name)["second_factors"]["totp"]  # type: ignore[index]
    assert other.get("/api/v1/session").status_code == 200  # setting up the first one ends nothing

    assert client.post(path, json={}, headers=headers).status_code == 400  # no password
    assert client.post(path, json={"password": "wrong-password-123"}, headers=headers).status_code == 400
    started = client.post(path, json={"password": password}, headers=headers).get_json()["data"]
    new_code = totp.code_at(started["secret"], int(time.time() // 30))
    # a refused attempt in between: the replacement must be confirmed again
    assert client.post(path, json={"password": "wrong-password-123"}, headers=headers).status_code == 400
    assert client.put(path, json={"code": new_code}, headers=headers).status_code == 400
    assert accounts.get(name)["second_factors"]["totp"] == enrolled  # type: ignore[index]

    started = client.post(path, json={"password": password}, headers=headers).get_json()["data"]
    new_code = totp.code_at(started["secret"], int(time.time() // 30))
    assert client.put(path, json={"code": new_code}, headers=headers).status_code == 200
    assert accounts.get(name)["second_factors"]["totp"]["secret"] != enrolled["secret"]  # type: ignore[index]
    assert other.get("/api/v1/session").status_code == 401  # other sessions end
    assert client.get("/api/v1/session").status_code == 200  # this one continues


def test_an_email_address_of_another_account_is_refused(app: Flask, mailbox: FakeMailer) -> None:
    owner, _ = _user()
    accounts.update(owner, email=f"{owner}@example.com")
    name, password = _user()
    client, _ = _sign_in(app, name, password)
    headers = _csrf(client)
    taken = client.patch("/api/v1/account", json={"email": f"{owner.upper()}@Example.com"}, headers=headers)
    assert (
        taken.status_code == 400 and taken.get_json()["detail"] == "Another account already uses this e-mail address."
    )
    path = "/api/v1/account/second-factors/email"
    factor = client.post(path, json={"email": f"{owner}@example.com"}, headers=headers)
    assert factor.status_code == 400 and not mailbox.sent
    assert accounts.get(name).get("email") is None  # type: ignore[union-attr]
    assert accounts.find(f"{owner}@example.com") == owner


def test_changing_the_sign_in_email_ends_other_sessions(app: Flask) -> None:
    name, password = _user()
    client, _ = _sign_in(app, name, password)
    other, _ = _sign_in(app, name, password)
    headers = _csrf(client)
    assert client.patch("/api/v1/account", json={"first_name": "Sam"}, headers=headers).status_code == 200
    assert other.get("/api/v1/session").status_code == 200  # a name changes nothing
    assert client.patch("/api/v1/account", json={"email": f"{name}@example.com"}, headers=headers).status_code == 200
    assert other.get("/api/v1/session").status_code == 401 and client.get("/api/v1/session").status_code == 200
