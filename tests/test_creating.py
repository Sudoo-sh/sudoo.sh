"""An instance while it is being created: the API shows it with status "creating" from the moment its creation is
accepted (before any of its files exist), with the job as current_job; a creation that fails and leaves nothing
answers 404 again while its job stays readable. Also the image download that starts every creation, with its
progress in the log, and downloading images ahead of time (PREPULL_IMAGES). Docker is never run."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

from sudoo import background
from sudoo.config import live
from sudoo.core import jobs
from sudoo.core.jobs import JobError
from sudoo.core.state import STATE
from sudoo.instances import integrity, store

Client = tuple[FlaskClient, dict[str, str]]
NAME = "being-made"
PLAN = {
    "version": "18.0",
    "edition": "community",
    "db_name": "being_made",
    "repo": "",
    "branch": "",
    "created_by": "admin1",
    "auto_update": False,
    "auto_upgrade": False,
    "auto_pause": True,
    "delete_after_days": 3,
}


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    shutil.rmtree(store.folder(NAME), ignore_errors=True)
    STATE.remove(NAME)
    integrity.forget(NAME)
    live.change(dict.fromkeys(live.overrides()))


def _wait(job: jobs.Job, statuses: tuple[str, ...]) -> None:
    deadline = time.time() + 5
    while job.status not in statuses:
        assert time.time() < deadline, job.status
        time.sleep(0.01)


def _start(work: Any) -> jobs.Job:
    return jobs.start(NAME, "Create instance", "create", work, user="admin1", details=PLAN)


def test_creating_instance_is_shown_before_its_files_exist(admin: Client) -> None:
    client, headers = admin
    release = threading.Event()

    def work(job: jobs.Job) -> None:
        job.expect(4)
        job.step("Downloading Odoo 18.0 image")
        release.wait(5)

    job = _start(work)
    try:
        _wait(job, ("running",))
        answer = client.get(f"/api/v1/instances/{NAME}")
        assert answer.status_code == 200, answer.get_json()
        found = answer.get_json()["data"]
        assert found["status"] == "creating" and found["url"] is None
        assert found["odoo"] == {"version": "18.0", "edition": "community", "database": "being_made"}
        assert found["source"] == "plain" and found["git"]["branch"] is None
        assert found["settings"] == {
            "auto_update": False,
            "auto_upgrade": False,
            "auto_pause": True,
            "delete_after_days": 3,
        }
        assert found["created_by"] == "admin1"
        current = found["current_job"]
        assert current["id"] == job.id and current["status"] == "running" and current["progress_percent"] == 0
        assert current["steps"] == [{"title": "Downloading Odoo 18.0 image", "status": "running"}]
        assert found["recent_jobs"][0]["id"] == job.id and found["backups"] == [] and found["recent_commits"] == []

        listed = client.get("/api/v1/instances").get_json()["data"]
        assert [i["status"] for i in listed if i["name"] == NAME] == ["creating"]
        # what needs the instance's files waits for them: 409, not "does not exist"
        refused = client.post(f"/api/v1/instances/{NAME}/actions/restart", json={}, headers=headers)
        assert refused.status_code == 409 and "still being created" in refused.get_json()["detail"]
        assert client.get(f"/api/v1/instances/{NAME}/credentials").status_code == 409
    finally:
        release.set()
    _wait(job, ("success", "failed"))
    assert client.get(f"/api/v1/instances/{NAME}").status_code == 404  # nothing was made in this test


def test_queued_creation_is_shown_as_waiting(admin: Client) -> None:
    """A creation waiting for the instance's lock (another job on the same name) is shown with its queued job."""
    client, _ = admin
    with jobs.INSTANCE_LOCKS[NAME]:
        job = _start(lambda job: None)
        found = client.get(f"/api/v1/instances/{NAME}").get_json()["data"]
        assert found["status"] == "creating"
        assert found["current_job"]["status"] == "queued" and found["current_job"]["steps"] == []
    _wait(job, ("success", "failed"))


def test_failed_creation_answers_404_and_keeps_its_job(admin: Client) -> None:
    client, _ = admin

    def work(job: jobs.Job) -> None:
        job.step("Downloading Odoo 18.0 image")
        job.write("Error response from daemon: manifest for odoo:18.0 not found\n")
        raise JobError("command failed: docker pull odoo:18.0")

    job = _start(work)
    _wait(job, ("failed",))
    assert client.get(f"/api/v1/instances/{NAME}").status_code == 404
    assert NAME not in [i["name"] for i in client.get("/api/v1/instances").get_json()["data"]]
    kept = client.get(f"/api/v1/jobs/{job.id}").get_json()["data"]
    assert kept["status"] == "failed" and kept["instance"] == NAME and kept["error"]
    assert kept["steps"] == [{"title": "Downloading Odoo 18.0 image", "status": "failed"}]


def test_creating_instance_with_files_keeps_status_creating(admin: Client) -> None:
    """Once its files exist, the real resource is shown, still with status "creating" until the job ends."""
    client, _ = admin
    release = threading.Event()

    def work(job: jobs.Job) -> None:
        folder = store.folder(NAME)
        (folder / "addons").mkdir(parents=True)
        (folder / "docker-compose.yml").write_text("services: {}\n")
        store.write_env(
            NAME,
            {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": "being_made", "GIT_BRANCH": "",
             "SOURCE": "plain", "GATEWAY_PORT": 39111, "ODOO_PORT": 39112, "AUTO_UPDATE": "false",
             "AUTO_UPGRADE": "false", "AUTO_PAUSE": "true", "DELETE_AFTER_DAYS": "3", "CREATED_AT": int(time.time())},
        )  # fmt: skip
        job.step("Starting the database")
        release.wait(5)

    job = _start(work)
    try:
        deadline = time.time() + 5
        while not job.steps:
            assert time.time() < deadline
            time.sleep(0.01)
        found = client.get(f"/api/v1/instances/{NAME}").get_json()["data"]
        assert found["status"] == "creating" and found["current_job"]["id"] == job.id
        assert found["current_job"]["steps"][-1]["title"] == "Starting the database"
    finally:
        release.set()
    _wait(job, ("success",))
    assert client.get(f"/api/v1/instances/{NAME}").get_json()["data"]["status"] != "creating"


def test_pull_shows_progress_while_docker_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """docker pull writes nothing while a large layer downloads: the log gets a line on how far it is."""
    script = (
        "echo '17.0: Pulling from library/odoo'; echo 'aaaaaaaaaaaa: Pulling fs layer';"
        " echo 'bbbbbbbbbbbb: Pulling fs layer'; echo 'aaaaaaaaaaaa: Download complete'; sleep 0.5;"
        " echo 'aaaaaaaaaaaa: Pull complete'"
    )
    original = jobs.Job.run_watched

    def fake(self: jobs.Job, cmd: list[str], timeout: float, on_line: Any, on_silence: Any, **_: Any) -> int:
        assert cmd == ["docker", "pull", "odoo:17.0"]
        return original(self, ["sh", "-c", script], timeout, on_line, on_silence, silence_seconds=0.2)

    monkeypatch.setattr(jobs.Job, "run_watched", fake)
    job = jobs.Job(NAME, "Create instance", "create", "admin1")
    integrity.pull_image(job, "odoo:17.0")
    log = "".join(job.log)
    assert "Still downloading odoo:17.0: 1 of 2 layers downloaded, 0 unpacked" in log
    assert log.rstrip().endswith("aaaaaaaaaaaa: Pull complete")


def test_pull_failure_uses_the_image_already_here(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs.Job, "run_watched", lambda self, cmd, timeout, on_line, on_silence: 1)
    job = jobs.Job(NAME, "Create instance", "create", "admin1")
    monkeypatch.setattr(integrity, "present", lambda image: True)
    integrity.pull_image(job, "odoo:17.0")
    assert job.notes and job.notes[0]["level"] == "warning"
    monkeypatch.setattr(integrity, "present", lambda image: False)
    with pytest.raises(JobError):
        integrity.pull_image(job, "odoo:17.0")


def test_prepull_downloads_only_missing_images(monkeypatch: pytest.MonkeyPatch) -> None:
    pulled: list[list[str]] = []
    monkeypatch.setattr(integrity, "present", lambda image: image == "odoo:18.0")
    monkeypatch.setattr(background, "succeeds", lambda cmd, timeout=30: pulled.append(cmd) or True)
    assert background.prepull_images() == []  # off by default
    live.change({"prepull_images": True, "odoo_versions": "17.0,18.0"})
    assert background.prepull_images() == ["odoo:17.0", "postgres:16"]
    assert pulled == [["docker", "pull", "--quiet", "odoo:17.0"], ["docker", "pull", "--quiet", "postgres:16"]]


def test_failed_download_removes_everything_and_keeps_its_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A creation that fails before the instance's .env exists (the image download, the clone) cleans up without
    tripping over the missing ports, and the job reports the download's error, not the cleanup's."""
    from sudoo.instances import operations

    def no_image(job: jobs.Job, version: str) -> None:
        job.step(f"Downloading Odoo {version} image")
        raise JobError(f"command failed: docker pull odoo:{version}")

    monkeypatch.setattr(operations, "_download_images", no_image)
    deleted: list[str] = []
    monkeypatch.setattr(operations.events, "publish", lambda topic, key, data=None: deleted.append(f"{topic} {key}"))
    plan = {**PLAN, "name": NAME, "ports": store.reserve_ports({}), "install_modules": False,
            "demo": False, "from_backup": None}  # fmt: skip
    job = jobs.Job(NAME, "Create instance", "create", "admin1")
    with pytest.raises(JobError, match="docker pull odoo:18.0"):
        operations.create(job, plan)
    assert not store.folder(NAME).exists()
    assert "Could not" not in "".join(job.log)
    assert f"instance.deleted {NAME}" in deleted
