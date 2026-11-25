"""The repository guard (repo_guard/): the files it adds (snapshots), its pull request, branch rules (rulesets, the
classic-protection fallback, plan limits), CI results, permissions and the audit log. GitHub never runs: a fake,
in-memory GitHub answers every call the panel makes (github_app.request is replaced)."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from sudoo import projects, repo_guard
from sudoo.config import live
from sudoo.core import audit
from sudoo.projects import promotion
from sudoo.repo_guard import addon_checks, files, rules
from sudoo.repos import github_app
from sudoo.repos.github_app import GitHubError
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
SNAPSHOTS = Path(__file__).resolve().parent / "snapshots" / "repo_guard"
REPO = "Acme/books"
SHA_OK, SHA_BAD = "a" * 40, "b" * 40
PULL = {"number": 1, "url": f"https://github.com/{REPO}/pull/1", "state": "merged"}
GUARD = {"contents": "write", "pull_requests": "write", "workflows": "write", "checks": "read",
         "administration": "write", "metadata": "read", "statuses": "write", "members": "read"}  # fmt: skip


class FakeGitHub:
    """Just enough of GitHub's REST API for the guard, kept in memory."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.owner_type = "Organization"
        self.private = True
        self.main: dict[str, str] = {"README.md": "# Books\n"}  # files of the default branch
        self.head: dict[str, str] | None = None  # files of the panel's branch
        self.pulls: list[dict[str, Any]] = []
        self.rulesets: dict[int, dict[str, Any]] = {}
        self.protections: dict[str, dict[str, Any]] = {}
        self.rulesets_missing = False  # the rulesets API answers 404 (not available)
        self.plan_limit = False  # a private repository on GitHub Free
        self.next_id = 100
        self.check_runs: dict[str, list[dict[str, Any]]] = {}
        self.tokens: list[dict[str, str]] = []

    def merge(self) -> None:
        """Someone merges the panel's open pull request on GitHub."""
        assert self.head is not None
        self.main = dict(self.head)
        for pull in self.pulls:
            if pull["state"] == "open":
                pull["state"], pull["merged_at"] = "closed", "2026-11-23T10:00:00Z"
        self.head = None

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def _plan(self) -> None:
        if self.plan_limit:
            raise GitHubError("GitHub answered 403: Upgrade to GitHub Pro or make this repository public to enable "
                              "this feature.", 403)  # fmt: skip

    def __call__(self, method: str, path: str, *, bearer: str | None = None, body: Any = None,
                 timeout: float = 20) -> Any:  # fmt: skip
        self.calls.append((method, path, body))
        root = f"/repos/{REPO}"
        if method == "POST" and re.match(r"^/app/installations/\d+/access_tokens$", path):
            self.tokens.append(body["permissions"])
            return {"token": f"ghs_{len(self.tokens)}", "expires_at": "2099-01-01T00:00:00Z"}
        assert bearer and bearer.startswith("ghs_"), path
        if path == root:
            return {"full_name": REPO, "private": self.private, "default_branch": "main",
                    "owner": {"type": self.owner_type, "login": "Acme"}}  # fmt: skip
        if path.startswith(f"{root}/contents/"):
            name = path.removeprefix(f"{root}/contents/").split("?")[0].replace("%2F", "/")
            if name not in self.main:
                raise GitHubError("GitHub answered 404: Not Found.", 404)
            return {"type": "file", "content": base64.b64encode(self.main[name].encode()).decode()}
        if path == f"{root}/git/ref/heads/main":
            return {"object": {"sha": "m" * 40}}
        if path == f"{root}/git/ref/heads/sudoo/repository-guard":
            if self.head is None:
                raise GitHubError("GitHub answered 404: Not Found.", 404)
            return {"object": {"sha": "h" * 40}}
        if path.startswith(f"{root}/git/commits/") and method == "GET":
            return {"tree": {"sha": "t" * 40}}
        if path == f"{root}/git/trees":
            tree = dict(self.main)
            for entry in body["tree"]:
                if entry.get("sha", "") is None:
                    tree.pop(entry["path"], None)
                else:
                    tree[entry["path"]] = entry["content"]
            self.pending = tree
            return {"sha": "n" * 40}
        if path == f"{root}/git/commits":
            return {"sha": "c" * 40}
        if path == f"{root}/git/refs" or path == f"{root}/git/refs/heads/sudoo/repository-guard":
            if method == "DELETE":
                self.head = None
                return None
            self.head = self.pending
            return {}
        if path.startswith(f"{root}/pulls"):
            return self._pulls(method, path, body)
        if path.startswith(f"{root}/rulesets"):
            return self._rulesets(method, path, body)
        if "/protection" in path:
            return self._protection(method, path, body)
        if path.startswith("/orgs/Acme/teams/"):
            slug = path.rsplit("/", 1)[1]
            if slug != "release-managers":
                raise GitHubError("GitHub answered 404: Not Found.", 404)
            return {"id": 9001, "slug": slug}
        match = re.match(rf"^{root}/commits/([0-9a-f]{{40}})/(check-runs|status)", path)
        if match:
            if match[2] == "status":
                return {"statuses": [{"context": "sudoo/staging", "state": "success"}]}
            return {"check_runs": self.check_runs.get(match[1], [])}
        raise AssertionError(f"unexpected call {method} {path}")

    def _pulls(self, method: str, path: str, body: Any) -> Any:
        if method == "GET" and "state=open" in path:
            return [p for p in self.pulls if p["state"] == "open"]
        number = path.rsplit("/", 1)[-1]
        if method == "POST":
            pull = {"number": len(self.pulls) + 1, "state": "open", "html_url": f"https://github.com/{REPO}/pull/"
                    f"{len(self.pulls) + 1}", "title": body["title"], "body": body["body"], "base": body["base"]}  # fmt: skip
            self.pulls.append(pull)
            return pull
        pull = next(p for p in self.pulls if str(p["number"]) == number)
        if method == "PATCH":
            pull.update(body)
        return pull

    def _rulesets(self, method: str, path: str, body: Any) -> Any:
        if self.rulesets_missing:
            raise GitHubError("GitHub answered 404: Not Found.", 404)
        self._plan()
        found = re.search(r"/rulesets/(\d+)$", path)
        if method == "GET" and not found:
            return [{"id": i, "name": r["name"], "enforcement": r["enforcement"]} for i, r in self.rulesets.items()]
        if method == "POST":
            number = self._id()
            self.rulesets[number] = {**body, "id": number}
            return self.rulesets[number]
        assert found
        number = int(found[1])
        if number not in self.rulesets:
            raise GitHubError("GitHub answered 404: Not Found.", 404)
        if method == "GET":
            return self.rulesets[number]
        if method == "PUT":
            self.rulesets[number] = {**body, "id": number}
            return self.rulesets[number]
        del self.rulesets[number]
        return None

    def _protection(self, method: str, path: str, body: Any) -> Any:
        self._plan()
        branch = path.split("/branches/")[1].split("/")[0]
        if path.endswith("/required_signatures"):
            self.protections.setdefault(branch, {})["signed"] = method == "POST"
            return {}
        if method == "GET":
            if branch not in self.protections:
                raise GitHubError("GitHub answered 404: Branch not protected.", 404)
            saved = self.protections[branch]
            return {
                "required_pull_request_reviews": saved.get("required_pull_request_reviews"),
                "required_status_checks": saved.get("required_status_checks"),
                "restrictions": saved.get("restrictions") and {
                    "users": [{"login": u} for u in saved["restrictions"]["users"]],
                    "teams": [{"slug": t} for t in saved["restrictions"]["teams"]]},
                "allow_force_pushes": {"enabled": saved.get("allow_force_pushes", False)},
                "allow_deletions": {"enabled": saved.get("allow_deletions", False)},
                "required_linear_history": {"enabled": saved.get("required_linear_history", False)},
                "required_signatures": {"enabled": saved.get("signed", False)},
            }  # fmt: skip
        if method == "PUT":
            self.protections[branch] = {**self.protections.get(branch, {}), **body}
            return {}
        self.protections.pop(branch, None)
        return None


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGitHub]:
    github = FakeGitHub()
    installation = {"id": 77, "account": "Acme", "account_type": "Organization", "permissions": dict(GUARD),
                    "settings_url": "https://github.com/organizations/Acme/settings/installations/77"}  # fmt: skip
    monkeypatch.setattr(github_app, "connected", lambda: True)
    monkeypatch.setattr(github_app, "installations", lambda: [installation])
    monkeypatch.setattr(github_app, "app_jwt", lambda: "jwt")
    monkeypatch.setattr(github_app, "describe", lambda: {"settings_url": "https://github.com/organizations/Acme/"
                        "settings/apps/sudoo", "install_url": "https://github.com/apps/sudoo/installations/new"})  # fmt: skip
    monkeypatch.setattr(github_app, "request", github)
    github.installation = installation  # type: ignore[attr-defined]
    repo_guard.forget_results()
    yield github
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)


def _project(**branches: str) -> dict[str, Any]:
    project = projects.create(repository=REPO, odoo_version="18.0", edition="community", origin="linked", user="t")
    stages_ = {"main": "production", "qa": "staging", **branches}
    projects.change(project["id"], lambda p: {**p, "branches": {b: {"stage": s} for b, s in stages_.items()}})
    return projects.get(project["id"]) or project


def _checks(**values: Any) -> dict[str, Any]:
    return {**files.DEFAULT_CHECKS, "enabled": True, **values}


# ---------------------------------------------------------------- the generated files


VERSIONS = {"default": ["18.0"], "branches": {"17.0": ["17.0"]}}


def _snapshot(name: str, text: str) -> None:
    path = SNAPSHOTS / name
    if os.environ.get("UPDATE_SNAPSHOTS") or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert text == path.read_text(), f"{name} changed: run with UPDATE_SNAPSHOTS=1 if that was meant"


def test_workflow_snapshot_and_pins() -> None:
    yaml = pytest.importorskip("yaml")
    text = files.workflow(_checks(tests=True), VERSIONS)
    _snapshot("sudoo-checks.yml", text)
    parsed = yaml.safe_load(text)
    assert set(parsed["jobs"]) == {"versions", "lint", "pylint-odoo", "addons", "tests", files.REQUIRED_CHECK}
    assert parsed["jobs"][files.REQUIRED_CHECK]["needs"] == ["lint", "pylint-odoo", "addons", "tests"]
    assert parsed["permissions"] == {"contents": "read"}
    # every action is pinned by a full commit hash, never a tag or a branch
    uses = re.findall(r"uses: (\S+)", text)
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses)
    # the matrix follows the project's series, and a branch with its own version gets its own
    assert json.loads(parsed["jobs"]["versions"]["steps"][0]["env"]["VERSIONS"]) == VERSIONS
    assert parsed["jobs"]["tests"]["container"]["image"] == "odoo:${{ matrix.odoo }}"
    assert parsed["jobs"]["tests"]["services"]["db"]["image"].startswith("postgres:")
    assert "--valid-odoo-versions=${{ matrix.odoo }}" in text


def test_pre_commit_snapshot_and_pins() -> None:
    yaml = pytest.importorskip("yaml")
    text = files.pre_commit(_checks(linter="flake8", strictness="strict"), "18.0")
    _snapshot("pre-commit-config.yaml", text)
    parsed = yaml.safe_load(text)
    for repo in parsed["repos"]:
        if repo["repo"] != "local":
            assert re.fullmatch(r"[0-9a-f]{40}", repo["rev"]), repo
    hooks = [h["id"] for r in parsed["repos"] for h in r["hooks"]]
    assert "flake8" in hooks and "pylint_odoo" in hooks and "sudoo-addons" in hooks and "ruff-check" not in hooks
    assert "--valid-odoo-versions=18.0" in text
    assert files.is_ours(text) and files.is_ours(files.workflow(_checks(), VERSIONS)) and not files.is_ours("x: 1\n")


def test_generated_files_follow_the_choices() -> None:
    assert files.generate(_checks(enabled=False), VERSIONS) == {}
    only_lint = files.generate(_checks(pylint_odoo=False, manifest=False, xml_csv=False), VERSIONS)
    assert set(only_lint) == {files.WORKFLOW, files.PRE_COMMIT, files.DOC}
    assert "pylint" not in only_lint[files.WORKFLOW] and "tests:" not in only_lint[files.WORKFLOW]
    everything = files.generate(_checks(tests=True), VERSIONS)
    assert set(everything) == set(files.PATHS) - {files.CODEOWNERS}  # CODEOWNERS only with owners
    assert everything[files.SCRIPT].startswith(f"# {files.MARKER}")
    relaxed = files.workflow(_checks(strictness="relaxed"), VERSIONS)
    strict = files.workflow(_checks(strictness="strict"), VERSIONS)
    assert "missing-readme" in strict and "missing-readme" not in relaxed
    assert "Odoo 17.0, 18.0" in files.document(_checks(), VERSIONS)


def test_addon_checks(tmp_path: Path) -> None:
    good = tmp_path / "sale_extra"
    (good / "views").mkdir(parents=True)
    (good / "__manifest__.py").write_text(repr({"name": "Sale extra", "version": "18.0.1.0.0", "license": "LGPL-3",
                                                "author": "Acme", "depends": ["sale"], "data": ["views/a.xml"]}))  # fmt: skip
    (good / "views" / "a.xml").write_text("<odoo><record id='x' model='ir.ui.view'/></odoo>")
    assert addon_checks.main(["--root", str(tmp_path), "--series", "18.0"]) == 0
    assert addon_checks.main(["--root", str(tmp_path), "--series", "17.0"]) == 1  # another series
    bad = tmp_path / "stock_extra"
    (bad / "data").mkdir(parents=True)
    (bad / "__manifest__.py").write_text(repr({"name": "S", "version": "1.0", "license": "MIT", "depends": "stock",
                                               "data": ["data/missing.xml"]}))  # fmt: skip
    (bad / "data" / "broken.xml").write_text("<odoo><record></odoo>")
    (bad / "data" / "rows.csv").write_text("id,name\nx,1,2\n")
    problems = addon_checks.check_manifest(str(bad), "18.0", "standard") + addon_checks.check_files(str(bad))
    text = "\n".join(problems)
    for expected in ("'author' is missing", "license 'MIT'", "should be 18.0.x.y.z", "depends must be a list",
                     "missing.xml, which does not exist", "not valid XML", "line 2 has 3 columns"):  # fmt: skip
        assert expected in text, expected
    # it runs on its own, as the workflow runs it
    found = subprocess.run([sys.executable, str(Path(addon_checks.__file__)), "--root", str(tmp_path),
                            "--list-modules"], capture_output=True, text=True, check=True)  # fmt: skip
    assert found.stdout.strip() == "sale_extra,stock_extra"


# ---------------------------------------------------------------- the pull request


def test_checks_open_update_and_remove_a_pull_request(fake: FakeGitHub) -> None:
    project = _project()
    dry = repo_guard.sync_checks(project, _checks(), "ada", dry_run=True)
    assert dry["result"] == "dry_run" and {f["action"] for f in dry["files"]} == {"add"} and not fake.pulls
    assert repo_guard.config(project["id"])["checks"]["enabled"] is False  # a dry run saves nothing

    opened = repo_guard.sync_checks(project, _checks(), "ada")
    assert opened["result"] == "opened" and opened["pull"]["number"] == 1
    assert fake.head is not None and set(files.PATHS) - {files.SCRIPT, files.CODEOWNERS} <= set(fake.head)
    assert fake.pulls[0]["base"] == "main"
    # never written to the default branch, only to the panel's own branch
    assert not any(p.endswith("/git/refs/heads/main") for _, p, _ in fake.calls if _ != "GET")
    assert {"contents": "write", "pull_requests": "write", "workflows": "write", "metadata": "read"} in fake.tokens

    updated = repo_guard.sync_checks(project, _checks(strictness="strict"), "ada")
    assert updated["result"] == "updated" and updated["pull"]["number"] == 1 and len(fake.pulls) == 1
    assert repo_guard.describe(project)["pull"]["url"].endswith("/pull/1")

    fake.merge()
    assert repo_guard.refresh_pull(project)["state"] == "merged"  # type: ignore[index]
    same = repo_guard.sync_checks(project, _checks(strictness="strict"), "ada")
    assert same["result"] == "up_to_date" and len(fake.pulls) == 1

    removed = repo_guard.sync_checks(project, _checks(enabled=False), "ada")
    assert removed["result"] == "opened" and removed["pull"]["purpose"] == "remove"
    assert fake.head is not None and not set(files.PATHS) & set(fake.head) and "README.md" in fake.head


def test_files_written_by_someone_else_are_kept(fake: FakeGitHub) -> None:
    project = _project()
    fake.main[files.PRE_COMMIT] = "repos: []  # the team's own\n"
    result = repo_guard.sync_checks(project, _checks(), "ada")
    kept = next(f for f in result["files"] if f["path"] == files.PRE_COMMIT)
    assert kept["action"] == "kept" and fake.head is not None
    assert fake.head[files.PRE_COMMIT] == "repos: []  # the team's own\n"
    fake.merge()
    off = repo_guard.sync_checks(project, _checks(enabled=False), "ada")
    assert fake.head is not None and fake.head[files.PRE_COMMIT] == "repos: []  # the team's own\n"
    assert files.PRE_COMMIT not in {f["path"] for f in off["files"]}


def test_closing_when_nothing_is_left_to_change(fake: FakeGitHub) -> None:
    project = _project()
    repo_guard.sync_checks(project, _checks(), "ada")
    result = repo_guard.sync_checks(project, _checks(enabled=False), "ada")  # nothing of the panel's on main yet
    assert result["result"] == "up_to_date" and fake.pulls[0]["state"] == "closed" and fake.head is None


# ---------------------------------------------------------------- branch rules


def _rules(**values: Any) -> dict[str, Any]:
    return repo_guard.clean_rules(values, rules.DEFAULT_RULES)


def test_rulesets_preview_apply_update_and_turn_off(fake: FakeGitHub) -> None:
    project = _project()
    fake.rulesets[5] = {"id": 5, "name": "Team rules", "enforcement": "active", "rules": [{"type": "deletion"}]}
    chosen = _rules(enabled=True)
    preview = repo_guard.preview_rules(project, chosen)
    assert preview["method"] == "rulesets" and preview["available"] and preview["affects_production"]
    assert {t["key"]: t["action"] for t in preview["targets"]} == {"production": "create", "production_merges":
                                                                   "create", "staging": "create"}  # fmt: skip
    assert preview["untouched"] == ["Team rules"] and len(fake.rulesets) == 1  # a preview changes nothing
    production = next(t for t in preview["targets"] if t["key"] == "production")
    assert {"field": "approvals", "label": "Approvals required", "before": "", "after": "1"} in production["changes"]

    with pytest.raises(repo_guard.GuardError, match="type the repository's name"):
        repo_guard.apply_rules(project, chosen, "ada", confirm="wrong")
    result = repo_guard.apply_rules(project, chosen, "ada", confirm="acme/BOOKS")
    assert result["result"] == "applied" and len(fake.rulesets) == 4
    ours = {r["name"]: r for r in fake.rulesets.values() if r["name"].startswith(rules.PREFIX)}
    body = ours[rules.NAMES["production"]]
    assert body["conditions"]["ref_name"]["include"] == ["refs/heads/main"] and body["bypass_actors"] == []
    types = {r["type"]: r.get("parameters") for r in body["rules"]}
    assert types["pull_request"]["required_approving_review_count"] == 1
    assert types["required_status_checks"]["required_status_checks"] == [
        {"context": files.REQUIRED_CHECK, "integration_id": 15368}
    ]
    assert {"deletion", "non_fast_forward"} <= set(types)
    merges = ours[rules.NAMES["production_merges"]]
    assert merges["rules"][0]["type"] == "update" and merges["bypass_actors"] == rules.ROLE_ACTORS
    assert ours[rules.NAMES["staging"]]["conditions"]["ref_name"]["include"] == ["refs/heads/qa"]

    again = repo_guard.preview_rules(project, chosen)
    assert {t["action"] for t in again["targets"]} == {"unchanged"} and not again["affects_production"]
    stricter = _rules(enabled=True, production={"approvals": 2, "linear_history": True})
    changed = repo_guard.preview_rules(project, stricter)
    production = next(t for t in changed["targets"] if t["key"] == "production")
    assert production["action"] == "update"
    assert {(c["field"], c["before"], c["after"]) for c in production["changes"]} == {("approvals", "1", "2"),
                                                                                     ("linear_history", "no", "yes")}  # fmt: skip
    repo_guard.apply_rules(project, stricter, "ada", confirm=REPO)
    assert len(fake.rulesets) == 4  # updated in place

    # someone renames the team's ruleset to look like the panel's? only the panel's prefix counts as the panel's
    removed = repo_guard.apply_rules(project, stricter, "ada", confirm=REPO, turn_off=True)
    assert removed["result"] == "removed" and list(fake.rulesets) == [5]
    assert repo_guard.describe(project)["applied"] is None


def test_named_teams_and_people(fake: FakeGitHub) -> None:
    project = _project()
    teams = _rules(enabled=True, pushers={"mode": "named", "teams": ["release-managers"]})
    repo_guard.apply_rules(project, teams, "ada", confirm=REPO)
    merges = next(r for r in fake.rulesets.values() if r["name"] == rules.NAMES["production_merges"])
    assert merges["bypass_actors"] == [{"actor_id": 9001, "actor_type": "Team", "bypass_mode": "always"}]
    with pytest.raises(repo_guard.GuardError):
        _rules(pushers={"mode": "named", "teams": [], "users": []})
    with pytest.raises(repo_guard.GuardError):
        _rules(pushers={"mode": "named", "users": ["not a login!"]})
    # people cannot be named in a ruleset: asking for rulesets says so
    people = _rules(enabled=True, method="rulesets", pushers={"mode": "named", "users": ["@ada"]})
    assert people["pushers"]["users"] == ["ada"]
    found = repo_guard.preview_rules(project, people)
    assert "people_in_rulesets" in {item["reason"] for item in found["limitations"]}


def test_branch_protection_fallback_leaves_others_alone(fake: FakeGitHub) -> None:
    project = _project()
    fake.rulesets_missing = True
    fake.protections["qa"] = {"required_pull_request_reviews": {"required_approving_review_count": 3}}
    chosen = _rules(enabled=True, pushers={"mode": "named", "users": ["ada"], "teams": []})
    preview = repo_guard.preview_rules(project, chosen)
    assert preview["method"] == "branch_protection"
    actions = {t["key"]: t["action"] for t in preview["targets"]}
    assert actions == {"main": "create", "qa": "blocked"}
    repo_guard.apply_rules(project, chosen, "ada", confirm=REPO)
    assert fake.protections["main"]["restrictions"] == {"users": ["ada"], "teams": [], "apps": []}
    assert fake.protections["main"]["allow_force_pushes"] is False
    assert fake.protections["qa"] == {"required_pull_request_reviews": {"required_approving_review_count": 3}}
    assert repo_guard.describe(project)["applied"]["protections"] == ["main"]
    repo_guard.apply_rules(project, chosen, "ada", confirm=REPO, turn_off=True)
    assert "main" not in fake.protections and "qa" in fake.protections  # only the panel's was removed


def test_plan_limits_are_explained(fake: FakeGitHub) -> None:
    project = _project()
    fake.plan_limit = True
    preview = repo_guard.preview_rules(project, _rules(enabled=True))
    assert not preview["available"] and preview["targets"] == []
    assert any(item["reason"] == "plan" and "GitHub Pro" in item["message"] for item in preview["limitations"])
    with pytest.raises(repo_guard.GuardError, match="GitHub Pro"):
        repo_guard.apply_rules(project, _rules(enabled=True), "ada", confirm=REPO)


def test_missing_permissions_are_named(fake: FakeGitHub) -> None:
    project = _project()
    fake.installation["permissions"] = {"contents": "read", "metadata": "read"}  # type: ignore[attr-defined]
    view = repo_guard.describe(project)["github"]
    assert view["missing"]["rules"] == ["administration"]
    assert set(view["missing"]["checks"]) == {"contents", "pull_requests", "workflows"}
    assert view["app_permissions_url"].endswith("/permissions") and view["installation_url"]
    with pytest.raises(repo_guard.GuardError, match="Administration"):
        repo_guard.preview_rules(project, _rules(enabled=True))
    with pytest.raises(repo_guard.GuardError, match="pull requests"):
        repo_guard.sync_checks(project, _checks(), "ada")
    assert not [c for c in fake.calls if c[0] != "GET"]  # nothing was asked of GitHub


def test_manifest_asks_for_the_guard_only_when_wanted() -> None:
    plain = github_app.manifest("https://p.example", "https://p.example", "p.example")
    guard = github_app.manifest("https://p.example", "https://p.example", "p.example", repository_guard=True)
    assert "workflows" not in plain["default_permissions"]
    assert {k: guard["default_permissions"][k] for k in github_app.GUARD_PERMISSIONS} == github_app.GUARD_PERMISSIONS


def test_required_check_is_pinned_to_github_actions(fake: FakeGitHub) -> None:
    project = _project()
    teams = _rules(enabled=True, pushers={"mode": "named", "teams": ["release-managers"]})
    repo_guard.apply_rules(project, teams, "ada", confirm=REPO)
    body = next(r for r in fake.rulesets.values() if r["name"] == rules.NAMES["production"])
    types = {r["type"]: r.get("parameters") for r in body["rules"]}
    assert types["required_status_checks"]["required_status_checks"] == [{"context": "sudoo-checks",
                                                                          "integration_id": 15368}]  # fmt: skip
    assert types["pull_request"]["require_code_owner_review"] is True  # the default for production
    staging = next(r for r in fake.rulesets.values() if r["name"] == rules.NAMES["staging"])
    assert {r["type"]: r.get("parameters") for r in staging["rules"]}["pull_request"][
        "require_code_owner_review"
    ] is False
    assert {t["action"] for t in repo_guard.preview_rules(project, teams)["targets"]} == {"unchanged"}


def test_rules_written_before_are_tightened_and_logged(fake: FakeGitHub, caplog: pytest.LogCaptureFixture) -> None:
    project = _project()
    teams = _rules(enabled=True, pushers={"mode": "named", "teams": ["release-managers"]})
    repo_guard.apply_rules(project, teams, "ada", confirm=REPO)
    for ruleset in fake.rulesets.values():  # as an older panel wrote them: no source, no code-owner review
        for rule in ruleset["rules"]:
            if rule["type"] == "required_status_checks":
                rule["parameters"]["required_status_checks"] = [{"context": "sudoo-checks"}]
            if rule["type"] == "pull_request":
                rule["parameters"]["require_code_owner_review"] = False
    preview = repo_guard.preview_rules(project, teams)
    production = next(t for t in preview["targets"] if t["key"] == "production")
    assert production["action"] == "update"
    assert {c["field"] for c in production["changes"]} == {"checks", "code_owner_review"}
    assert next(c for c in production["changes"] if c["field"] == "checks")["before"] == "sudoo-checks (any source)"
    with caplog.at_level("INFO", logger="sudoo.repo_guard.rules"):
        repo_guard.apply_rules(project, teams, "ada", confirm=REPO)
    lines = [r.getMessage() for r in caplog.records if r.name == "sudoo.repo_guard.rules"]
    assert len(lines) == 1 and REPO in lines[0] and "production" in lines[0]
    assert {t["action"] for t in repo_guard.preview_rules(project, teams)["targets"]} == {"unchanged"}


def test_code_owner_review_needs_someone_to_name(fake: FakeGitHub) -> None:
    project = _project()
    found = repo_guard.preview_rules(project, _rules(enabled=True))  # an organisation, roles only: nobody to name
    assert "no_code_owners" in {item["reason"] for item in found["limitations"]} and found["owners"] == []
    assert next(t for t in found["targets"] if t["key"] == "production")["after"]["code_owner_review"] is False
    off = repo_guard.preview_rules(project, _rules(enabled=True, production={"code_owner_review": False}))
    assert "no_code_owners" not in {item["reason"] for item in off["limitations"]}
    fake.owner_type = "User"  # a personal repository: its owner
    personal = repo_guard.preview_rules(project, _rules(enabled=True))
    assert personal["owners"] == ["@Acme"]
    assert next(t for t in personal["targets"] if t["key"] == "production")["after"]["code_owner_review"] is True


def test_branch_protection_pins_the_source_and_asks_for_owner_review(fake: FakeGitHub) -> None:
    project = _project()
    fake.rulesets_missing = True
    chosen = _rules(enabled=True, pushers={"mode": "named", "users": ["ada"], "teams": []})
    repo_guard.apply_rules(project, chosen, "ada", confirm=REPO)
    saved = fake.protections["main"]
    assert saved["required_status_checks"]["checks"] == [{"context": "sudoo-checks", "app_id": 15368}]
    assert saved["required_pull_request_reviews"]["require_code_owner_reviews"] is True  # @ada is named
    assert {t["action"] for t in repo_guard.preview_rules(project, chosen)["targets"]} == {"unchanged"}


def test_codeowners_file_follows_the_rules(fake: FakeGitHub) -> None:
    project = _project()
    teams = _rules(enabled=True, pushers={"mode": "named", "teams": ["release-managers"]})
    storage().put(repo_guard.COLLECTION, project["id"], {"rules": teams})
    opened = repo_guard.sync_checks(project, _checks(), "ada")
    assert opened["result"] == "opened" and files.CODEOWNERS in opened["pull"]["files"]
    assert fake.head is not None and fake.head[files.CODEOWNERS].endswith("/.github/ @Acme/release-managers\n")
    assert files.is_ours(fake.head[files.CODEOWNERS])
    only_owners = repo_guard.sync_checks(project, _checks(enabled=False), "ada")  # checks off, rules still on
    assert files.CODEOWNERS in only_owners["pull"]["files"] and files.WORKFLOW not in fake.head
    assert files.CODEOWNERS in fake.head


def test_only_github_actions_can_make_the_required_check(fake: FakeGitHub) -> None:
    project = _project()
    storage().put(repo_guard.COLLECTION, project["id"], {"checks": _checks(), "pull": PULL})
    fake.check_runs[SHA_OK] = [
        {"name": "sudoo-checks", "status": "completed", "conclusion": "success", "app": {"id": 777}},  # another app
        {"name": "Lint (ruff)", "status": "completed", "conclusion": "success", "app": {"id": 15368}},
    ]
    found = repo_guard.results(project, [SHA_OK])[SHA_OK]
    assert [r["name"] for r in found["runs"]] == ["Lint (ruff)"] and found["total"] == 1
    fake.check_runs[SHA_BAD] = [{"name": "sudoo-checks", "status": "completed", "conclusion": "failure",
                                 "app": {"id": 15368}}]  # fmt: skip
    assert repo_guard.results(project, [SHA_BAD])[SHA_BAD]["state"] == "failure"


# ---------------------------------------------------------------- results and promotion


def test_results_and_the_promotion_item(fake: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project()
    fake.check_runs[SHA_OK] = [{"name": "sudoo-checks", "status": "completed", "conclusion": "success",
                                "html_url": "https://github.com/x", "app": {"id": 15368}}]  # fmt: skip
    fake.check_runs[SHA_BAD] = [{"name": "Lint (ruff)", "status": "completed", "conclusion": "failure"},
                                {"name": "Odoo tests (18.0)", "status": "in_progress", "conclusion": None}]  # fmt: skip
    assert repo_guard.results(project, [SHA_OK]) == {}  # checks off: nothing is read
    assert promotion.ci_items(project, "qa") == []
    storage().put(repo_guard.COLLECTION, project["id"], {"checks": _checks(), "pull": PULL})
    found = repo_guard.results(project, [SHA_OK, SHA_BAD])
    assert found[SHA_OK]["state"] == "success" and found[SHA_OK]["total"] == 1  # the panel's own status left out
    assert found[SHA_BAD]["state"] == "failure" and found[SHA_BAD]["failed"] == 1 and found[SHA_BAD]["pending"] == 1
    calls = len(fake.calls)
    repo_guard.results(project, [SHA_OK])
    assert len(fake.calls) == calls  # read at most once a minute

    entry = {"type": "build", "status": "success", "commit": {"full_sha": SHA_BAD}}
    monkeypatch.setattr(promotion, "latest_build", lambda project_id, branch: entry)
    item = promotion.ci_items(project, "qa")[0]
    assert item["key"] == "ci" and item["status"] == "warning"  # failing, but promotion is not blocked
    storage().put(repo_guard.COLLECTION, project["id"], {"checks": _checks(), "pull": PULL, "block_promotion": True})
    assert promotion.ci_items(project, "qa")[0]["status"] == "blocked"
    entry["commit"] = {"full_sha": SHA_OK}
    assert promotion.ci_items(project, "qa")[0]["status"] == "ok"


# ---------------------------------------------------------------- the API: who may change it, and the audit log


def test_api_permissions_and_audit(app: Flask, admin: Client, operator: Client, fake: FakeGitHub) -> None:
    project = _project()
    client, headers = admin
    path = f"/api/v1/projects/{project['id']}/guard"
    view = client.get(path).get_json()["data"]
    assert view["may_change"] is True and view["checks"]["enabled"] is False and view["required_check"]

    other, other_headers = operator  # a developer: may read, never change
    assert other.get(path).get_json()["data"]["may_change"] is False
    refused = other.put(path + "/checks", json={"checks": {"enabled": True}}, headers=other_headers)
    assert refused.status_code in (403, 404)
    assert other.put(path + "/rules", json={"rules": {"enabled": True}, "confirm": REPO},
                     headers=other_headers).status_code in (403, 404)  # fmt: skip
    assert not fake.pulls and not fake.rulesets

    bad = client.put(path + "/checks", json={"checks": {"strictness": "extreme"}}, headers=headers)
    assert bad.status_code == 400
    opened = client.put(path + "/checks", json={"checks": {"enabled": True}}, headers=headers)
    assert opened.status_code == 200 and opened.get_json()["data"]["result"] == "opened"

    preview = client.post(path + "/rules/preview", json={"rules": {"enabled": True}}, headers=headers)
    assert preview.status_code == 200 and preview.get_json()["data"]["changes"] == 3
    unconfirmed = client.put(path + "/rules", json={"rules": {"enabled": True}}, headers=headers)
    assert unconfirmed.status_code == 400 and not fake.rulesets
    dry = client.put(path + "/rules", json={"rules": {"enabled": True}, "dry_run": True}, headers=headers)
    assert dry.status_code == 200 and dry.get_json()["data"]["result"] == "dry_run" and not fake.rulesets
    applied = client.put(path + "/rules", json={"rules": {"enabled": True}, "confirm": REPO}, headers=headers)
    assert applied.status_code == 200 and len(fake.rulesets) == 3
    assert client.patch(path, json={"block_promotion": True}, headers=headers).get_json()["data"]["block_promotion"]
    removed = client.delete(path + "/rules", json={"confirm": REPO}, headers=headers)
    assert removed.status_code == 200 and not fake.rulesets

    results = client.get(path + f"/results?sha={SHA_OK}")
    assert results.status_code == 200
    assert client.get(path + "/results?sha=nope").status_code == 400

    events = [e["event"] for e in audit.latest(50, user="admin1")]
    for expected in ("repository guard: checks on (opened)", "repository guard: branch rules applied",
                     "repository guard: failing checks block promotion", "repository guard: branch rules removed"):  # fmt: skip
        assert expected in events, expected


def test_named_teams_compare_by_name(fake: FakeGitHub) -> None:
    project = _project()
    teams = _rules(enabled=True, pushers={"mode": "named", "teams": ["release-managers"]})
    repo_guard.apply_rules(project, teams, "ada", confirm=REPO)
    found = repo_guard.preview_rules(project, teams)
    merges = next(t for t in found["targets"] if t["key"] == "production_merges")
    assert merges["action"] == "unchanged" and merges["after"]["pushers"] == "team release-managers"


# ---------------------------------------------------------------- defaults are panel settings


def test_defaults_come_from_the_panels_settings(fake: FakeGitHub) -> None:
    project = _project()
    try:
        live.change({"project_guard_checks": True, "project_guard_strictness": "strict",
                     "project_guard_production_approvals": 2, "project_guard_staging_approvals": 1,
                     "project_guard_linear_history": True, "project_guard_signed_commits": True,
                     "project_guard_pushers": "anyone", "project_guard_blocks_promotion": True})  # fmt: skip
        chosen = repo_guard.config(project["id"])
        assert chosen["checks"]["enabled"] is True and chosen["checks"]["strictness"] == "strict"
        production = chosen["rules"]["production"]
        assert production["approvals"] == 2 and production["linear_history"] and production["signed_commits"]
        assert chosen["rules"]["staging"]["approvals"] == 1 and chosen["rules"]["pushers"]["mode"] == "anyone"
        assert chosen["block_promotion"] is True
        # following the setting is not "active": nothing was proposed to the repository yet
        assert not repo_guard.checks_active(project["id"]) and promotion.ci_items(project, "qa") == []
        # a project keeps only what it changes; the rest keeps following the panel
        repo_guard.sync_checks(project, {**chosen["checks"], "linter": "flake8"}, "ada")
        assert repo_guard.stored(project["id"])["checks"] == {"linter": "flake8"}
        repo_guard.set_block_promotion(project, False, "ada")
        live.change({"project_guard_strictness": "relaxed", "project_guard_blocks_promotion": False})
        chosen = repo_guard.config(project["id"])
        assert chosen["checks"]["strictness"] == "relaxed" and chosen["checks"]["linter"] == "flake8"
        assert chosen["block_promotion"] is False
        view = repo_guard.describe(project)
        assert view["own"]["checks"] == {"linter": "flake8"} and view["defaults"]["checks"]["strictness"] == "relaxed"
        # the rules applied with the panel's approvals
        repo_guard.apply_rules(project, repo_guard.config(project["id"])["rules"], "ada", confirm=REPO)
        body = next(r for r in fake.rulesets.values() if r["name"] == rules.NAMES["production"])
        pull_rule = next(r for r in body["rules"] if r["type"] == "pull_request")
        assert pull_rule["parameters"]["required_approving_review_count"] == 2
        assert rules.NAMES["production_merges"] not in {r["name"] for r in fake.rulesets.values()}  # anyone
    finally:
        live.change(dict.fromkeys(live.overrides()))


def test_guard_settings_are_documented_and_checked() -> None:
    from pydantic import ValidationError

    from sudoo.config import Settings

    for bad in ({"project_guard_strictness": "extreme"}, {"project_guard_production_approvals": 11},
                {"project_guard_pushers": "named"}):  # fmt: skip
        with pytest.raises(ValidationError):
            Settings(**bad)
    configuration = (Path(__file__).resolve().parent.parent / "docs" / "CONFIGURATION.md").read_text()
    example = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
    for name in Settings.model_fields:
        if name.startswith("project_guard_"):
            assert f"`{name.upper()}`" in configuration and f"{name.upper()}=" in example
