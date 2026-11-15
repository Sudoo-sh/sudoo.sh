"""Instances of plain Odoo, created without a GitHub repository: their name, their creation (no clone), and the
actions and settings that need a repository, which the API refuses. Docker is never run: jobs are recorded instead
of started, and the creation job runs only until its first Docker step."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

from sudoo.core import jobs
from sudoo.core.state import STATE
from sudoo.instances import integrity, odoo, operations, store

Client = tuple[FlaskClient, dict[str, str]]
PLAIN, CODED = "plain-one", "coded-one"


def _instance(name: str, plain: bool) -> None:
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(folder / "addons")], check=True)
    if not plain:
        subprocess.run(
            ["git", "-C", str(folder / "addons"), "remote", "add", "origin", "https://github.com/acme/shop"], check=True
        )
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39101 if plain else 39103,
            "ODOO_PORT": 39102 if plain else 39104,
            "GIT_BRANCH": "" if plain else "main",
            **({"SOURCE": "plain"} if plain else {}),
            "AUTO_UPDATE": "false",
            "AUTO_UPGRADE": "false",
            "AUTO_PAUSE": "false",
            "DELETE_AFTER_DAYS": "0",
            "CREATED_AT": int(time.time()),
        },
    )


@pytest.fixture(autouse=True)
def instances() -> Iterator[None]:
    _instance(PLAIN, plain=True)
    _instance(CODED, plain=False)
    yield
    for name in (PLAIN, CODED, "made-here"):
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
        integrity.forget(name)


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, tuple[Any, ...]]]:
    calls: list[tuple[Any, tuple[Any, ...]]] = []

    def fake_start(
        instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any
    ) -> jobs.Job:
        calls.append((fn, args))
        return jobs.Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    from sudoo.limits import quotas

    monkeypatch.setattr(quotas, "claim", lambda user: None)
    return calls


def test_plan_names_plain_odoo_after_version_and_edition(admin: Client) -> None:
    client, _ = admin
    plain = client.get("/api/v1/instance-plan?version=18.0&edition=enterprise").get_json()["data"]
    assert re.fullmatch(r"odoo-18-0-enterprise-[0-9a-f]{4}", plain["name"])
    assert plain["database"] == plain["name"].replace("-", "_")
    named = client.get("/api/v1/instance-plan?version=18.0&edition=community&name=Sales%20Demo").get_json()["data"]
    assert named["name"] == "sales-demo"
    coded = client.get("/api/v1/instance-plan?repo=https://github.com/acme/shop&branch=dev").get_json()["data"]
    assert coded["name"] == "shop-dev"


def test_create_plain_odoo(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    body = {
        "version": "18.0",
        "edition": "community",
        "delete_after_days": 0,
        "auto_update": True,
        "auto_upgrade": True,
    }
    answer = client.post("/api/v1/instances", json=body, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    fn, (plan,) = started[-1]
    store.release_ports(plan["ports"])
    assert fn is operations.create and plan["repo"] == "" and plan["branch"] == ""
    assert re.fullmatch(r"odoo-18-0-community-[0-9a-f]{4}", plan["name"])
    assert plan["auto_update"] is False and plan["auto_upgrade"] is False  # nothing to update

    answer = client.post("/api/v1/instances", json={**body, "name": "Made here"}, headers=headers)
    fn, (plan,) = started[-1]
    store.release_ports(plan["ports"])
    assert answer.status_code == 202 and plan["name"] == "made-here"


def test_creation_job_does_not_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The job of a plain instance makes an empty add-ons folder (a repository without a remote) and records it as
    plain; it stops here at its first Docker step."""
    commands: list[list[str]] = []

    class Stop(Exception):
        pass

    def fake_run(self: jobs.Job, cmd: list[str], **kwargs: Any) -> None:
        commands.append(cmd)
        if cmd[0] == "git":
            subprocess.run(cmd, check=True)

    def stop(*args: Any, **kwargs: Any) -> None:
        raise Stop

    monkeypatch.setattr(jobs.Job, "run", fake_run)
    monkeypatch.setattr(operations, "_remove_leftovers", lambda job, name: None)
    monkeypatch.setattr(integrity, "present", lambda image: False)
    monkeypatch.setattr(integrity, "pull_image", lambda job, image: commands.append(["docker", "pull", image]))
    monkeypatch.setattr(integrity, "apply", stop)
    plan = {
        "name": "made-here", "db_name": "made_here", "repo": "", "branch": "", "version": "18.0",
        "edition": "community", "demo": False, "install_modules": True, "delete_after_days": 0,
        "auto_update": False, "auto_upgrade": False, "auto_pause": True, "ports": {"GATEWAY_PORT": 1, "ODOO_PORT": 2},
        "created_by": "admin1", "from_backup": None,
    }  # fmt: skip
    store.folder("made-here").mkdir(parents=True)
    job = jobs.Job("made-here", "Create instance", "create", "admin1")
    with pytest.raises(Stop):
        operations._create(job, plan)
    # the images come first, in a step of their own, so a first download shows what takes the time
    assert [step["title"] for step in job.steps] == ["Downloading Odoo 18.0 image", "Preparing the instance"]
    assert commands[:2] == [["docker", "pull", "odoo:18.0"], ["docker", "pull", "postgres:16"]]
    assert not any("clone" in cmd for cmd in commands)
    assert ["git", "init", "-q", "-b", "main", str(store.addons_dir("made-here"))] in commands
    env = store.read_env("made-here")
    assert store.plain(env) and env["GIT_BRANCH"] == "" and env["AUTO_UPDATE"] == "false"
    assert list(store.addons_dir("made-here").iterdir()) == [store.addons_dir("made-here") / ".git"]
    assert odoo.CUSTOM_ADDONS_MOUNT not in store.config_file("made-here").read_text()  # an empty folder: not a path


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("update", {}),
        ("update-submodules", {}),
        ("switch-branch", {"branch": "main"}),
        ("add-submodule", {"url": "https://github.com/OCA/web"}),
        ("remove-submodule", {"path": "web"}),
    ],
)
def test_plain_odoo_refuses_what_needs_a_repository(
    admin: Client, started: list[Any], action: str, body: dict[str, str]
) -> None:
    client, headers = admin
    answer = client.post(f"/api/v1/instances/{PLAIN}/actions/{action}", json=body, headers=headers)
    assert answer.status_code == 409 and answer.mimetype == "application/problem+json"
    assert "runs plain Odoo without a GitHub repository" in answer.get_json()["detail"]
    assert not started
    coded = client.post(f"/api/v1/instances/{CODED}/actions/{action}", json=body, headers=headers)
    assert coded.status_code == 202, coded.get_json()


def test_plain_odoo_keeps_everything_else(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    for action in ("restart", "pause", "backup", "start"):
        answer = client.post(f"/api/v1/instances/{PLAIN}/actions/{action}", json={}, headers=headers)
        assert answer.status_code == 202, (action, answer.get_json())
    changed = client.patch(
        f"/api/v1/instances/{PLAIN}", json={"auto_pause": True, "delete_after_days": 7}, headers=headers
    )
    assert changed.status_code == 200
    refused = client.patch(f"/api/v1/instances/{PLAIN}", json={"auto_update": True}, headers=headers)
    assert refused.status_code == 409
    assert client.patch(f"/api/v1/instances/{PLAIN}", json={"auto_update": False}, headers=headers).status_code == 200


def test_resource_says_plain(admin: Client) -> None:
    client, _ = admin
    plain = client.get(f"/api/v1/instances/{PLAIN}").get_json()["data"]
    assert plain["source"] == "plain" and plain["git"]["branch"] is None and plain["git"]["repository"] is None
    coded = client.get(f"/api/v1/instances/{CODED}").get_json()["data"]
    assert coded["source"] == "repository" and coded["git"]["branch"] == "main"


def test_connect_repository_only_for_plain_odoo(admin: Client, started: list[Any]) -> None:
    client, headers = admin
    body = {"repo": "https://github.com/acme/shop", "branch": "dev", "install_modules": True}
    answer = client.post(f"/api/v1/instances/{PLAIN}/actions/connect-repository", json=body, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    assert started[-1] == (operations.connect_repository, (PLAIN, "https://github.com/acme/shop", "dev", True))
    refused = client.post(f"/api/v1/instances/{CODED}/actions/connect-repository", json=body, headers=headers)
    assert refused.status_code == 409 and "has a GitHub repository already" in refused.get_json()["detail"]
    bad = client.post(f"/api/v1/instances/{PLAIN}/actions/connect-repository", json={"repo": "x"}, headers=headers)
    assert bad.status_code == 400
    assert len(started) == 1


def test_connect_repository_moves_the_clone_into_the_same_folder(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The clone goes into the existing add-ons folder (the containers mount that folder), the instance stops being
    plain, and the code change is applied; nothing is installed when not asked."""
    source = tmp_path / "shop"
    (source / "sale_custom").mkdir(parents=True)
    (source / "sale_custom" / "__manifest__.py").write_text("{'name': 'Sale custom'}\n")
    for cmd in (
        ["init", "-q", "-b", "dev"],
        ["add", "."],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
    ):
        subprocess.run(["git", "-C", str(source), *cmd], check=True)

    def fake_run(self: jobs.Job, cmd: list[str], **kwargs: Any) -> None:
        assert cmd[0] == "git", cmd  # no Docker: nothing is installed
        subprocess.run(cmd, check=True)

    applied: list[str] = []
    monkeypatch.setattr(jobs.Job, "run", fake_run)
    monkeypatch.setattr(operations, "_apply_code_change", lambda job, name: applied.append(name))
    addons = store.addons_dir(PLAIN)
    inode = addons.stat().st_ino
    job = jobs.Job(PLAIN, "Connect a repository", "update", "admin1")
    operations.connect_repository(job, PLAIN, str(source), "dev")
    assert addons.stat().st_ino == inode and (addons / "sale_custom" / "__manifest__.py").exists()
    assert not (store.folder(PLAIN) / "addons.connecting").exists()
    env = store.read_env(PLAIN)
    assert not store.plain(env) and env["GIT_BRANCH"] == "dev"
    assert applied == [PLAIN]
    from sudoo.web.v1.instances import has_repository

    assert has_repository(PLAIN)
    with pytest.raises(jobs.JobError, match="already has"):
        operations.connect_repository(job, PLAIN, str(source), "dev")


def test_connect_repository_never_overwrites_files(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "shop"
    source.mkdir()
    for cmd in (
        ["init", "-q", "-b", "main"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x", "--allow-empty"],
    ):
        subprocess.run(["git", "-C", str(source), *cmd], check=True)
    monkeypatch.setattr(jobs.Job, "run", lambda self, cmd, **kwargs: subprocess.run(cmd, check=True))
    (store.addons_dir(PLAIN) / "mine.txt").write_text("kept")
    job = jobs.Job(PLAIN, "Connect a repository", "update", "admin1")
    with pytest.raises(jobs.JobError, match="not empty"):
        operations.connect_repository(job, PLAIN, str(source), "main")
    assert (store.addons_dir(PLAIN) / "mine.txt").read_text() == "kept"
    assert store.plain(store.read_env(PLAIN))
    assert not (store.folder(PLAIN) / "addons.connecting").exists()
