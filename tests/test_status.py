"""Project status (status/): recording checks per minute and per hour, uptime math (the server's missing minutes count
as down), incidents, pruning, the backups' schedule, the instance check through the gateway, the Status tab's API
and the public page with its token. Docker never runs; the gateway is a small HTTP server on 127.0.0.1."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from flask.testing import FlaskClient

from sudoo import projects, status
from sudoo.config import live
from sudoo.instances import backups, monitor, store
from sudoo.status import DAY, DEGRADED, DOWN, HOUR, SERVER, UP, Result, checks, pages
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
T0 = 1_800_000_000.0 // DAY * DAY  # a UTC midnight


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    for collection in (status.MINUTES, status.HOURS, status.INCIDENTS, status.META, status.PAGES):
        for key in list(storage().all(collection)):
            storage().delete(collection, key)
    yield
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    live.change(dict.fromkeys(live.overrides()))


# ---------------------------------------------------------------- recording and uptime


def test_minutes_and_hours_add_up() -> None:
    target = "instance:a"
    for minute in range(60):
        state = DOWN if minute in (10, 11) else UP
        status.record(target, Result(state, "", 100.0 + minute), T0 + minute * 60 + 5)
    hour = storage().get(status.HOURS, f"{target}|{int(T0)}")
    assert hour is not None
    minutes, up, ms_sum, ms_count, degraded = hour["h"]["0"]
    assert (minutes, up, ms_count, degraded) == (60, 58, 60, 0)
    assert ms_sum == pytest.approx(sum(100.0 + m for m in range(60)))
    assert status.uptime(target, DAY, T0 + 3600) == pytest.approx(96.666, abs=0.001)


def test_a_minute_with_one_failure_is_down_and_counts_once() -> None:
    target = "instance:b"
    status.record(target, Result(UP, "", 50.0), T0 + 1)
    status.record(target, Result(DOWN, "HTTP 502"), T0 + 20)
    status.record(target, Result(UP, "", 50.0), T0 + 40)
    hour = storage().get(status.HOURS, f"{target}|{int(T0)}")
    assert hour is not None and hour["h"]["0"][:2] == [1, 0]
    assert status.uptime(target, DAY, T0 + 60) == 0.0


def test_degraded_counts_as_up_but_is_noted() -> None:
    target = "instance:c"
    status.record(target, Result(DEGRADED, "Slow answer", 5000.0), T0 + 1)
    hour = storage().get(status.HOURS, f"{target}|{int(T0)}")
    assert hour is not None and hour["h"]["0"][4] == 1
    assert status.uptime(target, DAY, T0 + 60) == 100.0


def test_uptime_is_rounded_down_and_none_without_checks() -> None:
    assert status.percent(99_999, 100_000) == 99.999
    assert status.percent(999_999, 1_000_000) == 99.999  # never shown as 100 % while a minute was down
    assert status.percent(0, 0) is None
    assert status.uptime("instance:never", DAY, T0) is None


def test_server_minutes_without_a_check_count_as_down() -> None:
    status.record(SERVER, Result(UP), T0)
    status.record(SERVER, Result(UP), T0 + 60)
    # the panel was not running for the next 8 minutes
    assert status.uptime(SERVER, DAY, T0 + 600) == pytest.approx(20.0)
    # an instance's missing minutes are unknown, not down
    status.record("instance:d", Result(UP), T0)
    assert status.uptime("instance:d", DAY, T0 + 600) == 100.0


def test_week_and_month_come_from_the_hours() -> None:
    target = "instance:e"
    for day in range(10):
        for minute in range(0, 60, 10):  # a few checks each day at midnight
            state = DOWN if day == 3 else UP
            status.record(target, Result(state, "", 200.0), T0 + day * DAY + minute * 60)
    now = T0 + 9 * DAY + HOUR
    assert status.uptime(target, 30 * DAY, now) == pytest.approx(90.0)
    assert status.uptime(target, 7 * DAY, now) == pytest.approx(85.714)  # days 3 to 9
    found = status.days(target, 10, now)
    assert [d["uptime"] for d in found] == [100.0, 100.0, 100.0, 0.0, *[100.0] * 6]
    assert found[0]["avg_ms"] == 200


def test_incidents_open_worsen_and_close() -> None:
    target = "instance:f"
    status.record(target, Result(UP), T0)
    status.record(target, Result(DEGRADED, "Slow answer: 4000 ms"), T0 + 60)
    status.record(target, Result(DOWN, "HTTP 502"), T0 + 120)
    status.record(target, Result(DOWN, "HTTP 502"), T0 + 180)
    change = status.record(target, Result(UP), T0 + 240)
    found = status.incidents([target])
    assert len(found) == 1
    incident = found[0]
    assert (incident["start"], incident["end"], incident["severity"], incident["what"]) == (
        T0 + 60, T0 + 240, DOWN, "HTTP 502"
    )  # fmt: skip
    assert change.before == DOWN and change.after == UP and change.incident is not None
    meta = status.meta(target)
    assert meta is not None and meta["since"] == T0 + 240 and meta["incident"] is None


def test_prune_keeps_minutes_two_days_and_hours_ninety() -> None:
    target = "instance:g"
    status.record(target, Result(DOWN, "old"), T0)
    status.record(target, Result(UP), T0 + 60)
    status.record(target, Result(UP), T0 + 3 * DAY)
    removed = status.prune(T0 + 3 * DAY)
    assert storage().get(status.MINUTES, f"{target}|{int(T0)}") is None
    assert storage().get(status.HOURS, f"{target}|{int(T0)}") is not None
    assert removed == 1
    status.prune(T0 + 100 * DAY)
    assert storage().get(status.HOURS, f"{target}|{int(T0)}") is None
    assert not status.incidents([target])


def test_overall_banner() -> None:
    assert status.overall({}, []) == "unknown"
    assert status.overall({"instance:a": UP, SERVER: UP}, ["instance:a"]) == "operational"
    assert status.overall({"instance:a": UP, SERVER: DEGRADED}, ["instance:a"]) == "degraded"
    assert status.overall({"instance:a": UP, "backups:a": DOWN}, ["instance:a"]) == "degraded"
    assert status.overall({"instance:a": DOWN, SERVER: UP}, ["instance:a"]) == "down"


# ---------------------------------------------------------------- the checks


def test_server_check_follows_the_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    from collections import deque

    host: dict[str, Any] = {"history": deque([(T0, 5.0, 1)]), "disk_total": 100, "disk_used": 50,
                            "memory_total": 100, "memory_used": 10}  # fmt: skip
    monkeypatch.setattr(monitor, "HOST", host)
    assert checks.check_server(T0 + 10).state == UP
    assert checks.check_server(T0 + 3600).state == DOWN
    host["disk_used"] = 97
    assert checks.check_server(T0 + 10).state == DEGRADED


def test_backups_follow_the_daily_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backups, "daily_slot", lambda now=None: T0 + 2 * HOUR)  # DAILY_BACKUP_TIME 02:00
    monkeypatch.setattr(store, "read_env", lambda name: {"CREATED_AT": str(int(T0 - 30 * DAY))})
    taken: list[dict[str, Any]] = []
    monkeypatch.setattr(backups, "own_backups", lambda name, kinds=backups.KINDS: taken)
    assert checks.check_backups("shop", T0 + 3 * HOUR).state == DOWN  # none at all
    taken.append({"created": T0 + 2 * HOUR - DAY + 60})  # yesterday's
    assert checks.check_backups("shop", T0 + 3 * HOUR).state == UP  # today's still has its grace hours
    assert checks.check_backups("shop", T0 + 5 * HOUR).state == DOWN  # late now
    taken.insert(0, {"created": T0 + 2 * HOUR + 120})
    assert checks.check_backups("shop", T0 + 5 * HOUR).state == UP


class Gateway(BaseHTTPRequestHandler):
    answer = 200
    delay = 0.0
    paths: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        type(self).paths.append(self.path)
        time.sleep(type(self).delay)
        self.send_response(type(self).answer)
        self.end_headers()
        self.wfile.write(b'{"status": "pass"}')

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def gateway() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Gateway.answer, Gateway.delay, Gateway.paths = 200, 0.0, []
    yield server.server_address[1]
    server.shutdown()


@pytest.fixture
def production(gateway: int) -> Iterator[dict[str, Any]]:
    project = projects.create(repository="Acme/Watched", odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    folder = store.folder("watched-main")
    (folder / "config").mkdir(parents=True)
    store.write_env("watched-main", {"ODOO_VERSION": "18.0", "ODOO_PORT": 28990, "GATEWAY_PORT": gateway,
                                     "GIT_BRANCH": "main", "STAGE": "production", "PROJECT": project["id"],
                                     "CREATED_AT": int(time.time())})  # fmt: skip
    (folder / "docker-compose.yml").write_text("services: {}\n")
    yield project
    shutil.rmtree(folder, ignore_errors=True)


def test_instance_is_checked_through_its_gateway(production: dict[str, Any]) -> None:
    found = checks.check_instance("watched-main")
    assert found.state == UP and found.ms is not None and Gateway.paths == ["/web/health"]
    Gateway.answer = 502
    assert checks.check_instance("watched-main").state == DOWN
    Gateway.answer = 200
    live.change({"status_slow_ms": 100})
    Gateway.delay = 0.2
    assert checks.check_instance("watched-main").state == DEGRADED


def test_a_round_records_and_notifies_production_down_and_up(
    production: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo import notifications

    sent: list[str] = []
    monkeypatch.setattr(notifications, "emit", lambda event, project_id, **values: sent.append(event) or 1)
    monkeypatch.setattr(checks, "check_server", lambda now: Result(UP))
    monkeypatch.setattr(checks, "check_backups", lambda name, now: Result(UP))
    Gateway.answer = 503
    checks.run_once(T0)
    assert sent == []  # one failed check is an incident, not yet a notification
    checks.run_once(T0 + 60)
    assert sent == ["production_down"]
    checks.run_once(T0 + 120)
    assert sent == ["production_down"]
    Gateway.answer = 200
    checks.run_once(T0 + 180)
    assert sent == ["production_down", "production_up"]
    view = checks.project_status(production, T0 + 200)
    instance = next(t for t in view["targets"] if t["kind"] == "instance")
    assert view["state"] == "operational" and instance["state"] == UP and instance["branch"] == "main"
    assert view["incidents"][0]["duration"] == 180 and view["incidents"][0]["name"] == "watched-main"


# ---------------------------------------------------------------- the API


def test_status_tab_and_public_page(admin: Client, operator: Client, production: dict[str, Any],
                                    monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    client, headers = admin
    status.record("instance:watched-main", Result(UP, "HTTP 200", 120.0))
    answer = client.get(f"/api/v1/projects/{production['id']}/status")
    assert answer.status_code == 200
    data = answer.get_json()["data"]
    assert data["production"] is True and data["public"] == {"enabled": False, "allowed": True, "path": None}
    assert {t["kind"] for t in data["targets"]} == {"instance", "backups", "server"}

    developer, developer_headers = operator
    assert developer.get(f"/api/v1/projects/{production['id']}/status").get_json()["data"]["public"] is None
    refused = developer.put(f"/api/v1/projects/{production['id']}/status/public", json={"enabled": True},
                            headers=developer_headers)  # fmt: skip
    assert refused.status_code == 403

    turned = client.put(f"/api/v1/projects/{production['id']}/status/public", json={"enabled": True}, headers=headers)
    path = turned.get_json()["data"]["path"]
    assert path.startswith("/status/")
    token = path.removeprefix("/status/")
    anonymous = client.application.test_client()
    page = anonymous.get(f"/api/v1/public-status/{token}")
    assert page.status_code == 200
    shown = page.get_json()["data"]
    assert shown["name"] == production["name"]
    assert set(shown["targets"][0]) == {"kind", "name", "state", "uptime", "days"}
    assert "detail" not in str(shown) and "branch" not in str(shown)

    renewed = client.put(f"/api/v1/projects/{production['id']}/status/public", json={"enabled": True,
                         "new_link": True}, headers=headers).get_json()["data"]  # fmt: skip
    assert renewed["path"] != path
    assert anonymous.get(f"/api/v1/public-status/{token}").status_code == 404
    live.change({"status_public_pages": False})
    assert anonymous.get(f"/api/v1/public-status/{renewed['path'].removeprefix('/status/')}").status_code == 404
    assert anonymous.get("/api/v1/public-status/short").status_code == 404


def test_public_token_is_stored_hashed_and_encrypted(production: dict[str, Any]) -> None:
    view = pages.change(production["id"], True, False, "t")
    token = view["path"].removeprefix("/status/")
    stored = storage().get(status.PAGES, production["id"])
    assert stored is not None and token not in str(stored)
    assert pages.project_of(token) == production["id"]
    pages.change(production["id"], False, False, "t")
    assert pages.project_of(token) is None
