"""The history of a project's branches (projects/builds.py): a build per commit an instance was built from, with
its status, stage changes and restores, retention, and the API that reads them. Docker never runs: a build's job
works on real git repositories (a stand-in for GitHub on disk) and writes Odoo's log lines itself."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import ROOT, sign_in
from sudoo import accounts, projects
from sudoo.config import live, settings
from sudoo.core import jobs
from sudoo.core.jobs import Job, JobError
from sudoo.core.state import STATE
from sudoo.instances import store
from sudoo.projects import access, branches, builds
from sudoo.repos import github
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
NAMES = ("bh-main", "bh-dev", "bh-qa")
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Dana Dev",
    "GIT_AUTHOR_EMAIL": "dana@example.com",
    "GIT_COMMITTER_NAME": "Dana Dev",
    "GIT_COMMITTER_EMAIL": "dana@example.com",
}
REMOTES = ROOT / "bh-remotes"


def _git(path: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True, env=GIT_ENV)
    return done.stdout.strip()


def _remote(branch: str) -> Path:
    """A repository standing in for GitHub, with one commit on `branch`."""
    path = REMOTES / branch.replace("/", "-")
    if not path.exists():
        path.mkdir(parents=True)
        _git(path, "init", "-q", "-b", branch)
        (path / "README.md").write_text("first\n")
        _git(path, "add", "README.md")
        _git(path, "commit", "-q", "-m", "First commit")
    return path


def _push(branch: str, subject: str) -> str:
    """A new commit on the stand-in for GitHub; returns its sha."""
    path = _remote(branch)
    (path / "README.md").write_text(f"{subject}\n")
    _git(path, "commit", "-q", "-am", subject)
    return _git(path, "rev-parse", "HEAD")


def _instance(name: str, project_id: str, branch: str, stage: str = "development") -> None:
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    subprocess.run(["git", "clone", "-q", str(_remote(branch)), str(folder / "addons")], check=True, env=GIT_ENV)
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39700 + 2 * NAMES.index(name),
            "ODOO_PORT": 39701 + 2 * NAMES.index(name),
            "GIT_BRANCH": branch,
            "AUTO_UPDATE": "true",
            "AUTO_UPGRADE": "true",
            "AUTO_PAUSE": "true",
            "DELETE_AFTER_DAYS": "30",
            "CREATED_AT": int(time.time() - 3600),
            "STAGE": stage,
            "PROJECT": project_id,
        },
    )


def _project() -> str:
    project = projects.create(repository="Acme/Books", odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    return str(project["id"])


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(github, "heads", lambda address: ({"main": "a" * 40, "feature": "b" * 40}, "main", None))
    # the build history follows jobs only in this module (the panel starts it in background.start)
    monkeypatch.setattr(jobs, "_START_LISTENERS", [builds.job_started])
    monkeypatch.setattr(jobs, "_END_LISTENERS", [builds.job_ended])
    yield
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
    shutil.rmtree(REMOTES, ignore_errors=True)
    for key in list(storage().all(builds.COLLECTION)):
        storage().delete(builds.COLLECTION, key)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    for key in list(branches._heads):
        branches.forget(key)
    live.change(dict.fromkeys(live.overrides()))


def _run(name: str, fn: Callable[[Job], None], trigger: str | None = "push", kind: str = "update",
         user: str = "system") -> Job:  # fmt: skip
    """A job through jobs.start, waited for."""
    job = jobs.start(name, "Automatic update: push", kind, fn, user=user,
                     details={"build": trigger} if trigger else None)  # fmt: skip
    deadline = time.time() + 30
    while job.status == "queued" and time.time() < deadline:
        time.sleep(0.01)
    with jobs.INSTANCE_LOCKS[name]:  # held until the job and its end listeners are done
        pass
    assert job.finished is not None
    return job


def _pull(name: str) -> Callable[[Job], None]:
    def pull(job: Job) -> None:
        job.run(["git", "-C", str(store.addons_dir(name)), "pull", "-q", "--ff-only"])

    return pull


def _builds(project_id: str, branch: str = "main") -> list[dict[str, Any]]:
    return [e for e in builds.history(project_id, branch) if e["type"] == "build"]


# ---------------------------------------------------------------- recording builds


def test_a_push_records_a_build_with_its_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    pid = _project()
    _instance("bh-main", pid, "main")
    sha = _push("main", "Add the invoice report")
    seen: list[str] = []

    def pull(job: Job) -> None:
        seen.extend(e["status"] for e in _builds(pid))  # while it runs: building
        _pull("bh-main")(job)

    job = _run("bh-main", pull)
    assert seen == ["building"]
    [build] = _builds(pid)
    assert build["status"] == "success" and build["trigger"] == "push" and build["job"] == job.id
    assert build["instance"] == "bh-main" and build["by"] == "system"
    assert build["commit"]["full_sha"] == sha and build["commit"]["subject"] == "Add the invoice report"
    assert build["commit"]["author"] == "Dana Dev"
    assert build["finished_at"] >= build["started_at"] >= build["queued_at"]
    assert build["result"] == builds.DONE["push"]


def test_an_update_with_nothing_new_is_no_build() -> None:
    pid = _project()
    _instance("bh-main", pid, "main")
    _run("bh-main", _pull("bh-main"))
    assert _builds(pid) == []
    # a job that builds nothing (a restart) records nothing either
    _run("bh-main", lambda job: None, trigger=None, kind="restart")
    assert builds.history(pid, "main") == []


def test_warnings_and_errors_in_odoos_log_make_a_warning() -> None:
    pid = _project()
    _instance("bh-main", pid, "main")
    _push("main", "Change a view")

    def update(job: Job) -> None:
        _pull("bh-main")(job)
        job.write("2026-11-22 10:00:00,123 7 INFO bh_main odoo.modules.loading: 42 modules loaded\n")
        job.write("2026-11-22 10:00:01,456 7 WARNING bh_main odoo.modules.loading: The model x has no access rules\n")
        job.write("2026-11-22 10:00:02,789 7 ERROR bh_main odoo.addons.base.models.ir_ui_view: Invalid view\n")

    _run("bh-main", update)
    [build] = _builds(pid)
    assert build["status"] == "warning"
    assert build["result"] == "1 warning and 1 error in Odoo's log while loading the modules"
    assert build["messages"][0].startswith("WARNING odoo.modules.loading: The model x")

    # a warning note (modules that could not be upgraded) is a warning too
    _push("main", "Another change")

    def noted(job: Job) -> None:
        _pull("bh-main")(job)
        job.note("Some of the repository's modules could not be upgraded.", "warning")

    _run("bh-main", noted)
    assert _builds(pid)[0]["status"] == "warning" and "could not be upgraded" in _builds(pid)[0]["result"]


def test_a_failed_build_keeps_the_previous_one_live() -> None:
    pid = _project()
    _instance("bh-main", pid, "main")
    _push("main", "Good commit")
    _run("bh-main", _pull("bh-main"))
    bad = _push("main", "Broken commit")

    def fails(job: Job) -> None:
        job.run(["git", "-C", str(store.addons_dir("bh-main")), "fetch", "-q", "origin"])
        raise JobError("Odoo did not start")  # the update was put back: the instance still runs "Good commit"

    _run("bh-main", fails)
    newest, previous = _builds(pid)
    assert newest["status"] == "failed" and newest["result"]
    assert newest["commit"]["full_sha"] == bad  # the commit it tried, not the one it was put back to
    assert previous["status"] == "success"


def test_a_newer_build_or_a_deletion_drops_the_older_one() -> None:
    pid = _project()
    _instance("bh-main", pid, "main")
    _instance("bh-dev", pid, "main")  # another instance of the branch keeps its own live build
    _push("main", "One")
    _run("bh-main", _pull("bh-main"))
    _run("bh-dev", _pull("bh-dev"))
    _push("main", "Two")
    _run("bh-main", _pull("bh-main"))
    statuses = [(b["instance"], b["commit"]["subject"], b["status"]) for b in _builds(pid)]
    assert statuses == [("bh-main", "Two", "success"), ("bh-dev", "One", "success"), ("bh-main", "One", "dropped")]
    assert _builds(pid)[2]["dropped_at"]

    _run("bh-dev", lambda job: None, trigger=None, kind="delete")
    assert [b["status"] for b in _builds(pid)] == ["success", "dropped", "dropped"]


def test_builds_cut_off_by_a_restart_are_dropped() -> None:
    pid = _project()
    _instance("bh-main", pid, "main")
    job = Job("bh-main", "Get latest code", "update", "admin1")
    job.details = {"build": "manual"}
    builds.job_started(job)  # its end never comes: the panel restarted
    builds._started.clear()
    builds._drop_building(lambda key: True, None)
    [build] = _builds(pid)
    assert build["status"] == "dropped" and build["result"] == builds.CUT_OFF


def test_a_restore_is_recorded_in_the_timeline() -> None:
    pid = _project()
    _instance("bh-main", pid, "main")
    job = _run("bh-main", lambda job: None, trigger=None, kind="restore")
    [entry] = builds.history(pid, "main")
    assert entry["type"] == "restore" and entry["status"] == "success" and entry["job"] == job.id


def test_retention_keeps_the_last_builds_of_each_branch() -> None:
    live.change({"project_builds_kept": 3})
    pid = _project()
    _instance("bh-main", pid, "main")
    builds.stage_changed(pid, "main", "development", "staging", "admin1")  # older than every kept build: removed
    for number in range(5):
        _push("main", f"Commit {number}")
        _run("bh-main", _pull("bh-main"))
    kept = _builds(pid)
    assert [b["commit"]["subject"] for b in kept] == ["Commit 4", "Commit 3", "Commit 2"]
    assert all(e["type"] == "build" for e in builds.history(pid, "main"))
    assert settings.project_builds_kept == 3


# ---------------------------------------------------------------- stage changes


def test_stage_changes_are_timeline_entries(admin: Client) -> None:
    client, headers = admin
    pid = _project()
    _instance("bh-main", pid, "main")
    _instance("bh-dev", pid, "main")  # two instances move: one entry for the branch
    url = f"/api/v1/projects/{pid}/branches/main/stage"
    answer = client.put(url, json={"stage": "staging"}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    [entry] = builds.history(pid, "main")
    assert (entry["type"], entry["from"], entry["to"], entry["by"]) == ("stage", "development", "staging", "admin1")
    assert entry["explanation"].startswith("Staging:")
    # the same stage again changes nothing
    assert client.put(url, json={"stage": "staging"}, headers=headers).status_code == 200
    assert len(builds.history(pid, "main")) == 1

    # through an instance's own stage
    _instance("bh-qa", pid, "feature")
    answer = client.patch("/api/v1/instances/bh-qa", json={"stage": "staging"}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    [entry] = builds.history(pid, "feature")
    assert (entry["from"], entry["to"]) == ("development", "staging") and "bh-qa" in entry["reason"]


# ---------------------------------------------------------------- the API


def test_the_builds_and_history_endpoints(admin: Client) -> None:
    client, _ = admin
    pid = _project()
    _instance("bh-main", pid, "main")
    _push("main", "One")
    first = _run("bh-main", _pull("bh-main"))
    _push("main", "Two")
    _run("bh-main", _pull("bh-main"))
    builds.stage_changed(pid, "main", "development", "staging", "admin1")

    listed = client.get(f"/api/v1/projects/{pid}/builds?per_branch=1")
    assert listed.status_code == 200, listed.get_json()
    [branch] = listed.get_json()["data"]
    assert branch["branch"] == "main" and len(branch["builds"]) == 1
    newest = branch["builds"][0]
    assert newest["commit"]["subject"] == "Two" and newest["status"] == "success" and newest["type"] == "build"
    assert newest["duration_seconds"] is not None and newest["links"]["job"].endswith(newest["job"])
    assert listed.get_json()["meta"] == {"per_branch": 1, "kept_per_branch": 200}
    assert client.get(f"/api/v1/projects/{pid}/builds?per_branch=0").status_code == 400

    timeline = client.get(f"/api/v1/projects/{pid}/branches/main/history?limit=2")
    body = timeline.get_json()
    assert [e["type"] for e in body["data"]] == ["stage", "build"] and body["meta"]["total"] == 3
    assert body["data"][0]["from"] == "development" and body["data"][0]["to"] == "staging"
    rest = client.get(f"/api/v1/projects/{pid}/branches/main/history?limit=2&offset=2").get_json()
    assert [e["status"] for e in rest["data"]] == ["dropped"] and rest["data"][0]["job"] == first.id

    one = client.get(f"/api/v1/projects/{pid}/builds/{newest['id']}")
    assert one.status_code == 200 and one.get_json()["data"]["id"] == newest["id"]
    assert client.get(f"/api/v1/projects/{pid}/builds/000000000000").status_code == 404
    assert client.get("/api/v1/projects/nope/builds").status_code == 404
    assert client.get(f"/api/v1/projects/{pid}/branches/feature/history").get_json()["data"] == []


def test_reading_builds_follows_project_access(app: Flask, admin: Client) -> None:
    pid = _project()
    _instance("bh-main", pid, "main")
    _instance("bh-dev", pid, "feature")
    for name, branch in (("bh-main", "main"), ("bh-dev", "feature")):
        _push(branch, f"Work on {branch}")
        _run(name, _pull(name))
    build_id = _builds(pid, "main")[0]["id"]

    # signed out: refused
    assert app.test_client().get(f"/api/v1/projects/{pid}/builds").status_code == 401

    if accounts.get("bhviewer") is None:
        accounts.create("bhviewer", "viewer", "willow-cobalt-ember-36")
    client, _ = sign_in(app, "bhviewer", "willow-cobalt-ember-36")
    assert {b["branch"] for b in client.get(f"/api/v1/projects/{pid}/builds").get_json()["data"]} == {
        "main",
        "feature",
    }
    restricted = {"branches": {"main": {"restricted": True, "grants": [{"type": "user", "id": "admin1",
                                                                         "level": "admin"}]}}}  # fmt: skip
    projects.change(pid, lambda p: {**p, "access": restricted})
    access.forget_restrictions()
    try:
        listed = client.get(f"/api/v1/projects/{pid}/builds").get_json()["data"]
        assert [b["branch"] for b in listed] == ["feature"]
        assert client.get(f"/api/v1/projects/{pid}/builds/{build_id}").status_code == 404
        assert client.get(f"/api/v1/projects/{pid}/branches/main/history").status_code == 404
        # the live event of a hidden branch is not sent to them
        from sudoo.core.events import Event
        from sudoo.web.v1 import events as stream
        from sudoo.web.v1.projects import build_event

        data = build_event(build_id)
        assert data is not None and data["branch"] == "main"
        user = {"name": "bhviewer", "role": "viewer"}
        assert stream.for_caller(user, Event(1, "build", build_id, data), data) is None
        assert admin[0].get(f"/api/v1/projects/{pid}/builds/{build_id}").status_code == 200
    finally:
        projects.change(pid, lambda p: {**p, "access": {}})
        access.forget_restrictions()


def test_deleting_a_project_removes_its_history(admin: Client) -> None:
    client, headers = admin
    pid = _project()
    builds.stage_changed(pid, "main", "development", "production", "admin1")
    assert builds.histories(pid)
    answer = client.delete(f"/api/v1/projects/{pid}", json={"confirm": pid}, headers=headers)
    assert answer.status_code == 204, answer.get_json()
    assert builds.histories(pid) == {}
