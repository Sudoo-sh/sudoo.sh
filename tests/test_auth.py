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
    return name, accounts.create(name, role)


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
    # the previous pages cannot sign this account in with a password alone
    assert (
        app.test_client().post("/api/login", json={"username": name, "password": password}, headers=ORIGIN).status_code
        == 403
    )


# ---------------------------------------------------------------- e-mail code


class FakeMailer:
    ready = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        self.sent.append((to, subject.rsplit(" ", 1)[-1]))


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> FakeMailer:
    fake = FakeMailer()
    monkeypatch.setattr(email_code, "mailer", lambda: fake)
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
    assert answer.status_code == 201 and answer.get_json()["data"]["must_set_up_second_factor"]
    assert client.get("/api/v1/instances").status_code == 403
    assert client.get("/api/v1/account").get_json()["data"]["second_factor_required"]
    operator, operator_password = _user("operator")
    other, _ = _sign_in(app, operator, operator_password)
    assert other.get("/api/v1/instances").status_code == 200  # "admins": operators are not asked


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
