"""Project access from GitHub: for an account linked to GitHub, a project's level follows the person's permission on
the project's repository (GITHUB_ACCESS_FROM_REPOSITORIES, with GITHUB_SIGN_IN).

    GitHub permission        project role (projects/access.py, project_roles.py)
    admin                    Admin
    maintain                 Maintainer
    write (push)             Developer
    triage                   Tester (Viewer in a project that keeps the earlier access model)
    read                     Viewer or Tester, as the project chose (GITHUB_READ_ROLE for projects made from now on;
                             a project made earlier keeps Tester until a project Admin changes it)
    none                     none: the project is not shown

GitHub computes the permission from everything that gives it: the person's own invitation, the organisation's base
permission and the repository's teams. When a team of the repository gives it, the "who can do what" view names
the team ("via"). Teams of a GitHub organisation can also be given a project role directly on the Access tab
(grants of type "github_team", "org/team-slug"): their members are read with the installation token
(GET /orgs/{org}/teams/{slug}/members, Organization permission Members: read) as often as the permissions, so
joining or leaving the team on GitHub changes the access in the panel.

How it combines with the panel's own rules (projects/access.level):
    - an individual grant to the person always wins (also a "none"): administrators can override GitHub per person;
    - otherwise the highest of GitHub's level, the person's panel teams' grants and their GitHub teams' grants;
    - a restricted branch still needs its own grant;
    - locks still apply;
    - super admins and administrators are not limited by it (they manage the panel).

The permission is read with the GitHub App's installation token (GET /repos/{owner}/{repo}/collaborators/{login}/
permission, which needs only Metadata: read), never with a token of the person: the panel keeps no user token. It is
read at each sign-in of the account and again every GITHUB_ACCESS_CHECK_MINUTES (background.py), and kept in the
panel's storage (collection "github_access"). A repository no installation of the App covers is not checked: the
panel's own rules apply to it. A permission that could not be read is kept as it was; one never read counts as
"none", and one older than three intervals (one hour at least) too, so losing access on GitHub never keeps access
in the panel for long. Every change is in the audit log.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Mapping
from typing import Any

from sudoo import accounts
from sudoo.config import settings
from sudoo.core import audit
from sudoo.repos import github_app
from sudoo.roles import ADMINISTRATORS
from sudoo.storage import storage

from . import github

COLLECTION = "github_access"
BY_ROLE = {"admin": "admin", "maintain": "maintainer", "write": "developer", "triage": "tester", "read": "tester"}
BY_PERMISSION = {"admin": "admin", "write": "developer", "read": "tester", "none": "none"}
TEAMS = "github_teams"  # "org/slug" -> {"members": [logins, lower case], "checked"}
REPOSITORY_TEAMS = "github_repository_teams"  # "owner/repo" -> {"teams": [{"id": "org/slug", "permission"}]}
_teams: dict[str, dict[str, Any]] | None = None

_cache: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_refreshing: set[str] = set()
_asked: dict[str, float] = {}


def active() -> bool:
    return bool(settings.github_sign_in and settings.github_access_from_repositories) and github.configured()


def applies(name: str | None) -> bool:
    """Whether GitHub decides the project levels of this account (linked, not an administrator, rule on)."""
    if not name or not active():
        return False
    account = accounts.get(name) or {}
    return github.link_of(account) is not None and account.get("role") not in ADMINISTRATORS


def level_of(answer: Mapping[str, Any]) -> tuple[str, str]:
    """(level, GitHub's word for it) from GitHub's answer about a collaborator's permission."""
    perms = (answer.get("user") or {}).get("permissions") or {}
    role = str(answer.get("role_name") or "")
    if role in BY_ROLE:
        return BY_ROLE[role], role
    if perms:
        for key, word in (("admin", "admin"), ("maintain", "maintain"), ("push", "write"), ("triage", "triage"),
                          ("pull", "read")):  # fmt: skip
            if perms.get(key):
                return BY_ROLE[word], word
        return "none", "none"
    permission = str(answer.get("permission") or "none")
    return BY_PERMISSION.get(permission, "none"), permission


READ_ROLES = ("viewer", "tester")


def read_role(project: Mapping[str, Any] | None) -> str:
    """What GitHub's "read" gives in this project: "viewer" or "tester". A project that does not say keeps
    "tester" (the behaviour before the setting existed), so nobody loses access by an upgrade."""
    from sudoo.projects import access

    chosen = access.access_of(project).get("github_read_role")
    return chosen if chosen in READ_ROLES else "tester"


def project_level(entry: Mapping[str, Any], project: Mapping[str, Any]) -> str | None:
    """The level a stored reading gives in `project`: GitHub's read follows the project's choice."""
    level = entry.get("level")
    if level == "tester" and entry.get("permission") == "read" and read_role(project) == "viewer":
        return "view"
    return str(level) if level else None


def check(repository: str, login: str) -> tuple[str, str] | None:
    """(level, permission) of `login` on `repository` ("Owner/Repo"), or None when it cannot be read: no installation
    covers the owner ("not covered" is told apart by the caller), or GitHub failed."""
    owner, _, repo = repository.partition("/")
    token = github_app.token_for(owner)
    if token is None:
        return None
    try:
        answer = github_app.request("GET", f"/repos/{owner}/{repo}/collaborators/{login}/permission", bearer=token)
    except github_app.GitHubError as error:
        if error.status == 404:  # not a collaborator (or no such user any more)
            return "none", "none"
        return None
    return level_of(answer if isinstance(answer, dict) else {})


def _stored(name: str) -> dict[str, Any]:
    with _lock:
        if name not in _cache:
            _cache[name] = storage().get(COLLECTION, name) or {}
        return _cache[name]


def forget(name: str) -> None:
    """The account was unlinked or deleted."""
    storage().delete(COLLECTION, name)
    with _lock:
        _cache.pop(name, None)


def refresh(name: str) -> list[str]:
    """Read the account's permission on every project's repository again; returns the projects whose level changed."""
    from sudoo import projects

    account = accounts.get(name) or {}
    link = github.link_of(account)
    if link is None:
        forget(name)
        return []
    before = dict(_stored(name).get("repos") or {})
    after: dict[str, Any] = {}
    changed: list[str] = []
    now = time.time()
    for project in projects.all_projects():
        repository = str(project.get("repository") or "")
        key = str(project.get("repository_id") or repository.lower())
        if "/" not in repository:
            continue
        owner = repository.split("/")[0]
        if github_app.installation_for(owner) is None:
            after[key] = {"level": None, "permission": None, "checked": now, "covered": False}
        else:
            found = check(repository, str(link["login"]))
            if found is None:
                if key in before:
                    after[key] = {**before[key], "failed": now}
                continue
            after[key] = {"level": found[0], "permission": found[1], "checked": now, "covered": True,
                          "via": _via_team(repository, str(link["login"]), found[1])}  # fmt: skip
        old = (before.get(key) or {}).get("level")
        if after[key]["level"] != old:
            changed.append(str(project.get("id")))
            audit.record(
                name,
                "",
                "project access from GitHub changed",
                detail=f"{project.get('id')}: {old or 'unknown'} -> {after[key]['level'] or 'not checked'}"
                f" ({after[key].get('permission') or 'repository not covered by the GitHub App'})",
            )
    record = {"login": link["login"], "checked": now, "repos": after}
    storage().put(COLLECTION, name, record)
    with _lock:
        _cache[name] = record
    if changed:
        from sudoo.projects import access

        access.forget_restrictions()
    return changed


def refresh_later(name: str) -> None:
    """Refresh in the background, at most once a minute per account (a project nobody's level is known for yet)."""
    now = time.time()
    with _lock:
        if name in _refreshing or now - _asked.get(name, 0) < 60:
            return
        _refreshing.add(name)
        _asked[name] = now

    def run() -> None:
        try:
            refresh(name)
        except Exception:  # noqa: BLE001 - tried again at the next interval
            pass
        finally:
            with _lock:
                _refreshing.discard(name)

    threading.Thread(target=run, daemon=True, name="github-access").start()


def max_age() -> float:
    return max(3 * settings.github_access_check_minutes * 60, 3600)


def level_for(name: str | None, project: Mapping[str, Any]) -> str | None:
    """The level GitHub gives `name` in `project`, or None when GitHub does not decide it (rule off, account not
    linked or an administrator, repository not covered by the App)."""
    if not applies(name) or name is None:
        return None
    key = str(project.get("repository_id") or str(project.get("repository") or "").lower())
    if not key:
        return None
    entry = (_stored(name).get("repos") or {}).get(key)
    if entry is None:
        refresh_later(name)
        return "none"
    if entry.get("covered") is False:
        return None
    if time.time() - float(entry.get("checked", 0)) > max_age():
        refresh_later(name)
        return "none"
    return project_level(entry, project) or "none"


def describe(project: Mapping[str, Any]) -> dict[str, Any]:
    """What the project's Access tab shows: whether the rule is on, and each linked person's permission and level."""
    key = str(project.get("repository_id") or str(project.get("repository") or "").lower())
    people = []
    for name, account in sorted(accounts.all_users().items()):
        link = github.link_of(account)
        if link is None:
            continue
        entry = (_stored(name).get("repos") or {}).get(key) or {}
        people.append(
            {
                "user": name,
                "name": accounts.display_name(name, account),
                "github_login": link.get("login"),
                "applies": applies(name),
                "permission": entry.get("permission"),
                "level": project_level(entry, project) if entry.get("covered", True) else None,
                "covered": entry.get("covered", True) if entry else None,
                "checked": entry.get("checked"),
                "via": entry.get("via"),
            }
        )
    return {"enabled": active(), "repository": project.get("repository"), "people": people,
            "read_role": read_role(project)}  # fmt: skip


def due() -> list[str]:
    """Accounts whose permissions are older than GITHUB_ACCESS_CHECK_MINUTES."""
    if not active():
        return []
    interval = settings.github_access_check_minutes * 60
    now = time.time()
    return [
        name
        for name, account in accounts.all_users().items()
        if github.link_of(account) is not None
        and account.get("role") not in ADMINISTRATORS
        and now - float(_stored(name).get("checked", 0)) >= interval
    ]


def run_forever() -> None:
    """Every minute: read again the permissions and GitHub teams that are due (background.py); access e-mails go to
    the people whose role changed (projects/notices.py)."""
    while True:
        time.sleep(60)
        with contextlib.suppress(Exception):
            refresh_due()


def refresh_due() -> None:
    from sudoo import projects
    from sudoo.projects import notices

    names = due()
    if not names and not github.configured():
        return
    with notices.watch([p["id"] for p in projects.all_projects()], by="GitHub"):
        if github.configured():
            refresh_teams()
        for name in names:
            with contextlib.suppress(Exception):  # tried again next time
                refresh(name)


# ---------------------------------------------------------------- GitHub teams


def _team_members(team: str) -> list[str] | None:
    """The logins (lower case) of a GitHub team "org/slug", read with the organisation's installation token; None when
    it cannot be read (no installation, GitHub failed). A team that does not exist has no members."""
    org, _, slug = team.partition("/")
    token = github_app.token_for(org)
    if token is None or not slug:
        return None
    found: list[str] = []
    for page in range(1, 11):
        try:
            answer = github_app.request(
                "GET", f"/orgs/{org}/teams/{slug}/members?per_page=100&page={page}", bearer=token
            )
        except github_app.GitHubError as error:
            return [] if error.status == 404 else None
        batch = [str(m.get("login") or "").lower() for m in answer or [] if isinstance(m, dict)]
        found.extend(b for b in batch if b)
        if len(batch) < 100:
            break
    return found


def _stored_teams() -> dict[str, dict[str, Any]]:
    global _teams
    with _lock:
        if _teams is None:
            _teams = storage().all(TEAMS)
        return _teams


def granted_teams() -> set[str]:
    """Every GitHub team named in a grant of a project (lower case)."""
    from sudoo import projects
    from sudoo.projects import access

    found: set[str] = set()
    for project in projects.all_projects():
        current = access.access_of(project)
        lists = [current.get("grants") or ()]
        lists += [rule.get("grants") or () for rule in (current.get("branches") or {}).values()]
        found |= {str(g.get("id") or "").lower() for grants in lists for g in grants if g.get("type") == "github_team"}
    return found


def refresh_team(team: str) -> bool:
    """Read a team's members again; True when they changed."""
    team = team.lower()
    members = _team_members(team)
    if members is None:
        return False
    before = (_stored_teams().get(team) or {}).get("members")
    record = {"members": sorted(set(members)), "checked": time.time()}
    storage().put(TEAMS, team, record)
    with _lock:
        if _teams is not None:
            _teams[team] = record
    if before is not None and sorted(before) != record["members"]:
        audit.record("github", "", "GitHub team members changed", detail=f"{team}: {len(before)} -> {len(members)}")
    return before is None or sorted(before) != record["members"]


def refresh_teams(force: bool = False) -> list[str]:
    """Read again the members of every granted team that is due; the teams whose members changed."""
    interval = settings.github_access_check_minutes * 60
    now = time.time()
    changed = []
    for team in sorted(granted_teams()):
        checked = float((_stored_teams().get(team) or {}).get("checked") or 0)
        if (force or now - checked >= interval) and refresh_team(team):
            changed.append(team)
    return changed


def teams_of(name: str | None) -> frozenset[str]:
    """The granted GitHub teams the account belongs to, by its linked GitHub login (never for administrators: they
    have every right anyway). Members read longer ago than three intervals count as none."""
    if not name or not github.configured():
        return frozenset()
    account = accounts.get(name) or {}
    link = github.link_of(account)
    if link is None:
        return frozenset()
    login = str(link.get("login") or "").lower()
    oldest = time.time() - max_age()
    return frozenset(
        team
        for team, record in _stored_teams().items()
        if float(record.get("checked") or 0) >= oldest and login in (record.get("members") or ())
    )


def _via_team(repository: str, login: str, permission: str) -> str | None:
    """The repository's team that gives `login` this permission, when one does (shown as "via team ...")."""
    owner, _, repo = repository.partition("/")
    token = github_app.token_for(owner)
    if token is None or permission in ("none", ""):
        return None
    try:
        teams = github_app.request("GET", f"/repos/{owner}/{repo}/teams?per_page=100", bearer=token) or []
    except github_app.GitHubError:
        return None
    for team in teams if isinstance(teams, list) else []:
        if not isinstance(team, dict) or team.get("permission") not in (
            permission,
            {"write": "push", "read": "pull"}.get(permission),
        ):
            continue
        name = f"{owner}/{team.get('slug')}".lower()
        members = (_stored_teams().get(name) or {}).get("members")
        if members is None:
            members = _team_members(name) or []
        if login.lower() in members:
            return name
    return None
