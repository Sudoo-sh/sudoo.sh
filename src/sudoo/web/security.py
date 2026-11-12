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
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from flask import Flask, Response, abort, g, jsonify, redirect, request, session
from flask.typing import ResponseReturnValue

from sudoo import accounts, api_tokens, exposure, limits
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
    app.before_request(_limit_rate)
    app.after_request(_add_headers)


def _limit_rate() -> None:
    """Requests per minute, per signed-in user or token, else per address (limits/); 429 beyond."""
    if not request.path.startswith(("/api/", "/hooks/")):
        return
    user = g.get("user")
    caller = f"user:{user['name']}" if user else f"ip:{client_ip()}"
    decision = limits.check_request(caller, changes=request.method not in SAFE_METHODS)
    if decision is None:
        return
    g.rate_limit = decision
    if not decision.allowed:
        g.retry_after = decision.reset_seconds
        abort(429, f"Too many requests. Try again in {decision.reset_seconds} seconds.")


def client_ip() -> str:
    """The visitor's address. The panel listens on 127.0.0.1 only, so requests come from its Cloudflare connector
    (which sets CF-Connecting-IP) or from an SSH tunnel."""
    # remote_addr is always set for a request received over TCP
    return request.headers.get("CF-Connecting-IP", cast(str, request.remote_addr))


def user_of_session(data: Mapping[str, Any], check_idle: bool = True) -> dict[str, str] | None:
    """{"name", "role"} of a session's account while the session is valid, else None.

    Valid: the account exists, its password was not changed since (session_version), and the session is not older
    than SESSION_MAX_HOURS nor (with check_idle) unused for SESSION_IDLE_MINUTES.
    """
    name = data.get("user")
    account = accounts.get(name) if isinstance(name, str) else None
    now = time.time()
    valid = (
        account is not None
        and data.get("version") == account["session_version"]
        and (not check_idle or now - data.get("seen", 0) <= settings.session_idle_minutes * 60)
        and now - data.get("started", 0) <= settings.session_max_hours * 3600
    )
    return {"name": str(name), "role": account["role"]} if valid and account is not None else None


def current_user() -> dict[str, str] | None:
    """{"name", "role"} of the signed-in account, or None (and the session is cleared)."""
    user = user_of_session(session)
    if user is None:
        if "user" in session:  # an ended session; a sign-in waiting for its second step is kept
            session.clear()
        return None
    now = time.time()
    if now - session["seen"] > 30:
        session["seen"] = now
    return user


def start_session(name: str, version: int, enroll_only: bool = False) -> None:
    """A new session (a new cookie, so nothing from before signing in carries over). With enroll_only it can only
    set up a second factor (REQUIRE_SECOND_FACTOR applies and the account has none)."""
    now = time.time()
    session.clear()
    session.permanent = True
    session.update(user=name, version=version, csrf=secrets.token_urlsafe(32), started=now, seen=now)
    if enroll_only:
        session["enroll_only"] = True


def _same_origin() -> bool:
    origin = request.headers.get("Origin")
    return origin is None or urlsplit(origin).netloc == request.host


API_PREFIX = "/api/v1/"  # the API: an API token and signature (other systems) or a session (the browser)
HOOKS_PREFIX = "/hooks/"  # webhooks of update triggers: each checks its sender's own signature
PUBLIC_API = {("POST", "/api/v1/session"), ("GET", "/api/v1/contact"), ("POST", "/api/v1/session/second-factor")}
PUBLIC_API_PREFIXES = ("/api/v1/session/second-factor/",)  # sending a code, between the two sign-in steps
ENROLL_ONLY_PREFIXES = ("/api/v1/session", "/api/v1/account", "/api/v1/contact")


def _check_request() -> ResponseReturnValue | None:
    if request.path.startswith(HOOKS_PREFIX):
        return None
    if request.path.startswith(API_PREFIX):
        if "Authorization" in request.headers:
            _authenticate_api_call()
        else:
            _authenticate_browser_call()
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
    if session.get("enroll_only"):  # the previous pages cannot set up a second factor
        abort(403, "Set up a second factor for your account first, in the new panel.")
    if request.method not in SAFE_METHODS and not hmac.compare_digest(
        request.headers.get("X-CSRF-Token", ""), session.get("csrf", "")
    ):
        abort(403, "Your session has expired. Please reload the page.")
    return None


def _authenticate_browser_call() -> None:
    """An /api/v1 call from the panel's web app: same origin, JSON, a valid session and its CSRF token."""
    if request.method not in SAFE_METHODS:
        if not _same_origin():
            abort(403, "Request blocked: it did not come from this panel.")
        if request.content_length and request.mimetype != "application/json":
            abort(415, "Requests must be sent as JSON.")
    if (request.method, request.path) in PUBLIC_API or (
        request.method == "POST" and request.path.startswith(PUBLIC_API_PREFIXES)
    ):
        return
    g.user = current_user()
    if not g.user:
        abort(401, "Your session has ended. Please sign in again.")
    if session.get("enroll_only") and not request.path.startswith(ENROLL_ONLY_PREFIXES):
        abort(403, "Set up a second factor for your account first (Account, Second factor).")
    if request.method not in SAFE_METHODS and not hmac.compare_digest(
        request.headers.get("X-CSRF-Token", ""), session.get("csrf", "")
    ):
        abort(403, "Your session has expired. Please reload the page.")


class Headers(Protocol):
    """Request headers, looked up without regard to case (Flask's and aiohttp's both are)."""

    def get(self, key: str, default: str, /) -> str: ...


def api_caller(headers: Headers, method: str, path_with_query: str, body: bytes, ip: str) -> dict[str, str]:
    """The user an API-token call acts as: {"name": "api:<token name>", "role"}. Raises PermissionError with the
    HTTP status as its second argument when the call is refused; refusals are counted and audit-logged."""
    if accounts.THROTTLE.seconds_to_wait(ip, "api-token"):
        raise PermissionError("Too many refused API calls from this address. Try again in 15 minutes.", 429)
    try:
        token = api_tokens.verify(
            headers.get("Authorization", ""),
            headers.get("X-Sudoo-Timestamp", ""),
            headers.get("X-Sudoo-Nonce", ""),
            headers.get("X-Sudoo-Signature", ""),
            method,
            path_with_query,
            body,
        )
    except PermissionError as error:
        accounts.THROTTLE.failed(ip, "api-token")
        audit.record("api", ip, "API call refused", ok=False, detail=str(error))
        raise PermissionError(str(error), 401) from error
    if token["scope"] != "write" and method not in SAFE_METHODS:
        audit.record(f"api:{token['name']}", ip, "API change refused (read-only token)", ok=False)
        raise PermissionError("This API token can only read. Create a token with the write scope to make changes.", 403)
    return {"name": f"api:{token['name']}", "role": "admin" if token["scope"] == "write" else "operator"}


def _path_with_query() -> str:
    query = request.query_string.decode()
    return f"{request.path}?{query}" if query else request.path


def _authenticate_api_call() -> None:
    """Accept an /api/v1 call with an API token only when its signature is valid; act as "api:<token name>"."""
    if request.content_length and request.mimetype != "application/json":
        abort(415, "Requests must be sent as JSON.")
    try:
        g.user = api_caller(request.headers, request.method, _path_with_query(), request.get_data(), client_ip())
    except PermissionError as error:
        abort(error.args[1], error.args[0])


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
    decision = g.get("rate_limit")
    if decision is not None:  # IETF RateLimit header fields
        response.headers["RateLimit-Limit"] = str(decision.limit)
        response.headers["RateLimit-Remaining"] = str(decision.remaining)
        response.headers["RateLimit-Reset"] = str(decision.reset_seconds)
    if response.status_code == 429:
        response.headers["Retry-After"] = str(g.get("retry_after") or 900)
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
