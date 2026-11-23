"""The Logs tab: which logs there are, filtering (plain text, any case), downloads (permission, limit, audit) and
following a log over Server-Sent Events (Docker is replaced by a small program that prints lines)."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from collections.abc import Iterator
from typing import Any

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
# a richer log: a traceback under its ERROR line, a cron line, requests, and stamps Docker shortened (".5Z")
RICH = [
    "2026-11-22T08:00:00.1Z 2026-11-22 08:00:00,100 7 INFO shop odoo.modules.loading: Modules loaded.",
    "2026-11-22T08:00:01.000000001Z 2026-11-22 08:00:01,200 7 INFO shop odoo.addons.base.models.ir_cron: "
    "Job 'Mail: Email Queue Manager' (3) done in 0.04s",
    "2026-11-22T08:00:02.000000001Z 2026-11-22 08:00:02,300 7 INFO shop werkzeug: 10.0.0.9 - - "
    '[22/Nov/2026 08:00:02] "POST /web/dataset/call_kw/sale.order/web_search_read HTTP/1.1" 200 - 12 0.010 0.045',
    "2026-11-22T08:00:03.000000001Z 2026-11-22 08:00:03,400 7 WARNING shop odoo.addons.account.models.move: "
    "Generating nondeterministic report",
    "2026-11-22T08:01:00.000000001Z 2026-11-22 08:01:00,500 7 ERROR shop odoo.http: Exception during request handling.",
    "2026-11-22T08:01:00.000000002Z Traceback (most recent call last):",
    '2026-11-22T08:01:00.000000003Z   File "/usr/lib/python3/dist-packages/odoo/http.py", line 2203, in __call__',
    "2026-11-22T08:01:00.000000004Z     response = request._serve_db()",
    "2026-11-22T08:01:00.000000005Z odoo.exceptions.ValidationError: The payment does not match its total.",
    "2026-11-22T08:02:00.5Z 2026-11-22 08:02:00,600 7 CRITICAL shop odoo.service.server: Worker crashed",
    "2026-11-22T08:03:00.000000001Z 2026-11-22 08:03:00,700 7 DEBUG shop odoo.sql_db: query took 0.2s",
    "2026-11-22T08:04:00.000000001Z 2026-11-22 08:04:00,800 7 INFO shop werkzeug: 10.0.0.9 - - "
    '[22/Nov/2026 08:04:00] "GET /web/login HTTP/1.1" 500 - 4 0.002 0.120',
]
SOURCE: list[str] = []


def _printer(lines: list[str]) -> list[str]:
    return [sys.executable, "-c", "import sys; sys.stdout.write(sys.argv[1])", "".join(line + "\n" for line in lines)]


@pytest.fixture(autouse=True)
def instance(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    folder = store.folder("logbox")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env("logbox", {"ODOO_VERSION": "18.0", "DB_NAME": "logbox", "CREATED_AT": int(time.time())})
    SOURCE[:] = LINES

    def command(
        name: str, tail: int, follow: bool = False, since: str | None = None, until: str | None = None
    ) -> list[str]:
        # as Docker: the last `tail` lines (all with --since), then those from `since` and before `until`
        shown = list(SOURCE) if since else SOURCE[-tail:] if tail else []
        key = lambda line: logs.normal_stamp(line.split(" ", 1)[0])  # noqa: E731
        shown = [line for line in shown if not since or key(line) >= logs.normal_stamp(since)]
        shown = [line for line in shown if not until or key(line) < logs.normal_stamp(until)]
        return _printer(shown)

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


def test_job_logs_are_never_listed_or_read() -> None:
    """The technical logs of jobs are server logs (docker logs sudoo-panel): a job that ran no Odoo gives no log."""
    job = jobs.Job("logbox", "Get latest code", "update", "admin1")
    job.log.extend(
        ["$ pip install -r requirements.txt\n", "Collecting requests\n", "ERROR: no matching distribution\n"]
    )
    jobs.JOBS[job.id] = job
    assert [entry["name"] for entry in logs.available("logbox")] == ["odoo.log"]
    assert list(logs.tail("logbox", "update.log", 10)) == []
    assert list(logs.tail("logbox", "secrets.txt", 10)) == []


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


def test_a_job_log_cannot_be_followed(app: Flask, admin: Client) -> None:
    job = jobs.Job("logbox", "Create", "create", "admin1")
    job.log.extend(["one\n", "two\nthree\n"])
    jobs.JOBS[job.id] = job
    cookie = admin[0].get_cookie("sudoo_session")
    assert cookie is not None
    status, _body = _follow(app, cookie.value, "?log=install.log&tail=2")
    assert status == 400


# ---------------------------------------------------------------- reading as entries: parsing, filters, pages


def _rich_entries() -> list[logs.Entry]:
    return list(logs.group((logs.normal_stamp(s), t) for s, t in (logs.split_stamp(line) for line in RICH)))


def test_parse_line_takes_odoo_s_format_apart() -> None:
    head = logs.parse_line("2026-11-22 08:01:00,500 7 ERROR shop odoo.http: Exception during request handling.")
    assert head == {
        "time": "2026-11-22 08:01:00,500",
        "pid": "7",
        "level": "ERROR",
        "db": "shop",
        "logger": "odoo.http",
        "message": "Exception during request handling.",
    }
    werkzeug = logs.parse_line(RICH[2].split(" ", 1)[1])
    assert werkzeug is not None and werkzeug["logger"] == "werkzeug" and '"POST /web/dataset' in werkzeug["message"]
    assert logs.parse_line("Traceback (most recent call last):") is None
    assert logs.parse_line("2026-11-22 08:00:00,1 7 NOTALEVEL db x: y") is None


def test_normal_stamps_compare_as_text() -> None:
    assert logs.normal_stamp("2026-11-22T08:00:00.1Z") == "2026-11-22T08:00:00.100000000Z"
    assert logs.normal_stamp("2026-11-22T08:00:00Z") == "2026-11-22T08:00:00.000000000Z"
    assert logs.normal_stamp("2026-11-22T08:00:00.1Z") > logs.normal_stamp("2026-11-22T08:00:00.000000123Z")
    assert logs.normal_stamp("yesterday") == ""


def test_a_traceback_is_one_entry_with_its_line() -> None:
    entries = _rich_entries()
    assert len(entries) == 8
    error = entries[4]
    assert error.level == "ERROR" and error.logger == "odoo.http" and error.traceback
    assert error.lines[-1].startswith("odoo.exceptions.ValidationError")
    assert error.id == "2026-11-22T08:01:00.000000001Z"
    assert error.as_dict()["pid"] == 7 and error.as_dict()["database"] == "shop"
    assert not entries[0].traceback


def _kept(**args: str) -> list[str]:
    filters = logs.filters_from(args)
    return [f"{entry.level}:{entry.logger}" for entry in _rich_entries() if filters.keep(entry)]


def test_filters_by_level_logger_traceback_time_and_text() -> None:
    assert _kept(level="ERROR,CRITICAL") == ["ERROR:odoo.http", "CRITICAL:odoo.service.server"]
    assert _kept(level="warning") == ["WARNING:odoo.addons.account.models.move"]
    assert _kept(logger="odoo.addons") == [
        "INFO:odoo.addons.base.models.ir_cron",
        "WARNING:odoo.addons.account.models.move",
    ]
    assert _kept(logger="odoo.add") == []  # a logger and those under it, not a prefix of a name
    assert _kept(tracebacks="1") == ["ERROR:odoo.http"]
    assert _kept(since="2026-11-22T08:01:00Z", until="2026-11-22T08:03:00Z") == [
        "ERROR:odoo.http",
        "CRITICAL:odoo.service.server",
    ]
    assert _kept(q="VALIDATIONERROR") == ["ERROR:odoo.http"]  # the text of the whole entry, its traceback too
    assert _kept(q=r"HTTP/1\.1\" 5\d\d", regex="1") == ["INFO:werkzeug"]
    assert _kept(q="(a+)+", regex="0") == []  # plain text: never a pattern


def test_filters_refuse_bad_values() -> None:
    for args in (
        {"level": "LOUD"},
        {"logger": "odoo http"},
        {"q": "x" * 201},
        {"q": "(", "regex": "1"},
        {"since": "yesterday"},
        {"since": "2026-11-22T09:00:00Z", "until": "2026-11-22T08:00:00Z"},
        {"since": "2026-11-22T09:00:00"},  # no zone
    ):
        with pytest.raises(ValueError):
            logs.filters_from(args)


@pytest.mark.parametrize("pattern", [r"(a+)+$", r"(x+x+)+y", r"(a|aa)+$"])
def test_catastrophic_patterns_are_refused(pattern: str) -> None:
    started = time.monotonic()
    with pytest.raises(ValueError, match="very long"):
        logs.compile_pattern(pattern)
    assert time.monotonic() - started < 1


def test_a_pattern_slow_on_the_log_itself_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    filters = logs.filters_from({"q": "shop", "regex": "1"})
    monkeypatch.setattr(logs, "PATTERN_REQUEST_SECONDS", -1)
    with pytest.raises(logs.SlowPattern):
        filters.keep(_rich_entries()[0])


def _entries(client: FlaskClient, query: str) -> tuple[int, Any]:
    answer = client.get(f"/api/v1/instances/logbox/logs/entries{query}")
    return answer.status_code, answer.get_json()


def test_entries_pages_backwards_forwards_and_around(admin: Client) -> None:
    SOURCE[:] = RICH
    client, _ = admin
    status, body = _entries(client, "?limit=3")
    assert status == 200
    assert [e["level"] for e in body["data"]] == ["CRITICAL", "DEBUG", "INFO"]
    assert body["meta"] == {
        "log": "odoo.log",
        "has_before": True,
        "has_after": False,
        "first": "2026-11-22T08:02:00.500000000Z",
        "last": "2026-11-22T08:04:00.000000001Z",
    }
    _, earlier = _entries(client, "?limit=3&before=2026-11-22T08:02:00.5Z")
    assert [e["logger"] for e in earlier["data"]] == ["werkzeug", "odoo.addons.account.models.move", "odoo.http"]
    assert earlier["data"][-1]["traceback"] is True
    _, later = _entries(client, "?limit=2&after=2026-11-22T08:01:00.000000001Z")
    assert [e["level"] for e in later["data"]] == ["CRITICAL", "DEBUG"]
    assert later["meta"]["has_after"] is True
    _, around = _entries(client, "?limit=4&around=2026-11-22T08:01:00.000000001Z")
    ids = [e["id"] for e in around["data"]]
    assert "2026-11-22T08:01:00.000000001Z" in ids and len(ids) == 4
    _, errors = _entries(client, "?level=ERROR,CRITICAL")
    assert [e["level"] for e in errors["data"]] == ["ERROR", "CRITICAL"]


def test_entries_refuse_bad_requests(admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = admin
    assert _entries(client, "?limit=0")[0] == 400
    assert _entries(client, "?limit=5001")[0] == 400
    assert _entries(client, "?before=x;rm")[0] == 400
    assert _entries(client, "?before=2026-11-22T08:00:00Z&after=2026-11-22T08:00:00Z")[0] == 400
    assert _entries(client, "?log=/etc/passwd")[0] == 400
    assert _entries(client, "?q=(a%2B)%2B$&regex=1")[0] == 400  # refused before reading anything
    monkeypatch.setattr(logs, "PATTERN_REQUEST_SECONDS", -1)
    status, body = _entries(client, "?q=db&regex=1")
    assert status == 422 and "too long" in body["detail"]


def test_logs_keep_the_instance_s_access(app: Flask, admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.projects import access

    assert app.test_client().get("/api/v1/instances/logbox/logs/entries").status_code == 401
    client, _ = admin

    def refused(user: dict[str, str], permission: str, name: str) -> tuple[int, str] | None:
        return (404, "This instance does not exist.") if permission == "instances.logs" else None

    monkeypatch.setattr(access, "instance_refusal", refused)  # a locked branch, a closed project...
    for path in ("entries", "files", "download"):
        assert client.get(f"/api/v1/instances/logbox/logs/{path}").status_code == 404
    cookie = client.get_cookie("sudoo_session")
    assert cookie is not None
    assert _follow(app, cookie.value, "")[0] == 404


def test_download_applies_the_filters(admin: Client) -> None:
    SOURCE[:] = RICH
    client, _ = admin
    body = client.get("/api/v1/instances/logbox/logs/download?level=ERROR").get_data(as_text=True)
    assert body.splitlines()[0].endswith("ERROR shop odoo.http: Exception during request handling.")
    assert body.splitlines()[-1].startswith("odoo.exceptions.ValidationError")
    assert "levels ERROR" in audit.latest(1, instance="logbox")[0]["detail"]


# ---------------------------------------------------------------- install.log and update.log


def _module_job(kind: str) -> jobs.Job:
    compose = "$ docker compose -f /opt/odoo/instances/logbox/docker-compose.yml"
    job = jobs.Job("logbox", "Create", kind, "admin1")
    for text in (
        "\n== Preparing the instance\n",
        f"{compose} up -d db\n",
        " Container odoo-logbox-db-1  Started\n",
        "\n== Installing the repository's modules\n",
        f"{compose} run --rm odoo odoo -d logbox -i sale --stop-after-init --no-http\n",
        " Container odoo-logbox-db-1  Running\n",
        "2026-11-22 09:00:00,100 1 INFO logbox odoo.modules.loading: loading 64 modules...\n",
        "2026-11-22 09:00:05,200 1 ERROR logbox odoo.modules.registry: Failed to load registry\n",
        "Traceback (most recent call last):\n",
        '  File "/usr/lib/python3/dist-packages/odoo/modules/registry.py", line 110, in new\n',
        "    odoo.modules.load_modules(registry, force_demo, status, update_module)\n",
        "psycopg2.errors.UndefinedColumn: column x does not exist\n",
        "Pulling fs layer from https://user:hunter2@registry.example/odoo\n",
        "[warning] The modules were not all installed\n",
        f"{compose} run --rm odoo odoo -d logbox -u sale --stop-after-init --no-http\n",
        "2026-11-22 09:01:00,100 1 INFO logbox odoo.modules.loading: updating modules list\n",
    ):
        job.write(text)
    jobs.JOBS[job.id] = job
    return job


def test_install_and_update_logs_hold_only_odoo_s_own_lines(admin: Client) -> None:
    _module_job("create")
    assert [entry["name"] for entry in logs.available("logbox")] == ["odoo.log", "install.log", "update.log"]
    install = "".join(logs.tail("logbox", "install.log", 1000))
    assert install.splitlines() == [
        "2026-11-22 09:00:00,100 1 INFO logbox odoo.modules.loading: loading 64 modules...",
        "2026-11-22 09:00:05,200 1 ERROR logbox odoo.modules.registry: Failed to load registry",
        "Traceback (most recent call last):",
        '  File "/usr/lib/python3/dist-packages/odoo/modules/registry.py", line 110, in new',
        "    odoo.modules.load_modules(registry, force_demo, status, update_module)",
        "psycopg2.errors.UndefinedColumn: column x does not exist",
    ]
    update = "".join(logs.tail("logbox", "update.log", 1000))
    assert update == "2026-11-22 09:01:00,100 1 INFO logbox odoo.modules.loading: updating modules list\n"
    for text in (install, update):
        for secret in ("/opt/odoo", "docker", "Container", "hunter2", "==", "[warning]"):
            assert secret not in text
    client, _ = admin
    status, body = _entries(client, "?log=install.log&level=ERROR")
    assert status == 200 and body["data"][0]["traceback"] is True
    assert client.get("/api/v1/instances/logbox/logs/files").get_json()["data"][1]["started_at"].endswith("Z")


# ---------------------------------------------------------------- following with filters


def test_follow_keeps_a_traceback_with_its_error_and_gives_ids(app: Flask, admin: Client) -> None:
    SOURCE[:] = RICH
    cookie = admin[0].get_cookie("sudoo_session")
    assert cookie is not None
    status, text = _follow(app, cookie.value, "?tail=50&level=ERROR")
    assert status == 200
    frames: list[Any] = [data for event, _, data in _frames(text) if event == "lines"]
    lines = [line for data in frames for line in data["lines"]]
    ids = [line_id for data in frames for line_id in data["ids"]]
    assert lines[0].endswith("ERROR shop odoo.http: Exception during request handling.")
    assert lines[-1].startswith("odoo.exceptions.ValidationError") and len(lines) == 5
    assert ids[0] == "2026-11-22T08:01:00.000000001Z" and len(ids) == 5


def test_follow_refuses_a_catastrophic_pattern(app: Flask, admin: Client) -> None:
    cookie = admin[0].get_cookie("sudoo_session")
    assert cookie is not None
    assert _follow(app, cookie.value, "?q=(a%2B)%2B$&regex=1")[0] == 400
