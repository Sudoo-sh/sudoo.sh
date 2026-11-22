"""Checking a project's link to GitHub (repos/github_check.py, POST /api/v1/projects/<id>/github/check): the
repository, the App's installation, its permissions and events, its webhook and deliveries, and submodules.
GitHub is never called: the App's REST calls and git are replaced by fakes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from sudoo import exposure, projects
from sudoo.core import audit
from sudoo.repos import github, github_app, github_check
from sudoo.storage import storage

PROJECT = "check-erp"
REPOSITORY = "Acme/erp"
PANEL = "https://panel.example.com"
GOOD_PERMISSIONS = {"contents": "read", "metadata": "read", "statuses": "write"}


class FakeApp:
    def __init__(self) -> None:
        self.installation: dict[str, Any] = {
            "id": 7, "account": {"login": "Acme"}, "repository_selection": "all", "html_url": "https://github.com/i/7",
            "permissions": dict(GOOD_PERMISSIONS), "events": ["push", "create", "delete"],
        }  # fmt: skip
        self.hook = {"url": PANEL + github_app.HOOK_PATH}
        self.deliveries: list[dict[str, Any]] = [
            {"event": "push", "status": "OK", "status_code": 200, "delivered_at": "2026-11-22T08:00:00Z",
             "repository_id": 42},
        ]  # fmt: skip
        self.selected = [REPOSITORY]

    def request(self, method: str, path: str, **_: Any) -> Any:
        if path == "/app/installations/7":
            return self.installation
        if path == "/app/hook/config":
            return self.hook
        if path.startswith("/app/hook/deliveries"):
            return self.deliveries
        if path == f"/repos/{REPOSITORY}":
            return {"id": 42}
        raise github_app.GitHubError("not faked", 404)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeApp]:
    app = FakeApp()
    monkeypatch.setattr(github, "_readable", lambda address: (True, ""))
    monkeypatch.setattr(github, "has_key", lambda rid: False)
    monkeypatch.setattr(github, "access_via", lambda rid: "github-app")
    monkeypatch.setattr(github, "diagnose", lambda address: None)
    monkeypatch.setattr(github, "access_report", lambda address, branch="": [{"url": address, "role": "main",
                                                                             "ok": True}])  # fmt: skip
    monkeypatch.setattr(github_app, "connected", lambda: True)
    monkeypatch.setattr(github_app, "sync_if_stale", lambda max_age=60: False)
    monkeypatch.setattr(github_app, "installation_for", lambda owner: 7 if owner.lower() == "acme" else None)
    monkeypatch.setattr(github_app, "installations", lambda: [{"id": 7, "account": "Acme"}])
    monkeypatch.setattr(github_app, "app_jwt", lambda: "jwt")
    monkeypatch.setattr(github_app, "installation_token", lambda number, now=None: "token")
    monkeypatch.setattr(github_app, "describe", lambda: {"settings_url": "https://github.com/settings/apps/x"})
    monkeypatch.setattr(github_app, "request", app.request)
    monkeypatch.setattr(
        github_app, "installation_repositories", lambda found: [{"full_name": name} for name in app.selected]
    )
    monkeypatch.setattr(exposure, "address", lambda which: PANEL)
    storage().put(projects.COLLECTION, PROJECT, {"id": PROJECT, "name": "Check", "repository": REPOSITORY})
    yield app
    storage().delete(projects.COLLECTION, PROJECT)


def _by_id(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in results}


def test_everything_in_order(fake: FakeApp) -> None:
    results = github_check.check({"repository": REPOSITORY})
    assert [r["id"] for r in results] == ["repository", "installation", "permissions", "webhook", "submodules"]
    assert {r["status"] for r in results} == {"ok"}, results
    assert github_check.overall(results) == "ok"
    assert "this repository's deliveries" in _by_id(results)["webhook"]["message"]


def test_repository_not_readable(fake: FakeApp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "_readable", lambda address: (False, "Repository not found."))
    found = _by_id(github_check.check({"repository": REPOSITORY}))
    assert found["repository"]["status"] == "error" and found["submodules"]["status"] == "skipped"


def test_not_installed_and_not_selected(fake: FakeApp, monkeypatch: pytest.MonkeyPatch) -> None:
    fake.installation["repository_selection"] = "selected"
    fake.selected = ["Acme/other"]
    found = _by_id(github_check.check({"repository": REPOSITORY}))
    assert found["installation"]["status"] == "error" and found["installation"]["fix"]["label"] == "Choose repositories"
    assert found["permissions"]["status"] == "skipped"
    monkeypatch.setattr(github_app, "installation_for", lambda owner: None)
    monkeypatch.setattr(github_app, "explain", lambda name: {"reason": "not_installed", "message": "Install it.",
                                                             "url": "https://github.com/apps/x", "label": "Install"})  # fmt: skip
    found = _by_id(github_check.check({"repository": REPOSITORY}))
    assert found["installation"]["message"] == "Install it." and found["installation"]["status"] == "error"


def test_permissions_and_events(fake: FakeApp) -> None:
    fake.installation["permissions"] = {"contents": "read", "metadata": "read"}
    fake.installation["events"] = ["push"]
    found = _by_id(github_check.check({"repository": REPOSITORY}))["permissions"]
    assert (
        found["status"] == "warning" and "Commit statuses" in found["message"] and "create, delete" in found["message"]
    )
    fake.installation["permissions"] = {"metadata": "read"}
    assert _by_id(github_check.check({"repository": REPOSITORY}))["permissions"]["status"] == "error"


def test_webhook(fake: FakeApp) -> None:
    fake.hook = {"url": "https://old.trycloudflare.com/hooks/github-app"}
    found = _by_id(github_check.check({"repository": REPOSITORY}))["webhook"]
    assert found["status"] == "error" and "not to this panel" in found["message"]
    fake.hook = {"url": PANEL + github_app.HOOK_PATH}
    fake.deliveries = [{"event": "push", "status": "Invalid HTTP Response: 502", "status_code": 502,
                        "delivered_at": "2026-11-22T09:00:00Z", "repository_id": 42}] + fake.deliveries  # fmt: skip
    found = _by_id(github_check.check({"repository": REPOSITORY}))["webhook"]
    assert found["status"] == "error" and "502" in found["message"] and found["fix"]["url"].endswith("/advanced")
    fake.deliveries = fake.deliveries[1:] + fake.deliveries[:1]
    assert _by_id(github_check.check({"repository": REPOSITORY}))["webhook"]["status"] == "warning"
    fake.deliveries = []
    assert _by_id(github_check.check({"repository": REPOSITORY}))["webhook"]["status"] == "ok"


def test_without_an_app(fake: FakeApp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "connected", lambda: False)
    found = _by_id(github_check.check({"repository": REPOSITORY}))
    assert found["installation"]["status"] == "warning" and found["webhook"]["status"] == "skipped"
    monkeypatch.setattr(github, "has_key", lambda rid: True)
    assert _by_id(github_check.check({"repository": REPOSITORY}))["installation"]["status"] == "skipped"


def test_private_submodules(fake: FakeApp, monkeypatch: pytest.MonkeyPatch) -> None:
    report = [
        {"url": "https://github.com/Acme/erp", "role": "main", "ok": True},
        {
            "url": "git@github.com:Acme/lib.git",
            "role": "submodule",
            "ok": True,
            "via": "deploy-key",
            "repo": "acme/lib",
        },
        {"url": "git@github.com:Other/x.git", "role": "submodule", "ok": False, "repo": "other/x", "fix": None},
    ]
    monkeypatch.setattr(github, "access_report", lambda address, branch="": report)
    found = _by_id(github_check.check({"repository": REPOSITORY}))["submodules"]
    assert found["status"] == "error" and "other/x" in found["message"] and found["fix"]["url"] == "/github"
    report.pop()
    found = _by_id(github_check.check({"repository": REPOSITORY}))["submodules"]
    assert found["status"] == "ok" and "1 with a deploy key" in found["message"]


def test_the_endpoint(fake: FakeApp, admin: Any, operator: Any) -> None:
    flask_client, headers = admin
    answer = flask_client.post(f"/api/v1/projects/{PROJECT}/github/check", json={}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    data = answer.get_json()["data"]
    assert data["status"] == "ok" and len(data["checks"]) == 5 and data["checked_at"].endswith("Z")
    entry = audit.latest(1)[0]
    assert entry["event"] == "GitHub connection checked" and entry["project"] == PROJECT
    developer, developer_headers = operator  # a developer cannot manage projects
    refused = developer.post(f"/api/v1/projects/{PROJECT}/github/check", json={}, headers=developer_headers)
    assert refused.status_code == 403
    missing = flask_client.post("/api/v1/projects/nope/github/check", json={}, headers=headers)
    assert missing.status_code == 404
