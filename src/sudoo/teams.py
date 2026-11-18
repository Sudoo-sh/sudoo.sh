"""Teams: named groups of accounts that per-project and per-branch access is given to (projects/access.py).

A team is a document in the storage (collection "teams", key: the team's id):

    id              "backend-leads" (letters, digits, hyphens; made from the name, never changes)
    name            "Backend leads", what people read (unique, case aside)
    description     a sentence, optional
    members         the account names in it, sorted
    created_at, created_by, changed_at, changed_by

A team gives nothing by itself: it is named in a project's or a branch's grants (projects/access.py), and a member's
permissions there are their panel role's permissions, limited by the level the team has (role ∩ grant). Deleting an
account takes it out of every team; deleting a team takes it out of every grant and lock.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterable
from typing import Any

from sudoo.instances.naming import slug
from sudoo.storage import Document, storage

COLLECTION = "teams"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
NAME_MAX = 60
DESCRIPTION_MAX = 200
MAX_MEMBERS = 500
_LOCK = threading.RLock()


class TeamError(Exception):
    """A change to a team that is not allowed; the message is meant for people."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def all_teams() -> list[Document]:
    """Every team, by name."""
    return sorted(storage().all(COLLECTION).values(), key=lambda t: (str(t.get("name", "")).lower(), t["id"]))


def get(team_id: str) -> Document | None:
    return storage().get(COLLECTION, team_id) if team_id and ID_RE.match(team_id) else None


def of_user(name: str | None) -> frozenset[str]:
    """The ids of the teams `name` is a member of."""
    if not name:
        return frozenset()
    return frozenset(t["id"] for t in storage().all(COLLECTION).values() if name in (t.get("members") or ()))


def _new_id(name: str) -> str:
    base = slug(name)[:36].strip("-") or "team"
    taken = set(storage().all(COLLECTION))
    found, counter = base, 2
    while found in taken:
        found, counter = f"{base[:33].strip('-')}-{counter}", counter + 1
    return found


def _clean_name(name: str, other_than: str | None = None) -> str:
    shown = " ".join(name.split())
    if not shown:
        raise TeamError("Give the team a name.", 400)
    if len(shown) > NAME_MAX:
        raise TeamError(f"A team's name is at most {NAME_MAX} characters.", 400)
    if any(t["id"] != other_than and str(t.get("name", "")).lower() == shown.lower() for t in all_teams()):
        raise TeamError(f"There is a team called {shown} already.")
    return shown


def _clean_members(members: Iterable[str], known: Iterable[str]) -> list[str]:
    wanted = sorted({str(m) for m in members})
    if len(wanted) > MAX_MEMBERS:
        raise TeamError(f"A team has at most {MAX_MEMBERS} members.", 400)
    unknown = sorted(set(wanted) - set(known))
    if unknown:
        raise TeamError(f"There is no account {unknown[0][:60]}.", 400)
    return wanted


def create(name: str, description: str, members: Iterable[str], known: Iterable[str], user: str) -> Document:
    with _LOCK:
        shown = _clean_name(name)
        now = round(time.time())
        team: Document = {
            "id": _new_id(shown),
            "name": shown,
            "description": description.strip()[:DESCRIPTION_MAX],
            "members": _clean_members(members, known),
            "created_at": now,
            "created_by": user,
            "changed_at": now,
            "changed_by": user,
        }
        storage().put(COLLECTION, team["id"], team)
        return team


def change(team_id: str, user: str, known: Iterable[str], **values: Any) -> Document:
    """Change a team's name, description or members (only those given)."""
    with _LOCK:
        found = get(team_id)
        if found is None:
            raise TeamError("This team does not exist.", 404)
        team = dict(found)
        if "name" in values:
            team["name"] = _clean_name(str(values["name"]), other_than=team_id)
        if "description" in values:
            team["description"] = str(values["description"] or "").strip()[:DESCRIPTION_MAX]
        if "members" in values:
            team["members"] = _clean_members(values["members"], known)
        team |= {"changed_at": round(time.time()), "changed_by": user}
        storage().put(COLLECTION, team_id, team)
        return team


def remove(team_id: str) -> Document:
    """Delete a team; the grants and locks that name it are cleaned by the caller (projects/access.forget_team)."""
    with _LOCK:
        found = get(team_id)
        if found is None:
            raise TeamError("This team does not exist.", 404)
        storage().delete(COLLECTION, team_id)
        return found


def forget_member(name: str) -> list[str]:
    """Take a deleted account out of every team; returns the ids of the teams it was in."""
    changed = []
    with _LOCK:
        for team in all_teams():
            if name in (team.get("members") or ()):
                team = dict(team)
                team["members"] = [m for m in team["members"] if m != name]
                storage().put(COLLECTION, team["id"], team)
                changed.append(team["id"])
    return changed
