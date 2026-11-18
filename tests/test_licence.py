"""The Sudoo.sh Licence Agreement: the bundled text, acceptance records, the step that stops super admins until they
accept, re-acceptance of a new version, the installer's log and sending acceptances to the hub."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import ORIGIN, sign_in
from fake_hub import FakeHub
from sudoo import accounts, licence
from sudoo.config import live
from sudoo.storage import storage

REPO = Path(__file__).resolve().parent.parent
Client = tuple[FlaskClient, dict[str, str]]


def licence_acceptance(use: str = "company", **extra: Any) -> dict[str, Any]:
    """What the web app sends when the bundled agreement is accepted."""
    agreement = licence.bundled()
    return {
        "accepted": True,
        "version": agreement.version,
        "sha256": agreement.sha256,
        "use": use,
        "on_behalf": use != "personal",
        "language": "en",
        **extra,
    }


@pytest.fixture
def fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """An installation where nobody has accepted the agreement yet (as one updated from before 3.0)."""
    monkeypatch.setattr(licence, "COLLECTION", f"licence_test_{time.monotonic_ns()}")
    monkeypatch.setattr(licence, "SYNC_COLLECTION", f"licence_sync_test_{time.monotonic_ns()}")
    yield


@pytest.fixture
def new_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[licence.Agreement]:
    """A release that bundles version 1.1 of the agreement, required by LICENCE_REQUIRED_VERSION."""
    text = licence.TEXT_FILE.read_text().replace("Version 1.0, effective", "Version 1.1, effective", 1)
    path = tmp_path / "agreement.md"
    path.write_text(text)
    monkeypatch.setattr(licence, "TEXT_FILE", path)
    licence.bundled.cache_clear()
    licence.rendered.cache_clear()
    licence._ACCEPTED.clear()
    live.change({"licence_required_version": "1.1"})
    yield licence.bundled()
    live.change({"licence_required_version": None})
    monkeypatch.undo()
    licence.bundled.cache_clear()
    licence.rendered.cache_clear()
    licence._ACCEPTED.clear()


# ---------------------------------------------------------------- the text


def test_the_bundled_copy_is_exactly_licence_md() -> None:
    assert licence.TEXT_FILE.read_bytes() == (REPO / "LICENSE.md").read_bytes()
    agreement = licence.bundled()
    assert agreement.version == "1.0" and agreement.effective == "2026-11-18"
    assert agreement.sha256 == hashlib.sha256((REPO / "LICENSE.md").read_bytes()).hexdigest()
    assert agreement.version_url == "https://sudoo.sh/legal/license/v1.0"


def test_the_demo_has_the_same_text() -> None:
    """The demo (web/src/demo) shows the agreement from its own copy, made from this one."""
    import json

    demo = json.loads((REPO / "web" / "src" / "demo" / "backend" / "licence.json").read_text())
    assert demo == licence.rendered(), "run: uv run python -m sudoo.licence > web/src/demo/backend/licence.json"


def test_the_text_is_public_for_first_run_setup(app: Flask) -> None:
    answer = app.test_client().get("/api/v1/licence/text")
    assert answer.status_code == 200
    data = answer.get_json()["data"]
    assert data["version"] == "1.0" and data["sha256"] == licence.bundled().sha256
    assert "One account per company" in data["summary_html"] and "<blockquote>" not in data["summary_html"]
    assert "نوع الاستخدام" in data["summary_ar_html"]
    assert "Governing law and disputes" in data["html"]
    assert data["statements"]["accept"].endswith("version 1.0.")
    # who accepted is not public
    assert app.test_client().get("/api/v1/licence").status_code == 401


# ---------------------------------------------------------------- first-run setup


def test_setup_needs_the_acceptance_and_records_it(app: Flask, monkeypatch: pytest.MonkeyPatch, fresh: None) -> None:
    monkeypatch.setattr(accounts, "setup_needed", lambda: True)
    monkeypatch.setattr(accounts, "_setup_file", lambda: accounts.settings.manager_dir / "setup_code_licence")
    code = accounts.setup_code()
    client = app.test_client()
    body = {
        "setup_code": code,
        "password": "violet-harbour-candle-58",
        "first_name": "Lina",
        "email": "lina@example.com",
    }
    headers = {**ORIGIN, "User-Agent": "Browser/1.0"}

    missing = client.post("/api/v1/setup", json=body, headers=headers)
    assert missing.status_code == 400 and "Licence Agreement" in missing.get_json()["detail"]
    no_use = client.post("/api/v1/setup", json={**body, "licence": licence_acceptance(use="")}, headers=headers)
    assert no_use.status_code == 400 and "how you will use" in no_use.get_json()["detail"]
    not_authorised = licence_acceptance(use="service_provider", on_behalf=False)
    refused = client.post("/api/v1/setup", json={**body, "licence": not_authorised}, headers=headers)
    assert refused.status_code == 400 and "authorised" in refused.get_json()["detail"]
    other_text = client.post(
        "/api/v1/setup", json={**body, "licence": licence_acceptance(sha256="0" * 64)}, headers=headers
    )
    assert other_text.status_code == 400 and "changed" in other_text.get_json()["detail"]
    assert licence.history() == [] and accounts.get("lina") is None  # nothing created

    done = client.post(
        "/api/v1/setup", json={**body, "licence": licence_acceptance(use="service_provider")}, headers=headers
    )
    assert done.status_code == 201, done.get_json()
    assert done.get_json()["data"]["licence_required"] is False
    [entry] = licence.history()
    assert entry["version"] == "1.0" and entry["sha256"] == licence.bundled().sha256
    assert entry["user"] == "lina" and entry["user_email"] == "lina@example.com" and entry["user_name"] == "Lina"
    assert entry["use"] == "service_provider" and entry["on_behalf"] is True and entry["surface"] == "panel_setup"
    assert entry["ip"] and entry["user_agent"] == "Browser/1.0" and entry["accepted_at"].endswith("Z")
    assert entry["statements"][1].startswith("I accept it on behalf of my company")
    assert client.get("/api/v1/instances").status_code == 200  # nothing waits
    storage().delete(accounts.USERS, "lina")


def test_personal_use_needs_no_company_statement(fresh: None) -> None:
    accepted = licence.check_acceptance(licence_acceptance(use="personal", on_behalf=True))
    assert accepted == {"use": "personal", "on_behalf": False, "language": "en"}
    entry = licence.record(user="x", account={}, role="super_admin", ip="1", user_agent="", surface="panel", **accepted)
    assert entry["statements"] == [licence.statements("1.0")["accept"]]


# ---------------------------------------------------------------- an installation from before 3.0


def test_a_super_admin_must_accept_before_managing_anything(app: Flask, passwords: dict[str, str], fresh: None) -> None:
    web, headers = sign_in(app, "admin1", passwords["admin"])
    me = web.get("/api/v1/session").get_json()["data"]
    assert me["licence_required"] is True
    for path in ("/api/v1/instances", "/api/v1/settings", "/api/v1/users"):
        answer = web.get(path)
        assert answer.status_code == 403 and "Licence Agreement" in answer.get_json()["detail"]
    assert web.patch("/api/v1/settings", json={"idle_hours": 3}, headers=headers).status_code == 403
    # what the step itself needs stays open
    assert web.get("/api/v1/account").status_code == 200
    status = web.get("/api/v1/licence").get_json()["data"]
    assert status["pending"] is True and status["accepted"] is None and status["required_version"] == "1.0"

    # other people are not stopped
    operator, _ = sign_in(app, "sync1", passwords["operator"])
    assert operator.get("/api/v1/session").get_json()["data"]["licence_required"] is False
    assert operator.get("/api/v1/instances").status_code == 200
    refused = operator.post("/api/v1/licence/acceptances", json=licence_acceptance(), headers=ORIGIN)
    assert refused.status_code == 403

    accepted = web.post("/api/v1/licence/acceptances", json=licence_acceptance(use="company"), headers=headers)
    assert accepted.status_code == 201, accepted.get_json()
    data = accepted.get_json()["data"]
    assert data["pending"] is False and data["accepted"]["user"] == "admin1" and data["accepted"]["use"] == "company"
    assert data["history"][-1]["surface"] == "panel" and data["history"][-1]["ip"]
    assert web.get("/api/v1/instances").status_code == 200
    assert web.get("/api/v1/session").get_json()["data"]["licence_required"] is False


def test_records_are_only_ever_added(app: Flask, admin: Client, fresh: None, monkeypatch: pytest.MonkeyPatch) -> None:
    web, headers = admin
    for use in ("personal", "company"):
        assert (
            web.post("/api/v1/licence/acceptances", json=licence_acceptance(use=use), headers=headers).status_code
            == 201
        )
    first, second = licence.history()
    assert (first["use"], second["use"]) == ("personal", "company")
    assert licence.latest() == second
    # a record with the same id is refused, never replaced
    monkeypatch.setattr(licence.time, "time", lambda: first["ts"])
    monkeypatch.setattr(licence.secrets, "token_hex", lambda n: first["id"].rsplit("-", 1)[1])
    with pytest.raises(licence.LicenceError):
        licence.record(
            user="x",
            account={},
            role="super_admin",
            ip="1",
            user_agent="",
            use="personal",
            on_behalf=False,
            surface="panel",
        )
    assert licence.history() == [first, second]
    audit = web.get("/api/v1/audit").get_json()["data"]
    assert any(item["event"] == "accepted the Sudoo.sh Licence Agreement" for item in audit)


def test_viewers_see_who_accepted_but_not_the_details(app: Flask, passwords: dict[str, str]) -> None:
    web, _ = sign_in(app, "sync1", passwords["operator"])
    data = web.get("/api/v1/licence").get_json()["data"]
    assert data["accepted"]["user"] and "history" not in data and "ip" not in data["accepted"]


# ---------------------------------------------------------------- a new version


def test_a_new_required_version_asks_again(app: Flask, admin: Client, new_version: licence.Agreement) -> None:
    web, headers = admin
    assert licence.pending() is True
    assert web.get("/api/v1/session").get_json()["data"]["licence_required"] is True
    assert web.get("/api/v1/instances").status_code == 403
    old = licence_acceptance(version="1.0", sha256=hashlib.sha256((REPO / "LICENSE.md").read_bytes()).hexdigest())
    assert web.post("/api/v1/licence/acceptances", json=old, headers=headers).status_code == 400
    assert web.post("/api/v1/licence/acceptances", json=licence_acceptance(), headers=headers).status_code == 201
    assert licence.latest()["version"] == "1.1" and licence.latest()["sha256"] == new_version.sha256
    assert web.get("/api/v1/instances").status_code == 200


def test_the_required_version_is_a_setting(app: Flask, admin: Client) -> None:
    web, headers = admin
    setting = next(s for s in web.get("/api/v1/settings").get_json()["data"] if s["key"] == "licence_required_version")
    assert setting["value"] == "1.0" and setting["applies"] == "now"
    newer = web.patch("/api/v1/settings", json={"licence_required_version": "2.0"}, headers=headers)
    assert newer.status_code == 400 and "Update the panel first" in newer.get_json()["detail"]
    assert web.patch("/api/v1/settings", json={"licence_required_version": "one"}, headers=headers).status_code == 400
    assert web.patch("/api/v1/settings", json={"licence_required_version": "1.0"}, headers=headers).status_code == 200
    assert web.delete("/api/v1/settings/licence_required_version", headers=headers).status_code == 204


def test_a_required_version_this_release_does_not_have_stops_nobody(
    monkeypatch: pytest.MonkeyPatch, fresh: None
) -> None:
    monkeypatch.setattr(licence, "required_version", lambda: "9.0")
    assert licence.update_needed() is True and licence.pending() is False


# ---------------------------------------------------------------- the installer's log


def test_the_installer_log_is_read(tmp_path: Path) -> None:
    sha = "a" * 64
    log = tmp_path / "licence-acceptance.log"
    log.write_text(
        f"2026-11-18T10:00:00Z version=1.0 sha256={sha} host=erp-1 user=root via=prompt installer=3.0.0 command=install\n"
        "not a record\n"
        f"2026-11-18T11:00:00Z version=x sha256={sha} host=erp-1 user=root\n"
    )
    [entry] = licence.installer_records(log)
    assert entry["version"] == "1.0" and entry["sha256"] == sha and entry["host"] == "erp-1"
    assert entry["via"] == "prompt" and entry["surface"] == "installer" and entry["id"].startswith("installer-")
    assert licence.installer_records(tmp_path / "missing.log") == []


# ---------------------------------------------------------------- the hub


@pytest.fixture
def linked_hub(monkeypatch: pytest.MonkeyPatch, admin: Client) -> Iterator[FakeHub]:
    from sudoo.hub import client

    fake = FakeHub().start()
    monkeypatch.setattr(client, "hub_url", lambda: fake.url)
    monkeypatch.setattr(licence, "sync_soon", lambda: None)  # the test calls sync() itself
    for collection in (client.COLLECTION,):
        for key in storage().all(collection):
            storage().delete(collection, key)
    live.change({"marketplace": True})
    web, headers = admin
    assert web.post("/api/v1/marketplace/link", json={"code": "LINKCODE42"}, headers=headers).status_code == 200
    yield fake
    live.change({"marketplace": None})
    fake.stop()


def test_acceptances_wait_until_the_hub_takes_them(linked_hub: FakeHub, fresh: None, tmp_path: Path) -> None:
    licence.record(
        user="admin1",
        account={},
        role="super_admin",
        ip="1",
        user_agent="",
        use="company",
        on_behalf=True,
        surface="panel",
    )
    (licence.settings.manager_dir / licence.INSTALLER_LOG).write_text(
        f"2026-11-18T09:00:00Z version=1.0 sha256={'b' * 64} host=erp-1 user=root via=flag\n"
    )
    try:
        assert licence.sync() == 0  # this hub does not take them yet: kept
        assert licence.synced() == {}
        linked_hub.licence_acceptances = []
        assert licence.sync() == 2
        assert {a["surface"] for a in linked_hub.licence_acceptances} == {"panel", "installer"}
        assert licence.sync() == 0  # sent once
        assert all(doc["synced_at"] for doc in licence.status(full=True)["history"])
    finally:
        (licence.settings.manager_dir / licence.INSTALLER_LOG).unlink()


def test_nothing_is_sent_while_not_linked(fresh: None) -> None:
    licence.record(
        user="a",
        account={},
        role="super_admin",
        ip="1",
        user_agent="",
        use="personal",
        on_behalf=False,
        surface="panel",
    )
    assert licence.sync() == 0
