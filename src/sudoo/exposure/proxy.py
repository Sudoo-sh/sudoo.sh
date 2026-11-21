"""Exposure plugin "proxy": your own reverse proxy or load balancer in front of the server handles https.

The panel's web service and each instance's gateway listen on PROXY_LISTEN (127.0.0.1 by default, for a proxy on
this server) on their own ports; the proxy forwards to them:

    the panel        <PUBLIC_SCHEME>://<PANEL_DOMAIN or PUBLIC_HOST>   ->  PROXY_LISTEN:PANEL_PORT
    an instance      its custom domain (CUSTOM_DOMAINS=external), else
                     <PUBLIC_SCHEME>://<PUBLIC_HOST>:<gateway port>     ->  PROXY_LISTEN:<gateway port>

The proxy must send the visitor's address in PROXY_CLIENT_IP_HEADER (X-Real-IP by default) on every request,
replacing any the visitor sent: the panel and the gateways trust that header, so nothing but the proxy may reach
their ports. PUBLIC_SCHEME is https unless set otherwise.
"""

from __future__ import annotations

from sudoo.config import Settings
from sudoo.exposure.base import Target


class Proxy:
    """Local ports behind the owner's proxy, which handles https and sends the visitor's address."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def listen_host(self) -> str:
        return str(self.settings.proxy_listen)

    @property
    def https(self) -> bool:
        return bool(self.settings.public_scheme == "https")

    @property
    def client_ip_header(self) -> str | None:
        return str(self.settings.proxy_client_ip_header) or None

    def instance_ports(self) -> dict[str, int]:
        return {}

    def panel_env(self) -> dict[str, str]:
        return {}

    def ensure(self, target: Target) -> None:
        """Nothing to start: the proxy forwards to the ports that already listen."""

    def remove(self, target: Target) -> None:
        """Nothing to stop."""

    def address(self, target: Target) -> str | None:
        scheme, server = self.settings.public_scheme, str(self.settings.public_host)
        server = f"[{server}]" if ":" in server else server
        if target.name == "panel":
            host = self.settings.panel_domain or server
            return f"{scheme}://{host}" if host else None
        return f"{scheme}://{server}:{target.port}" if server else None
