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

import gzip
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from flask import Response, current_app, g, jsonify, request

from sudoo.core.cache import VersionedMemo

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


try:  # Zstandard is in the standard library from Python 3.14 (the panel's image); gzip alone before
    from compression import zstd as _zstd  # type: ignore[import-not-found, unused-ignore]
except ImportError:  # pragma: no cover - older Python
    _zstd = None

_SHARED: VersionedMemo[dict[str, bytes]] = VersionedMemo(seconds=60, size=256)
_COMPRESS_FROM = 1400  # bytes; smaller answers fit in one packet anyway


def ok_shared(key: Any, version: Any, build: Callable[[], tuple[Any, dict[str, Any] | None]]) -> Response:
    """Like ok(data, meta=meta) for `(data, meta) = build()`, serialised (and gzip-compressed, when large) once per
    `version` of `key` and given to every caller until the version changes (at most a minute). Only for answers
    that are the same for everyone allowed to read them, after the permission check: the key never names a caller.
    The web service passes an answer compressed here on as it is, instead of compressing it again for every caller."""

    def serialise() -> dict[str, bytes]:
        data, meta = build()
        body = current_app.json.dumps({"data": data, **({"meta": meta} if meta else {})}).encode() + b"\n"
        encodings = {"identity": body}
        if len(body) >= _COMPRESS_FROM:
            if _zstd is not None:
                encodings["zstd"] = _zstd.compress(body)
            encodings["gzip"] = gzip.compress(body, compresslevel=6, mtime=0)
        return encodings

    encodings = _SHARED.get(key, version, serialise)
    chosen = next((name for name in ("zstd", "gzip") if name in encodings and request.accept_encodings[name]), None)
    response = current_app.response_class(encodings[chosen or "identity"], mimetype="application/json")
    if chosen:
        response.headers["Content-Encoding"] = chosen
    response.vary.add("Accept-Encoding")
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
