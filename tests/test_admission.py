"""Requests worked on at once (web/admission.py): beyond MAX_CONCURRENT_REQUESTS a request waits its turn, and one
that waited REQUEST_QUEUE_SECONDS is answered 503 "busy" with Retry-After and nothing done, so a rush never leaves
the panel working for minutes on requests nobody waits for any more."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from sudoo.config import live
from sudoo.instances import odoo
from sudoo.web import admission

Client = tuple[FlaskClient, dict[str, str]]


@pytest.fixture
def one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """One request at a time, a second of waiting; GET /api/v1/versions holds its turn until the event is set."""
    live.change({"max_concurrent_requests": 1, "request_queue_seconds": 1})
    release = threading.Event()
    real = odoo.versions
    monkeypatch.setattr(odoo, "versions", lambda: release.wait(10) and real())
    yield release
    release.set()
    live.change({"max_concurrent_requests": None, "request_queue_seconds": None})


def _hold(client: FlaskClient, answers: list[int]) -> threading.Thread:
    thread = threading.Thread(target=lambda: answers.append(client.get("/api/v1/versions").status_code))
    thread.start()
    for _ in range(200):  # until it holds the only turn
        if admission.counts()["working"] == 1:
            break
        time.sleep(0.01)
    assert admission.counts()["working"] == 1
    return thread


def test_a_request_beyond_the_limit_waits_then_is_refused_busy(admin: Client, one_at_a_time: threading.Event) -> None:
    client, _ = admin
    answers: list[int] = []
    holder = _hold(client, answers)
    started = time.monotonic()
    refused = client.get("/api/v1/session")
    waited = time.monotonic() - started
    assert refused.status_code == 503 and 0.9 <= waited < 5
    assert refused.headers["Retry-After"] == str(admission.RETRY_SECONDS)
    assert refused.get_json()["detail"] == admission.BUSY
    assert client.get("/healthz").status_code == 200  # never waits
    one_at_a_time.set()
    holder.join(10)
    assert answers == [200]
    assert admission.counts() == {"working": 0, "waiting": 0}
    assert client.get("/api/v1/session").status_code == 200  # back to normal at once


def test_a_waiting_request_gets_its_turn_when_one_ends(admin: Client, one_at_a_time: threading.Event) -> None:
    live.change({"request_queue_seconds": 10})
    client, _ = admin
    answers: list[int] = []
    holder = _hold(client, answers)
    threading.Timer(0.3, one_at_a_time.set).start()
    started = time.monotonic()
    assert client.get("/api/v1/session").status_code == 200
    assert 0.2 <= time.monotonic() - started < 5
    holder.join(10)
    assert answers == [200] and admission.counts() == {"working": 0, "waiting": 0}


def test_no_limit_when_turned_off(admin: Client, one_at_a_time: threading.Event) -> None:
    live.change({"max_concurrent_requests": 0})
    client, _ = admin
    answers: list[int] = []
    thread = threading.Thread(target=lambda: answers.append(client.get("/api/v1/versions").status_code))
    thread.start()
    time.sleep(0.1)
    assert client.get("/api/v1/session").status_code == 200  # not held up by the request still being worked on
    one_at_a_time.set()
    thread.join(10)
    assert answers == [200] and admission.counts() == {"working": 0, "waiting": 0}


def test_an_error_gives_the_turn_back(app: Flask, admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    live.change({"max_concurrent_requests": 1, "request_queue_seconds": 1})
    try:
        monkeypatch.setattr(odoo, "versions", lambda: 1 / 0)
        client, _ = admin
        assert client.get("/api/v1/versions").status_code == 500
        assert admission.counts() == {"working": 0, "waiting": 0}
        assert client.get("/api/v1/session").status_code == 200
    finally:
        live.change({"max_concurrent_requests": None, "request_queue_seconds": None})
