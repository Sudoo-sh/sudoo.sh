"""Safe code updates (operations.update_code): a before-update backup when the code changes, and the instance put
back when a later step fails. Git runs for real on repositories in a temporary folder; Docker never runs (the steps
that would use it are replaced and recorded)."""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sudoo.config import live, settings
from sudoo.core import audit, jobs
from sudoo.core.hints import friendly_hint
from sudoo.core.state import STATE
from sudoo.instances import backups, operations, store
from sudoo.triggers.polling import Polling

NAME = "upd"
GIT_USER = ["-c", "user.name=t", "-c", "user.email=t@t"]


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *GIT_USER, *args], check=True, capture_output=True, text=True).stdout


def _head(path: Path) -> str:
    return _git(path, "rev-parse", "HEAD").strip()


class World:
    """The instance, its GitHub repository (a bare one here) and what the replaced steps were asked to do."""

    def __init__(self, root: Path) -> None:
        self.origin, self.work = root / "origin.git", root / "work"
        self.addons = store.addons_dir(NAME)
        self.paused = False
        self.done: list[Any] = []
        self.upgrade_fails = False
        self.start_failures = 0  # how many starts fail, from the first one
        self.starts = 0

    def push(self, text: str = "x") -> str:
        """A developer pushes a change to the module."""
        manifest = self.work / "shop_mod" / "__manifest__.py"
        manifest.write_text(f"{{'name': 'Shop', 'summary': {text!r}}}\n")
        _git(self.work, "commit", "-qam", f"change {text}")
        _git(self.work, "push", "-q", "origin", "main")
        return _head(self.work)

    def kinds(self) -> list[str]:
        return [what for what, *_ in self.done]


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    found = World(tmp_path)
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(found.origin))
    subprocess.run(["git", "clone", "-q", str(found.origin), str(found.work)], check=True, capture_output=True)
    (found.work / "shop_mod").mkdir()
    (found.work / "shop_mod" / "__manifest__.py").write_text("{'name': 'Shop'}\n")
    _git(found.work, "checkout", "-q", "-b", "main")
    _git(found.work, "add", ".")
    _git(found.work, "commit", "-qm", "first")
    _git(found.work, "push", "-q", "-u", "origin", "main")
    store.folder(NAME).mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(found.origin), str(found.addons)], check=True, capture_output=True)
    store.write_env(
        NAME,
        {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": "upd", "GIT_BRANCH": "main",
         "AUTO_UPDATE": "true", "AUTO_UPGRADE": "true", "AUTO_PAUSE": "false", "DELETE_AFTER_DAYS": "0",
         "CREATED_AT": int(time.time()) - 3600},
    )  # fmt: skip

    def run(self: jobs.Job, cmd: list[str], timeout: float | None = None, input: str | None = None) -> None:
        if cmd[0] != "git":  # docker compose: recorded, never run
            after = next((i + 1 for i, arg in enumerate(cmd) if arg.endswith("docker-compose.yml")), 0)
            found.done.append(("docker", " ".join(cmd[after:])))
            return
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.write(result.stdout + result.stderr)
        if result.returncode:
            raise jobs.JobError(f"command failed: {' '.join(cmd[:4])} ...")

    def create(job: jobs.Job, name: str, kind: str = "manual") -> list[Path]:
        found.done.append(("backup", kind, _head(found.addons)))
        return [settings.backups_dir / f"{name}__upd__20261117-000000__{kind}.zip"]

    def upgrade(job: jobs.Job, name: str, modules: list[str]) -> None:
        found.done.append(("upgrade", modules))
        if found.upgrade_fails:
            job.write("odoo.tools.convert.ParseError: while parsing views.xml\n")
            raise jobs.JobError("command failed: docker compose run ...")

    def start(job: jobs.Job, name: str, restart: bool = False, strict: bool = False) -> None:
        found.starts += 1
        found.done.append(("start", _head(found.addons), restart, strict))
        if found.starts <= found.start_failures:
            raise jobs.JobError("Odoo did not answer after it was started")

    monkeypatch.setattr(jobs.Job, "run", run)
    monkeypatch.setattr(backups, "create", create)
    monkeypatch.setattr(backups, "restore", lambda job, name, b, db=None: found.done.append(("restore", b["file"])))
    monkeypatch.setattr(operations, "_upgrade", upgrade)
    monkeypatch.setattr(operations, "_start_odoo", start)
    monkeypatch.setattr(operations, "refresh_addons_path", lambda job, name: None)
    monkeypatch.setattr(operations, "_require_branch", lambda job, name, addons: None)
    monkeypatch.setattr(operations, "_paused", lambda name: found.paused)
    yield found
    shutil.rmtree(store.folder(NAME), ignore_errors=True)
    shutil.rmtree(settings.backups_dir, ignore_errors=True)
    STATE.remove(NAME)
    live.change(dict.fromkeys(live.overrides()))


def _update(user: str = "admin1", submodules_only: bool = False) -> jobs.Job:
    job = jobs.Job(NAME, "Get latest code", "update", user)
    operations.update_code(job, NAME, submodules_only)
    return job


def _failing_update(user: str = "admin1") -> tuple[jobs.Job, str]:
    job = jobs.Job(NAME, "Get latest code", "update", user)
    with pytest.raises(jobs.JobError) as failure:
        operations.update_code(job, NAME)
    assert friendly_hint("".join(job.log)) == str(failure.value)  # what the job shows as its error
    return job, str(failure.value)


# ---------------------------------------------------------------- the backup


def test_a_backup_is_taken_before_an_update_that_changes_the_code(world: World) -> None:
    before = _head(world.addons)
    new = world.push()
    job = _update()
    # the backup comes before the pull: the code is still the previous one when it is taken
    assert world.done == [
        ("backup", "before-update", before),
        ("upgrade", ["shop_mod"]),
        ("start", new, False, True),
    ]
    assert _head(world.addons) == new and job.notes[0]["text"].startswith("Backup taken before the update")
    assert STATE.get(NAME).get("update_failed") is None


def test_no_update_when_the_backup_fails(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(job: jobs.Job, name: str, kind: str = "manual") -> list[Path]:
        raise jobs.JobError("database dump failed")

    monkeypatch.setattr(backups, "create", broken)
    before = _head(world.addons)
    world.push()
    job = jobs.Job(NAME, "Get latest code", "update", "admin1")
    with pytest.raises(jobs.JobError, match="database dump failed"):
        operations.update_code(job, NAME)
    assert _head(world.addons) == before and world.done == []
    assert friendly_hint("".join(job.log)) == operations.UPDATE_BACKUP_FAILED


def test_no_backup_when_already_up_to_date(world: World) -> None:
    job = _update()
    assert world.done == [] and "Already up to date" in job.notes[0]["text"]


def test_the_backup_kind_is_listed_and_kept_once_per_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "before-update" in backups.KINDS and backups.FILE_RE.match("upd__upd__20261117-000000__before-update.zip")
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    store.folder(NAME).mkdir(parents=True, exist_ok=True)
    store.write_env(NAME, {"ODOO_VERSION": "18.0", "EDITION": "community"})
    stamps = iter(["20261117-000001", "20261117-000002", "20261117-000003"])

    def write(job: jobs.Job, name: str, db: str, facts: Any, stamp: str, kind: str) -> Path:
        path = settings.backups_dir / f"{name}__{db}__{next(stamps)}__{kind}.zip"
        path.write_bytes(b"zip")
        return path

    monkeypatch.setattr(backups, "_database_up", lambda job, name: None)
    monkeypatch.setattr(backups, "_backup_database", write)
    monkeypatch.setattr(operations.odoo, "databases", lambda name: ["upd"])
    job = jobs.Job(NAME, "Get latest code", "update", "admin1")
    (first,) = backups.create(job, NAME, "before-update")
    (second,) = backups.create(job, NAME, "before-update")
    assert not first.exists() and second.exists()  # only the latest is kept
    STATE.update(NAME, keep_backups=[second.name])  # an automatic rollback failed: kept for a restore by hand
    (third,) = backups.create(job, NAME, "before-update")
    assert second.exists() and third.exists()
    STATE.remove(NAME)
    shutil.rmtree(store.folder(NAME), ignore_errors=True)
    shutil.rmtree(settings.backups_dir, ignore_errors=True)


# ---------------------------------------------------------------- failures put the instance back


def test_a_pull_that_fails_puts_the_code_back(world: World) -> None:
    world.push("on github")
    (world.addons / "shop_mod" / "local.py").write_text("x = 1\n")  # changed on the server: the branches diverge
    _git(world.addons, "add", ".")
    _git(world.addons, "commit", "-qm", "on the server")
    before = _head(world.addons)
    job, message = _failing_update()
    assert message.startswith(
        f"The update to {_git(world.origin, 'rev-parse', 'main').strip()[:7]} failed at downloading the code; "
        f"the instance was put back to {before[:7]}."
    )
    assert "went in different directions" in message  # the explanation of the failure (core/hints.py)
    assert _head(world.addons) == before
    assert "restore" not in world.kinds()  # the database was not touched
    assert world.done[-1] == ("start", before, True, True)  # Odoo started again on the previous code and checked
    assert job.steps[-1]["title"] == f"Putting the instance back to {before[:7]}"
    failed = STATE.get(NAME)["update_failed"]
    assert failed["rolled_back"] and failed["message"] == message
    assert audit.latest(instance=NAME)[0]["event"] == "update failed, instance put back"


def test_a_failed_upgrade_puts_back_the_code_and_the_database(world: World) -> None:
    before = _head(world.addons)
    new = world.push()
    world.upgrade_fails = True
    _, message = _failing_update()
    assert message.startswith(f"The update to {new[:7]} failed at upgrading modules; the instance was put back to "
                              f"{before[:7]}.")  # fmt: skip
    assert "error in its XML files" in message
    assert _head(world.addons) == before
    assert world.kinds() == ["backup", "upgrade", "docker", "restore", "start"]
    assert world.done[3] == ("restore", "upd__upd__20261117-000000__before-update.zip")
    assert world.done[-1] == ("start", before, True, True)


def test_odoo_not_answering_after_the_update_puts_everything_back(world: World) -> None:
    before = _head(world.addons)
    world.push()
    world.start_failures = 1  # the new version does not answer; the previous one does
    _, message = _failing_update()
    assert f"failed at starting Odoo; the instance was put back to {before[:7]}." in message
    assert world.kinds() == ["backup", "upgrade", "start", "docker", "restore", "start"]
    assert _head(world.addons) == before


def test_without_an_upgrade_the_database_is_not_restored(world: World) -> None:
    store.update_env(NAME, AUTO_UPGRADE="false")
    before = _head(world.addons)
    world.push()
    world.start_failures = 1
    _, message = _failing_update()
    assert "failed at starting Odoo" in message and "restore" not in world.kinds()
    assert _head(world.addons) == before


def test_when_putting_back_fails_the_backup_is_kept_and_named(world: World) -> None:
    world.push()
    world.upgrade_fails, world.start_failures = True, 99
    _, message = _failing_update()
    assert "failed at upgrading modules, and putting the instance back to" in message and "failed too" in message
    assert "kept for a restore by hand: upd__upd__20261117-000000__before-update.zip" in message
    state = STATE.get(NAME)
    assert not state["update_failed"]["rolled_back"]
    assert state["keep_backups"] == ["upd__upd__20261117-000000__before-update.zip"]
    assert audit.latest(instance=NAME)[0]["event"] == "update failed, rollback failed"


def test_a_failed_commit_is_not_tried_again_automatically(world: World) -> None:
    from sudoo.web.v1.resources import update_failure

    new = world.push()
    world.upgrade_fails = True
    _failing_update(user="system")
    shown = update_failure(STATE.get(NAME).get("update_failed"))
    assert shown is not None and shown["rolled_back"] and shown["job"].startswith("/api/v1/jobs/")
    assert not Polling.has_new_commits(NAME)  # polling does not ask for it again
    world.done.clear()
    job = _update(user="system")  # a webhook delivered again: skipped
    assert world.done == [] and "not tried again automatically" in job.notes[0]["text"]

    world.upgrade_fails = False
    _update()  # Get latest code tries it again
    assert _head(world.addons) == new and STATE.get(NAME).get("update_failed") is None
    assert update_failure(STATE.get(NAME).get("update_failed")) is None


# ---------------------------------------------------------------- paused instances and the setting


def test_a_paused_instance_is_backed_up_only_before_an_upgrade(world: World) -> None:
    world.paused = True
    store.update_env(NAME, AUTO_UPGRADE="false")
    world.push("one")
    _update()
    assert world.done == []  # code updated, applied on the next start, as before

    store.update_env(NAME, AUTO_UPGRADE="true")
    new = world.push("two")
    _update()
    # the backup comes after the pull (only then is it known that an upgrade runs) and before the upgrade
    assert world.done == [("backup", "before-update", new), ("upgrade", ["shop_mod"]), ("docker", "stop")]


def test_a_paused_instance_stays_paused_when_put_back(world: World) -> None:
    world.paused = True
    before = _head(world.addons)
    world.push()
    world.upgrade_fails = True
    _, message = _failing_update()
    assert f"put back to {before[:7]}" in message and _head(world.addons) == before
    assert world.kinds() == ["backup", "upgrade", "docker", "restore", "docker"]
    assert world.done[-1] == ("docker", "stop") and "start" not in world.kinds()


def test_with_safe_updates_off_updates_work_as_before(world: World) -> None:
    live.change({"safe_updates": False})
    new = world.push()
    world.upgrade_fails = True
    job = jobs.Job(NAME, "Get latest code", "update", "admin1")
    with pytest.raises(jobs.JobError, match="command failed"):
        operations.update_code(job, NAME)
    assert world.kinds() == ["upgrade"]  # no backup, nothing put back
    assert _head(world.addons) == new and STATE.get(NAME).get("update_failed") is None
