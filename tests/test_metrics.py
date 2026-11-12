"""Metrics: request figures from the gateway, samples from the monitor, history, the API; and job progress."""

from __future__ import annotations

import time

from flask.testing import FlaskClient

from sudoo.core.jobs import Job
from sudoo.instances import metrics

Client = tuple[FlaskClient, dict[str, str]]

SERVER = {"cpu_percent": 12.5, "memory_percent": 40.0, "disk_percent": 55.0, "load_1m": 0.4}


def _usage(memory: int = 500, limit: int = 2000) -> metrics.Usage:
    return metrics.Usage(True, 3.2, memory, limit, time.time() - 120, 10**9)


def test_percentile() -> None:
    assert metrics.percentile([], 0.95) is None
    assert metrics.percentile([5.0], 0.95) == 5.0
    assert metrics.percentile([float(n) for n in range(1, 101)], 0.95) == 95.0


def test_a_sample_turns_requests_into_rates_and_percentages() -> None:
    metrics.sample(SERVER, {"shop": _usage()})  # starts the interval
    for duration in (10.0, 20.0, 30.0, 400.0):
        metrics.record_request("shop", 200, duration)
    metrics.record_request("shop", 502, 5.0)
    figures = metrics.sample(SERVER, {"shop": _usage()})["instances"]["shop"]
    assert figures["memory_percent"] == 25.0
    assert figures["requests_per_minute"] > 0
    assert figures["error_rate_percent"] == 20.0
    assert figures["response_ms_p95"] == 400.0 and figures["response_ms_avg"] == 93.0
    assert 115 <= figures["uptime_seconds"] <= 125
    quiet = metrics.sample(SERVER, {"shop": _usage()})["instances"]["shop"]
    assert quiet["requests_per_minute"] == 0 and quiet["response_ms_p95"] is None  # counting starts again


def test_history_is_in_columns_and_forgets_deleted_instances() -> None:
    metrics.sample(SERVER, {"a": _usage(), "b": _usage()})
    columns = metrics.instance_history("a")
    assert set(columns) == {"t", *metrics.INSTANCE_SERIES}
    assert len(columns["t"]) == len(columns["cpu_percent"]) >= 1
    metrics.sample(SERVER, {"a": _usage()})
    assert "b" not in metrics.snapshot()["instances"] and metrics.instance_history("b")["t"] == []


def test_metrics_endpoints(admin: Client) -> None:
    metrics.sample(SERVER, {})
    client, _ = admin
    current = client.get("/api/v1/metrics").get_json()["data"]
    assert current["server"]["cpu_percent"] == 12.5 and current["interval_seconds"] == 5
    history = client.get("/api/v1/metrics/history?minutes=5").get_json()
    assert history["meta"]["minutes"] == 5 and history["data"]["server"]["cpu_percent"][-1] == 12.5
    assert client.get("/api/v1/metrics/history?minutes=999").status_code == 400
    assert client.get("/api/v1/instances/nope/metrics").status_code == 404


def test_job_progress() -> None:
    job = Job("shop", "Restart", "restart", "tester")
    assert job.progress_percent() == 0
    job.status = "running"
    assert job.progress_percent() is None  # number of steps unknown
    job.expect(4)
    job.step("one")
    job.step("two")
    job.step("three")
    assert job.progress_percent() == 50
    for _ in range(3):
        job.step("more than expected")
    assert job.progress_percent() == 83  # never 100 while running
    job.status = "success"
    assert job.progress_percent() == 100


def test_docker_stats_lines_with_terminal_codes():
    from sudoo.instances.monitor import _stats_line

    body = '{"CPUPerc":"12.50%","MemUsage":"29.55MiB / 3GiB","Name":"sudoo-web"}'
    for line in (body + "\n", "\x1b[H" + body + "\n", body + " \x1b[K\n", "\x1b[2J\x1b[H" + body + " \x1b[K\n"):
        name, (cpu, used, limit) = _stats_line(line)
        assert name == "sudoo-web" and cpu == 12.5 and limit == 3 * 1024**3 and used > 29 * 1024**2
    assert _stats_line("\x1b[J\n") is None
    assert _stats_line('{"Name": "x"}\n') is None
    assert _stats_line("{broken\n") is None


def test_daily_backup_slot_and_due_instances(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from sudoo.config import live
    from sudoo.instances import backups

    monkeypatch.setattr(live, "_current", live._current.model_copy(update={"timezone": "Africa/Cairo"}), raising=False)
    zone = ZoneInfo("Africa/Cairo")
    at = datetime(2026, 11, 12, 15, 30, tzinfo=zone).timestamp()
    slot = backups.daily_slot(at)
    assert datetime.fromtimestamp(slot, zone) == datetime(2026, 11, 12, 0, 0, tzinfo=zone)
    before_midnight = datetime(2026, 11, 11, 23, 0, tzinfo=zone).timestamp()
    assert backups.needs_daily(before_midnight, slot)  # used yesterday: due
    assert not backups.needs_daily(slot + 60, slot)  # first used after midnight: tomorrow's
    assert not backups.needs_daily(None, slot)  # not used since the last one


def test_backup_names_carry_their_kind():
    from sudoo.instances.backups import FILE_RE

    old = FILE_RE.match("shop__shop_db__20261112-000000.zip")
    assert old and old["kind"] is None
    daily = FILE_RE.match("shop__shop_db__20261112-000000__daily.zip")
    assert daily and daily["kind"] == "daily"
    assert not FILE_RE.match("shop__shop_db__20261112-000000__other.zip")
