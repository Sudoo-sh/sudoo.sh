"""How every /api/v1 answer is shaped.

success      {"data": <resource or list>, "meta": {...}}      200; 201 Created with Location for new resources
background   202 Accepted, the job in "data", Location pointing to it (GET /api/v1/jobs/<id>)
no content   204 for deletions that finish at once
errors       RFC 9457 problem details, Content-Type application/problem+json:
             {"type", "title", "status", "detail", "instance", "request_id"}
times        ISO 8601 in UTC, e.g. 2026-11-12T08:30:00Z
every reply  X-Request-ID (the caller's own when it sent a valid one)
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from flask import Response, g, jsonify, request

PREFIX = "/api/v1"


def iso(timestamp: float | int | str | None) -> str | None:
    """A Unix time as ISO 8601 in UTC, or None."""
    if timestamp in (None, ""):
        return None
    return datetime.fromtimestamp(float(timestamp), UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def request_id() -> str:
    """The id of this request: the caller's X-Request-ID when it is sensible, otherwise a new one."""
    if "request_id" not in g:
        given = request.headers.get("X-Request-ID", "")
        valid = 8 <= len(given) <= 64 and all(c.isalnum() or c in "-_." for c in given)
        g.request_id = given if valid else uuid.uuid4().hex
    return str(g.request_id)


def ok(data: Any, status: int = 200, meta: dict[str, Any] | None = None) -> Response:
    response = jsonify({"data": data, **({"meta": meta} if meta else {})})
    response.status_code = status
    return response


def created(data: Any, location: str) -> Response:
    """201 Created; Location is where the new resource can be read."""
    response = ok(data, status=201)
    response.headers["Location"] = location
    return response


def no_content() -> Response:
    return Response(status=204)


def problem(status: int, detail: str) -> Response:
    """An RFC 9457 problem details response."""
    response = jsonify(
        type="about:blank",
        title=HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        instance=request.path,
        request_id=request_id(),
    )
    response.status_code = status
    response.mimetype = "application/problem+json"
    if status == 429:
        response.headers["Retry-After"] = str(g.get("retry_after") or 900)
    return response


def add_request_id(response: Response) -> Response:
    response.headers["X-Request-ID"] = request_id()
    return response


def page[T](items: Sequence[T], limit: int, offset: int, present: Callable[[T], Any]) -> Response:
    """One page of a list, each item shown with `present`; "meta" has total, count, limit and offset."""
    chosen = items[offset : offset + limit]
    meta = {"total": len(items), "count": len(chosen), "limit": limit, "offset": offset}
    return ok([present(item) for item in chosen], meta=meta)
