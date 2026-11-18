"""Database upgrades (upgrades/): pre-check, the test on a new staging instance, re-runs, the promotion of production
with its rollback, the watch, permissions. Docker, GitHub, Odoo's upgrade service and OpenUpgrade never run: every
command is recorded, and the upgrade service's script is a stand-in that writes the files it would download."""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from conftest import sign_in
from sudoo import accounts, addons, projects, upgrades
from sudoo.config import live, settings
from sudoo.core import jobs
from sudoo.core.jobs import Job, JobError
from sudoo.core.state import STATE
from sudoo.instances import backups, gateway, integrity, odoo, operations, store
from sudoo.limits import quotas
from sudoo.projects import automation
from sudoo.repos import github
from sudoo.storage import storage
from sudoo.upgrades import Secret, methods, precheck
from sudoo.upgrades import run as upgrade_run

NAMES = ("shop-prod", "ent-prod", "shop-prod-odoo19", "plain-prod")
CODE = "M2026-SECRET-CODE-1234"


def _instance(name: str, repo: str | None = "Acme/Shop", edition: str = "community", **env: Any) -> None:
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "config").mkdir(exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    (folder / "config" / "odoo.conf").write_text("[options]\naddons_path = /mnt/extra-addons\n")
    subprocess.run(["git", "init", "-q", str(folder / "addons")], check=True)
    if repo:
        subprocess.run(["git", "-C", str(folder / "addons"), "config", "remote.origin.url",
                        f"https://github.com/{repo}"], check=True)  # fmt: skip
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": edition,
            "DB_NAME": name.replace("-", "_"),
            "DB_PASSWORD": "db-pass-123",
            "GATEWAY_PORT": 39700 + 2 * NAMES.index(name),
            "ODOO_PORT": 39701 + 2 * NAMES.index(name),
            "GIT_BRANCH": "main" if repo else "",
            "AUTO_UPDATE": "false",
            "AUTO_UPGRADE": "true",
            "AUTO_PAUSE": "false",
            "DELETE_AFTER_DAYS": "0",
            "CREATED_AT": int(time.time() - 3600),
            "STAGE": "production",
            **({} if repo else {"SOURCE": "plain"}),
            **env,
        },
    )


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(github, "heads", lambda address: ({"main": "a" * 40, "19.0": "b" * 40}, "main", None))
    yield
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
    for key in list(storage().all(upgrades.COLLECTION)):
        upgrades.forget(key)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    shutil.rmtree(settings.backups_dir, ignore_errors=True)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, Any, tuple[Any, ...]]]:
    calls: list[tuple[str, str, Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system",
                   **_: Any) -> Job:  # fmt: skip
        calls.append((instance, kind, fn, args))
        return Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    monkeypatch.setattr(quotas, "claim", lambda user: None)
    return calls


@pytest.fixture(scope="module")
def maintainer(app: Flask) -> tuple[Any, dict[str, str]]:
    accounts.create("upmaint", "maintainer", "garnet-willow-cipher-71")
    return sign_in(app, "upmaint", "garnet-willow-cipher-71")


def _checked(monkeypatch: pytest.MonkeyPatch, admin: Any, name: str = "shop-prod") -> dict[str, Any]:
    monkeypatch.setattr(precheck, "_branch_modules", lambda address, branch: ({
        "shop_sale": {"version": "19.0.1.0.0"}, "shop_stock": {"version": "18.0.2.0.0"}}, None))  # fmt: skip
    monkeypatch.setattr(precheck, "modules_of", lambda addons: {"shop_sale": {"version": "18.0.1.0.0"},
                        "shop_stock": {"version": "18.0.2.0.0"}, "shop_unused": {"version": "1.0"}})  # fmt: skip
    monkeypatch.setattr(precheck, "succeeds", lambda cmd, timeout=30: True)
    monkeypatch.setattr(backups, "installed_modules", lambda name, db: {"base", "sale", "shop_sale", "shop_stock"})
    client, headers = admin
    answer = client.post(f"/api/v1/instances/{name}/upgrade/check", json={"target": "19.0"}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    return dict(answer.get_json()["data"])


# ---------------------------------------------------------------- versions and the pre-check


def test_targets_are_the_next_version_unless_the_method_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    community = {"ODOO_VERSION": "18.0", "EDITION": "community"}
    enterprise = {"ODOO_VERSION": "18.0", "EDITION": "enterprise"}
    assert upgrades.targets(community) == ["19.0"]  # OpenUpgrade: one version at a time
    assert upgrades.targets(enterprise) == ["19.0", "20.0"]  # Odoo's service may skip
    assert upgrades.targets({"ODOO_VERSION": "20.0", "EDITION": "community"}) == []
    assert "Enterprise add-ons" in (upgrades.method_problem(enterprise, "19.0") or "")
    monkeypatch.setattr(addons, "available", lambda version: True)
    assert upgrades.method_problem(enterprise, "20.0") is None
    assert upgrades.method_problem(community, "19.0") is None
    live.change({"upgrade_methods": "enterprise"})
    assert "not allowed" in (upgrades.method_problem(community, "19.0") or "")
    live.change({"upgrade_methods": None, "openupgrade_refs": "18.0=" + "c" * 40})
    assert "No OpenUpgrade commit" in (upgrades.method_problem(community, "19.0") or "")


def test_module_status() -> None:
    assert precheck.status({"version": "19.0.1.0.0"}, "19.0") == "ready"
    assert precheck.status({"version": "18.0.1.0.0"}, "19.0") == "version_mismatch"
    assert precheck.status({"version": "1.0"}, "19.0") == "unknown_version"
    assert precheck.status({"version": "19.0.1.0.0", "installable": False}, "19.0") == "not_installable"
    assert precheck.status(None, "19.0") == "missing"


def test_manifests_are_read_never_run(tmp_path: Path) -> None:
    (tmp_path / "mod").mkdir()
    (tmp_path / "mod" / "__manifest__.py").write_text("__import__('os').system('false')")
    assert precheck.manifest(tmp_path / "mod") == {}
    (tmp_path / "mod" / "__manifest__.py").write_text("{'name': 'Mod', 'version': '19.0.1.0.0'}")
    assert precheck.manifest(tmp_path / "mod")["version"] == "19.0.1.0.0"


def test_precheck_reports_the_repository_modules(monkeypatch: pytest.MonkeyPatch, admin: Any) -> None:
    _instance("shop-prod")
    found = _checked(monkeypatch, admin)
    report = found["report"]
    assert found["next"] == "19.0" and found["method"] == "openupgrade" and found["state"] == "checked"
    assert report["branch_exists"] is True and found["branch"] == "19.0"
    assert {m["name"]: m["status"] for m in report["modules"]} == {"shop_sale": "ready",
                                                                   "shop_stock": "version_mismatch"}  # fmt: skip
    assert [w["code"] for w in report["warnings"]] == ["modules_not_ready"]
    assert report["installed_count"] == 4
    assert any(e["event"] == "database upgrade: pre-check" for e in storage().logs(20))


def test_precheck_without_an_upgrade_branch_warns(monkeypatch: pytest.MonkeyPatch, admin: Any) -> None:
    _instance("shop-prod")
    monkeypatch.setattr(github, "heads", lambda address: ({"main": "a" * 40}, "main", None))
    found = _checked(monkeypatch, admin)
    assert found["branch"] is None
    assert "no_branch" in [w["code"] for w in found["report"]["warnings"]]
    assert all(m["status"] == "missing" for m in found["report"]["modules"])


# ---------------------------------------------------------------- permissions


def test_only_administrators_upgrade(monkeypatch: pytest.MonkeyPatch, admin: Any, maintainer: Any,
                                     started: list[Any]) -> None:  # fmt: skip
    _instance("shop-prod")
    client, headers = maintainer
    assert client.get("/api/v1/instances/shop-prod/upgrade").status_code == 200  # reading: instances.view
    for path in ("check", "test", "promote", "cancel"):
        answer = client.post(f"/api/v1/instances/shop-prod/upgrade/{path}", json={}, headers=headers)
        assert answer.status_code == 403, path
    assert not started


# ---------------------------------------------------------------- the test


def test_the_test_creates_a_staging_instance_on_the_target(monkeypatch: pytest.MonkeyPatch, admin: Any,
                                                           started: list[Any]) -> None:  # fmt: skip
    project = projects.create(repository="Acme/Shop", odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    _instance("shop-prod", PROJECT=project["id"])
    client, headers = admin
    early = client.post("/api/v1/instances/shop-prod/upgrade/test", json={"target": "19.0"}, headers=headers)
    assert early.status_code == 409  # the pre-check comes first
    _checked(monkeypatch, admin)
    answer = client.post("/api/v1/instances/shop-prod/upgrade/test", json={"target": "19.0"}, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    name, kind, fn, args = started[-1]
    plan = args[0]
    store.release_ports(plan["ports"])
    assert (kind, fn) == ("create", upgrade_run.create_test)
    assert name == "shop-prod-odoo19" and plan["version"] == "19.0" and plan["stage"] == "staging"
    assert plan["branch"] == "19.0" and plan["repo"] == "https://github.com/Acme/Shop"
    assert plan["project"] == project["id"] and plan["auto_update"] is True
    assert plan["upgrade"] == {"source": "shop-prod", "target": "19.0", "method": "openupgrade", "code": None}
    assert upgrades.get("shop-prod")["state"] == "testing"
    again = client.post("/api/v1/instances/shop-prod/upgrade/test", json={"target": "19.0"}, headers=headers)
    assert again.status_code == 409  # one test at a time


def test_enterprise_needs_the_terms_and_the_code(monkeypatch: pytest.MonkeyPatch, admin: Any,
                                                 started: list[Any]) -> None:  # fmt: skip
    monkeypatch.setattr(addons, "available", lambda version: True)
    _instance("ent-prod", edition="enterprise")
    _checked(monkeypatch, admin, "ent-prod")
    client, headers = admin
    url = "/api/v1/instances/ent-prod/upgrade/test"
    no_terms = client.post(url, json={"target": "19.0", "subscription_code": CODE}, headers=headers)
    assert no_terms.status_code == 400 and "Odoo S.A." in no_terms.get_json()["detail"]
    no_code = client.post(url, json={"target": "19.0", "accept_odoo_terms": True}, headers=headers)
    assert no_code.status_code == 400
    answer = client.post(url, json={"target": "19.0", "accept_odoo_terms": True, "subscription_code": CODE},
                         headers=headers)  # fmt: skip
    assert answer.status_code == 202, answer.get_json()
    plan = started[-1][3][0]
    store.release_ports(plan["ports"])
    assert isinstance(plan["upgrade"]["code"], Secret) and plan["upgrade"]["code"].reveal() == CODE
    assert CODE not in repr(plan)
    assert CODE not in str(storage().get(upgrades.COLLECTION, "ent-prod"))
    assert all(CODE not in str(entry) for entry in storage().logs(50))
    assert upgrades.resource("ent-prod")["terms_url"].startswith("https://www.odoo.com/")


def _fake_backup(name: str, kind: str) -> dict[str, Any]:
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    db = store.read_env(name)["DB_NAME"]
    path = settings.backups_dir / f"{name}__{db}__20261118-010000__{kind}.zip"
    path.write_bytes(b"zip")
    return {"file": path.name, "instance": name, "db": db, "created": time.time(), "kind": kind}


def test_openupgrade_runs_the_pinned_sources_in_a_temporary_container(monkeypatch: pytest.MonkeyPatch) -> None:
    _instance("shop-prod-odoo19", ODOO_VERSION="19.0", STAGE="staging")
    job = Job("shop-prod-odoo19", "Create instance", "create", "t")
    commands: list[list[str]] = []
    monkeypatch.setattr(job, "run_unchecked", lambda cmd, **_: commands.append(cmd) or 0)
    monkeypatch.setattr(methods, "database_version", lambda name, db: "19.0.1.3")
    upgrade_run.migrate_copy(job, "shop-prod-odoo19", "shop_prod_odoo19", {"target": "19.0", "method": "openupgrade"})
    command = commands[0]
    assert command[:2] == ["docker", "compose"] and "run" in command and "--rm" in command
    pinned = upgrades.openupgrade_refs()["19.0"]
    assert f"https://github.com/OCA/OpenUpgrade/archive/{pinned}.tar.gz" in command
    assert settings.openupgrade_lib in command
    assert command[command.index("-u") + 1] == "all"
    assert "base,web,openupgrade_framework" in command
    assert command[command.index("--addons-path") + 1] == "/mnt/extra-addons,/tmp/openupgrade"
    monkeypatch.setattr(methods, "database_version", lambda name, db: "18.0.1.3")
    with pytest.raises(JobError, match="not at Odoo 19.0"):
        upgrade_run.migrate_copy(job, "shop-prod-odoo19", "shop_prod_odoo19",
                                 {"target": "19.0", "method": "openupgrade"})  # fmt: skip


def test_odoos_service_gets_the_code_once_and_never_logs_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Odoo's upgrade script runs in a temporary container (mocked here): it reads the code from a file it deletes,
    and the upgraded dump it downloads is loaded."""
    _instance("ent-prod", edition="enterprise")
    job = Job("ent-prod", "Upgrade", "db-upgrade", "t")
    seen: dict[str, Any] = {}

    def run(cmd: list[str], timeout: float | None = None, input: str | None = None) -> int:
        job.write(f"$ {' '.join(cmd)}\n")
        if cmd[:2] == ["docker", "create"]:
            work = Path(cmd[cmd.index("-v") + 1].split(":")[0])
            seen["work"], seen["create"] = work, cmd
            seen["contract"] = (work / ".contract").read_text()
            seen["mode"] = (work / ".contract").stat().st_mode & 0o777
            seen["env"] = (work / ".env").read_text()
        if cmd[:3] == ["docker", "start", "-a"]:
            work = seen["work"]
            (work / ".contract").unlink()  # what the container's script does
            job.write(f"Fallback to the one given as parameter: {CODE}\n")  # the script may print it
            (work / "upgraded.dump").mkdir()
            (work / "upgraded.dump" / "toc.dat").write_text("toc")
            (work / "filestore").mkdir()
            (work / "filestore" / "ab").mkdir()
            (work / "filestore" / "ab" / "new").write_text("file")
        return 0

    monkeypatch.setattr(job, "run", lambda cmd, **kw: run(cmd))
    monkeypatch.setattr(job, "run_unchecked", lambda cmd, **kw: run(cmd))
    with methods.work_folder() as work:
        dump = methods.odoo_service(job, "ent-prod", "ent_prod", "19.0", "test", Secret(CODE), work)
        assert dump.name == "upgraded.dump"
        assert not (work / ".contract").exists() and not (work / ".env").exists()
    assert not seen["work"].exists()  # the work folder is removed
    assert seen["contract"] == CODE and seen["mode"] == 0o600
    assert "PGPASSWORD=db-pass-123" in seen["env"] and settings.upgrade_service_url in seen["env"]
    create = seen["create"]
    assert create[create.index("--network") + 1] == "bridge"
    assert create[-7:] == ["sh", "test", "-d", "ent_prod", "-t", "19.0", "--no-restore"]
    log = "".join(job.log)
    assert CODE not in log and "db-pass-123" not in log and "********" in log
    assert "/upgrade" in methods.ODOO_SCRIPT and "--contract" in methods.ODOO_SCRIPT

    def failed(cmd: list[str]) -> int:
        return 0

    monkeypatch.setattr(job, "run", lambda cmd, **kw: failed(cmd))
    monkeypatch.setattr(job, "run_unchecked", lambda cmd, **kw: failed(cmd))
    with methods.work_folder() as work, pytest.raises(JobError, match="did not return an upgraded database"):
        methods.odoo_service(job, "ent-prod", "ent_prod", "19.0", "test", Secret(CODE), work)
    with methods.work_folder() as work, pytest.raises(JobError, match="subscription code"):
        methods.odoo_service(job, "ent-prod", "ent_prod", "19.0", "test", Secret(""), work)


def test_a_rerun_copies_upgrades_neutralises_and_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    _instance("shop-prod")
    _instance("shop-prod-odoo19", ODOO_VERSION="19.0", STAGE="staging", GIT_BRANCH="19.0")
    upgrades.update("shop-prod", target="19.0", staging="shop-prod-odoo19", state="tested")
    steps: list[str] = []
    copy = _fake_backup("shop-prod", "staging-copy")

    def backup(job: Job, name: str, kind: str = "manual") -> list[Path]:
        steps.append(f"backup {name} {kind} locked={jobs.INSTANCE_LOCKS[name].locked()}")
        return [settings.backups_dir / copy["file"]]

    monkeypatch.setattr(backups, "create", backup)
    monkeypatch.setattr(backups, "restore", lambda job, name, b, db: steps.append(f"restore {name} {db}"))
    monkeypatch.setattr(backups, "neutralize", lambda job, name, db: steps.append("neutralize") or "ok")
    monkeypatch.setattr(operations, "_require_branch", lambda job, name, addons: None)
    monkeypatch.setattr(operations, "_pull", lambda job, addons: steps.append("pull"))
    monkeypatch.setattr(operations, "_adopt_admin", lambda job, name, db: steps.append("admin") or "admin")
    monkeypatch.setattr(operations, "refresh_addons_path", lambda job, name: None)
    monkeypatch.setattr(operations, "_finish_restore", lambda job, name: steps.append("start"))
    monkeypatch.setattr(methods, "openupgrade", lambda job, name, db, target: steps.append(f"openupgrade {target}"))
    monkeypatch.setattr(methods, "verify", lambda job, name, db, target: steps.append("verify"))
    job = Job("shop-prod-odoo19", "Re-run", "upgrade-test", "t")
    monkeypatch.setattr(job, "run", lambda cmd, **_: None)
    upgrade_run.rerun(job, "shop-prod-odoo19", "shop-prod", None)
    assert steps == ["backup shop-prod staging-copy locked=True", "pull", "restore shop-prod-odoo19 shop_prod_odoo19",
                     "openupgrade 19.0", "verify", "neutralize", "admin", "start"]  # fmt: skip
    assert upgrades.get("shop-prod")["state"] == "tested"


def test_a_push_to_the_upgrade_branch_runs_the_test_again() -> None:
    project = projects.create(repository="Acme/Shop", odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    _instance("shop-prod", PROJECT=project["id"])
    _instance("shop-prod-odoo19", ODOO_VERSION="19.0", STAGE="staging", GIT_BRANCH="19.0", PROJECT=project["id"])
    upgrades.update("shop-prod", target="19.0", staging="shop-prod-odoo19", state="tested")
    found = automation.update_for("shop-prod-odoo19")
    assert found is not None and found[1:3] == ("upgrade-test", upgrade_run.rerun)
    assert found[3] == ("shop-prod-odoo19", "shop-prod", None)
    projects.change(project["id"], lambda p: {**p, "options": {"upgrade_test_on_push": False}})
    assert automation.update_for("shop-prod-odoo19") is None or automation.update_for("shop-prod-odoo19")[1] != (
        "upgrade-test"
    )
    projects.change(project["id"], lambda p: {**p, "options": {}})
    store.update_env("shop-prod", EDITION="enterprise")  # Odoo's service: the code is never kept
    assert automation.update_for("shop-prod-odoo19") is None or automation.update_for("shop-prod-odoo19")[1] != (
        "upgrade-test"
    )


# ---------------------------------------------------------------- production


def test_promotion_needs_a_test_and_a_confirmation(monkeypatch: pytest.MonkeyPatch, admin: Any,
                                                   started: list[Any]) -> None:  # fmt: skip
    _instance("shop-prod")
    client, headers = admin
    url = "/api/v1/instances/shop-prod/upgrade/promote"
    assert client.post(url, json={"confirm_production": True}, headers=headers).status_code == 409
    upgrades.update("shop-prod", target="19.0", branch="19.0", state="tested", tested_at=time.time())
    assert client.post(url, json={}, headers=headers).status_code == 400
    answer = client.post(url, json={"confirm_production": True}, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    assert started[-1][:3] == ("shop-prod", "db-upgrade", upgrade_run.promote)
    upgrades.update("shop-prod", branch=None)
    refused = client.post(url, json={"confirm_production": True}, headers=headers)
    assert refused.status_code == 409 and "no branch 19.0" in refused.get_json()["detail"]


class _Promotion:
    """Records what a promotion does, with every step mocked."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, fail_at: str | None = None) -> None:
        self.steps: list[str] = []
        self.fail_at = fail_at
        steps = self.steps

        def step(what: str) -> Any:
            def do(*args: Any, **kwargs: Any) -> Any:
                steps.append(what)
                if what == self.fail_at:
                    raise JobError(f"{what} failed")
                return "x"

            return do

        def backup(job: Job, name: str, kind: str = "manual") -> list[Path]:
            steps.append(f"backup {kind}")
            return [settings.backups_dir / _fake_backup(name, kind)["file"]]

        monkeypatch.setattr(backups, "create", backup)
        monkeypatch.setattr(backups, "restore", lambda job, name, b, db=None: steps.append(f"restore {b['kind']}"))
        monkeypatch.setattr(upgrade_run, "_switch_code", lambda job, name, branch: steps.append(f"code {branch}")
                            or store.update_env(name, GIT_BRANCH=branch))  # fmt: skip
        monkeypatch.setattr(integrity, "pinned_reference", lambda job, ref: f"{ref}@sha256:{'1' * 64}")
        monkeypatch.setattr(integrity, "retarget", lambda job, name, image, folder: steps.append(f"image {image[:9]}"))
        monkeypatch.setattr(methods, "openupgrade", step("openupgrade"))
        monkeypatch.setattr(methods, "verify", step("verify"))
        monkeypatch.setattr(operations, "refresh_addons_path", lambda job, name: None)
        monkeypatch.setattr(operations, "_start_odoo", step("start"))
        monkeypatch.setattr(operations, "_finish_restore", lambda job, name: steps.append("started again"))
        monkeypatch.setattr(upgrade_run, "capture", lambda cmd, timeout=30: "f" * 40)

    def job(self, name: str, monkeypatch: pytest.MonkeyPatch) -> Job:
        job = Job(name, "Upgrade to Odoo 19.0", "db-upgrade", "t")
        monkeypatch.setattr(job, "run", lambda cmd, **_: self.steps.append(" ".join(cmd[-2:])))
        monkeypatch.setattr(job, "run_unchecked", lambda cmd, **_: self.steps.append(" ".join(cmd[-2:])) or 0)
        return job


def test_promotion_upgrades_production_and_opens_the_rollback_window(monkeypatch: pytest.MonkeyPatch) -> None:
    project = projects.create(repository="Acme/Shop", odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    projects.record_branch(project["id"], "main", stage="production")
    _instance("shop-prod", PROJECT=project["id"])
    upgrades.update("shop-prod", target="19.0", branch="19.0", state="tested", tested_at=time.time())
    promotion = _Promotion(monkeypatch)
    upgrade_run.promote(promotion.job("shop-prod", monkeypatch), "shop-prod", None)
    assert promotion.steps == ["stop odoo", "backup before-upgrade", "code 19.0", "image odoo:19.0", "openupgrade",
                               "verify", "start"]  # fmt: skip
    doc = upgrades.get("shop-prod")
    assert doc["state"] == "promoted" and upgrades.rollback_open(doc)
    assert doc["promotion"]["backup"][0].endswith("__before-upgrade.zip")
    assert store.read_env("shop-prod")["ODOO_VERSION"] == "19.0"
    branches = projects.get(project["id"])["branches"]
    assert branches["19.0"]["stage"] == "production" and "stage" not in branches["main"]

    # cancelled within the window: back to the backup, the old version and branch
    cancel = _Promotion(monkeypatch)
    upgrade_run.cancel(cancel.job("shop-prod", monkeypatch), "shop-prod")
    assert "backup before-rollback" in cancel.steps and "restore before-upgrade" in cancel.steps
    assert cancel.steps[-1] == "started again"
    assert store.read_env("shop-prod")["ODOO_VERSION"] == "18.0"
    assert store.read_env("shop-prod")["GIT_BRANCH"] == "main"
    assert upgrades.get("shop-prod")["state"] == "rolled_back"
    assert projects.get(project["id"])["branches"]["main"]["stage"] == "production"


def test_a_failed_promotion_puts_production_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _instance("shop-prod")
    upgrades.update("shop-prod", target="19.0", branch="19.0", state="tested", tested_at=time.time())
    promotion = _Promotion(monkeypatch, fail_at="start")
    with pytest.raises(JobError, match="was put back"):
        upgrade_run.promote(promotion.job("shop-prod", monkeypatch), "shop-prod", None)
    assert promotion.steps[-3:] == ["image odoo:18.0", "restore before-upgrade", "started again"]
    assert store.read_env("shop-prod")["ODOO_VERSION"] == "18.0"
    assert upgrades.get("shop-prod")["state"] == "promotion_failed"
    assert not upgrades.rollback_open(upgrades.get("shop-prod"))


def test_the_watch_rolls_back_when_odoo_stops_answering(monkeypatch: pytest.MonkeyPatch,
                                                        started: list[Any]) -> None:  # fmt: skip
    _instance("shop-prod")
    upgrades.update("shop-prod", state="promoted", promotion={"rollback_until": time.time() + 600})
    monkeypatch.setattr(odoo, "responds", lambda port, timeout=2: False)
    for _ in range(upgrade_run.WATCH_FAILURES - 1):
        assert upgrade_run.watch_once() == []
    assert upgrade_run.watch_once() == ["shop-prod"]
    assert started[-1][:3] == ("shop-prod", "db-upgrade", upgrade_run.cancel)
    # after the window nothing happens
    upgrades.update("shop-prod", promotion={"rollback_until": time.time() - 1})
    for _ in range(upgrade_run.WATCH_FAILURES):
        assert upgrade_run.watch_once() == []


def test_cancel_and_keep_through_the_api(admin: Any, started: list[Any]) -> None:
    _instance("shop-prod")
    client, headers = admin
    assert client.post("/api/v1/instances/shop-prod/upgrade/cancel", json={"confirm_production": True},
                       headers=headers).status_code == 409  # fmt: skip
    upgrades.update("shop-prod", state="promoted", promotion={"rollback_until": time.time() + 600})
    assert client.post("/api/v1/instances/shop-prod/upgrade/cancel", json={}, headers=headers).status_code == 400
    answer = client.post("/api/v1/instances/shop-prod/upgrade/cancel", json={"confirm_production": True},
                         headers=headers)  # fmt: skip
    assert answer.status_code == 202 and started[-1][:3] == ("shop-prod", "db-upgrade", upgrade_run.cancel)
    assert client.delete("/api/v1/instances/shop-prod/upgrade", headers=headers).status_code == 409
    kept = client.post("/api/v1/instances/shop-prod/upgrade/keep", json={}, headers=headers)
    assert kept.status_code == 200 and kept.get_json()["data"]["rollback_open"] is False
    assert client.delete("/api/v1/instances/shop-prod/upgrade", headers=headers).status_code == 204


def test_visitors_see_the_upgrading_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _instance("shop-prod")
    job = Job("shop-prod", "Upgrade to Odoo 19.0", "db-upgrade", "t")
    monkeypatch.setitem(jobs.ACTIVE_JOB, "shop-prod", job)
    assert gateway.wake_decision("shop-prod")[0] == "upgrading"


def test_the_job_log_masks_secrets() -> None:
    job = Job("shop-prod", "x", "x", "t")
    job.mask(CODE)
    job.write(f"contract {CODE} used\n")
    assert CODE not in "".join(job.log)
