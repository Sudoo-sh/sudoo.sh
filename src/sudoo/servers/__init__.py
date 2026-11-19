"""Several servers managed from one panel: the main server (where the panel runs) and other servers, each running a
small agent (docs/ARCHITECTURE.md "Several servers").

    trust.py      keys, fingerprints, join codes, signed connection requests and signed frames
    registry.py   the panel's list of servers: join codes, joining, approval, revocation, limits from the plan
    links.py      the panel's side of each agent's channel (a WebSocket the agent opens): requests, reports, events
    inventory.py  every instance of every server (names, facts, server), for lists, names and counts
    forward.py    API requests about an instance of another server, passed to its agent
    agent.py      the agent: joins, connects out to the panel, answers its requests, runs its server on its own
    placement.py  which server a new instance goes to, and each server's room
    move.py       moving an instance to another server (backup, copy, restore, check; rolled back on failure)

This module itself is imported by core modules: it only says whether this process is an agent, and what the main
panel last told it (`PANEL`).
"""

from __future__ import annotations

from typing import Any

MAIN = "main"
"""The id of the main server (where the panel runs) in every place a server is named."""

PANEL: dict[str, Any] = {}
"""In an agent: what the main panel last pushed (entitlements, production instances, locked instances, the link
deadline). Empty in the panel itself."""

_ROLE = ["panel"]


def is_agent() -> bool:
    """Whether this process is a server's agent (`sudoo.sh agent`) rather than the panel."""
    return _ROLE[0] == "agent"


def become_agent() -> None:
    _ROLE[0] = "agent"
