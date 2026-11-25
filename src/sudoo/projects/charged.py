"""Whose running-instance limit an instance made by automation counts against (the person's own limit, see
instances/capacity.py; the panel-wide limit always counts it too).

A webhook is signed, so GitHub sent it, but only the numeric id of the GitHub account in its "sender" field is GitHub's
own statement of who acted. A name, e-mail address or commit author in the payload is typed by whoever made the commit
and is never used for this. The order:

    1. the panel account linked to the sender's GitHub id, when that account exists and has access to the project
    2. else the account that created the project (the project has no separate owner field)
    3. else "system": no person is charged, only the panel-wide limit applies

`how` says which one decided, for the audit log and the refusal text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sudoo import accounts, roles

SYSTEM = "system"


def sender_id(payload: Mapping[str, Any]) -> int | None:
    """The GitHub id of the account that caused a webhook event, or None."""
    value = (payload.get("sender") or {}).get("id") if isinstance(payload.get("sender"), Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _usable(name: str) -> dict[str, Any] | None:
    """The account, unless it is missing or disabled. A disabled person (accounts.disable) is not charged."""
    account = accounts.get(name)
    if not account or accounts.is_disabled(account):
        return None
    return account


def of(project: Mapping[str, Any], github_id: int | None = None) -> tuple[str, str]:
    """(account name or "system", "sender", "creator" or "none") for an instance automation creates in `project`."""
    from sudoo.auth import github
    from sudoo.projects import access

    if github_id is not None:
        name = github.linked_account(github_id)
        account = _usable(name) if name else None
        if name and account:
            user = {"name": name, "role": roles.normalize(str(account.get("role")))}
            if access.level(user, project) != "none":
                return name, "sender"
    creator = str(project.get("created_by") or "")
    if creator and _usable(creator):
        return creator, "creator"
    return SYSTEM, "none"


def label(name: str, how: str) -> str:
    """Said where the person is told whose limit an automatic instance used."""
    return {"sender": f"{name} (the person who pushed or opened it)", "creator": f"{name} (the project's creator)"}.get(
        how, "nobody (only the panel-wide limit)"
    )
