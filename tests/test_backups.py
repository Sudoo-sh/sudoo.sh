"""Backups that go anywhere: restoring another instance's backup, rollback after a restore, creating an instance from
a backup. Docker is never run: the jobs are not started (their function and arguments are checked), and the steps
that would run commands are replaced."""

from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from sudoo import accounts
from sudoo.config import live, settings
from sudoo.core import jobs
from sudoo.core.state import STATE
from sudoo.instances import backups, operations, store

Client = tuple[FlaskClient, dict[str, str]]


def _instance(
    name: str,
    version: str = "18.0",
    edition: str = "community",
    created_hours_ago: float = 30 * 24,
    instance_id: str | None = None,
) -> None:
    """An instance created `created_hours_ago` (before the backups the tests make, unless they say otherwise)."""
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {
            "ODOO_VERSION": version,
            "EDITION": edition,
            "DB_NAME": name.replace("-", "_"),
            "GIT_BRANCH": "main",
            "AUTO_UPDATE": "false",
            "AUTO_UPGRADE": "false",
            "AUTO_PAUSE": "false",
            "DELETE_AFTER_DAYS": "0",
            "CREATED_AT": int(time.time() - created_hours_ago * 3600),
            **({"INSTANCE_ID": instance_id} if instance_id else {}),
        },
    )


def _backup(
    instance: str,
    kind: str = "manual",
    version: str | None = "18.0",
    age_hours: float = 1,
    edition: str | None = None,
    stamp: str = "20261112-000000",
    instance_id: str | None = None,
) -> str:
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    name = f"{instance}__{instance.replace('-', '_')}__{stamp}__{kind}.zip"
    path = settings.backups_dir / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("dump.sql", "-- empty\n")
        if version is not None:
            manifest: dict[str, Any] = {"db_name": instance, "version": version}
            if edition:
                manifest["edition"] = edition
            if instance_id:
                manifest["instance_id"] = instance_id
            archive.writestr("manifest.json", json.dumps(manifest))
    taken = time.time() - age_hours * 3600
    os.utime(path, (taken, taken))
    return name


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    shutil.rmtree(settings.backups_dir, ignore_errors=True)
    for name in ("shop", "lab", "old", "lab-copy"):
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, tuple[Any, ...]]]:
    """Jobs are recorded (function, arguments) instead of run."""
    calls: list[tuple[Any, tuple[Any, ...]]] = []

    def fake_start(
        instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any
    ) -> jobs.Job:
        calls.append((fn, args))
        return jobs.Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    return calls


@pytest.fixture(scope="module")
def maintainer(app: Flask) -> Client:
    password = "walnut-harbor-quiet-58"
    accounts.create("maint1", "maintainer", password)
    return sign_in(app, "maint1", password)


# ---------------------------------------------------------------- what a backup is of


def test_series_of_odoo_versions() -> None:
    assert backups.series("17.0") == "17.0"
    assert backups.series("17.0+e") == "17.0"
    assert backups.series("17.0-20240101") == "17.0"
    assert backups.series("saas~17.1") == "17.1"
    assert backups.series("") is None and backups.series(None) is None and backups.series("latest") is None


def test_version_mismatch_and_unknown_versions_are_refused() -> None:
    same, other, unknown = _backup("shop"), _backup("lab", version="17.0"), _backup("old", version=None)
    assert backups.problem(same, "18.0", "community") is None
    assert "Odoo 17.0" in (backups.problem(other, "18.0", "community") or "")
    assert "manifest.json" in (backups.problem(unknown, "18.0", "community") or "")
    enterprise = _backup("lab", edition="enterprise", stamp="20261112-010000")
    assert "Enterprise" in (backups.problem(enterprise, "18.0", "community") or "")
    assert backups.problem(enterprise, "18.0", "enterprise") is None


def test_backup_listing_shows_version_and_edition(admin: Client) -> None:
    _backup("shop", edition="community")
    client, _ = admin
    listed = client.get("/api/v1/backups").get_json()["data"]
    assert listed[0]["version"] == "18.0" and listed[0]["series"] == "18.0" and listed[0]["edition"] == "community"


# ---------------------------------------------------------------- restoring another instance's backup


def test_restore_refuses_unknown_files(admin: Client, started: list[Any]) -> None:
    _instance("shop")
    client, headers = admin
    for file_name in ("nope__nope__20261112-000000__manual.zip", "../../etc/passwd"):
        answer = client.post("/api/v1/instances/shop/actions/restore", json={"file": file_name}, headers=headers)
        assert answer.status_code == 404
    assert not started


def test_restore_of_another_instance_checks_version_and_permission(
    admin: Client, maintainer: Client, started: list[Any]
) -> None:
    _instance("shop")
    _instance("lab")
    foreign, wrong_version = _backup("lab"), _backup("lab", version="17.0", stamp="20261112-010000")
    client, headers = admin
    answer = client.post("/api/v1/instances/shop/actions/restore", json={"file": wrong_version}, headers=headers)
    assert answer.status_code == 400 and "Odoo 17.0" in answer.get_json()["detail"]

    # a maintainer may restore the instance's own backups, not another instance's (that needs backups.manage)
    own = _backup("shop")
    m_client, m_headers = maintainer
    answer = m_client.post("/api/v1/instances/shop/actions/restore", json={"file": foreign}, headers=m_headers)
    assert answer.status_code == 403
    # refused before looking: whether another instance's backup exists is not revealed either
    unknown = "lab__lab__20201112-000000__manual.zip"
    answer = m_client.post("/api/v1/instances/shop/actions/restore", json={"file": unknown}, headers=m_headers)
    assert answer.status_code == 403
    answer = m_client.post("/api/v1/instances/shop/actions/restore", json={"file": own}, headers=m_headers)
    assert answer.status_code == 202

    answer = client.post("/api/v1/instances/shop/actions/restore", json={"file": foreign}, headers=headers)
    assert answer.status_code == 202
    assert started[-1] == (operations.restore, ("shop", foreign))


def test_cross_instance_restore_uses_the_target_database_and_neutralises(monkeypatch: pytest.MonkeyPatch) -> None:
    _instance("shop")
    _instance("lab")
    foreign, own = _backup("lab"), _backup("shop")
    done: list[tuple[str, Any]] = []
    monkeypatch.setattr(backups, "create", lambda job, name, kind="manual": done.append(("backup", kind)) or [])
    monkeypatch.setattr(backups, "restore", lambda job, name, b, db=None: done.append(("restore", (b["file"], db))))
    monkeypatch.setattr(backups, "neutralize", lambda job, name, db: done.append(("neutralize", db)) or "neutral")
    monkeypatch.setattr(operations, "_adopt_admin", lambda job, name, db: done.append(("admin", db)) or "boss")
    monkeypatch.setattr(operations, "_finish_restore", lambda job, name: None)
    job = jobs.Job("shop", "Restore", "restore", "test")
    monkeypatch.setattr(job, "run", lambda *args, **kwargs: None)

    assert operations.target_database("shop", backups.all_backups()[0]) == "shop"
    operations.restore(job, "shop", foreign)
    assert done == [
        ("backup", "before-restore"),
        ("restore", (foreign, "shop")),
        ("neutralize", "shop"),
        ("admin", "shop"),  # the copied administrator gets this instance's password, so its credentials work
    ]
    assert "neutral" in [note["text"] for note in job.notes]
    assert any("(boss)" in note["text"] for note in job.notes)

    done.clear()
    operations.restore(job, "shop", own)
    assert done == [("backup", "before-restore"), ("restore", (own, "shop"))]  # its own data: not neutralised


def test_the_filestore_folder_is_an_argument_of_the_restore_script(monkeypatch: pytest.MonkeyPatch) -> None:
    """The database name (DB_NAME of .env for another instance's backup) never becomes part of the root script."""
    _instance("shop")
    _backup("lab")
    backup = backups.all_backups()[0]
    fed: list[list[str]] = []
    monkeypatch.setattr(backups, "_database_up", lambda job, name: None)
    monkeypatch.setattr(backups, "_feed", lambda cmd, data, what: fed.append(cmd))
    job = jobs.Job("shop", "Restore", "restore", "test")
    monkeypatch.setattr(job, "run", lambda *args, **kwargs: None)
    for db in ("a;cd ~odoo;sh x #", "shop/../../x", "Shop", "$(x)"):
        with pytest.raises(jobs.JobError):
            backups.restore(job, "shop", backup, db)
    assert not fed

    backups.restore(job, "shop", backup, "shop")
    script = fed[-1][-3]
    assert fed[-1][-5:] == ["odoo", "-c", script, "sh", "/var/lib/odoo/filestore/shop"]
    assert "shop" not in script and '"$1"' in script


@pytest.mark.parametrize("ids", [True, False])  # instances and backups with INSTANCE_ID, and older ones without
def test_a_new_instance_does_not_own_the_backups_of_a_deleted_one_of_the_same_name(
    ids: bool, admin: Client, maintainer: Client, started: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _instance("shop", instance_id="1" * 32 if ids else None)
    deletion = _backup("shop", "deletion", instance_id="1" * 32 if ids else None)
    assert backups.belongs_to("shop", backups.all_backups()[0]) and backups.latest("shop") is not None
    shutil.rmtree(store.folder("shop"))  # deleted; a maintainer creates an instance of the same name again
    _instance("shop", created_hours_ago=0, instance_id="2" * 32 if ids else None)

    old = backups.all_backups()[0]
    assert not backups.belongs_to("shop", old) and backups.latest("shop") is None
    assert backups.own_backups("shop") == [] and backups.rollback_snapshot("shop") == []
    m_client, m_headers = maintainer
    answer = m_client.post("/api/v1/instances/shop/actions/restore", json={"file": deletion}, headers=m_headers)
    assert answer.status_code == 403
    answer = m_client.post("/api/v1/instances/shop/actions/restore", json={}, headers=m_headers)
    assert answer.status_code == 409
    assert not started

    # with backups.manage it can be put back, as another instance's data: neutralised, the administrator adopted
    client, headers = admin
    answer = client.post("/api/v1/instances/shop/actions/restore", json={"file": deletion}, headers=headers)
    assert answer.status_code == 202 and started[-1] == (operations.restore, ("shop", deletion))
    done: list[str] = []
    monkeypatch.setattr(backups, "create", lambda job, name, kind="manual": [])
    monkeypatch.setattr(backups, "restore", lambda job, name, b, db=None: done.append("restore"))
    monkeypatch.setattr(backups, "neutralize", lambda job, name, db: done.append("neutralize") or "neutral")
    monkeypatch.setattr(operations, "_adopt_admin", lambda job, name, db: done.append("admin") or "admin")
    monkeypatch.setattr(operations, "_finish_restore", lambda job, name: None)
    job = jobs.Job("shop", "Restore", "restore", "test")
    monkeypatch.setattr(job, "run", lambda *args, **kwargs: None)
    operations.restore(job, "shop", deletion)
    assert done == ["restore", "neutralize", "admin"]

    # backups the new instance takes are its own
    _backup("shop", "manual", age_hours=0, stamp="20261113-000000", instance_id="2" * 32 if ids else None)
    assert backups.latest("shop")["file"] != deletion  # type: ignore[index]
    m_client.post("/api/v1/instances/shop/actions/restore", json={}, headers=m_headers)
    assert started[-1] == (operations.restore, ("shop", None))


def test_backups_record_the_instance_they_were_taken_from(monkeypatch: pytest.MonkeyPatch) -> None:
    _instance("shop", instance_id="3" * 32)
    monkeypatch.setattr(backups, "_database_up", lambda job, name: None)

    def fake_copy(cmd: list[str], path: Any, what: str) -> None:
        if what == "file storage copy":
            import tarfile

            with tarfile.open(path, "w"):
                pass
        else:
            path.write_text("-- dump\n")

    monkeypatch.setattr(backups, "_run_to_file", fake_copy)
    (saved,) = backups.create(jobs.Job("shop", "Back up", "backup", "test"), "shop")
    assert backups.instance_id(saved.name) == "3" * 32 and backups.belongs_to("shop", backups.all_backups()[0])
    assert "instance_id" not in backups.details(saved.name)  # not part of what the API shows


# ---------------------------------------------------------------- rollback


def test_rollback_snapshot_is_kept_for_rollback_hours() -> None:
    _instance("shop")
    assert backups.rollback_snapshot("shop") == []
    recent = _backup("shop", "before-restore", age_hours=2)
    assert [b["file"] for b in backups.rollback_snapshot("shop")] == [recent]
    assert backups.expired_snapshots() == []
    live.change({"rollback_hours": 1})
    assert backups.rollback_snapshot("shop") == []
    assert [b["file"] for b in backups.expired_snapshots()] == [recent]


def test_expired_snapshots_are_removed_but_not_while_a_job_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo import background

    _instance("shop")
    old = _backup("shop", "before-restore", age_hours=30)
    old_rollback = _backup("shop", "before-rollback", age_hours=30, stamp="20261112-010000")
    kept = _backup("shop", "manual", age_hours=30, stamp="20261112-020000")
    monkeypatch.setitem(jobs.ACTIVE_JOB, "lab", jobs.Job("lab", "Restore", "restore", "test"))
    assert background.remove_expired_snapshots() == []
    monkeypatch.delitem(jobs.ACTIVE_JOB, "lab")
    assert sorted(background.remove_expired_snapshots()) == sorted([old, old_rollback])
    assert [b["file"] for b in backups.all_backups()] == [kept]


def test_rollback_never_replaces_the_snapshot_it_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.web.v1.resources import rollback

    _instance("shop")
    snapshot = _backup("shop", "before-restore", age_hours=1)
    assert rollback("shop") is not None
    kinds: list[str] = []
    restored: list[str] = []
    monkeypatch.setattr(backups, "create", lambda job, name, kind="manual": kinds.append(kind) or [])
    monkeypatch.setattr(backups, "restore", lambda job, name, b, db=None: restored.append(b["file"]))
    monkeypatch.setattr(operations, "_finish_restore", lambda job, name: None)
    job = jobs.Job("shop", "Roll back", "restore", "test")
    monkeypatch.setattr(job, "run", lambda *args, **kwargs: None)

    operations.rollback(job, "shop")
    assert kinds == ["before-rollback"] and restored == [snapshot]
    assert rollback("shop") is None  # offered no more once used
    kinds.clear()
    operations.restore(job, "shop", snapshot)  # restoring the snapshot by hand is a rollback too
    assert kinds == ["before-rollback"]


def test_rollback_goes_on_when_the_current_state_cannot_be_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    _instance("shop")
    _backup("shop", "before-restore")

    def broken(job: jobs.Job, name: str, kind: str = "manual") -> list[Any]:
        raise jobs.JobError("database dump failed")

    restored: list[str] = []
    monkeypatch.setattr(backups, "create", broken)
    monkeypatch.setattr(backups, "restore", lambda job, name, b, db=None: restored.append(b["file"]))
    monkeypatch.setattr(operations, "_finish_restore", lambda job, name: None)
    job = jobs.Job("shop", "Roll back", "restore", "test")
    monkeypatch.setattr(job, "run", lambda *args, **kwargs: None)
    operations.rollback(job, "shop")
    assert restored and job.notes[0]["level"] == "warning"


def test_rollback_action(admin: Client, started: list[Any]) -> None:
    from sudoo.web.v1.resources import rollback

    _instance("shop")
    client, headers = admin
    assert client.post("/api/v1/instances/shop/actions/rollback", json={}, headers=headers).status_code == 409
    snapshot = _backup("shop", "before-restore")
    assert client.post("/api/v1/instances/shop/actions/rollback", json={}, headers=headers).status_code == 202
    assert started[-1] == (operations.rollback, ("shop",))
    # once rolled back to it, the snapshot is not offered (GET shows "rollback": null) and the action is refused
    STATE.update("shop", rolled_back=snapshot)
    assert rollback("shop") is None
    answer = client.post("/api/v1/instances/shop/actions/rollback", json={}, headers=headers)
    assert answer.status_code == 409 and "rolled back already" in answer.get_json()["detail"]
    _backup("shop", "before-restore", age_hours=0.5, stamp="20261112-010000")  # a new restore: offered again
    assert client.post("/api/v1/instances/shop/actions/rollback", json={}, headers=headers).status_code == 202


def test_credentials_show_the_administrator_login_of_a_backup(admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.core import vault

    _instance("shop")
    store.update_env(
        "shop", ODOO_ADMIN_PASSWORD="admin", MASTER_PASSWORD=vault.encrypt("m", store.master_password_label("shop"))
    )
    client, headers = admin
    assert client.get("/api/v1/instances/shop/credentials", headers=headers).get_json()["data"]["login"] == "admin"

    monkeypatch.setattr(operations, "capture", lambda cmd, timeout=30: "boss@example.com")
    assert operations._record_admin_login("shop", "shop") == "boss@example.com"
    assert store.read_env("shop")["ODOO_ADMIN_LOGIN"] == "boss@example.com"
    data = client.get("/api/v1/instances/shop/credentials", headers=headers).get_json()["data"]
    assert data["login"] == "boss@example.com"

    monkeypatch.setattr(operations, "capture", lambda cmd, timeout=30: "")  # unreadable: the kept login stays
    assert operations._record_admin_login("shop", "shop") == "boss@example.com"


@pytest.mark.parametrize(
    "login",
    [
        "admin\nODOO_PORT=2375\nGATEWAY_PORT=8000\nDELETE_AFTER_DAYS=1\nCREATED_AT=0\nCREATED_BY=someone-else",
        "admin\rEDITION=enterprise",
        "admin\x00x",
        "a" * 201,
    ],
)
def test_an_administrator_login_from_the_database_cannot_change_env(
    login: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The login comes from the instance's own database: only a short single line is recorded, anything else leaves
    .env as it was."""
    _instance("shop")
    store.update_env("shop", ODOO_ADMIN_LOGIN="boss", ODOO_PORT=8070, GATEWAY_PORT=8100, CREATED_BY="admin1")
    before = store.read_env("shop")
    raw = (store.folder("shop") / ".env").read_text()
    monkeypatch.setattr(operations, "capture", lambda cmd, timeout=30: login + "\n")
    assert operations._record_admin_login("shop", "shop") == "boss"
    assert store.read_env("shop") == before and (store.folder("shop") / ".env").read_text() == raw
    assert operations.expires_at(store.read_env("shop")) is None


def test_env_writer_refuses_line_breaks_and_odd_keys() -> None:
    _instance("shop")
    raw = (store.folder("shop") / ".env").read_text()
    for values in ({"ODOO_ADMIN_LOGIN": "a\nODOO_PORT=1"}, {"ODOO_ADMIN_LOGIN": "a\tb"}, {"x": "1"}, {"A B": "1"}):
        with pytest.raises(ValueError):
            store.update_env("shop", **values)
    assert (store.folder("shop") / ".env").read_text() == raw
    store.update_env("shop", ODOO_ADMIN_LOGIN="boss@example.com")
    assert store.read_env("shop")["ODOO_ADMIN_LOGIN"] == "boss@example.com"


def test_rollback_hours_is_a_documented_live_setting() -> None:
    from sudoo.config import EDITABLE

    assert "rollback_hours" in EDITABLE and settings.rollback_hours == 24


# ---------------------------------------------------------------- creating an instance from a backup


def test_create_from_backup_plan(admin: Client, maintainer: Client, started: list[Any], monkeypatch: Any) -> None:
    from sudoo.limits import quotas

    monkeypatch.setattr(quotas, "claim", lambda user: None)
    _instance("lab")
    source, older = _backup("lab"), _backup("lab", version="17.0", stamp="20261112-010000")
    body = {"name": "lab copy", "version": "18.0", "edition": "community", "delete_after_days": 0}
    client, headers = admin
    answer = client.post("/api/v1/instances", json={**body, "from_backup": older}, headers=headers)
    assert answer.status_code == 400 and "Odoo 17.0" in answer.get_json()["detail"]
    answer = client.post("/api/v1/instances", json={**body, "from_backup": "nope.zip"}, headers=headers)
    assert answer.status_code == 404
    m_client, m_headers = maintainer
    answer = m_client.post("/api/v1/instances", json={**body, "from_backup": source}, headers=m_headers)
    assert answer.status_code == 403
    assert not started

    answer = client.post("/api/v1/instances", json={**body, "from_backup": source}, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    fn, (plan,) = started[-1]
    assert fn is operations.create and plan["from_backup"] == source and plan["name"] == "lab-copy"
    store.release_ports(plan["ports"])

    # the job checks again: the backup may be gone by the time it runs
    assert operations._backup_to_start_from({**plan, "from_backup": None}) is None
    assert operations._backup_to_start_from(plan)["file"] == source  # type: ignore[index]
    (settings.backups_dir / source).unlink()
    with pytest.raises(jobs.JobError):
        operations._backup_to_start_from(plan)
