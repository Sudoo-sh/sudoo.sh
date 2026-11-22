"""The jobs of a database upgrade (see upgrades/__init__.py): the test on a new staging instance, its re-runs, the
promotion of production and its rollback, and the watch over a promoted instance."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from sudoo import addons as addons_sources
from sudoo import projects
from sudoo.config import settings
from sudoo.core import audit, jobs
from sudoo.core.jobs import Job, JobError
from sudoo.core.shell import capture
from sudoo.core.state import STATE
from sudoo.instances import backups, integrity, odoo, operations, stages, store
from sudoo.instances.store import compose
from sudoo.repos.gitrepo import git
from sudoo.upgrades import Secret, get, method_for, methods, remember, rollback_open, test_identity, update

PRODUCTION_WAIT_SECONDS = 1800
WATCH_SECONDS = 30
WATCH_FAILURES = 4  # checks in a row without an answer from Odoo before a promoted upgrade is rolled back


# ---------------------------------------------------------------- the copy


def fresh_copy(job: Job, source: str) -> dict[str, Any]:
    """A new backup of `source` (kind staging-copy), taken while no other job runs on it; its listing entry."""
    job.step(f"Backing up {source}")
    lock = jobs.INSTANCE_LOCKS[source]
    if not lock.acquire(timeout=PRODUCTION_WAIT_SECONDS):
        raise JobError(f"{source} has been busy for half an hour; try again when its job has finished.")
    try:
        if not store.exists(source):
            raise JobError(f"{source} no longer exists.")
        saved = backups.create(job, source, "staging-copy")
    finally:
        lock.release()
    database = store.read_env(source)["DB_NAME"].split(",")[0]
    chosen = next((path for path in saved if f"__{database}__" in path.name), saved[0] if saved else None)
    found = next((b for b in backups.all_backups() if chosen and b["file"] == chosen.name), None)
    if found is None:
        raise JobError(f"The backup of {source} could not be read back.")
    taken = datetime.fromtimestamp(found["created"], settings.zone).strftime("%Y-%m-%d %H:%M")
    job.note(f"Backed up {source} at {taken} ({found['file']}).")
    return found


def migrate_copy(job: Job, name: str, db: str, upgrade: dict[str, Any]) -> None:
    """Database `db` of the test instance `name` holds the copy (of the earlier version): upgrade it to the
    instance's version with the method of the upgrade."""
    target, method = upgrade["target"], upgrade["method"]
    code = upgrade.get("code")
    job.step(f"Upgrading the copy to Odoo {target} ({_method_name(method)})")
    if method == "enterprise":
        with methods.work_folder() as work:
            dump = methods.odoo_service(job, name, db, target, "test", code or Secret(""), work)
            methods.load_dump(job, name, db, dump)
        methods.own_modules_upgrade(job, name, db)
    else:
        methods.openupgrade(job, name, db, target)
    methods.verify(job, name, db, target)


def _method_name(method: str) -> str:
    return "Odoo's upgrade service" if method == "enterprise" else "OpenUpgrade"


# ---------------------------------------------------------------- the test


def create_test(job: Job, plan: dict[str, Any]) -> None:
    """The job creating the test instance of an upgrade (operations.create with plan["upgrade"]); the upgrade's
    state follows how it ends."""
    source = plan["upgrade"]["source"]
    try:
        operations.create(job, plan)
    except Exception as error:
        remember(source, job.user, "test failed", str(error)[:300], state="test_failed", test_job=job.id)
        raise
    finally:
        plan["upgrade"]["code"] = None  # the subscription code is kept no longer than the job
    remember(source, job.user, "tested", f"on {plan['name']}", state="tested", staging=plan["name"],
             **test_identity(plan["name"]), tested_at=round(time.time()), test_job=job.id)  # fmt: skip
    job.note(
        f"{plan['name']} runs a neutralised copy of {source} upgraded to Odoo {plan['version']}. Test it; "
        f"then upgrade production from {source}'s Upgrade tab."
    )


def rerun(job: Job, name: str, source: str, code: Secret | None) -> None:
    """Run the upgrade test again on the existing test instance `name`: a fresh backup of `source`, the branch's
    latest code, the upgrade, neutralised, started."""
    doc = get(source) or {}
    target, method = str(doc.get("target")), method_for(store.read_env(source))
    env = store.read_env(name)
    database = env["DB_NAME"].split(",")[0]
    job.expect(8)
    remember(source, job.user, "test started", f"again on {name}", state="testing", test_job=job.id)
    try:
        copy = fresh_copy(job, source)
        if not store.plain(env):
            job.step(f"Downloading the latest code of {env['GIT_BRANCH']}")
            operations._require_branch(job, name, store.addons_dir(name))
            operations._pull(job, store.addons_dir(name))
        job.step("Stopping Odoo")
        job.run(compose(name, "stop", "odoo"), timeout=300)
        job.step(f"Loading the copy into database {database}")
        backups.restore(job, name, copy, database)
        operations.refresh_addons_path(job, name)
        migrate_copy(job, name, database, {"target": target, "method": method, "code": code})
        job.step("Neutralising the copied data")
        job.note(backups.neutralize(job, name, database))
        job.step("Setting the admin password")
        operations._adopt_admin(job, name, database)
        operations._finish_restore(job, name)
    except Exception as error:
        remember(source, job.user, "test failed", str(error)[:300], state="test_failed")
        raise
    remember(source, job.user, "tested", f"again on {name}", state="tested", tested_at=round(time.time()))


# ---------------------------------------------------------------- production


def _switch_code(job: Job, name: str, branch: str) -> None:
    addons = store.addons_dir(name)
    job.run(git(addons, "fetch", "origin", branch), timeout=900)
    job.run(git(addons, "checkout", "-B", branch, f"origin/{branch}"))
    job.run(git(addons, "branch", f"--set-upstream-to=origin/{branch}", branch))
    job.run(git(addons, "submodule", "update", "--init", "--recursive"), timeout=1800)
    store.update_env(name, GIT_BRANCH=branch)


def _switch_version(job: Job, name: str, target: str) -> None:
    env = store.read_env(name)
    folder = ""
    if env.get("EDITION") == "enterprise":
        folder = str(addons_sources.prepare(target, job.write))
    image = integrity.pinned_reference(job, f"{settings.odoo_image}:{target}")
    store.update_env(name, ODOO_VERSION=target)
    integrity.retarget(job, name, image, folder)


def promote(job: Job, name: str, code: Secret | None) -> None:
    """Upgrade instance `name` itself to the version its test ran on: maintenance, a final backup, the method on the
    latest data, the new version and branch, Odoo started. When anything fails, the instance is put back."""
    doc = get(name) or {}
    target, branch = str(doc.get("target")), doc.get("branch")
    env = store.read_env(name)
    method = method_for(env)
    database = env["DB_NAME"].split(",")[0]
    previous = {
        "ODOO_VERSION": env["ODOO_VERSION"],
        "GIT_BRANCH": env.get("GIT_BRANCH", ""),
        "ODOO_IMAGE_REF": (integrity.record(name) or {}).get("inputs", {}).get("ODOO_IMAGE_REF")
        or env.get("ODOO_IMAGE_REF")
        or f"{settings.odoo_image}:{env['ODOO_VERSION']}",
        "ENTERPRISE_FOLDER": (integrity.record(name) or {}).get("inputs", {}).get("ENTERPRISE_FOLDER")
        or env.get("ENTERPRISE_FOLDER", ""),
        "HEAD": capture(git(store.addons_dir(name), "rev-parse", "HEAD")),
    }
    job.expect(7)
    remember(name, job.user, "production upgrade started", f"to Odoo {target}", state="promoting",
             promotion={"job": job.id, "at": round(time.time()), "by": job.user, "target": target,
                        "previous": previous})  # fmt: skip
    job.step("Putting Odoo into maintenance")
    job.run(compose(name, "stop", "odoo"), timeout=300)
    job.write("Visitors see the Upgrading page until the upgrade ends.\n")
    job.step("Backing up the database and files")
    saved = [path.name for path in backups.create(job, name, "before-upgrade")]
    job.note(f"Backup before the upgrade: {', '.join(saved)}")
    _set_promotion(name, backup=saved)
    try:
        if method == "enterprise":
            with methods.work_folder() as work:
                job.step(f"Upgrading the database to Odoo {target} with Odoo's upgrade service")
                dump = methods.odoo_service(job, name, database, target, "production", code or Secret(""), work)
                job.step(f"Switching to Odoo {target}" + (f" and branch {branch}" if branch else ""))
                if branch:
                    _switch_code(job, name, str(branch))
                _switch_version(job, name, target)
                methods.load_dump(job, name, database, dump)
            operations.refresh_addons_path(job, name)
            methods.own_modules_upgrade(job, name, database)
        else:
            job.step(f"Switching to Odoo {target}" + (f" and branch {branch}" if branch else ""))
            if branch:
                _switch_code(job, name, str(branch))
            _switch_version(job, name, target)
            operations.refresh_addons_path(job, name)
            job.step(f"Upgrading the database to Odoo {target} with OpenUpgrade")
            methods.openupgrade(job, name, database, target)
        methods.verify(job, name, database, target)
        job.step("Starting Odoo")
        operations._start_odoo(job, name, strict=True)
    except Exception as error:
        job.note(f"The upgrade failed ({error}); putting {name} back as it was before it.", "warning")
        try:
            _put_back(job, name, previous, saved)
        except Exception as again:
            remember(name, job.user, "production upgrade failed, putting back failed", str(again)[:300],
                     state="promotion_failed")  # fmt: skip
            raise JobError(
                f"The upgrade failed and putting {name} back failed too ({again}). Restore the backup "
                f"{', '.join(saved)} from the Backups card."
            ) from error
        remember(name, job.user, "production upgrade failed, put back", str(error)[:300], state="promotion_failed")
        raise JobError(f"The upgrade to Odoo {target} failed, so {name} was put back on its backup: {error}") from error
    until = round(time.time() + settings.upgrade_rollback_minutes * 60) if settings.upgrade_rollback_minutes else None
    _set_promotion(name, rollback_until=until, finished_at=round(time.time()))
    remember(name, job.user, "production upgraded", f"to Odoo {target}", state="promoted")
    _record_branches(name, previous["GIT_BRANCH"], str(branch or ""), job.user)
    STATE.update(name, update_failed=None)
    if until:
        job.note(
            f"{name} runs Odoo {target}. For {settings.upgrade_rollback_minutes} minutes you can cancel the upgrade "
            "(back to the backup taken before it; what was changed meanwhile is lost), and it is put back by itself "
            "if Odoo stops answering."
        )


def _set_promotion(name: str, **values: Any) -> None:
    def apply(doc: dict[str, Any]) -> dict[str, Any]:
        return {**doc, "promotion": {**(doc.get("promotion") or {}), **values}}

    from sudoo.upgrades import change

    change(name, apply)


def _record_branches(name: str, before: str, after: str, user: str) -> None:
    """The project follows: the upgrade branch becomes the production branch, the earlier one leaves production."""
    project_id = store.read_env(name).get("PROJECT", "")
    if not project_id or not after or after == before or projects.get(project_id) is None:
        return
    recorded = ((projects.get(project_id) or {}).get("branches") or {}).get(after, {}).get("stage")
    after_before = str(recorded or stages.STAGING)  # an upgrade branch's test instance is a staging instance
    projects.record_branch(project_id, after, stage=stages.PRODUCTION, changed_by=user)

    def drop(project: dict[str, Any]) -> dict[str, Any]:
        branches = dict(project.get("branches") or {})
        if before in branches:
            branches[before] = {k: v for k, v in branches[before].items() if k != "stage"}
        project["branches"] = branches
        return project

    projects.change(project_id, drop)
    from sudoo.projects import builds  # late: projects/ imports the instances package

    builds.stage_changed(project_id, after, after_before, stages.PRODUCTION, user, reason="database upgrade")
    left = projects.branch_stage(projects.get(project_id) or {}, before)
    builds.stage_changed(project_id, before, stages.PRODUCTION, left, user, reason="database upgrade")


def _put_back(job: Job, name: str, previous: dict[str, str], saved: list[str]) -> None:
    """The instance as it was before its upgrade: its image, Enterprise add-ons, branch and the backup."""
    job.step("Putting the instance back")
    job.run_unchecked(compose(name, "stop", "odoo"), timeout=300)
    branch, head = previous.get("GIT_BRANCH"), previous.get("HEAD")
    if branch and head:  # the very commit it ran before, on its branch
        addons = store.addons_dir(name)
        job.run(git(addons, "checkout", "-B", branch, head))
        job.run_unchecked(git(addons, "branch", f"--set-upstream-to=origin/{branch}", branch))
        job.run_unchecked(git(addons, "submodule", "update", "--init", "--recursive"), timeout=1800)
        store.update_env(name, GIT_BRANCH=branch)
    store.update_env(name, ODOO_VERSION=previous["ODOO_VERSION"])
    integrity.retarget(job, name, previous["ODOO_IMAGE_REF"], previous["ENTERPRISE_FOLDER"])
    listed = {b["file"]: b for b in backups.all_backups()}
    found = [listed[file] for file in saved if file in listed]
    if not found:
        raise JobError("the backup taken before the upgrade is gone")
    for backup in found:
        backups.restore(job, name, backup)
    operations.refresh_addons_path(job, name)
    operations._finish_restore(job, name)


def cancel(job: Job, name: str, reason: str = "") -> None:
    """Undo a promoted upgrade while it may still be undone: the current state is saved (before-rollback), then the
    instance is put back on the backup taken before the upgrade."""
    doc = get(name) or {}
    promotion = doc.get("promotion") or {}
    if doc.get("state") != "promoted":
        raise JobError("There is no upgrade of this instance to cancel.")
    job.expect(5)
    job.step("Saving the current state first")
    try:
        kept = backups.create(job, name, "before-rollback")
        job.note(f"The upgraded state is kept as {', '.join(p.name for p in kept)}")
    except JobError as error:
        job.note(f"The current state could not be saved first ({error}); putting back anyway.", "warning")
    _put_back(job, name, promotion.get("previous") or {}, list(promotion.get("backup") or []))
    previous = promotion.get("previous") or {}
    _record_branches(name, str(doc.get("branch") or ""), str(previous.get("GIT_BRANCH") or ""), job.user)
    _set_promotion(name, rolled_back_at=round(time.time()), rolled_back_by=job.user, reason=reason or None)
    remember(name, job.user, "production upgrade cancelled", reason or "by hand", state="rolled_back")
    job.note(f"{name} is back on Odoo {previous.get('ODOO_VERSION')} with the data of before the upgrade.")


# ---------------------------------------------------------------- the watch


_DOWN: dict[str, int] = {}


def watch_once(now: float | None = None) -> list[str]:
    """One look at every promoted upgrade still in its rollback window: Odoo that has not answered WATCH_FAILURES
    times in a row (and is not busy, paused or switched off) is put back. Returns the instances rolled back."""
    from sudoo.upgrades import all_upgrades

    started: list[str] = []
    for doc in all_upgrades():
        name = str(doc.get("source"))
        if not rollback_open(doc, now) or not store.exists(name) or jobs.busy(name):
            _DOWN.pop(name, None)
            continue
        state = STATE.get(name)
        if state.get("paused") or state.get("switched_off"):
            continue
        if odoo.responds(store.read_env(name)["ODOO_PORT"]):
            _DOWN.pop(name, None)
            continue
        _DOWN[name] = _DOWN.get(name, 0) + 1
        if _DOWN[name] >= WATCH_FAILURES:
            _DOWN.pop(name, None)
            reason = "Odoo stopped answering after the upgrade"
            audit.record("system", None, "database upgrade rolled back automatically", name, ok=False, detail=reason)
            jobs.start(name, "Cancel the Odoo upgrade (automatic)", "db-upgrade", cancel, name, reason)
            started.append(name)
    return started


def watch_forever() -> None:
    while True:
        time.sleep(WATCH_SECONDS)
        try:
            watch_once()
        except Exception as error:  # noqa: BLE001 - the watch goes on
            print(f"upgrades: watch failed: {error!r}", flush=True)


def close_window(name: str) -> None:
    """End the rollback window at once (someone keeps the upgrade)."""
    update(name, promotion={**((get(name) or {}).get("promotion") or {}), "rollback_until": int(time.time()) - 1})
