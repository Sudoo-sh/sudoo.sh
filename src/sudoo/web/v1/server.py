"""The server and the panel itself.

GET /api/v1/server   resource use of the server, the panel's address, version and plugins in use
"""

from __future__ import annotations

from importlib.metadata import version

from flask import Blueprint, Response

from sudoo import exposure
from sudoo.config import settings
from sudoo.core import events
from sudoo.instances import monitor

from .responses import ok

blueprint = Blueprint("server", __name__)


@blueprint.get("/server")
def server() -> Response:
    return ok(
        {
            "version": version("sudoo"),
            "panel_url": exposure.address(exposure.PANEL),
            "plugins": {
                "exposure": settings.exposure,
                "enterprise_source": settings.enterprise_source,
                "update_triggers": [t.strip() for t in settings.update_triggers.split(",") if t.strip()],
            },
            "metrics": monitor.metrics()["server"],
            "live_clients": events.BUS.subscriber_count,
        }
    )
