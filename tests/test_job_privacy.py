"""Jobs never show their technical log to the panel's users: the API and the live events carry steps, progress,
notes, the result and "Why it failed" (core/failure.py) only; the log goes to the server's own log. Also: the
progress of an image download, using the images already on the server, the background check for newer Odoo
images, and the e-mail to the person whose push could not be deployed (projects/push_failed.py). Docker is never
run."""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient
from pydantic import ValidationError

from sudoo import background, projects
from sudoo.config import Settings, live
from sudoo.core import failure, jobs
from sudoo.core.jobs import Job, JobError
from sudoo.instances import integrity, operations
from sudoo.projects import push_failed
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
SECRET_URL = "https://x-access-token:ghs_abcdefghijklmnopqrstuvwxyz0123@github.com/acme/erp.git"
SUBMODULE_FAILURE = """
== Downloading the latest code from GitHub
$ git -C /opt/odoo/instances/erp-dev/addons fetch origin
Fetching submodule payments
ERROR: Repository not found.
fatal: Could not read from remote repository.

Please make sure you have the correct access rights
and the repository exists.
fatal: clone of 'git@github.com:acme/payments.git' into submodule path '/opt/odoo/instances/erp-dev/addons/payments' failed
Failed to clone 'payments'. Retry scheduled
"""


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    live.change(dict.fromkeys(live.overrides()))
    for key in list(storage().all(push_failed.COLLECTION)):
        storage().delete(push_failed.COLLECTION, key)
    background._IMAGE_REFRESH["at"] = 0.0
    while _MADE:
        projects.remove(_MADE.pop())


def _wait(job: Job) -> None:
    deadline = time.time() + 5
    while job.status in ("queued", "running"):
        assert time.time() < deadline, job.status
        time.sleep(0.01)


# ---------------------------------------------------------------- the log stays on the server


def test_the_job_api_and_events_never_carry_the_log(admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = admin
    published: list[tuple[str, str, Any]] = []
    original = jobs.events.publish
    monkeypatch.setattr(jobs.events, "publish", lambda topic, key, data=None: published.append((topic, key, data))
                        or original(topic, key, data))  # fmt: skip

    def work(job: Job) -> None:
        job.step("Downloading the code from GitHub")
        job.write(f"cloning {SECRET_URL}\n$ docker compose run odoo\nsecret output\n")
        raise JobError("command failed: git clone")

    job = jobs.start("privacy-box", "Get latest code", "update", work, user="admin1")
    _wait(job)
    assert not any(topic == "job.output" for topic, _, _ in published)
    found = client.get(f"/api/v1/jobs/{job.id}?include=log").get_json()["data"]
    text = str(found)
    assert "log" not in found and "secret output" not in text and "docker" not in text and "ghs_" not in text
    assert found["failure"]["step"] == "Downloading the code from GitHub"
    assert client.get(f"/api/v1/jobs/{job.id}/log").status_code == 404
    listed = client.get("/api/v1/jobs?instance=privacy-box").get_json()["data"]
    assert "secret output" not in str(listed)


def test_the_server_log_receives_every_line_with_the_job_and_instance() -> None:
    seen: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    handler = Keep()
    jobs.SERVER_LOG.addHandler(handler)
    try:
        job = Job("server-log-box", "Restart", "restart", "admin1")
        job.mask("sub-code-123")
        job.write(f"first line\nusing {SECRET_URL} and sub-code-123\n\n")
    finally:
        jobs.SERVER_LOG.removeHandler(handler)
    assert seen[0] == f"job {job.id} server-log-box: first line"
    assert seen[1].startswith(f"job {job.id} server-log-box: using https://***@github.com")
    assert "sub-code-123" not in seen[1] and "ghs_" not in seen[1] and len(seen) == 2
    assert "".join(job.log).startswith("first line")  # kept in memory for the panel's own use


def test_a_failure_without_a_known_cause_gets_the_general_hint() -> None:
    job = jobs.start("privacy-box", "Restart", "restart", lambda job: (job.step("Restarting Odoo"),
                     job.write("something odd\n"), (_ for _ in ()).throw(JobError("command failed: x"))))  # fmt: skip
    _wait(job)
    assert (
        job.hint
        == "Something went wrong in this step. The server's log has the details; your administrator can read it."
    )
    assert job.failure is not None and job.failure["kind"] == "other" and job.failure["details"] == []


# ---------------------------------------------------------------- progress


def test_progress_text_of_an_image_download(monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job("pull-box", "Create instance", "create", "admin1")
    job.step("Downloading Odoo 19.0, first time on this server")
    shown: list[tuple[str | None, int | None]] = []
    monkeypatch.setattr(jobs, "_PROGRESS_EVERY_SECONDS", 0)
    monkeypatch.setattr(
        job, "changed", lambda: shown.append((job.steps[-1].get("progress"), job.steps[-1].get("percent")))
    )

    def fake(cmd: list[str], timeout: float, on_line: Callable[[str], None], on_silence: Callable[[], None]) -> int:
        on_silence()
        for line in ("19.0: Pulling from library/odoo", "aaaaaaaaaaaa: Pulling fs layer",
                     "bbbbbbbbbbbb: Pulling fs layer", "aaaaaaaaaaaa: Download complete",
                     "aaaaaaaaaaaa: Pull complete", "bbbbbbbbbbbb: Download complete"):  # fmt: skip
            on_line(line + "\n")
        return 0

    monkeypatch.setattr(job, "run_watched", fake)
    integrity.pull_image(job, "odoo:19.0")
    texts = [text for text, _ in shown]
    assert texts[0] == "Downloading Odoo 19.0"
    assert "Downloading Odoo 19.0: 0 of 2 parts" in texts and "Downloading Odoo 19.0: 1 of 2 parts" in texts
    assert shown[-1] == ("Unpacking Odoo 19.0: 1 of 2 parts", 75)
    assert not any("layer" in (text or "") or "docker" in (text or "") for text in texts)
    assert integrity.image_label("postgres:16") == "PostgreSQL 16"
    assert integrity.pull_progress("Odoo 18.0", {}) == ("Downloading Odoo 18.0", None)


def test_odoo_module_progress_from_its_log() -> None:
    job = Job("pull-box", "Upgrade modules", "upgrade", "admin1")
    job.step("Upgrading the modules")
    job._odoo_progress("2026-11-22 10:00:00,123 7 INFO db odoo.modules.loading: loading sale_custom/views/a.xml\n")
    assert job.steps[-1]["progress"] == "Setting up the module sale_custom"


# ---------------------------------------------------------------- "Why it failed"


def test_a_submodule_without_access_names_the_repository() -> None:
    found = failure.extract(SUBMODULE_FAILURE)
    assert found["kind"] == "repository" and found["repository"] == "acme/payments"
    assert "ERROR: Repository not found." in found["details"]
    assert all("/opt/" not in line and not line.startswith("$") for line in found["details"])
    assert any("submodule path 'payments'" in line for line in found["details"])


def test_odoo_errors_keep_the_traceback_tail_without_server_paths() -> None:
    log = "\n".join([
        "== Upgrading the modules",
        "$ docker compose run --rm odoo odoo -u sale_custom",
        "2026-11-22 10:00:00,123 7 INFO db odoo.modules.loading: loading sale_custom/views/a.xml",
        "2026-11-22 10:00:01,123 7 ERROR db odoo.modules.registry: Failed to load registry",
        "Traceback (most recent call last):",
        '  File "/usr/lib/python3/dist-packages/odoo/modules/registry.py", line 90, in new',
        '  File "/mnt/extra-addons/sale_custom/models/order.py", line 12, in <module>',
        "ValueError: bad field on 10.0.0.5",
    ])  # fmt: skip
    found = failure.extract(log)
    assert found["kind"] == "odoo"
    assert found["details"][0] == "ERROR odoo.modules.registry: Failed to load registry"
    assert '  File "odoo/modules/registry.py", line 90, in new' in found["details"]
    assert '  File "sale_custom/models/order.py", line 12, in <module>' in found["details"]
    assert found["details"][-1] == "ValueError: bad field on <address>"


def test_pip_errors_and_secrets_are_cleaned() -> None:
    log = (
        "== Installing the Python requirements\nCollecting nosuchpkg==9.9\n"
        "ERROR: Could not find a version that satisfies the requirement nosuchpkg==9.9\n"
        "ERROR: No matching distribution found for nosuchpkg==9.9\n"
        f"token ghp_{'a' * 36} used\n"
    )
    found = failure.extract(log)
    assert found["kind"] == "requirements" and len(found["details"]) == 3
    assert (
        failure.clean(f"see ghp_{'a' * 36} and {SECRET_URL}") == "see ******** and https://***@github.com/acme/erp.git"
    )
    assert len(failure.extract("== x\n" + "ERROR: No matching distribution found for y\n" * 200)["details"]) <= 60


def test_failing_tests_are_the_details() -> None:
    tests = {"status": "failed", "messages": ["FAIL: TestOrder.test_total", "AssertionError: 3 != 4"]}
    found = failure.describe("Running the tests", "At least one test failed", "", tests)
    assert found["kind"] == "tests" and found["details"] == tests["messages"] and found["step"] == "Running the tests"


# ---------------------------------------------------------------- images: the copy on the server, the background check


def test_creation_uses_an_image_already_on_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    pulled: list[str] = []
    monkeypatch.setattr(integrity, "present", lambda image: True)
    monkeypatch.setattr(integrity, "pull_image", lambda job, image, timeout=1800: pulled.append(image))
    job = Job("img-box", "Create instance", "create", "admin1")
    operations._download_images(job, "19.0")
    assert pulled == [] and job.steps[-1]["title"] == "Using the Odoo 19.0 image on this server"


def test_creation_downloads_a_missing_image(monkeypatch: pytest.MonkeyPatch) -> None:
    pulled: list[str] = []
    monkeypatch.setattr(integrity, "present", lambda image: image != "odoo:19.0")
    monkeypatch.setattr(integrity, "pull_image", lambda job, image, timeout=1800: pulled.append(image))
    job = Job("img-box", "Create instance", "create", "admin1")
    operations._download_images(job, "19.0")
    assert pulled == ["odoo:19.0"] and job.steps[-1]["title"] == "Downloading Odoo 19.0, first time on this server"


def test_background_check_pulls_each_version_in_use_once_per_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    pulled: list[list[str]] = []
    digests = {"odoo:18.0": "odoo:18.0@sha256:1", "odoo:20.0": "odoo:20.0@sha256:2"}
    monkeypatch.setattr(background.store, "names", lambda: ["a", "b", "c"])
    versions = {"a": "18.0", "b": "18.0", "c": "17.0"}
    monkeypatch.setattr(background.store, "read_env", lambda name: {"ODOO_VERSION": versions[name]})
    monkeypatch.setattr(integrity, "local_pinned", lambda image: digests.get(image))

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        pulled.append(cmd)
        if cmd[-1] == "odoo:20.0":
            digests["odoo:20.0"] = "odoo:20.0@sha256:new"
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(background.subprocess, "run", run)
    # in use: 17.0 and 18.0; the default for new instances: the newest offered (20.0)
    assert background.refresh_odoo_images(now=1_000_000) == ["odoo:20.0"]
    assert [cmd[-1] for cmd in pulled] == ["odoo:17.0", "odoo:18.0", "odoo:20.0"]
    assert background.refresh_odoo_images(now=1_000_000 + 3600) == []  # within ODOO_IMAGE_CHECK_HOURS (24)
    assert len(pulled) == 3
    live.change({"odoo_image_check_hours": 1})
    background.refresh_odoo_images(now=1_000_000 + 3601)
    assert len(pulled) == 6


def test_background_check_waits_while_a_job_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(integrity, "pulling", lambda: True)
    monkeypatch.setattr(background.subprocess, "run", lambda *a, **k: pytest.fail("pulled while a job downloads"))
    assert background.refresh_odoo_images(now=2_000_000) == []
    assert background._IMAGE_REFRESH["at"] == 0.0  # tried again at the next round


def test_the_image_check_interval_is_validated() -> None:
    assert Settings(odoo_image_check_hours=1).odoo_image_check_hours == 1
    for wrong in (0, 169):
        with pytest.raises(ValidationError):
            Settings(odoo_image_check_hours=wrong)


def test_a_newer_local_image_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(integrity, "record", lambda name: {"inputs": {"ODOO_IMAGE_REF": "odoo:19.0@sha256:old"}})
    monkeypatch.setattr(integrity, "local_pinned", lambda image: "odoo:19.0@sha256:new")
    assert integrity.newer_odoo_image("img-box") is True
    monkeypatch.setattr(integrity, "local_pinned", lambda image: "odoo:19.0@sha256:old")
    assert integrity.newer_odoo_image("img-box") is False


# ---------------------------------------------------------------- the e-mail to who pushed


_MADE: list[str] = []


def _project(**options: Any) -> str:
    project = projects.create(repository="Acme/PushMail", odoo_version="18.0", edition="community",
                              origin="linked", user="t")  # fmt: skip
    projects.change(project["id"], lambda p: {**p, "options": options})
    _MADE.append(str(project["id"]))
    return str(project["id"])


def _failed_job(pusher: dict[str, str] | None) -> Job:
    job = Job("erp-dev", "Automatic update", "update", "system")
    job.details = {"build": "push", **({"pusher": pusher} if pusher else {})}
    job.log.append(SUBMODULE_FAILURE)
    job.failure = failure.describe("Downloading the latest code from GitHub", "GitHub says ...", SUBMODULE_FAILURE)
    return job


COMMIT = {"sha": "3f2a91c", "full_sha": "3f2a91c" + "0" * 33, "subject": "Add payments"}


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any]]]:
    from sudoo import mail

    found: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(mail, "send", lambda to, key, **values: found.append((to, key, values)))
    return found


def test_a_submodule_access_failure_is_e_mailed_to_who_pushed(sent: list[Any]) -> None:
    pid = _project()
    job = _failed_job({"name": "Huda", "email": "huda@example.com"})
    assert job.failure is not None
    result = push_failed.notify(job, pid, "feature-a", COMMIT, job.failure)
    assert result["emailed"] is True
    ((to, key, values),) = sent
    assert to == "huda@example.com" and key == "push_failed"
    assert values["branch"] == "feature-a" and values["commit"] == "3f2a91c Add payments"
    assert "acme/payments" in values["what_to_do"] and "GitHub App" in values["what_to_do"]
    assert "Repository not found" in values["details"].text and "/opt/" not in values["details"].text
    assert values["link"].endswith("/projects/" + pid + "/branches/feature-a/history") or values["link"] == ""
    entry = storage().logs(limit=1, instance="erp-dev")[0]
    assert entry["event"] == "pusher e-mailed about a failed deployment" and "huda@example.com" in entry["detail"]
    assert "Repository not found" not in str(entry)  # who, never the text
    # once per branch and commit
    assert push_failed.notify(job, pid, "feature-a", COMMIT, job.failure) == {"emailed": False, "reason": "already"}
    assert len(sent) == 1


def test_noreply_addresses_are_skipped(sent: list[Any]) -> None:
    pid = _project()
    job = _failed_job({"name": "Huda", "email": "123+huda@users.noreply.github.com"})
    assert job.failure is not None
    assert push_failed.notify(job, pid, "feature-a", COMMIT, job.failure) == {"emailed": False,
                                                                              "reason": "no_address"}  # fmt: skip
    assert sent == []


def test_the_option_off_sends_nothing(sent: list[Any]) -> None:
    pid = _project(email_pusher=False)
    job = _failed_job({"name": "Huda", "email": "huda@example.com"})
    assert job.failure is not None
    assert push_failed.notify(job, pid, "feature-a", COMMIT, job.failure)["reason"] == "option_off"
    assert sent == []


def test_who_pushed_comes_from_the_push_payload() -> None:
    from sudoo import triggers

    payload = {"pusher": {"name": "huda", "email": ""}, "head_commit": {"author": {"name": "Huda", "email": "h@x.io"}}}
    assert triggers.pusher_of(payload) == {"name": "Huda", "email": "h@x.io"}
    triggers.note_pusher("erp-dev", {"name": "Huda", "email": "h@x.io"})
    assert triggers._take_pusher("erp-dev") == {"name": "Huda", "email": "h@x.io"}
    assert triggers._take_pusher("erp-dev") is None
