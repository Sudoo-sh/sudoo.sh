"""The code profiler (instances/profiler.py, web/v1/profiler.py): who may record, production rules, one recording per
instance, the sidecar's docker command, the speedscope file without command lines, the flame graph's call tree,
keeping PROFILER_KEEP recordings, downloads and deletions, and the audit log. Docker never runs: the docker command
line is replaced by a fake that answers like Docker would (a real run is described in docs/ADMIN_GUIDE.md)."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from conftest import sign_in
from sudoo import accounts, roles
from sudoo.config import live
from sudoo.core import audit
from sudoo.instances import monitor, odoo, profiler, store

DEV, PROD = "profbox", "profprod"
SECRET = "--db_password s3cret-value"


def speedscope() -> dict[str, Any]:
    """A small speedscope file as py-spy writes it: two processes, three threads."""
    main = f'process 7:"/usr/bin/python3 /usr/bin/odoo {SECRET}"'
    worker = f'process 9:"/usr/bin/python3 /usr/bin/odoo {SECRET}"'
    frames = [
        {"name": main, "file": None, "line": None},
        {"name": worker, "file": None, "line": None},
        {"name": "Thread._bootstrap", "file": "/usr/lib/python3.12/threading.py", "line": 1030},
        {"name": "Dispatcher.dispatch", "file": "/usr/lib/python3/dist-packages/odoo/http.py", "line": 2000},
        {"name": "Model.search", "file": "/usr/lib/python3/dist-packages/odoo/models.py", "line": 1500},
        {"name": "Cursor.execute", "file": "/usr/lib/python3/dist-packages/odoo/sql_db.py", "line": 330},
    ]
    return {
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "name": f"py-spy {SECRET}",
        "shared": {"frames": frames},
        "profiles": [
            {"type": "sampled", "name": 'Process 7 Thread 7 "MainThread"', "unit": "seconds", "startValue": 0,
             "endValue": 2.5, "samples": [[0, 2]], "weights": [0.01]},
            {"type": "sampled", "name": 'Process 9 Thread 11 "Worker WorkerHTTP (9) workthread"', "unit": "seconds",
             "startValue": 0, "endValue": 3.0, "samples": [[0, 1, 2, 3, 4, 5]] * 6 + [[0, 1, 2, 3, 4]] * 3,
             "weights": [0.01] * 9},
            {"type": "sampled", "name": 'Process 9 Thread 12 "Worker WorkerHTTP (9) workthread"', "unit": "seconds",
             "startValue": 0, "endValue": 1.0, "samples": [], "weights": []},
        ],
    }  # fmt: skip


class FakeDocker:
    """Answers the docker commands the profiler runs; records them."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.image = False
        self.running = True
        self.labels_ok = True
        self.exit_code = "0"
        self.profile: dict[str, Any] | None = speedscope()
        self.on_wait: Callable[[], None] | None = None

    def __call__(self, args: list[str], timeout: float = 60, stdin: str | None = None) -> Any:
        self.calls.append(args)

        def answer(code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(["docker", *args], code, out, err)

        match args[0]:
            case "image":
                return answer(0 if self.image else 1)
            case "build":
                assert stdin is not None and "--require-hashes" in stdin
                self.image = True
                return answer()
            case "container":
                name = args[-1]
                instance = name.removeprefix("odoo-").removesuffix("-odoo-1")
                found = {
                    "Name": f"/{name}",
                    "Config": {"Labels": {odoo.LABEL_MANAGED: "true", odoo.LABEL_INSTANCE: instance,
                                          odoo.LABEL_SERVICE: "odoo" if self.labels_ok else "db"}},
                    "State": {"Running": self.running},
                    "HostConfig": {},
                }  # fmt: skip
                return answer(out=json.dumps([found]))
            case "wait":
                if self.on_wait:
                    self.on_wait()
                return answer(out=self.exit_code + "\n")
            case "cp":
                if self.profile is None:
                    return answer(1, err="Could not find the file /out/profile.json")
                Path(args[-1]).write_text(json.dumps(self.profile))
                return answer()
            case "logs":
                return answer(out="py-spy> Wrote speedscope file\n")
        return answer()


def _instance(name: str, stage: str) -> None:
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(name, {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": f"{name}_db", "STAGE": stage})
    monitor.STATUS[name] = {"running": True, "healthy": True}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeDocker]:
    docker = FakeDocker()
    monkeypatch.setattr(profiler, "docker", docker)
    monkeypatch.setattr(profiler, "_thread", lambda fn: fn())  # the recording runs at once
    _instance(DEV, "development")
    _instance(PROD, "production")
    yield docker
    for name in (DEV, PROD):
        shutil.rmtree(store.folder(name), ignore_errors=True)
        shutil.rmtree(profiler.folder(name), ignore_errors=True)
        monitor.STATUS.pop(name, None)
        profiler.RUNS.pop(name, None)
        profiler.LAST_ERROR.pop(name, None)
    live.change(dict.fromkeys(live.overrides()))


Client = Any


@pytest.fixture(scope="module")
def viewer(app: Flask) -> Client:
    accounts.create("profviewer", "viewer", "tundra-velvet-crane-64")
    return sign_in(app, "profviewer", "tundra-velvet-crane-64")


@pytest.fixture(scope="module")
def maintainer(app: Flask) -> Client:
    accounts.create("profmaint", "maintainer", "harbor-quartz-melon-59")
    return sign_in(app, "profmaint", "harbor-quartz-melon-59")


def _post(client: Client, path: str, body: dict[str, Any] | None = None) -> Any:
    flask_client, headers = client
    return flask_client.post(f"/api/v1{path}", json=body or {}, headers=headers)


def _get(client: Client, path: str) -> Any:
    return client[0].get(f"/api/v1{path}")


# ---------------------------------------------------------------- permissions


def test_who_may_profile() -> None:
    assert roles.PROFILE in roles.permissions("super_admin") and roles.PROFILE in roles.permissions("admin")
    assert roles.PROFILE in roles.permissions("maintainer")  # PROFILER_ROLES=maintainers by default
    assert roles.PROFILE not in roles.permissions("developer") and roles.PROFILE not in roles.permissions("viewer")
    live.change({"profiler_roles": "developers"})
    try:
        assert roles.PROFILE in roles.permissions("developer") and roles.PROFILE not in roles.permissions("viewer")
        live.change({"profiler_roles": "admins"})
        assert roles.PROFILE not in roles.permissions("maintainer")
        live.change({"profiler_enabled": False, "profiler_roles": None})
        assert roles.PROFILE not in roles.permissions("super_admin")
    finally:
        live.change({"profiler_roles": None, "profiler_enabled": None})


def test_a_viewer_cannot_profile(fake: FakeDocker, viewer: Client) -> None:
    assert _get(viewer, f"/instances/{DEV}/profiler").status_code == 403
    assert _post(viewer, f"/instances/{DEV}/profiler", {"minutes": 1}).status_code == 403
    assert not fake.calls


# ---------------------------------------------------------------- recording


def test_record_read_download_delete(fake: FakeDocker, maintainer: Client, admin: Client) -> None:
    answer = _post(maintainer, f"/instances/{DEV}/profiler", {"minutes": 2})
    assert answer.status_code == 202, answer.get_json()
    assert answer.get_json()["data"]["minutes"] == 2
    built = [c for c in fake.calls if c[0] == "build"]
    assert built and built[0][built[0].index("--tag") + 1] == profiler.BUILT_IMAGE
    run = next(c for c in fake.calls if c[0] == "run")
    assert f"--pid=container:{store.container(DEV, 'odoo')}" in run
    assert run[run.index("--cap-add") + 1] == "SYS_PTRACE" and run[run.index("--cap-drop") + 1] == "ALL"
    assert run[run.index("--network") + 1] == "none" and "no-new-privileges" in run
    assert run[-2:] == ["120", "100"]  # seconds, samples per second
    assert ["rm", "--force", profiler.sidecar_name(DEV)] in fake.calls  # the sidecar is removed

    state = _get(maintainer, f"/instances/{DEV}/profiler").get_json()["data"]
    assert state["current"] is None and state["max_minutes"] == 5 and len(state["recordings"]) == 1
    recording = state["recordings"][0]
    assert recording["samples"] == 10 and recording["threads"] == 2 and recording["user"] == "profmaint"

    read = _get(maintainer, f"/instances/{DEV}/profiles/{recording['id']}").get_json()["data"]
    tree = read["tree"]
    assert tree["total"] == 10 and tree["seconds"] == 3.0
    worker = next(c for c in tree["root"]["c"] if c["n"].startswith("Worker"))
    assert worker["v"] == 9
    assert worker["c"][0]["n"] == "Thread._bootstrap"
    assert tree["top"][0] == {"name": "Cursor.execute", "where": "odoo/sql_db.py:330", "self": 6, "total": 6}

    download = maintainer[0].get(f"/api/v1/instances/{DEV}/profiles/{recording['id']}/download")
    assert download.status_code == 200 and "attachment" in download.headers["Content-Disposition"]
    body = download.get_data(as_text=True)
    assert "s3cret" not in body and "process 9 (odoo)" in body  # command lines (passwords) never kept

    events = [e["event"] for e in audit.latest(20, instance=DEV)]
    assert {"profiler started", "profile recorded", "profile downloaded"} <= set(events)

    flask_client, headers = admin
    gone = flask_client.delete(f"/api/v1/instances/{DEV}/profiles/{recording['id']}", headers=headers)
    assert gone.status_code == 204
    assert _get(admin, f"/instances/{DEV}/profiles/{recording['id']}").status_code == 404
    assert "profile deleted" in [e["event"] for e in audit.latest(5, instance=DEV)]


def test_minutes_are_limited(fake: FakeDocker, admin: Client) -> None:
    assert _post(admin, f"/instances/{DEV}/profiler", {"minutes": 6}).status_code == 400
    assert _post(admin, f"/instances/{DEV}/profiler", {"minutes": "2"}).status_code == 400
    live.change({"profiler_max_minutes": 10})
    assert _post(admin, f"/instances/{DEV}/profiler", {"minutes": 6}).status_code == 202


def test_one_recording_per_instance(fake: FakeDocker, admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting: list[Callable[[], None]] = []
    monkeypatch.setattr(profiler, "_thread", waiting.append)  # the recording does not finish
    assert _post(admin, f"/instances/{DEV}/profiler", {"minutes": 1}).status_code == 202
    again = _post(admin, f"/instances/{DEV}/profiler", {"minutes": 1})
    assert again.status_code == 409 and "being recorded" in again.get_json()["detail"]
    state = _get(admin, f"/instances/{DEV}/profiler").get_json()["data"]
    assert state["current"]["phase"] == "preparing"
    stopped: list[Any] = []
    fake.on_wait = lambda: stopped.append(_post(admin, f"/instances/{DEV}/profiler/stop"))  # stopped while recording
    waiting.pop()()
    assert stopped[0].status_code == 202 and stopped[0].get_json()["data"]["stopping"]
    assert ["kill", "--signal", "INT", profiler.sidecar_name(DEV)] in fake.calls
    assert profiler.recordings(DEV)[0]["stopped_early"] is True  # py-spy wrote what it had
    assert _post(admin, f"/instances/{DEV}/profiler/stop").status_code == 409


def test_stopping_before_the_recording_starts(fake: FakeDocker, admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting: list[Callable[[], None]] = []
    monkeypatch.setattr(profiler, "_thread", waiting.append)
    assert _post(admin, f"/instances/{DEV}/profiler", {"minutes": 1}).status_code == 202
    assert _post(admin, f"/instances/{DEV}/profiler/stop").status_code == 202
    waiting.pop()()
    assert not [c for c in fake.calls if c[0] == "run"] and not profiler.recordings(DEV) and DEV not in profiler.RUNS


def test_production_needs_a_confirmation(fake: FakeDocker, admin: Client) -> None:
    refused = _post(admin, f"/instances/{PROD}/profiler", {"minutes": 1})
    assert refused.status_code == 400 and "confirm_production" in refused.get_json()["detail"]
    assert _post(admin, f"/instances/{PROD}/profiler", {"minutes": 1, "confirm_production": True}).status_code == 202
    assert any("production, confirmed" in (e.get("detail") or "") for e in audit.latest(5, instance=PROD))
    live.change({"profiler_production": False})
    off = _post(admin, f"/instances/{PROD}/profiler", {"minutes": 1, "confirm_production": True})
    assert off.status_code == 403


def test_not_running_and_foreign_containers(fake: FakeDocker, admin: Client) -> None:
    monitor.STATUS[DEV] = {"running": False, "healthy": False}
    assert _post(admin, f"/instances/{DEV}/profiler", {"minutes": 1}).status_code == 409
    monitor.STATUS[DEV] = {"running": True, "healthy": True}
    fake.labels_ok = False  # the container was not made by the panel for this instance
    assert _post(admin, f"/instances/{DEV}/profiler", {"minutes": 1}).status_code == 202
    state = _get(admin, f"/instances/{DEV}/profiler").get_json()["data"]
    assert state["last_error"]["code"] == "not-ours" and not state["recordings"]
    assert not [c for c in fake.calls if c[0] == "run"]


def test_a_failed_recording_is_reported(fake: FakeDocker, admin: Client) -> None:
    fake.exit_code = "1"
    assert _post(admin, f"/instances/{DEV}/profiler", {"minutes": 1}).status_code == 202
    state = _get(admin, f"/instances/{DEV}/profiler").get_json()["data"]
    assert state["last_error"]["code"] == "failed" and not state["recordings"]
    assert "profile failed" in [e["event"] for e in audit.latest(3, instance=DEV)]


def test_only_the_last_recordings_are_kept(fake: FakeDocker, admin: Client) -> None:
    live.change({"profiler_keep": 2})
    made = []
    for _ in range(3):
        _post(admin, f"/instances/{DEV}/profiler", {"minutes": 1})
        made.append(profiler.recordings(DEV)[0]["id"])
    kept = [r["id"] for r in profiler.recordings(DEV)]
    assert len(kept) == 2 and made[0] not in kept
    assert len(list(profiler.folder(DEV).iterdir())) == 6  # meta, speedscope, tree of each


def test_a_configured_image_is_pulled(fake: FakeDocker, admin: Client) -> None:
    live.change({"profiler_image": "registry.example.com/tools/py-spy:0.4.3"})
    assert _post(admin, f"/instances/{DEV}/profiler", {"minutes": 1}).status_code == 202
    assert ["pull", "--", "registry.example.com/tools/py-spy:0.4.3"] in fake.calls
    assert not [c for c in fake.calls if c[0] == "build"]


def test_recording_ids_are_checked(fake: FakeDocker, admin: Client) -> None:
    assert _get(admin, f"/instances/{DEV}/profiles/..%2Fsecret").status_code == 404
    assert _get(admin, f"/instances/{DEV}/profiles/20260101-000000-abcdef").status_code == 404


def test_the_dockerfile_pins_py_spy() -> None:
    assert f"py-spy=={profiler.PY_SPY_VERSION}" in profiler.DOCKERFILE
    assert all(f"--hash=sha256:{h}" in profiler.DOCKERFILE for h in profiler.PY_SPY_HASHES)
    assert profiler.DOCKERFILE.startswith(f"FROM {profiler.BASE_IMAGE}")


def test_the_tree_is_pruned() -> None:
    data = speedscope()
    data["profiles"][1]["samples"] = [[0, 1, 2, 3, 4, 5]] * 10000 + [[0, 1, 2, 4]]
    tree = profiler.summarize(profiler.sanitize(data))
    worker = next(c for c in tree["root"]["c"] if c["n"].startswith("Worker"))
    search = worker["c"][0]["c"][0]["c"][0]  # Thread._bootstrap > Dispatcher.dispatch > Model.search
    assert search["n"] == "Model.search" and len(search["c"]) == 1  # the one-sample call is below the floor
