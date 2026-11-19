"""Several servers (Business plans): what the core asks about other servers, answered by the premium package
"servers" when it runs, and by this server alone otherwise.

The panel is a one-server panel without that package: every instance is this server's (instances/store.py), a new
instance goes to this server, and asking for another server answers 402 with the plans that include it. With the
package, its `sudoo.premium.servers` entry point "servers" provides the object below (`provider()`), and the core
calls it at these points:

    instances on every server   names(), env(name), exists(name), remote(name), server_of(name), remote_names(),
                                remote_resource(name, full), remote_jobs(instance), job_server(job_id)
    servers                     reference(server_id), choices(), counted(), default_server(value)
    creating                    choose(requested, project), start_remote(server_id, plan, user),
                                start_planned(plan, user, server_id)
    automatic updates           request_update(name, reason) (an instance of another server)
    the web app                 install(app) (requests about another server's instances go to its agent; live
                                events of servers), add_routes(aiohttp app) (the agents' channels)

Agent mode
    On a server other than the main one, the panel's image runs as that server's agent (`sudoo.sh agent`,
    container sudoo-agent, premium/agent.py). The agent has no web page and listens on no port; it runs the same
    code for its own instances, for the person and with the permissions the main panel names, and takes the panel's
    decisions from what it last pushed (`PANEL`): the plan, the production instances of every server, the locked
    instances, the link deadline. `is_agent()` tells the core which of the two this process is.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from typing import Any

MAIN = "main"
"""The id of the main server (where the panel runs) wherever a server is named."""

PANEL: dict[str, Any] = {}
"""In an agent: what the main panel last pushed (entitlements, production instances and branches, locked instances,
the link deadline). Empty in the panel itself."""

FORWARDED_ENV = "sudoo.forwarded_user"
"""In an agent: the WSGI environment key with the person (and permissions) a request from the main panel runs for."""

SOCKET_ENV = "SUDOO_AGENT_SOCKET"
"""In an agent: the socket where git's credential helper asks the agent for a GitHub token from the main panel."""

_ROLE = ["panel"]


class Refused(Exception):
    """A new instance cannot go to the server asked for: (HTTP status, sentence for people)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def is_agent() -> bool:
    """Whether this process is a server's agent (`sudoo.sh agent`) rather than the panel."""
    return _ROLE[0] == "agent"


def become_agent() -> None:
    _ROLE[0] = "agent"


def provider() -> Any | None:
    """The servers package's hooks while it runs in the panel (never in an agent), else None."""
    if is_agent():
        return None
    from . import SERVERS, hook, loader

    if "servers" not in loader.activate():
        return None
    try:
        return hook(SERVERS, "servers")
    except Exception:  # noqa: BLE001 - a broken package leaves a one-server panel
        return None


# ---------------------------------------------------------------- instances on every server


def names() -> list[str]:
    """Every instance: this server's, and those other servers' agents reported."""
    found = provider()
    if found is not None:
        return list(found.names())
    from sudoo.instances import store

    return store.names()


def env(name: str) -> dict[str, str]:
    """The .env facts of an instance of any server ({} when there is none)."""
    from sudoo.instances import store

    if store.exists(name):
        return store.read_env(name)
    found = remote(name)
    return dict(found.get("env") or {}) if found else {}


def exists(name: str) -> bool:
    from sudoo.instances import store

    return store.exists(name) or remote(name) is not None


def remote(name: str) -> dict[str, Any] | None:
    """{"server", "env", ...} of an instance of another server, or None (this server's, or unknown)."""
    found = provider()
    return None if found is None else found.remote(name)


def server_of(name: str) -> str:
    found = provider()
    return MAIN if found is None else str(found.server_of(name))


def remote_names() -> list[str]:
    found = provider()
    return [] if found is None else list(found.remote_names())


def remote_resource(name: str, full: bool = False) -> dict[str, Any] | None:
    """The instance resource another server's agent last reported, as the panel shows it; None for this server's."""
    found = provider()
    return None if found is None else found.remote_resource(name, full)


def remote_jobs(instance: str | None = None) -> list[dict[str, Any]]:
    found = provider()
    return [] if found is None else list(found.remote_jobs(instance))


# ---------------------------------------------------------------- servers


def reference(server_id: str = MAIN) -> dict[str, Any]:
    """{"id", "name", "region", "online"} of the server an instance runs on, as instances show it."""
    found = provider()
    if found is not None:
        return dict(found.reference(server_id))
    from sudoo.config import settings

    return {"id": MAIN, "name": settings.server_name, "region": settings.server_region, "online": True}


def choices() -> list[dict[str, Any]]:
    """The servers a new instance may go to now (the main one first)."""
    found = provider()
    if found is not None:
        return list(found.choices())
    main = reference()
    return [{"id": MAIN, "name": main["name"], "region": main["region"]}]


def counted() -> int:
    """The servers the plan counts (heartbeat usage): this one, plus every joined server of the package."""
    found = provider()
    return 1 if found is None else int(found.counted())


def default_server(value: str | None) -> str | None:
    """A project's default server as given (None: the main server); 402 without the package, 400 when unknown."""
    if not value or value == MAIN:
        return None
    found = provider()
    if found is None:
        _refuse_premium("choosing another server")
    return found.default_server(value)  # type: ignore[union-attr]


# ---------------------------------------------------------------- creating


def choose(
    requested: str | None,
    project: Mapping[str, Any] | None = None,
    from_backup: str | None = None,
    from_production: str | None = None,
) -> str:
    """The server a new instance goes to: the one asked for, else the project's default server, else the main one.
    Raises Refused (or 402 without the package, when another server is asked for)."""
    found = provider()
    if found is None:
        if requested and requested != MAIN:
            _refuse_premium("creating an instance on another server")
        return MAIN
    return str(found.choose(requested, project, from_backup, from_production))


def start_remote(server_id: str, plan: dict[str, Any], user: str) -> dict[str, Any]:
    """Pass a planned creation to another server's agent; the job as the API shows it."""
    found = provider()
    if found is None:
        _refuse_premium("creating an instance on another server")
    return dict(found.start_remote(server_id, plan, user))  # type: ignore[union-attr]


def start_planned(plan: dict[str, Any], user: str, server_id: str = "") -> None:
    """Start a creation the panel planned itself (a development instance for a new branch): on the project's default
    server when the package runs and that server is connected, else here."""
    found = provider()
    if found is not None and server_id and server_id != MAIN:
        found.start_planned(plan, user, server_id)
        return
    from sudoo.instances import planning

    planning.start(plan, user)


def request_update(name: str, reason: str) -> None:
    """An automatic update for an instance of another server (its agent gets the new code); nothing otherwise."""
    found = provider()
    if found is not None:
        found.request_update(name, reason)


# ---------------------------------------------------------------- the web app


def install(app: Any) -> None:
    """Called once by create_app: the package's request forwarding and live events."""
    found = provider()
    if found is not None:
        found.install(app)


def add_routes(app: Any) -> None:
    """Called by the live event server (web/v1/events.py): the agents' channels."""
    found = provider()
    if found is not None:
        found.add_routes(app)


def _refuse_premium(what: str) -> None:
    from sudoo.web.security import refuse_without_premium

    refuse_without_premium("servers", what)


# ---------------------------------------------------------------- an agent's GitHub tokens


def token_from_agent(owner: str, timeout: float) -> str:
    """In git's credential helper on an agent: ask the agent process for a GitHub token of `owner` (from the main
    panel); "" when there is no agent socket."""
    path = os.environ.get(SOCKET_ENV, "")
    if not path:
        return ""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(path)
        client.sendall(owner.encode() + b"\n")
        return client.makefile("rb").readline().decode().strip()
