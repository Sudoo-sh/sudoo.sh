"""Recovery codes for the authenticator app, the sign-in e-mail, and the E-mail templates page: single use, hashes
only, new codes replace old ones, rate limits, who gets the e-mail and who may turn it off, and texts that are
checked (no unknown placeholders, no template code) and escaped."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

import sudoo.mail
from sudoo import accounts
from sudoo.auth import recovery, sign_in_notice, totp
from sudoo.config import live
from sudoo.core import audit
from sudoo.mail import overrides, textformat
from sudoo.mail.catalog import MESSAGES
from sudoo.mail.render import compose
from sudoo.storage import storage

ORIGIN = {"Origin": "http://localhost"}
PASSWORD = "orchid-pebble-summit-73"


class FakeMailer:
    ready = True

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.fail = False

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        if self.fail:
            raise sudoo.mail.MailError("The mail server refused the message.")
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html or ""})


class NowThread:
    """threading.Thread that runs its target at once, so a test sees the e-mail sent in the background."""

    def __init__(self, target: Callable[[], None], **_: Any) -> None:
        self.target = target

    def start(self) -> None:
        self.target()


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    accounts.THROTTLE._failures.clear()
    monkeypatch.setattr(sign_in_notice.threading, "Thread", NowThread)
    yield
    live.change(dict.fromkeys(live.overrides()))
    accounts.THROTTLE._failures.clear()
    for key in list(storage().all(overrides.COLLECTION)):
        overrides.reset(key)


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> FakeMailer:
    fake = FakeMailer()
    monkeypatch.setattr(sudoo.mail, "mailer", lambda: fake)
    return fake


def _user(role: str = "admin", email: bool = True) -> str:
    name = f"r{time.time_ns() % 10**10}"
    accounts.create(name, role, PASSWORD, email=f"{name}@example.com" if email else None)
    return name


def _sign_in(app: Flask, name: str) -> tuple[FlaskClient, Any]:
    client = app.test_client()
    return client, client.post("/api/v1/session", json={"username": name, "password": PASSWORD}, headers=ORIGIN)


def _headers(client: FlaskClient) -> dict[str, str]:
    return {**ORIGIN, "X-CSRF-Token": client.get("/api/v1/session").get_json()["data"]["csrf_token"]}


def _with_app(app: Flask, name: str) -> tuple[FlaskClient, dict[str, str], str, list[str]]:
    """A signed-in client whose account has just set up the authenticator app: its secret and recovery codes."""
    client, answer = _sign_in(app, name)
    assert answer.status_code == 201
    headers = _headers(client)
    secret = client.post("/api/v1/account/second-factors/totp", json={}, headers=headers).get_json()["data"]["secret"]
    code = totp.code_at(secret, int(time.time() // 30))
    done = client.put("/api/v1/account/second-factors/totp", json={"code": code}, headers=headers)
    assert done.status_code == 200
    return client, headers, secret, done.get_json()["meta"]["recovery_codes"]


def _recovery_sign_in(app: Flask, name: str, code: str) -> Any:
    client, step1 = _sign_in(app, name)
    assert step1.status_code == 200
    return client.post("/api/v1/session/second-factor", json={"method": "recovery", "code": code}, headers=ORIGIN)


# ---------------------------------------------------------------- recovery codes


def test_setting_up_the_app_makes_recovery_codes_kept_only_as_hashes(app: Flask) -> None:
    live.change({"recovery_codes_count": 8})
    name = _user()
    _client, _headers_, _secret, codes = _with_app(app, name)
    assert len(codes) == 8 and len(set(codes)) == 8
    assert all(len(code) == 11 and code[5] == "-" for code in codes)
    stored = json.dumps(accounts.get(name))
    assert not any(code in stored or recovery.normalize(code) in stored for code in codes)
    assert accounts.get(name)["recovery_codes"]["hashes"][0] == recovery.digest(name, codes[0])
    assert recovery.digest(name, codes[0]) != recovery.digest("someone-else", codes[0])


def test_a_recovery_code_signs_in_once(app: Flask) -> None:
    name = _user()
    _with_app(app, name)
    codes = recovery.create(name)
    _client, step1 = _sign_in(app, name)
    methods = [m["name"] for m in step1.get_json()["data"]["second_factor"]["methods"]]
    assert methods == ["totp", "recovery"]
    typed = codes[0].upper().replace("-", " ")  # capitals, spaces or no dash: the same code
    assert _recovery_sign_in(app, name, typed).status_code == 201
    again = _recovery_sign_in(app, name, codes[0])
    assert again.status_code == 401 and "used already" in again.get_json()["detail"]
    assert recovery.state(accounts.get(name) or {})["left"] == len(codes) - 1
    events = [entry["event"] for entry in audit.latest(10, user=name)]
    assert "recovery code used" in events and "second factor refused (recovery code)" in events


def test_wrong_recovery_codes_are_rate_limited_like_passwords(app: Flask) -> None:
    name = _user()
    _with_app(app, name)
    codes = recovery.create(name)
    answers = [_recovery_sign_in(app, name, "aaaaa-bbbbb").status_code for _ in range(5)]
    assert answers == [401] * 5
    _client, blocked = _sign_in(app, name)
    assert blocked.status_code == 429  # the account and address wait now, whatever is typed
    assert recovery.state(accounts.get(name) or {})["left"] == len(codes)  # and it was not spent


def test_new_codes_need_the_password_and_replace_the_old_ones(app: Flask) -> None:
    name = _user()
    client, headers, _secret, first = _with_app(app, name)
    wrong = client.post("/api/v1/account/recovery-codes", json={"password": "not-it-at-all-99"}, headers=headers)
    assert wrong.status_code == 400
    made = client.post("/api/v1/account/recovery-codes", json={"password": PASSWORD}, headers=headers)
    assert made.status_code == 201
    second = made.get_json()["data"]["codes"]
    assert made.get_json()["data"]["left"] == len(second) and not set(first) & set(second)
    assert recovery.use(name, first[0]) is None  # the old ones no longer work
    assert recovery.use(name, second[0]) == len(second) - 1
    shown = client.get("/api/v1/account").get_json()["data"]
    assert shown["recovery_codes"]["left"] == len(second) - 1 and shown["recovery_codes_needed"] is False


def test_codes_go_with_the_app(app: Flask, admin: tuple[FlaskClient, dict[str, str]]) -> None:
    name = _user()
    client, headers, _secret, _codes = _with_app(app, name)
    removed = client.delete("/api/v1/account/second-factors/totp", json={"password": PASSWORD}, headers=headers)
    assert removed.status_code == 204 and "recovery_codes" not in (accounts.get(name) or {})
    no_app = client.post("/api/v1/account/recovery-codes", json={"password": PASSWORD}, headers=headers)
    assert no_app.status_code == 400

    other = _user()
    _with_app(app, other)
    admin_client, admin_headers = admin
    reset = admin_client.delete(f"/api/v1/users/{other}/second-factors", headers=admin_headers)
    assert reset.status_code == 204 and "recovery_codes" not in (accounts.get(other) or {})


def test_an_account_with_the_app_but_no_codes_is_asked_to_make_them(app: Flask) -> None:
    name = _user()
    _with_app(app, name)
    accounts.update(name, recovery_codes=None)  # set up before recovery codes existed
    secret = totp.vault.decrypt(accounts.get(name)["second_factors"]["totp"]["secret"], f"totp:{name}")
    accounts.update(name, second_factors={"totp": {**accounts.get(name)["second_factors"]["totp"], "last_step": 0}})
    client, _ = _sign_in(app, name)
    code = totp.code_at(secret, int(time.time() // 30))
    signed = client.post("/api/v1/session/second-factor", json={"method": "totp", "code": code}, headers=ORIGIN)
    assert signed.status_code == 201 and signed.get_json()["data"]["recovery_codes_needed"] is True
    assert _recovery_sign_in(app, name, "aaaaa-bbbbb").status_code == 401  # no codes: the method is not offered


# ---------------------------------------------------------------- the sign-in e-mail


def test_every_sign_in_sends_the_e_mail(app: Flask, mailbox: FakeMailer) -> None:
    name = _user("viewer")
    _client, answer = _sign_in(app, name)
    assert answer.status_code == 201
    [mail] = mailbox.sent
    assert mail["to"] == f"{name}@example.com" and mail["subject"] == "New sign-in to your Sudoo.sh account"
    assert "Password" in mail["text"] and "turn it off" in mail["text"]


def test_the_e_mail_names_the_recovery_code(app: Flask, mailbox: FakeMailer) -> None:
    name = _user()
    _with_app(app, name)
    codes = recovery.create(name)
    mailbox.sent.clear()
    assert _recovery_sign_in(app, name, codes[0]).status_code == 201
    assert "Password and recovery code" in mailbox.sent[-1]["text"]


def test_who_gets_the_sign_in_e_mail(app: Flask, mailbox: FakeMailer) -> None:
    name = _user("viewer")
    client, _ = _sign_in(app, name)
    headers = _headers(client)
    turned_off = client.patch("/api/v1/account", json={"sign_in_email": False}, headers=headers)
    assert turned_off.status_code == 200 and turned_off.get_json()["data"]["sign_in_email"]["enabled"] is False
    mailbox.sent.clear()
    _sign_in(app, name)
    assert mailbox.sent == []  # the person's own choice

    live.change({"sign_in_email_opt_out": False})  # the administrator decides for everyone
    _sign_in(app, name)
    assert len(mailbox.sent) == 1
    refused = client.patch("/api/v1/account", json={"sign_in_email": False}, headers=headers)
    assert refused.status_code == 403

    live.change({"sign_in_email": False, "sign_in_email_opt_out": True})
    accounts.update(name, sign_in_email=None)
    mailbox.sent.clear()
    _sign_in(app, name)
    assert mailbox.sent == []  # off by default on this server
    accounts.update(name, sign_in_email=True)
    _sign_in(app, name)
    assert len(mailbox.sent) == 1  # anyone may turn it on for themselves
    assert client.patch("/api/v1/account", json={"sign_in_email": "yes"}, headers=headers).status_code == 400


def test_guided_tour_progress_is_kept_per_person_on_the_server_and_an_administrator_can_turn_tours_off(
    app: Flask,
) -> None:
    name, other = _user("viewer"), _user("viewer")
    client, signed_in = _sign_in(app, name)
    headers = _headers(client)
    assert signed_in.get_json()["data"]["tours"] == {"seen": [], "tips": True, "enabled": True}

    saved = client.patch("/api/v1/account", json={"tours": {"mark": ["panel-welcome"]}}, headers=headers)
    assert saved.status_code == 200
    assert saved.get_json()["data"]["tours"] == {"seen": ["panel-welcome"], "tips": True, "enabled": True}
    tips = client.patch("/api/v1/account", json={"tours": {"tips": False}}, headers=headers)
    assert tips.get_json()["data"]["tours"] == {"seen": ["panel-welcome"], "tips": False, "enabled": True}  # kept
    # a new browser of the same person sees the same
    again = app.test_client().post("/api/v1/session", json={"username": name, "password": PASSWORD}, headers=ORIGIN)
    assert again.get_json()["data"]["tours"] == {"seen": ["panel-welcome"], "tips": False, "enabled": True}
    # another person is not affected
    elsewhere, other_in = _sign_in(app, other)
    assert other_in.get_json()["data"]["tours"] == {"seen": [], "tips": True, "enabled": True}

    live.change({"tours_enabled": False})  # the administrator's switch, for everyone, at once
    assert client.get("/api/v1/session").get_json()["data"]["tours"]["enabled"] is False
    assert client.get("/api/v1/account").get_json()["data"]["tours"]["enabled"] is False
    live.change({"tours_enabled": True})
    restarted = client.patch("/api/v1/account", json={"tours": {"seen": []}}, headers=headers)
    assert restarted.get_json()["data"]["tours"] == {"seen": [], "tips": False, "enabled": True}

    # only the tour state changes: the profile and the other choices stay, and two saves in turn both stay
    before = {k: v for k, v in accounts.get(name).items() if k != "tours"}
    client.patch("/api/v1/account", json={"tours": {"mark": ["x-tour"]}}, headers=headers)
    client.patch("/api/v1/account", json={"tours": {"tips": True}}, headers=headers)
    after = accounts.get(name)
    assert {k: v for k, v in after.items() if k != "tours"} == before
    assert after["tours"] == {"seen": ["x-tour"], "tips": True}  # the reset before it left nothing else
    client.patch("/api/v1/account", json={"tours": {"seen": []}}, headers=headers)

    # two stale browsers: each marks its own tour without knowing the other's; both stay. Reset is explicit only.
    client.patch("/api/v1/account", json={"tours": {"mark": ["tour-a"]}}, headers=headers)
    client.patch("/api/v1/account", json={"tours": {"mark": ["tour-b"]}}, headers=headers)
    assert accounts.get(name)["tours"]["seen"] == ["tour-a", "tour-b"]
    client.patch("/api/v1/account", json={"tours": {"seen": [], "mark": ["tour-c"]}}, headers=headers)
    assert accounts.get(name)["tours"]["seen"] == ["tour-c"]
    client.patch("/api/v1/account", json={"tours": {"seen": []}}, headers=headers)

    for wrong in ({}, {"x": 1}, {"tips": "no"}, {"seen": "panel-welcome"}, {"seen": ["panel-welcome"]}, {"mark": ["Bad Name"]},
                  {"mark": [1]}, {"mark": "a"}, {"mark": ["a"] * 51}, "yes", None, ["panel-welcome"]):  # fmt: skip
        assert client.patch("/api/v1/account", json={"tours": wrong}, headers=headers).status_code == 400
    assert accounts.get(name)["tours"] == {"seen": [], "tips": True}  # unchanged by the refused bodies


def test_page_tours_are_kept_by_their_ids_for_a_viewer_and_never_touch_other_account_fields(app: Flask) -> None:
    name = _user("viewer")  # the lowest role: a tour is the person's own progress, so it needs no more
    client, _ = _sign_in(app, name)
    headers = _headers(client)
    before = {k: v for k, v in accounts.get(name).items() if k != "tours"}
    for tour in ("panel-welcome", "projects-page", "backups-page", "audit-page", "instances-page", "settings-page", "projects-page"):  # the last is a repeat
        saved = client.patch("/api/v1/account", json={"tours": {"mark": [tour]}}, headers=headers)
        assert saved.status_code == 200
    seen = ["panel-welcome", "projects-page", "backups-page", "audit-page", "instances-page", "settings-page"]
    assert client.get("/api/v1/session").get_json()["data"]["tours"]["seen"] == seen
    assert accounts.get(name)["tours"]["seen"] == seen
    assert {k: v for k, v in accounts.get(name).items() if k != "tours"} == before
    client.patch("/api/v1/account", json={"tours": {"seen": []}}, headers=headers)  # "Restart all tours"
    assert client.get("/api/v1/session").get_json()["data"]["tours"]["seen"] == []


def test_marking_tours_seen_from_many_places_at_once_keeps_every_one(app: Flask) -> None:
    import _thread  # real threads: the suite's fixture replaces threading.Thread with one that runs at once

    name = _user("viewer")
    before = dict(accounts.get(name))
    names = [f"tour-{n}" for n in range(12)]
    gate, done = threading.Barrier(len(names)), threading.Semaphore(0)

    def mark(one: str) -> None:
        gate.wait()
        accounts.update_tours(name, {"mark": [one]})
        done.release()

    for one in names:
        _thread.start_new_thread(mark, (one,))
    for _ in names:
        assert done.acquire(timeout=20)
    after = accounts.get(name)
    assert sorted(after["tours"]["seen"]) == sorted(names)
    assert {k: v for k, v in after.items() if k != "tours"} == {k: v for k, v in before.items() if k != "tours"}
    accounts.update_tours(name, {"tips": False})
    assert accounts.get(name)["tours"]["tips"] is False and len(accounts.get(name)["tours"]["seen"]) == 12


def test_each_person_chooses_the_language_and_the_server_has_a_default(app: Flask) -> None:
    name = _user("viewer")
    client, signed_in = _sign_in(app, name)
    headers = _headers(client)
    assert signed_in.get_json()["data"]["language"] == "en"  # nothing chosen: the server's default
    assert client.get("/api/v1/account").get_json()["data"]["language"] == {
        "value": "en",
        "own_choice": None,
        "default": "en",
    }

    chosen = client.patch("/api/v1/account", json={"language": "ar"}, headers=headers)
    assert chosen.status_code == 200
    assert chosen.get_json()["data"]["language"] == {"value": "ar", "own_choice": "ar", "default": "en"}
    assert accounts.get(name)["language"] == "ar"  # kept on the account: it follows the person to any browser
    assert (
        app.test_client()
        .post("/api/v1/session", json={"username": name, "password": PASSWORD}, headers=ORIGIN)
        .get_json()["data"]["language"]
        == "ar"
    )

    live.change({"default_language": "ar"})  # the administrator's default, for those who have not chosen
    assert client.get("/api/v1/setup").get_json()["data"]["default_language"] == "ar"
    assert client.get("/api/v1/contact").get_json()["data"]["default_language"] == "ar"
    client.patch("/api/v1/account", json={"language": "en"}, headers=headers)
    assert client.get("/api/v1/session").get_json()["data"]["language"] == "en"  # an own choice wins
    back = client.patch("/api/v1/account", json={"language": None}, headers=headers)
    assert back.get_json()["data"]["language"] == {"value": "ar", "own_choice": None, "default": "ar"}
    assert client.get("/api/v1/session").get_json()["data"]["language"] == "ar"

    for wrong in ("fr", "AR", 1, ["ar"], {"x": 1}):
        assert client.patch("/api/v1/account", json={"language": wrong}, headers=headers).status_code == 400
    assert accounts.get(name).get("language") is None


def test_the_default_language_is_a_setting_changed_in_the_panel() -> None:
    from sudoo.web.v1 import settings as settings_api

    found = {s["key"]: s for s in settings_api.resource()}
    assert found["default_language"]["value"] == "en" and found["default_language"]["applies"] == "now"
    live.change({"default_language": "ar"})
    assert {s["key"]: s for s in settings_api.resource()}["default_language"]["value"] == "ar"


def test_no_e_mail_without_mail_or_an_address_and_a_failure_never_blocks(
    app: Flask, mailbox: FakeMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_address = _user("viewer", email=False)
    assert _sign_in(app, no_address)[1].status_code == 201 and mailbox.sent == []
    name = _user("viewer")
    mailbox.fail = True
    assert _sign_in(app, name)[1].status_code == 201
    monkeypatch.setattr(FakeMailer, "ready", False)
    mailbox.fail = False
    assert _sign_in(app, name)[1].status_code == 201 and mailbox.sent == []


def test_sign_out_everywhere_else(app: Flask) -> None:
    name = _user("viewer")
    other, _ = _sign_in(app, name)
    client, _ = _sign_in(app, name)
    assert client.delete("/api/v1/account/sessions", headers=_headers(client)).status_code == 204
    assert client.get("/api/v1/session").status_code == 200
    assert other.get("/api/v1/session").status_code == 401


# ---------------------------------------------------------------- e-mail templates


def test_every_built_in_text_is_valid_and_renders() -> None:
    for key, message in MESSAGES.items():
        assert textformat.problems(message.subject, message.title, message.body, message.spec, message.required) == []
        composed = compose(key, message.samples())
        assert composed.subject and "Sudoo.sh" in composed.html and composed.text.endswith("server.\n")
        assert "{{" not in composed.html + composed.text + composed.subject


def test_unknown_placeholders_template_code_and_bad_links_are_refused(
    admin: tuple[FlaskClient, dict[str, str]],
) -> None:
    client, headers = admin
    base = {"subject": "Your sign-in code", "title": "Code", "body": "Hello {{ name }},\n\n{{ code_box }}"}
    assert client.put("/api/v1/mail-templates/sign_in_code", json=base, headers=headers).status_code == 200
    for body, why in (
        ("{{ code }} and {{ password }}", "Unknown placeholder: {{ password }}"),
        ("{{ code }} {% for x in y %}{{ config }}{% endfor %}", "Unknown placeholder: {{ config }}"),
        ("{{ code }} {{ code.__class__ }}", "not written like"),
        ("Code: {{ code_box }}", "must stand alone"),
        ("{{ code }}\n[Click](javascript:alert(1))", ""),  # not a link at all: shown as text
        ("{{ code }}\n[Click]({{ name }})", "not an address"),
        ("Hello", "must contain {{ code }} or {{ code_box }}"),
    ):
        answer = client.put("/api/v1/mail-templates/sign_in_code", json={**base, "body": body}, headers=headers)
        if why:
            assert answer.status_code == 400 and why in answer.get_json()["detail"], body
        else:
            assert answer.status_code == 200
    subject = client.put("/api/v1/mail-templates/sign_in_code", json={**base, "subject": "{{ code_box }}"},
                         headers=headers)  # fmt: skip
    assert subject.status_code == 400


def test_values_and_typed_html_are_escaped(admin: tuple[FlaskClient, dict[str, str]]) -> None:
    client, headers = admin
    body = "<script>alert(1)</script> **Hello {{ name }}**\n\n{{ code_box }}\n\n[Open]({{ panel_link }})"
    preview = client.post("/api/v1/mail-templates/sign_in_code/preview", json={"body": body}, headers=headers)
    data = preview.get_json()["data"]
    assert data["problems"] == [] and "<script>" not in data["html"] and "&lt;script&gt;" in data["html"]
    assert "<strong>Hello sam</strong>" in data["html"] and "<script>alert(1)</script>" in data["text"]
    saved = client.put("/api/v1/mail-templates/sign_in_code",
                       json={"subject": "Code {{ code }}", "title": "T", "body": body}, headers=headers)  # fmt: skip
    assert saved.status_code == 200
    sent = compose(
        "sign_in_code", {"name": '<img src=x onerror="alert(1)">', "code": "1", "panel_link": "javascript:x"}
    )
    assert "<img" not in sent.html and "&lt;img" in sent.html and "javascript:" not in sent.html
    bad = client.post("/api/v1/mail-templates/sign_in_code/preview", json={"body": "{{ nope }}"}, headers=headers)
    assert bad.status_code == 200 and bad.get_json()["data"]["problems"] and bad.get_json()["data"]["html"] is None


def test_a_changed_text_is_used_until_it_is_reset(admin: tuple[FlaskClient, dict[str, str]]) -> None:
    client, headers = admin
    text = {"subject": "Hej: {{ server }}", "title": "Det fungerar", "body": "Hej,\n\nSkickat via {{ server }}."}
    changed = client.put("/api/v1/mail-templates/test", json=text, headers=headers)
    assert changed.status_code == 200 and changed.get_json()["data"]["customized"] is True
    assert changed.get_json()["data"]["changed_by"] == "admin1"
    assert compose("test", {"server": "smtp.x:587"}).subject == "Hej: smtp.x:587"
    assert audit.latest(1)[0]["event"] == "e-mail template changed"
    listed = client.get("/api/v1/mail-templates", headers=headers).get_json()["data"]
    keys = [item["key"] for item in listed]
    assert keys == list(MESSAGES) and "sign_in_notice" in keys
    entry = next(item for item in listed if item["key"] == "test")
    assert entry["default"]["subject"] == "Sudoo.sh e-mail works" and entry["subject"] == text["subject"]
    assert {p["name"] for p in entry["placeholders"]} == {"server", "panel_link"}
    assert client.delete("/api/v1/mail-templates/test", headers=headers).status_code == 204
    assert compose("test", {"server": "s"}).subject == "Sudoo.sh e-mail works"
    assert audit.latest(1)[0]["event"] == "e-mail template reset to default"
    assert client.get("/api/v1/mail-templates/nope", headers=headers).status_code == 404


def test_send_me_a_test(admin: tuple[FlaskClient, dict[str, str]], mailbox: FakeMailer) -> None:
    client, headers = admin
    draft = {"subject": "Draft {{ alert_title }}", "title": "{{ alert_title }}", "body": "{{ alert_text }}"}
    answer = client.post("/api/v1/mail-templates/alert/test", json=draft, headers=headers)
    assert answer.status_code == 200 and answer.get_json()["data"]["sent_to"] == "ada@example.com"
    assert mailbox.sent[-1]["subject"] == "[Test] Draft Instance shop-main is down"
    assert overrides.get("alert") is None  # a test does not save


def test_only_super_admins_change_e_mail_texts(app: Flask, operator: tuple[FlaskClient, dict[str, str]]) -> None:
    name = _user("admin")
    client, _ = _sign_in(app, name)
    assert client.get("/api/v1/mail-templates").status_code == 403
    assert client.put("/api/v1/mail-templates/test", json={}, headers=_headers(client)).status_code == 403
    developer, headers = operator
    assert developer.delete("/api/v1/mail-templates/test", headers=headers).status_code == 403


def test_the_demo_shows_the_same_e_mails() -> None:
    """web/src/demo/backend/mail.json (the demo's copy of the catalogue) matches mail/catalog.py."""
    from pathlib import Path

    demo = json.loads((Path(__file__).resolve().parent.parent / "web/src/demo/backend/mail.json").read_text())
    shown = {m["key"]: m for m in demo["messages"]}
    assert list(shown) == list(MESSAGES)
    for key, message in MESSAGES.items():
        assert shown[key]["default"] == {"subject": message.subject, "title": message.title, "body": message.body}
        assert {p["name"] for p in shown[key]["placeholders"]} == set(message.spec)


def test_adding_a_second_factor_next_to_one_set_up_needs_the_password(
    app: Flask, mailbox: FakeMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo.auth import email_code

    monkeypatch.setattr(email_code, "mailer", lambda: mailbox)
    name = _user()
    client, headers, _, _ = _with_app(app, name)
    body = {"email": f"{name}-other@example.com"}
    refused = client.post("/api/v1/account/second-factors/email", json=body, headers=headers)
    assert refused.status_code == 400, refused.get_json()
    assert mailbox.sent == [] or all(m["to"] != body["email"] for m in mailbox.sent)
    wrong = client.post("/api/v1/account/second-factors/email", json={**body, "password": "nope"}, headers=headers)
    assert wrong.status_code == 400
    answer = client.post("/api/v1/account/second-factors/email", json={**body, "password": PASSWORD}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
