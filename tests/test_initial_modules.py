"""Choosing the modules a new instance's database gets: the module list of a repository's branch (GET
/api/v1/repositories/modules, read from the panel's copy of the repository, submodules included), the standard Odoo
apps (GET /api/v1/odoo-apps), "modules" when creating (exactly those installed with -i), the older "install_modules"
switch, refused names, and the project's choice for the instances the panel creates itself. Docker is never run."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask.testing import FlaskClient

from conftest import sign_in
from sudoo import projects
from sudoo.config import live, settings
from sudoo.core import jobs
from sudoo.core.jobs import Job
from sudoo.core.state import STATE
from sudoo.instances import integrity, odoo_apps, operations, store
from sudoo.limits import quotas
from sudoo.projects import automation, branches
from sudoo.repos import github, mirror
from sudoo.repos import modules as repository_modules
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
ADDRESS = "https://github.com/acme/shop"
SHARED = "https://github.com/acme/shared-addons"
NAMES = ["picked", "shop-feature-x"]


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def _write(path: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        (path / relative).parent.mkdir(parents=True, exist_ok=True)
        (path / relative).write_text(text)


def _repository(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    _write(path, files)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "First")
    return _git(path, "rev-parse", "HEAD")


@pytest.fixture
def repositories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """acme/shop with its own modules and a submodule acme/shared-addons (pinned at its commit); the mirror reads
    both from local folders."""
    shared = tmp_path / "shared"
    shared_head = _repository(
        shared,
        {
            "shared_base/__manifest__.py": "{'name': 'Shared base', 'version': '18.0.1.0.0'}",
            "shared_app/__manifest__.py": "{'name': 'Shared app', 'application': True}",
        },
    )
    shop = tmp_path / "shop"
    _repository(
        shop,
        {
            "addons/acme_sale/__manifest__.py": "{'name': 'Acme Sales', 'application': True, 'summary': 'Quotes'}",
            "addons/acme_sale/models.py": "x = 1\n",
            "addons/acme_report/__manifest__.py": "{'name': 'Acme reports'}",
            "addons/acme_old/__manifest__.py": "{'name': 'Old', 'installable': False}",
            "addons/acme_sale/sub_module/__manifest__.py": "{'name': 'Inside another module'}",
            "setup/acme_sale/__manifest__.py": "{'name': 'Packaging copy'}",
            "README.md": "Shop\n",
            ".gitmodules": f'[submodule "shared"]\n\tpath = shared\n\turl = {SHARED}\n',
        },
    )
    _git(shop, "update-index", "--add", "--cacheinfo", f"160000,{shared_head},shared")
    _git(shop, "commit", "-q", "-m", "Shared add-ons")
    _git(shop, "branch", "feature-x")
    local = {ADDRESS.lower(): shop, SHARED.lower(): shared}
    real = mirror.refresh
    monkeypatch.setattr(mirror, "refresh", lambda address, force=False: real(str(local[address.lower()]), force))
    return local


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
        integrity.forget(name)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    for key in list(branches._heads):
        branches.forget(key)
    shutil.rmtree(settings.manager_dir / "mirrors", ignore_errors=True)
    mirror._fetched.clear()
    repository_modules._cache.clear()
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any, tuple[Any, ...]]]:
    """Jobs are recorded (instance, function, arguments) instead of run."""
    calls: list[tuple[str, Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system",
                   **_: Any) -> Job:  # fmt: skip
        calls.append((instance, fn, args))
        return Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    monkeypatch.setattr(quotas, "claim", lambda user: None)
    return calls


def _plan(started: list[Any]) -> dict[str, Any]:
    plan: dict[str, Any] = started[-1][2][0]
    store.release_ports(plan["ports"])
    return plan


# ---------------------------------------------------------------- listing


def test_the_modules_of_a_branch_with_its_submodules(admin: Client, repositories: dict[str, Path]) -> None:
    client, _ = admin
    answer = client.get(f"/api/v1/repositories/modules?url={ADDRESS}&branch=main")
    assert answer.status_code == 200, answer.get_json()
    found = answer.get_json()["data"]
    assert found["repository"] == "acme/shop" and found["branch"] == "main" and len(found["commit"]) == 40
    by_name = {module["name"]: module for module in found["modules"]}
    # not installable, inside another module, in a setup folder: left out, as the instance would never find them
    assert sorted(by_name) == ["acme_report", "acme_sale", "shared_app", "shared_base"]
    assert by_name["acme_sale"] == {
        "name": "acme_sale",
        "title": "Acme Sales",
        "summary": "Quotes",
        "application": True,
        "path": "addons/acme_sale",
        "submodule": None,
    }
    assert by_name["shared_base"]["submodule"] == "shared" and by_name["shared_base"]["path"] == "shared/shared_base"
    assert found["problems"] == []
    # the repository's own applications are selected first (never its submodules')
    assert found["default"] == {"source": "applications", "modules": ["acme_sale"]}
    # nothing was created
    assert not store.folder("picked").exists()

    missing = client.get(f"/api/v1/repositories/modules?url={ADDRESS}&branch=nope")
    assert missing.status_code == 404 and "no branch nope" in missing.get_json()["detail"]
    assert client.get("/api/v1/repositories/modules?url=https://gitlab.com/a/b&branch=main").status_code == 400


def test_without_applications_every_module_of_the_repository_is_selected_first() -> None:
    listed = [
        {"name": "a", "application": False, "submodule": None},
        {"name": "b", "application": False, "submodule": None},
        {"name": "c", "application": True, "submodule": "oca"},
    ]
    assert repository_modules.recommended(listed) == ["a", "b"]


def test_listing_modules_needs_the_right_to_create_instances(app: Any, repositories: dict[str, Path]) -> None:
    from sudoo import accounts

    if accounts.get("modviewer") is None:
        accounts.create("modviewer", "viewer", "harbor-quartz-fennel-38")
    viewer, _ = sign_in(app, "modviewer", "harbor-quartz-fennel-38")
    assert viewer.get(f"/api/v1/repositories/modules?url={ADDRESS}&branch=main").status_code == 403
    assert viewer.get("/api/v1/odoo-apps?version=18.0&edition=community").status_code == 403


def test_a_project_branch_selects_its_projects_list_first(admin: Client, repositories: dict[str, Path]) -> None:
    client, _ = admin
    project = projects.create(repository="acme/shop", odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    projects.change(project["id"], lambda p: {**p, "options": {"module_install": "list", "modules": "crm,acme_report"}})
    found = client.get(f"/api/v1/repositories/modules?url={ADDRESS}&branch=feature-x").get_json()["data"]
    assert found["default"] == {"source": "project_list", "modules": ["crm", "acme_report"]}


def test_the_odoo_apps_follow_the_version_and_the_edition(admin: Client) -> None:
    client, _ = admin
    community = client.get("/api/v1/odoo-apps?version=18.0&edition=community").get_json()
    names = [app["name"] for app in community["data"]]
    assert {"sale_management", "account", "crm", "stock", "website", "point_of_sale", "project", "hr"} <= set(names)
    assert "helpdesk" not in names and "accountant" not in names and community["meta"]["max_modules"] == 200
    enterprise = [a["name"] for a in client.get("/api/v1/odoo-apps?version=18.0&edition=enterprise").get_json()["data"]]
    assert "helpdesk" in enterprise and "accountant" in enterprise and names == enterprise[: len(names)]
    assert "knowledge" not in odoo_apps.names("15.0", "enterprise") and "knowledge" in odoo_apps.names("saas-17.2",
                                                                                                         "enterprise")  # fmt: skip
    assert "note" in odoo_apps.names("16.0", "community") and "note" not in odoo_apps.names("17.0", "community")
    assert len(names) == len(set(names))


# ---------------------------------------------------------------- creating


def test_creating_with_modules_plans_exactly_those(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    body = {"version": "18.0", "edition": "community", "name": "picked"}
    answer = client.post("/api/v1/instances", json={**body, "modules": ["sale_management", "crm", "crm"]},
                         headers=headers)  # fmt: skip
    assert answer.status_code == 202, answer.get_json()
    plan = _plan(started)
    assert plan["modules"] == ["sale_management", "crm"] and plan["install_modules"] is True

    # an empty list: an empty database
    assert client.post("/api/v1/instances", json={**body, "modules": []}, headers=headers).status_code == 202
    plan = _plan(started)
    assert plan["modules"] == [] and plan["install_modules"] is False


def test_the_older_switch_still_works(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    body = {"repo": ADDRESS, "branch": "main", "version": "18.0", "edition": "community", "name": "picked"}
    assert client.post("/api/v1/instances", json={**body, "install_modules": True}, headers=headers).status_code == 202
    plan = _plan(started)
    assert plan["modules"] is None and plan["install_modules"] is True
    assert client.post("/api/v1/instances", json=body, headers=headers).status_code == 202
    plan = _plan(started)
    assert plan["modules"] is None and plan["install_modules"] is False


@pytest.mark.parametrize(
    "modules",
    [["sale", "-u all"], ["Sale"], ["sale,crm"], "sale", [1], ["a" * 65]],
)
def test_wrong_module_names_are_refused(admin: Client, started: list[Any], modules: Any) -> None:
    client, headers = admin
    body = {"version": "18.0", "edition": "community", "name": "picked", "modules": modules}
    answer = client.post("/api/v1/instances", json=body, headers=headers)
    assert answer.status_code == 400, answer.get_json()
    assert started == []


def test_too_many_modules_are_refused(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    live.change({"max_initial_modules": 2})
    body = {"version": "18.0", "edition": "community", "name": "picked", "modules": ["crm", "sale", "stock"]}
    answer = client.post("/api/v1/instances", json=body, headers=headers)
    assert answer.status_code == 400 and "at most 2 modules" in answer.get_json()["detail"]
    assert started == []


def _run_creation(monkeypatch: pytest.MonkeyPatch, plan: dict[str, Any]) -> list[list[str]]:
    """Run operations.create up to the moment Odoo would serve the instance; the commands it ran."""
    commands: list[list[str]] = []

    class Stop(Exception):
        pass

    def stop(job: Job, name: str, db: str | None = None) -> None:
        raise Stop

    monkeypatch.setattr(operations, "_download_images", lambda job, version: None)
    monkeypatch.setattr(operations, "_remove_leftovers", lambda job, name: None)
    monkeypatch.setattr(operations, "refresh_addons_path", lambda job, name: None)
    monkeypatch.setattr(operations.integrity, "apply", lambda job, name, pull=False: None)
    monkeypatch.setattr(operations.events, "publish", lambda topic, key, data=None: None)
    monkeypatch.setattr(operations, "_before_serving", stop)
    monkeypatch.setattr(Job, "run_unchecked", lambda self, cmd, timeout=None, input=None: commands.append(cmd) or 0)
    with pytest.raises(Stop):
        operations.create(Job(plan["name"], "Create instance", "create", "admin1"), plan)
    return commands


def _installs(commands: list[list[str]]) -> list[str]:
    """The -i argument of each `odoo` run after the database was made (base, web_enterprise left out)."""
    return [cmd[cmd.index("-i") + 1] for cmd in commands if "-i" in cmd and "--no-http" in cmd
            and not cmd[cmd.index("-i") + 1].startswith("base")]  # fmt: skip


PLAN = {
    "name": "picked",
    "db_name": "picked",
    "repo": "",
    "branch": "",
    "version": "18.0",
    "edition": "community",
    "demo": False,
    "auto_update": False,
    "auto_upgrade": False,
    "auto_pause": True,
    "delete_after_days": 3,
    "stage": "development",
    "created_by": "admin1",
    "from_backup": None,
    "from_production": None,
    "project": "",
}


def test_a_creation_with_modules_installs_exactly_those(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = {**PLAN, "ports": store.reserve_ports({}), "install_modules": True, "modules": ["sale_management", "crm"]}
    commands = _run_creation(monkeypatch, plan)
    assert _installs(commands) == ["sale_management,crm"]


def test_a_creation_with_an_empty_list_installs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = {**PLAN, "ports": store.reserve_ports({}), "install_modules": False, "modules": []}
    assert _installs(_run_creation(monkeypatch, plan)) == []


def test_a_creation_with_the_older_switch_installs_the_repositorys_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operations, "own_modules", lambda addons: ["acme_sale", "acme_report"])
    plan = {**PLAN, "repo": ADDRESS, "branch": "main", "ports": store.reserve_ports({}), "install_modules": True}
    assert _installs(_run_creation(monkeypatch, plan)) == ["acme_sale,acme_report"]


def test_a_new_branch_gets_the_projects_modules(monkeypatch: pytest.MonkeyPatch, started: list[Any]) -> None:
    """The instance the panel creates for a new branch installs the project's "Modules to install in new instances"
    (module_install "list") without anyone choosing."""
    monkeypatch.setattr(github, "heads", lambda address: ({"main": "a" * 40, "feature-x": "b" * 40}, "main", None))
    project = projects.create(repository="acme/shop", odoo_version="18.0", edition="community", origin="linked",
                              user="t")  # fmt: skip
    options = {"module_install": "list", "modules": "crm,acme_report"}
    projects.change(project["id"], lambda p: {**p, "seen": ["main"], "options": options})
    made = automation.branch_created("acme/shop", "feature-x")
    assert made == "shop-feature-x"
    plan = _plan(started)
    assert plan["modules"] is None and plan["install_modules"] is True  # the panel's choice: the project's options
    plan["ports"] = store.reserve_ports({})
    assert _installs(_run_creation(monkeypatch, plan)) == ["crm,acme_report"]
