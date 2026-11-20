"""The mail catcher (mailcatcher/): staging and development instances send their e-mail to it, production keeps its
own; each instance sees only its own caught e-mails; reading, deleting and releasing are permission-gated, releasing
is audit-logged; retention and size limits apply. The SMTP server runs for real on 127.0.0.1; Docker never runs."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import smtplib
import threading
import time
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from sudoo import accounts, mail
from sudoo.config import live, settings
from sudoo.core import audit
from sudoo.instances import odoo, store
from sudoo.mailcatcher import HOST, PORT, mailbox, server
from sudoo.mailcatcher import panel as catcher

Client = tuple[FlaskClient, dict[str, str]]
NAMES = ("mc-dev", "mc-stage", "mc-prod")


def _instance(name: str, stage: str) -> None:
    folder = store.folder(name)
    (folder / "config").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": name.replace("-", "_"), "STAGE": stage, "GIT_BRANCH": "main", "AUTO_UPDATE": "false", "AUTO_UPGRADE": "false",
         "GATEWAY_PORT": 39400 + 2 * NAMES.index(name), "ODOO_PORT": 39401 + 2 * NAMES.index(name),
         "CREATED_AT": int(time.time()), "DELETE_AFTER_DAYS": "0", "AUTO_PAUSE": "false"},
    )  # fmt: skip
    (folder / "config" / "odoo.conf").write_text("[options]\ndb_host = db\n")


@pytest.fixture(autouse=True)
def instances(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # no Docker: the network and the instances' databases are recorded, not touched
    monkeypatch.setattr(catcher, "attach", lambda name: True)
    monkeypatch.setattr(catcher, "detach", lambda name: True)
    monkeypatch.setattr(catcher, "switch_off_mail_servers", lambda name: 0)
    _instance("mc-dev", "development")
    _instance("mc-stage", "staging")
    _instance("mc-prod", "production")
    yield
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
    shutil.rmtree(settings.mail_catcher_dir, ignore_errors=True)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture(scope="module")
def developer(app: Flask) -> Client:
    accounts.create("maildev", "developer", "harbor-quill-saffron-38")
    return sign_in(app, "maildev", "harbor-quill-saffron-38")


@pytest.fixture(scope="module")
def viewer(app: Flask) -> Client:
    accounts.create("mailviewer", "viewer", "meadow-tundra-ochre-55")
    return sign_in(app, "mailviewer", "meadow-tundra-ochre-55")


@pytest.fixture
def smtp() -> Iterator[int]:
    """The catcher's SMTP server on a free port of 127.0.0.1, with the current catcher.json."""
    catcher.write_settings()
    server.CONFIG.mtime = -1
    loop = asyncio.new_event_loop()
    listener = loop.run_until_complete(asyncio.start_server(server.handle, "127.0.0.1", 0, limit=server.MAX_LINE))
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield listener.sockets[0].getsockname()[1]
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    listener.close()
    loop.close()


def _message(subject: str = "Your invoice", html: bool = True, attachment: bytes | None = b"%PDF-1.4") -> EmailMessage:
    message = EmailMessage()
    message["From"] = "Acme <sales@acme.example>"
    message["To"] = "customer@example.com"
    message["Subject"] = subject
    message.set_content("Hello, your invoice is attached.")
    if html:
        message.add_alternative(
            '<p style="color:red">Hello <img src="https://tracker.example/pixel.gif"><script>alert(1)</script></p>',
            subtype="html",
        )
    if attachment is not None:
        message.add_attachment(attachment, maintype="application", subtype="pdf", filename="INV-001.pdf")
    return message


def _send(port: int, name: str, password: str, message: EmailMessage) -> None:
    with smtplib.SMTP("127.0.0.1", port, timeout=10) as client:
        client.login(name, password)
        client.send_message(message)


# ---------------------------------------------------------------- routing per stage


def test_staging_and_development_send_to_the_catcher_production_keeps_its_own() -> None:
    for name in ("mc-dev", "mc-stage"):
        options = catcher.conf_options(name)
        assert options["smtp_server"] == HOST and options["smtp_port"] == str(PORT)
        assert options["smtp_user"] == name and options["smtp_ssl"] == "False"
        assert options["smtp_password"] == store.read_env(name)[catcher.PASSWORD_KEY]
    assert catcher.conf_options("mc-prod") == dict.fromkeys(catcher.CONF_KEYS)
    assert catcher.PASSWORD_KEY not in store.read_env("mc-prod")


def test_odoo_conf_is_written_for_the_stage_and_cleaned_when_it_changes() -> None:
    assert catcher.write_conf("mc-dev")
    conf = odoo.read_config("mc-dev")
    assert conf.get("options", "smtp_server") == HOST and conf.get("options", "smtp_user") == "mc-dev"
    assert conf.get("options", "db_host") == "db"  # the rest of odoo.conf stays
    assert not catcher.write_conf("mc-dev")  # nothing to change the second time

    assert not catcher.write_conf("mc-prod")
    assert not odoo.read_config("mc-prod").has_option("options", "smtp_server")

    store.update_env("mc-dev", STAGE="production")
    assert catcher.write_conf("mc-dev")
    assert not any(odoo.read_config("mc-dev").has_option("options", key) for key in catcher.CONF_KEYS)


def test_a_production_instance_keeps_its_own_smtp_server_in_odoo_conf() -> None:
    path = store.config_file("mc-prod")
    path.write_text("[options]\nsmtp_server = smtp.acme.example\nsmtp_port = 587\n")
    assert not catcher.write_conf("mc-prod")
    assert odoo.read_config("mc-prod").get("options", "smtp_server") == "smtp.acme.example"


def test_the_catcher_settings_list_only_staging_and_development(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        catcher.conf_options(name)
    live.change({"mail_catcher_max_message_mb": 3, "mail_catcher_max_messages": 40})
    catcher.write_settings()
    written = json.loads((settings.mail_catcher_dir / "catcher.json").read_text())
    assert set(written["mailboxes"]) == {"mc-dev", "mc-stage"}
    assert all(len(value) == 64 and value != store.read_env("mc-dev")[catcher.PASSWORD_KEY]
               for value in written["mailboxes"].values())  # fmt: skip
    assert written["limits"]["max_message_bytes"] == 3 * 1024**2 and written["limits"]["max_messages"] == 40
    assert (settings.mail_catcher_dir / "catcher.json").stat().st_mode & 0o077 == 0
    assert not catcher.write_settings()  # unchanged: not written again


def test_stage_changes_move_the_instance_to_or_from_the_catcher(admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers = admin
    started: list[str] = []
    monkeypatch.setattr(catcher, "after_start", lambda name: started.append(name) or 0)
    live.change({"max_production_instances": 0})
    catcher.write_conf("mc-stage")
    answer = client.patch("/api/v1/instances/mc-stage", json={"stage": "production"}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    assert not odoo.read_config("mc-stage").has_option("options", "smtp_server")
    answer = client.patch(
        "/api/v1/instances/mc-stage", json={"stage": "staging", "confirm_production": True}, headers=headers
    )
    assert answer.status_code == 200, answer.get_json()
    assert odoo.read_config("mc-stage").get("options", "smtp_server") == HOST
    time.sleep(0.2)
    assert started == ["mc-stage", "mc-stage"]


def test_reconcile_connects_staging_and_development_and_disconnects_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done: list[tuple[str, str]] = []
    monkeypatch.setattr(catcher, "attach", lambda name: done.append(("connect", name)) or True)
    monkeypatch.setattr(catcher, "detach", lambda name: done.append(("disconnect", name)) or True)
    monkeypatch.setattr(
        catcher,
        "state",
        lambda: {"exists": True, "running": True,
                 "networks": ["sudoo-mail", "odoo-mc-prod_default", "odoo-mc-stage_default", "odoo-gone_default"]},
    )  # fmt: skip
    result = catcher.reconcile()
    assert ("connect", "mc-dev") in done and ("disconnect", "mc-prod") in done and ("disconnect", "gone") in done
    assert ("connect", "mc-stage") not in done and result["connected"] == ["mc-dev"]


def test_the_instances_own_mail_servers_are_switched_off_on_start_only_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()
    ran: list[list[str]] = []
    monkeypatch.setattr(catcher, "capture", lambda cmd, timeout=30: ran.append(cmd) or "2")
    assert catcher.switch_off_mail_servers("mc-prod") == 0 and not ran
    assert catcher.switch_off_mail_servers("mc-dev") == 2
    assert any("UPDATE ir_mail_server SET active = false" in " ".join(cmd) for cmd in ran)


# ---------------------------------------------------------------- the SMTP server


def test_mail_is_filed_in_the_mailbox_of_the_instance_that_authenticated(smtp: int) -> None:
    dev, stage = catcher.password("mc-dev"), catcher.password("mc-stage")
    catcher.write_settings()
    _send(smtp, "mc-dev", dev, _message("For dev"))
    _send(smtp, "mc-stage", stage, _message("For staging", attachment=None))
    assert [m["subject"] for m in mailbox.summaries("mc-dev")] == ["For dev"]
    assert [m["subject"] for m in mailbox.summaries("mc-stage")] == ["For staging"]
    found = next(mailbox.summaries("mc-dev"))
    assert found["rcpt_to"] == ["customer@example.com"] and found["mail_from"] == "sales@acme.example"
    assert found["attachments"] == 1 and found["has_html"]


def test_the_catcher_refuses_wrong_passwords_other_mailboxes_and_production(smtp: int) -> None:
    catcher.password("mc-dev")
    catcher.write_settings()
    with pytest.raises(smtplib.SMTPAuthenticationError):
        _send(smtp, "mc-dev", "wrong", _message())
    with pytest.raises(smtplib.SMTPAuthenticationError):  # one instance's password does not open another's box
        _send(smtp, "mc-stage", catcher.password("mc-dev"), _message())
    with pytest.raises(smtplib.SMTPAuthenticationError):  # production has no mailbox
        _send(smtp, "mc-prod", "anything", _message())
    with smtplib.SMTP("127.0.0.1", smtp, timeout=10) as client:  # no e-mail without authentication
        code, _ = client.docmd("MAIL FROM:<a@b.example>")
        assert code == 530
    assert not list(mailbox.summaries("mc-dev")) and not list(mailbox.summaries("mc-stage"))


def test_the_catcher_refuses_an_e_mail_beyond_the_size_limit(smtp: int) -> None:
    live.change({"mail_catcher_max_message_mb": 1})
    password = catcher.password("mc-dev")
    catcher.write_settings()
    server.CONFIG.mtime = -1
    with pytest.raises(smtplib.SMTPException):
        _send(smtp, "mc-dev", password, _message(attachment=b"x" * (2 * 1024**2)))
    assert not list(mailbox.summaries("mc-dev"))
    _send(smtp, "mc-dev", password, _message("small"))
    assert [m["subject"] for m in mailbox.summaries("mc-dev")] == ["small"]


def test_dot_stuffing_and_auth_login(smtp: int) -> None:
    password = catcher.password("mc-dev")
    catcher.write_settings()
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = "dots", "a@acme.example", "b@example.com"
    message.set_content("line one\n.leading dot\n..two dots\n")
    with smtplib.SMTP("127.0.0.1", smtp, timeout=10) as client:
        client.ehlo()
        client.user, client.password = "mc-dev", password
        code, _ = client.auth("LOGIN", client.auth_login)
        assert code == 235
        client.send_message(message)
    found = next(mailbox.summaries("mc-dev"))
    raw = (mailbox.raw("mc-dev", found["id"]) or b"").replace(b"\r\n", b"\n")
    assert b"\n.leading dot\n..two dots\n" in raw and b"\n..." not in raw


# ---------------------------------------------------------------- the API


def _caught(name: str, subject: str = "Your invoice", **kwargs: Any) -> str:
    raw = bytes(_message(subject, **kwargs))
    envelope = {"received_at": time.time(), "mail_from": "sales@acme.example", "rcpt_to": ["customer@example.com"]}
    return mailbox.deliver(name, raw, envelope, {"max_messages": 0, "max_bytes": 0})


def test_list_search_and_read_a_caught_e_mail(developer: Client) -> None:
    client, _ = developer
    first = _caught("mc-dev", "Quotation S00012")
    _caught("mc-dev", "Password reset")
    answer = client.get("/api/v1/instances/mc-dev/mail")
    assert answer.status_code == 200
    body = answer.get_json()
    assert [m["subject"] for m in body["data"]] == ["Password reset", "Quotation S00012"]
    assert body["meta"]["total"] == 2 and body["meta"]["usage"]["messages"] == 2 and "catcher" in body["meta"]
    found = client.get("/api/v1/instances/mc-dev/mail?q=quotation").get_json()["data"]
    assert [m["id"] for m in found] == [first]

    detail = client.get(f"/api/v1/instances/mc-dev/mail/{first}").get_json()["data"]
    assert detail["text"].startswith("Hello") and detail["has_html"]
    assert ["Subject", "Quotation S00012"] in detail["headers"]
    attachment = detail["attachment_list"][0]
    assert attachment["filename"] == "INV-001.pdf"
    download = client.get(f"/api/v1/instances/mc-dev/mail/{first}/attachments/{attachment['index']}")
    assert download.status_code == 200 and download.data == b"%PDF-1.4"
    assert download.mimetype == "application/octet-stream" and "attachment" in download.headers["Content-Disposition"]
    raw = client.get(f"/api/v1/instances/mc-dev/mail/{first}/raw")
    assert (
        raw.status_code == 200
        and b"Quotation S00012" in raw.data
        and "attachment" in raw.headers["Content-Disposition"]
    )


def test_the_html_is_served_sandboxed_without_scripts_or_remote_content(developer: Client) -> None:
    client, _ = developer
    found = _caught("mc-dev")
    answer = client.get(f"/api/v1/instances/mc-dev/mail/{found}/html")
    assert answer.status_code == 200 and answer.mimetype == "text/html"
    policy = answer.headers["Content-Security-Policy"]
    assert "default-src 'none'" in policy and "sandbox" in policy and "script-src" not in policy
    assert "img-src data:" in policy and "frame-ancestors 'self'" in policy
    assert answer.headers["X-Frame-Options"] == "SAMEORIGIN" and "X-Sudoo-Frame" not in answer.headers
    assert answer.headers["X-Content-Type-Options"] == "nosniff"


def test_inline_images_are_shown_from_the_message_itself() -> None:
    message = EmailMessage()
    message["Subject"] = "logo"
    message.set_content("text")
    message.add_alternative('<img src="cid:logo123">', subtype="html")
    message.get_payload()[1].add_related(b"\x89PNG", maintype="image", subtype="png", cid="<logo123>")
    found = mailbox.deliver("mc-dev", bytes(message), {"received_at": time.time()}, {})
    html = mailbox.html("mc-dev", found) or ""
    assert "data:image/png;base64," in html and "cid:" not in html


def test_each_instance_sees_only_its_own_mail(developer: Client) -> None:
    client, _ = developer
    found = _caught("mc-dev")
    for path in ("", "/html", "/raw", "/attachments/3"):
        assert client.get(f"/api/v1/instances/mc-stage/mail/{found}{path}").status_code == 404
    assert client.get("/api/v1/instances/mc-stage/mail").get_json()["data"] == []
    assert client.get("/api/v1/instances/mc-prod/mail").status_code == 409
    assert client.get("/api/v1/instances/mc-dev/mail/..%2F..%2Fmanager").status_code == 404


def test_who_may_read_delete_and_release(admin: Client, developer: Client, viewer: Client) -> None:
    found = _caught("mc-dev")
    viewer_client, viewer_headers = viewer
    assert viewer_client.get("/api/v1/instances/mc-dev/mail").status_code == 403
    client, headers = developer
    assert client.get("/api/v1/instances/mc-dev/mail").status_code == 200
    assert client.delete(f"/api/v1/instances/mc-dev/mail/{found}", headers=headers).status_code == 403
    answer = client.post(f"/api/v1/instances/mc-dev/mail/{found}/release", json={"to": "qa@acme.example"},
                         headers=headers)  # fmt: skip
    assert answer.status_code == 403
    admin_client, admin_headers = admin
    assert admin_client.delete(f"/api/v1/instances/mc-dev/mail/{found}", headers=admin_headers).status_code == 204
    assert mailbox.get("mc-dev", found) is None
    assert any(e["event"] == "caught e-mail deleted" for e in audit.latest(limit=20))
    _caught("mc-dev")
    _caught("mc-dev")
    assert admin_client.delete("/api/v1/instances/mc-dev/mail", headers=admin_headers).status_code == 204
    assert not list(mailbox.summaries("mc-dev"))


def test_release_sends_a_copy_from_the_panel_and_is_audit_logged(
    admin: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[EmailMessage] = []

    class FakeMailer:
        ready = True

        def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
            raise AssertionError("the composed message is sent as it is")

        def send_message(self, message: EmailMessage) -> None:
            sent.append(message)

    monkeypatch.setattr(mail, "mailer", lambda: FakeMailer())
    live.change({"mail_from": "Sudoo.sh <panel@example.org>"})
    found = _caught("mc-dev", "Order confirmed")
    client, headers = admin
    answer = client.post(f"/api/v1/instances/mc-dev/mail/{found}/release", json={"to": "qa@acme.example"},
                         headers=headers)  # fmt: skip
    assert answer.status_code == 200, answer.get_json()
    copy = sent[0]
    assert copy["To"] == "qa@acme.example" and "panel@example.org" in copy["From"]
    assert copy["Reply-To"] == "Acme <sales@acme.example>" and copy["Subject"] == "[mc-dev] Order confirmed"
    assert copy["X-Sudoo-Released-From"] == f"mc-dev {found}" and copy["Cc"] is None
    assert any(part.get_filename() == "INV-001.pdf" for part in copy.iter_attachments())
    entry = next(e for e in audit.latest(limit=20) if e["event"] == "caught e-mail released")
    assert entry["instance"] == "mc-dev" and "qa@acme.example" in entry["detail"]

    bad = client.post(f"/api/v1/instances/mc-dev/mail/{found}/release", json={"to": "not an address"}, headers=headers)
    assert bad.status_code == 400
    live.change({"mail_catcher_release": False})
    off = client.post(f"/api/v1/instances/mc-dev/mail/{found}/release", json={"to": "qa@acme.example"},
                      headers=headers)  # fmt: skip
    assert off.status_code == 403 and len(sent) == 1


def test_release_goes_only_to_the_typed_address_whatever_resent_headers_say(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.mail import smtp

    envelopes: list[tuple[str, list[str], EmailMessage]] = []

    class FakeSMTP:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def send_message(self, message: EmailMessage, from_addr: str | None = None,
                         to_addrs: list[str] | None = None) -> None:  # fmt: skip
            assert from_addr and to_addrs, "the envelope is always given, never taken from the headers"
            envelopes.append((from_addr, list(to_addrs), message))

    monkeypatch.setattr(smtp.smtplib, "SMTP", FakeSMTP)
    live.change({"mail_from": "Sudoo.sh <panel@example.org>", "smtp_host": "127.0.0.1", "smtp_security": "none",
                 "smtp_username": "", "mail_catcher_release": True})  # fmt: skip
    message = _message("Resent")
    message["Resent-Date"] = "Tue, 18 Nov 2026 09:00:00 +0000"
    message["Resent-From"] = "panel@example.org"
    message["Resent-To"] = "other1@outside.example"
    message["RESENT-CC"] = "other2@outside.example"
    message["Resent-Bcc"] = "other3@outside.example"
    found = mailbox.deliver("mc-dev", bytes(message), {"received_at": time.time()}, {})
    catcher.release("mc-dev", found, "admin@panel.example")
    assert len(envelopes) == 1
    sender, recipients, copy = envelopes[0]
    assert sender == "panel@example.org" and recipients == ["admin@panel.example"]
    assert not [key for key in copy if key.lower().startswith("resent-")]


# ---------------------------------------------------------------- retention and limits


def test_retention_and_limits() -> None:
    live.change({"mail_catcher_retention_days": 2, "mail_catcher_max_messages": 10})
    catcher.password("mc-dev")
    raw = bytes(_message())
    now = time.time()
    for days_ago in (5, 3, 1, 0):
        mailbox.deliver("mc-dev", raw, {"received_at": now - days_ago * 86400}, {})
    for _ in range(12):
        mailbox.deliver("mc-dev", raw, {"received_at": now}, {})
    removed = catcher.clean(now)
    assert removed["mc-dev"] == 6  # 2 past the retention, 4 beyond the 10 kept
    assert mailbox.usage("mc-dev")["messages"] == 10
    assert mailbox.prune("mc-dev", max_bytes=3 * len(raw)) == 7


def test_the_mailboxes_of_production_and_deleted_instances_are_removed() -> None:
    catcher.password("mc-dev")
    _caught("mc-dev")
    _caught("mc-prod")
    (settings.mail_catcher_dir / "boxes" / "gone").mkdir(parents=True)
    removed = catcher.clean()
    assert "mc-prod" in removed and "gone" in removed and "mc-dev" not in removed
    assert mailbox.boxes() == ["mc-dev"]


# ---------------------------------------------------------------- the help panel


def test_help_tells_the_version_and_where_support_is(developer: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.hub import marketplace

    client, _ = developer
    live.change({"support_url": "", "admin_contact_email": "it@acme.example"})
    data = client.get("/api/v1/help").get_json()["data"]
    assert data["version"] and data["docs_url"] == "https://docs.sudoo.sh"
    assert data["support"] == {"kind": "email", "url": "mailto:it@acme.example"}
    live.change({"support_url": "https://help.acme.example/sudoo"})
    assert client.get("/api/v1/help").get_json()["data"]["support"]["kind"] == "link"
    monkeypatch.setattr(marketplace, "state", lambda: {"linked": {"account_name": "Acme"}})
    live.change({"marketplace": True})
    assert client.get("/api/v1/help").get_json()["data"]["support"] == {
        "kind": "hub",
        "url": "https://app.sudoo.sh/support",
    }
    with pytest.raises(ValueError):
        live.change({"support_url": "javascript:alert(1)"})
    with pytest.raises(ValueError):
        live.change({"docs_url": "http://docs.example.com"})


def test_every_help_topic_links_to_a_page_and_anchor_that_exist(developer: Client) -> None:
    """web/src/lib/help-topics.ts: each page's documentation link (in the panel and on the documentation site)."""
    import re
    from pathlib import Path

    client, _ = developer
    source = (Path(__file__).resolve().parent.parent / "web/src/lib/help-topics.ts").read_text()
    links = re.findall(r'^\s+(\w+): \["([^"]+)", "([^"]+)"\],$', source, re.MULTILINE)
    topics = re.search(r"export type Topic =([^;]+);", source)
    assert topics and len(links) == len(re.findall(r'"(\w+)"', topics.group(1)))
    broken = []
    for topic, path, anchor in links:
        page = client.get(f"/api/v1/docs/{path}")
        if page.status_code != 200 or f'id="{anchor}"' not in page.get_json()["data"]["html"]:
            broken.append(f"{topic}: {path}#{anchor}")
    assert not broken


def test_a_link_or_a_fifo_the_catcher_plants_at_its_settings_is_replaced_never_followed(tmp_path: Any) -> None:
    path = settings.mail_catcher_dir / "catcher.json"
    catcher.write_settings()
    outside = tmp_path / "outside.json"
    outside.write_text(path.read_text())  # the very text the panel would write
    path.unlink()
    path.symlink_to(outside)
    assert catcher.write_settings()  # not read through the link: written again, the link replaced
    assert not path.is_symlink() and path.is_file()
    path.unlink()
    os.mkfifo(path)
    done = threading.Event()
    worker = threading.Thread(target=lambda: (catcher.write_settings(), done.set()), daemon=True)
    worker.start()
    assert done.wait(5), "write_settings waited on the FIFO"
    assert path.is_file() and not path.is_symlink()
