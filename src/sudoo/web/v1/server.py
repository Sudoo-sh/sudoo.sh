"""The server and the panel itself.

GET /api/v1/server            resource use of the server, the panel's address, version and plugins in use
GET /api/v1/server/capacity   how many instances the server can run at once, the panel's own use, disk headroom
GET /api/v1/server/managed-subdomains        managed names (instances/managed_names.py): the setting, whether
                                             Sudoo.sh offers them, its base domain, the company label, use and limit
POST /api/v1/server/managed-subdomains/sync  compare the instances with their names now (settings.manage)
"""

from __future__ import annotations

import os
import signal
import threading
import time
from functools import cache
from importlib.metadata import version

from flask import Blueprint, Response, abort, g, request

from sudoo import exposure
from sudoo.config import settings
from sudoo.core import events, jobs
from sudoo.instances import capacity, metrics, panel_address
from sudoo.projects import access

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
            "public_address": panel_address.resource(),
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
    found = capacity.snapshot()
    if not access.sees_everything(g.user):  # instances of hidden projects and branches are left out
        found = {**found, "instances": access.visible_map(g.user, found.get("instances") or {})}
    return ok(found)


@blueprint.get("/server/capacity")
@require("instances.view")
def server_capacity() -> Response:
    """The capacity estimate (instances/capacity.py), for the web app's warnings and for other tools."""
    estimate = capacity.current()
    if estimate is None:
        abort(503, "The server has not been measured yet. Try again in a few seconds.")
    return ok(estimate)


@blueprint.get("/metrics/history")
@require("instances.view")
def history() -> Response:
    """The dashboard's charts: every instance's last hour, the largest answer of the API. It changes only with each
    measurement, so it is built once per measurement for every caller."""
    minutes = _minutes()
    interval = settings.metrics_interval_seconds
    if not access.sees_everything(g.user):  # built for this caller alone: hidden instances are left out
        found = metrics.history(minutes)
        found["instances"] = access.visible_map(g.user, found.get("instances") or {})
        return ok(found, meta={"minutes": minutes, "interval_seconds": interval})
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


@blueprint.get("/server/managed-subdomains")
@require("instances.view")
def managed_subdomains() -> Response:
    from sudoo.instances import managed_names

    return ok(managed_names.overview())


@blueprint.post("/server/managed-subdomains/sync")
@require("settings.manage")
def managed_subdomains_sync() -> Response:
    from sudoo.instances import managed_names, panel_name

    if not settings.marketplace:
        abort(409, "Managed subdomains come from Sudoo.sh: link this panel first (Settings, Marketplace).")
    managed_names.sync()
    panel_name.sync()
    log("managed subdomains: check now")
    return ok(managed_names.overview())
