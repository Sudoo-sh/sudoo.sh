"""Promoting code between branches: comparing branches (repos/mirror.py, projects/compare.py), the pre-promotion
checklist (projects/promotion.py), the scheduled staging refresh (projects/refresh.py) and pull-request previews
(projects/previews.py). GitHub never runs: the repository is a local git repository standing in for GitHub's, its
API a recording stand-in; jobs are recorded instead of run."""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from sudoo import projects
from sudoo.config import live, settings
from sudoo.core import audit, jobs
from sudoo.core.jobs import Job
from sudoo.core.state import STATE
from sudoo.instances import backups, monitor, operations, store
from sudoo.limits import quotas
from sudoo.projects import access, branches, builds, compare, previews, promotion, refresh, staging
from sudoo.repos import github, github_app, mirror
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
NAMES = ("pm-main", "pm-qa", "pm-dev", "pm-other")
REPOSITORY = "Acme/Promo"
ADDRESS = f"https://github.com/{REPOSITORY}"
HEADS: dict[str, str] = {}


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def _write(path: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository with main (production) and qa: qa bumps sale_custom with a migration script, adds stock_extra,
    removes old_module and changes website_x without a new version."""
    path = tmp_path / "origin"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    _write(
        path,
        {
            "addons/sale_custom/__manifest__.py": "{'name': 'Sale custom', 'version': '18.0.1.0.0'}",
            "addons/sale_custom/models.py": "x = 1\n",
            "addons/old_module/__manifest__.py": "{'name': 'Old', 'version': '18.0.1.0.0'}",
            "addons/website_x/__manifest__.py": "{'name': 'Website', 'version': '18.0.2.0.0'}",
            "addons/website_x/views.xml": "<odoo/>\n",
            "addons/untouched/__manifest__.py": "{'name': 'Untouched', 'version': '18.0.1.0.0'}",
        },
    )
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "First")
    HEADS["main"] = _git(path, "rev-parse", "HEAD")
    _git(path, "checkout", "-q", "-b", "qa")
    _write(
        path,
        {
            "addons/sale_custom/__manifest__.py": "{'name': 'Sale custom', 'version': '18.0.1.1.0'}",
            "addons/sale_custom/migrations/18.0.1.1.0/post-migrate.py": "def migrate(cr, version):\n    pass\n",
            "addons/stock_extra/__manifest__.py": "{'name': 'Stock extra', 'version': '18.0.1.0.0'}",
            "addons/website_x/views.xml": "<odoo><data/></odoo>\n",
        },
    )
    _git(path, "rm", "-q", "-r", "addons/old_module")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "Bump sale_custom")
    _write(path, {"addons/stock_extra/models.py": "y = 2\n"})
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "Stock extra models")
    HEADS["qa"] = _git(path, "rev-parse", "HEAD")
    _git(path, "checkout", "-q", "main")
    real = mirror.refresh
    monkeypatch.setattr(mirror, "refresh", lambda address, force=False: real(str(path), force))
    return path


def _instance(name: str, stage: str, branch: str, project_id: str, version: str = "18.0") -> None:
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    subprocess.run(["git", "init", "-q", str(folder / "addons")], check=True)
    subprocess.run(["git", "-C", str(folder / "addons"), "config", "remote.origin.url", ADDRESS], check=True)
    store.write_env(
        name,
        {
            "ODOO_VERSION": version,
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39900 + 2 * NAMES.index(name),
            "ODOO_PORT": 39901 + 2 * NAMES.index(name),
            "GIT_BRANCH": branch,
            "AUTO_UPDATE": "true",
            "AUTO_UPGRADE": "true",
            "AUTO_PAUSE": "true",
            "DELETE_AFTER_DAYS": "30",
            "CREATED_AT": int(time.time() - 3 * 86400),
            "STAGE": stage,
            "PROJECT": project_id,
        },
    )


def _project(**options: Any) -> str:
    project = projects.create(repository=REPOSITORY, odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    projects.change(project["id"], lambda p: {**p, "options": options, "seen": ["main", "qa"]})
    return str(project["id"])


def _backup(name: str, hours_ago: float) -> None:
    """A backup file of instance `name`, taken `hours_ago`."""
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    env = store.read_env(name)
    stamp = datetime.fromtimestamp(time.time() - hours_ago * 3600).strftime("%Y%m%d-%H%M%S")
    path = settings.backups_dir / f"{name}__{env['DB_NAME']}__{stamp}__manual.zip"
    path.write_bytes(b"PK")
    taken = time.time() - hours_ago * 3600
    import os

    os.utime(path, (taken, taken))


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    HEADS.clear()
    monkeypatch.setattr(
        github, "heads", lambda address: (dict(HEADS), "main", None) if address == ADDRESS else ({}, None, "not found")
    )
    monkeypatch.setattr(previews, "_spawn", lambda fn: fn())
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
    compare._cache.clear()
    shutil.rmtree(settings.backups_dir, ignore_errors=True)
    shutil.rmtree(settings.manager_dir / "mirrors", ignore_errors=True)
    mirror._fetched.clear()
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, Any, tuple[Any, ...]]]:
    """Jobs are recorded (instance, action, function, arguments) instead of run."""
    calls: list[tuple[str, str, Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system",
                   **_: Any) -> Job:  # fmt: skip
        calls.append((instance, action, fn, args))
        return Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    monkeypatch.setattr(quotas, "claim", lambda user: None)
    return calls


# ---------------------------------------------------------------- comparing


def test_the_mirror_reads_manifests_as_data_and_finds_migrations(repository: Path) -> None:
    path = mirror.refresh(ADDRESS)
    assert (path / "HEAD").exists() and not (path / "addons").exists()  # bare: nothing checked out
    assert mirror.counts(path, HEADS["main"], HEADS["qa"]) == (2, 0)
    found = mirror.manifests(path, HEADS["qa"])
    assert found["sale_custom"] == {"path": "addons/sale_custom", "version": "18.0.1.1.0", "name": "Sale custom"}
    assert "old_module" not in found
    files = mirror.files_at(path, HEADS["qa"])
    assert mirror.migrations(files, "addons/sale_custom", "18.0.1.1.0", "18.0") == [
        "addons/sale_custom/migrations/18.0.1.1.0/post-migrate.py"
    ]
    assert mirror.migrations(["m/migrations/1.1.0/pre.py"], "m", "18.0.1.1.0", "18.0") == ["m/migrations/1.1.0/pre.py"]
    # a manifest is never run: anything but a literal reads as nothing
    assert mirror.read_manifest("__import__('os').system('true')") == {}
    assert mirror.read_manifest("{'version': '1.0'}") == {"version": "1.0"}
    with pytest.raises(mirror.GitError):
        mirror.counts(path, "not-a-sha", HEADS["qa"])
    assert compare.bump("18.0.1.0.0", "18.0.1.10.0") == "up" and compare.bump("18.0.2.0", "18.0.1.9") == "down"


def test_compare_two_branches(repository: Path) -> None:
    project = projects.get(_project()) or {}
    result = compare.compare(project, "main", "qa")
    assert (result["ahead"], result["behind"], result["source"]) == (2, 0, "git")
    assert [c["subject"] for c in result["commits"]] == ["Stock extra models", "Bump sale_custom"]
    assert result["files_changed"] == 6
    modules = result["modules"]
    assert modules["added"] == [{"name": "stock_extra", "version": "18.0.1.0.0"}]
    assert modules["removed"] == [{"name": "old_module", "version": "18.0.1.0.0"}]
    assert {c["name"]: c["bump"] for c in modules["changed"]} == {"sale_custom": "up", "website_x": "same"}
    assert result["migrations"] == [{"module": "sale_custom", "version": "18.0.1.1.0",
                                     "scripts": ["addons/sale_custom/migrations/18.0.1.1.0/post-migrate.py"]}]  # fmt: skip
    assert result["github_url"] == f"https://github.com/{REPOSITORY}/compare/main...qa"
    with pytest.raises(compare.CompareError) as missing:
        compare.compare(project, "main", "nope")
    assert missing.value.status == 404


def test_compare_falls_back_to_githubs_compare_api(monkeypatch: pytest.MonkeyPatch) -> None:
    HEADS.update(main="a" * 40, qa="b" * 40)
    project = projects.get(_project()) or {}

    def unreadable(address: str, force: bool = False) -> Path:
        raise mirror.GitError("Repository not found.")

    calls: list[str] = []

    def fake_request(method: str, path: str, **_: Any) -> Any:
        calls.append(path)
        return {"ahead_by": 3, "behind_by": 1, "files": [{"filename": "addons/sale_custom/__manifest__.py"}],
                "commits": [{"sha": "b" * 40, "commit": {"message": "Bump\n\nmore", "author": {"name": "A"}}}]}  # fmt: skip

    monkeypatch.setattr(mirror, "refresh", unreadable)
    monkeypatch.setattr(github_app, "connected", lambda: True)
    monkeypatch.setattr(github_app, "token_for", lambda owner: "token")
    monkeypatch.setattr(github_app, "request", fake_request)
    result = compare.compare(project, "main", "qa")
    assert calls == [f"/repos/{REPOSITORY}/compare/{'a' * 40}...{'b' * 40}"]
    assert (result["ahead"], result["behind"], result["files_changed"], result["source"]) == (3, 1, 1, "github")
    assert result["modules"]["known"] is False and result["modules"]["changed"][0]["name"] == "sale_custom"
    monkeypatch.setattr(github_app, "connected", lambda: False)
    compare._cache.clear()
    with pytest.raises(compare.CompareError, match="Repository not found"):
        compare.compare(project, "main", "qa")


def test_the_compare_endpoint(admin: Client, viewer_client: Client, repository: Path) -> None:
    client, _ = admin
    pid = _project()
    answer = client.get(f"/api/v1/projects/{pid}/compare?base=main&head=qa")
    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["data"]["ahead"] == 2
    assert client.get(f"/api/v1/projects/{pid}/compare?base=main&head=main").status_code == 400
    assert client.get(f"/api/v1/projects/{pid}/compare?base=main&head=nope").status_code == 404
    assert client.get(f"/api/v1/projects/{pid}/compare?base=main&head=-x").status_code == 404
    assert viewer_client[0].get(f"/api/v1/projects/{pid}/compare?base=main&head=qa").status_code == 200


@pytest.fixture(scope="module")
def viewer_client(app: Flask) -> Client:
    from conftest import sign_in
    from sudoo import accounts

    accounts.create("promoview", "viewer", "lantern-copper-violet-41")
    return sign_in(app, "promoview", "lantern-copper-violet-41")


# ---------------------------------------------------------------- the checklist


def _failed_build(pid: str, branch: str, instance: str, tests_failed: bool = False) -> None:
    builds._add({"type": "build", "project": pid, "branch": branch, "instance": instance, "trigger": "push",
                 "status": "failed" if not tests_failed else "failed",
                 **({"tests": {"status": "failed", "tests": 3, "failed": 1}} if tests_failed else {})})  # fmt: skip


def test_the_checklist_items(repository: Path) -> None:
    pid = _project()
    _instance("pm-main", "production", "main", pid)
    _instance("pm-qa", "staging", "qa", pid)
    projects.record_branch(pid, "main", stage="production")
    projects.record_branch(pid, "qa", stage="staging")
    project = projects.get(pid) or {}
    found = promotion.check(project, "qa")
    items = {item["key"]: item for item in found["items"]}
    assert [item["key"] for item in found["items"]] == list(promotion.ORDER)
    assert items["build"]["status"] == "warning"  # never built
    assert items["backup"]["status"] == "warning" and items["backup"]["backup"] is None
    assert items["modules"]["status"] == "warning" and "old_module" in items["modules"]["text"]
    assert (
        items["migrations"]["status"] == "warning" and items["migrations"]["migrations"][0]["module"] == "sale_custom"
    )
    assert items["odoo"]["status"] == "ok" and items["lock"]["status"] == "ok"
    assert found["production"] == {"branch": "main", "instance": "pm-main"}
    # one production branch per project: qa cannot become a second one, but it can be merged into main
    assert found["blocked"] == ["capacity"] and found["blocking"] is True and found["compare"]["ahead"] == 2
    merged = promotion.check(project, "qa", merge=True)
    assert merged["mode"] == "merge" and merged["blocked"] == [] and merged["blocking"] is False
    assert "capacity" not in {item["key"] for item in merged["items"]}

    _backup("pm-main", 2)
    assert promotion.backup_item("pm-main")["status"] == "ok"
    _backup("pm-main", 30)  # the newest counts
    assert promotion.backup_item("pm-main")["status"] == "ok"
    live.change({"project_promotion_backup_hours": 1})
    assert promotion.backup_item("pm-main")["status"] == "warning"
    assert promotion.backup_item(None)["status"] == "ok"

    builds._add({"type": "build", "project": pid, "branch": "qa", "instance": "pm-qa", "trigger": "push",
                 "status": "success", "tests": {"status": "success", "tests": 12}})  # fmt: skip
    assert promotion.build_item(pid, "qa")["status"] == "ok"
    _failed_build(pid, "qa", "pm-qa", tests_failed=True)
    assert promotion.build_item(pid, "qa")["status"] == "blocked"

    _instance("pm-qa", "staging", "qa", pid, version="19.0")
    found = promotion.check(projects.get(pid) or {}, "qa", deep=False)
    assert set(found["blocked"]) == {"build", "odoo", "capacity"} and found["blocking"] is True
    assert "modules" not in {item["key"] for item in found["items"]}  # the quick check leaves git out
    live.change({"project_promotion_blocks": False})
    assert promotion.check(projects.get(pid) or {}, "qa", deep=False)["blocking"] is False


def test_a_lock_is_red_for_whom_it_does_not_let_through() -> None:
    HEADS.update(main="a" * 40, qa="b" * 40)
    pid = _project()

    def lock(project: dict[str, Any]) -> dict[str, Any]:
        project["access"] = {**access.access_of(project),
                             "locks": {"qa": {"locked_by": "boss", "allow": [{"type": "user", "id": "boss"}]}}}  # fmt: skip
        return project

    projects.change(pid, lock)
    project = projects.get(pid) or {}
    assert promotion.lock_item(project, "qa", None, {"name": "someone", "role": "admin"})["status"] == "blocked"
    assert promotion.lock_item(project, "qa", None, {"name": "boss", "role": "admin"})["status"] == "warning"
    assert promotion.lock_item(project, "main", None, None)["status"] == "ok"


def test_red_items_stop_the_move_to_production_unless_promoted_anyway(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    HEADS.update(main="a" * 40, qa="b" * 40)
    pid = _project()
    _instance("pm-qa", "staging", "qa", pid)
    projects.record_branch(pid, "qa", stage="staging")
    _failed_build(pid, "qa", "pm-qa")
    check = client.get(f"/api/v1/projects/{pid}/branches/qa/promotion-check")
    assert check.status_code == 200, check.get_json()
    data = check.get_json()["data"]
    assert data["blocked"] == ["build"] and data["blocking"] is True and isinstance(data["checked_at"], str)
    url = f"/api/v1/projects/{pid}/branches/qa/stage"
    refused = client.put(url, json={"stage": "production", "confirm_production": True}, headers=headers)
    assert refused.status_code == 409 and "promote anyway" in refused.get_json()["detail"]
    assert projects.branch_stage(projects.get(pid) or {}, "qa") == "staging"
    moved = client.put(url, json={"stage": "production", "confirm_production": True, "promote_anyway": True},
                       headers=headers)  # fmt: skip
    assert moved.status_code == 200, moved.get_json()
    assert projects.branch_stage(projects.get(pid) or {}, "qa") == "production"
    logged = [e for e in audit.latest(50) if e["event"] == "promotion checklist overridden"]
    assert logged and "did not pass" in logged[0]["detail"]
    assert client.get(f"/api/v1/projects/{pid}/branches/nope/promotion-check").status_code == 200  # never built
    assert client.get(f"/api/v1/projects/{pid}/branches/-x/promotion-check").status_code == 404


# ---------------------------------------------------------------- the scheduled refresh


def test_schedules_are_checked_and_their_slots_computed() -> None:
    with pytest.raises(ValueError):
        refresh.clean({"frequency": "hourly"})
    with pytest.raises(ValueError):
        refresh.clean({"frequency": "daily", "time": "25:00"})
    with pytest.raises(ValueError):
        refresh.clean({"frequency": "weekly", "weekday": 7})
    with pytest.raises(ValueError):
        refresh.clean({"frequency": "daily", "branches": ["../x"]})
    assert refresh.clean({"frequency": "daily", "time": "03:30", "branches": ["qa", "qa"]})["branches"] == ["qa"]
    zone = settings.zone
    now = datetime(2026, 11, 18, 10, 0, tzinfo=zone).timestamp()  # a Wednesday
    daily = {"frequency": "daily", "time": "03:00", "weekday": 6, "branches": []}
    assert refresh.last_slot(daily, now) == datetime(2026, 11, 18, 3, 0, tzinfo=zone).timestamp()
    assert refresh.next_run(daily, now) == datetime(2026, 11, 19, 3, 0, tzinfo=zone).timestamp()
    weekly = {**daily, "frequency": "weekly", "weekday": 6}  # Sundays
    assert refresh.last_slot(weekly, now) == datetime(2026, 11, 15, 3, 0, tzinfo=zone).timestamp()
    assert refresh.next_run(weekly, now) == datetime(2026, 11, 22, 3, 0, tzinfo=zone).timestamp()
    assert refresh.last_slot({**daily, "frequency": "off"}, now) is None


def test_the_scheduled_refresh_rebuilds_staging_and_skips_what_it_must(started: list[Any]) -> None:
    HEADS.update(main="a" * 40, qa="b" * 40, uat="c" * 40)
    pid = _project()
    _instance("pm-main", "production", "main", pid)
    _instance("pm-qa", "staging", "qa", pid)
    _instance("pm-other", "staging", "uat", pid)
    _instance("pm-dev", "development", "feature", pid)
    for branch, stage in (("main", "production"), ("qa", "staging"), ("uat", "staging")):
        projects.record_branch(pid, branch, stage=stage)
    zone = settings.zone
    saved_at = datetime(2026, 11, 18, 4, 0, tzinfo=zone).timestamp()
    refresh.save(pid, refresh.clean({"frequency": "daily", "time": "03:00"}), "admin1", now=saved_at)
    assert not refresh.due(projects.get(pid) or {}, saved_at + 60)  # today's slot had passed when it was saved
    tomorrow = datetime(2026, 11, 19, 3, 1, tzinfo=zone).timestamp()
    assert refresh.due(projects.get(pid) or {}, tomorrow)

    with jobs.INSTANCE_LOCKS["pm-other"]:  # busy with another job
        assert refresh.run_due(tomorrow) == [pid]
    assert [(c[0], c[1], c[2]) for c in started] == [("pm-qa", refresh.ACTION, staging.rebuild)]
    assert not refresh.due(projects.get(pid) or {}, tomorrow + 120)  # once per slot
    entry = builds.history(pid, "qa")[0]
    assert entry["type"] == "refresh" and entry["started"] == ["pm-qa"] and entry["status"] == "success"
    skipped = builds.history(pid, "uat")[0]
    assert skipped["status"] == "failed" and "busy" in skipped["result"]

    # a locked branch is skipped; only the chosen branches are refreshed
    def lock(project: dict[str, Any]) -> dict[str, Any]:
        project["access"] = {**access.access_of(project), "locks": {"qa": {"locked_by": "boss", "allow": []}}}
        return project

    projects.change(pid, lock)
    started.clear()
    refresh.save(pid, refresh.clean({"frequency": "daily", "time": "03:00", "branches": ["qa"]}), "admin1")
    result = refresh.run(projects.get(pid) or {})
    assert result["started"] == [] and result["skipped"] == [{"instance": "pm-qa", "reason": "the branch is locked"}]
    assert started == []


def test_the_schedule_endpoints(admin: Client, viewer_client: Client, started: list[Any]) -> None:
    client, headers = admin
    HEADS.update(main="a" * 40, qa="b" * 40)
    pid = _project()
    _instance("pm-main", "production", "main", pid)
    _instance("pm-qa", "staging", "qa", pid)
    projects.record_branch(pid, "qa", stage="staging")
    url = f"/api/v1/projects/{pid}/staging-refresh"
    read = client.get(url).get_json()
    assert read["data"]["frequency"] == "off" and read["data"]["next_run"] is None
    assert read["meta"]["branches"] == [{"name": "qa", "instances": ["pm-qa"]}]
    changed = client.put(url, json={"frequency": "weekly", "time": "02:15", "weekday": 5, "branches": ["qa"]},
                         headers=headers)  # fmt: skip
    assert changed.status_code == 200, changed.get_json()
    assert changed.get_json()["data"]["weekday"] == 5 and changed.get_json()["data"]["next_run"]
    assert client.put(url, json={"frequency": "sometimes"}, headers=headers).status_code == 400
    viewer, viewer_headers = viewer_client
    assert viewer.get(url).status_code == 200
    assert viewer.put(url, json={"frequency": "off"}, headers=viewer_headers).status_code == 403
    ran = client.post(f"{url}/run", json={}, headers=headers)
    assert ran.status_code == 202 and ran.get_json()["data"]["started"] == ["pm-qa"]


# ---------------------------------------------------------------- pull-request previews


@pytest.fixture
def github_stand_in(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """The GitHub App's comments, recorded: ("comment", (number, body, comment_id))."""
    calls: list[tuple[str, Any]] = []

    def fake_comment(full_name: str, number: int, body: str, comment_id: int | None = None) -> int:
        calls.append(("comment", (number, body, comment_id)))
        return comment_id or 1000 + number

    monkeypatch.setattr(github_app, "comment", fake_comment)
    return calls


@pytest.fixture
def planned(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    from sudoo.premium import servers

    def fake_start(plan: dict[str, Any], user: str, server_id: str = "") -> None:
        store.release_ports(plan["ports"])
        plans.append(plan)

    monkeypatch.setattr(servers, "start_planned", fake_start)
    monkeypatch.setattr(jobs, "creating", lambda name: next((Job(name, "Create", "create", "system") for p in plans
                                                             if p["name"] == name), None))  # fmt: skip
    return plans


def _pull(number: int, branch: str, sha: str = "e" * 40, repository: str = REPOSITORY, **extra: Any) -> dict[str, Any]:
    return {"number": number, "title": f"Change {number}", "html_url": f"https://github.com/{REPOSITORY}/pull/{number}",
            "state": "open", "head": {"ref": branch, "sha": sha, "repo": {"full_name": repository}},
            "base": {"ref": "main", "repo": {"full_name": REPOSITORY}}, **extra}  # fmt: skip


def _event(client: FlaskClient, action: str, pull: dict[str, Any]) -> Any:
    body = json.dumps({"action": action, "number": pull["number"], "pull_request": pull,
                       "repository": {"full_name": REPOSITORY}}).encode()  # fmt: skip
    secret = "w" * 32
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Event": "pull_request"}
    return client.post("/hooks/github-app", data=body, headers=headers, content_type="application/json")


@pytest.fixture
def app_webhook(monkeypatch: pytest.MonkeyPatch) -> FlaskClient:
    from flask import Flask as App

    from sudoo.config import Settings
    from sudoo.triggers.github_app import GitHubAppWebhook

    monkeypatch.setattr(github_app, "connected", lambda: True)
    monkeypatch.setattr(github_app, "webhook_secret", lambda: "w" * 32)
    trigger = GitHubAppWebhook(Settings())
    trigger.start(lambda name, reason: None)
    flask_app = App(__name__)
    blueprint = trigger.blueprint()
    assert blueprint is not None
    flask_app.register_blueprint(blueprint)
    return flask_app.test_client()


def test_a_pull_request_gets_a_preview_and_loses_it_when_closed(
    app_webhook: FlaskClient, github_stand_in: list[Any], planned: list[dict[str, Any]], started: list[Any]
) -> None:
    HEADS.update(main="a" * 40, qa="b" * 40, feature="e" * 40)
    pid = _project()
    off = _event(app_webhook, "opened", _pull(7, "feature")).get_json()
    assert off["preview"] is None and "off" in off["reason"] and planned == []

    projects.change(pid, lambda p: {**p, "options": {"pr_previews": True}})
    answer = _event(app_webhook, "opened", _pull(7, "feature"))
    assert answer.status_code == 200, answer.get_json()
    name = answer.get_json()["preview"]
    assert planned[0]["name"] == name and planned[0]["branch"] == "feature" and planned[0]["project"] == pid
    assert planned[0]["stage"] == "development"
    record = previews.records(projects.get(pid) or {})["7"]
    assert record["instance"] == name and record["status"] == "creating" and record["comment_id"] == 1007
    assert (
        github_stand_in[0][1][0] == 7
        and name in github_stand_in[0][1][1]
        and previews.MARKER in github_stand_in[0][1][1]
    )

    # told again (webhook and polling): the same preview; the comment is not written twice
    assert _event(app_webhook, "synchronize", _pull(7, "feature", sha="f" * 40)).get_json()["preview"] == name
    assert len(planned) == 1 and len(github_stand_in) == 1

    # a build of the preview changes the comment
    _instance("pm-dev", "development", "feature", pid)
    projects.change(pid, lambda p: {**p, "previews": {"7": {**record, "instance": "pm-dev"}}})
    job = Job("pm-dev", "Update", "update", "system")
    job.status = "failed"
    job.hint = "Odoo did not start."
    previews.job_ended(job)
    assert github_stand_in[-1][1][2] == 1007 and "failed" in github_stand_in[-1][1][1]
    assert previews.records(projects.get(pid) or {})["7"]["status"] == "failed"

    closed = _event(app_webhook, "closed", _pull(7, "feature", state="closed")).get_json()
    assert closed["deleted"] == "pm-dev"
    assert started[-1][:3] == ("pm-dev", "Delete (pull request closed)", operations.delete)
    assert started[-1][3] == ("pm-dev", False, True)  # no backup
    assert "Deleted" in github_stand_in[-1][1][1]
    assert previews.records(projects.get(pid) or {}) == {}


def test_previews_reuse_a_branchs_instance_and_keep_to_the_limit(
    github_stand_in: list[Any], planned: list[dict[str, Any]], started: list[Any]
) -> None:
    HEADS.update(main="a" * 40, qa="b" * 40)
    pid = _project(pr_previews=True, pr_preview_limit=1)
    _instance("pm-qa", "staging", "qa", pid)
    project = projects.get(pid) or {}
    assert previews.opened(project, _pull(1, "qa")) == {"preview": "pm-qa", "existing": True}
    assert planned == []  # the branch's own instance
    assert previews.opened(projects.get(pid) or {}, _pull(2, "one"))["preview"] == planned[0]["name"]
    refused = previews.opened(projects.get(pid) or {}, _pull(3, "two"))
    assert refused["preview"] is None and "limit" in refused["reason"] and len(planned) == 1
    record = previews.records(projects.get(pid) or {})["3"]
    assert record["status"] == "refused" and "No preview" in github_stand_in[-1][1][1]
    # closing the pull request of an existing instance never deletes it
    assert previews.closed(projects.get(pid) or {}, 1) == {"deleted": None} and started == []


def test_forks_get_no_preview_unless_the_project_allows_them(
    github_stand_in: list[Any], planned: list[dict[str, Any]]
) -> None:
    HEADS.update(main="a" * 40)
    pid = _project(pr_previews=True)
    fork = _pull(4, "main", repository="Stranger/Promo")
    answer = previews.opened(projects.get(pid) or {}, fork)
    assert answer["preview"] is None and "forks" in answer["reason"] and planned == []
    projects.change(pid, lambda p: {**p, "options": {"pr_previews": True, "pr_previews_forks": True}})
    answer = previews.opened(projects.get(pid) or {}, fork)
    plan = planned[0]
    assert answer["preview"] == plan["name"] and plan["repo"] == "https://github.com/Stranger/Promo"
    fork_project = projects.by_repository("Stranger/Promo") or {}
    assert plan["project"] == fork_project["id"] != pid  # never mixed with the project's own "main"
    assert fork_project["options"] == {"auto_development": False}


def test_polling_opens_and_closes_previews(
    monkeypatch: pytest.MonkeyPatch, github_stand_in: list[Any], planned: list[dict[str, Any]], started: list[Any]
) -> None:
    HEADS.update(main="a" * 40)
    pid = _project(pr_previews=True)
    monkeypatch.setattr(github_app, "connected", lambda: True)
    open_now = [_pull(9, "feature-9")]
    monkeypatch.setattr(github_app, "open_pulls", lambda full_name: list(open_now))
    assert previews.poll(projects.get(pid) or {}) == {"opened": [9], "closed": []}
    assert previews.poll(projects.get(pid) or {}) == {"opened": [], "closed": []}  # nothing new
    open_now.clear()
    report = previews.poll(projects.get(pid) or {})
    assert report == {"opened": [], "closed": [9]}
    assert started[-1][1] == "Delete (pull request closed)"

    def unreachable(full_name: str) -> list[Any]:
        raise github_app.GitHubError("GitHub cannot be reached.")

    previews.opened(projects.get(pid) or {}, _pull(10, "feature-10"))
    monkeypatch.setattr(github_app, "open_pulls", unreachable)
    assert previews.poll(projects.get(pid) or {}) == {"opened": [], "closed": []}  # nothing closed by mistake
    assert "10" in previews.records(projects.get(pid) or {})


def test_the_previews_endpoint_and_options(admin: Client, planned: list[dict[str, Any]],
                                           github_stand_in: list[Any]) -> None:  # fmt: skip
    client, headers = admin
    HEADS.update(main="a" * 40)
    pid = _project()
    changed = client.patch(f"/api/v1/projects/{pid}", json={"options": {"pr_previews": True, "pr_preview_limit": 2}},
                           headers=headers)  # fmt: skip
    assert changed.status_code == 200, changed.get_json()
    bad = client.patch(f"/api/v1/projects/{pid}", json={"options": {"pr_preview_limit": 99}}, headers=headers)
    assert bad.status_code == 400
    previews.opened(projects.get(pid) or {}, _pull(5, "feature-5"))
    answer = client.get(f"/api/v1/projects/{pid}/previews").get_json()
    assert answer["meta"]["enabled"] is True and answer["meta"]["limit"] == 2 and answer["meta"]["forks"] is False
    assert answer["data"][0]["number"] == 5 and answer["data"][0]["status"] == "creating"


def test_the_app_asks_for_pull_requests_and_comments_with_its_own_token(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = github_app.manifest("https://p", "https://p", "p")
    assert manifest["default_permissions"]["pull_requests"] == "write"
    assert manifest["default_permissions"]["issues"] == "write"
    assert manifest["default_permissions"]["contents"] == "read" and "pull_request" in manifest["default_events"]
    installation = {"id": 5, "account": "Acme", "suspended": False, "permissions": {"pull_requests": "write"}}
    monkeypatch.setattr(github_app, "connected", lambda: True)
    monkeypatch.setattr(github_app, "installations", lambda: [installation])
    monkeypatch.setattr(github_app, "app_jwt", lambda: "jwt")
    github_app._comment_tokens.clear()
    calls: list[tuple[str, str, Any]] = []

    def fake_request(method: str, path: str, *, bearer: str | None = None, body: Any = None, **_: Any) -> Any:
        calls.append((method, path, body))
        if path.endswith("/access_tokens"):
            return {"token": "pr-token", "expires_at": "2099-01-01T00:00:00Z"}
        if method == "PATCH":
            raise github_app.GitHubError("GitHub answered 404.", 404)
        return {"id": 77}

    monkeypatch.setattr(github_app, "request", fake_request)
    assert github_app.can_comment("acme")
    assert github_app.comment("Acme/Promo", 3, "hello", comment_id=12) == 77  # deleted on GitHub: a new one
    assert calls[0][2] == {"permissions": {"pull_requests": "write", "metadata": "read"}}
    assert [c[:2] for c in calls[1:]] == [("PATCH", "/repos/Acme/Promo/issues/comments/12"),
                                          ("POST", "/repos/Acme/Promo/issues/3/comments")]  # fmt: skip
    installation["permissions"] = {"contents": "read"}
    assert not github_app.can_comment("Acme")
    with pytest.raises(github_app.GitHubError):
        github_app.comment("Acme/Promo", 3, "hello")
    with pytest.raises(github_app.GitHubError):
        github_app.open_pulls("Acme/Promo")


def test_a_preview_is_deleted_without_a_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    pid = _project()
    _instance("pm-dev", "development", "feature", pid)
    backed_up: list[str] = []
    monkeypatch.setattr(backups, "create", lambda job, name, kind: backed_up.append(name) or [])
    job = Job("pm-dev", "Delete", "delete", "system")
    monkeypatch.setattr(job, "run", lambda *a, **k: "")
    from sudoo.instances import gateway

    monkeypatch.setattr(gateway, "remove", lambda name: None)
    operations.delete(job, "pm-dev", False, True)
    assert backed_up == [] and not store.exists("pm-dev")
    assert any("throw-away" in note["text"] for note in job.notes)
