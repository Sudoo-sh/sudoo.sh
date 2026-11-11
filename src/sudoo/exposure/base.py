"""The interface every exposure plugin implements: how the panel and the instances are reached from outside.

The panel and each instance's gateway listen on a local port. An exposure plugin turns that port into an address
people can open, for example through a Cloudflare tunnel or by publishing the port on the server's own address.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Target:
    """Something to expose: the panel (name "panel") or an instance, and the local port it listens on."""

    name: str
    port: int
    env: Mapping[str, str] = field(default_factory=dict)  # the instance's .env (empty for the panel)


@runtime_checkable
class Exposure(Protocol):
    """How a local port becomes a public address."""

    listen_host: str
    """Interface the panel and the gateways listen on: 127.0.0.1 when a connector reaches them locally,
    0.0.0.0 when visitors connect to the server directly."""

    https: bool
    """True when visitors use https, so cookies can be marked Secure."""

    def instance_ports(self) -> dict[str, int]:
        """Extra ports this plugin needs per instance, as {.env key: first port}; reserved when an instance is created."""
        ...

    def panel_env(self) -> dict[str, str]:
        """Values the plugin needs for the panel's own target (like an instance's .env)."""
        ...

    def ensure(self, target: Target) -> None:
        """Make the target reachable (start what it needs); does nothing when it already is."""
        ...

    def remove(self, target: Target) -> None:
        """Stop exposing the target (when an instance is deleted)."""
        ...

    def address(self, target: Target) -> str | None:
        """The target's current public address, or None while it is not ready yet."""
        ...
