"""Database upgrades of an instance to a newer Odoo version (upgrades/).

    GET    /api/v1/instances/<name>/upgrade            what can be done and how it went (upgrades.resource)
    POST   /api/v1/instances/<name>/upgrade/check      {"target"}: the pre-check report, kept with the upgrade
    POST   /api/v1/instances/<name>/upgrade/test       {"target", "subscription_code", "accept_odoo_terms"} -> 202
                                                       job: a new staging instance on the target version (or the
                                                       test run again on the one there is)
    POST   /api/v1/instances/<name>/upgrade/promote    {"confirm_production", "subscription_code",
                                                       "accept_odoo_terms"} -> 202 job: the instance itself upgraded
    POST   /api/v1/instances/<name>/upgrade/cancel     {"confirm_production"} -> 202 job: back to the backup taken
                                                       before the upgrade (within UPGRADE_ROLLBACK_MINUTES)
    POST   /api/v1/instances/<name>/upgrade/keep       end the rollback window now
    DELETE /api/v1/instances/<name>/upgrade            forget the upgrade (its test instance stays) -> 204

Everything but reading needs instances.upgrade (administrators and super admins); upgrading or cancelling on a
production instance also needs "confirm_production": true. Odoo's upgrade service (an Enterprise database) needs the
customer's Enterprise subscription code and "accept_odoo_terms": true (Odoo S.A.'s service, under the customer's
subscription and Odoo's terms, upgrades.TERMS_URL); the code is passed to the job only, never stored or logged.
"""

from __future__ import annotations

import re
from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo import projects, upgrades
from sudoo.config import settings
from sudoo.core import jobs
from sudoo.instances import planning, stages, store
from sudoo.limits import quotas
from sudoo.repos.gitrepo import origin_url
from sudoo.upgrades import Secret, precheck
from sudoo.upgrades import run as upgrade_run

from .. import validation as check
from ..security import log, refuse_unless, require
from . import instances as instances_api
from .responses import iso, no_content, ok

blueprint = Blueprint("upgrades", __name__)

CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{3,99}$")
BUSY_STATES = ("testing", "promoting")


def _resource(name: str) -> dict[str, Any]:
    """upgrades.resource with its times as ISO 8601 (the API's convention)."""
    found = upgrades.resource(name)
    for key in ("checked_at", "tested_at"):
        found[key] = iso(found.get(key))
    if found.get("report"):
        found["report"] = {**found["report"], "checked_at": iso(found["report"].get("checked_at"))}
    if found.get("promotion"):
        found["promotion"] = {
            key: iso(value) if key in ("at", "finished_at", "rollback_until", "rolled_back_at") else value
            for key, value in found["promotion"].items()
        }
    found["history"] = [{**entry, "at": iso(entry.get("at"))} for entry in found["history"]]
    return found


def _target(name: str, data: dict[str, Any]) -> str:
    env = store.read_env(name)
    offered = upgrades.targets(env)
    target = check.text(data, "target", max_length=10) or (offered[0] if offered else "")
    if target not in offered:
        abort(400, f"Choose a version {name} can be upgraded to: {', '.join(offered) or 'none'}.")
    problem = upgrades.method_problem(env, target)
    if problem:
        abort(409, problem)
    return target


def _code(name: str, data: dict[str, Any]) -> Secret | None:
    """The subscription code for Odoo's upgrade service (Enterprise), after the terms were accepted; None for
    OpenUpgrade. Never logged."""
    if upgrades.method_for(store.read_env(name)) != "enterprise":
        return None
    if check.flag(data, "accept_odoo_terms") is not True:
        abort(
            400,
            "This uses Odoo S.A.'s upgrade service under your Odoo Enterprise subscription and Odoo's terms: accept "
            'them first ("accept_odoo_terms": true).',
        )
    value = data.get("subscription_code")
    if not isinstance(value, str) or not CODE_RE.fullmatch(value.strip()):
        abort(400, "Enter the Odoo Enterprise subscription code of this database (letters, digits and dashes).")
    return Secret(value.strip())


def _confirm_production(name: str, data: dict[str, Any], what: str) -> None:
    if stages.is_production(store.read_env(name)) and check.flag(data, "confirm_production") is not True:
        abort(400, f'{name} is a production instance: confirm that you want to {what} ("confirm_production": true).')


@blueprint.get("/instances/<name>/upgrade")
@require("instances.view")
def read(name: str) -> Response:
    return ok(_resource(instances_api.existing(name)))


@blueprint.post("/instances/<name>/upgrade/check")
@require("instances.upgrade")
def run_check(name: str) -> Response:
    instances_api.existing(name)
    refused = upgrades.refusal(name)
    if refused:
        abort(409, refused)
    target = _target(name, check.body())
    report = precheck.report(name, target)
    upgrades.remember(
        name, g.user["name"], "pre-check", f"to Odoo {target}: {len(report['modules'])} repository modules, "
        f"{sum(m['status'] != 'ready' for m in report['modules'])} not ready",  # fmt: skip
        report=report, target=target, checked_at=report["checked_at"],
        branch=report["branch"] if report.get("branch_exists") else None,
        state=(upgrades.get(name) or {}).get("state") or "checked",
    )  # fmt: skip
    return ok(_resource(name))


@blueprint.post("/instances/<name>/upgrade/test")
@require("instances.upgrade")
def test(name: str) -> Response:
    instances_api.existing(name)
    data = check.body()
    refused = upgrades.refusal(name)
    if refused:
        abort(409, refused)
    target = _target(name, data)
    doc = upgrades.get(name) or {}
    report = doc.get("report") or {}
    if report.get("target") != target:
        abort(409, f"Run the pre-check for Odoo {target} first, and read its report.")
    if report.get("blocking"):
        abort(409, report["blocking"][0]["text"])
    if doc.get("state") in BUSY_STATES:
        abort(409, "A test or an upgrade of this instance is running already.")
    instances_api.refuse_unlinked("testing upgrades", f"upgrade test of {name} refused (panel not linked)")
    code = _code(name, data)
    existing = upgrades.staging_of(name)
    if existing and store.read_env(existing).get("ODOO_VERSION") == target:
        # the test instance is overwritten: the caller needs to be able to restore it (its branch's access and lock)
        refuse_unless("instances.restore", f"run the upgrade test of {name} again on {existing}", instance=existing)
        if jobs.busy(existing):
            abort(409, f"{existing} is busy; try again when its job has finished.")
        job = jobs.start(existing, f"Run the Odoo {target} upgrade test again", "upgrade-test", upgrade_run.rerun,
                         existing, name, code, user=g.user["name"])  # fmt: skip
        log("database upgrade test run again", instance=name, detail=f"to Odoo {target} on {existing}")
        return instances_api._accepted(job)
    with quotas.LOCK:
        return _create_test(name, target, doc, code)


def _create_test(name: str, target: str, doc: dict[str, Any], code: Secret | None) -> Response:
    env = store.read_env(name)
    branch = doc.get("branch")
    repo = origin_url(store.addons_dir(name)) if branch and not store.plain(env) else ""
    project = projects.get(env.get("PROJECT", "")) if repo else None
    if project is not None:  # an instance of the upgrade branch: the caller's access in that branch
        instances_api._refuse_in_branch("instances.create", project, str(branch), f"test the upgrade of {name}")
    with quotas.LOCK:  # from the limits until the test instance's job is registered: two tests cannot both pass
        instances_api._refuse_over_capacity(
            "upgrade test refused (running instances)", stages.STAGING, g.user["name"], queued=False
        )  # the answer is needed now: an upgrade test does not wait in the capacity queue
        if settings.max_instances and len(store.names()) + len(jobs.being_created()) >= settings.max_instances:
            abort(409, f"The server has its maximum of {settings.max_instances} instances; delete one first.")
        try:
            quotas.claim(g.user["name"])
        except quotas.QuotaError as error:
            g.retry_after = error.retry_after
            abort(error.status, error.message)
        lifetime = projects.lifetime_days(project, stages.STAGING) if project else settings.staging_delete_after_days
        plan = planning.build(
            repo=repo,
            branch=str(branch or ""),
            version=target,
            edition=env.get("EDITION", "community"),
            stage=stages.STAGING,
            user=g.user["name"],
            switches={
                "auto_update": bool(repo),
                "auto_upgrade": True,
                "auto_pause": True,
                "delete_after_days": lifetime,
            },
            custom_name=f"{name[:20]}-odoo{upgrades.major(target)}",
            project=project["id"] if project else None,
        )
        plan["upgrade"] = {"source": name, "target": target, "method": upgrades.method_for(env), "code": code}
        job = planning.start(plan, g.user["name"], upgrade_run.create_test)
        upgrades.remember(name, g.user["name"], "test started", f"to Odoo {target} on {plan['name']}", state="testing",
                          test_job=job.id, target=target)  # fmt: skip
        log("database upgrade test started", instance=name, detail=f"to Odoo {target} on {plan['name']}")
    return instances_api._accepted(job)


@blueprint.post("/instances/<name>/upgrade/promote")
@require("instances.upgrade")
def promote(name: str) -> Response:
    instances_api.existing(name)
    data = check.body()
    doc = upgrades.get(name) or {}
    if doc.get("state") not in ("tested", "promotion_failed", "rolled_back") or not doc.get("tested_at"):
        abort(409, "Test the upgrade first: production is upgraded only after a test that succeeded.")
    target = str(doc.get("target") or "")
    if upgrades.method_problem(store.read_env(name), target):
        abort(409, upgrades.method_problem(store.read_env(name), target))
    env = store.read_env(name)
    if not store.plain(env) and not doc.get("branch"):
        abort(
            409,
            f"{name} follows a repository, which has no branch {upgrades.upgrade_branch(target)}: push the code "
            "ported to the new version to that branch, run the pre-check and the test again, then upgrade.",
        )
    _confirm_production(name, data, f"upgrade it to Odoo {target}")
    instances_api.refuse_unlinked("upgrading instances", f"upgrade of {name} refused (panel not linked)")
    _refuse_production_branch(name, str(doc.get("branch") or ""))
    code = _code(name, data)
    if jobs.busy(name):
        abort(409, f"{name} is busy; try again when its job has finished.")
    job = jobs.start(name, f"Upgrade to Odoo {target}", "db-upgrade", upgrade_run.promote, name, code,
                     user=g.user["name"])  # fmt: skip
    log("database upgrade of production started", instance=name, detail=f"to Odoo {target}")
    return instances_api._accepted(job)


def _refuse_production_branch(name: str, branch: str) -> None:
    """The upgrade branch becomes the project's production branch in place of the instance's branch (run.py,
    _record_branches): 409 beyond the project's and the plan's production-branch limits, as for a stage change. The
    earlier branch is not counted unless another production instance stays on it."""
    env = store.read_env(name)
    project = projects.get(env.get("PROJECT", ""))
    before = env.get("GIT_BRANCH", "")
    if project is None or not branch or branch == before:
        return
    keep_before = projects.stays_production(project["id"], before, without=name)
    with quotas.LOCK:
        refused = projects.production_refusal(project, branch, replacing=None if keep_before else before)
    if refused:
        log("database upgrade of production refused (production branches)", instance=name, ok=False, detail=refused)
        abort(409, refused)


@blueprint.post("/instances/<name>/upgrade/cancel")
@require("instances.upgrade")
def cancel(name: str) -> Response:
    instances_api.existing(name)
    data = check.body()
    if not upgrades.rollback_open(upgrades.get(name)):
        abort(409, "There is no upgrade of this instance that can still be cancelled.")
    _confirm_production(name, data, "cancel its upgrade (its data goes back to before the upgrade)")
    if jobs.busy(name):
        abort(409, f"{name} is busy; try again when its job has finished.")
    job = jobs.start(name, "Cancel the Odoo upgrade", "db-upgrade", upgrade_run.cancel, name, "",
                     user=g.user["name"])  # fmt: skip
    log("database upgrade cancelled", instance=name)
    return instances_api._accepted(job)


@blueprint.post("/instances/<name>/upgrade/keep")
@require("instances.upgrade")
def keep(name: str) -> Response:
    instances_api.existing(name)
    if not upgrades.rollback_open(upgrades.get(name)):
        abort(409, "There is no upgrade of this instance that can still be cancelled.")
    upgrade_run.close_window(name)
    upgrades.remember(name, g.user["name"], "upgrade kept", "the rollback window was closed")
    return ok(_resource(name))


@blueprint.delete("/instances/<name>/upgrade")
@require("instances.upgrade")
def forget(name: str) -> Response:
    instances_api.existing(name)
    doc = upgrades.get(name) or {}
    if doc.get("state") in BUSY_STATES or upgrades.rollback_open(doc):
        abort(409, "This upgrade is running or can still be cancelled; it cannot be forgotten now.")
    upgrades.forget(name)
    log("database upgrade forgotten", instance=name)
    return no_content()
