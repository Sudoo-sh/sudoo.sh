"""Sign-in sessions, CSRF protection, security headers and roles for the panel.

Every request except the public paths must belong to a valid session:
    - a signed session cookie (HttpOnly, SameSite=Strict, Secure)
    - its account still exists and its session_version matches (password changes end other sessions)
    - not idle for more than SESSION_IDLE_MINUTES and not older than SESSION_MAX_HOURS
Every change (non-GET request) must come from the panel's own address, be sent as JSON and carry the session's
CSRF token. Values inside the request are checked by validation.py.
"""
import hmac
import secrets
import time
from functools import wraps
from urllib.parse import urlsplit

from flask import abort, g, jsonify, redirect, request, session

from .. import accounts, audit, settings

PUBLIC_PATHS = {"/login", "/api/login", "/api/contact", "/healthz", "/favicon.ico", "/static/app.css",
                "/static/js/login.js", "/static/favicon.ico", "/static/odoo-icon.svg"}

SAFE_METHODS = ("GET", "HEAD")

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def install(app):
    app.before_request(_check_request)
    app.after_request(_add_headers)


def client_ip():
    """The visitor's address. The panel listens on 127.0.0.1 only, so requests come from its Cloudflare connector
    (which sets CF-Connecting-IP) or from an SSH tunnel."""
    return request.headers.get("CF-Connecting-IP", request.remote_addr)


def current_user():
    """{"name", "role"} of the signed-in account, or None (and the session is cleared)."""
    name = session.get("user")
    account = accounts.get(name) if name else None
    now = time.time()
    valid = (account is not None and session.get("version") == account["session_version"]
             and now - session.get("seen", 0) <= settings.SESSION_IDLE_MINUTES * 60
             and now - session.get("started", 0) <= settings.SESSION_MAX_HOURS * 3600)
    if not valid:
        session.clear()
        return None
    if now - session["seen"] > 30:
        session["seen"] = now
    return {"name": name, "role": account["role"]}


def start_session(name, version):
    now = time.time()
    session.clear()
    session.permanent = True
    session.update(user=name, version=version, csrf=secrets.token_urlsafe(32), started=now, seen=now)


def _same_origin():
    origin = request.headers.get("Origin")
    return origin is None or urlsplit(origin).netloc == request.host


def _check_request():
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
    if request.method not in SAFE_METHODS and not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""),
                                                           session.get("csrf", "")):
        abort(403, "Your session has expired. Please reload the page.")
    return None


def _add_headers(response):
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000"   # the panel is only used over https
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    if request.path.startswith("/api/") or response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store"
    return response


def is_admin():
    return g.user["role"] == "admin"


def message_for_user(text):
    """`text`, followed by who to contact when the signed-in user is not an administrator."""
    if getattr(g, "user", None) and not is_admin():
        return f"{text} {settings.contact_sentence()}"
    return text


def refuse_unless_admin(what):
    if not is_admin():
        audit.record(g.user["name"], client_ip(), f"refused: {what}", ok=False)
        abort(403, f"Only the administrator can do this. {settings.contact_sentence()}")


def admin_only(view):
    """Decorator: the endpoint is for administrators only."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        refuse_unless_admin(request.path)
        return view(*args, **kwargs)
    return wrapper


def log(event, instance=None, detail=None, ok=True):
    """Audit-log an event of the signed-in user."""
    audit.record(g.user["name"], client_ip(), event, instance=instance, ok=ok, detail=detail)
