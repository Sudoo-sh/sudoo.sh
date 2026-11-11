"""Exposure plugin "host-ports": visitors connect straight to a port on the server's own address.

For servers that can accept incoming connections (a VPS with a public address, a machine on the office network).
The panel listens on PANEL_PORT and each instance's gateway on its GATEWAY_PORT, on every interface; the address is
<PUBLIC_SCHEME>://<PUBLIC_HOST>:<port>. Nothing runs besides the panel, and addresses never change.

Open those ports in the firewall. Use PUBLIC_SCHEME=https only when a TLS proxy in front of the server terminates
https on the same ports; plain http sends passwords unencrypted and is meant for trusted networks.
"""

from __future__ import annotations

from sudoo.config import Settings
from sudoo.exposure.base import Target


class HostPorts:
    """Publishes the panel and the gateways on the server's own ports."""

    listen_host = "0.0.0.0"  # noqa: S104 - reachable from outside on purpose; that is what this plugin is for

    def __init__(self, settings: Settings) -> None:
        if not settings.public_host:
            raise ValueError("EXPOSURE=host-ports needs PUBLIC_HOST: the server's public name or address.")
        self.settings = settings
        self.https = settings.public_scheme == "https"

    def instance_ports(self) -> dict[str, int]:
        return {}

    def panel_env(self) -> dict[str, str]:
        return {}

    def ensure(self, target: Target) -> None:
        """Nothing to start: the panel and the gateway already listen on the port."""

    def remove(self, target: Target) -> None:
        """Nothing to stop: the gateway stops listening when the instance is deleted."""

    def address(self, target: Target) -> str | None:
        return f"{self.settings.public_scheme}://{self.settings.public_host}:{target.port}"
