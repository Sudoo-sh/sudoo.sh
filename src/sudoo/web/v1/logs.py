"""An instance's logs: which there are, and downloading one (following one live: log_stream.py).

    GET /api/v1/instances/<name>/logs/files               the logs that can be read (instances/logs.py):
                                                          [{"name", "job", "status", "started_at"}]
    GET /api/v1/instances/<name>/logs/download?log=&q=    the log as a text file: its last LOG_DOWNLOAD_MAX_LINES
                                                          lines, only those containing q (ignoring case) when given

Both need instances.logs in the instance, as GET /api/v1/instances/<name>/logs.
"""

from __future__ import annotations

import time

from flask import Blueprint, Response, abort, request, stream_with_context

from sudoo.config import settings
from sudoo.instances import logs

from ..security import log, require
from .instances import existing
from .responses import ok

blueprint = Blueprint("logs", __name__)


def _chosen_log() -> str:
    chosen = request.args.get("log", logs.ODOO_LOG)
    if chosen not in logs.NAMES:
        abort(400, f"Choose one of the logs: {', '.join(logs.NAMES)}.")
    return chosen


def _query() -> str:
    query = request.args.get("q", "")
    if len(query) > logs.MAX_QUERY:
        abort(400, f"The filter is too long (at most {logs.MAX_QUERY} characters).")
    return query


@blueprint.get("/instances/<name>/logs/files")
@require("instances.logs")
def files(name: str) -> Response:
    found = logs.available(existing(name))
    return ok(
        [
            entry
            | {
                "started_at": (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(entry["started_at"])))  # type: ignore[arg-type]
                    if entry["started_at"]
                    else None
                )
            }
            for entry in found
        ]
    )


@blueprint.get("/instances/<name>/logs/download")
@require("instances.logs")
def download(name: str) -> Response:
    chosen, query = _chosen_log(), _query()
    existing(name)
    log("log downloaded", instance=name, detail=chosen + (f" (filter: {query})" if query else ""))
    lines = logs.tail(name, chosen, settings.log_download_max_lines, query)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    response = Response(stream_with_context(lines), mimetype="text/plain")
    response.headers["Content-Disposition"] = f'attachment; filename="{name}-{stamp}-{chosen}"'
    response.headers["Cache-Control"] = "no-store"
    return response
