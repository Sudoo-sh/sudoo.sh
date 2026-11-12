"""Who is calling, what they may do, and the headers of every answer.

The backend answers:
    /api/v1/*   the API: an API token and signature (other systems), or the web app's session (the browser)
    /hooks/*    webhooks of update triggers, each checking its sender's own signature
    /docs/*     the documentation's images, for signed-in people
    /healthz    a health check
A browser session must be valid:
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

from flask import Flask, Response, abort, g, redirect, request, session
from flask.typing import ResponseReturnValue

from sudoo import accounts, api_tokens, exposure, limits, roles
from sudoo.config import settings
from sudoo.core import audit

PUBLIC_PATHS = {"/healthz"}

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


def client_ip_of(headers: Headers, remote: str) -> str:
    """The visitor's address. The backend listens on 127.0.0.1 behind the web service, which sets X-Forwarded-For
    to the address it received the request from (replacing any sent by the visitor); in front of that, the
    exposure may name a header it sets itself (Cloudflare's CF-Connecting-IP)."""
    trusted = getattr(exposure.provider(), "client_ip_header", None)
    if trusted and headers.get(trusted, ""):
        return headers.get(trusted, "")
    forwarded = headers.get("X-Forwarded-For", "")
    if forwarded and remote in ("127.0.0.1", "::1"):
        return forwarded.split(",")[-1].strip()
    return remote


def client_ip() -> str:
    # remote_addr is always set for a request received over TCP
    return client_ip_of(request.headers, cast(str, request.remote_addr))


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
    return {"name": str(name), "role": roles.normalize(account["role"])} if valid and account is not None else None


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


def start_session(name: str, version: int, restricted: str | None = None) -> None:
    """A new session (a new cookie, so nothing from before signing in carries over). A restricted session opens
    only what its task needs (signin.restriction: choose a password, set up a second factor)."""
    now = time.time()
    session.clear()
    session.permanent = True
    session.update(user=name, version=version, csrf=secrets.token_urlsafe(32), started=now, seen=now)
    session["restricted"] = restricted


def _same_origin() -> bool:
    origin = request.headers.get("Origin")
    return origin is None or urlsplit(origin).netloc == request.host


API_PREFIX = "/api/v1/"  # the API: an API token and signature (other systems) or a session (the browser)
HOOKS_PREFIX = "/hooks/"  # webhooks of update triggers: each checks its sender's own signature
PUBLIC_API = {
    ("GET", "/api/v1/setup"),
    ("POST", "/api/v1/setup"),
    ("GET", "/api/v1/contact"),
    ("POST", "/api/v1/session"),
    ("POST", "/api/v1/session/second-factor"),
}
PUBLIC_API_PREFIXES = ("/api/v1/session/second-factor/",)  # sending a code, between the two sign-in steps
RESTRICTED_PREFIXES = ("/api/v1/session", "/api/v1/account", "/api/v1/contact")  # all a restricted session opens


def _check_request() -> ResponseReturnValue | None:
    if request.path.startswith(HOOKS_PREFIX) or request.path in PUBLIC_PATHS:
        return None
    if request.path.startswith(API_PREFIX):
        if "Authorization" in request.headers:
            _authenticate_api_call()
        else:
            _authenticate_browser_call()
        return None
    # the documentation's images: read only, for a signed-in session
    if request.method not in SAFE_METHODS:
        abort(405)
    g.user = current_user()
    if not g.user:
        return redirect("/login")
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
    task = session.get("restricted")
    if task and not request.path.startswith(RESTRICTED_PREFIXES):
        abort(403, "Choose your own password first." if task == "password" else "Set up a second factor first.")
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
    return {"name": f"api:{token['name']}", "role": "admin" if token["scope"] == "write" else "viewer"}


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


def can(permission: str) -> bool:
    """Whether the caller's role includes `permission` (roles.py)."""
    return permission in roles.permissions(g.user["role"])


def message_for_user(text: str | None) -> str | None:
    """`text`, followed by who to contact when the caller cannot fix things themselves."""
    if getattr(g, "user", None) and not can("accounts.manage"):
        return f"{text} {settings.contact_sentence()}"
    return text


def refuse_unless(permission: str, what: str) -> None:
    if not can(permission):
        audit.record(g.user["name"], client_ip(), f"refused: {what}", ok=False)
        needed = roles.PERMISSIONS[permission].lower()
        abort(403, f"Your role cannot do this ({needed}). {settings.contact_sentence()}")


def require[**P, R](permission: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: the endpoint needs `permission`."""

    def decorate(view: Callable[P, R]) -> Callable[P, R]:
        @wraps(view)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            refuse_unless(permission, request.path)
            return view(*args, **kwargs)

        return wrapper

    return decorate


def log(event: str, instance: str | None = None, detail: str | None = None, ok: bool = True) -> None:
    """Audit-log an event of the signed-in user."""
    audit.record(g.user["name"], client_ip(), event, instance=instance, ok=ok, detail=detail)
