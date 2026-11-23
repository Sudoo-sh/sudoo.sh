"""Project roles: what each level may do in each stage of a project (production, staging, development), as a matrix
an administrator of the project can change.

    Viewer      sees that branches and instances exist, their status, history and builds
    Tester      opens staging and development instances in Odoo to try them (Connect), and sees their history;
                nothing in production
    Developer   development: everything; staging: see and test, no data (no shell, database, backups, passwords);
                production: only that it exists, its history and status
    Maintainer  development and staging: everything, staging data included; production: logs, monitoring, connect
                (with the usual confirmation), backups (list, download), restart and new code; never production's
                shell or database, restores, deletion, the project's settings, access or locks
    Admin       everything in the project (fixed: not editable)

Rows are permissions of roles.py that apply inside a project (projects/access.PROJECT_PERMISSIONS); those about the
project as a whole (its settings, access and locks, its resources) are in the "project" scope instead of a stage.
A project keeps only the cells changed from the defaults ("matrix" in its access document), so later versions can
improve the defaults of everything else. The matrix only ever applies inside the project; panel-wide rights
(accounts, settings, tokens, ...) stay with the panel role (roles.py).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

STAGES = ("production", "staging", "development")
SCOPES = (*STAGES, "project")
LEVELS = ("none", "view", "tester", "developer", "maintainer", "admin")
EDITABLE_LEVELS = ("view", "tester", "developer", "maintainer")
LABELS = {
    "none": "No access",
    "view": "Viewer",
    "tester": "Tester",
    "developer": "Developer",
    "maintainer": "Maintainer",
    "admin": "Admin",
}

STAGE_RIGHTS: tuple[str, ...] = (
    "instances.view",
    "instances.connect",
    "instances.logs",
    "instances.update",
    "instances.operate",
    "instances.create",
    "instances.delete",
    "instances.credentials",
    "instances.shell",
    "instances.database_shell",
    "backups.manage",
    "instances.restore",
    "instances.upgrade",
    "caught_mail.view",
    "caught_mail.manage",
    "instances.profile",
    "instances.resources",
    "instances.move",
    "domains.manage",
    "offsite.restore",
    "offsite.delete",
)
"""Rights that depend on the stage, in the order the matrix shows them."""

PROJECT_RIGHTS: tuple[str, ...] = ("projects.manage", "projects.access", "projects.resources")
"""Rights about the project as a whole."""

RIGHT_LABELS = {
    "instances.view": "see branches, status, history and builds",
    "instances.connect": "open Odoo signed in (Connect)",
    "instances.logs": "read logs and monitoring",
    "instances.update": "restart, start and get new code",
    "instances.operate": "pause, switch off, back up, change automation",
    "instances.create": "create instances",
    "instances.delete": "delete instances",
    "instances.credentials": "see Odoo passwords and ports",
    "instances.shell": "open the Odoo container's shell",
    "instances.database_shell": "open the database (SQL)",
    "backups.manage": "list and download backups",
    "instances.restore": "restore and roll back",
    "instances.upgrade": "upgrade the database to a newer Odoo",
    "caught_mail.view": "read caught e-mails",
    "caught_mail.manage": "delete and release caught e-mails",
    "instances.profile": "use the code profiler",
    "instances.resources": "change instance resources",
    "instances.move": "move instances to another server",
    "domains.manage": "manage custom domains",
    "offsite.restore": "restore from off-site copies",
    "offsite.delete": "delete off-site copies",
    "projects.manage": "change the project's settings and stages",
    "projects.access": "manage access, roles and locks",
    "projects.resources": "change the project's resources",
}

_SEE = frozenset({"instances.view"})
_TEST = _SEE | {"instances.connect"}
_DEVELOP_DEV = frozenset(STAGE_RIGHTS) - {
    "instances.upgrade",
    "instances.move",
    "instances.resources",
    "domains.manage",
    "offsite.restore",
    "offsite.delete",
}
_PRODUCTION_MAINTAIN = _SEE | {"instances.connect", "instances.logs", "backups.manage", "instances.update"}

DEFAULTS: dict[str, dict[str, frozenset[str]]] = {
    "none": {scope: frozenset() for scope in SCOPES},
    "view": {"production": _SEE, "staging": _SEE, "development": _SEE, "project": frozenset()},
    "tester": {"production": frozenset(), "staging": _TEST, "development": _TEST, "project": frozenset()},
    "developer": {
        "production": _SEE,
        "staging": _TEST | {"instances.logs"},
        "development": _DEVELOP_DEV,
        "project": frozenset(),
    },
    "maintainer": {
        "production": _PRODUCTION_MAINTAIN,
        "staging": _DEVELOP_DEV,
        "development": _DEVELOP_DEV,
        "project": frozenset(),
    },
    "admin": {**{stage: frozenset(STAGE_RIGHTS) for stage in STAGES}, "project": frozenset(PROJECT_RIGHTS)},
}

RANK = {level: index for index, level in enumerate(LEVELS)}


def _rights_of(scope: str) -> tuple[str, ...]:
    return PROJECT_RIGHTS if scope == "project" else STAGE_RIGHTS


def overrides(project: Mapping[str, Any] | None) -> dict[str, dict[str, list[str]]]:
    raw = ((project or {}).get("access") or {}).get("matrix") or {}
    return raw if isinstance(raw, dict) else {}


def matrix(project: Mapping[str, Any] | None) -> dict[str, dict[str, frozenset[str]]]:
    """The project's matrix: the defaults with its own changes."""
    found = {level: dict(scopes) for level, scopes in DEFAULTS.items()}
    for level, scopes in overrides(project).items():
        if level not in EDITABLE_LEVELS or not isinstance(scopes, dict):
            continue
        for scope, chosen in scopes.items():
            if scope in SCOPES and isinstance(chosen, list):
                found[level][scope] = frozenset(r for r in chosen if r in _rights_of(scope))
    return found


def rights(project: Mapping[str, Any] | None, level: str, stage: str | None) -> frozenset[str]:
    """What `level` may do in `stage` of the project (with the project-wide rights); stage None: in any stage."""
    found = matrix(project).get(level) or DEFAULTS["none"]
    project_wide = found.get("project", frozenset())
    if stage in STAGES:
        return found[stage] | project_wide
    return frozenset().union(*(found[s] for s in STAGES)) | project_wide


def checked(raw: Any) -> dict[str, dict[str, list[str]]]:
    """A matrix sent to the API, as the changes from the defaults to keep. Raises ValueError."""
    if not isinstance(raw, dict):
        raise ValueError('matrix: {"<level>": {"<stage>": ["<right>", ...]}}.')
    kept: dict[str, dict[str, list[str]]] = {}
    for level, scopes in raw.items():
        if level not in EDITABLE_LEVELS:
            raise ValueError(f"matrix: {str(level)[:20]!r} cannot be changed; levels: {', '.join(EDITABLE_LEVELS)}.")
        if not isinstance(scopes, dict):
            raise ValueError(f"matrix.{level}: one list of rights per stage.")
        for scope, chosen in scopes.items():
            if scope not in SCOPES or not isinstance(chosen, list):
                raise ValueError(f"matrix.{level}: {str(scope)[:20]!r} is not one of {', '.join(SCOPES)}.")
            unknown = [r for r in chosen if r not in _rights_of(scope)]
            if unknown:
                raise ValueError(f"matrix.{level}.{scope}: unknown rights {', '.join(map(str, unknown))[:120]}.")
            ordered = [r for r in _rights_of(scope) if r in chosen]
            if frozenset(ordered) != DEFAULTS[level][scope]:
                kept.setdefault(str(level), {})[str(scope)] = ordered
    return kept


def describe(project: Mapping[str, Any] | None) -> dict[str, Any]:
    """The matrix as the Access tab shows it: per level and scope, the rights in effect and whether changed."""
    current, changed = matrix(project), overrides(project)
    return {
        "levels": [level for level in LEVELS if level != "none"],
        "editable": list(EDITABLE_LEVELS),
        "stages": list(SCOPES),
        "rights": {"stage": list(STAGE_RIGHTS), "project": list(PROJECT_RIGHTS)},
        "labels": RIGHT_LABELS,
        "matrix": {
            level: {scope: sorted(current[level][scope]) for scope in SCOPES} for level in LEVELS if level != "none"
        },
        "changed": {level: sorted(scopes) for level, scopes in changed.items() if level in EDITABLE_LEVELS},
    }


def stage_list(granted: frozenset[str], scope: str) -> list[str]:
    return [r for r in _rights_of(scope) if r in granted]
