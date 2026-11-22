"""The Logs tab: which logs there are, filtering (plain text, any case), downloads (permission, limit, audit) and
following a log over Server-Sent Events (Docker is replaced by a small program that prints lines)."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from collections.abc import Iterator

import pytest
from aiohttp.test_utils import TestClient, TestServer
from flask import Flask
from flask.testing import FlaskClient

from sudoo.config import live
from sudoo.core import audit, jobs
from sudoo.core.state import STATE
from sudoo.instances import logs, store
from sudoo.web.v1 import log_stream

Client = tuple[FlaskClient, dict[str, str]]
LINES = [
    "2026-11-22T07:00:00.000000001Z 2026-11-22 07:00:00,1 1 INFO db odoo.modules.loading: Modules loaded.",
    "2026-11-22T07:00:01.000000002Z 2026-11-22 07:00:01,2 1 WARNING db odoo.models: Something odd",
    "2026-11-22T07:00:02.000000003Z 2026-11-22 07:00:02,3 1 ERROR db odoo.http: Exception in request",
    "2026-11-22T07:00:03.000000004Z 2026-11-22 07:00:03,4 1 INFO db werkzeug: 127.0.0.1 GET /web HTTP/1.1 200",
]


def _printer(lines: list[str]) -> list[str]:
    return [sys.executable, "-c", "import sys; sys.stdout.write(sys.argv[1])", "".join(line + "\n" for line in lines)]


@pytest.fixture(autouse=True)
def instance(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    folder = store.folder("logbox")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env("logbox", {"ODOO_VERSION": "18.0", "DB_NAME": "logbox", "CREATED_AT": int(time.time())})
    asked: list[list[str]] = []

    def command(name: str, tail: int, follow: bool = False, since: str | None = None) -> list[str]:
        asked.append(["since", since or "", "tail", str(tail)])
        shown = [line for line in LINES if not since or line.split(" ", 1)[0] >= since]
        return _printer(shown[-tail:] if not since and tail else shown)

    monkeypatch.setattr(logs, "odoo_log_command", command)
    yield
    shutil.rmtree(folder, ignore_errors=True)
    STATE.remove("logbox")
    live.change(dict.fromkeys(live.overrides()))
    for key in [k for k, j in jobs.JOBS.items() if j.instance == "logbox"]:
        del jobs.JOBS[key]


def test_matcher_is_plain_text_ignoring_case() -> None:
    keep = logs.matcher("ERROR db")
    assert keep("2026 1 error DB x") and not keep("2026 1 INFO db")
    assert logs.matcher("(.*)+$")("nothing") is False  # never a pattern
    assert logs.matcher("  ")("anything")


def test_split_stamp() -> None:
    assert logs.split_stamp(LINES[0])[0] == "2026-11-22T07:00:00.000000001Z"
    assert logs.split_stamp("no stamp here") == ("", "no stamp here")


def test_available_lists_job_logs() -> None:
    job = jobs.Job("logbox", "Create", "create", "admin1")
    job.log.append("cloning\n")
    jobs.JOBS[job.id] = job
    found = logs.available("logbox")
    assert [entry["name"] for entry in found] == ["odoo.log", "install.log"]
    assert found[1]["job"] == job.id


def test_tail_of_a_job_log_with_filter() -> None:
    job = jobs.Job("logbox", "Get latest code", "update", "admin1")
    job.log.extend(["Collecting requests\n", "pip install ok\n", "ERROR: no matching distribution\n"])
    jobs.JOBS[job.id] = job
    assert list(logs.tail("logbox", "update.log", 10, "error")) == ["ERROR: no matching distribution\n"]
    assert list(logs.tail("logbox", "update.log", 1)) == ["ERROR: no matching distribution\n"]


def test_files_endpoint(admin: Client) -> None:
    client, _ = admin
    answer = client.get("/api/v1/instances/logbox/logs/files")
    assert answer.status_code == 200 and answer.get_json()["data"][0]["name"] == "odoo.log"


def test_download_filters_limits_and_audits(admin: Client) -> None:
    client, _ = admin
    answer = client.get("/api/v1/instances/logbox/logs/download?q=warning")
    assert answer.status_code == 200 and answer.mimetype == "text/plain"
    assert "attachment" in answer.headers["Content-Disposition"] and "odoo.log" in answer.headers["Content-Disposition"]
    body = answer.get_data(as_text=True)
    assert body.splitlines() == ["2026-11-22 07:00:01,2 1 WARNING db odoo.models: Something odd"]
    entry = audit.latest(1, instance="logbox")[0]
    assert entry["event"] == "log downloaded" and "warning" in entry["detail"]
    live.change({"log_download_max_lines": 1000})
    assert len(client.get("/api/v1/instances/logbox/logs/download").get_data(as_text=True).splitlines()) == 4


def test_download_refuses_other_logs_and_long_filters(admin: Client) -> None:
    client, _ = admin
    assert client.get("/api/v1/instances/logbox/logs/download?log=../etc/passwd").status_code == 400
    assert client.get("/api/v1/instances/logbox/logs/download?q=" + "x" * 201).status_code == 400


def test_download_needs_instances_logs(app: Flask) -> None:
    anonymous = app.test_client()
    assert anonymous.get("/api/v1/instances/logbox/logs/download").status_code == 401


# ---------------------------------------------------------------- following


def _frames(text: str) -> list[tuple[str, str | None, object]]:
    found = []
    for block in text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
        if "event" in fields:
            found.append((fields["event"], fields.get("id"), json.loads(fields["data"])))
    return found


def _follow(app: Flask, cookie: str, query: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    from sudoo.web.v1.events import make_app

    async def scenario() -> tuple[int, str]:
        async with TestClient(TestServer(make_app(app))) as stream_client:
            response = await stream_client.get(
                f"/api/v1/instances/logbox/logs/stream{query}",
                headers={"Cookie": f"sudoo_session={cookie}", **(headers or {})},
            )
            return response.status, await response.text()

    return asyncio.run(scenario())


def test_follow_sends_filtered_lines_then_ends(app: Flask, admin: Client) -> None:
    cookie = admin[0].get_cookie("sudoo_session")
    assert cookie is not None
    status, text = _follow(app, cookie.value, "?tail=10&q=ERROR")
    assert status == 200
    frames = _frames(text)
    lines = [line for event, _, data in frames if event == "lines" for line in data["lines"]]  # type: ignore[index]
    assert lines == ["2026-11-22 07:00:02,3 1 ERROR db odoo.http: Exception in request"]
    assert frames[-1][0] == "end" and frames[-1][2] == {"reason": "stopped"}
    assert frames[-1][1] == "2026-11-22T07:00:03.000000004Z"  # where to continue


def test_follow_resumes_after_the_last_event(app: Flask, admin: Client) -> None:
    cookie = admin[0].get_cookie("sudoo_session")
    assert cookie is not None
    _, text = _follow(app, cookie.value, "", {"Last-Event-ID": "2026-11-22T07:00:02.000000003Z"})
    lines = [line for event, _, data in _frames(text) if event == "lines" for line in data["lines"]]  # type: ignore[index]
    assert lines == [LINES[3].split(" ", 1)[1]]  # the line it resumed from is not sent again


def test_follow_refuses_bad_requests(app: Flask, admin: Client, operator: Client) -> None:
    cookie = admin[0].get_cookie("sudoo_session")
    assert cookie is not None
    assert _follow(app, cookie.value, "?log=secrets.txt")[0] == 400
    assert _follow(app, cookie.value, "?tail=99999")[0] == 400
    assert _follow(app, cookie.value, "?tail=²")[0] == 400
    assert _follow(app, "forged", "")[0] == 401


def test_follow_ends_after_the_maximum_duration(app: Flask, admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(log_stream, "MAX_SECONDS", 0)
    cookie = admin[0].get_cookie("sudoo_session")
    assert cookie is not None
    frames = _frames(_follow(app, cookie.value, "")[1])
    assert frames[-1][2] == {"reason": "max_duration"}


def test_follow_a_job_log(app: Flask, admin: Client) -> None:
    job = jobs.Job("logbox", "Create", "create", "admin1")
    job.log.extend(["one\n", "two\nthree\n"])
    job.status = "success"
    jobs.JOBS[job.id] = job
    cookie = admin[0].get_cookie("sudoo_session")
    assert cookie is not None
    frames = _frames(_follow(app, cookie.value, "?log=install.log&tail=2")[1])
    lines = [line for event, _, data in frames if event == "lines" for line in data["lines"]]  # type: ignore[index]
    assert lines == ["two", "three"] and frames[-1][2] == {"reason": "finished"}
    assert frames[0][1] == "2"  # the offset in the job's log to continue from
