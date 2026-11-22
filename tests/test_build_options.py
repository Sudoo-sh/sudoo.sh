"""Build options of a project's branches (projects/build_options.py), the tests of a build (projects/testing.py) and
commit statuses on GitHub (projects/commit_status.py). Docker, Odoo and GitHub never run: jobs are recorded, Odoo's
logs are sample files (tests/odoo_test_logs/), and GitHub's API is a recording stand-in."""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from pydantic import ValidationError

from conftest import sign_in
from sudoo import accounts, projects, triggers
from sudoo.config import Settings, live
from sudoo.core import jobs
from sudoo.core.jobs import Job, JobError
from sudoo.core.odoo_args import modules_of_tags, parse_modules, parse_test_tags
from sudoo.core.state import STATE
from sudoo.instances import backups, monitor, operations, stages, store
from sudoo.projects import branches, build_options, builds, commit_status, staging, testing
from sudoo.repos import github, github_app
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
LOGS = Path(__file__).resolve().parent / "odoo_test_logs"
NAMES = ("bo-main", "bo-dev", "bo-stage")
REPO = "https://github.com/Acme/Books"
SHA = "c" * 40


def _instance(name: str, stage: str, branch: str, project_id: str, version: str = "18.0") -> None:
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    subprocess.run(["git", "init", "-q", str(folder / "addons")], check=True)
    subprocess.run(["git", "-C", str(folder / "addons"), "config", "remote.origin.url", REPO], check=True)
    store.write_env(
        name,
        {
            "ODOO_VERSION": version,
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39800 + 2 * NAMES.index(name),
            "ODOO_PORT": 39801 + 2 * NAMES.index(name),
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


def _project(**options: Any) -> dict[str, Any]:
    project = projects.create(repository="Acme/Books", odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    if options:
        projects.change(project["id"], lambda p: {**p, "options": options})
    return projects.get(project["id"]) or project


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(github, "heads", lambda address: ({"main": "a" * 40, "dev": SHA, "qa": "d" * 40}, "main", None))
    yield
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
        monitor.STATUS.pop(name, None)
    for key in list(storage().all(builds.COLLECTION)):
        storage().delete(builds.COLLECTION, key)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    for key in list(branches._heads):
        branches.forget(key)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any, tuple[Any, ...]]]:
    calls: list[tuple[str, Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system",
                   **_: Any) -> Job:  # fmt: skip
        calls.append((instance, fn, args))
        return Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    return calls


# ---------------------------------------------------------------- checking values


def test_module_names_and_test_tags_are_checked() -> None:
    assert parse_modules(" sale_custom, stock_custom ,sale_custom,") == ["sale_custom", "stock_custom"]
    assert parse_modules(["a1", "b_2"]) == ["a1", "b_2"]
    for bad in ("sale-custom", "--init=all", "sale custom", "../x", "_x"):
        with pytest.raises(ValueError):
            parse_modules(bad)
    with pytest.raises(ValueError):
        parse_modules(",".join(f"m{n}" for n in range(201)))
    assert parse_test_tags(" /sale_custom , -slow,post_install ") == "/sale_custom,-slow,post_install"
    assert (
        parse_test_tags("/sale_custom:TestOrder.test_total,-/stock,*") == "/sale_custom:TestOrder.test_total,-/stock,*"
    )
    assert parse_test_tags("") == ""
    for bad in ("--db-filter=.*", "-", "/sale custom", "a;rm -rf /", "/mod:", "tag/"):
        with pytest.raises(ValueError):
            parse_test_tags(bad)
    assert modules_of_tags("/sale_custom,-/stock,post_install,/web:T") == ["sale_custom", "web"]
    # the settings refuse them too
    with pytest.raises(ValidationError):
        Settings(project_test_tags="--stop-after-init")
    with pytest.raises(ValidationError):
        Settings(project_modules="sale custom")
    assert Settings(project_modules="a, b").project_modules == "a,b"


def test_clean_values() -> None:
    assert build_options.clean("on_new_commit", "nothing") == "nothing"
    assert build_options.clean("modules", ["x", "y"]) == "x,y"
    assert build_options.clean("demo_data", None) is None
    for key, bad in (("on_new_commit", "rebuild"), ("module_install", "some"), ("test_suite", True),
                     ("demo_data", "yes"), ("nope", 1)):  # fmt: skip
        with pytest.raises(ValueError):
            build_options.clean(key, bad)


# ---------------------------------------------------------------- precedence


def test_branch_over_project_over_panel() -> None:
    project = _project()
    found = build_options.effective(project, "dev", stages.DEVELOPMENT)
    assert (
        found["on_new_commit"] == "update" and found["test_suite"] == "off" and found["module_install"] == "repository"
    )
    assert found["sources"]["on_new_commit"] == "project"
    live.change({"project_development_on_new_commit": "new_build", "project_test_suite": "selection"})
    found = build_options.effective(project, "dev", stages.DEVELOPMENT)
    assert found["on_new_commit"] == "new_build" and found["test_suite"] == "selection"
    projects.change(project["id"], lambda p: {**p, "options": {"development_on_new_commit": "update"}})
    project = projects.get(project["id"]) or project
    assert build_options.effective(project, "dev", stages.DEVELOPMENT)["on_new_commit"] == "update"
    build_options.change(project["id"], "dev", {"on_new_commit": "nothing", "demo_data": False}, "t")
    project = projects.get(project["id"]) or project
    found = build_options.effective(project, "dev", stages.DEVELOPMENT)
    assert found["on_new_commit"] == "nothing" and found["demo_data"] is False
    assert found["sources"]["on_new_commit"] == "branch" and found["sources"]["module_install"] == "project"
    # another branch keeps the project's
    assert build_options.effective(project, "feature", stages.DEVELOPMENT)["on_new_commit"] == "update"
    # null follows the project again; the branch keeps no empty options
    build_options.change(project["id"], "dev", {"on_new_commit": None, "demo_data": None}, "t")
    project = projects.get(project["id"]) or project
    assert build_options.own(project, "dev") == {} and "options" not in project["branches"]["dev"]


def test_stage_rules() -> None:
    project = _project(test_suite="selection")
    # staging follows staging_on_push until the branch chooses; its tests only with staging_tests
    staged = build_options.effective(project, "qa", stages.STAGING)
    assert staged["on_new_commit"] == "new_build" and staged["test_suite"] == "off"
    projects.change(project["id"], lambda p: {**p, "options": {**p["options"], "staging_on_push": "keep_data",
                                                                 "staging_tests": True}})  # fmt: skip
    project = projects.get(project["id"]) or project
    staged = build_options.effective(project, "qa", stages.STAGING)
    assert staged["on_new_commit"] == "update" and staged["test_suite"] == "selection"
    # production: always the safe update, never tests, whatever the branch says
    build_options.change(project["id"], "main", {"on_new_commit": "new_build", "test_suite": "custom",
                                                 "test_tags": "/x"}, "t")  # fmt: skip
    project = projects.get(project["id"]) or project
    found = build_options.effective(project, "main", stages.PRODUCTION)
    assert found["on_new_commit"] == "update" and found["test_suite"] == "off"
    assert found["sources"]["on_new_commit"] == "production"
    # every module: no tests; a list without modules and custom tests without tags fall back
    build_options.change(project["id"], "dev", {"module_install": "all"}, "t")
    project = projects.get(project["id"]) or project
    found = build_options.effective(project, "dev", stages.DEVELOPMENT)
    assert found["test_suite"] == "off" and found["sources"]["test_suite"] == "module_install"
    assert build_options.problem({"module_install": "list", "modules": ""})
    assert build_options.problem({"test_suite": "custom", "test_tags": ""})
    assert build_options.problem({"module_install": "list", "modules": "a", "test_suite": "off"}) is None


# ---------------------------------------------------------------- the API


def test_branch_options_api(app: Flask, admin: Client) -> None:
    client, headers = admin
    project = _project()
    url = f"/api/v1/projects/{project['id']}/branches/feature/x/options"
    answer = client.get(url, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    data = answer.get_json()["data"]
    assert data["branch"] == "feature/x" and data["stage"] == "development" and data["own"] == {}
    assert data["options"]["on_new_commit"] == "update" and data["commit_statuses"]["available"] is False
    assert data["commit_statuses"]["context"] == "sudoo/development"
    change = {"on_new_commit": "new_build", "module_install": "list", "modules": "sale_custom, stock_custom",
              "test_suite": "custom", "test_tags": "/sale_custom,-slow"}  # fmt: skip
    answer = client.put(url, json=change, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    data = answer.get_json()["data"]
    assert data["own"]["modules"] == "sale_custom,stock_custom" and data["options"]["test_suite"] == "custom"
    assert data["sources"]["test_tags"] == "branch" and data["changed_by"]
    for bad in ({"on_new_commit": "rebuild"}, {"modules": "--init=all"}, {"test_tags": "--db-filter=x"},
                {"nope": 1}, {}, {"modules": None}, {"test_tags": ""}):  # fmt: skip
        assert client.put(url, json=bad, headers=headers).status_code == 400, bad
    reset = client.put(url, json={"test_suite": None, "test_tags": None}, headers=headers).get_json()["data"]
    assert reset["options"]["test_suite"] == "off" and "test_tags" not in reset["own"]
    # the project's defaults go through PATCH /projects/<id>
    patch = client.patch(f"/api/v1/projects/{project['id']}",
                         json={"options": {"test_suite": "selection", "commit_statuses": False}}, headers=headers)  # fmt: skip
    assert patch.status_code == 200, patch.get_json()
    shown = patch.get_json()["data"]
    assert shown["options"]["test_suite"] == "selection" and shown["own_options"]["commit_statuses"] is False
    for bad in ({"test_suite": "custom"}, {"module_install": "list"}, {"modules": "a b"}, {"test_tags": "-"}):
        answer = client.patch(f"/api/v1/projects/{project['id']}", json={"options": bad}, headers=headers)
        assert answer.status_code == 400, bad
    other = client.get(url, headers=headers).get_json()["data"]
    assert other["project_defaults"]["test_suite"] == "selection" and other["options"]["test_suite"] == "selection"
    # reading is for those who see the project; changing needs projects.manage
    if accounts.get("boviewer") is None:
        accounts.create("boviewer", "viewer", "maple-orbit-quarry-91")
    viewer, viewer_headers = sign_in(app, "boviewer", "maple-orbit-quarry-91")
    assert viewer.get(url, headers=viewer_headers).status_code == 200
    assert viewer.put(url, json={"demo_data": True}, headers=viewer_headers).status_code == 403


# ---------------------------------------------------------------- what a new commit does


def test_behaviour_upon_new_commits(started: list[Any], admin: Client) -> None:
    project = _project()
    pid = project["id"]
    _instance("bo-main", "production", "main", pid)
    _instance("bo-dev", "development", "dev", pid)
    _instance("bo-stage", "staging", "qa", pid)
    # today's behaviour: development updates in place, staging rebuilds from production, production updates
    triggers.request_update("bo-dev", "push")
    assert started[-1][:2] == ("bo-dev", operations.update_code)
    triggers.request_update("bo-stage", "push")
    assert started[-1][:2] == ("bo-stage", staging.rebuild)
    triggers.request_update("bo-main", "push")
    assert started[-1][:2] == ("bo-main", operations.update_code)
    # new_build: development from scratch; staging's own "update" keeps its data
    build_options.change(pid, "dev", {"on_new_commit": "new_build"}, "t")
    build_options.change(pid, "qa", {"on_new_commit": "update"}, "t")
    triggers.request_update("bo-dev", "push")
    assert started[-1][:2] == ("bo-dev", testing.new_build)
    triggers.request_update("bo-stage", "push")
    assert started[-1][:2] == ("bo-stage", operations.update_code)
    # production ignores its branch's options
    build_options.change(pid, "main", {"on_new_commit": "nothing"}, "t")
    triggers.request_update("bo-main", "push")
    assert started[-1][:2] == ("bo-main", operations.update_code)
    # nothing: no job, the commit is recorded once (the webhook and polling both tell)
    build_options.change(pid, "dev", {"on_new_commit": "nothing"}, "t")
    count = len(started)
    triggers.request_update("bo-dev", "push")
    triggers.request_update("bo-dev", "new code on GitHub")
    assert len(started) == count
    entries = builds.history(pid, "dev")
    assert [e["type"] for e in entries] == ["commit"] and entries[0]["commit"]["full_sha"] == SHA
    assert entries[0]["result"] == builds.NOT_BUILT
    # the timeline lists recorded commits only when asked (the web app's timeline shows builds, stages, restores)
    client, headers = admin
    url = f"/api/v1/projects/{pid}/branches/dev/history"
    assert client.get(url, headers=headers).get_json()["data"] == []
    shown = client.get(f"{url}?commits=1", headers=headers).get_json()["data"]
    assert shown[0]["type"] == "commit" and shown[0]["status"] == "skipped" and shown[0]["commit"]["full_sha"] == SHA


def test_a_new_branch_gets_the_branchs_demo_data(started: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.limits import quotas
    from sudoo.projects import automation

    monkeypatch.setattr(quotas, "claim", lambda user: None)
    project = _project()
    projects.change(project["id"], lambda p: {**p, "seen": ["main"]})
    build_options.change(project["id"], "feature", {"demo_data": False}, "t")
    automation.branch_created("acme/books", "feature")
    plan = started[-1][2][0]
    store.release_ports(plan["ports"])
    assert plan["branch"] == "feature" and plan["demo"] is False


# ---------------------------------------------------------------- Odoo's log


@pytest.mark.parametrize(
    ("sample", "status", "words"),
    [
        ("odoo17_failed.log", "failed", "At least one test failed when loading the modules (sale_custom). 1 failed, "
                                        "0 with errors, of 14 tests."),  # fmt: skip
        ("odoo18_passed_with_warning.log", "warning", "Tests passed with 1 warning in Odoo's log."),
        ("odoo19_error.log", "failed", "At least one test failed when loading the modules (stock_custom). 0 failed, "
                                       "1 with errors, of 8 tests."),  # fmt: skip
        ("odoo19_passed.log", "success", "8 tests passed."),
        ("odoo15_failed.log", "failed", "At least one test failed when loading the modules (sale_custom). 1 failed, "
                                        "0 with errors, of 6 tests."),  # fmt: skip
    ],
)
def test_reading_odoos_test_log(sample: str, status: str, words: str) -> None:
    report = testing.parse_log((LOGS / sample).read_text())
    assert report["status"] == status
    assert report["result"] == words
    if status == "failed":
        assert any("FAIL" in m or "ERROR:" in m for m in report["messages"])


def test_reading_odoos_test_log_without_tests() -> None:
    assert testing.parse_log("", timed_out=True, minutes=30)["result"] == (
        "The tests took longer than 30 minutes and were stopped."
    )
    stopped = testing.parse_log("2026-11-22 10:00:00,000 1 INFO acme odoo.modules.loading: loading", exit_code=255)
    assert stopped["status"] == "failed" and "exit code 255" in stopped["result"]
    noisy = "2026-11-22 10:00:00,000 1 ERROR acme odoo.addons.mail.models.mail_mail: failed sending\n"
    assert testing.parse_log(noisy)["result"].startswith("At least one test failed when loading the modules (mail)")
    other = "2026-11-22 10:00:00,000 1 ERROR acme odoo.sql_db: bad query\n"
    assert testing.parse_log(other)["status"] == "success"  # not about the tests


def test_the_test_command() -> None:
    args = testing.command_args("18.0", "selection", "", ["sale_custom", "stock_custom"], "acme")
    assert args == ["-d", "acme", "--test-enable", "--stop-after-init", "--no-http", "-u", "sale_custom,stock_custom",
                    "--test-tags=/sale_custom,/stock_custom"]  # fmt: skip
    args = testing.command_args("19.0", "custom", "-slow,/web:TestUi", ["sale_custom"], "acme")
    assert args[-3:] == ["-u", "sale_custom,web", "--test-tags=-slow,/web:TestUi"]
    old = testing.command_args("15.0", "custom", "/sale_custom", ["sale_custom"], "acme")
    assert not any(a.startswith("--test-tags") for a in old) and "--test-enable" in old


def _fake_job(monkeypatch: pytest.MonkeyPatch, name: str, log: str = "", code: int = 0) -> tuple[Job, list[list[str]]]:
    job = Job(name, "Automatic update: push", "update", "system")
    job.details = {"build": "push"}
    commands: list[list[str]] = []

    def run(cmd: list[str], timeout: float | None = None, input: str | None = None) -> None:
        commands.append(cmd)

    def run_unchecked(cmd: list[str], timeout: float | None = None, input: str | None = None) -> int:
        commands.append(cmd)
        if "--test-enable" in cmd:
            job.write(log)
            if code < 0:
                raise JobError("this step took too long and was stopped")
            return code
        return 0

    monkeypatch.setattr(job, "run", run)
    monkeypatch.setattr(job, "run_unchecked", run_unchecked)
    monkeypatch.setattr(operations, "refresh_addons_path", lambda job, name: None)
    monkeypatch.setattr(testing.odoo, "wait_for_database", lambda name, timeout=30: True)
    monkeypatch.setattr(testing, "own_modules", lambda addons: ["sale_custom", "not_installed"])
    monkeypatch.setattr(backups, "installed_modules", lambda name, db: {"sale_custom", "sale"})
    return job, commands


def test_development_tests_run_on_its_database(monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(test_suite="selection")
    _instance("bo-dev", "development", "dev", project["id"])
    job, commands = _fake_job(monkeypatch, "bo-dev", (LOGS / "odoo17_failed.log").read_text(), code=1)
    report = testing.run(job, "bo-dev")
    assert report is not None and report["status"] == "failed" and report["modules"] == ["sale_custom"]
    tests = next(cmd for cmd in commands if "--test-enable" in cmd)
    assert tests[tests.index("-d") + 1] == "bo_dev" and "--test-tags=/sale_custom" in tests
    assert not any("CREATE DATABASE" in " ".join(cmd) for cmd in commands)
    assert next(cmd for cmd in commands if "stop" in cmd)[-2:] == ["stop", "odoo"]  # Odoo stopped first
    status, result, messages = builds.outcome(job, "push")
    assert status == "failed" and result.startswith("At least one test failed when loading the modules (sale_custom)")
    assert messages and "FAIL: TestOrder.test_total" in messages[0]


def test_staging_tests_run_on_a_throw_away_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(test_suite="selection", staging_tests=True)
    _instance("bo-stage", "staging", "qa", project["id"])
    job, commands = _fake_job(monkeypatch, "bo-stage", (LOGS / "odoo19_passed.log").read_text())
    report = testing.run(job, "bo-stage")
    assert report is not None and report["status"] == "success"
    text = [" ".join(cmd) for cmd in commands]
    created = next(
        i for i, c in enumerate(text) if 'CREATE DATABASE "bo_stage_tests" OWNER odoo TEMPLATE "bo_stage"' in c
    )
    ran = next(i for i, c in enumerate(text) if "--test-enable" in c)
    dropped = max(i for i, c in enumerate(text) if 'DROP DATABASE IF EXISTS "bo_stage_tests"' in c)
    assert created < ran < dropped
    assert "-d bo_stage_tests" in text[ran]
    assert any("/var/lib/odoo/filestore/bo_stage_tests" in c and "rm -rf" in c for c in text[ran:])
    status, result, _ = builds.outcome(job, "push")
    assert status == "success" and result == "Built from the pushed commit. 8 tests passed."
    # without staging_tests, staging runs none
    projects.change(project["id"], lambda p: {**p, "options": {"test_suite": "selection"}})
    job, commands = _fake_job(monkeypatch, "bo-stage")
    assert testing.run(job, "bo-stage") is None and commands == []


def test_a_timeout_fails_the_build_and_the_copy_still_goes(monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(test_suite="custom", test_tags="/sale_custom", staging_tests=True)
    _instance("bo-stage", "staging", "qa", project["id"])
    job, commands = _fake_job(monkeypatch, "bo-stage", code=-1)
    report = testing.run(job, "bo-stage")
    assert report is not None and report["status"] == "failed" and "longer than 30 minutes" in report["result"]
    assert 'DROP DATABASE IF EXISTS "bo_stage_tests" WITH (FORCE)' in " ".join(commands[-2])


def test_no_tests_outside_builds_or_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project()
    _instance("bo-dev", "development", "dev", project["id"])
    job, commands = _fake_job(monkeypatch, "bo-dev")
    testing.before_serving(job, "bo-dev")  # test_suite off
    job.details = {}
    build_options.change(project["id"], "dev", {"test_suite": "selection"}, "t")
    testing.before_serving(job, "bo-dev")  # not a build (a restart, a restore)
    assert commands == []


def test_module_installation_of_a_new_database(monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project()
    _instance("bo-dev", "development", "dev", project["id"])
    monkeypatch.setattr(testing, "own_modules", lambda addons: ["sale_custom"])
    job = Job("bo-dev", "Create", "create", "t")
    assert testing.modules_to_install(job, "bo-dev", "bo_dev") == ["sale_custom"]
    build_options.change(project["id"], "dev", {"module_install": "list", "modules": "crm,sale_custom"}, "t")
    assert testing.modules_to_install(job, "bo-dev", "bo_dev") == ["crm", "sale_custom"]
    build_options.change(project["id"], "dev", {"module_install": "all"}, "t")
    listed = "account\nl10n_be\nsale\ntest_mail\nhw_drivers\n"
    monkeypatch.setattr(testing, "capture", lambda cmd: listed)
    assert testing.modules_to_install(job, "bo-dev", "bo_dev") == ["account", "sale", "sale_custom"]


# ---------------------------------------------------------------- commit statuses


@pytest.fixture
def fake_github(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any] | None]]:
    """GitHub's API recorded: an installation on Acme that may write statuses; posting answers as GitHub does."""
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    permissions = {"contents": "read", "metadata": "read", "statuses": "write"}
    installation = {"id": 7, "account": "Acme", "account_type": "Organization", "suspended": False,
                    "permissions": permissions}  # fmt: skip

    def request(method: str, path: str, *, bearer: str | None = None, body: dict[str, Any] | None = None,
                timeout: float = 20) -> Any:  # fmt: skip
        calls.append((method, path, body))
        if path.endswith("/access_tokens"):
            return {"token": "ghs_status", "expires_at": "2099-01-01T00:00:00Z"}
        if FAILING:
            raise github_app.GitHubError("GitHub answered 403: Resource not accessible by integration.", 403)
        return {"state": (body or {}).get("state")}

    monkeypatch.setattr(github_app, "connected", lambda: True)
    monkeypatch.setattr(github_app, "installations", lambda: [installation])
    monkeypatch.setattr(github_app, "app_jwt", lambda: "jwt")
    monkeypatch.setattr(github_app, "request", request)
    monkeypatch.setattr(github_app, "_status_tokens", {})
    monkeypatch.setattr(commit_status, "_spawn", lambda fn: fn())  # at once
    monkeypatch.setattr(
        commit_status, "target_url", lambda pid, branch: f"https://panel.example/projects/{pid}/branches/{branch}"
    )
    FAILING.clear()
    return calls


FAILING: list[bool] = []


def _statuses(calls: list[tuple[str, str, dict[str, Any] | None]]) -> list[tuple[str, str, str]]:
    return [(path.rsplit("/", 1)[-1], body["state"], body["context"]) for method, path, body in calls
            if "/statuses/" in path and body]  # fmt: skip


def test_a_build_reports_pending_then_its_result(fake_github: list[Any]) -> None:
    project = _project()
    assert commit_status.available(project) and commit_status.enabled(project)
    job = Job("bo-dev", "Create instance", "create", "t")
    job.details = {"build": "create", "project": project["id"], "branch": "dev"}
    builds.job_started(job)
    job.status, job.finished = "success", time.time()
    builds.job_ended(job)
    assert _statuses(fake_github) == [(SHA, "pending", "sudoo/development"), (SHA, "success", "sudoo/development")]
    posted = [body for method, path, body in fake_github if "/statuses/" in path]
    assert posted[0]["target_url"] == f"https://panel.example/projects/{project['id']}/branches/dev"
    assert posted[0]["description"] == "Building" and posted[1]["description"] == "Instance created"
    token = next(body for method, path, body in fake_github if path.endswith("/access_tokens"))
    assert token == {"permissions": {"statuses": "write", "metadata": "read"}}  # only what statuses need
    assert sum(path.endswith("/access_tokens") for _, path, _ in fake_github) == 1  # the token is reused


def test_failed_tests_are_a_failure_other_failures_an_error(fake_github: list[Any]) -> None:
    project = _project()
    for tests, expected in (({"status": "failed", "result": "At least one test failed"}, "failure"), (None, "error")):
        fake_github.clear()
        job = Job("bo-dev", "Create instance", "create", "t")
        job.details = {"build": "create", "project": project["id"], "branch": "dev"}
        builds.job_started(job)
        if tests:
            job.details["tests"] = tests
        else:
            job.status = "failed"
        job.status, job.finished = ("success" if tests else "failed"), time.time()
        builds.job_ended(job)
        assert _statuses(fake_github)[-1] == (SHA, expected, "sudoo/development")


def test_statuses_follow_the_option_and_the_permission(fake_github: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(commit_statuses=False)
    assert not commit_status.enabled(project)
    job = Job("bo-dev", "Create instance", "create", "t")
    job.details = {"build": "create", "project": project["id"], "branch": "dev"}
    builds.job_started(job)
    job.status, job.finished = "success", time.time()
    builds.job_ended(job)
    assert _statuses(fake_github) == []
    projects.change(project["id"], lambda p: {**p, "options": {}})
    monkeypatch.setattr(github_app, "installations", lambda: [{"id": 7, "account": "Acme", "permissions": {}}])
    assert not commit_status.available(projects.get(project["id"]) or {})


def test_github_refusing_never_fails_a_build(fake_github: list[Any]) -> None:
    project = _project()
    FAILING.append(True)
    job = Job("bo-dev", "Create instance", "create", "t")
    job.details = {"build": "create", "project": project["id"], "branch": "dev"}
    builds.job_started(job)
    job.status, job.finished = "success", time.time()
    builds.job_ended(job)  # no exception
    assert [e["status"] for e in builds.history(project["id"], "dev")] == ["success"]
    with pytest.raises(github_app.GitHubError):
        github_app.post_status("Acme/Books", "not-a-sha", "success", "sudoo/development", "x", None)
    with pytest.raises(github_app.GitHubError):
        github_app.post_status("Acme/Books", SHA, "done", "sudoo/development", "x", None)


def test_the_manifest_asks_for_commit_statuses() -> None:
    manifest = github_app.manifest("https://p", "https://p", "p")
    assert manifest["default_permissions"]["statuses"] == "write"
    assert manifest["default_permissions"]["contents"] == "read"


def test_a_build_keeps_its_tests_counts(fake_github: list[Any], admin: Client) -> None:
    project = _project()
    job = Job("bo-dev", "Create instance", "create", "t")
    job.details = {"build": "create", "project": project["id"], "branch": "dev"}
    builds.job_started(job)
    job.details["tests"] = {"status": "failed", "result": "At least one test failed", "tests": 132, "failed": 1,
                            "errors": 0, "warnings": 2, "modules": ["sale_custom"], "messages": ["x"]}  # fmt: skip
    job.status, job.finished = "success", time.time()
    builds.job_ended(job)
    client, headers = admin
    shown = client.get(f"/api/v1/projects/{project['id']}/branches/dev/history", headers=headers).get_json()["data"]
    assert shown[0]["status"] == "failed"
    assert shown[0]["tests"] == {"status": "failed", "tests": 132, "failed": 1, "errors": 0, "warnings": 2,
                                 "modules": ["sale_custom"]}  # fmt: skip
