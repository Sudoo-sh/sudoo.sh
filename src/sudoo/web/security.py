"""Sign-in sessions, CSRF protection, security headers and roles for the panel.

Every request except the public paths must belong to a valid session:
    - a signed session cookie (HttpOnly, SameSite=Strict, Secure)
    - its account still exists and its session_version matches (password changes end other sessions)
    - not idle for more than SESSION_IDLE_MINUTES and not older than SESSION_MAX_HOURS
Every change (non-GET request) must come from the panel's own address, be sent as JSON and carry the session's
CSRF token. Values inside the request are checked by validation.py.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable
from functools import wraps
from typing import cast
from urllib.parse import urlsplit

from flask import Flask, Response, abort, g, jsonify, redirect, request, session
from flask.typing import ResponseReturnValue

from sudoo import accounts, api_tokens, exposure
from sudoo.config import settings
from sudoo.core import audit

PUBLIC_PATHS = {
    "/login",
    "/api/login",
    "/api/contact",
    "/healthz",
    "/favicon.ico",
    "/static/app.css",
    "/static/js/login.js",
    "/static/favicon.ico",
    "/static/odoo-icon.svg",
}

SAFE_METHODS = ("GET", "HEAD")

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


SVG_POLICY = "default-src 'none'; style-src 'unsafe-inline'"


def install(app: Flask) -> None:
    app.before_request(_check_request)
    app.after_request(_add_headers)


def client_ip() -> str:
    """The visitor's address. The panel listens on 127.0.0.1 only, so requests come from its Cloudflare connector
    (which sets CF-Connecting-IP) or from an SSH tunnel."""
    # remote_addr is always set for a request received over TCP
    return request.headers.get("CF-Connecting-IP", cast(str, request.remote_addr))


def current_user() -> dict[str, str] | None:
    """{"name", "role"} of the signed-in account, or None (and the session is cleared)."""
    name = session.get("user")
    account = accounts.get(name) if name else None
    now = time.time()
    valid = (
        account is not None
        and session.get("version") == account["session_version"]
        and now - session.get("seen", 0) <= settings.session_idle_minutes * 60
        and now - session.get("started", 0) <= settings.session_max_hours * 3600
    )
    if not valid or account is None:
        session.clear()
        return None
    if now - session["seen"] > 30:
        session["seen"] = now
    return {"name": name, "role": account["role"]}


def start_session(name: str, version: int) -> None:
    now = time.time()
    session.clear()
    session.permanent = True
    session.update(user=name, version=version, csrf=secrets.token_urlsafe(32), started=now, seen=now)


def _same_origin() -> bool:
    origin = request.headers.get("Origin")
    return origin is None or urlsplit(origin).netloc == request.host


API_PREFIX = "/api/v1/"  # other systems: API token and signature (api_tokens.py), no session
HOOKS_PREFIX = "/hooks/"  # webhooks of update triggers: each checks its sender's own signature


def _check_request() -> ResponseReturnValue | None:
    if request.path.startswith(HOOKS_PREFIX):
        return None
    if request.path.startswith(API_PREFIX):
        _authenticate_api_call()
        return None
    if request.method not in SAFE_METHODS:
        if not _same_origin():
            abort(403, "Request blocked: it did not come from this panel.")
        if request.content_length and request.mimetype != "application/json":
            abort(415, "Requests must be sent as JSON.")
    if request.path in PUBLIC_PATHS:
        return None
    g.user = current_user()
    if not g.user:
        if request.path.startswith("/api/"):
            return jsonify(error="Your session has ended. Please sign in again.", login=True), 401
        return redirect("/login")
    if request.method not in SAFE_METHODS and not hmac.compare_digest(
        request.headers.get("X-CSRF-Token", ""), session.get("csrf", "")
    ):
        abort(403, "Your session has expired. Please reload the page.")
    return None


def _path_with_query() -> str:
    query = request.query_string.decode()
    return f"{request.path}?{query}" if query else request.path


def _authenticate_api_call() -> None:
    """Accept an /api/v1 call only with a valid API token and signature; act as "api:<token name>" from then on."""
    ip = client_ip()
    if accounts.THROTTLE.seconds_to_wait(ip, "api-token"):
        abort(429, "Too many refused API calls from this address. Try again in 15 minutes.")
    if request.content_length and request.mimetype != "application/json":
        abort(415, "Requests must be sent as JSON.")
    try:
        token = api_tokens.verify(
            request.headers.get("Authorization", ""),
            request.headers.get("X-Sudoo-Timestamp", ""),
            request.headers.get("X-Sudoo-Nonce", ""),
            request.headers.get("X-Sudoo-Signature", ""),
            request.method,
            _path_with_query(),
            request.get_data(),
        )
    except PermissionError as error:
        accounts.THROTTLE.failed(ip, "api-token")
        audit.record("api", ip, "API call refused", ok=False, detail=str(error))
        abort(401, str(error))
    if token["scope"] != "write" and request.method not in SAFE_METHODS:
        audit.record(f"api:{token['name']}", ip, "API change refused (read-only token)", ok=False)
        abort(403, "This API token can only read. Create a token with the write scope to make changes.")
    g.user = {"name": f"api:{token['name']}", "role": "admin" if token["scope"] == "write" else "operator"}


def _add_headers(response: Response) -> Response:
    # the documentation's diagrams are SVG files with their own colours (an inline <style>) and no scripts
    response.headers["Content-Security-Policy"] = (
        SVG_POLICY if response.mimetype == "image/svg+xml" else CONTENT_SECURITY_POLICY
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if exposure.provider().https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    if request.path.startswith("/api/") or response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store"
    return response


def is_admin() -> bool:
    return g.user["role"] == "admin"


def message_for_user(text: str | None) -> str | None:
    """`text`, followed by who to contact when the signed-in user is not an administrator."""
    if getattr(g, "user", None) and not is_admin():
        return f"{text} {settings.contact_sentence()}"
    return text


def refuse_unless_admin(what: str) -> None:
    if not is_admin():
        audit.record(g.user["name"], client_ip(), f"refused: {what}", ok=False)
        abort(403, f"Only the administrator can do this. {settings.contact_sentence()}")


def admin_only[**P, R](view: Callable[P, R]) -> Callable[P, R]:
    """Decorator: the endpoint is for administrators only."""

    @wraps(view)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        refuse_unless_admin(request.path)
        return view(*args, **kwargs)

    return wrapper


def log(event: str, instance: str | None = None, detail: str | None = None, ok: bool = True) -> None:
    """Audit-log an event of the signed-in user."""
    audit.record(g.user["name"], client_ip(), event, instance=instance, ok=ok, detail=detail)
