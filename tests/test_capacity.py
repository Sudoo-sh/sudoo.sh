"""Capacity: how many instances the server runs comfortably, the panel's own footprint, warnings and the hard
limit."""

from __future__ import annotations

import time

from flask.testing import FlaskClient

import docrun  # noqa: E402
from sudoo.config import live
from sudoo.instances import capacity, metrics, monitor, store
from sudoo.instances.capacity import MB, Measured, Rules, estimate

Client = tuple[FlaskClient, dict[str, str]]
GB = 1024 * MB


def _server(memory_gb: float, *, used_percent: float = 40.0, panel_mb: int = 300, cores: int = 4) -> dict:
    return {
        "memory_total_bytes": int(memory_gb * GB),
        "memory_percent": used_percent,
        "cores": cores,
        "panel_memory_bytes": panel_mb * MB,
        "panel_memory_peak_bytes": panel_mb * MB,
        "panel_cpu_percent": 0.5,
        "disk_total_bytes": 200 * GB,
        "disk_used_bytes": 80 * GB,
    }


def _measured(peak_mb: int, *, version: str = "18.0", cpu: float = 0.2, samples: int = 10) -> Measured:
    return Measured("x", version, samples, peak_mb * MB * 0.8, peak_mb * MB, cpu)


def test_no_history_uses_the_default_per_instance() -> None:
    # 8 GB: reserve 1 GB (more than 10 %), panel 300 MB, 700 MB per instance -> 6868 / 700 = 9
    result = estimate(_server(8), [], 0, [], Rules())
    assert result["comfortable"] == 9 and result["headroom"] == 9
    assert result["memory"]["per_instance_source"] == "default"
    assert result["memory"]["reserve_bytes"] == GB
    assert result["processor"]["instances"] is None and result["limited_by"] == "memory"
    assert not result["warning"] and result["message"] is None and not result["overloaded"]
    assert result["by_version"] == []
    # 16 GB: the reserve is 10 % (1.6 GB) -> 14446 / 700 = 20
    assert estimate(_server(16), [], 0, [], Rules())["comfortable"] == 20


def test_measured_peaks_decide_and_short_runs_do_not_count() -> None:
    measured = [_measured(1000), _measured(1200, version="19.0"), _measured(100, samples=1)]  # the last: starting
    result = estimate(_server(8), measured, 2, [], Rules())
    assert result["memory"]["per_instance_source"] == "measured"
    assert result["memory"]["per_instance_bytes"] == 1100 * MB and result["memory"]["measured_instances"] == 2
    assert result["memory"]["instance_largest_bytes"] == 1200 * MB
    assert result["comfortable"] == (8192 - 1024 - 300) // 1100 == 6
    assert [v["version"] for v in result["by_version"]] == ["18.0", "19.0"]


def test_tiny_server_has_room_for_none() -> None:
    result = estimate(_server(1, panel_mb=250), [], 0, [], Rules())
    assert result["memory"]["usable_bytes"] == 0 and result["comfortable"] == 0
    assert result["warning"] and result["message"].startswith("This server can run about 0 instances at once")


def test_more_running_than_capacity_warns_and_alerts() -> None:
    result = estimate(_server(4), [], 5, [], Rules())  # (4096 - 1024 - 300) / 700 = 3
    assert result["comfortable"] == 3 and result["headroom"] == -2
    assert result["message"] == (
        "This server can run about 3 instances at once; 5 are running. Starting another may slow every instance."
    )
    assert result["overloaded"] and "5 instances are running" in result["alert"]
    full = estimate(_server(4), [], 3, [], Rules())
    assert full["warning"] and not full["overloaded"]  # at the estimate: warn before one more, no alert yet


def test_high_memory_alerts_and_the_processor_can_be_the_limit() -> None:
    high = estimate(_server(16, used_percent=91), [], 1, [], Rules())
    assert high["overloaded"] and high["alert"].startswith("Memory is at 91% of this server")
    busy = [_measured(500, cpu=20.0) for _ in range(3)]  # each keeps a fifth of the processor busy
    result = estimate(_server(16), busy, 3, [], Rules(), panel_cpu_average=5.0)
    assert result["processor"]["instances"] == 4 and result["limited_by"] == "processor"
    assert result["comfortable"] == 4


def test_disk_headroom_and_hard_limit() -> None:
    result = estimate(_server(8), [], 2, [2 * GB, 4 * GB, 0], Rules(hard_limit=2))
    assert result["disk"] == {"free_bytes": 120 * GB, "instance_average_bytes": 3 * GB, "more_instances": 40}
    assert result["hard_limit"] == 2 and result["blocked"]
    assert estimate(_server(8), [], 2, [], Rules())["disk"]["more_instances"] is None


def _usage(running: bool, memory: int) -> metrics.Usage:
    return metrics.Usage(running, 1.0, memory, 2 * GB, time.time() - 60 if running else None, GB if running else None)


def _measure(running: dict[str, bool]) -> None:
    server = {**_server(8), "panel_cpu_percent": 0.5, "panel_memory_bytes": 250 * MB}
    server.pop("panel_memory_peak_bytes")
    usage = {name: _usage(on, 600 * MB if on else 0) for name, on in running.items()}
    metrics.sample(server, usage, {"backend": {"cpu_percent": 0.5, "memory_bytes": 250 * MB, "containers": 1}})


def test_panel_footprint_from_docker_figures(monkeypatch) -> None:
    usage = {
        "sudoo-panel": (40.0, 200 * MB, 0),
        "sudoo-web": (4.0, 20 * MB, 0),
        "sudoo-connector-panel": (2.0, 25 * MB, 0),
        "sudoo-connector-shop": (2.0, 25 * MB, 0),
        "odoo-shop-odoo": (90.0, GB, 0),
    }
    parts = monitor.panel_footprint(usage, 4)
    assert parts["backend"] == {"cpu_percent": 10.0, "memory_bytes": 200 * MB, "containers": 1}
    assert parts["connectors"]["containers"] == 2 and parts["connectors"]["memory_bytes"] == 50 * MB
    outside = monitor.panel_footprint({}, 4)  # not in a container: this process
    assert outside["backend"]["memory_bytes"] > 0 and "web" not in outside


def test_capacity_endpoint_and_metrics_carry_the_estimate(admin: Client) -> None:
    client, _ = admin
    for _ in range(3):
        _measure({"a": True, "b": True, "c": False})
    data = client.get("/api/v1/server/capacity").get_json()["data"]
    assert data["running"] == 2 and data["memory"]["per_instance_source"] == "measured"
    assert data["memory"]["per_instance_bytes"] == 600 * MB
    assert data["panel"]["memory_peak_bytes"] == 250 * MB and data["panel"]["parts"]["backend"]["containers"] == 1
    current = client.get("/api/v1/metrics").get_json()["data"]
    assert current["capacity"]["comfortable"] == data["comfortable"]
    assert current["server"]["panel_memory_peak_bytes"] == 250 * MB
    history = client.get("/api/v1/metrics/history?minutes=5").get_json()["data"]["server"]
    assert history["panel_memory_bytes"][-1] == 250 * MB


def test_hard_limit_refuses_creation_and_start_only_when_set(admin: Client, monkeypatch) -> None:
    client, headers = admin
    _measure({"a": True, "b": True})
    monkeypatch.setattr(
        store, "names", lambda: ["a", "b"]
    )  # the strict count is the shared record, of instances that exist
    docrun.run("a")
    docrun.run("b")
    assert capacity.refusal() is None  # off by default: only warned
    live.change({"max_running_instances": 2})
    try:
        assert "at most 2 instances at once" in (capacity.refusal() or "")
        answer = client.post(
            "/api/v1/instances", json={"version": "18.0", "edition": "community", "name": "more"}, headers=headers
        )
        assert answer.status_code == 403 and "at most 2 instances" in answer.get_json()["detail"]
    finally:
        live.change({"max_running_instances": None})
        docrun.reset()
    assert capacity.refusal() is None
