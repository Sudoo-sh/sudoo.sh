"""Task 18c: the API of "Explain with AI": who may do what, and that nothing leaves without a confirmed preview.
Name resolution and the exchange are faked; no network."""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

from sudoo import projects
from sudoo.config import live, settings
from sudoo.core.jobs import Job
from sudoo.explain import service
from sudoo.notifications import guard
from sudoo.projects import builds
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
ENDPOINT = "https://api.models.example/v1/chat/completions"
KEY = "sk-live-APIAPIAPIAPIAPIAPIAPIAPI"


class Exchange:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    def __call__(self, target: Any, method: str, body: bytes, headers: dict[str, str], timeout: float, max_answer: int,
                 strict_size: bool = False) -> tuple[int, bytes]:  # fmt: skip
        self.calls.append(body)
        return 200, json.dumps({"choices": [{"message": {"content": "Check the XML."}}]}).encode()


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[Exchange]:
    for collection in (service.CONFIG, service.PREVIEWS, service.USAGE):
        for key in list(storage().all(collection)):
            storage().delete(collection, key)
    monkeypatch.setattr(settings, "explain_allowed_destinations", "api.models.example:443")
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, type=0, **_: [(socket.AF_INET, type, 6, "", ("93.184.216.34", port))]
    )
    fake = Exchange()
    monkeypatch.setattr(guard, "exchange", fake)
    yield fake
    for collection in (service.CONFIG, service.PREVIEWS, service.USAGE):
        for key in list(storage().all(collection)):
            storage().delete(collection, key)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def build() -> tuple[dict[str, Any], str]:
    project = projects.create(
        repository="Acme/Books", odoo_version="18.0", edition="community", origin="linked", user="t"
    )
    job = Job("ex-dev", "Create instance", "create", "t")
    job.details = {"build": "create", "project": project["id"], "branch": "dev"}
    builds.job_started(job)
    job.step("Starting Odoo")
    job.status, job.finished = "failed", time.time()
    job.failure = {"step": "Starting Odoo", "reason": "r", "kind": "other", "rule": None,
                   "details": ["boom", "password=hunter2hunter2", "end"], "repository": None}  # fmt: skip
    builds.job_ended(job)
    entry = builds.history(project["id"], "dev")[0]
    return project, str(entry["id"])


def _audit() -> str:
    from sudoo.core import audit

    return json.dumps(audit.latest(200), default=str)


def _turn_on(client: FlaskClient, headers: dict[str, str], project_id: str) -> None:
    done = client.put(
        "/api/v1/explain", json={"enabled": True, "endpoint": ENDPOINT, "model": "m-1", "key": KEY}, headers=headers
    )
    assert done.status_code == 200, done.get_json()
    assert (
        client.put(f"/api/v1/projects/{project_id}/explain", json={"enabled": True}, headers=headers).status_code == 200
    )


def test_settings_need_the_administrator_and_never_show_the_key(admin: Client, operator: Client) -> None:
    client, headers = admin
    shown = client.put("/api/v1/explain", json={"endpoint": ENDPOINT, "model": "m-1", "key": KEY}, headers=headers)
    assert shown.status_code == 200
    again = client.get("/api/v1/explain")
    assert KEY not in again.get_data(as_text=True) and again.get_json()["data"]["key_set"] is True
    other, other_headers = operator
    assert other.get("/api/v1/explain").status_code in (401, 403, 404)
    assert other.put("/api/v1/explain", json={"enabled": True}, headers=other_headers).status_code in (401, 403, 404)


def test_the_audit_log_never_holds_the_key(admin: Client) -> None:
    client, headers = admin
    client.put("/api/v1/explain", json={"endpoint": ENDPOINT, "model": "m-1", "key": KEY}, headers=headers)
    assert KEY not in _audit()


def test_an_endpoint_off_the_list_is_refused(admin: Client) -> None:
    client, headers = admin
    done = client.put("/api/v1/explain", json={"endpoint": "https://elsewhere.example/v1"}, headers=headers)
    assert (
        done.status_code == 400 and "EXPLAIN_ALLOWED_DESTINATIONS" in done.get_json()["error"]["message"]
        if "error" in done.get_json()
        else done.status_code == 400
    )


def test_the_whole_flow_and_the_audit_without_text(
    admin: Client, build: tuple[dict[str, Any], str], clean: Exchange
) -> None:
    client, headers = admin
    project, build_id = build
    base = f"/api/v1/projects/{project['id']}/builds/{build_id}/explain"
    assert client.get(base).get_json()["data"] == {"available": False, "reason": "off", "enabled_for_project": False}
    _turn_on(client, headers, project["id"])
    assert client.get(base).get_json()["data"]["available"] is True
    shown = client.post(f"{base}/preview", json={"lang": "en"}, headers=headers).get_json()["data"]
    assert clean.calls == []  # a preview sends nothing
    assert "hunter2hunter2" not in shown["payload"] and "boom" in shown["payload"]
    done = client.post(f"{base}/confirm", json={"preview": shown["id"]}, headers=headers).get_json()["data"]
    assert done["text"] == "Check the XML." and done["generated"] is True
    assert len(clean.calls) == 1 and json.loads(clean.calls[0]) == json.loads(shown["payload"])
    replay = client.post(f"{base}/confirm", json={"preview": shown["id"]}, headers=headers)
    assert replay.status_code == 409 and len(clean.calls) == 1
    logged = _audit()
    assert (
        "AI explanation sent" in logged and "Check the XML" not in logged and "boom" not in logged and KEY not in logged
    )


def test_a_build_of_another_project_or_an_unknown_one_is_not_found(
    admin: Client, build: tuple[dict[str, Any], str]
) -> None:
    client, headers = admin
    project, build_id = build
    _turn_on(client, headers, project["id"])
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/builds/nope/explain/preview", json={}, headers=headers
        ).status_code
        == 404
    )
    assert client.post(
        f"/api/v1/projects/{project['id']}/builds/{build_id}/explain/confirm",
        json={"preview": "0" * 32},
        headers=headers,
    ).status_code in (404, 409)


def test_deleting_the_project_drops_its_opt_in(admin: Client, build: tuple[dict[str, Any], str]) -> None:
    client, headers = admin
    project, _ = build
    _turn_on(client, headers, project["id"])
    assert project["id"] in service.config()["projects"]
    service.forget_project(project["id"])
    assert project["id"] not in service.config()["projects"]


def test_a_client_account_is_refused_everywhere(
    app: Any, passwords: dict[str, str], build: tuple[dict[str, Any], str]
) -> None:
    from conftest import sign_in
    from sudoo import accounts

    accounts.create("cli1", "client", "violet-harbor-engine-93", first_name="C", email="c@example.com")
    accounts.update("cli1", sign_in_methods=["password", "email_code"])
    client, headers = sign_in(app, "cli1", "violet-harbor-engine-93")
    project, build_id = build
    base = f"/api/v1/projects/{project['id']}/builds/{build_id}/explain"
    assert client.get("/api/v1/explain").status_code == 403
    assert client.get(base).status_code == 403
    assert client.post(f"{base}/preview", json={}, headers=headers).status_code == 403
    assert client.post(f"{base}/confirm", json={"preview": "x"}, headers=headers).status_code == 403


def _roles_project(branch: str, stage: str) -> tuple[dict[str, Any], str]:
    from sudoo.projects import access

    project = projects.create(repository=f"Acme/x-{time.time_ns()}", odoo_version="18.0", edition="community",
                              origin="linked", user="t")  # fmt: skip
    project = projects.change(project["id"], lambda p: {**p, "access": {**p["access"], "model": "project_roles"},
                                                        "branches": {branch: {"stage": stage}}}) or project  # fmt: skip
    access.forget_restrictions()
    job = Job(f"ex-{branch}", "Create instance", "create", "t")
    job.details = {"build": "create", "project": project["id"], "branch": branch}
    builds.job_started(job)
    job.step("Starting Odoo")
    job.status, job.finished = "failed", time.time()
    job.failure = {"step": "Starting Odoo", "reason": "r", "kind": "other", "rule": None,
                   "details": ["boom"], "repository": None}  # fmt: skip
    builds.job_ended(job)
    return project, str(builds.history(project["id"], branch)[0]["id"])


def _member(app: Any, project: dict[str, Any], level: str | None) -> tuple[Any, dict[str, str]]:
    from conftest import sign_in
    from sudoo import accounts
    from sudoo.projects import access

    name = f"ex{time.time_ns() % 10**9}"
    accounts.create(name, "member", "violet-harbor-engine-93", must_change=False, email=f"{name}@example.com")
    if level:
        projects.change(project["id"], lambda p: {**p, "access": {**p["access"], "grants": [
            {"type": "user", "id": name, "level": level}]}})  # fmt: skip
        access.forget_restrictions()
    return sign_in(app, name, "violet-harbor-engine-93")


def test_who_may_not_read_the_logs_of_a_branch_cannot_preview_or_send_its_excerpt(
    app: Any, admin: Client, clean: Exchange
) -> None:
    client, headers = admin
    project, build_id = _roles_project("main", "production")
    _turn_on(client, headers, project["id"])
    base = f"/api/v1/projects/{project['id']}/builds/{build_id}/explain"
    # a maintainer may read production's logs: the route works for them (so the refusals below mean something)
    maintainer, m_headers = _member(app, project, "maintainer")
    made = maintainer.post(f"{base}/preview", json={}, headers=m_headers)
    assert made.status_code == 200, made.get_json()
    # a viewer sees production but may not read its logs (test_project_roles MATRIX): refused on all three routes
    viewer, v_headers = _member(app, project, "view")
    assert viewer.get(base).status_code == 403
    assert viewer.post(f"{base}/preview", json={}, headers=v_headers).status_code == 403
    assert (
        viewer.post(f"{base}/confirm", json={"preview": made.get_json()["data"]["id"]}, headers=v_headers).status_code
        == 403
    )
    assert clean.calls == []


def test_a_person_who_cannot_see_the_branch_is_told_it_does_not_exist(app: Any, admin: Client, clean: Exchange) -> None:
    client, headers = admin
    project, build_id = _roles_project("dev", "development")
    _turn_on(client, headers, project["id"])
    base = f"/api/v1/projects/{project['id']}/builds/{build_id}/explain"
    stranger, s_headers = _member(app, project, None)
    for answer in (
        stranger.get(base),
        stranger.post(f"{base}/preview", json={}, headers=s_headers),
        stranger.post(f"{base}/confirm", json={"preview": "0" * 32}, headers=s_headers),
    ):
        assert answer.status_code in (403, 404), answer.get_json()
    assert clean.calls == []


def test_a_preview_made_by_one_person_cannot_be_sent_by_another(app: Any, admin: Client, clean: Exchange) -> None:
    client, headers = admin
    project, build_id = _roles_project("dev", "development")
    _turn_on(client, headers, project["id"])
    base = f"/api/v1/projects/{project['id']}/builds/{build_id}/explain"
    made = client.post(f"{base}/preview", json={}, headers=headers).get_json()["data"]
    other, o_headers = _member(app, project, "maintainer")
    taken = other.post(f"{base}/confirm", json={"preview": made["id"]}, headers=o_headers)
    assert taken.status_code in (403, 404, 409) and clean.calls == []
    assert client.post(f"{base}/confirm", json={"preview": made["id"]}, headers=headers).status_code == 200


def test_nothing_is_reachable_without_signing_in(app: Any, clean: Exchange) -> None:
    from conftest import ORIGIN

    anonymous = app.test_client()
    base = "/api/v1/projects/p/builds/b/explain"
    for method, path in (
        ("get", "/api/v1/explain"),
        ("put", "/api/v1/explain"),
        ("put", "/api/v1/projects/p/explain"),
        ("get", base),
        ("post", f"{base}/preview"),
        ("post", f"{base}/confirm"),
    ):
        answer = getattr(anonymous, method)(path, **({"json": {}, "headers": ORIGIN} if method != "get" else {}))
        assert answer.status_code == 401, (method, path, answer.status_code)
    assert clean.calls == []
