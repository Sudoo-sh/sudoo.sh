"""What the panel can do with an instance. Every function here runs as a job (see jobs.py): fn(job, ...).

Each function is one straight sequence of steps; a failing step raises JobError and the job stops there.
"""
import secrets
import shutil
import time

from . import backups, gateway, instances, monitor, odoo, settings, tunnels, vault
from .gitrepo import branch_on_github, changed_modules, git, module_folders, own_modules, snapshot
from .instances import compose
from .jobs import JobError
from .shell import capture
from .state import STATE


# --------------------------------------------------------------------------- helpers

def refresh_addons_path(job, name):
    """Point Odoo's addons_path at every folder of the repository that contains modules, then Enterprise.

    The instance's own modules come first, so one of them can replace an Enterprise module of the same name.
    """
    addons = instances.addons_dir(name)
    paths = [settings.CUSTOM_ADDONS_MOUNT if folder == addons
             else f"{settings.CUSTOM_ADDONS_MOUNT}/{folder.relative_to(addons)}" for folder in module_folders(addons)]
    if instances.read_env(name)["EDITION"] == "enterprise":
        paths.append(settings.ENTERPRISE_ADDONS_MOUNT)
    job.write(f"addons_path = {odoo.set_addons_path(name, paths)}\n")


def _remove_leftovers(job, name):
    """Remove containers and volumes of an earlier instance with the same name, so nothing old is reused."""
    label = f"label=com.docker.compose.project=odoo-{name}"
    containers = capture(["docker", "ps", "-aq", "--filter", label]).split()
    volumes = capture(["docker", "volume", "ls", "-q", "--filter", label]).split()
    if containers:
        job.run(["docker", "rm", "-f", *containers])
    if volumes:
        job.run(["docker", "volume", "rm", "-f", *volumes])


def _paused(name):
    return not monitor.STATUS.get(name, {}).get("running") and bool(
        STATE.get(name).get("paused") or STATE.get(name).get("switched_off"))


def _wait_until_ready(job, name):
    if odoo.wait_until_ready(instances.read_env(name)["ODOO_PORT"]):
        monitor.mark_running(name)
    else:
        job.note("Odoo is taking longer than usual to answer. It may still be starting; check the logs if it does "
                 "not come up in a few minutes.", "warning")


def _start_odoo(job, name, restart=False):
    """Start the instance (database and Odoo), restart Odoo if asked, and wait until Odoo answers.

    Its connector is started too, in case it was stopped, so the instance keeps its public link.
    """
    refresh_addons_path(job, name)
    tunnels.ensure(name)
    job.run(compose(name, "up", "-d", "--remove-orphans"), timeout=900)
    if restart:
        job.run(compose(name, "restart", "odoo"), timeout=300)
    STATE.update(name, paused=False, switched_off=False)
    STATE.record_visit(name)
    job.step("Waiting for Odoo to be ready")
    _wait_until_ready(job, name)


# --------------------------------------------------------------------------- create and delete

def create(job, plan):
    """Create an instance from `plan` (see web/api_instances.py). On failure everything created is removed."""
    name, folder = plan["name"], instances.folder(plan["name"])
    try:
        folder.mkdir(parents=True)        # claims the name; the cleanup below never touches another instance
    except FileExistsError:
        instances.release_ports(plan["ports"])
        raise JobError("An instance with this name already exists. Choose another name.")
    try:
        _create(job, plan)
    except Exception:
        job.write("\nRemoving the half-created instance so the same name can be used again.\n")
        if (folder / "docker-compose.yml").exists():
            job.run_unchecked(compose(name, "down", "-v", "--remove-orphans"), timeout=300)
        gateway.remove(name)
        tunnels.remove(name)
        shutil.rmtree(folder, ignore_errors=True)
        STATE.remove(name)
        raise
    finally:
        instances.release_ports(plan["ports"])


def _create(job, plan):
    name, db, version = plan["name"], plan["db_name"], plan["version"]
    enterprise = plan["edition"] == "enterprise"
    addons = instances.addons_dir(name)

    job.step("Preparing the instance")
    (instances.folder(name) / "config").mkdir()
    _remove_leftovers(job, name)
    if plan["repo"]:
        job.step("Downloading the code from GitHub")
        job.run(["git", "clone", "--recurse-submodules", "-b", plan["branch"], "--", plan["repo"], str(addons)],
                timeout=1800)
    else:
        job.run(["git", "init", "-q", "-b", plan["branch"], str(addons)])

    db_password, master_password = secrets.token_urlsafe(18), secrets.token_urlsafe(16)
    instances.write_env(name, {
        "ODOO_VERSION": version,
        "EDITION": plan["edition"],
        **{key: str(port) for key, port in plan["ports"].items()},
        "DB_NAME": db,
        "DB_PASSWORD": db_password,
        "GIT_BRANCH": plan["branch"],
        "ODOO_ADMIN_PASSWORD": settings.ODOO_ADMIN_PASSWORD,
        "MASTER_PASSWORD": vault.encrypt(master_password, instances.master_password_label(name)),
        "AUTO_UPDATE": "true",
        "AUTO_UPGRADE": "true",
        "AUTO_PAUSE": "true",
        "DELETE_AFTER_DAYS": plan["delete_after_days"],     # 0: keep until deleted by hand
        "CREATED_AT": int(time.time()),
    })
    odoo.write_config(name, db, db_password, master_password)
    (instances.folder(name) / "docker-compose.yml").write_text(odoo.compose_file(name, version, enterprise))
    refresh_addons_path(job, name)
    STATE.update(name, last_visit=time.time(), paused=False, switched_off=False)

    job.step(f"Downloading Odoo {version} (the first time can take a few minutes)")
    job.run(compose(name, "pull"), timeout=1800)

    job.step("Starting the database")
    job.run(compose(name, "up", "-d", "db"), timeout=300)

    job.step(f"Creating the Odoo {'Enterprise' if enterprise else 'Community'} database (about 1-2 minutes)")
    # web_enterprise turns the database into Enterprise: Enterprise interface and apps
    init = ["-d", db, "-i", "base,web_enterprise" if enterprise else "base", "--stop-after-init", "--no-http"]
    if plan["demo"]:
        init.append("--with-demo")
    job.run(odoo.run_cmd(name, *init), timeout=1800)

    modules = own_modules(addons) if plan["install_modules"] else []
    if modules:
        job.step(f"Installing the repository's modules ({len(modules)})")
        if job.run_unchecked(odoo.run_cmd(name, "-d", db, "-i", ",".join(modules), "--stop-after-init", "--no-http"),
                             timeout=3600):
            job.note("Some of the repository's modules could not be installed. Odoo works; install them from Apps "
                     "after a developer has looked at the technical log.", "warning")

    if settings.ODOO_ADMIN_PASSWORD != "admin":   # a new database already has the login admin / admin
        job.step("Setting the admin password")
        script = (f"env['res.users'].browse(2).write({{'password': {settings.ODOO_ADMIN_PASSWORD!r}}})\n"
                  "env.cr.commit()\n")
        job.run(odoo.run_cmd(name, "shell", "-d", db), timeout=600, input=script)

    job.step("Creating the public link")
    gateway.add(name)
    tunnels.ensure(name)
    url = tunnels.wait_for_address(name)
    if not url:
        raise JobError("Cloudflare did not provide a public address for this instance.")
    for cmd in odoo.public_address_cmds(name, url):
        job.run(cmd, timeout=60)

    job.step("Starting Odoo")
    job.run(compose(name, "up", "-d"), timeout=600)
    _wait_until_ready(job, name)
    job.result = {"name": name, "url": url, "login": "admin", "password": settings.ODOO_ADMIN_PASSWORD, "db": db}


def delete(job, name):
    """Back up every database, then remove the instance completely. No backup, no deletion."""
    job.step("Backing up the database and files")
    saved = backups.create(job, name)
    job.note(f"Backup kept: {', '.join(path.name for path in saved)}")
    job.step("Removing Odoo, its database and its files")
    job.run(compose(name, "down", "-v", "--remove-orphans"), timeout=600)
    gateway.remove(name)
    tunnels.remove(name)
    job.step("Removing the instance folder")
    shutil.rmtree(instances.folder(name))
    STATE.remove(name)


def backup(job, name):
    job.step("Backing up the database and files")
    saved = backups.create(job, name)
    if _paused(name):
        job.run(compose(name, "stop"), timeout=300)       # leave a paused instance paused
    job.note(f"Backup saved: {', '.join(path.name for path in saved)}")


def expires_at(env):
    """When the instance is deleted automatically (a timestamp), or None when it is kept."""
    days = int(env["DELETE_AFTER_DAYS"])
    return int(env["CREATED_AT"]) + days * 86400 if days > 0 else None


# --------------------------------------------------------------------------- running state

def restart(job, name):
    job.step("Restarting Odoo")
    _start_odoo(job, name, restart=True)


def wake(job, name):
    job.step("Starting Odoo")
    _start_odoo(job, name)


def pause(job, name):
    job.step("Pausing to free server resources")
    job.run(compose(name, "stop"), timeout=300)
    STATE.update(name, paused=True, switched_off=False)
    monitor.mark_stopped(name)


def switch_off(job, name):
    job.step("Switching off")
    job.run(compose(name, "stop"), timeout=300)
    STATE.update(name, switched_off=True, paused=False)
    monitor.mark_stopped(name)


# --------------------------------------------------------------------------- code updates

def _pull(job, addons):
    job.run(git(addons, "pull", "--ff-only"), timeout=900)
    job.run(git(addons, "submodule", "sync", "--recursive"))
    job.run(git(addons, "submodule", "update", "--init", "--recursive"), timeout=1800)


def upgrade_modules(job, name, modules, stay_paused=False):
    """Run `odoo -u <modules>` on every database of the instance, with Odoo stopped meanwhile."""
    job.step(f"Upgrading modules: {', '.join(modules)}")
    refresh_addons_path(job, name)
    job.run(compose(name, "up", "-d", "db"), timeout=300)
    job.run(compose(name, "stop", "odoo"), timeout=300)
    for db in odoo.databases(name):
        job.run(odoo.run_cmd(name, "-d", db, "-u", ",".join(modules), "--stop-after-init", "--no-http"), timeout=3600)
    if stay_paused:
        job.run(compose(name, "stop"), timeout=300)
        job.note("The instance stays paused and uses the new code when someone opens it.")
    else:
        job.step("Starting Odoo")
        _start_odoo(job, name)


def update_code(job, name, submodules_only=False):
    """Get new code from GitHub (or move submodules to their latest commit), upgrade what changed, restart."""
    addons = instances.addons_dir(name)
    if not capture(git(addons, "remote", "get-url", "origin")):
        raise JobError("This instance has no GitHub repository.")
    paused = _paused(name)
    before = snapshot(addons)
    if submodules_only:
        job.step("Moving the submodules to their latest version")
        job.run(git(addons, "submodule", "update", "--init", "--remote", "--recursive"), timeout=1800)
    else:
        job.step("Downloading the latest code from GitHub")
        _require_branch(job, name, addons)
        _pull(job, addons)
    after = snapshot(addons)
    if before == after:
        job.note("Already up to date: there is nothing new on GitHub.")
        return
    modules = changed_modules(addons, before, after)
    job.note(f"Changed modules: {', '.join(modules)}" if modules else "New code downloaded.")
    if modules and instances.read_env(name)["AUTO_UPGRADE"] == "true":
        upgrade_modules(job, name, modules, stay_paused=paused)
    elif paused:
        refresh_addons_path(job, name)
        job.note("The instance stays paused and uses the new code when someone opens it.")
    else:
        restart(job, name)


def _require_branch(job, name, addons):
    """Stop with a clear message when the instance's branch was deleted on GitHub (often after a merged pull
    request); git itself only says "no such ref was fetched"."""
    branch = instances.read_env(name)["GIT_BRANCH"]
    exists = branch_on_github(addons, branch)
    STATE.update(name, branch_missing=exists is False)
    if exists is False:
        job.write(f"The branch {branch} no longer exists on GitHub.\n")
        raise JobError(f"branch {branch} no longer exists on GitHub")


def switch_branch(job, name, branch):
    addons = instances.addons_dir(name)
    job.step(f"Switching the code to branch {branch}")
    job.run(git(addons, "fetch", "origin", branch), timeout=900)
    job.run(git(addons, "checkout", "-B", branch, f"origin/{branch}"))
    job.run(git(addons, "branch", f"--set-upstream-to=origin/{branch}", branch))
    job.run(git(addons, "submodule", "update", "--init", "--recursive"), timeout=1800)
    instances.update_env(name, GIT_BRANCH=branch)
    STATE.update(name, branch_missing=False)
    if not _paused(name):
        restart(job, name)


def _apply_code_change(job, name):
    """After the code changed: a paused instance only gets the new addons_path, a running one restarts."""
    if _paused(name):
        refresh_addons_path(job, name)
    else:
        restart(job, name)


def _save_to_github(job, addons, undo):
    """Push the last commit; if GitHub refuses, run `undo` so the server matches GitHub again, and fail."""
    if not capture(git(addons, "remote", "get-url", "origin")):
        return
    job.step("Saving the change to GitHub")
    if job.run_unchecked(git(addons, "push"), timeout=300):
        undo()
        raise JobError("GitHub refused the change. The repository's deploy key needs write access "
                       "(tick 'Allow write access' for it on GitHub). The change was undone on the server.")


def add_submodule(job, name, url, path, branch):
    addons = instances.addons_dir(name)
    job.step(f"Adding submodule {path}")
    job.run(git(addons, "submodule", "add", *(["-b", branch] if branch else []), "--", url, path), timeout=1800)
    job.run(git(addons, "submodule", "update", "--init", "--recursive", path), timeout=1800)
    job.run(git(addons, "commit", "-m", f"Add submodule {path}"))

    def undo():
        job.run_unchecked(git(addons, "reset", "--hard", "HEAD~1"))
        job.run_unchecked(git(addons, "submodule", "deinit", "-f", path))
        shutil.rmtree(addons / ".git" / "modules" / path, ignore_errors=True)
        shutil.rmtree(addons / path, ignore_errors=True)

    _save_to_github(job, addons, undo)
    _apply_code_change(job, name)


def remove_submodule(job, name, path):
    addons = instances.addons_dir(name)
    job.step(f"Removing submodule {path}")
    job.run(git(addons, "submodule", "deinit", "-f", path))
    job.run(git(addons, "rm", "-f", path))
    shutil.rmtree(addons / ".git" / "modules" / path, ignore_errors=True)
    job.run(git(addons, "commit", "-m", f"Remove submodule {path}"))

    def undo():
        job.run_unchecked(git(addons, "reset", "--hard", "HEAD~1"))
        job.run_unchecked(git(addons, "submodule", "update", "--init", "--recursive", path))

    _save_to_github(job, addons, undo)
    _apply_code_change(job, name)
