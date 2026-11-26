"""Whose own running-instance limit an instance made by automation counts against: the person linked to the GitHub
account the signed webhook names as sender (when they have access to the project), else the project's creator, else
nobody; never a name or address typed in a commit. The panel-wide limit counts it in every case."""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import time
from collections.abc import Iterator
from typing import Any

import pytest

from sudoo import accounts, projects
from sudoo.auth import github as github_auth
from sudoo.config import live
from sudoo.core import audit, jobs
from sudoo.core.jobs import Job
from sudoo.instances import capacity, metrics, store
from sudoo.projects import automation, charged, previews
from sudoo.repos import github
from sudoo.storage import storage

SECRET = "hook-" + "k" * 30
CREATED: list[str] = []  # the accounts a test made, removed again so later tests see the shared app as they found it


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any, tuple[Any, ...]]]:
    calls: list[tuple[str, Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any) -> Job:
        calls.append((instance, fn, args))
        return Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    monkeypatch.setattr(github, "heads", lambda address: ({"main": "a" * 40, "feature": "b" * 40}, "main", None))
    return calls


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    for name in list(metrics.INSTANCES):
        shutil.rmtree(store.folder(name), ignore_errors=True)
    metrics.INSTANCES.clear()
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    live.change(dict.fromkeys(live.overrides()))
    for name in CREATED:
        storage().delete(accounts.USERS, name)  # not accounts.delete(): that refuses to remove the last super admin
    CREATED.clear()


def _account(role: str = "developer", github_id: int | None = None) -> str:
    name = f"c{time.time_ns() % 10**10}"
    accounts.create(name, role, "orchid-pebble-summit-73", must_change=False)
    CREATED.append(name)
    if github_id is not None:
        github_auth.link(name, github_auth.GitHubUser(id=github_id, login=f"gh{github_id}"))
    return name


def _project(creator: str) -> dict[str, Any]:
    project = projects.create(repository="Acme/Charge", odoo_version="18.0", edition="community", origin="linked",
                              user=creator)  # fmt: skip
    return projects.get(project["id"]) or project


def _running(owner: str, *names: str) -> None:
    for name in names:
        store.folder(name).mkdir(parents=True, exist_ok=True)
        (store.folder(name) / "docker-compose.yml").write_text("services: {}\n")
        store.write_env(name, {"ODOO_VERSION": "18.0", "DB_NAME": name, "ODOO_PORT": "18999", "STAGE": "development",
                               "CREATED_BY": owner})  # fmt: skip
        metrics.INSTANCES[name] = {"running": True}


def test_the_sender_is_read_from_githubs_own_field_only() -> None:
    assert charged.sender_id({"sender": {"id": 77, "login": "x"}}) == 77
    assert charged.sender_id({"sender": {"id": "77"}}) is None  # text is not an id
    assert charged.sender_id({"sender": {"id": True}}) is None
    assert charged.sender_id({"sender": "admin1"}) is None
    assert charged.sender_id({"pusher": {"name": "admin1", "email": "ada@example.com"}}) is None


def test_the_linked_sender_with_access_is_charged() -> None:
    creator, sender = _account("super_admin"), _account("developer", 4101)
    project = _project(creator)
    assert charged.of(project, 4101) == (sender, "sender")


def test_a_commit_name_or_address_never_decides() -> None:
    """The pusher's e-mail and name are typed by the committer: an account with that address is not charged."""
    creator, victim = _account("super_admin"), _account("developer", 4102)
    project = _project(creator)
    assert charged.of(project, None) == (creator, "creator")
    assert charged.of(project, 999999) == (creator, "creator")  # a GitHub account nobody linked
    assert victim != creator


def test_an_account_without_access_to_the_project_is_not_charged(monkeypatch: pytest.MonkeyPatch) -> None:
    creator, outsider = _account("super_admin"), _account("developer", 4103)
    project = _project(creator)
    from sudoo.projects import access

    monkeypatch.setattr(access, "level", lambda user, project, branch=None: "none")
    assert charged.of(project, 4103) == (creator, "creator")
    assert outsider != creator


def test_without_a_creator_nobody_is_charged() -> None:
    project = _project("gone-user")
    assert charged.of(project, None) == ("system", "none")


def test_a_new_branch_counts_against_the_sender_and_the_audit_says_so(started: list[Any],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    from sudoo.limits import quotas

    monkeypatch.setattr(quotas, "claim", lambda user: None)
    creator, sender = _account("super_admin"), _account("developer", 4104)
    project = _project(creator)
    projects.change(project["id"], lambda p: {**p, "seen": ["main"]})
    made = automation.branch_created("acme/charge", "feature", github_id=4104)
    assert made
    plan = started[-1][2][0]
    store.release_ports(plan["ports"])
    assert plan["created_by"] == sender
    assert any(sender in str(e.get("detail")) for e in audit.latest(5))  # the log names whose limit it used


def test_a_sender_at_their_limit_gets_no_instance_and_the_text_names_them(started: list[Any]) -> None:
    creator, sender = _account("super_admin"), _account("developer", 4105)
    project = _project(creator)
    projects.change(project["id"], lambda p: {**p, "seen": ["main"]})
    _running(sender, "c-dev-1", "c-dev-2")
    live.change({"max_running_non_production_per_user": 2})
    refused = automation.creation_refusal(project, "feature", sender)
    assert refused and sender in refused and "counts against them" in refused
    assert automation.branch_created("acme/charge", "feature", github_id=4105) is None
    assert not started


def test_the_creator_is_charged_without_a_known_sender(started: list[Any]) -> None:
    creator = _account("super_admin")
    project = _project(creator)
    _running(creator, "c-dev-3")
    live.change({"max_running_non_production_per_user": 1})
    assert "counts against them" in (automation.creation_refusal(project, "feature", creator) or "")
    assert automation.creation_refusal(project, "feature", "system") is None  # nobody to charge


def test_zero_is_off_for_the_person_but_the_panel_limit_still_holds(started: list[Any]) -> None:
    """0 on the account (or in the default) means no per-person limit; MAX_RUNNING_INSTANCES is separate."""
    creator = _account("super_admin")
    project = _project(creator)
    _running(creator, "c-dev-4", "c-dev-5")
    live.change({"max_running_non_production_per_user": 0})
    assert capacity.user_limit(creator) == 0
    assert automation.creation_refusal(project, "feature", creator) is None
    live.change({"max_running_instances": 2})
    assert "at most 2 instances" in (automation.creation_refusal(project, "feature", creator) or "")


def test_a_preview_counts_against_the_sender(started: list[Any]) -> None:
    creator, sender = _account("super_admin"), _account("developer", 4106)
    project = _project(creator)
    _running(sender, "c-dev-6")
    live.change({"max_running_non_production_per_user": 1})
    assert "counts against them" in (previews.refusal(project, sender) or "")
    assert previews.refusal(project) is None


def test_the_webhooks_pass_only_githubs_sender_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.config import Settings
    from sudoo.triggers.github_webhook import GitHubWebhook

    seen: list[Any] = []
    monkeypatch.setattr(
        automation, "branch_created", lambda repository, branch, user="system", github_id=None: seen.append(github_id)
    )  # noqa: E501
    monkeypatch.setattr(GitHubWebhook, "instances_following", staticmethod(lambda repository, branch: []))
    hook = GitHubWebhook(Settings(github_webhook_secret="s" * 24))
    hook._request_update = lambda name, reason: None
    payload = {"ref": "refs/heads/feature", "created": True, "repository": {"full_name": "acme/charge"},
               "sender": {"id": 4107, "login": "someone"}, "pusher": {"name": "admin1", "email": "ada@example.com"},
               "head_commit": {"author": {"name": "admin1", "email": "ada@example.com"}}}  # fmt: skip
    from flask import Flask

    with Flask(__name__).test_request_context():
        hook.push(payload)
    assert seen == [4107]


# ---------------------------------------------------------------- at the webhook's door (the real route)


@pytest.fixture
def door(started: list[Any]) -> Iterator[Any]:
    from flask import Flask

    from sudoo.config import Settings
    from sudoo.limits import quotas
    from sudoo.triggers.github_webhook import GitHubWebhook

    trigger = GitHubWebhook(Settings(github_webhook_secret=SECRET))
    trigger.start(lambda name, reason: None)
    flask_app = Flask(__name__)
    blueprint = trigger.blueprint()
    assert blueprint is not None
    flask_app.register_blueprint(blueprint)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(quotas, "claim", lambda user: None)
        yield flask_app.test_client()


def _post(client: Any, payload: dict[str, Any], secret: str = SECRET, signed: bool = True) -> Any:
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": "push"}
    if signed:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/hooks/github", data=body, headers=headers, content_type="application/json")


def _push(sender: int | None, commit_email: str = "nobody@example.com") -> dict[str, Any]:
    return {"ref": "refs/heads/feature", "created": True, "repository": {"full_name": "Acme/Charge"},
            "sender": {"id": sender, "login": "x"} if sender else {}, "pusher": {"name": "n", "email": commit_email},
            "head_commit": {"author": {"name": "n", "email": commit_email}}}  # fmt: skip


def test_a_signed_push_charges_the_linked_sender_not_the_commit_address(door: Any, started: list[Any]) -> None:
    creator = _account("super_admin")
    sender = _account("developer", 4201)
    victim = _account("developer", 4202)
    accounts.update(victim, email="victim@example.com")
    project = _project(creator)
    projects.change(project["id"], lambda p: {**p, "seen": ["main"]})
    answer = _post(door, _push(4201, commit_email="victim@example.com"))
    assert answer.status_code == 200 and answer.get_json()["created"]
    plan = started[-1][2][0]
    store.release_ports(plan["ports"])
    assert plan["created_by"] == sender  # not victim, whose address the commit carried


def test_a_bad_or_missing_signature_reaches_nothing(door: Any, started: list[Any]) -> None:
    creator = _account("super_admin")
    _account("developer", 4203)
    project = _project(creator)
    projects.change(project["id"], lambda p: {**p, "seen": ["main"]})
    assert _post(door, _push(4203), secret="z" * 30).status_code == 401
    assert _post(door, _push(4203), signed=False).status_code == 401
    assert not started
    assert "feature" not in (projects.get(project["id"]) or {})["seen"]  # nothing was even remembered


def test_a_replayed_delivery_creates_one_instance(door: Any, started: list[Any]) -> None:
    creator = _account("super_admin")
    _account("developer", 4204)
    project = _project(creator)
    projects.change(project["id"], lambda p: {**p, "seen": ["main"]})
    first = _post(door, _push(4204))
    again = _post(door, _push(4204))
    assert first.get_json()["created"] and again.get_json()["created"] is None
    store.release_ports(started[-1][2][0]["ports"])
    assert len(started) == 1


def test_an_unlinked_or_forged_sender_id_falls_back_to_the_creator(door: Any, started: list[Any]) -> None:
    creator = _account("super_admin")
    project = _project(creator)
    projects.change(project["id"], lambda p: {**p, "seen": ["main"]})
    assert _post(door, _push(999001)).get_json()["created"]  # signed, but nobody linked that GitHub id
    plan = started[-1][2][0]
    store.release_ports(plan["ports"])
    assert plan["created_by"] == creator


def test_a_disabled_or_deleted_creator_is_never_charged() -> None:
    creator = _account("developer")  # a super admin could not be deleted below when no other one exists
    project = _project(creator)
    accounts.update(creator, disabled=True)
    assert charged.of(project, None) == ("system", "none")  # disabled: nobody
    accounts.update(creator, disabled=None)
    assert charged.of(project, None) == (creator, "creator")
    accounts.delete(creator)
    assert charged.of(project, None) == ("system", "none")  # deleted: nobody


def test_a_disabled_linked_sender_is_not_charged() -> None:
    creator, sender = _account("super_admin"), _account("developer", 4205)
    project = _project(creator)
    accounts.update(sender, disabled=True)
    assert charged.of(project, 4205) == (creator, "creator")


def test_a_pull_request_passes_only_githubs_sender_id(monkeypatch: pytest.MonkeyPatch) -> None:
    creator = _account("super_admin")
    _project(creator)
    seen: list[Any] = []

    def opened(project: Any, pull: Any, action: str = "opened", github_id: int | None = None) -> dict[str, Any]:
        seen.append(github_id)
        return {}

    monkeypatch.setattr(previews, "opened", opened)
    payload = {"action": "opened", "repository": {"full_name": "Acme/Charge"}, "pull_request": {"number": 1, "user": {"id": 5}},
               "sender": {"id": 4302}}  # fmt: skip
    previews.pull_request(payload)
    assert seen == [4302]  # the pull request's author field is not what decides


# ---------------------------------------------------------------- a visit to a paused instance


def test_a_visit_starts_a_paused_instance_only_when_the_limits_allow(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    from sudoo.instances import gateway, stages

    owner = _account("developer")
    _running(owner, "v-dev-1")
    write = _running
    write(owner, "v-dev-2")
    metrics.INSTANCES.pop("v-dev-2")  # paused: not running
    gateway.STATUS.pop("v-dev-2", None)

    def forget() -> None:  # `clean` only knows the instances still in metrics.INSTANCES, and this one left them
        shutil.rmtree(store.folder("v-dev-2"), ignore_errors=True)
        gateway.STATUS.pop("v-dev-2", None)

    request.addfinalizer(forget)
    started_jobs: list[str] = []

    def fake_wake(name: str) -> Job:
        started_jobs.append(name)
        return Job(name, "Start", "wake", "visitor")

    monkeypatch.setattr(gateway, "_wake", fake_wake)
    monkeypatch.setattr(gateway, "_WAKE_REQUESTED", {})
    live.change({"max_running_non_production_per_user": 1})
    assert gateway.wake_decision("v-dev-2")[0] == "full" and started_jobs == []  # the person is at their limit
    live.change({"max_running_non_production_per_user": 2})
    gateway._WAKE_REQUESTED.clear()
    assert gateway.wake_decision("v-dev-2")[0] == "waking" and started_jobs == ["v-dev-2"]
    live.change({"max_running_non_production_per_user": 0, "max_running_instances": 1})
    started_jobs.clear()
    gateway._WAKE_REQUESTED.clear()
    assert gateway.wake_decision("v-dev-2")[0] == "full" and started_jobs == []  # the panel cap
    assert stages.of(store.read_env("v-dev-2")) == "development"


def test_a_push_or_pull_request_of_a_disabled_person_creates_no_instance(started: list[Any]) -> None:
    creator, sender = _account("super_admin"), _account("developer", 4206)
    project = _project(creator)
    projects.change(project["id"], lambda p: {**p, "seen": ["main"]})
    accounts.update(sender, disabled=True)
    assert charged.disabled_sender(4206) and sender not in (charged.disabled_sender(4206) or "")
    assert charged.disabled_sender(None) is None and charged.disabled_sender(999999) is None
    assert automation.branch_created("acme/charge", "feature", github_id=4206) is None  # not charged to the creator
    assert not started
    assert any(e["event"] == "no instance for a new branch" for e in audit.latest(5))
    # enabled again: the same kind of push creates one for the next branch (the first branch is already handled)
    accounts.update(sender, disabled=None)
    assert charged.disabled_sender(4206) is None
