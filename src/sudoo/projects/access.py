"""Who may do what in a project and in each of its branches: panel roles, teams, per-project and per-branch grants,
locked branches. Every check about a project, a branch or an instance of a project goes through this module.

Effective permissions
    What someone may do in a project is their panel role's permissions (roles.py) limited by the level they have in
    that project: role ∩ grant. A grant never gives more than the role: a viewer granted "admin" is still a viewer.

        none        nothing: the project (or the branch) is not shown
        view        instances.view, instances.logs
        developer   view + get new code, restart, start, submodules, caught e-mails, the Odoo container's shell
        maintainer  developer + create, operate, restore, credentials, the project's settings and stages
        admin       every permission that applies inside a project (PROJECT_PERMISSIONS), access and locks included

    Permissions that are about the whole panel (accounts, tokens, settings, the audit log, plugins, GitHub, off-site
    storage, teams) never depend on a project: the role alone decides.

A project's access (its "access" document)
    mode        panel_roles   (the default, and every project made before teams existed) everyone keeps the
                              access their role gives; grants only change it for those they name, a "none" grant
                              hides the project from them
                restricted    only the teams and people granted a level see the project
    grants      [{"type": "team" | "user", "id", "level"}]
    branches    {branch: {"restricted": bool, "grants": [...]}}: a branch may give its own levels (they replace the
                project's for those they name), and a restricted branch is seen only by those its own grants name
                (production visible to leads only)
    locks       {branch: {"by", "at", "reason", "allow": [{"type", "id"}]}}: a locked branch

    For an account linked to GitHub (auth/github_access.py), the permission on the project's repository gives a
    level too: their own grant still wins, otherwise the higher of GitHub's level and their teams'.

    The level that applies to a person: their own grant when there is one (an individual grant always wins, also a
    "none"), else the highest level of their teams' grants; the branch's grants first, then the project's; with no
    grant at all, "admin" (role decides) in a panel_roles project and "none" in a restricted project or branch.

Sudoo.sh support
    acts as a synthetic user (support/): its grant decides its level (support.level_in: "admin" inside the grant's
    scope, "none" outside), it is in no team and no lock allows it, so locked branches stay locked to it. Instances
    outside every project (and creating projects or instances outside one) are within a grant for "all" only, or
    for an instance the grant names itself.

Super admins
    always have the level "admin" in every project and branch: grants and restricted modes never hide anything from
    them (they could give themselves access anyway, and they must be able to repair a project nobody can see). Locks
    do apply to them: they must unlock the branch first (which they always may), and that is audited.

Locked branches
    Nobody may open (credentials, logs, the shell, caught e-mails), change, update, back up, restore or delete the
    instances of a locked branch, or change the branch's stage or create an instance of it, except the people and
    teams the lock allows (allowed people still need the permission through their role and level). Everyone who may
    see the branch still sees it, with the lock shown. Locking and unlocking need projects.access in the project;
    unlocking (or changing who is allowed) also needs to be allowed by the lock, except for super admins. The panel's
    own safety work goes on: daily backups (production every day), monitoring, certificates. Automatic changes
    stop: pushes do not update a locked branch's instances or rebuild its staging, an expired lifetime does not
    delete them, a deleted branch on GitHub does not archive or delete them, and a visit does not start a paused one.
    A locked production branch keeps serving.

API tokens
    A token acts for the account that created it (its owner): its permissions are its scope's role ∩ the owner's
    role, and the owner's teams and grants apply. A token whose owner no longer exists keeps its scope's role but has
    no grants and no teams: restricted projects and locked branches are closed to it.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import Any

from sudoo import accounts, roles, teams

from . import project_roles

MODES = ("panel_roles", "restricted")
MODELS = ("project_roles", "legacy")
LEVELS = project_roles.LEVELS
GRANT_TYPES = ("team", "user", "github_team")

PROJECT_PERMISSIONS = frozenset(
    {
        "instances.view",
        "instances.logs",
        "instances.update",
        "instances.operate",
        "instances.create",
        "instances.restore",
        "instances.upgrade",
        "instances.delete",
        "instances.credentials",
        "instances.connect",
        "instances.resources",
        "instances.move",
        "instances.shell",
        "instances.database_shell",
        "instances.profile",
        "caught_mail.view",
        "caught_mail.manage",
        "backups.manage",
        "offsite.restore",
        "offsite.delete",
        "domains.manage",
        "projects.manage",
        "projects.access",
        "projects.resources",
    }
)
"""Permissions that apply inside a project (and its branches and instances); the others are panel-wide."""

_VIEW = frozenset({"instances.view", "instances.logs"})
_DEVELOP = _VIEW | {"instances.update", "caught_mail.view", "instances.shell", "instances.profile"}
_MAINTAIN = _DEVELOP | {
    "instances.operate",
    "instances.restore",
    "instances.create",
    "instances.credentials",
    "instances.connect",
    "projects.manage",
}
LEVEL_PERMISSIONS: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "view": _VIEW,
    "tester": _VIEW,
    "developer": _DEVELOP,
    "maintainer": _MAINTAIN,
    "admin": PROJECT_PERMISSIONS,
}
"""The levels of a project that keeps the access model of earlier versions ("legacy": role ∩ level, the same in
every stage); projects with project roles use the stage matrix of project_roles.py instead."""

DEFAULT_LEVEL = {
    "super_admin": "admin",
    "admin": "admin",
    "maintainer": "maintainer",
    "developer": "developer",
    "viewer": "view",
    "member": "none",
}
"""Project roles: the level a person's panel role gives in a project open to panel roles where nothing else names
them (no grant, no team, no GitHub permission). Members get nothing by themselves."""
LOCKED_PERMISSIONS = PROJECT_PERMISSIONS - {"instances.view"}
"""What a lock takes from everyone it does not allow."""


# ---------------------------------------------------------------- who is asking


def person(user: Mapping[str, Any]) -> str | None:
    """The account behind `user`: the account itself, or a token's owner (None when the owner no longer exists, and
    for Sudoo.sh support: no account, no team, never allowed by a lock)."""
    if user.get("support"):
        return None
    if user.get("token_id"):
        owner = user.get("owner")
        return str(owner) if owner and accounts.get(str(owner)) is not None else None
    return str(user["name"])


def _person_role(user: Mapping[str, Any]) -> str | None:
    name = person(user)
    account = accounts.get(name) if name else None
    return roles.normalize(account["role"]) if account else None


def is_super_admin(user: Mapping[str, Any]) -> bool:
    """A super admin, or a token whose owner is one (never Sudoo.sh support)."""
    if user.get("support"):
        return False
    if user.get("token_id"):
        return _person_role(user) == "super_admin"
    return user.get("role") == "super_admin"


def role_permissions(user: Mapping[str, Any]) -> frozenset[str]:
    """The role's permissions; for a token, its scope's role ∩ its owner's role (when the owner exists); for
    Sudoo.sh support, what its grant allows while it is active (support.permissions). In a server's agent, a request
    the main panel passes on carries exactly what the panel allows (premium/servers.py)."""
    if user.get("support"):
        from sudoo.premium import stubs

        return stubs.support_permissions(user)
    forwarded = user.get("permissions")
    if user.get("via") == "panel" and isinstance(forwarded, list | tuple | frozenset):
        from sudoo.premium import servers

        if servers.is_agent():
            return frozenset(str(p) for p in forwarded)
    found = roles.permissions(str(user["role"]))
    if user.get("token_id"):
        owner_role = _person_role(user)
        if owner_role is not None:
            found = found & roles.permissions(owner_role)
    return found


def reach_permissions(user: Mapping[str, Any]) -> frozenset[str]:
    """What `user` may do somewhere: their role's permissions and, under project roles, the rights they have in any
    project. The gate of endpoints that list or act across projects (each item is checked again)."""
    found = role_permissions(user)
    if user.get("support") or full_access(user):
        return found
    from sudoo import projects

    for project in projects.all_projects():
        if model(project) == "project_roles":
            found |= permissions(user, project, with_lock=False) & PROJECT_PERMISSIONS
    return found


# ---------------------------------------------------------------- a project's access


def access_of(project: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = (project or {}).get("access") or {}
    return raw if isinstance(raw, dict) else {}


def mode(project: Mapping[str, Any] | None) -> str:
    return str(access_of(project).get("mode") or "panel_roles")


def model(project: Mapping[str, Any] | None) -> str:
    """ "project_roles" (stage-aware roles, project_roles.py) or "legacy" (role ∩ level, the model of projects made
    before project roles existed, until an admin switches them; also any project that does not say)."""
    return "project_roles" if access_of(project).get("model") == "project_roles" else "legacy"


def _roles_model(user: Mapping[str, Any], project: Mapping[str, Any] | None) -> bool:
    """Whether project roles decide `user`'s rights in `project` (Sudoo.sh support keeps its own rules)."""
    return project is not None and not user.get("support") and model(project) == "project_roles"


def full_access(user: Mapping[str, Any]) -> bool:
    """Super admins and administrators (or a token of one) have every right in every project; locks still apply."""
    if user.get("support"):
        return False
    role = _person_role(user) if user.get("token_id") else roles.normalize(str(user.get("role")))
    return role in roles.ADMINISTRATORS


def _default_level(user: Mapping[str, Any]) -> str:
    role = _person_role(user) if user.get("token_id") else roles.normalize(str(user.get("role")))
    return DEFAULT_LEVEL.get(role or "", "none")


def stage_of(project: Mapping[str, Any] | None, branch: str | None) -> str | None:
    """The stage of a project's branch (None without a branch: rights in any stage count)."""
    if project is None or not branch:
        return None
    from sudoo import projects

    return projects.branch_stage(project, branch)


def _pick(grants: Iterable[Mapping[str, Any]], name: str | None, member_of: frozenset[str]) -> str | None:
    """The level `grants` give: the person's own grant first, else the highest of their teams' (panel teams, and
    GitHub teams "org/slug" they belong to on GitHub); None when none applies."""
    best: str | None = None
    github_teams: frozenset[str] | None = None
    for grant in grants:
        level = grant.get("level")
        if level not in LEVELS:
            continue
        if grant.get("type") == "user" and name is not None and grant.get("id") == name:
            return str(level)
        better = best is None or LEVELS.index(str(level)) > LEVELS.index(best)
        if grant.get("type") == "team" and grant.get("id") in member_of and better:
            best = str(level)
        if grant.get("type") == "github_team" and better:
            if github_teams is None:
                github_teams = _github_teams(name)
            if str(grant.get("id") or "").lower() in github_teams:
                best = str(level)
    return best


def _github_teams(name: str | None) -> frozenset[str]:
    from sudoo.auth import github_access

    return github_access.teams_of(name)


def branch_access(project: Mapping[str, Any] | None, branch: str | None) -> dict[str, Any]:
    if not branch:
        return {}
    found = (access_of(project).get("branches") or {}).get(branch) or {}
    return found if isinstance(found, dict) else {}


def level(user: Mapping[str, Any], project: Mapping[str, Any] | None, branch: str | None = None) -> str:
    """The level `user` has in `project` (and `branch`): see the module's description."""
    if user.get("support"):
        from sudoo.premium import stubs

        return stubs.support_level(user, project, branch)
    if project is None or is_super_admin(user):
        return "admin"
    if mode(project) not in MODES:  # an access mode this panel does not know: nothing rather than everything
        return "none"
    return level_and_source(user, project, branch)[0]


def level_and_source(user: Mapping[str, Any], project: Mapping[str, Any], branch: str | None = None) -> tuple[str, str]:
    """(level, where it comes from: "grant", "team", "github", "role", "admin" or "none")."""
    if _roles_model(user, project) and full_access(user):
        return "admin", "admin"
    if mode(project) not in MODES:
        return "none", "none"
    name = person(user)
    member_of = teams.of_user(name)
    own = branch_access(project, branch)
    found = _pick(own.get("grants") or (), name, member_of) if own else None
    if found is None and own.get("restricted"):
        return "none", "none"
    if found is None:
        found = _pick(access_of(project).get("grants") or (), name, member_of)
    source = "grant" if found is not None and _individual(project, branch, name) else "team"
    from_github = _github_level(name, project)
    if from_github is not None:  # auth/github_access.py: an individual grant wins, else the higher of GitHub and teams
        if found is not None and source == "grant":
            return found, "grant"
        if found is None or LEVELS.index(from_github) > LEVELS.index(found):
            return from_github, "github"
        return found, "team"
    if found is not None:
        return found, source
    if mode(project) == "restricted":
        return "none", "none"
    if _roles_model(user, project):
        return _default_level(user), "role"
    return "admin", "role"


def _github_level(name: str | None, project: Mapping[str, Any]) -> str | None:
    from sudoo.auth import github_access

    return github_access.level_for(name, project)


def _individual(project: Mapping[str, Any], branch: str | None, name: str | None) -> bool:
    """Whether the person has a grant of their own in the branch or the project (it then wins over GitHub)."""
    if name is None:
        return False
    lists = [branch_access(project, branch).get("grants") or (), access_of(project).get("grants") or ()]
    return any(g.get("type") == "user" and g.get("id") == name for grants in lists for g in grants)


def lock_of(project: Mapping[str, Any] | None, branch: str | None) -> dict[str, Any] | None:
    """The lock of a branch, or None."""
    if not branch:
        return None
    found = (access_of(project).get("locks") or {}).get(branch)
    return found if isinstance(found, dict) else None


def lock_allows(user: Mapping[str, Any], lock: Mapping[str, Any]) -> bool:
    """Whether the lock lets `user` (a token: its owner) through."""
    name = person(user)
    member_of = teams.of_user(name)
    for entry in lock.get("allow") or ():
        if entry.get("type") == "user" and name is not None and entry.get("id") == name:
            return True
        if entry.get("type") == "team" and entry.get("id") in member_of:
            return True
    return False


def permissions(
    user: Mapping[str, Any],
    project: Mapping[str, Any] | None,
    branch: str | None = None,
    stage: str | None = None,
    with_lock: bool = True,
) -> frozenset[str]:
    """Everything `user` may do in `project` (and `branch`, whose stage is `stage` or looked up), panel-wide
    permissions included."""
    from_role = role_permissions(user)
    if project is None:
        if user.get("support") and level(user, None) == "none":  # outside every project: only a grant for "all"
            return from_role - PROJECT_PERMISSIONS
        return from_role
    if _roles_model(user, project):
        found = (from_role - PROJECT_PERMISSIONS) | _project_rights(user, project, branch, stage)
    else:
        found = (from_role - PROJECT_PERMISSIONS) | (from_role & LEVEL_PERMISSIONS[level(user, project, branch)])
    lock = lock_of(project, branch)
    if with_lock and lock is not None and not lock_allows(user, lock):
        found -= LOCKED_PERMISSIONS
    return found


def _project_rights(
    user: Mapping[str, Any], project: Mapping[str, Any], branch: str | None, stage: str | None
) -> frozenset[str]:
    """Project roles: the matrix's rights of the person's level in the branch's stage, with their running temporary
    access (elevation.py); a token is also limited to its scope. The panel's switches still apply (shells, the
    profiler)."""
    from sudoo.config import settings

    from . import elevation

    stage = stage or stage_of(project, branch)
    if full_access(user):
        found = PROJECT_PERMISSIONS
    else:
        found = project_roles.rights(project, level(user, project, branch), stage)
        found |= elevation.rights(person(user), project, stage)
    if user.get("token_id"):
        found &= roles.permissions(str(user["role"]))
    if not settings.shell_enabled:
        found -= roles.SHELL_PERMISSIONS
    if not settings.profiler_enabled:
        found -= {roles.PROFILE}
    return found & PROJECT_PERMISSIONS


def allowed(
    user: Mapping[str, Any],
    permission: str,
    project: Mapping[str, Any] | None = None,
    branch: str | None = None,
    stage: str | None = None,
) -> bool:
    """Whether `user` ({"name", "role"}, a token also "token_id" and "owner") has `permission` in `project` (None:
    outside every project) and `branch` (None: the project as a whole)."""
    return permission in permissions(user, project, branch, stage)


def denial(user: Mapping[str, Any], permission: str, project: Mapping[str, Any], stage: str | None) -> str:
    """The sentence a refused right shows under project roles: who may not do what where, and what to do."""
    found = level(user, project)
    who = project_roles.LABELS.get(found, found)
    what = project_roles.RIGHT_LABELS.get(permission, roles.PERMISSIONS.get(permission, permission).lower())
    where = f" in {stage}" if stage else ""
    name = project.get("name") or project.get("id")
    plural = "People without a role" if found == "none" else f"{who}s"
    return (
        f"{plural} of {name} cannot {what}{where}. Ask an admin of the project for temporary access "
        "(the project's Access tab, Request access)."
    )


def refusal(
    user: Mapping[str, Any],
    permission: str,
    project: Mapping[str, Any] | None = None,
    branch: str | None = None,
    stage: str | None = None,
) -> tuple[int, str] | None:
    """Why `permission` is refused (HTTP status, a sentence for people), or None when it is allowed. 404 when the
    project or branch is not even visible, so its existence is not told."""
    if permission in PROJECT_PERMISSIONS and project is not None and _roles_model(user, project):
        return _roles_refusal(user, permission, project, branch, stage)
    if permission not in role_permissions(user):
        return 403, f"Your role cannot do this ({roles.PERMISSIONS[permission].lower()})."
    if permission not in PROJECT_PERMISSIONS:
        return None
    if project is None:
        if user.get("support") and level(user, None) == "none":
            return 403, "Support access is limited to the projects and instances it was given."
        return None
    seen = level(user, project, branch)
    if seen == "none":
        return 404, "This project does not exist." if branch is None else "There is no such branch."
    if permission not in LEVEL_PERMISSIONS[seen]:
        return 403, f"Your access to {project.get('name', project.get('id'))} does not include this ({seen})."
    lock = lock_of(project, branch)
    if lock is not None and permission in LOCKED_PERMISSIONS and not lock_allows(user, lock):
        by = lock.get("by") or "an administrator"
        return 403, f"The branch {branch} is locked by {by}: only the people and teams the lock allows may do this."
    return None


def _roles_refusal(
    user: Mapping[str, Any], permission: str, project: Mapping[str, Any], branch: str | None, stage: str | None
) -> tuple[int, str] | None:
    stage = stage or stage_of(project, branch)
    found = permissions(user, project, branch, stage)
    if permission in found:
        return None
    unlocked = permissions(user, project, branch, stage, with_lock=False)
    if "instances.view" not in unlocked and (branch is not None or not sees_project(user, project)):
        return 404, "This project does not exist." if branch is None else "There is no such branch."
    lock = lock_of(project, branch)
    if permission in unlocked and lock is not None:
        by = lock.get("by") or "an administrator"
        return 403, f"The branch {branch} is locked by {by}: only the people and teams the lock allows may do this."
    return 403, denial(user, permission, project, stage)


def sees_project(user: Mapping[str, Any], project: Mapping[str, Any]) -> bool:
    """Whether the project is shown to `user`: they see it as a whole, or one of its branches is open to them."""
    if _roles_model(user, project):
        if "instances.view" in permissions(user, project, with_lock=False):
            return True
        return any(sees_branch(user, project, branch) for branch in access_of(project).get("branches") or {})
    if "instances.view" not in role_permissions(user):
        return False
    if level(user, project) != "none":
        return True
    return any(level(user, project, branch) != "none" for branch in access_of(project).get("branches") or {})


def visible(user: Mapping[str, Any], found: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The projects of `found` the user may see."""
    return [project for project in found if sees_project(user, project)]


def sees_branch(user: Mapping[str, Any], project: Mapping[str, Any], branch: str) -> bool:
    if _roles_model(user, project):
        return "instances.view" in permissions(user, project, branch, with_lock=False)
    return "instances.view" in role_permissions(user) and level(user, project, branch) != "none"


OPENS_DATA = frozenset(
    {"instances.credentials", "instances.shell", "instances.database_shell", "backups.manage", "caught_mail.view"}
)
"""Permissions that open an instance's data to whoever has them."""


def _everyone() -> list[dict[str, Any]]:
    """Every principal that can act on the panel, as the panel builds it for a request: each account, each API token
    (its owner's teams and grants, or none when its owner is gone) and Sudoo.sh support under each active grant."""
    from sudoo import api_tokens
    from sudoo.premium import stubs
    from sudoo.web.security import SCOPE_ROLES

    found: list[dict[str, Any]] = [
        {"name": name, "role": roles.normalize(account.get("role", ""))}
        for name, account in accounts.all_users().items()
    ]
    for token in api_tokens.all_tokens():
        found.append(
            {
                "name": f"api:{token.get('name')}",
                "role": SCOPE_ROLES.get(str(token.get("scope")), "viewer"),
                "token_id": str(token.get("id")),
                "owner": str(token.get("created_by") or ""),
            }
        )
    found.extend(dict(user) for user in stubs.support_users())
    return found


def copy_reveals(project: Mapping[str, Any], source_branch: str, target_branch: str) -> list[str]:
    """Who would get into data of `source_branch` they cannot open there, if it were copied into the instances of
    `target_branch` (an automatic staging rebuild, with nobody asking): the accounts, API tokens and Sudoo.sh support
    grants that may open the target's data in a way the source does not let them. Empty when the copy shows nobody
    anything new."""
    found = []
    for user in _everyone():
        opened = permissions(user, project, target_branch) & OPENS_DATA
        if opened - permissions(user, project, source_branch):
            found.append(str(user["name"]))
    return found


def manages(user: Mapping[str, Any], project: Mapping[str, Any], branch: str | None = None) -> bool:
    """Whether `user` manages the access of the project (and branch), locks left aside."""
    if _roles_model(user, project):
        return "projects.access" in permissions(user, project, branch, with_lock=False)
    return "projects.access" in (role_permissions(user) & LEVEL_PERMISSIONS[level(user, project, branch)])


def may_unlock(user: Mapping[str, Any], project: Mapping[str, Any], branch: str) -> bool:
    """Unlocking (or changing who the lock allows) needs projects.access in the project and the lock's own
    permission, except for super admins."""
    if not manages(user, project, branch):
        return False
    lock = lock_of(project, branch)
    return lock is None or is_super_admin(user) or lock_allows(user, lock)


# ---------------------------------------------------------------- instances


def scope_of_instance(name: str) -> tuple[Mapping[str, Any] | None, str | None]:
    """(project, branch) of an instance, or (None, None) for one outside every project (or that does not exist)."""
    from sudoo import projects

    project_id = _project_id_of(name)
    found = projects.get(project_id) if project_id else None
    if found is None:
        return None, None
    return found, projects.branch_of(name) or None


def _project_id_of(name: str) -> str | None:
    from sudoo.core import jobs
    from sudoo.instances import store

    if store.exists(name):
        return store.read_env(name).get("PROJECT") or None
    from sudoo.premium import servers

    remote = servers.remote(name)
    if remote is not None:
        return (remote.get("env") or {}).get("PROJECT") or None
    job = jobs.creating(name)
    return str(job.details.get("project") or "") or None if job else None


def _support_scope(user: Mapping[str, Any], name: str) -> tuple[Mapping[str, Any] | None, str | None, bool]:
    """(project, branch, chosen): an instance support's grant names itself is open to it whatever restricts its
    project or branch (locks still apply)."""
    project, branch = scope_of_instance(name)
    if user.get("support"):
        from sudoo.premium import stubs

        return project, branch, stubs.support_chosen_instance(user, name)
    return project, branch, False


def _chosen_permissions(
    user: Mapping[str, Any], project: Mapping[str, Any] | None, branch: str | None
) -> frozenset[str]:
    found = role_permissions(user)
    lock = lock_of(project, branch)
    return found - LOCKED_PERMISSIONS if lock is not None else found


def instance_stage(name: str, project: Mapping[str, Any] | None) -> str | None:
    """The stage an instance of a project runs as (its own, which may differ from its branch's while it changes)."""
    if project is None or model(project) != "project_roles":
        return None
    from sudoo import projects

    return projects.stage_of_instance(name)


def allowed_instance(user: Mapping[str, Any], permission: str, name: str) -> bool:
    project, branch, chosen = _support_scope(user, name)
    if chosen:
        return permission in _chosen_permissions(user, project, branch)
    return allowed(user, permission, project, branch, instance_stage(name, project))


def instance_refusal(user: Mapping[str, Any], permission: str, name: str) -> tuple[int, str] | None:
    project, branch, chosen = _support_scope(user, name)
    if chosen:
        if permission in _chosen_permissions(user, project, branch):
            return None
        if permission not in role_permissions(user):
            return 403, f"Your role cannot do this ({roles.PERMISSIONS[permission].lower()})."
        return 403, f"The branch {branch} is locked: support access does not open locked branches."
    found = refusal(user, permission, project, branch, instance_stage(name, project))
    hidden = project is None and user.get("support") and level(user, None) == "none"  # outside support's grant
    if found and (found[0] == 404 or hidden):
        return 404, "This instance does not exist."
    return found


def sees_instance(user: Mapping[str, Any], name: str) -> bool:
    return allowed_instance(user, "instances.view", name)


def instance_lock(name: str) -> dict[str, Any] | None:
    """The lock of the branch an instance follows, or None (automatic work asks this before changing it). In a
    server's agent: the locks the main panel pushed (premium/servers.py)."""
    from sudoo.premium import servers

    if servers.is_agent():
        return {"locked_by": "panel"} if name in (servers.PANEL.get("locked") or ()) else None
    project, branch = scope_of_instance(name)
    return lock_of(project, branch)


def visible_instances(user: Mapping[str, Any], names: Iterable[str]) -> list[str]:
    """The instances of `names` the user may see (cheap when nothing restricts them)."""
    names = list(names)
    if sees_everything(user):
        return names
    return [name for name in names if sees_instance(user, name)]


_RESTRICTION: list[tuple[float, bool]] = []  # (when, answer): any_restriction is asked for every live event


def any_restriction() -> bool:
    """Whether any project has grants, a restricted mode or restricted branches (locks do not hide anything);
    remembered for a second."""
    from sudoo import projects

    now = time.monotonic()
    if _RESTRICTION and now - _RESTRICTION[0][0] < 1:
        return _RESTRICTION[0][1]
    found = False
    for project in projects.all_projects():
        current = access_of(project)
        if model(project) == "project_roles":  # roles differ per stage: nothing is open to everyone by itself
            found = True
            break
        if current.get("mode", "panel_roles") != "panel_roles" or current.get("grants") or current.get("branches"):
            found = True
            break
    _RESTRICTION[:] = [(now, found)]
    return found


def forget_restrictions() -> None:
    """A project's access changed: ask again."""
    _RESTRICTION.clear()


def sees_everything(user: Mapping[str, Any]) -> bool:
    """Whether nothing is hidden from `user` (the fast path of lists and live events)."""
    if user.get("support"):
        return False
    if is_super_admin(user):
        return "instances.view" in role_permissions(user)
    from sudoo.auth import github_access

    if github_access.applies(person(user)):
        return False
    return "instances.view" in role_permissions(user) and not any_restriction()


def visible_map(user: Mapping[str, Any], found: Mapping[str, Any]) -> dict[str, Any]:
    """`found` ({instance name: anything}) without the instances hidden from `user`."""
    if sees_everything(user):
        return dict(found)
    shown = set(visible_instances(user, found))
    return {name: value for name, value in found.items() if name in shown}


# ---------------------------------------------------------------- what the API shows


def describe_lock(lock: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if lock is None:
        return None
    return {
        "locked_by": lock.get("by"),
        "locked_at": lock.get("at"),
        "reason": lock.get("reason") or "",
        "allow": [{"type": e.get("type"), "id": e.get("id")} for e in lock.get("allow") or ()],
    }


def forget_team(team_id: str) -> list[str]:
    """Take a deleted team out of every grant and lock; returns the ids of the projects that changed."""
    return _forget("team", team_id)


def forget_user(name: str) -> list[str]:
    """Take a deleted account out of every grant and lock; returns the ids of the projects that changed."""
    return _forget("user", name)


def _forget(kind: str, value: str) -> list[str]:
    from sudoo import projects

    def keep(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [dict(e) for e in entries if not (e.get("type") == kind and e.get("id") == value)]

    def named(found: Mapping[str, Any]) -> bool:
        lists = [found.get("grants") or ()]
        lists += [v.get("grants") or () for v in (found.get("branches") or {}).values()]
        lists += [v.get("allow") or () for v in (found.get("locks") or {}).values()]
        return any(e.get("type") == kind and e.get("id") == value for entries in lists for e in entries)

    changed = []
    for project in projects.all_projects():
        if not named(access_of(project)):
            continue

        def update(stored: dict[str, Any]) -> dict[str, Any]:
            current = dict(access_of(stored))
            current["grants"] = keep(current.get("grants") or ())
            current["branches"] = {
                b: {**v, "grants": keep(v.get("grants") or ())} for b, v in (current.get("branches") or {}).items()
            }
            current["locks"] = {
                b: {**v, "allow": keep(v.get("allow") or ())} for b, v in (current.get("locks") or {}).items()
            }
            stored["access"] = current
            return stored

        projects.change(project["id"], update)
        changed.append(project["id"])
    return changed
