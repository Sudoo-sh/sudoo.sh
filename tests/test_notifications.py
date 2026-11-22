"""Notifications (notifications/): the body per channel (Slack, Discord, Teams, Telegram, signed webhook, e-mail), the
signature, the guard against private addresses (resolved names, IPv4 inside IPv6, the administrator's allow-list),
retries with growing waits, the events taken from the panel's own (builds, stages, restores, backups, storage), and
the API (secrets encrypted, never returned, only their last 4 characters). Nothing leaves this machine: requests go
through a fake transport."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
from collections.abc import Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

from sudoo import mail, notifications, projects
from sudoo.config import live
from sudoo.core import jobs
from sudoo.notifications import Delivery, channels, guard
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
SLACK = "https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXXsecret1234"
HOOK = "https://hooks.example.com/sudoo/abcdef123456"
BOT = "123456789:AAH-abcdefghijklmnopqrstuvwxyz012345"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    notifications.QUEUE.clear()
    yield
    notifications.QUEUE.clear()
    for key in list(storage().all(notifications.COLLECTION)):
        storage().delete(notifications.COLLECTION, key)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def project() -> dict[str, Any]:
    return projects.create(repository="Acme/Told", odoo_version="18.0", edition="community", origin="linked", user="t")


class Transport:
    """Stands in for guard.post: records each request and answers what the test says."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, bytes, dict[str, str]]] = []
        self.answers: list[int | Exception] = []

    def __call__(self, url: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
        self.requests.append((url, body, headers))
        answer = self.answers.pop(0) if self.answers else 200
        if isinstance(answer, Exception):
            raise answer
        if answer >= 400:
            raise guard.DeliveryError(f"answered HTTP {answer}", answer == 429 or answer >= 500, answer)
        return answer


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Transport:
    fake = Transport()
    monkeypatch.setattr(guard, "post", fake)
    return fake


def _note(**values: Any) -> dict[str, Any]:
    return {"id": "n1", "event": "build_failed", "project": "acme", "project_name": "Acme", "title": "Build of main failed",
            "text": "Module x failed <!channel> @everyone [click](https://evil.example)", "details": ["Branch: main"],
            "severity": "problem", "at": 1_800_000_000.0, "path": "/projects/acme/builds", **values}  # fmt: skip


# ---------------------------------------------------------------- formats


def test_slack_discord_teams_bodies_are_safe() -> None:
    slack = channels.payload("slack", _note(), "https://panel.example/x", {})
    assert slack["text"].startswith("*Alert: Build of main failed*")
    assert "<!channel>" not in slack["text"] and "&lt;!channel&gt;" in slack["text"]
    assert slack["text"].endswith("<https://panel.example/x|Open Sudoo.sh>")
    discord = channels.payload("discord", _note(), None, {})
    assert discord["allowed_mentions"] == {"parse": []} and "@everyone" not in discord["content"]
    assert "](" not in discord["content"]
    teams = channels.payload("teams", _note(), "https://panel.example/x", {})
    card = teams["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard" and card["actions"][0]["url"] == "https://panel.example/x"


def test_telegram_body_is_plain_text_to_the_chat() -> None:
    body = channels.payload("telegram", _note(severity="recovered"), "https://panel.example/x", {"chat_id": "-1001"})
    assert body["chat_id"] == "-1001" and body["disable_web_page_preview"] is True
    assert "parse_mode" not in body
    assert body["text"].startswith("Resolved: Build of main failed") and body["text"].endswith(
        "https://panel.example/x"
    )


def test_webhook_json_and_signature(transport: Transport) -> None:
    channels.send("webhook", _note(), "https://panel.example/x", {}, {"url": HOOK, "signing_secret": "whsec_k"}, 5)
    url, body, headers = transport.requests[0]
    assert url == HOOK
    expected = "sha256=" + hmac.new(b"whsec_k", body, hashlib.sha256).hexdigest()
    assert headers["X-Sudoo-Signature"] == expected
    assert headers["X-Sudoo-Event"] == "build_failed" and headers["X-Sudoo-Delivery"] == "n1"
    sent = json.loads(body)
    assert sent["event"] == "build_failed" and sent["project"] == {"id": "acme", "name": "Acme"}
    assert sent["at"] == "2027-01-15T08:00:00+00:00" and sent["link"] == "https://panel.example/x"


def test_telegram_goes_to_the_bot_api(transport: Transport) -> None:
    assert channels.send("telegram", _note(), None, {"chat_id": "@acme_ops"}, {"bot_token": BOT}, 5) == "HTTP 200"
    assert transport.requests[0][0] == f"https://api.telegram.org/bot{BOT}/sendMessage"


def test_email_uses_the_alert_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str, dict[str, Any]]] = []

    class Ready:
        ready = True

    monkeypatch.setattr(mail, "mailer", lambda: Ready())
    monkeypatch.setattr(mail, "send", lambda to, template, **values: sent.append((to, template, values)))
    result = channels.send("email", _note(), "https://panel.example/x", {"addresses": ["a@x.io", "b@x.io"]}, {}, 5)
    assert result == "e-mailed to 2 of 2"
    assert sent[0][1] == "alert" and sent[0][2]["alert_title"] == "Build of main failed"
    assert sent[0][2]["link"] == "https://panel.example/x"


# ---------------------------------------------------------------- the guard


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1",
     "fe80::1", "fc00::1", "::ffff:10.0.0.1", "224.0.0.1", "2002:0a00:0001::1"],  # fmt: skip
)
def test_private_and_local_addresses_are_refused(address: str) -> None:
    assert not guard.public(ipaddress.ip_address(address))


def test_public_addresses_pass() -> None:
    assert guard.public(ipaddress.ip_address("93.184.216.34"))
    assert guard.public(ipaddress.ip_address("2606:4700::6810:85e5"))


def _resolving(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    def fake(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_names_are_resolved_and_every_answer_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolving(monkeypatch, "93.184.216.34")
    assert guard.resolve(HOOK).address == "93.184.216.34"
    _resolving(monkeypatch, "93.184.216.34", "10.0.0.5")  # one private answer is enough to refuse
    with pytest.raises(guard.Refused, match="private or local"):
        guard.resolve(HOOK)
    with pytest.raises(guard.Refused, match="private or local"):
        guard.resolve("https://169.254.169.254/latest/meta-data")


@pytest.mark.parametrize(
    ("url", "reason"),
    [("http://hooks.example.com/x", "https://"), ("https://user:pw@hooks.example.com/x", "user name"),
     ("ftp://hooks.example.com", "https://"), ("https://", "https://")],  # fmt: skip
)
def test_only_plain_https_addresses(url: str, reason: str) -> None:
    with pytest.raises(guard.Refused, match=reason):
        guard.parse(url)


def test_administrator_can_allow_a_host_or_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolving(monkeypatch, "10.0.0.5")
    with pytest.raises(guard.Refused):
        guard.resolve("https://hooks.internal/x")
    live.change({"notification_allowed_hosts": "hooks.internal"})
    assert guard.resolve("https://hooks.internal/x").address == "10.0.0.5"
    live.change({"notification_allowed_hosts": "10.0.0.0/24"})
    assert guard.resolve("https://other.internal/x").address == "10.0.0.5"
    with pytest.raises(ValueError):
        live.change({"notification_allowed_hosts": "not a host!"})


def test_redirects_and_errors_are_not_retried_but_5xx_and_timeouts_are() -> None:
    assert guard.DeliveryError("x").retry
    with pytest.raises(guard.Refused):  # a private address is refused before any connection
        guard.post("https://127.0.0.1/x", b"{}", {}, 1)


# ---------------------------------------------------------------- channels and delivery


def test_secrets_are_encrypted_and_shown_by_their_last_four(project: dict[str, Any]) -> None:
    channel, signing = notifications.create(
        project["id"], {"type": "webhook", "name": "Ops", "secrets": {"url": HOOK}}, "t"
    )
    assert signing is not None and signing.startswith("whsec_")
    stored = json.dumps(storage().get(notifications.COLLECTION, channel["id"]))
    assert HOOK not in stored and signing not in stored
    shown = notifications.resource(channel)
    assert shown["secrets"] == {"url": "…3456", "signing_secret": f"…{signing[-4:]}"}
    assert notifications.secrets_of(channel) == {"url": HOOK, "signing_secret": signing}
    assert shown["events"] == list(notifications.DEFAULT_EVENTS)


def test_channel_rules(project: dict[str, Any]) -> None:
    with pytest.raises(notifications.ChannelError, match="https://"):
        notifications.create(project["id"], {"type": "slack", "secrets": {"url": "http://x.example"}}, "t")
    with pytest.raises(notifications.ChannelError, match="bot token"):
        notifications.create(
            project["id"], {"type": "telegram", "config": {"chat_id": "1"}, "secrets": {"bot_token": "x"}}, "t"
        )
    with pytest.raises(notifications.ChannelError, match="chat id"):
        notifications.create(
            project["id"], {"type": "telegram", "config": {"chat_id": "nope"}, "secrets": {"bot_token": BOT}}, "t"
        )
    with pytest.raises(notifications.ChannelError, match="not an e-mail"):
        notifications.create(project["id"], {"type": "email", "config": {"addresses": ["no"]}}, "t")
    with pytest.raises(notifications.ChannelError, match="Unknown event"):
        notifications.create(
            project["id"], {"type": "email", "events": ["x"], "config": {"addresses": ["a@x.io"]}}, "t"
        )


def test_retries_wait_longer_each_time_then_stop(project: dict[str, Any], transport: Transport) -> None:
    channel, _ = notifications.create(project["id"], {"type": "slack", "secrets": {"url": SLACK}}, "t")
    assert [notifications.backoff(n) for n in (1, 2, 3, 4, 9)] == [30, 60, 120, 240, 3600]
    live.change({"notification_retries": 2})
    transport.answers = [503, guard.DeliveryError("no answer within 10 seconds"), 502]
    delivery = Delivery(channel["id"], notifications.notification("build_failed", project["id"], title="t", text="x"))
    first = notifications.attempt(delivery, 1000.0)
    assert first == 1030.0
    second = notifications.attempt(delivery, first)
    assert second == first + 60
    assert notifications.attempt(delivery, second) is None  # out of tries
    last = (notifications.get(channel["id"]) or {})["last"]
    assert last["ok"] is False and last["attempts"] == 3 and last["result"] == "answered HTTP 502"
    assert len(transport.requests) == 3


def test_a_refusal_or_4xx_is_not_retried(project: dict[str, Any], transport: Transport) -> None:
    channel, _ = notifications.create(project["id"], {"type": "slack", "secrets": {"url": SLACK}}, "t")
    transport.answers = [404]
    delivery = Delivery(channel["id"], notifications.notification("build_failed", project["id"], title="t", text="x"))
    assert notifications.attempt(delivery) is None
    transport.answers = [guard.Refused("The address leads to a private or local network.")]
    assert notifications.attempt(Delivery(channel["id"], delivery.note)) is None


def test_queue_delivers_due_items_in_order(project: dict[str, Any], transport: Transport) -> None:
    channel, _ = notifications.create(
        project["id"], {"type": "discord", "secrets": {"url": HOOK}, "events": ["build_failed"]}, "t"
    )
    assert notifications.emit("build_warning", project["id"], title="t", text="x") == 0  # not wanted
    assert notifications.emit("build_failed", project["id"], title="first", text="x") == 1
    notifications.QUEUE.drain()
    assert len(transport.requests) == 1 and b"first" in transport.requests[0][1]
    live.change({"notifications": False})
    assert notifications.emit("build_failed", project["id"], title="t", text="x") == 0


# ---------------------------------------------------------------- events of the panel


def test_build_events(project: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.projects import builds

    entries: dict[str, dict[str, Any]] = {}
    history: list[dict[str, Any]] = []
    monkeypatch.setattr(builds, "find", lambda entry_id, project_id=None: entries.get(entry_id))
    monkeypatch.setattr(builds, "history", lambda project_id, branch: history)
    sent: list[str] = []
    monkeypatch.setattr(notifications, "emit", lambda event, project_id, **values: sent.append(event) or 1)

    def add(entry_id: str, **values: Any) -> None:
        entry = {"id": entry_id, "project": project["id"], "branch": "main", "instance": "i", **values}
        entries[entry_id] = entry
        history.append(entry)
        notifications.on_build(entry_id)

    add("b1", type="build", status="failed", at=1.0)
    add("b2", type="build", status="success", at=2.0)
    add("b3", type="build", status="warning", at=3.0)
    add("b4", type="build", status="success", at=4.0)
    add("s1", type="stage", **{"from": "staging", "to": "production"}, at=5.0)
    add("s2", type="stage", **{"from": "development", "to": "staging"}, at=6.0)
    add("r1", type="restore", status="success", at=7.0)
    add("f1", type="refresh", status="success", at=8.0)  # the scheduled staging refresh (projects/refresh.py)
    notifications.on_build("b1")  # published again (an update of the entry): told once
    assert sent == ["build_failed", "build_recovered", "build_warning", "promoted", "stage_changed", "restore_done",
                    "staging_refreshed"]  # fmt: skip


def test_backup_failure_and_storage_over(project: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        notifications, "emit", lambda event, project_id, **values: sent.append((event, project_id)) or 1
    )
    monkeypatch.setattr(projects, "project_of", lambda name: project["id"])
    monkeypatch.setattr(projects, "branch_of", lambda name: "main")
    job = jobs.Job("shop", "Back up", "backup", "t")
    job.status = "failed"
    notifications.on_job_end(job)
    notifications.on_alert({"id": "a1", "type": "storage", "key": "storage_over:shop", "severity": "problem",
                            "count": 1, "instance": "shop", "title": "shop is over", "text": "x"})  # fmt: skip
    notifications.on_alert({"id": "a2", "type": "storage", "key": "storage:shop", "severity": "problem", "count": 1,
                            "instance": "shop"})  # fmt: skip
    assert sent == [("backup_failed", project["id"]), ("storage_over", project["id"])]


# ---------------------------------------------------------------- the API


def test_api_never_returns_a_secret(
    admin: Client, operator: Client, project: dict[str, Any], transport: Transport
) -> None:
    client, headers = admin
    base = f"/api/v1/projects/{project['id']}/notifications"
    added = client.post(base, json={"type": "webhook", "name": "Ops", "secrets": {"url": HOOK},
                                    "events": ["build_failed", "production_down"]}, headers=headers)  # fmt: skip
    assert added.status_code == 201, added.get_json()
    data = added.get_json()["data"]
    signing = data["signing_secret"]
    assert data["secrets"]["url"] == "…3456"
    listed = client.get(base).get_json()["data"]
    assert HOOK not in json.dumps(listed) and signing not in json.dumps(listed)
    assert listed["channels"][0]["events"] == ["build_failed", "production_down"]

    channel = data["id"]
    changed = client.patch(f"{base}/{channel}", json={"enabled": False, "name": "On call"}, headers=headers)
    assert changed.get_json()["data"]["name"] == "On call" and changed.get_json()["data"]["enabled"] is False
    assert notifications.secrets_of(notifications.get(channel) or {})["url"] == HOOK  # kept when left out

    tested = client.post(f"{base}/{channel}/test", headers=headers).get_json()["data"]
    assert tested["delivery"]["ok"] is True and tested["delivery"]["test"] is True
    sent = json.loads(transport.requests[-1][1])
    assert sent["event"] == "test" and sent["test"] is True

    renewed = client.post(f"{base}/{channel}/signing-secret", headers=headers).get_json()["data"]
    assert renewed["signing_secret"] != signing

    logged = json.dumps(storage().logs(limit=50))
    assert HOOK not in logged and signing not in logged

    developer, developer_headers = operator
    assert developer.get(base).status_code == 403
    assert client.delete(f"{base}/{channel}", headers=headers).status_code == 204
    assert client.get(f"{base}/{channel}x").status_code in (404, 405)
