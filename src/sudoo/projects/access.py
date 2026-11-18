"""Who may do what in a project: the scope teams and per-project access will attach to.

Every check about a project or one of its branches goes through `allowed(user, permission, project)`, never straight
to the role, so that per-project access can be added here without touching the callers. A project records its access
mode in its document ("access"):

    panel_roles   (today, every project) the panel-wide roles of roles.py apply in the project: whoever may create
                  instances may create them for its branches, and so on

Later modes (teams, members per project and per branch, locked branches) are added here: `allowed` then also reads
the project's members and the branch's lock. Projects a user may not see are left out of lists (`visible`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sudoo import roles

MODES = ("panel_roles",)


def allowed(user: Mapping[str, str], permission: str, project: Mapping[str, Any] | None = None) -> bool:
    """Whether `user` ({"name", "role"}) has `permission` in `project` (None: outside every project)."""
    mode = ((project or {}).get("access") or {}).get("mode", "panel_roles")
    if mode not in MODES:  # an access mode this panel does not know: nothing is allowed rather than everything
        return False
    return permission in roles.permissions(user["role"])


def visible(user: Mapping[str, str], found: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The projects of `found` the user may see."""
    return [project for project in found if allowed(user, "instances.view", project)]
