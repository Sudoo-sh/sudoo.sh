"""The server and the panel itself.

GET /api/v1/server   resource use of the server, the panel's address, version and plugins in use
"""

from __future__ import annotations

import os
import signal
import threading
import time
from functools import cache
from importlib.metadata import version

from flask import Blueprint, Response, abort, request

from sudoo import exposure
from sudoo.config import settings
from sudoo.core import events, jobs
from sudoo.instances import metrics

from .. import validation as check
from ..security import log, require
from .instances import existing
from .responses import ok, ok_shared

blueprint = Blueprint("server", __name__)


@cache
def _version() -> str:
    return version("sudoo")  # reads the installed package's metadata files: once


@blueprint.get("/server")
@require("instances.view")
def server() -> Response:
    return ok(
        {
            "version": _version(),
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
@require("instances.view")
def current() -> Response:
    return ok(metrics.snapshot())


@blueprint.get("/metrics/history")
@require("instances.view")
def history() -> Response:
    """The dashboard's charts: every instance's last hour, the largest answer of the API. It changes only with each
    measurement, so it is built once per measurement for every caller."""
    minutes = _minutes()
    interval = settings.metrics_interval_seconds
    return ok_shared(
        ("history", minutes),
        (metrics.version(), interval),
        lambda: (metrics.history(minutes), {"minutes": minutes, "interval_seconds": interval}),
    )


@blueprint.get("/instances/<name>/metrics")
@require("instances.view")
def instance(name: str) -> Response:
    minutes = _minutes()
    existing(name)
    interval = settings.metrics_interval_seconds
    return ok_shared(
        ("instance-metrics", name, minutes),
        (metrics.version(), interval),
        lambda: (
            {"current": metrics.snapshot()["instances"].get(name), "history": metrics.instance_history(name, minutes)},
            {"minutes": minutes, "interval_seconds": interval},
        ),
    )


@blueprint.post("/server/restart")
@require("settings.manage")
def restart() -> Response:
    """Stop the backend; Docker starts it again at once (restart: unless-stopped). The web app, the connectors and
    the instances keep running. Refused while jobs run, because they would be cut off."""
    running = [job.instance for job in jobs.ACTIVE_JOB.values()]
    if running:
        abort(409, f"Jobs are running on {', '.join(sorted(running))}. Restart when they have finished.")
    log("panel restarted")

    def stop() -> None:
        time.sleep(1)  # let this answer reach the browser
        os.kill(os.getppid(), signal.SIGTERM)  # gunicorn's main process: a clean stop of the whole backend

    threading.Thread(target=stop, daemon=True).start()
    return ok({"restarting": True}, status=202)
