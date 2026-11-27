"""Resources of an instance (instances/resources.py): unlimited by default, production first under load, limits only
when an admin sets them, odoo.conf workers and limits, the checks, the API and the job that applies them. Docker is
never run here (tests/test_resources_docker.py checks the rendering against the installed Docker)."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

from sudoo import config, roles
from sudoo.config import live
from sudoo.core import jobs
from sudoo.core.jobs import JobError
from sudoo.core.state import STATE
from sudoo.instances import gateway, gateway_service, integrity, odoo, operations, resources, store

Client = tuple[FlaskClient, dict[str, str]]
NAME = "res"
REFS = {"ODOO_IMAGE_REF": "odoo:18.0@sha256:" + "1" * 64, "DB_IMAGE_REF": "postgres:16@sha256:" + "2" * 64}


@pytest.fixture(autouse=True)
def instance(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    folder = store.folder(NAME)
    (folder / "config").mkdir(parents=True)
    (folder / "addons").mkdir()
    store.write_env(
        NAME,
        {"ODOO_VERSION": "18.0", "EDITION": "community", "ODOO_PORT": 28200, "GATEWAY_PORT": 8200,
         "DB_NAME": "res", "DB_PASSWORD": "pw", "AUTO_PAUSE": "false", "DELETE_AFTER_DAYS": 0,
         "CREATED_AT": int(time.time()), "GIT_BRANCH": "main"},
    )  # fmt: skip
    (folder / "docker-compose.yml").write_text("services: {}\n")
    odoo.write_config(NAME, "res", "pw", "master")
    monkeypatch.setattr(integrity, "pin_images", lambda job, name, pull: store.update_env(name, **REFS) or REFS)
    monkeypatch.setattr(integrity, "odoo_user", lambda reference: "100:101")
    monkeypatch.setattr(integrity, "isolated_networks", lambda: True)
    monkeypatch.setattr(integrity, "_image", lambda ref: {"Id": "sha256:" + "a" * 64, "RepoDigests": []})
    monkeypatch.setattr(resources, "cores", lambda: 4)
    monkeypatch.setattr(resources, "_server_memory", lambda: (8 * 1024**3, 6 * 1024**3))
    yield
    shutil.rmtree(folder, ignore_errors=True)
    integrity.forget(NAME)
    resources.forget(NAME)
    STATE.remove(NAME)
    live.change(dict.fromkeys(live.overrides()))


def _spec(stage: str = "development") -> dict[str, Any]:
    store.update_env(NAME, STAGE=stage)
    integrity.apply(jobs.Job(NAME, "test", "test", "system"), NAME)
    found = integrity.record(NAME)
    assert found is not None
    return found["spec"]


# ---------------------------------------------------------------- compose: priority and limits


@pytest.mark.parametrize(
    ("stage", "weight", "oom", "reservation", "db_reservation"),
    [
        ("production", 4096, 0, "1g", "512m"),
        ("staging", 2048, 300, None, None),
        ("development", 1024, 600, None, None),
    ],
)
def test_every_stage_gets_its_weights_and_no_limit_by_default(
    stage: str, weight: int, oom: int, reservation: str | None, db_reservation: str | None
) -> None:
    services = _spec(stage)["services"]
    for service, reserved in (("odoo", reservation), ("db", db_reservation)):
        keys = services[service]
        assert keys["cpu_shares"] == weight and keys["oom_score_adj"] == oom
        assert keys.get("mem_reservation") == reserved
        assert "mem_limit" not in keys and "cpus" not in keys  # unlimited unless an admin sets a limit
    assert services["odoo"]["ports"] == ["127.0.0.1:28200:8069"]  # threaded: no WebSocket port


def test_production_gets_the_processor_and_memory_first_and_is_stopped_last() -> None:
    weights = {stage: resources.priority(stage) for stage in ("production", "staging", "development")}
    assert weights["production"]["cpu_weight"] > weights["staging"]["cpu_weight"] > weights["development"]["cpu_weight"]
    assert weights["production"]["oom_score_adj"] < weights["staging"]["oom_score_adj"]
    assert weights["staging"]["oom_score_adj"] < weights["development"]["oom_score_adj"]
    assert [weights[s]["rank"] for s in ("production", "staging", "development")] == [1, 2, 3]


def test_weights_are_settings() -> None:
    live.change({"priority_cpu_weight_production": 8192, "priority_oom_score_development": 900,
                 "priority_memory_reservation_staging": "256m"})  # fmt: skip
    assert _spec("production")["services"]["db"]["cpu_shares"] == 8192
    staging = _spec("staging")["services"]
    assert staging["odoo"]["mem_reservation"] == "256m" and "mem_reservation" not in staging["db"]
    assert _spec("development")["services"]["odoo"]["oom_score_adj"] == 900
    with pytest.raises(ValueError):
        live.change({"priority_oom_score_production": -1000})  # the out-of-memory killer is never switched off


def test_stage_defaults_and_the_instances_own_limits() -> None:
    live.change({"staging_odoo_memory_limit": "2g", "staging_db_cpu_limit": 1.5})
    services = _spec("staging")["services"]
    assert services["odoo"]["mem_limit"] == "2g" and services["db"]["cpus"] == 1.5
    resources.save_overrides(NAME, {"odoo_memory_limit": "0", "odoo_cpu_limit": 3}, "admin")
    services = _spec("staging")["services"]
    assert "mem_limit" not in services["odoo"]  # 0 for this instance: no limit, whatever the stage says
    assert services["odoo"]["cpus"] == 3 and services["db"]["cpus"] == 1.5


def test_a_reservation_never_exceeds_the_limit_and_processors_never_exceed_the_server() -> None:
    keys = resources.compose_keys("production", {"odoo_memory_limit": "512m", "db_cpu_limit": 64}, available=4)
    assert keys["odoo"]["mem_reservation"] == "512m" and keys["odoo"]["mem_limit"] == "512m"
    assert keys["db"]["cpus"] == 4


def test_check_spec_refuses_switching_the_oom_killer_off() -> None:
    spec = _spec()
    spec["services"]["odoo"]["oom_score_adj"] = -1000
    with pytest.raises(odoo.UnsafeCompose, match="out-of-memory"):
        odoo.check_spec(spec)


def test_containers_matching_the_weights_have_no_drift_and_changed_weights_are_found() -> None:
    spec = _spec("production")
    found = integrity.record(NAME)
    assert found is not None
    want = spec["services"]["odoo"]
    host = {"CpuShares": want["cpu_shares"], "MemoryReservation": integrity._memory(want["mem_reservation"]),
            "OomScoreAdj": want["oom_score_adj"]}  # fmt: skip
    texts = integrity.compare(NAME, "odoo", spec, found, {"HostConfig": host})
    assert not [t for t in texts if t.startswith(("processor weight", "memory reservation", "out-of-memory"))]
    host["CpuShares"] = 1024
    texts = integrity.compare(NAME, "odoo", spec, found, {"HostConfig": host})
    assert any(t.startswith("processor weight") for t in texts)


# ---------------------------------------------------------------- odoo.conf, workers and WebSockets


def test_odoo_conf_gets_workers_and_limits_only_when_set() -> None:
    conf = odoo.read_config(NAME)
    assert not any(conf.has_option("options", o) for o in resources.CONF_OPTIONS.values())
    _spec()
    limits = {"odoo_workers": 3, "odoo_cron_threads": 1, "odoo_limit_memory_soft_mb": 640,
              "odoo_limit_memory_hard_mb": 768, "odoo_limit_time_cpu": 60, "odoo_limit_time_real": 120}  # fmt: skip
    resources.save_overrides(NAME, limits, "admin")
    assert resources.write_conf(NAME) is True
    options = dict(odoo.read_config(NAME)["options"])
    assert options["workers"] == "3" and options["max_cron_threads"] == "1"
    assert options["limit_memory_soft"] == str(640 * 1024**2) and options["limit_memory_hard"] == str(768 * 1024**2)
    assert options["limit_time_cpu"] == "60" and options["limit_time_real"] == "120"
    assert options["list_db"] == "False"  # the rest of the file is kept
    assert resources.write_conf(NAME) is False
    found = integrity.record(NAME)
    assert found is not None and found["conf"] == store.config_file(NAME).read_text()
    resources.save_overrides(NAME, {}, "admin")
    resources.write_conf(NAME)
    assert not any(odoo.read_config(NAME).has_option("options", o) for o in resources.CONF_OPTIONS.values())


def test_workers_publish_the_websocket_port_and_the_gateway_follows() -> None:
    assert gateway.PanelHost().route(NAME) == gateway.Route(28200, False, None)
    resources.save_overrides(NAME, {"odoo_workers": 2}, "admin")
    resources.write_conf(NAME)
    spec = _spec()
    env = store.read_env(NAME)
    port = int(env["ODOO_GEVENT_PORT"])
    assert env["ODOO_WORKERS"] == "2"
    assert spec["services"]["odoo"]["ports"] == ["127.0.0.1:28200:8069", f"127.0.0.1:{port}:8072"]
    assert gateway.PanelHost().route(NAME) == gateway.Route(28200, False, port)
    assert not resources.pending(NAME)
    resources.save_overrides(NAME, {}, "admin")  # back to threaded mode: the port stays reserved, unpublished
    assert resources.pending(NAME)
    spec = _spec()
    assert spec["services"]["odoo"]["ports"] == ["127.0.0.1:28200:8069"]
    assert store.read_env(NAME)["ODOO_GEVENT_PORT"] == str(port) and store.read_env(NAME)["ODOO_WORKERS"] == "0"
    assert gateway.PanelHost().route(NAME) == gateway.Route(28200, False, None)


def test_routes_json_carries_the_websocket_port() -> None:
    document = {
        "format": gateway_service.FORMAT, "mode": "service",
        "edge": {"listen_host": "127.0.0.1", "https": False, "client_ip_header": None}, "settings": {},
        "instances": {"a": {"gateway_port": 8300, "odoo_port": 28300, "switched_off": False, "websocket_port": 28301},
                      "b": {"gateway_port": 8302, "odoo_port": 28302, "switched_off": False}},
    }  # fmt: skip
    routes = gateway_service.parse_routes(json.loads(json.dumps(document)))
    assert routes.instances["a"][1].websocket_port == 28301 and routes.instances["b"][1].websocket_port is None
    document["instances"]["a"]["websocket_port"] = "x"  # type: ignore[index]
    with pytest.raises(ValueError):
        gateway_service.parse_routes(document)


def test_pending_follows_the_stage() -> None:
    _spec("development")
    assert not resources.pending(NAME)
    store.update_env(NAME, STAGE="production")  # the weights change at the next start
    assert resources.pending(NAME)


# ---------------------------------------------------------------- checks


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"odoo_workers": -1}, "whole number"),
        ({"odoo_workers": "two"}, "whole number"),
        ({"odoo_workers": True}, "whole number"),
        ({"odoo_workers": 300}, "at most"),
        ({"odoo_cpu_limit": 8}, "4 cores"),
        ({"db_cpu_limit": "lots"}, "processor cores"),
        ({"odoo_memory_limit": "64m"}, "at least 128m"),
        ({"odoo_memory_limit": "2 GB"}, "Docker units"),
        ({"odoo_limit_memory_soft_mb": 900, "odoo_limit_memory_hard_mb": 800}, "soft memory"),
        ({"swap": 1}, "Unknown"),
    ],
)
def test_limits_that_cannot_work_are_refused(data: dict[str, Any], message: str) -> None:
    with pytest.raises(resources.Invalid, match=message):
        resources.clean(data, available=4)


def test_cleaning_keeps_set_values_and_drops_empty_ones() -> None:
    assert resources.clean({"odoo_workers": "2", "odoo_memory_limit": "4G", "db_cpu_limit": "0.5",
                            "odoo_cron_threads": None, "odoo_limit_time_cpu": ""}, available=4) == {
        "odoo_workers": 2, "odoo_memory_limit": "4g", "db_cpu_limit": 0.5}  # fmt: skip


def test_warnings_never_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resources.warnings(NAME, resources.effective(NAME), "development") == []
    monkeypatch.setattr(resources, "_server_memory", lambda: (1024**3, 512 * 1024**2))
    found = resources.warnings(NAME, {"odoo_workers": 12, "odoo_memory_limit": "1g"}, "production")
    codes = {w["code"] for w in found}
    assert codes == {"reserved_over_total", "workers_memory", "workers_over_cores"}  # 1.5 GB reserved, 1 GB memory
    assert all(w["text"] for w in found)


# ---------------------------------------------------------------- settings


def test_limits_are_off_by_default_and_old_limits_set_in_the_panel_become_stage_defaults() -> None:
    for stage in config.STAGES:
        assert all(not value or value == "0" for value in resources.stage_defaults(stage).values())
    migrated = config._migrated({"odoo_memory_limit": "3g", "db_cpu_limit": 1, "staging_db_cpu_limit": 2})
    assert "odoo_memory_limit" not in migrated
    assert {migrated[f"{s}_odoo_memory_limit"] for s in config.STAGES} == {"3g"}
    assert migrated["production_db_cpu_limit"] == 1 and migrated["staging_db_cpu_limit"] == 2


def test_only_administrators_set_resources() -> None:
    assert "instances.resources" in roles.permissions("admin")
    assert "instances.resources" in roles.permissions("super_admin")
    for role in ("maintainer", "developer", "viewer", roles.CONNECTED_APP):
        assert "instances.resources" not in roles.permissions(role)


# ---------------------------------------------------------------- API and job


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, tuple[Any, ...]]]:
    calls: list[tuple[Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system",
                   prepare: Any = None, **_: Any) -> jobs.Job:  # fmt: skip
        if prepare is not None:
            prepare()  # what the real start does once the slot is held
        calls.append((fn, args))
        return jobs.Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    return calls


def test_api_reads_and_applies_resources(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    url = f"/api/v1/instances/{NAME}/resources"
    data = client.get(url, headers=headers).get_json()["data"]
    assert data["unlimited"] is True and data["priority"]["rank"] == 3 and data["own"] == {}
    assert data["limits"]["odoo_workers"] == 0 and data["server"]["cores"] == 4
    answer = client.put(url, json={"limits": {"odoo_workers": 2, "odoo_memory_limit": "4g"}}, headers=headers)
    assert answer.status_code == 202
    assert started == [(operations.apply_resources, (NAME, {}))]
    assert resources.overrides(NAME) == {"odoo_workers": 2, "odoo_memory_limit": "4g"}
    data = client.get(url, headers=headers).get_json()["data"]
    assert data["unlimited"] is False and data["own"]["odoo_workers"] == 2
    assert client.put(url, json={"limits": {"odoo_workers": "x"}}, headers=headers).status_code == 400
    assert client.put(url, json={"workers": 2}, headers=headers).status_code == 400


def test_api_asks_production_to_confirm(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    store.update_env(NAME, STAGE="production")
    url = f"/api/v1/instances/{NAME}/resources"
    assert client.get(url, headers=headers).get_json()["data"]["priority"]["rank"] == 1
    answer = client.put(url, json={"limits": {"odoo_workers": 2}}, headers=headers)
    assert answer.status_code == 400 and "production" in answer.get_json()["detail"]
    assert not started and resources.overrides(NAME) == {}
    body = {"limits": {"odoo_workers": 2}, "confirm_production": True}
    assert client.put(url, json=body, headers=headers).status_code == 202


def test_api_refuses_those_without_the_permission(operator: Client, started: list[Any]) -> None:
    client, headers = operator
    url = f"/api/v1/instances/{NAME}/resources"
    assert client.get(url, headers=headers).status_code == 200
    assert client.put(url, json={"limits": {"odoo_workers": 2}}, headers=headers).status_code == 403
    assert not started


def test_the_job_puts_the_previous_limits_back_when_production_does_not_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.update_env(NAME, STAGE="production")
    backed_up: list[str] = []
    starts: list[dict[str, Any]] = []
    monkeypatch.setattr(operations.backups, "create", lambda job, name, kind: backed_up.append(kind) or [])
    monkeypatch.setattr(operations, "_paused", lambda name: False)

    def start(job: jobs.Job, name: str, restart: bool = False, strict: bool = False) -> None:
        starts.append(resources.overrides(name))
        if strict:
            raise JobError("Odoo did not answer after it was started")

    monkeypatch.setattr(operations, "_start_odoo", start)
    resources.save_overrides(NAME, {"odoo_memory_limit": "128m"}, "admin")
    with pytest.raises(JobError, match="previous ones were put back"):
        operations.apply_resources(jobs.Job(NAME, "Apply resources", "restart", "admin"), NAME, {"odoo_workers": 1})
    assert backed_up == ["before-update"]
    assert starts == [{"odoo_memory_limit": "128m"}, {"odoo_workers": 1}]
    assert resources.overrides(NAME) == {"odoo_workers": 1}


def test_the_job_only_writes_the_files_of_a_paused_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operations, "_paused", lambda name: True)
    monkeypatch.setattr(operations, "_start_odoo", lambda *a, **k: pytest.fail("a paused instance stays paused"))
    resources.save_overrides(NAME, {"odoo_workers": 2}, "admin")
    job = jobs.Job(NAME, "Apply resources", "restart", "admin")
    operations.apply_resources(job, NAME, {})
    assert odoo.read_config(NAME)["options"]["workers"] == "2"
    assert not resources.pending(NAME)
