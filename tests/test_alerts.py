"""Alerts: what raises them, the quiet period, recovery notices, e-mail and webhooks (signing, formats, the rules
every request keeps), the settings that steer them, the bell's API and its permissions. No mail server is used (the
mailer is replaced) and webhooks go to a small HTTP server on 127.0.0.1."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from flask import Flask
from pydantic import ValidationError

from conftest import sign_in
from sudoo import alerts, mail
from sudoo.alerts import watch, webhooks
from sudoo.config import Settings, live
from sudoo.core import audit, jobs
from sudoo.core.state import STATE
from sudoo.instances import monitor, store
from sudoo.storage import storage

Client = tuple[Any, dict[str, str]]
NAME = "shop"


class FakeMailer:
    ready = True


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """No alert from before, a mailer that records what it would send, alerts e-mailed to one address only."""
    for key in list(storage().all(alerts.COLLECTION)):
        storage().delete(alerts.COLLECTION, key)
    storage().delete(alerts.META, "digest")
    alerts._RECORDS = None
    watch._SINCE.clear()
    watch._ACTIVE.clear()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(mail, "mailer", lambda: FakeMailer())
    monkeypatch.setattr(mail, "send", lambda to, template, **context: sent.append(
        {"to": to, "template": template, **context}))  # fmt: skip
    monkeypatch.setattr(alerts, "panel_url", lambda: "https://panel.example.com")
    # a password that is not the well-known \"admin\", so only the tests of the standing alert see it
    live.change(
        {"alert_super_admins": False, "alert_emails": "ops@example.com", "odoo_admin_password": "sudoo-test-marker"}
    )
    host = dict(monitor.HOST)
    yield sent
    alerts.drain()
    monitor.HOST.clear()
    monitor.HOST.update(host)
    live.change(dict.fromkeys(live.overrides()))
    STATE.remove(NAME)


def _failed_job(kind: str = "restart", action: str = "Restart", hint: str = "Odoo did not start.") -> jobs.Job:
    job = jobs.Job(NAME, action, kind, "ada")
    job.status, job.hint = "failed", hint
    job.steps = [{"title": "Stopping Odoo", "status": "done"}, {"title": "Starting Odoo", "status": "failed"}]
    return job


def _listed() -> list[dict[str, Any]]:
    alerts.drain()
    return alerts.recent()


# ---------------------------------------------------------------- what raises an alert


def test_failed_job_is_alerted_with_its_hint_and_link(clean: list[dict[str, Any]]) -> None:
    job = _failed_job()
    job.log = ["password=hunter2 https://user:token@github.com/x.git\n"]  # never copied into an alert
    watch.on_job_end(job)
    [alert] = _listed()
    assert alert["type"] == "job_failed" and alert["title"] == f"Restart failed on {NAME}"
    assert alert["text"] == "Odoo did not start." and "Failed at: Starting Odoo" in alert["details"]
    assert alert["path"] == f"/i/{NAME}/activity?job={job.id}"
    [email] = clean
    assert email["to"] == "ops@example.com" and email["template"] == "alert"
    assert email["link"] == f"https://panel.example.com/i/{NAME}/activity?job={job.id}"
    assert "hunter2" not in json.dumps(alert) + json.dumps(email, default=str)


def test_successful_and_panel_jobs_raise_nothing() -> None:
    ok = _failed_job()
    ok.status = "success"
    watch.on_job_end(ok)
    plugin = jobs.Job("plugin:x", "Install", "plugin-install", "ada")
    plugin.status = "failed"
    watch.on_job_end(plugin)
    assert _listed() == []


@pytest.mark.parametrize("rolled_back", [True, False])
def test_failed_update_says_whether_it_was_rolled_back(rolled_back: bool) -> None:
    job = _failed_job("update", "Get latest code")
    message = "The update to abc1234 failed at upgrading modules; the instance was put back to 9876543."
    STATE.update(
        NAME, update_failed={"target": "abc1234", "message": message, "job": job.id, "rolled_back": rolled_back}
    )
    watch.on_job_end(job)
    [alert] = _listed()
    assert alert["type"] == "update_failed" and alert["text"] == message
    assert ("rolled back" in alert["title"]) is rolled_back and ("rollback" in alert["title"]) is not rolled_back


def test_update_failing_before_any_change_is_a_job_failure() -> None:
    watch.on_job_end(_failed_job("update", "Get latest code", "GitHub could not be reached."))
    assert [a["type"] for a in _listed()] == ["job_failed"]


def test_failed_backup_has_its_own_alert() -> None:
    watch.on_job_end(_failed_job("backup", "Daily backup", "The server's disk is full."))
    [alert] = _listed()
    assert alert["type"] == "backup_failed" and alert["text"] == "The server's disk is full."


def test_a_kind_turned_off_is_neither_sent_nor_listed(clean: list[dict[str, Any]]) -> None:
    live.change({"alert_job_failed": False})
    watch.on_job_end(_failed_job())
    live.change({"alert_job_failed": True, "alerts": False})
    watch.on_job_end(_failed_job())
    assert _listed() == [] and clean == []


def _instances(
    monkeypatch: pytest.MonkeyPatch, status: dict[str, str], address: str | None = "https://x.example"
) -> None:
    monkeypatch.setattr(store, "names", lambda: list(status))
    monkeypatch.setattr(monitor, "status", lambda name: status[name])
    from sudoo import exposure

    monkeypatch.setattr(exposure, "address", lambda name: address)
    monitor.HOST.update(disk_total=100 * 2**30, disk_used=50 * 2**30, memory_total=8 * 2**30, memory_used=2**30)


def test_instance_down_after_its_minutes_then_recovered(
    monkeypatch: pytest.MonkeyPatch, clean: list[dict[str, Any]]
) -> None:
    status = {NAME: "starting"}
    _instances(monkeypatch, status)
    start = 1_000_000.0
    watch.check(start)
    watch.check(start + 4 * 60)
    assert _listed() == []  # not 5 minutes yet
    watch.check(start + 5 * 60)
    [down] = _listed()
    assert down["type"] == "instance_down" and down["title"] == f"{NAME} is down" and "does not answer" in down["text"]
    watch.check(start + 6 * 60)
    assert len(_listed()) == 1  # still down: not sent again
    status[NAME] = "online"
    watch.check(start + 7 * 60)
    recovered, problem = _listed()
    assert recovered["severity"] == "recovered" and recovered["title"] == f"{NAME} is back online"
    assert problem["resolved_at"] is not None
    assert [m["kind"] for m in clean] == ["Alert", "Resolved"]


def test_busy_waits_and_paused_closes_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    status = {NAME: "stopped"}
    _instances(monkeypatch, status)
    watch.check(0)
    status[NAME] = "busy"
    watch.check(400)  # a job runs: the countdown starts again afterwards
    status[NAME] = "stopped"
    watch.check(500)
    assert _listed() == []
    watch.check(500 + 300)
    assert [a["type"] for a in _listed()] == ["instance_down"]
    status[NAME] = "busy"
    watch.check(900)  # what was alerted stays open while a job runs
    status[NAME] = "paused"
    watch.check(1000)
    [alert] = _listed()
    assert alert["resolved_at"] == 1000 and alert["severity"] == "problem"  # closed, no "back online" notice


def test_disk_alert_and_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    _instances(monkeypatch, {})
    monitor.HOST.update(disk_total=100 * 2**30, disk_used=95 * 2**30)
    watch.check(0)
    watch.check(10)
    [alert] = _listed()
    assert alert["type"] == "disk" and alert["title"] == "Disk almost full: 5.0% free" and "5 GB free" in alert["text"]
    live.change({"alert_disk_free_percent": 4})  # the threshold is a setting
    watch.check(20)
    assert [a["severity"] for a in _listed()] == ["recovered", "problem"]


def test_memory_alert_needs_its_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    _instances(monkeypatch, {})
    live.change({"capacity_warning_percent": 80, "alert_memory_minutes": 2})
    monitor.HOST.update(memory_total=100, memory_used=90)
    watch.check(0)
    watch.check(60)
    assert _listed() == []
    watch.check(120)
    [alert] = _listed()
    assert alert["type"] == "memory" and "above 80%" in alert["text"]
    monitor.HOST.update(memory_used=50)
    watch.check(180)
    assert _listed()[0]["title"] == "Memory back to normal: 50% used"


def test_missing_public_link(monkeypatch: pytest.MonkeyPatch) -> None:
    _instances(monkeypatch, {NAME: "online"}, address=None)
    watch.check(0)
    watch.check(600)
    found = {a["key"] for a in _listed()}
    assert found == {"public_link:panel", f"public_link:{NAME}"}
    from sudoo import exposure

    monkeypatch.setattr(exposure, "address", lambda name: "https://new.example")
    watch.check(700)
    assert sum(a["severity"] == "recovered" for a in _listed()) == 2


def test_recovery_notice_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    live.change({"alert_recovery": False})
    _instances(monkeypatch, {})
    monitor.HOST.update(disk_total=100, disk_used=99)
    watch.check(0)
    monitor.HOST.update(disk_used=10)
    watch.check(10)
    [alert] = _listed()
    assert alert["resolved_at"] == 10 and alert["severity"] == "problem"


# ---------------------------------------------------------------- quiet period, digest


def test_same_alert_is_sent_once_per_quiet_period(clean: list[dict[str, Any]]) -> None:
    alert = alerts.Alert("job_failed", "Restart failed", "x", instance=NAME)
    first = alerts.send(alert, now=1000)
    alerts.send(alerts.Alert("job_failed", "Restart failed", "x", instance=NAME), now=1000 + 59 * 60)
    [listed] = _listed()
    assert first is not None and listed["id"] == first["id"] and listed["count"] == 2 and len(clean) == 1
    alerts.send(alerts.Alert("job_failed", "Restart failed", "x", instance=NAME), now=1000 + 61 * 60)
    assert len(_listed()) == 2 and len(clean) == 2
    alerts.send(alerts.Alert("job_failed", "Restart failed", "x", instance="other"), now=1000 + 62 * 60)
    alerts.drain()
    assert len(clean) == 3  # another instance is another alert


def test_quiet_zero_sends_every_time(clean: list[dict[str, Any]]) -> None:
    live.change({"alert_quiet_minutes": 0})
    for _ in range(3):
        alerts.send(alerts.Alert("backup_failed", "Backup failed", "x", instance=NAME))
    alerts.drain()
    assert len(clean) == 3


def test_daily_digest_instead_of_each_e_mail(clean: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    live.change({"alert_digest": True, "alert_digest_time": "08:00", "timezone": "UTC"})
    morning = 1_763_366_400.0  # 2025-11-17 08:00 UTC
    alerts.send(alerts.Alert("disk", "Disk almost full", "x"), now=morning - 3600)
    alerts.send(alerts.Alert("backup_failed", "Backup failed", "y", instance=NAME), now=morning - 1800)
    alerts.drain()
    assert clean == []  # nothing one by one
    assert alerts.send_digest(morning - 60) == 0  # the previous slot had nothing
    assert alerts.send_digest(morning + 60) == 2
    [summary] = clean
    listed = summary["alerts"].text
    assert summary["template"] == "alert_digest" and summary["count"] == "2 alerts"
    assert listed.index("Disk almost full") < listed.index("Backup failed")
    assert alerts.send_digest(morning + 120) == 0  # once per day


def test_alert_e_mails_render() -> None:
    from sudoo.mail.catalog import alerts_block
    from sudoo.mail.render import compose

    record = {"title": "shop is down", "text": "Instance shop is down.", "hint": "Restart it.",
              "details": ["Failed at: x"], "count": 3, "severity": "problem"}  # fmt: skip
    message = compose("alert", alerts._mail_values(record, None) | {"link": "https://p/i/shop"})
    assert message.subject == "Sudoo.sh Alert: shop is down"
    assert "Instance shop is down." in message.text and "Seen 3 times" in message.text
    assert "Failed at: x" in message.html and "https://p/i/shop" in message.html
    once = compose("alert", alerts._mail_values({**record, "count": 1, "hint": None}, None))
    assert "Seen" not in once.text and "Restart it" not in once.text  # lines with empty placeholders are left out
    items = [{**record, "link": "https://p/i/shop"}]
    message = compose("alert_digest", {"count": "1 alert", "alerts": alerts_block(items), "link": "https://p"})
    assert "shop is down (3 times)" in message.text and "https://p/i/shop" in message.html


def test_recipients_include_super_admins(passwords: dict[str, str]) -> None:
    live.change({"alert_super_admins": True, "alert_emails": "Ops@Example.com, ada@example.com"})
    found = alerts.recipients()
    assert found[:2] == ["ops@example.com", "ada@example.com"] and len(found) == len(set(found))
    live.change({"alert_super_admins": False})
    assert alerts.recipients() == ["ops@example.com", "ada@example.com"]


# ---------------------------------------------------------------- webhooks


class Receiver:
    """A webhook receiver on 127.0.0.1 that records requests and answers `status` (and `location` for 3xx)."""

    def __init__(self) -> None:
        self.requests: list[tuple[dict[str, str], bytes]] = []
        self.status = 200
        self.delay = 0.0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the http.server name
                data = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append((dict(self.headers), data))
                time.sleep(outer.delay)
                self.send_response(outer.status)
                if 300 <= outer.status < 400:
                    self.send_header("Location", f"http://127.0.0.1:{outer.port}/elsewhere")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def receiver() -> Iterator[Receiver]:
    found = Receiver()
    yield found
    found.server.shutdown()


RECORD = {"id": "abc", "type": "instance_down", "severity": "problem", "title": "shop is down",
          "text": "Instance shop is stopped.", "hint": None, "details": ["a"], "instance": "shop", "count": 1,
          "at": 1_763_366_400.0}  # fmt: skip


def test_json_webhook_is_signed(receiver: Receiver) -> None:
    status = webhooks.post(receiver.url, "s3cret", "json", RECORD, "https://p/i/shop", 5)
    [(headers, data)] = receiver.requests
    assert (
        status == 200 and headers["Content-Type"] == "application/json" and headers["X-Sudoo-Event"] == "instance_down"
    )
    expected = "sha256=" + hmac.new(b"s3cret", data, hashlib.sha256).hexdigest()
    assert headers["X-Sudoo-Signature"] == expected
    sent = json.loads(data)
    assert sent["event"] == "alert" and sent["title"] == "shop is down" and sent["link"] == "https://p/i/shop"
    assert sent["at"] == "2025-11-17T08:00:00+00:00" and sent["sent_at"]


def test_unsigned_without_a_secret(receiver: Receiver) -> None:
    webhooks.post(receiver.url, "", "json", RECORD, None, 5)
    assert "X-Sudoo-Signature" not in receiver.requests[0][0]


def test_chat_formats() -> None:
    slack = webhooks.payload(RECORD, "slack", "https://p/i/shop")
    assert slack["text"].startswith("*Alert: shop is down*") and "<https://p/i/shop|Open Sudoo.sh>" in slack["text"]
    discord = webhooks.payload({**RECORD, "text": "@everyone " + "x" * 5000}, "discord", "https://p")
    assert discord["allowed_mentions"] == {"parse": []} and len(discord["content"]) <= 2000
    teams = webhooks.payload(RECORD, "teams", "https://p/i/shop")
    card = teams["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard" and card["actions"][0]["url"] == "https://p/i/shop"
    assert webhooks.payload({**RECORD, "severity": "recovered"}, "slack", None)["text"].startswith("*Resolved:")


def test_chat_formats_show_job_output_as_written() -> None:
    printed = "<!here> see <https://x.invalid/a|the panel> and [docs](https://x.invalid/b) @everyone & co"
    record = {**RECORD, "title": "update of shop failed", "text": printed, "hint": printed, "details": [printed]}
    slack = webhooks.payload(record, "slack", "https://p/i/shop")["text"]
    assert "<!here>" not in slack and "<https://x.invalid" not in slack and "&lt;!here&gt;" in slack
    assert "&amp; co" in slack and slack.endswith("<https://p/i/shop|Open Sudoo.sh>")  # the panel's own link stays
    discord = webhooks.payload(record, "discord", "https://p/i/shop")["content"]
    assert "](" not in discord and "@everyone" not in discord and discord.endswith("\nhttps://p/i/shop")
    card = webhooks.payload(record, "teams", "https://p/i/shop")["attachments"][0]["content"]
    assert all("](" not in block["text"] and "@everyone" not in block["text"] for block in card["body"])


def test_body_size_is_limited() -> None:
    huge = {**RECORD, "details": ["y" * 300] * 20, "text": "z" * 2000, "hint": "h" * 2000}
    for form in webhooks.FORMATS:
        assert len(webhooks.body(huge, form, None)) <= webhooks.MAX_BODY


def test_redirects_are_not_followed(receiver: Receiver) -> None:
    receiver.status = 302
    with pytest.raises(webhooks.WebhookError, match="redirect"):
        webhooks.post(receiver.url, "", "json", RECORD, None, 5)
    assert len(receiver.requests) == 1  # /elsewhere was never asked


def test_errors_and_timeouts_never_name_the_address(receiver: Receiver) -> None:
    receiver.status = 500
    with pytest.raises(webhooks.WebhookError) as error:
        webhooks.post(receiver.url + "?token=abc", "", "json", RECORD, None, 5)
    assert str(error.value) == "answered HTTP 500"
    receiver.status, receiver.delay = 200, 2
    with pytest.raises(webhooks.WebhookError, match="could not be reached") as error:
        webhooks.post(receiver.url, "", "json", RECORD, None, 0.5)
    assert "127.0.0.1" not in str(error.value)


@pytest.mark.parametrize(
    "url", ["http://hooks.example.com/x", "ftp://example.com/x", "https://user:pw@example.com/x", "notaurl"]
)
def test_only_https_addresses(url: str) -> None:
    with pytest.raises(webhooks.WebhookError):
        webhooks.check_url(url)
    with pytest.raises(ValidationError):
        Settings(alert_webhook_1_url=url)


def test_failed_delivery_is_noted_and_audited_without_the_address(receiver: Receiver) -> None:
    receiver.status = 404
    live.change({"alert_webhook_2_url": receiver.url + "/T0KEN", "alert_webhook_2_format": "slack"})
    alerts.send(alerts.Alert("disk", "Disk almost full", "x"))
    [alert] = _listed()
    assert "webhook 2: answered HTTP 404" in alert["delivery"]
    entry = audit.latest(5)[0]
    assert entry["event"] == "alert webhook failed" and "T0KEN" not in json.dumps(entry)


# ---------------------------------------------------------------- settings


@pytest.mark.parametrize(
    ("values", "ok"),
    [
        ({"alert_emails": "a@example.com, b@example.org;c@example.net"}, True),
        ({"alert_emails": "not-an-address"}, False),
        ({"alert_instance_down_minutes": 0}, False),
        ({"alert_disk_free_percent": 95}, False),
        ({"alert_quiet_minutes": -1}, False),
        ({"alert_digest_time": "25:00"}, False),
        ({"alert_webhook_1_format": "telegram"}, False),
        ({"alert_webhook_1_url": "http://localhost:9000/hook"}, True),
        ({"alert_webhook_timeout_seconds": 600}, False),
    ],
)
def test_settings_are_checked(values: dict[str, Any], ok: bool) -> None:
    if ok:
        Settings(**values)
    else:
        with pytest.raises(ValidationError):
            Settings(**values)


def test_every_alert_setting_is_editable_and_webhooks_are_secret() -> None:
    from sudoo import config

    assert set(config.ALERT_SETTINGS) <= set(config.EDITABLE)
    assert {"alert_webhook_1_url", "alert_webhook_3_secret"} <= set(config.SECRET)
    assert Settings(alert_emails="A@x.io,a@x.io , b@x.io").alert_emails == "A@x.io,a@x.io,b@x.io"


def test_webhook_settings_never_shown_or_audited(admin: Client) -> None:
    client, headers = admin
    url = "https://hooks.slack.com/services/T0/B0/SECRETPART"
    answer = client.patch("/api/v1/settings", json={"alert_webhook_1_url": url, "alert_webhook_1_secret": "k3y"},
                          headers=headers)  # fmt: skip
    assert answer.status_code == 200, answer.get_json()
    shown = {s["key"]: s for s in answer.get_json()["data"]}
    assert shown["alert_webhook_1_url"]["value"] is True and shown["alert_webhook_1_url"]["secret"]
    assert "SECRETPART" not in answer.get_data(as_text=True)
    assert "SECRETPART" not in json.dumps(audit.latest(20)) and "k3y" not in json.dumps(audit.latest(20))
    refused = client.patch("/api/v1/settings", json={"alert_webhook_1_url": "http://example.com"}, headers=headers)
    assert refused.status_code == 400 and "ALERT_WEBHOOK_1_URL" in refused.get_json()["detail"]


# ---------------------------------------------------------------- the API


def test_bell_lists_alerts(admin: Client) -> None:
    alerts.send(alerts.Alert("disk", "Disk almost full", "x"))
    client, _ = admin
    answer = client.get("/api/v1/alerts?limit=10")
    assert answer.status_code == 200
    [listed] = answer.get_json()["data"]
    assert listed["title"] == "Disk almost full" and listed["link"] == listed["path"] and "emailed" not in listed
    assert client.get("/api/v1/alerts?limit=0").status_code == 400


# -------------------------------------------- the standing alert while the Odoo admin password is \"admin\"


def _default_password_alert(answer: Any) -> dict[str, Any] | None:
    assert answer.status_code == 200, answer.get_json()
    return next((a for a in answer.get_json()["data"] if a["type"] == "default_admin_password"), None)


def test_default_password_alert_while_password_is_admin(admin: Client) -> None:
    client, _ = admin
    live.change({"odoo_admin_password": "admin"})
    listed = _default_password_alert(client.get("/api/v1/alerts?limit=10"))
    assert listed is not None and listed["severity"] == "problem" and listed["resolved_at"] is None
    assert listed["link"] == "/settings?tab=instances&focus=odoo_admin_password"  # a super admin may change it
    assert listed["instance"] is None and listed["count"] == 1


def test_default_password_alert_gone_for_another_value(admin: Client) -> None:
    client, _ = admin
    assert _default_password_alert(client.get("/api/v1/alerts")) is None  # the fixture's password is not "admin"
    live.change({"odoo_admin_password": "admin"})
    assert _default_password_alert(client.get("/api/v1/alerts")) is not None
    live.change({"odoo_admin_password": "sudoo-test-marker"})
    answer = client.get("/api/v1/alerts")
    assert _default_password_alert(answer) is None
    assert "sudoo-test-marker" not in answer.get_data(as_text=True)  # the password itself is never sent


def test_default_password_alert_links_only_for_super_admins(app: Flask, admin: Client) -> None:
    from sudoo import accounts

    accounts.create("t6admin", "admin", "rusty-falcon-paper-63")
    client, _ = sign_in(app, "t6admin", "rusty-falcon-paper-63")
    live.change({"odoo_admin_password": "admin"})
    listed = _default_password_alert(client.get("/api/v1/alerts"))
    assert listed is not None and listed["link"] is None  # an admin cannot change the panel's settings


def test_default_password_alert_not_for_other_roles(app: Flask) -> None:
    from sudoo import accounts
    from sudoo.web.v1.alerts import default_password_alert

    accounts.create("t6viewer", "viewer", "silent-harbor-comet-29")
    client, _ = sign_in(app, "t6viewer", "silent-harbor-comet-29")
    live.change({"odoo_admin_password": "admin"})
    assert client.get("/api/v1/alerts").status_code == 403  # a viewer may not read the alerts at all
    assert default_password_alert("maintainer", manage_settings=False) is None
    assert default_password_alert("developer", manage_settings=False) is None
    assert default_password_alert("super_admin", manage_settings=True) is not None


def test_test_alert_by_e_mail(admin: Client, clean: list[dict[str, Any]]) -> None:
    client, headers = admin
    answer = client.post("/api/v1/alerts/test", json={"channel": "email"}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["data"]["sent_to"] == ["ops@example.com"]
    [sent] = [m for m in clean if m["template"] == "alert"]  # signing in also sent the sign-in e-mail
    assert sent["alert_title"] == "Test alert" and sent["kind"] == "Notice"
    assert audit.latest(1)[0]["event"] == "test alert sent"
    live.change({"alert_emails": ""})
    nobody = client.post("/api/v1/alerts/test", json={"channel": "email"}, headers=headers)
    assert nobody.status_code == 400 and "Nobody" in nobody.get_json()["detail"]


def test_test_alert_to_a_webhook(admin: Client, receiver: Receiver) -> None:
    client, headers = admin
    missing = client.post("/api/v1/alerts/test", json={"channel": "webhook", "webhook": 3}, headers=headers)
    assert missing.status_code == 400
    live.change({"alert_webhook_3_url": receiver.url, "alert_webhook_3_secret": "k"})
    answer = client.post("/api/v1/alerts/test", json={"channel": "webhook", "webhook": 3}, headers=headers)
    assert answer.status_code == 200 and answer.get_json()["data"]["status"] == 200
    assert json.loads(receiver.requests[0][1])["type"] == "test"
    receiver.status = 500
    failed = client.post("/api/v1/alerts/test", json={"channel": "webhook", "webhook": 3}, headers=headers)
    assert failed.status_code == 502 and failed.get_json()["detail"] == "Webhook 3: answered HTTP 500"
    for wrong in ({"channel": "sms"}, {"channel": "webhook", "webhook": 4}, {"channel": "webhook", "webhook": True}):
        assert client.post("/api/v1/alerts/test", json=wrong, headers=headers).status_code == 400


def test_permissions(operator: Client) -> None:
    client, headers = operator  # a developer: neither audit.view nor settings.manage
    assert client.get("/api/v1/alerts").status_code == 403
    assert client.post("/api/v1/alerts/test", json={"channel": "email"}, headers=headers).status_code == 403


def test_live_alert_topic_only_for_audit_readers() -> None:
    from sudoo.web.v1 import events

    class Asked:
        def __init__(self, topics: str = "") -> None:
            self.query = {"topics": topics} if topics else {}

    admin = {"name": "a", "role": "admin"}
    developer = {"name": "d", "role": "developer"}
    assert events._topics(Asked(), admin) is None  # type: ignore[arg-type]
    everything = events._topics(Asked(), developer)  # type: ignore[arg-type]
    assert everything is not None and "alert" not in everything and "job" in everything
    assert events._topics(Asked("alert,job"), developer) == frozenset({"job"})  # type: ignore[arg-type]
    assert events._topics(Asked("alert"), admin) == frozenset({"alert"})  # type: ignore[arg-type]


def test_new_alerts_are_published_live() -> None:
    from sudoo.core.events import BUS

    before = BUS.last_id
    alerts.send(alerts.Alert("disk", "Disk almost full", "x"))
    published = BUS.missed(before, BUS.last_id, frozenset({"alert"})) or []
    assert published and published[-1].data["title"] == "Disk almost full"
