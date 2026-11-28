"""The panel's own records under changes at the same moment (RACE-P-004, 008, 009, 010, 011, 013, 014, 015, 016):
a plugin disabled during its update stays disabled and is not reported as changed by hand, a project deleted while
an instance of it is being created leaves no orphan, a maintenance window someone chose is not replaced by the
default, a webhook's new signing secret is kept, a confirmed client action is not recorded as cancelled, the client
limit holds, a confirmation link works once, and a pusher gets one e-mail per failed commit.

Each test makes the two changes meet where they used to overwrite each other: one waits at the point after its read
until the other is done (or, once the read and the write are one step, until a short time has passed)."""

from __future__ import annotations

import contextlib
import io
import shutil
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

import fake_hub
from sudoo import access_requests, accounts, maintenance, notifications, plugin_manager, projects
from sudoo.config import live, settings
from sudoo.core import jobs
from sudoo.core.failure import describe
from sudoo.core.jobs import Job
from sudoo.instances import operations
from sudoo.limits import quotas
from sudoo.projects import client_confirm, clients, push_failed
from sudoo.repos import github
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]


def _meanwhile(other: Callable[[], Any], wait: float = 10) -> list[Any]:
    """Run `other` in a thread and wait for it (at most `wait` seconds: once the change it races with is one step,
    it waits for that step instead, and finishes after it); its result, when it finished in time."""
    result: list[Any] = []
    thread = threading.Thread(target=lambda: result.append(other()), daemon=True)
    thread.start()
    thread.join(wait)
    _LATE.append(thread)
    return result


_LATE: list[threading.Thread] = []


@pytest.fixture(autouse=True)
def join_late() -> Iterator[None]:
    yield
    for thread in _LATE:
        thread.join(20)
    _LATE.clear()


# ---------------------------------------------------------------- RACE-P-004 and RACE-P-011


@pytest.fixture
def plugin() -> Iterator[str]:
    plugin_id = f"race.hello{uuid.uuid4().hex[:6]}"
    yield plugin_id
    storage().delete(plugin_manager.COLLECTION, plugin_id)
    shutil.rmtree(plugin_manager.plugins_dir() / plugin_id, ignore_errors=True)


def _unpack(plugin_id: str, version: str) -> None:
    data = fake_hub.make_package(plugin_id, version, module=f"m{plugin_id.split('.')[-1]}")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = plugin_manager.json.loads(archive.read("manifest.json"))
        plugin_manager._unpack(Job(plugin_manager.JOB_INSTANCE, "Install", "plugin", "t"), archive, manifest, {}, "t")


def test_a_plugin_disabled_while_it_is_updated_stays_disabled(plugin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _unpack(plugin, "1.0.0")
    real_hash = plugin_manager.tree_hash
    disabled: list[Any] = []

    def disabled_meanwhile(root: Any) -> str:
        if not disabled:
            disabled.extend(_meanwhile(lambda: plugin_manager._update(plugin, enabled=False), wait=0.5) or [True])
        return real_hash(root)

    monkeypatch.setattr(plugin_manager, "tree_hash", disabled_meanwhile)
    _unpack(plugin, "1.1.0")
    monkeypatch.setattr(plugin_manager, "tree_hash", real_hash)
    for thread in _LATE:
        thread.join(10)
    found = plugin_manager.record(plugin) or {}
    assert found["version"] == "1.1.0"
    assert found["enabled"] is False  # the administrator disabled it: the update keeps that


def test_the_watchdog_does_not_report_files_changed_by_an_update_running_now(
    plugin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The watchdog read the records, then an update replaced the plugin's folder and record: it checked the old
    record against the new folder (or none) and switched the freshly updated plugin off as changed by hand."""
    _unpack(plugin, "1.0.0")
    stale = {plugin: plugin_manager.record(plugin) or {}}
    monkeypatch.setattr(plugin_manager, "records", lambda: stale)
    monkeypatch.setattr(plugin_manager, "LOADED", {plugin})
    monkeypatch.setattr(plugin_manager, "_mail_super_admins", lambda *a: None)
    updating, finish = threading.Event(), threading.Event()

    def update_job() -> None:  # what a running update holds while it swaps the folder
        with jobs.INSTANCE_LOCKS[plugin_manager.JOB_INSTANCE]:
            _unpack(plugin, "1.1.0")  # v1.0.0's folder is gone, the record says 1.1.0
            updating.set()
            finish.wait(10)

    thread = threading.Thread(target=update_job)
    thread.start()
    updating.wait(10)
    try:
        plugin_manager.watch(start_job=lambda *a, **k: None)
    finally:
        finish.set()
        thread.join(10)
    assert not (plugin_manager.record(plugin) or {}).get("tampered")


# ---------------------------------------------------------------- RACE-P-008


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    found = projects.create(repository=f"Acme/Race{uuid.uuid4().hex[:6]}", odoo_version="18.0", edition="community",
                            origin="linked", user="t")  # fmt: skip
    monkeypatch.setattr(github, "heads", lambda address: ({"main": "a" * 40, "feature": "b" * 40}, "main", None))
    yield str(found["id"])
    if projects.get(found["id"]) is not None:
        projects.remove(found["id"])


def test_a_project_deleted_while_an_instance_of_it_is_being_created_leaves_no_orphan(
    admin: Client, project: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = admin
    release = threading.Event()
    monkeypatch.setattr(operations, "create", lambda job, plan: release.wait(20))
    monkeypatch.setattr(quotas, "claim", lambda user: None)
    real_members = projects.instances
    created: list[Any] = []

    def created_meanwhile(project_id: str) -> list[str]:
        members = real_members(project_id)
        if not created:
            created.extend(_meanwhile(lambda: client.post(f"/api/v1/projects/{project}/branches/feature/instances",
                                                          json={}, headers=headers), wait=1) or [None])  # fmt: skip
        return members

    monkeypatch.setattr(projects, "instances", created_meanwhile)
    try:
        deleted = client.delete(f"/api/v1/projects/{project}", json={"confirm": project}, headers=headers)
        for thread in _LATE:
            thread.join(20)
        orphans = [name for name in jobs.being_created() if (jobs.creating(name) or Job("", "", "", "")).details.get(
            "project") == project]  # fmt: skip
        if deleted.status_code == 204:
            assert projects.get(project) is None and not orphans, orphans
        else:
            assert projects.get(project) is not None
    finally:
        release.set()
        for _ in range(100):
            if not jobs.being_created():
                break
            time.sleep(0.05)


# ---------------------------------------------------------------- RACE-P-009


@pytest.fixture
def item(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    import sudoo.mail

    class Ready:
        ready = True

    monkeypatch.setattr(sudoo.mail, "mailer", lambda: Ready())
    monkeypatch.setattr(sudoo.mail, "send", lambda to, key, **values: None)
    live.change({"timezone": "UTC"})
    now = int(time.time())
    definition = {
        "maintenance_id": str(uuid.uuid4()), "kind": "restart", "scope": "panel", "targets": [], "version": None,
        "reason": {"en": "A planned restart.", "ar": "إعادة تشغيل."}, "estimated_minutes": 30,
        "earliest_start": now + 3600, "deadline": now + 7 * 86400, "required": True,
    }  # fmt: skip
    assert maintenance.receive("cmd-" + definition["maintenance_id"][:8], definition) == ("done", None)
    found = maintenance.get(definition["maintenance_id"]) or {}
    yield found
    storage().delete(maintenance.COLLECTION, found["id"])
    live.change({"timezone": None})


def test_a_window_chosen_while_the_default_is_applied_is_kept(
    item: dict[str, Any], passwords: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    later = item["received_at"] + settings.maintenance_choose_hours * 3600 + 1
    chosen = maintenance.bounds(item, later)[0] + 7200
    real_default = maintenance.default_start
    picked: list[Any] = []

    def chosen_meanwhile(doc: dict[str, Any], now: float) -> float | None:
        if not picked:
            picked.extend(_meanwhile(lambda: maintenance.choose(item["id"], chosen, "admin1", now=later)) or [None])
        return real_default(doc, now)

    monkeypatch.setattr(maintenance, "default_start", chosen_meanwhile)
    maintenance.tick(now=later)
    found = maintenance.get(item["id"]) or {}
    assert found["window_source"] == "panel" and found["window_start"] == round(chosen), found


# ---------------------------------------------------------------- RACE-P-010


@pytest.fixture
def channel() -> Iterator[dict[str, Any]]:
    channel_id = uuid.uuid4().hex[:12]
    doc = {"id": channel_id, "project": "race", "type": "webhook", "name": "Hook", "events": ["build.failed"],
           "enabled": True, "config": {}, "secrets": {"url": "sealed-url", "signing_secret": "sealed-old"},
           "hints": {"url": "…/x", "signing_secret": "…old"}, "created_at": 1, "created_by": "t", "last": None}  # fmt: skip
    storage().put(notifications.COLLECTION, channel_id, doc)
    yield doc
    storage().delete(notifications.COLLECTION, channel_id)


def test_a_rename_in_flight_does_not_put_back_the_old_signing_secret(channel: dict[str, Any]) -> None:
    """The rename read the channel; the signing secret was renewed meanwhile; the rename wrote its copy back."""
    renewed = _meanwhile(lambda: notifications.new_signing_secret(channel))
    assert renewed
    notifications.change(channel, {"name": "Renamed"})
    found = notifications.get(channel["id"]) or {}
    assert found["name"] == "Renamed"
    assert found["secrets"]["signing_secret"] == renewed[0][0]["secrets"]["signing_secret"]


def test_a_change_in_flight_does_not_bring_back_a_deleted_channel(channel: dict[str, Any]) -> None:
    _meanwhile(lambda: notifications.remove(channel["id"]))
    with pytest.raises(notifications.ChannelError):
        notifications.change(channel, {"name": "Renamed"})
    with pytest.raises(notifications.ChannelError):
        notifications.new_signing_secret(channel)
    assert notifications.get(channel["id"]) is None


# ---------------------------------------------------------------- RACE-P-013


def test_a_confirmed_client_action_is_not_recorded_as_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client confirmed by the e-mail's link while the same confirmation was cancelled in the portal: the action
    ran, and the cancellation then wrote "cancelled" over "confirmed"."""
    confirmation_id, secret = uuid.uuid4().hex[:12], "s" * 32
    storage().put(client_confirm.COLLECTION, confirmation_id, {
        "id": confirmation_id, "project": "race", "client": "racer", "sid": "sid-1", "kind": "open",
        "action": {}, "summary": "Open access", "created": time.time(), "expires": time.time() + 600,
        "secret_hash": client_confirm._digest(secret), "status": "pending",
    })  # fmt: skip
    real_end = client_confirm._end
    confirmed: list[Any] = []

    def confirmed_meanwhile(found: Any, status: str) -> None:
        if not confirmed:
            confirmed.extend(_meanwhile(
                lambda: client_confirm.confirm(confirmation_id, "racer", "sid-1", token=secret)) or [None])  # fmt: skip
        real_end(found, status)

    monkeypatch.setattr(client_confirm, "_end", confirmed_meanwhile)
    with contextlib.suppress(client_confirm.ConfirmError):  # refused when the confirmation won: that is right too
        client_confirm.cancel(confirmation_id, "racer")
    assert confirmed and confirmed[0] is not None  # the action ran
    assert (storage().get(client_confirm.COLLECTION, confirmation_id) or {})["status"] == "confirmed"
    storage().delete(client_confirm.COLLECTION, confirmation_id)


# ---------------------------------------------------------------- RACE-P-014


def test_clients_added_at_the_same_moment_stay_within_the_limit(project: str, monkeypatch: pytest.MonkeyPatch) -> None:
    names = [f"client{uuid.uuid4().hex[:6]}" for _ in range(2)]
    for name in names:
        accounts.create(name, "client", "pebble-harbor-lantern-19")
    monkeypatch.setattr(clients, "MAX_CLIENTS", 1)
    real_entries = clients.entries
    added: list[Any] = []

    def added_meanwhile(found: Any) -> list[dict[str, Any]]:
        listed = real_entries(found)
        if not added and threading.current_thread() is threading.main_thread():
            added.extend(_meanwhile(lambda: _add(names[1]), wait=0.5) or [None])
        return listed

    def _add(name: str) -> str:
        try:
            clients.add_client(project, name, "admin1")
            return "added"
        except clients.ClientError:
            return "refused"

    monkeypatch.setattr(clients, "entries", added_meanwhile)
    first = _add(names[0])
    monkeypatch.setattr(clients, "entries", real_entries)
    for thread in _LATE:
        thread.join(10)
    assert len(clients.entries(projects.get(project))) == 1, (first, added)
    for name in names:
        accounts.delete(name)


# ---------------------------------------------------------------- RACE-P-015


def test_a_confirmation_link_of_an_access_request_works_once(monkeypatch: pytest.MonkeyPatch) -> None:
    request_id, secret = uuid.uuid4().hex[:12], "t" * 32
    storage().put(access_requests.COLLECTION, request_id, {
        "email": "jo@example.org", "first_name": "Jo", "last_name": "", "status": "unconfirmed",
        "created": time.time(), "secret_hash": access_requests._digest(secret),
    })  # fmt: skip
    real_digest = access_requests._digest

    def slow(value: str) -> str:
        time.sleep(0.05)  # the moment between reading the request and writing it
        return real_digest(value)

    monkeypatch.setattr(access_requests, "_digest", slow)
    gate = threading.Barrier(4)
    results: list[Any] = []

    def confirm() -> None:
        gate.wait()
        results.append(access_requests.confirm(f"{request_id}.{secret}"))

    threads = [threading.Thread(target=confirm) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len([r for r in results if r is not None]) == 1, results  # one administrators' notice, not four
    storage().delete(access_requests.COLLECTION, request_id)


def test_access_requests_sent_at_once_for_one_address_make_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    live.change({"access_request_domains": "example.org"})
    access_requests.BY_ADDRESS.clear()
    access_requests.BY_CLIENT.clear()
    address = f"ann{uuid.uuid4().hex[:6]}@example.org"
    real_open = access_requests._open_for

    def slow(email: str) -> str | None:
        found = real_open(email)
        time.sleep(0.05)
        return found

    monkeypatch.setattr(access_requests, "_open_for", slow)
    gate = threading.Barrier(3)

    def submit(n: int) -> None:
        gate.wait()
        access_requests.submit(address, "Ann", "", f"10.0.0.{n}")

    threads = [threading.Thread(target=submit, args=(n,)) for n in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    made = [key for key, found in access_requests.all_requests().items() if found.get("email") == address]
    for key in made:
        storage().delete(access_requests.COLLECTION, key)
    live.change({"access_request_domains": None})
    assert len(made) == 1, made


# ---------------------------------------------------------------- RACE-P-016


def test_a_pusher_gets_one_e_mail_when_two_instances_of_the_branch_fail_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sudoo import mail

    found = projects.create(repository=f"Acme/Push{uuid.uuid4().hex[:6]}", odoo_version="18.0", edition="community",
                            origin="linked", user="t")  # fmt: skip
    pid = str(found["id"])
    projects.change(pid, lambda p: {**p, "options": {"email_pusher": True}})
    sent: list[str] = []

    def slow_send(to: str, key: str, **values: Any) -> None:
        time.sleep(0.1)  # a mail server takes a moment
        sent.append(to)

    monkeypatch.setattr(mail, "send", slow_send)
    commit = {"sha": "9e1f2aa", "full_sha": "9e1f2aa" + uuid.uuid4().hex[:33], "subject": "Fix"}

    def fail(n: int) -> dict[str, Any]:
        job = Job(f"erp-dev-{n}", "Automatic update", "update", "system")
        job.details = {"build": "push", "pusher": {"name": "Huda", "email": "huda@example.com"}}
        job.failure = describe("Updating", "It failed.", "error")
        return push_failed.notify(job, pid, "feature-a", commit, job.failure)

    gate = threading.Barrier(3)
    results: list[Any] = []

    def run(n: int) -> None:
        gate.wait()
        results.append(fail(n))

    threads = [threading.Thread(target=run, args=(n,)) for n in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    projects.remove(pid)
    assert sent == ["huda@example.com"], results


# ---------------------------------------------------------------- RACE-P-006 (a branch deleted, reported twice)


def test_a_branch_deleted_reported_twice_archives_its_instance_once(
    project: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The GitHub App sends a push (deleted) and a delete event for one deleted branch: the second report found the
    instance not busy (the first one's job was queued, not yet running), so it was archived twice (two backups)."""
    from sudoo.instances import store
    from sudoo.projects import automation

    name = f"{project}-gone"
    store.folder(name).mkdir(parents=True, exist_ok=True)
    (store.folder(name) / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(name, {"ODOO_VERSION": "18.0", "DB_NAME": name.replace("-", "_"), "STAGE": "development",
                           "GIT_BRANCH": "feature", "PROJECT": project, "CREATED_BY": "t"})  # fmt: skip
    projects.change(project, lambda p: {**p, "options": {"branch_deleted": "archive"}})
    started: list[str] = []

    def queued(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any) -> Job:
        job = Job(instance, action, kind, user)  # registered and queued: its thread has not taken the lock yet
        with jobs._JOBS_LOCK:
            jobs.JOBS[job.id] = job
        started.append(job.id)
        return job

    monkeypatch.setattr(jobs, "start", queued)
    repository = str((projects.get(project) or {})["repository"]).lower()
    try:
        automation.branch_deleted(repository, "feature")
        automation.branch_deleted(repository, "feature")
        assert len(started) == 1, started
    finally:
        with jobs._JOBS_LOCK:
            for job_id in started:
                jobs.JOBS.pop(job_id, None)
        shutil.rmtree(store.folder(name), ignore_errors=True)


# ---------------------------------------------------------------- RACE-P-020 to RACE-P-024 (smaller ones)


def test_a_plugin_switched_on_while_it_is_switched_off_for_failing_stays_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plugin reached its failure limit and was being switched off when an administrator switched it on: the
    switch-off was written to the storage after the switch-on, so it stayed off after the next restart."""
    from sudoo import plugin_guard, plugins
    from sudoo.core import resilience

    group, name = plugins.MAILER_GROUP, f"race-{uuid.uuid4().hex[:6]}"
    live.change({"plugin_failure_limit": 1})
    real_report = resilience.report
    enabled: list[Any] = []

    def switched_on_meanwhile(*args: Any, **kwargs: Any) -> None:
        real_report(*args, **kwargs)
        if not enabled:
            enabled.extend(_meanwhile(lambda: plugin_guard.enable(group, name, "admin1"), wait=0.5) or [None])

    monkeypatch.setattr(resilience, "report", switched_on_meanwhile)
    try:
        assert plugin_guard.failed(group, name, "call", RuntimeError("down")) is True
        for thread in _LATE:
            thread.join(10)
        assert plugin_guard.disabled(group, name) is False
        assert storage().get(plugin_guard.COLLECTION, plugin_guard._key(group, name)) is None
    finally:
        live.change({"plugin_failure_limit": None})
        plugin_guard.enable(group, name, "admin1")


def test_alerts_raised_at_the_same_moment_start_one_delivery_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo import alerts

    class SlowEvent(threading.Event):
        def is_set(self) -> bool:
            found = super().is_set()
            time.sleep(0.05)  # the moment between looking and setting
            return found

    started: list[str] = []

    class Counted:
        def __init__(self, target: Any, daemon: bool, name: str) -> None:
            self.name = name

        def start(self) -> None:
            started.append(self.name)

    gate = threading.Barrier(4)

    def raise_one(n: int) -> None:
        gate.wait()
        alerts._deliver_later(f"race-{n}")

    threads = [threading.Thread(target=raise_one, args=(n,)) for n in range(4)]  # made before Thread is replaced
    monkeypatch.setattr(alerts, "_WORKER", SlowEvent())
    monkeypatch.setattr(alerts, "_QUEUE", alerts.queue.Queue())
    monkeypatch.setattr(alerts.threading, "Thread", Counted)
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert started == ["alerts"]


def test_the_hook_address_and_an_installation_recorded_at_once_are_both_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.repos import github_app

    before = github_app._read()
    github_app._write(
        {"id": 1, "slug": "race", "installations": {}, "hook_url": "https://old.example/hooks/github-app"}
    )
    real_write = github_app._write

    def slow_write(data: dict[str, Any]) -> None:
        time.sleep(0.05)
        real_write(data)

    monkeypatch.setattr(github_app, "_write", slow_write)
    try:
        gate = threading.Barrier(2)

        def hook() -> None:
            gate.wait()
            github_app.set_hook_url("https://new.example/hooks/github-app")

        def installed() -> None:
            gate.wait()
            time.sleep(0.01)
            github_app.record_installation({"id": 77, "account": {"login": "acme", "type": "Organization"}})

        threads = [threading.Thread(target=hook), threading.Thread(target=installed)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        found = github_app._read() or {}
        assert found["hook_url"] == "https://new.example/hooks/github-app"
        assert "77" in found["installations"]
    finally:
        monkeypatch.setattr(github_app, "_write", real_write)
        if before is None:
            github_app._file().unlink(missing_ok=True)
        else:
            real_write(before)


def test_a_commit_reported_twice_at_once_is_recorded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.projects import builds

    pid, sha = f"race{uuid.uuid4().hex[:6]}", uuid.uuid4().hex + "abcdefgh"
    real_history = builds.history

    def slow(project_id: str, branch: str) -> list[dict[str, Any]]:
        found = real_history(project_id, branch)
        time.sleep(0.05)
        return found

    monkeypatch.setattr(builds, "history", slow)
    gate = threading.Barrier(3)

    def report() -> None:
        gate.wait()
        builds.commit_recorded(pid, "main", f"{pid}-main", sha)

    threads = [threading.Thread(target=report) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    entries = real_history(pid, "main")
    storage().delete(builds.COLLECTION, f"{pid}:main")
    assert len([e for e in entries if e["type"] == "commit"]) == 1, entries


def test_an_update_that_ends_never_removes_the_request_of_a_later_update(
    item: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two panel updates overlapped: the first one, ending, removed the request file the second one had written,
    so the second update was never applied and failed after its wait."""
    from sudoo.hub import client

    request_file = settings.manager_dir / maintenance.REQUEST_FILE
    monkeypatch.setattr(client, "sudoo_version", lambda: "3.5.0")
    first = {"id": "first-update", "version": "3.5.0", "started_at": time.time()}
    request_file.write_text('{"version": "3.6.0", "maintenance": "second-update"}')  # written by the later one
    maintenance._check_update(first, time.time())
    try:
        assert request_file.exists() and "second-update" in request_file.read_text()
    finally:
        request_file.unlink(missing_ok=True)
