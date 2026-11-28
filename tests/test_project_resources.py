"""Resources of projects and branches (instances/resources.py layers, web/v1/project_resources.py), the maxima, the
computed container limits, the reservation check, the cron timeout and database connections, storage measurements
(instances/disk_use.py) and their alerts, and the long-polling bus of older Odoo with workers. Docker never runs."""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

from sudoo import alerts, projects, roles
from sudoo.alerts import watch
from sudoo.config import live
from sudoo.core import jobs
from sudoo.core.jobs import JobError
from sudoo.core.state import STATE
from sudoo.instances import capacity, disk_use, integrity, odoo, operations, resources, store
from sudoo.projects import access
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
NAMES = {"pr-main": ("production", "main"), "pr-qa": ("staging", "qa"), "pr-dev": ("development", "dev")}
REFS = {"ODOO_IMAGE_REF": "odoo:18.0@sha256:" + "1" * 64, "DB_IMAGE_REF": "postgres:16@sha256:" + "2" * 64}
GB = 1024**3


@pytest.fixture(autouse=True)
def project(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    found = projects.create(repository="Acme/Sized", odoo_version="18.0", edition="community", origin="linked",
                            user="t")  # fmt: skip
    for number, (name, (stage, branch)) in enumerate(NAMES.items()):
        folder = store.folder(name)
        (folder / "config").mkdir(parents=True)
        (folder / "addons").mkdir()
        store.write_env(
            name,
            {"ODOO_VERSION": "18.0", "EDITION": "community", "ODOO_PORT": 28400 + number,
             "GATEWAY_PORT": 8400 + number, "DB_NAME": name.replace("-", "_"), "DB_PASSWORD": "pw",
             "AUTO_PAUSE": "false", "DELETE_AFTER_DAYS": 0, "CREATED_AT": int(time.time()), "GIT_BRANCH": branch,
             "STAGE": stage, "PROJECT": found["id"]},
        )  # fmt: skip
        (folder / "docker-compose.yml").write_text("services: {}\n")
        odoo.write_config(name, name.replace("-", "_"), "pw", "master")
    monkeypatch.setattr(integrity, "pin_images", lambda job, name, pull: store.update_env(name, **REFS) or REFS)
    monkeypatch.setattr(integrity, "odoo_user", lambda reference: "100:101")
    monkeypatch.setattr(integrity, "isolated_networks", lambda: True)
    monkeypatch.setattr(integrity, "_image", lambda ref: {"Id": "sha256:" + "a" * 64, "RepoDigests": []})
    monkeypatch.setattr(resources, "cores", lambda: 4)
    monkeypatch.setattr(resources, "_server_memory", lambda: (16 * GB, 12 * GB))
    monkeypatch.setattr(capacity, "current", lambda: {"memory": {"usable_bytes": 12 * GB, "total_bytes": 16 * GB}})
    yield found
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
        integrity.forget(name)
        resources.forget(name)
        disk_use.forget(name)
        STATE.remove(name)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    live.change(dict.fromkeys(live.overrides()))
    watch._ACTIVE.clear()
    watch._SINCE.clear()
    watch._OVER_SENT.clear()


def _spec(name: str) -> dict[str, Any]:
    integrity.apply(jobs.Job(name, "test", "test", "system"), name)
    found = integrity.record(name)
    assert found is not None
    return found["spec"]


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any, tuple[Any, ...]]]:
    calls: list[tuple[str, Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system",
                   **_: Any) -> jobs.Job:  # fmt: skip
        calls.append((instance, fn, args))
        return jobs.Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    return calls


# ---------------------------------------------------------------- layers, maxima, computed limits


def test_nothing_changes_for_existing_instances_by_default() -> None:
    for name in NAMES:
        assert all(not value or value == "0" for value in resources.effective(name).values())
        services = _spec(name)["services"]
        assert "mem_limit" not in services["odoo"] and "command" not in services["db"]
        assert not any(odoo.read_config(name).has_option("options", o) for o in ("workers", "db_maxconn"))


def test_each_layer_overrides_the_one_below(project: dict[str, Any]) -> None:
    live.change({"production_odoo_limit_time_real": 300})
    resources.save_project(project["id"], "production", {"odoo_workers": 2, "odoo_limit_time_real": 240}, "t")
    found = resources.layers("pr-main")
    assert found["stage"]["odoo_limit_time_real"] == 300 and found["project"]["odoo_limit_time_real"] == 240
    resources.save_branch(project["id"], "main", {"odoo_limit_time_real": 200}, "t")
    resources.save_overrides("pr-main", {"odoo_workers": 3}, "admin")
    limits = resources.effective("pr-main")
    assert limits["odoo_limit_time_real"] == 200 and limits["odoo_workers"] == 3
    sources = resources.sources(resources.layers("pr-main"))
    assert sources["odoo_limit_time_real"] == "branch" and sources["odoo_workers"] == "instance"
    assert sources["odoo_cron_threads"] == "stage"
    assert resources.effective("pr-qa")["odoo_workers"] == 0  # another stage: the project's production values not


def test_maxima_refuse_owner_values_and_clamp_values_set_before_a_lower_maximum(project: dict[str, Any]) -> None:
    with pytest.raises(resources.Invalid, match="at most 8"):
        resources.clean({"odoo_workers": 9}, available=4, bounded=True)
    with pytest.raises(resources.Invalid, match="no limit"):
        resources.clean({"odoo_memory_limit": "0"}, available=4, bounded=True)
    assert resources.clean({"odoo_workers": 9}, available=4) == {"odoo_workers": 9}  # administrators: not bound
    resources.save_project(project["id"], "production", {"odoo_workers": 6, "odoo_memory_limit": "12g"}, "t")
    live.change({"resources_max_odoo_workers": 4, "resources_max_odoo_memory_limit": "8g"})
    limits = resources.effective("pr-main")
    assert limits["odoo_workers"] == 4 and limits["odoo_memory_limit"] == "8g"
    resources.save_overrides("pr-main", {"odoo_workers": 6}, "admin")  # an instance's own value: not clamped
    assert resources.effective("pr-main")["odoo_workers"] == 6
    live.change({"resources_max_odoo_workers": 0})  # 0: no maximum
    assert resources.clean({"odoo_workers": 50}, available=4, bounded=True) == {"odoo_workers": 50}


def test_workers_an_administrator_set_get_no_implicit_cap(project: dict[str, Any]) -> None:
    resources.save_overrides("pr-dev", {"odoo_workers": 2}, "admin")
    assert (
        "odoo_memory_limit" not in resources.effective("pr-dev")
        or resources.effective("pr-dev")["odoo_memory_limit"] == "0"
    )
    resources.save_overrides("pr-dev", {}, "admin")
    resources.save_branch(project["id"], "dev", {"odoo_workers": 2}, "t")
    assert resources.effective("pr-dev")["odoo_memory_limit"] == f"{512 + 4 * 2048}m"


def test_container_limits_are_computed_from_the_workers_unless_set() -> None:
    limits = {"odoo_workers": 2, "odoo_cron_threads": 1, "odoo_limit_memory_soft_mb": 640}
    assert resources.computed(limits, available=4) == {"odoo_memory_limit": f"{512 + 3 * 640}m", "odoo_cpu_limit": 2.0}
    assert resources.computed({"odoo_workers": 8}, available=4)["odoo_cpu_limit"] == 4.0  # never above the server
    assert resources.computed({"odoo_workers": 2, "odoo_memory_limit": "3g", "odoo_cpu_limit": 1}, available=4) == {}
    assert resources.computed({"odoo_workers": 0}, available=4) == {}
    live.change({"resources_computed_limits": False})
    assert resources.computed(limits, available=4) == {}


# ---------------------------------------------------------------- odoo.conf and compose


def test_cron_timeout_and_database_connections_reach_odoo_conf_and_the_database(project: dict[str, Any]) -> None:
    resources.save_project(
        project["id"],
        "production",
        {"odoo_workers": 2, "odoo_cron_threads": 1, "odoo_limit_time_real_cron": 900, "db_max_connections": 64},
        "t",
    )
    assert resources.write_conf("pr-main") is True
    options = dict(odoo.read_config("pr-main")["options"])
    assert options["limit_time_real_cron"] == "900"
    assert options["db_maxconn"] == str((64 - 8) // (2 + 1 + 1))  # workers + cron + the gevent server
    services = _spec("pr-main")["services"]
    assert services["db"]["command"] == ["postgres", "-c", "max_connections=64"]
    assert services["odoo"]["mem_limit"] == f"{512 + 3 * 2048}m" and services["odoo"]["cpus"] == 2.0
    assert not resources.pending("pr-main")
    resources.save_overrides("pr-main", {"odoo_workers": 0, "db_max_connections": 40}, "admin")  # threaded: one process
    assert resources.conf_options(resources.effective("pr-main"))["db_maxconn"] == "32"
    assert resources.pending("pr-main")


def test_only_the_database_connections_may_be_a_command() -> None:
    spec = _spec("pr-dev")
    spec["services"]["db"]["command"] = ["postgres", "-c", "shared_preload_libraries=evil"]
    with pytest.raises(odoo.UnsafeCompose, match="max_connections"):
        odoo.check_spec(spec)
    spec["services"]["db"].pop("command")
    spec["services"]["odoo"]["command"] = ["postgres", "-c", "max_connections=10"]
    with pytest.raises(odoo.UnsafeCompose, match="max_connections"):
        odoo.check_spec(spec)


def test_a_changed_database_command_is_drift() -> None:
    resources.save_overrides("pr-dev", {"db_max_connections": 50}, "admin")
    spec = _spec("pr-dev")
    found = integrity.record("pr-dev")
    assert found is not None
    texts = integrity.compare("pr-dev", "db", spec, found, {"Config": {"Cmd": ["postgres"]}})
    assert any(text.startswith("command") for text in texts)


# ---------------------------------------------------------------- reservation check


def test_reservations_count_limits_production_fully_and_the_others_by_the_overcommit() -> None:
    assert resources.reserved_bytes("production", {}) == int(1.5 * GB)  # the stage's reservations: 1g + 512m
    assert resources.reserved_bytes("production", {"odoo_memory_limit": "4g", "db_memory_limit": "1g"}) == 5 * GB
    assert resources.reserved_bytes("staging", {"odoo_memory_limit": "4g"}) == 2 * GB  # half of it
    live.change({"capacity_overcommit": 1})
    assert resources.reserved_bytes("development", {"odoo_memory_limit": "4g"}) == 4 * GB


def test_a_change_beyond_the_server_is_refused_and_lowering_is_always_allowed() -> None:
    too_much = {"pr-main": ("production", {"odoo_memory_limit": "16g"})}
    found = resources.plan(too_much)
    assert found["refusal"] and "12.0 GB" in found["refusal"] and found["after_bytes"] > found["before_bytes"]
    assert capacity.refusal(reservation=(20 * GB, 19 * GB)) is None  # lower than before, even if still too much
    assert capacity.refusal(reservation=(1 * GB, 2 * GB)) is None
    live.change({"resources_reservation_check": False})
    assert resources.plan(too_much)["refusal"] is None


# ---------------------------------------------------------------- API


def test_project_resources_api(admin: Client, project: dict[str, Any], started: list[Any]) -> None:
    client, headers = admin
    url = f"/api/v1/projects/{project['id']}/resources"
    data = client.get(url, headers=headers).get_json()["data"]
    assert set(data["stages"]) == {"production", "staging", "development"}
    assert data["maxima"]["odoo_workers"] == 8 and data["stages"]["production"]["quota_bytes"] == 20 * GB
    assert {i["name"] for i in data["instances"]} == set(NAMES)
    body = {"stage": "production", "limits": {"odoo_workers": 2, "odoo_limit_memory_soft_mb": 640}}
    preview = client.put(url, json={**body, "preview": True}, headers=headers).get_json()["data"]
    assert preview["changes"] == ["pr-main"] and preview["plan"]["after_bytes"] > preview["plan"]["before_bytes"]
    assert resources.project_own(projects.get(project["id"]), "production") == {}  # a preview changes nothing
    answer = client.put(url, json=body, headers=headers)
    assert answer.status_code == 400 and "confirm_production" in answer.get_json()["detail"]
    answer = client.put(url, json={**body, "confirm_production": True}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    data = answer.get_json()["data"]
    assert [call[0] for call in started] == ["pr-main"]
    assert started[0][1] is operations.apply_resources and started[0][2][2:] == (
        "project",
        (project["id"], "production"),
    )
    assert data["stages"]["production"]["own"] == {"odoo_workers": 2, "odoo_limit_memory_soft_mb": 640}
    assert data["stages"]["production"]["computed"] == ["odoo_cpu_limit", "odoo_memory_limit"]
    assert resources.effective("pr-main")["odoo_memory_limit"] == f"{512 + 4 * 640}m"
    assert (
        client.put(url, json={"stage": "production", "limits": {"odoo_workers": 20}}, headers=headers).status_code
        == 400
    )
    assert client.put(url, json={"stage": "nope", "limits": {}}, headers=headers).status_code == 400


def test_project_resources_api_refuses_what_the_server_cannot_hold(
    admin: Client, project: dict[str, Any], started: list[Any]
) -> None:
    client, headers = admin
    body = {"stage": "production", "limits": {"odoo_memory_limit": "14g"}, "confirm_production": True}
    answer = client.put(f"/api/v1/projects/{project['id']}/resources", json=body, headers=headers)
    assert answer.status_code == 409 and "can spare" in answer.get_json()["detail"]
    assert not started and resources.project_own(projects.get(project["id"]), "production") == {}


def test_branch_resources_api(admin: Client, project: dict[str, Any], started: list[Any]) -> None:
    client, headers = admin
    url = f"/api/v1/projects/{project['id']}/branches/dev/resources"
    data = client.get(url, headers=headers).get_json()["data"]
    assert data["stage"] == "development" and data["own"] == {} and [i["name"] for i in data["instances"]] == ["pr-dev"]
    answer = client.put(url, json={"limits": {"odoo_workers": 1, "odoo_limit_time_real_cron": 600}}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["data"]["sources"]["odoo_workers"] == "own"
    assert [call[0] for call in started] == ["pr-dev"] and started[0][2][2] == "branch"
    assert resources.effective("pr-dev")["odoo_limit_time_real_cron"] == 600
    assert resources.effective("pr-qa")["odoo_workers"] == 0
    answer = client.put(url, json={"limits": {}}, headers=headers)  # back to the project's values
    assert answer.get_json()["data"]["own"] == {}


def test_a_busy_instance_applies_at_its_next_start(
    admin: Client, project: dict[str, Any], started: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = admin
    monkeypatch.setattr(jobs, "unfinished", lambda name: name == "pr-qa")  # a job queued or running
    url = f"/api/v1/projects/{project['id']}/resources"
    data = client.put(url, json={"stage": "staging", "limits": {"odoo_workers": 1}}, headers=headers).get_json()["data"]
    assert data["waiting"] == ["pr-qa"] and not started


def test_only_those_with_project_resources_change_them(operator: Client, project: dict[str, Any]) -> None:
    client, headers = operator
    url = f"/api/v1/projects/{project['id']}/resources"
    assert client.get(url, headers=headers).status_code == 200
    body = {"stage": "development", "limits": {"odoo_workers": 1}}
    assert client.put(url, json=body, headers=headers).status_code == 403
    assert "projects.resources" in roles.permissions("admin") and "projects.resources" in access.PROJECT_PERMISSIONS
    assert "projects.resources" not in roles.permissions("maintainer")
    assert "projects.resources" in access.LEVEL_PERMISSIONS["admin"]


def test_the_apply_job_puts_the_projects_previous_values_back(
    project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(operations.backups, "create", lambda job, name, kind: [])
    monkeypatch.setattr(operations, "_paused", lambda name: False)

    def start(job: jobs.Job, name: str, restart: bool = False, strict: bool = False) -> None:
        if strict:
            raise JobError("Odoo did not answer after it was started")

    monkeypatch.setattr(operations, "_start_odoo", start)
    resources.save_project(project["id"], "production", {"odoo_workers": 4}, "t")
    job = jobs.Job("pr-main", "Apply resources", "restart", "admin")
    with pytest.raises(JobError, match="put back"):
        operations.apply_resources(job, "pr-main", {"odoo_workers": 1}, "project", (project["id"], "production"))
    assert resources.project_own(projects.get(project["id"]), "production") == {"odoo_workers": 1}


# ---------------------------------------------------------------- storage


def test_storage_measurements_are_parsed() -> None:
    assert disk_use.parse_database_size("12345678\n") == 12345678
    assert disk_use.parse_database_size("WARNING: x\n42") == 42
    assert disk_use.parse_database_size("ERROR: database does not exist") is None
    assert disk_use.parse_du("5242880\t/var/lib/odoo/filestore/db\n") == 5242880
    assert disk_use.parse_du("du: cannot access") is None
    assert disk_use.parse_du("") is None


def test_storage_is_measured_hourly_and_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def capture(command: list[str]) -> str:
        calls.append(command)
        return "1000\n" if "psql" in command else "500\t/var/lib/odoo/filestore/pr_dev\n"

    monkeypatch.setattr(disk_use, "capture", capture)
    assert disk_use.current("pr-dev") == {"db": None, "files": None, "at": None}  # not running: not measured
    found = disk_use.current("pr-dev", db_running=True, odoo_running=True)
    assert (found["db"], found["files"]) == (1000, 500) and len(calls) == 2
    assert calls[1][-1] == "/var/lib/odoo/filestore/pr_dev"
    disk_use.current("pr-dev", db_running=True, odoo_running=True)
    assert len(calls) == 2  # not due before STORAGE_MEASURE_MINUTES
    assert disk_use.usage("pr-dev")["total_bytes"] == 1500


def test_quota_levels() -> None:
    storage().put(disk_use.COLLECTION, "pr-dev", {"db": 3 * GB, "files": GB, "at": time.time()})
    found = disk_use.usage("pr-dev")
    assert found["quota_bytes"] == 5 * GB and found["percent"] == 80.0 and found["level"] == "warning"
    assert disk_use.level(6 * GB, 5 * GB) == "over" and disk_use.level(GB, 5 * GB) == "ok"
    assert disk_use.level(GB, 0) == "none" and disk_use.level(None, 5 * GB) == "none"
    totals = disk_use.totals(list(NAMES))
    assert totals["by_stage"]["development"] == 4 * GB and totals["total_bytes"] == 4 * GB


def test_storage_alerts_at_the_warning_and_over_the_quota_production_every_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(watch, "send", lambda alert, now=None: sent.append(alert.title))
    monkeypatch.setattr(watch, "recover", lambda key, alert, now=None: sent.append(alert.title))
    monkeypatch.setattr(watch, "close", lambda key, now=None: None)
    assert alerts.TYPES["storage"] == "alert_storage"
    now = time.time()
    storage().put(disk_use.COLLECTION, "pr-main", {"db": 17 * GB, "files": 0, "at": now})
    watch._check_storage("pr-main", now)
    assert sent == ["pr-main is near its storage quota"]
    storage().put(disk_use.COLLECTION, "pr-main", {"db": 21 * GB, "files": 0, "at": now})
    watch._check_storage("pr-main", now + 60)
    watch._check_storage("pr-main", now + 120)
    assert sent[1:] == ["pr-main is over its storage quota"]  # once
    watch._check_storage("pr-main", now + 60 + watch.DAY)
    assert sent[2:] == ["pr-main is over its storage quota"]  # production: again a day later
    storage().put(disk_use.COLLECTION, "pr-main", {"db": GB, "files": 0, "at": now})
    watch._check_storage("pr-main", now + 2 * watch.DAY)
    assert sent[3:] == ["pr-main is back under its storage quota"]


def test_project_resources_apply_goes_through_the_real_hold_and_a_refusal_in_its_write_waits(
    admin: Client, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real jobs.start and record_hold (no starter stub): a refusal inside the hold's write leaves the instance
    waiting, with no hold and no job; with room it starts and holds."""
    import docrun
    from sudoo.core import slots

    client, headers = admin
    ran: list[str] = []
    monkeypatch.setattr(operations, "apply_resources", lambda job, name, *a, **k: ran.append(name))
    url = f"/api/v1/projects/{project['id']}/resources"
    monkeypatch.setattr(capacity, "admission_refusal", docrun.passes_then_full())
    try:
        data = client.put(url, json={"stage": "staging", "limits": {"odoo_workers": 1}}, headers=headers).get_json()[
            "data"
        ]
        assert (
            data["waiting"] == ["pr-qa"]
            and data["jobs"] == []
            and not any(h["instance"] == "pr-qa" for h in slots.snapshot().held.values())
        )
        monkeypatch.undo()
        docrun.reset()
        monkeypatch.setattr(operations, "apply_resources", lambda job, name, *a, **k: ran.append(name))
        data = client.put(url, json={"stage": "staging", "limits": {"odoo_workers": 2}}, headers=headers).get_json()[
            "data"
        ]
        assert [j["instance"] for j in data["jobs"]] == ["pr-qa"] and data["waiting"] == []
        assert any(h["instance"] == "pr-qa" for h in slots.snapshot().held.values())
    finally:
        jobs._HELD.clear()
        docrun.reset()


def test_rebuild_goes_through_the_real_hold_and_a_refusal_in_its_write_is_a_403(
    admin: Client, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import docrun
    from sudoo.core import slots
    from sudoo.projects import staging
    from sudoo.web.v1 import instances as instances_api
    from sudoo.web.v1 import projects as projects_api

    client, headers = admin
    monkeypatch.setattr(instances_api, "refuse_unlinked", lambda *a, **k: None)
    monkeypatch.setattr(staging, "rebuild_refusal", lambda name: None)
    monkeypatch.setattr(projects_api, "_refuse_locked_production", lambda *a, **k: None)
    monkeypatch.setattr(staging, "rebuild", lambda job, name: None)
    url = f"/api/v1/projects/{project['id']}/branches/qa/rebuild"
    try:
        check = docrun.passes_then_full()
        monkeypatch.setattr(instances_api, "admit_start", lambda name, what: check(name))
        answer = client.post(url, json={}, headers=headers)
        assert answer.status_code == 403, answer.get_data(as_text=True)
        assert not any(h["instance"] == "pr-qa" for h in slots.snapshot().held.values())
        monkeypatch.undo()
        docrun.reset()
        monkeypatch.setattr(instances_api, "refuse_unlinked", lambda *a, **k: None)
        monkeypatch.setattr(staging, "rebuild_refusal", lambda name: None)
        monkeypatch.setattr(projects_api, "_refuse_locked_production", lambda *a, **k: None)
        monkeypatch.setattr(staging, "rebuild", lambda job, name: None)
        answer = client.post(url, json={}, headers=headers)
        assert answer.status_code == 202, answer.get_data(as_text=True)
        assert any(h["instance"] == "pr-qa" for h in slots.snapshot().held.values())
    finally:
        jobs._HELD.clear()
        docrun.reset()
