"""Who can do what in a project: each account, its role there, where that role comes from, and its rights in every
stage (the Access tab's table, the access review e-mail). Also the switch of a project from the earlier access model
to project roles, which keeps everyone's access as close as it can and says what changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sudoo import accounts, roles, teams

from . import access, elevation, project_roles

SOURCE_LABELS = {
    "admin": "panel administrator",
    "grant": "given to them",
    "team": "a team",
    "github": "GitHub",
    "role": "their panel role",
    "none": "no access",
}


def _source_detail(user: Mapping[str, Any], project: Mapping[str, Any], source: str) -> str:
    """The team ("team Backend", "GitHub team acme/backend") or GitHub permission behind a role."""
    name = access.person(user)
    if source == "github":
        from sudoo.auth import github_access

        key = str(project.get("repository_id") or "")
        entry = (github_access._stored(name or "").get("repos") or {}).get(key) or {}
        via = f", via team {entry['via']}" if entry.get("via") else ""
        return f"GitHub: {entry.get('permission') or '?'}{via}"
    if source == "team":
        member_of = teams.of_user(name)
        github_teams = access._github_teams(name)
        found = []
        for grant in access.access_of(project).get("grants") or ():
            if grant.get("type") == "team" and grant.get("id") in member_of:
                team = teams.get(str(grant["id"])) or {}
                found.append(f"team {team.get('name') or grant['id']}")
            if grant.get("type") == "github_team" and str(grant.get("id") or "").lower() in github_teams:
                found.append(f"GitHub team {grant['id']}")
        return ", ".join(found) or "a team"
    return SOURCE_LABELS.get(source, source)


def of_project(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every account with its role, the role's source and its rights per stage, those with access first."""
    found = []
    for name, account in sorted(accounts.all_users().items()):
        user = {"name": name, "role": roles.normalize(str(account.get("role")))}
        level, source = (
            access.level_and_source(user, project) if access.model(project) == "project_roles" else
            (access.level(user, project), "role")
        )  # fmt: skip
        stages = {
            stage: project_roles.stage_list(access.permissions(user, project, stage=stage, with_lock=False)
                                            & access.PROJECT_PERMISSIONS, stage)  # fmt: skip
            for stage in project_roles.STAGES
        }
        if not access.sees_project(user, project):
            level, source = "none", "none"
        running = elevation.active(name, str(project.get("id")))
        found.append(
            {
                "user": name,
                "name": accounts.display_name(name, account),
                "panel_role": user["role"],
                "level": level,
                "source": source,
                "source_label": _source_detail(user, project, source),
                "stages": stages,
                "project": project_roles.stage_list(
                    access.permissions(user, project, with_lock=False) & frozenset(project_roles.PROJECT_RIGHTS),
                    "project",
                ),
                "temporary": [
                    {"id": r["id"], "stage": r["stage"], "level": r["level"], "expires": r.get("expires")}
                    for r in running
                ],
            }
        )
    return sorted(found, key=lambda p: (p["level"] == "none", -project_roles.RANK.get(p["level"], 0), p["name"]))


LEGACY_EQUIVALENT = {"viewer": "view", "developer": "developer", "maintainer": "maintainer"}


def conversion(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What switching the project to project roles does to each account (not administrators, who keep everything):
    the role it gets, kept by an individual grant where the default would differ, and whether its rights grow or
    shrink in some stage."""
    converted = {**project, "access": {**access.access_of(project), "model": "project_roles"}}
    changes = []
    for name, account in sorted(accounts.all_users().items()):
        user = {"name": name, "role": roles.normalize(str(account.get("role")))}
        if access.full_access(user):
            continue
        old_level = access.level(user, project)
        if not access.sees_project(user, project):
            target = "none"
        else:  # role ∩ grant: the lower of what the panel role and the project's level gave
            by_role = LEGACY_EQUIVALENT.get(user["role"], "none")
            target = min((by_role, old_level), key=lambda lv: project_roles.RANK.get(lv, 0))
        new_level = access.level(user, converted)
        before = access.permissions(user, project) & access.PROJECT_PERMISSIONS
        after_rights = {
            stage: sorted(project_roles.rights(converted, target, stage) - frozenset(project_roles.PROJECT_RIGHTS))
            for stage in project_roles.STAGES
        }
        changes.append(
            {
                "user": name,
                "name": accounts.display_name(name, account),
                "before": old_level if access.sees_project(user, project) else "none",
                "after": target,
                "grant": target != new_level,
                "lost": sorted({r for r in before if all(r not in after_rights[s] for s in after_rights)}),
            }
        )
    return changes


def convert(project_id: str, by: str) -> list[dict[str, Any]]:
    """Switch the project to project roles: individual grants keep each person's role where the default differs."""
    from sudoo import projects

    project = projects.get(project_id)
    if project is None:
        raise ValueError("This project does not exist.")
    changes = conversion(project)

    def apply(stored: dict[str, Any]) -> dict[str, Any]:
        current = dict(access.access_of(stored))
        grants = [g for g in current.get("grants") or () if not (g.get("type") == "user" and any(
            c["user"] == g.get("id") and c["grant"] for c in changes))]  # fmt: skip
        grants += [{"type": "user", "id": c["user"], "level": c["after"]} for c in changes if c["grant"]]
        current.update(model="project_roles", grants=sorted(grants, key=lambda e: (e["type"], e["id"])),
                       converted_by=by)  # fmt: skip
        stored["access"] = current
        return stored

    projects.change(project_id, apply)
    access.forget_restrictions()
    return changes
