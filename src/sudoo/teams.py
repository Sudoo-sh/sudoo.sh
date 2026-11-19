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

Making teams and adding members come with the premium "teams" package (premium/); this module keeps what the core
needs whatever the plan: reading teams (the grants that name them stay enforced), taking members out and deleting a
team.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterable

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


def remove_members(team_id: str, names: Iterable[str], user: str) -> Document:
    """Take accounts out of a team (always possible, whatever the plan)."""
    gone = set(names)
    with _LOCK:
        found = get(team_id)
        if found is None:
            raise TeamError("This team does not exist.", 404)
        team = dict(found)
        team["members"] = [m for m in team.get("members") or [] if m not in gone]
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
