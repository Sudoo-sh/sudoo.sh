"""An instance's logs: which there are, pages of one, and downloading one (following one live: log_stream.py).

    GET /api/v1/instances/<name>/logs/files               the logs that can be read (instances/logs.py):
                                                          [{"name", "job", "status", "started_at"}]
    GET /api/v1/instances/<name>/logs/entries?log=&limit=&before=|after=|around=&<filters>
                                                          a page of entries, oldest first, with their parsed parts
    GET /api/v1/instances/<name>/logs/download?log=&<filters>
                                                          the log as a text file: its last LOG_DOWNLOAD_MAX_LINES
                                                          lines, only the entries the filters keep

Filters (instances/logs.filters_from): `level` (DEBUG, INFO, WARNING, ERROR, CRITICAL, comma separated), `logger`
(that logger and those under it), `q` (text, any case; a regular expression with `regex=1`, time-limited), `since`
and `until` (ISO 8601 with a zone) and `tracebacks=1` (only entries with a traceback).

All need instances.logs in the instance, as GET /api/v1/instances/<name>/logs.
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


def _filters() -> logs.Filters:
    try:
        return logs.filters_from(request.args)
    except ValueError as error:
        abort(400, str(error))


def _describe(chosen: str, filters: logs.Filters) -> str:
    """What the audit log says of a download: the log and its filters."""
    parts = []
    if filters.levels:
        parts.append("levels " + ",".join(sorted(filters.levels)))
    if filters.logger:
        parts.append(f"logger {filters.logger}")
    if filters.query:
        parts.append(f"filter: {filters.query}")
    if filters.pattern is not None:
        parts.append(f"pattern: {filters.pattern.pattern}")
    if filters.since or filters.until:
        parts.append(
            f"time {filters.since.isoformat() if filters.since else ''}..{filters.until.isoformat() if filters.until else ''}"
        )
    if filters.tracebacks:
        parts.append("tracebacks")
    return chosen + (f" ({'; '.join(parts)})" if parts else "")


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


@blueprint.get("/instances/<name>/logs/entries")
@require("instances.logs")
def entries(name: str) -> Response:
    chosen, filters = _chosen_log(), _filters()
    existing(name)
    raw_limit = request.args.get("limit", str(logs.PAGE_DEFAULT))
    if not raw_limit.isascii() or not raw_limit.isdigit() or not 1 <= int(raw_limit) <= logs.PAGE_MAX:
        abort(400, f"limit is a whole number from 1 to {logs.PAGE_MAX}.")
    cursors = {key: request.args.get(key, "") for key in ("before", "after", "around") if request.args.get(key)}
    if len(cursors) > 1:
        abort(400, "Give one of before, after and around.")
    try:
        chosen_cursor = {key: logs.cursor(value, key) for key, value in cursors.items()}
    except ValueError as error:
        abort(400, str(error))
    try:
        found = logs.page(
            name, chosen, filters, limit=int(raw_limit), window=settings.log_download_max_lines, **chosen_cursor
        )
    except logs.SlowPattern:
        abort(422, "This regular expression takes too long on this log. Make it simpler or more precise.")
    return ok(
        [entry.as_dict() for entry in found.entries],
        meta={
            "log": chosen,
            "has_before": found.has_before,
            "has_after": found.has_after,
            "first": found.entries[0].id if found.entries else None,
            "last": found.entries[-1].id if found.entries else None,
        },
    )


@blueprint.get("/instances/<name>/logs/download")
@require("instances.logs")
def download(name: str) -> Response:
    chosen, filters = _chosen_log(), _filters()
    existing(name)
    log("log downloaded", instance=name, detail=_describe(chosen, filters))
    lines = logs.tail(name, chosen, settings.log_download_max_lines, filters=filters)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    response = Response(stream_with_context(lines), mimetype="text/plain")
    response.headers["Content-Disposition"] = f'attachment; filename="{name}-{stamp}-{chosen}"'
    response.headers["Cache-Control"] = "no-store"
    return response
