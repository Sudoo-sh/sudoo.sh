"""Project access from GitHub: for an account linked to GitHub, a project's level follows the person's permission on
the project's repository (GITHUB_ACCESS_FROM_REPOSITORIES, with GITHUB_SIGN_IN).

    GitHub permission        level in the project (projects/access.py)
    admin                    admin
    maintain                 maintainer
    write (push)             developer
    triage, read             view
    none                     none: the project is not shown

How it combines with the panel's own rules (projects/access.level):
    - an individual grant to the person always wins (also a "none"): administrators can override GitHub per person;
    - otherwise the higher of GitHub's level and the person's teams' grants;
    - a restricted branch still needs its own grant;
    - the person's panel role is the most they can do anyway (role ∩ level);
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
BY_ROLE = {"admin": "admin", "maintain": "maintainer", "write": "developer", "triage": "view", "read": "view"}
BY_PERMISSION = {"admin": "admin", "write": "developer", "read": "view", "none": "none"}

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
            after[key] = {"level": found[0], "permission": found[1], "checked": now, "covered": True}
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
    return str(entry.get("level") or "none")


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
                "level": entry.get("level") if entry.get("covered", True) else None,
                "covered": entry.get("covered", True) if entry else None,
                "checked": entry.get("checked"),
            }
        )
    return {"enabled": active(), "repository": project.get("repository"), "people": people}


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
    """Every minute: read again the permissions that are due (background.py)."""
    while True:
        time.sleep(60)
        for name in due():
            with contextlib.suppress(Exception):  # tried again next time
                refresh(name)
