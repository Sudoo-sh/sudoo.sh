"""Tests of a build, and the modules a new development database gets (projects/build_options.py).

When a build creates or updates an instance of a development branch (or of a staging branch whose project runs
staging tests) and the branch's test_suite is not "off", Odoo runs the tests before the instance is served:

    odoo -d <db> --test-enable --stop-after-init --no-http -u <modules> [--test-tags=<tags>]

in a one-off container of the instance's own image (odoo.run_cmd), with Odoo stopped. Development tests run on the
instance's database itself; staging tests on a throw-away copy of it (a PostgreSQL template copy and a copy of its
files, both removed afterwards), so nothing the tests do reaches the data testers work with. "selection" tests the
repository's installed modules (--test-tags=/module,... from Odoo 16 on; older versions run the tests of the modules
being upgraded); "custom" passes the branch's test_tags. The run stops after PROJECT_TEST_TIMEOUT_MINUTES.

Odoo's log is then read (parse_log): the "N failed, N error(s) of M tests" summary (Odoo 16 and later), the
"Ran N tests" / "FAILED (failures=N, errors=N)" lines of older versions, ERROR lines of a test module
(odoo.addons.<module>.tests...), of odoo.modules.loading or unittest, and WARNING lines. The result goes into the job
(job.details["tests"]) and from there into the build (projects/builds.py): "failed" with "At least one test failed when
loading the modules (module_x)", "warning", or "success". A failed test never stops the instance from being served,
as on Odoo.sh: the build shows that it failed.
"""

from __future__ import annotations

import re
from typing import Any

from sudoo import exposure, projects
from sudoo.config import settings
from sudoo.core.jobs import Job, JobError
from sudoo.core.odoo_args import modules_of_tags
from sudoo.core.shell import capture
from sudoo.instances import backups, odoo, stages, store
from sudoo.instances.store import compose
from sudoo.projects import build_options
from sudoo.repos.gitrepo import own_modules

MESSAGES_KEPT = 10
TAGS_FROM = 16  # Odoo versions with --test-tags as the panel uses it
# Odoo's log lines: "2026-11-22 10:00:00,123 7 ERROR acme odoo.addons.sale_custom.tests.test_order: FAIL: ..."
LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ \d+ (INFO|WARNING|ERROR|CRITICAL) \S+ (\S+): ?(.*)$")
SUMMARY = re.compile(r"(\d+) failed, (\d+) error\(s\) of (\d+) tests?")
RAN = re.compile(r"\bRan (\d+) tests? in ")
OLD_FAILED = re.compile(r"FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?\)")
AT_LEAST_ONE = "At least one test failed when loading the modules"
TEST_LOGGERS = ("odoo.modules.loading", "odoo.modules.module", "odoo.tests", "unittest", "odoo.service.server")
# left out of "every module": localisations (they would fight over the company's country), test modules (Odoo's own
# test suites), hardware drivers (they need the IoT box)
ALL_EXCLUDED = ("l10n_", "test_", "hw_")


# ---------------------------------------------------------------- which options apply to an instance


def _context(name: str) -> tuple[dict[str, Any], str, str] | None:
    """(project, branch, stage) of instance `name`, or None outside every project or in production."""
    env = store.read_env(name)
    project = projects.get(env.get("PROJECT", ""))
    branch = env.get("GIT_BRANCH", "")
    stage = stages.of(env)
    if project is None or not branch or stage == stages.PRODUCTION:
        return None
    return project, branch, stage


def options_of(name: str) -> dict[str, Any] | None:
    """The build options of instance `name` (build_options.effective), or None when none apply."""
    found = _context(name)
    if found is None:
        return None
    project, branch, stage = found
    return build_options.effective(project, branch, stage) | {"stage": stage}


# ---------------------------------------------------------------- modules of a new database


def modules_to_install(job: Job, name: str, db: str) -> list[str]:
    """The modules a new database of instance `name` gets by its branch's module_install: the repository's (the
    default, and for every instance outside a development branch of a project), every module Odoo has, or a list."""
    addons = store.addons_dir(name)
    found = options_of(name)
    if found is None or found["stage"] != stages.DEVELOPMENT or found["module_install"] == "repository":
        return own_modules(addons)
    if found["module_install"] == "list":
        return [m for m in str(found["modules"]).split(",") if m]
    # "all": what base's module list knows (the database has base already), with the repository's
    sql = "SELECT name FROM ir_module_module WHERE state = 'uninstalled' ORDER BY name"
    listed = capture(["docker", "exec", store.container(name, "db"), "psql", "-U", "odoo", "-d", db, "-tAc", sql])
    every = [m.strip() for m in listed.splitlines() if m.strip() and not m.strip().startswith(ALL_EXCLUDED)]
    if not every:
        job.note(
            "The list of Odoo's modules could not be read; only the repository's modules are installed.", "warning"
        )
    return list(dict.fromkeys([*every, *own_modules(addons)]))


# ---------------------------------------------------------------- the command


def major(version: str) -> int:
    try:
        return int(str(version).split(".")[0])
    except ValueError:
        return 0


def command_args(version: str, suite: str, tags: str, modules: list[str], db: str) -> list[str]:
    """The arguments of `odoo` that run the tests of `suite` on database `db`."""
    upgraded = list(dict.fromkeys([*modules, *(modules_of_tags(tags) if suite == "custom" else [])]))
    args = ["-d", db, "--test-enable", "--stop-after-init", "--no-http"]
    if upgraded:
        args += ["-u", ",".join(upgraded)]
    if major(version) >= TAGS_FROM:
        if suite == "selection" and modules:
            args.append("--test-tags=" + ",".join(f"/{module}" for module in modules))
        elif suite == "custom" and tags:
            args.append(f"--test-tags={tags}")  # one argument: a tag starting with "-" is never read as an option
    return args


# ---------------------------------------------------------------- reading Odoo's log


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def parse_log(text: str, exit_code: int | None = 0, timed_out: bool = False, minutes: int = 0) -> dict[str, Any]:
    """What a test run's log says: {"status" (success, warning, failed), "result" (a sentence), "messages" (the first
    error and warning lines), "tests", "failed", "errors", "warnings", "modules" (modules with a failing test)}."""
    tests = failed = errors = warnings = 0
    summary_seen = at_least_one = False
    modules: list[str] = []
    error_lines: list[str] = []
    warning_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if match := SUMMARY.search(line):
            summary_seen = True
            failed, errors, tests = max(failed, int(match[1])), max(errors, int(match[2])), max(tests, int(match[3]))
        elif match := RAN.search(line):
            tests += int(match[1])
        elif match := OLD_FAILED.search(line):
            failed += int(match[1] or 0)
            errors += int(match[2] or 0)
        if AT_LEAST_ONE in line:
            at_least_one = True
        found = LINE.match(line)
        if not found:
            continue
        level, logger, message = found[1], found[2], found[3]
        if level == "WARNING":
            warnings += 1
            warning_lines.append(f"WARNING {logger}: {message}"[:300])
        elif level in ("ERROR", "CRITICAL"):
            if logger.startswith("odoo.addons."):
                module = logger.split(".")[2]
                if module not in modules:
                    modules.append(module)
            elif not logger.startswith(TEST_LOGGERS):
                continue  # an error of something else (a mail server, a cron): Odoo's own loading warns about it
            error_lines.append(f"{level} {logger}: {message}"[:300])
    messages = (error_lines + warning_lines)[:MESSAGES_KEPT]
    report: dict[str, Any] = {"tests": tests, "failed": failed, "errors": errors, "warnings": warnings,
                              "modules": modules, "messages": messages}  # fmt: skip
    if timed_out:
        return report | {
            "status": "failed",
            "result": f"The tests took longer than {minutes} minutes and were stopped.",
        }
    if failed or errors or at_least_one or modules:
        where = f" ({', '.join(modules)})" if modules else ""
        counted = f" {failed} failed, {errors} with errors, of {tests} tests." if summary_seen or tests else ""
        return report | {"status": "failed", "result": f"{AT_LEAST_ONE}{where}.{counted}"}
    if error_lines:
        return report | {"status": "failed", "result": "Odoo logged errors while loading the modules for the tests."}
    if exit_code:
        return report | {
            "status": "failed",
            "result": f"Odoo stopped with an error (exit code {exit_code}) while running the tests.",
        }
    if warnings:
        return report | {
            "status": "warning",
            "result": f"Tests passed with {_plural(warnings, 'warning')} in Odoo's log.",
        }
    return report | {"status": "success", "result": f"{_plural(tests, 'test')} passed." if tests else "Tests passed."}


# ---------------------------------------------------------------- running them


def _sql(name: str, statement: str) -> list[str]:
    return ["docker", "exec", store.container(name, "db"), "psql", "-q", "-U", "odoo", "-d", "postgres", "-c",
            statement]  # fmt: skip


def _filestore(name: str, script: str, *paths: str) -> list[str]:
    """A shell script on the instance's filestore, as root in a short-lived container (the paths are arguments)."""
    capabilities = [arg for cap in odoo.FILESTORE_CAPABILITIES for arg in ("--cap-add", cap)]
    command = compose(name, "run", "--rm", "--no-deps", "-T", "--user", "root", *capabilities, "--entrypoint", "sh")
    return [*command, "odoo", "-c", script, "sh", *paths]


def copy_name(db: str) -> str:
    return f"{db[:57]}_tests"


def run(job: Job, name: str, db: str | None = None) -> dict[str, Any] | None:
    """Run the tests of instance `name`'s branch on its database (development) or a throw-away copy (staging), with
    Odoo stopped; record the outcome in job.details["tests"] and return it. None when no tests apply. Never raises
    for a failing test or a timeout: the build records them."""
    found = options_of(name)
    if found is None or found["test_suite"] == "off":
        return None
    env = store.read_env(name)
    db = db or odoo.databases(name)[0]
    copy = found["stage"] == stages.STAGING
    addons = store.addons_dir(name)
    job.step("Running the tests" + (" on a copy of the database" if copy else ""))
    from sudoo.instances import operations  # late: operations imports this module late too

    operations.refresh_addons_path(job, name)
    job.run(compose(name, "up", "-d", "db"), timeout=300)
    job.run(compose(name, "stop", "odoo"), timeout=300)
    odoo.wait_for_database(name)
    modules = sorted(set(own_modules(addons)) & backups.installed_modules(name, db))
    if found["test_suite"] == "selection" and not modules:
        report: dict[str, Any] = {"status": "success", "result": "No module of the repository is installed: no tests to run.",
                  "messages": [], "tests": 0, "failed": 0, "errors": 0, "warnings": 0, "modules": []}  # fmt: skip
        job.details["tests"] = report
        job.note(str(report["result"]))
        return report
    target = copy_name(db) if copy else db
    minutes = int(settings.project_test_timeout_minutes)
    start = len(job.log)
    timed_out, code = False, 0
    try:
        if copy:
            job.run(_sql(name, f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)'), timeout=600)
            job.run(_sql(name, f'CREATE DATABASE "{target}" OWNER odoo TEMPLATE "{db}"'), timeout=1800)
            script = 'rm -rf "$2" && if [ -d "$1" ]; then cp -a "$1" "$2"; fi'
            job.run(_filestore(name, script, f"/var/lib/odoo/filestore/{db}", f"/var/lib/odoo/filestore/{target}"),
                    timeout=1800)  # fmt: skip
        args = command_args(env.get("ODOO_VERSION", ""), found["test_suite"], str(found["test_tags"]), modules, target)
        start = len(job.log)
        try:
            code = job.run_unchecked(odoo.run_cmd(name, *args), timeout=minutes * 60)
        except JobError:
            timed_out = True
    finally:
        end = len(job.log)
        if copy:  # the copy goes whatever happened
            job.run_unchecked(_sql(name, f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)'), timeout=600)
            job.run_unchecked(_filestore(name, 'rm -rf "$1"', f"/var/lib/odoo/filestore/{target}"), timeout=600)
    report = parse_log("".join(job.log[start:end]), code, timed_out, minutes)
    job.details["tests"] = report
    job.details["tests_log"] = [start, end]
    job.note(str(report["result"]), "info" if report["status"] == "success" else "warning")
    return report


def before_serving(job: Job, name: str, db: str | None = None) -> None:
    """The hook of the instance operations (operations.py, staging.py): the tests of a build, before Odoo serves the
    new code. Only for jobs that build (job.details["build"], projects/builds.py)."""
    if not job.details.get("build") or not store.exists(name):
        return
    run(job, name, db)


# ---------------------------------------------------------------- a new database (on_new_commit "new_build")


def new_build(job: Job, name: str) -> None:
    """Build a development instance again from scratch: the branch's latest code, a new database (base, its
    modules by module_install, demo data by demo_data), the tests, then Odoo started. The old database and its files
    are replaced: development data is not kept, as on Odoo.sh."""
    from sudoo.instances import operations  # late: operations imports this module late too

    env = store.read_env(name)
    found = options_of(name)
    if found is None or found["stage"] != stages.DEVELOPMENT:
        raise JobError("Only a development instance of a project is built again from scratch.")
    db = env["DB_NAME"].split(",")[0]
    if not backups.DB_NAME_RE.fullmatch(db):
        raise JobError(f"{db[:70]!r} is not a database name this panel uses")
    addons = store.addons_dir(name)
    job.expect(7)
    job.step(f"Downloading the latest code of {env['GIT_BRANCH']}")
    operations._require_branch(job, name, addons)
    operations._pull(job, addons)
    job.step("Stopping Odoo")
    job.run(compose(name, "up", "-d", "db"), timeout=300)
    job.run(compose(name, "stop", "odoo"), timeout=300)
    odoo.wait_for_database(name)
    job.step(f"Creating a new database {db}")
    job.run(_sql(name, f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)'), timeout=600)
    job.run(_filestore(name, 'rm -rf "$1"', f"/var/lib/odoo/filestore/{db}"), timeout=600)
    operations.refresh_addons_path(job, name)
    enterprise = env.get("EDITION") == "enterprise"
    init = ["-d", db, "-i", "base,web_enterprise" if enterprise else "base", "--stop-after-init", "--no-http"]
    if found["demo_data"]:
        init.append("--with-demo")
    job.run(odoo.run_cmd(name, *init), timeout=1800)
    modules = modules_to_install(job, name, db)
    job.step(f"Installing modules ({len(modules)})" if modules else "No modules to install")
    if modules and job.run_unchecked(
        odoo.run_cmd(name, "-d", db, "-i", ",".join(modules), "--stop-after-init", "--no-http"), timeout=3600
    ):
        job.note("Some modules could not be installed. Odoo works; the technical log says why.", "warning")
    if settings.odoo_admin_password != "admin" or env.get("ODOO_ADMIN_PASSWORD", "admin") != "admin":
        operations._set_admin_password(job, name, db)
    url = exposure.address(name)
    if url:
        for cmd in odoo.public_address_cmds(name, url):
            job.run(cmd, timeout=60)
    before_serving(job, name, db)
    job.step("Starting Odoo")
    operations._start_odoo(job, name)
    job.note(f"Built from scratch: a new database {db} with the branch's latest code.")
