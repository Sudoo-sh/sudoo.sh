"""What the core answers for a premium feature whose package is absent (or switched off): the safe answer. Support
access without its package grants nothing at all; the audit log still recognises support's past entries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import SUPPORT, hook, loaded

SUPPORT_NAME_PREFIX = "Sudoo.sh support ("  # support's synthetic user, as audit entries name it


def support() -> Any | None:
    """The support package's hooks (grant_of, permissions, level_in, chosen_instance, start_channel, active_users),
    or None."""
    return hook(SUPPORT, "support") if loaded("support") else None


def support_grant(user: Mapping[str, Any]) -> dict[str, Any] | None:
    found = support()
    return None if found is None else found.grant_of(user)


def support_permissions(user: Mapping[str, Any]) -> frozenset[str]:
    found = support()
    return frozenset() if found is None else frozenset(found.permissions(user))


def support_level(user: Mapping[str, Any], project: Mapping[str, Any] | None, branch: str | None) -> str:
    found = support()
    return "none" if found is None else str(found.level_in(user, project, branch))


def support_chosen_instance(user: Mapping[str, Any], name: str) -> bool:
    found = support()
    return False if found is None else bool(found.chosen_instance(user, name))


def support_users() -> list[Mapping[str, Any]]:
    """The user support acts as under each active grant (to ask what it could open): the package's "active_users"
    hook; none without the package, or with an earlier one that does not provide it."""
    found = support()
    if found is None:
        return []
    listed = getattr(found, "active_users", None)
    return list(listed()) if callable(listed) else []


def support_entry(name: object) -> bool:
    """Whether an audit entry's user is support's synthetic user."""
    return isinstance(name, str) and name.startswith(SUPPORT_NAME_PREFIX)
