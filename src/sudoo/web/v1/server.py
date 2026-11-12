"""The server and the panel itself.

GET /api/v1/server   resource use of the server, the panel's address, version and plugins in use
"""

from __future__ import annotations

from importlib.metadata import version

from flask import Blueprint, Response, request

from sudoo import exposure
from sudoo.config import settings
from sudoo.core import events
from sudoo.instances import metrics

from .. import api_instances
from .. import validation as check
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
            "metrics": metrics.snapshot()["server"],
            "live_clients": events.BUS.subscriber_count,
        }
    )


def _minutes() -> int:
    return check.whole_number(request.args.get("minutes", "60"), "minutes", minimum=1, maximum=60)


@blueprint.get("/metrics")
def current() -> Response:
    return ok(metrics.snapshot())


@blueprint.get("/metrics/history")
def history() -> Response:
    minutes = _minutes()
    return ok(
        metrics.history(minutes), meta={"minutes": minutes, "interval_seconds": settings.monitor_interval_seconds}
    )


@blueprint.get("/instances/<name>/metrics")
def instance(name: str) -> Response:
    minutes = _minutes()
    api_instances.existing(name)
    return ok(
        {"current": metrics.snapshot()["instances"].get(name), "history": metrics.instance_history(name, minutes)},
        meta={"minutes": minutes, "interval_seconds": settings.monitor_interval_seconds},
    )
