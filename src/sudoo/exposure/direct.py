"""Exposure plugin "direct" (the default of new installations): this server answers on ports 80 and 443 itself.

The gateway (instances/gateway.py) listens on ports 80 and 443 (DOMAIN_HTTP_PORT, DOMAIN_HTTPS_PORT) and serves
every name with a certificate from the ACME authority (Let's Encrypt by default), got and renewed by the panel:

    the panel        PANEL_DOMAIN (else PUBLIC_HOST when it is a name), forwarded to the web service on
                     127.0.0.1:PANEL_PORT; until it has a certificate, or without a name, the server's own address
                     with the panel's own certificate (instances/panel_address.py)
    an instance      its primary custom domain (CUSTOM_DOMAINS=acme), else its managed name under Sudoo.sh's domain
                     (instances/managed_names.py), else https://<server address>:<gateway port>, served by the
                     gateway port with the panel's own certificate

Open ports 80 and 443 to the internet (the ACME authority checks port 80); the gateway ports only for instances
that have no name yet. The web service listens on 127.0.0.1 only: everything comes in through the gateway, which
sends the visitor's address to the panel in X-Sudoo-Client-IP (and removes any a visitor sent).
"""

from __future__ import annotations

from sudoo.config import Settings
from sudoo.exposure.base import Target

PANEL_CLIENT_IP_HEADER = "X-Sudoo-Client-IP"


class Direct:
    """The server's own ports 80 and 443, with ACME certificates; nothing runs besides the panel and the gateway."""

    listen_host = "0.0.0.0"  # noqa: S104 - the gateway ports answer instances without a name, over https
    https = True
    client_ip_header = None  # visitors connect straight to the gateway: no header can be trusted
    panel_client_ip_header = PANEL_CLIENT_IP_HEADER  # set by the gateway when it forwards to the panel
    gateway_tls = True  # the gateway ports speak https, with the panel's own certificate
    serves_panel = True  # the gateway answers the panel's address on port 443

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def instance_ports(self) -> dict[str, int]:
        return {}

    def panel_env(self) -> dict[str, str]:
        return {}

    def ensure(self, target: Target) -> None:
        """The panel: make its own certificate when needed (the gateway serves it); instances: nothing to start."""
        if target.name == "panel":
            from sudoo.instances import panel_address

            panel_address.ensure_server_certificate()

    def remove(self, target: Target) -> None:
        """Nothing to stop: the gateway stops answering when the instance is deleted."""

    def address(self, target: Target) -> str | None:
        from sudoo.instances import panel_address

        if target.name == "panel":
            return panel_address.url()
        host = panel_address.server_host()
        return f"https://{host}:{target.port}" if host else None
