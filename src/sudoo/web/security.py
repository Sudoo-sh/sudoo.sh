"""Who is calling, what they may do, and the headers of every answer.

The backend answers:
    /api/v1/*   the API: an API token and signature (other systems), or the web app's session (the browser)
    /hooks/*    webhooks of update triggers, each checking its sender's own signature
    /docs/*     the documentation's images, for signed-in people
Requests of Sudoo.sh support come from inside the process (support/channel.py), with their user in the WSGI
environment (SUPPORT_ENVIRON) and checked against an active grant.
    /healthz    a health check
A browser session must be valid:
    - a signed session cookie (HttpOnly, SameSite=Strict, Secure)
    - its account still exists and its session_version matches (password changes end other sessions)
    - it was not signed out: each session has a random id (sid), remembered as ended when its owner signs out
    - not idle for more than SESSION_IDLE_MINUTES and not older than SESSION_MAX_HOURS
Every change (non-GET request) must come from the panel's own address, be sent as JSON and carry the session's
CSRF token. Values inside the request are checked by validation.py.
"""

from __future__ import annotations

import hmac
import re
import secrets
import time
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from flask import Flask, Response, abort, g, redirect, request, session
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import HTTPException

from sudoo import accounts, api_tokens, exposure, licence, limits, roles
from sudoo.config import settings
from sudoo.core import audit
from sudoo.projects import access

PUBLIC_PATHS = {"/healthz"}

SAFE_METHODS = ("GET", "HEAD")

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


SVG_POLICY = "default-src 'none'; style-src 'unsafe-inline'"

FORWARDED_POLICY = "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox"
"""What an answer another server's agent built gets (premium/servers.py: it marks it with X-Sudoo-Forwarded): never a
page or a script of the panel's origin, whatever its content."""


def install(app: Flask) -> None:
    app.before_request(_check_request)
    app.before_request(_limit_rate)
    app.after_request(_add_headers)


def _limit_rate() -> None:
    """Requests per minute, per signed-in user or token, else per address (limits/); 429 beyond."""
    if not request.path.startswith(("/api/", "/hooks/")):
        return
    from sudoo.premium import servers

    if servers.is_agent():  # the main panel counted the request already
        return
    user = g.get("user")
    # per token, not per person a token acts for (X-Sudoo-Acting-For): a token cannot multiply its limit
    caller = (
        (f"token:{user['token_id']}" if user.get("token_id") else f"user:{user['name']}")
        if user
        else f"ip:{client_ip()}"
    )
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
    exposure may name a header set by what is in front (Cloudflare's CF-Connecting-IP, the owner's proxy's
    X-Real-IP; for a list, its last address), and with EXPOSURE=direct the gateway sends it in X-Sudoo-Client-IP
    (the web service listens on 127.0.0.1 only, so nobody else can send that)."""
    provider = exposure.provider()
    for trusted in (getattr(provider, "panel_client_ip_header", None), getattr(provider, "client_ip_header", None)):
        if trusted and headers.get(trusted, "").strip():
            return headers.get(trusted, "").split(",")[-1].strip()
    forwarded = headers.get("X-Forwarded-For", "")
    if forwarded and remote in ("127.0.0.1", "::1"):
        return forwarded.split(",")[-1].strip()
    return remote


def client_ip() -> str:
    # remote_addr is always set for a request received over TCP
    return client_ip_of(request.headers, cast(str, request.remote_addr))


def user_of_session(data: Mapping[str, Any], check_idle: bool = True) -> dict[str, str] | None:
    """{"name", "role"} of a session's account while the session is valid, else None.

    Valid: the account exists, its password was not changed since (session_version), the session was not signed
    out (sid), and it is not older than SESSION_MAX_HOURS nor (with check_idle) unused for SESSION_IDLE_MINUTES.
    """
    name = data.get("user")
    account = accounts.get(name) if isinstance(name, str) else None
    now = time.time()
    sid = data.get("sid")
    valid = (
        account is not None
        and data.get("version") == account["session_version"]
        and (not check_idle or now - data.get("seen", 0) <= settings.session_idle_minutes * 60)
        and now - data.get("started", 0) <= settings.session_max_hours * 3600
        and not (isinstance(sid, str) and accounts.session_ended(sid))
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
    if "sid" not in session:  # a session started before sessions had an id gets one, so signing out can end it
        session["sid"] = secrets.token_urlsafe(16)
    return user


def end_current_session() -> None:
    """Sign out: the session's id is remembered as ended (a copy of the cookie stops working), and the cookie is
    cleared."""
    sid = session.get("sid")
    if isinstance(sid, str):
        accounts.end_session(sid, float(session.get("started", time.time())))
    session.clear()


def start_session(name: str, version: int, restricted: str | None = None) -> None:
    """A new session (a new cookie, so nothing from before signing in carries over). A restricted session opens
    only what its task needs (signin.restriction: choose a password, set up a second factor)."""
    now = time.time()
    session.clear()
    session.permanent = True
    session.update(
        user=name, version=version, sid=secrets.token_urlsafe(16), csrf=secrets.token_urlsafe(32), started=now, seen=now
    )
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
    ("GET", "/api/v1/session/options"),  # which ways to sign in the page offers
    ("POST", "/api/v1/session/email-code"),  # a sign-in code by e-mail, without a password (auth/email_sign_in.py)
    ("POST", "/api/v1/session/email-code/verify"),
    ("POST", "/api/v1/session/github"),  # Continue with GitHub (auth/github.py)
    ("POST", "/api/v1/session/github/callback"),
    ("POST", "/api/v1/invitation"),  # an invitee: who is invited, then accepting (web/v1/invitations.py)
    ("POST", "/api/v1/invitation/accept"),
    ("GET", "/api/v1/licence/text"),  # the agreement this release bundles, shown at first-run setup
    ("POST", "/api/v1/servers/join"),  # a new server's agent with its join code (402 without several servers)
}
PUBLIC_API_PREFIXES = ("/api/v1/session/second-factor/",)  # sending a code, between the two sign-in steps
PUBLIC_API_READ_PREFIXES = ("/api/v1/servers/agent-packages", "/api/v1/public-status/")
"""GET without a session: a server's agent fetching packages with its join code or its own signature (premium/agent.py;
402 without several servers), and a project's public status page (its token is in the path; web/v1/project_status.py)."""
RESTRICTED_PREFIXES = ("/api/v1/session", "/api/v1/account", "/api/v1/contact")  # all a restricted session opens
LICENCE_PREFIXES = (*RESTRICTED_PREFIXES, "/api/v1/licence")  # all a super admin can open before accepting it


SUPPORT_ENVIRON = "sudoo.support"
"""The WSGI environment key that carries Sudoo.sh support's user for a request the support channel runs in this
process (support/channel.py). Keys from the network never look like this (HTTP headers become HTTP_*)."""


def _check_request() -> ResponseReturnValue | None:
    from sudoo.premium import servers

    if servers.is_agent():
        # a server's agent listens on no port: every request is one the main panel passed on over its signed
        # channel, run here in-process for the person it names with the permissions it gives (premium/agent.py)
        forwarded = request.environ.get(servers.FORWARDED_ENV)
        if not isinstance(forwarded, dict) or forwarded.get("via") != "panel":
            abort(404)
        g.user = forwarded
        return None
    support_user = request.environ.get(SUPPORT_ENVIRON)
    if support_user is not None:
        _authenticate_support_call(support_user)
        return None
    if request.path.startswith(HOOKS_PREFIX) or request.path in PUBLIC_PATHS:
        return None
    if request.path.startswith(API_PREFIX):
        if _upload_piece():
            request.max_content_length = UPLOAD_PIECE_MAX  # one piece of an imported database (web/v1/imports.py)
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


UPLOAD_PIECE_RE = re.compile(r"^/api/v1/instances/[a-z0-9][a-z0-9-]{0,30}/imports/[A-Za-z0-9_-]{16,40}$")
UPLOAD_PIECE_MAX = 8 * 2**20 + 1024
"""Requests are small JSON documents, except one piece of a database being imported: PUT on UPLOAD_PIECE_RE as
application/octet-stream, at most 8 MB (instances/imports.py CHUNK_MAX)."""


def _upload_piece() -> bool:
    return (
        request.method == "PUT"
        and request.mimetype == "application/octet-stream"
        and UPLOAD_PIECE_RE.match(request.path) is not None
    )


def _authenticate_support_call(user: Any) -> None:
    """A request Sudoo.sh support makes through the support channel: only /api/v1, only while its grant is active."""
    from sudoo.premium import stubs

    if not isinstance(user, dict) or not request.path.startswith(API_PREFIX):
        abort(404)
    if stubs.support_grant(user) is None:  # without the support package: never
        abort(403, "Support access to this panel was withdrawn or has ended.")
    g.user = user


def _authenticate_browser_call() -> None:
    """An /api/v1 call from the panel's web app: same origin, JSON, a valid session and its CSRF token."""
    if request.method not in SAFE_METHODS:
        if not _same_origin():
            abort(403, "Request blocked: it did not come from this panel.")
        if request.content_length and request.mimetype != "application/json" and not _upload_piece():
            abort(415, "Requests must be sent as JSON.")
    if (
        (request.method, request.path) in PUBLIC_API
        or (request.method == "POST" and request.path.startswith(PUBLIC_API_PREFIXES))
        or (request.method == "GET" and request.path.startswith(PUBLIC_API_READ_PREFIXES))
    ):
        return
    g.user = current_user()
    if not g.user:
        abort(401, "Your session has ended. Please sign in again.")
    task = session.get("restricted")
    if task and not request.path.startswith(RESTRICTED_PREFIXES):
        abort(403, "Choose your own password first." if task == "password" else "Set up a second factor first.")
    if g.user["role"] == "super_admin" and not request.path.startswith(LICENCE_PREFIXES) and licence.pending():
        # instances keep running and serving; only managing them waits for the acceptance (licence/__init__.py)
        abort(403, "Accept the Sudoo.sh Licence Agreement first.")
    if request.method not in SAFE_METHODS and not hmac.compare_digest(
        request.headers.get("X-CSRF-Token", ""), session.get("csrf", "")
    ):
        abort(403, "Your session has expired. Please reload the page.")


class Headers(Protocol):
    """Request headers, looked up without regard to case (Flask's and aiohttp's both are)."""

    def get(self, key: str, default: str, /) -> str: ...

    def __contains__(self, key: str, /) -> bool: ...


SCOPE_ROLES = {"read": "viewer", "instances": roles.CONNECTED_APP, "developer": roles.DEVELOPER_TOKEN, "write": "admin"}
"""The role an API token acts as, by its scope (roles.py)."""

ACTING_FOR_HEADER = "X-Sudoo-Acting-For"

_NAME = r"[^/]+"
CONNECTED_APP_ENDPOINTS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, re.compile(f"{API_PREFIX}{path}"))
    for method, path in (
        ("GET", "versions"),
        ("GET", "instances"),
        ("POST", "instances"),
        ("GET", f"instances/{_NAME}"),
        ("PATCH", f"instances/{_NAME}"),
        ("DELETE", f"instances/{_NAME}"),
        ("POST", f"instances/{_NAME}/actions/{_NAME}"),  # which actions: v1/instances.CONNECTED_APP_ACTIONS
        ("GET", "instance-quota"),
        ("GET", f"jobs/{_NAME}"),
    )
)
"""All a token with the instances scope may call (method, path): its role's permissions open more endpoints than a
connected app needs (GitHub repositories, metrics, the documentation...), so the scope is narrowed to these."""


DEVELOPER_ENDPOINTS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, re.compile(f"{API_PREFIX}{path}"))
    for method, path in (
        ("GET", "versions"),
        ("GET", "instances"),
        ("POST", "instances"),  # development instances only (v1/instances.create)
        ("GET", f"instances/{_NAME}"),
        ("DELETE", f"instances/{_NAME}"),  # development instances only
        ("GET", f"instances/{_NAME}/logs(/files|/download|/stream)?"),
        ("GET", f"instances/{_NAME}/resources"),
        ("GET", f"instances/{_NAME}/mail(/.+)?"),
        ("POST", f"instances/{_NAME}/actions/{_NAME}"),  # which actions: v1/instances.DEVELOPER_ACTIONS
        ("GET", "instance-quota"),
        ("GET", "instance-plan"),
        ("GET", "repositories/modules"),  # the module picker of a new instance
        ("GET", "odoo-apps"),
        ("GET", "jobs"),
        ("GET", f"jobs/{_NAME}"),
        ("GET", f"jobs/{_NAME}/log"),
        ("GET", "projects"),
        ("GET", f"projects/{_NAME}"),
        ("POST", f"projects/{_NAME}/branches/.+/instances"),  # development branches only (v1/projects)
        ("GET", "events"),
    )
)
"""All a token with the developer scope may call (method, path): its role (roles.DEVELOPER_TOKEN) ∩ its owner's
role still decides, and the project access of its owner applies. Settings, accounts, teams, access, locks, stages,
restores, backups, domains, off-site copies, shells and credentials are not here."""


def _may_call(endpoints: tuple[tuple[str, re.Pattern[str]], ...], method: str, path_with_query: str) -> bool:
    path = path_with_query.split("?", 1)[0]
    method = "GET" if method == "HEAD" else method
    return any(method == allowed and pattern.fullmatch(path) for allowed, pattern in endpoints)


def _connected_app_may_call(method: str, path_with_query: str) -> bool:
    return _may_call(CONNECTED_APP_ENDPOINTS, method, path_with_query)


def acting_for(headers: Headers) -> str | None:
    """The X-Sudoo-Acting-For header: who, in the calling system, the call is made for (None when not sent).
    Raises PermissionError(message, 400) when it is sent but is not 1-120 letters, digits or ._@+-"""
    if ACTING_FOR_HEADER not in headers:
        return None
    value = headers.get(ACTING_FOR_HEADER, "")
    if not api_tokens.ACTING_FOR_RE.fullmatch(value):
        raise PermissionError(
            f"{ACTING_FOR_HEADER} must be 1 to 120 letters, digits, dots, dashes, underscores, @ or +.", 400
        )
    return value


def api_caller(headers: Headers, method: str, path_with_query: str, body: bytes, ip: str) -> dict[str, str]:
    """The user an API-token call acts as: {"name": "api:<token name>", "role", "token_id"}; "api:<token name> for
    <person>" when the call says on whose behalf it is made (X-Sudoo-Acting-For, signed). Raises PermissionError
    with the HTTP status as its second argument when the call is refused; refusals are counted and audit-logged."""
    if accounts.THROTTLE.seconds_to_wait(ip, "api-token"):
        raise PermissionError("Too many refused API calls from this address. Try again in 15 minutes.", 429)
    person = acting_for(headers)
    try:
        token = api_tokens.verify(
            headers.get("Authorization", ""),
            headers.get("X-Sudoo-Timestamp", ""),
            headers.get("X-Sudoo-Nonce", ""),
            headers.get("X-Sudoo-Signature", ""),
            method,
            path_with_query,
            body,
            person,
        )
    except PermissionError as error:
        accounts.THROTTLE.failed(ip, "api-token")
        audit.record("api", ip, "API call refused", ok=False, detail=str(error))
        raise PermissionError(str(error), 401) from error
    name = f"api:{token['name']}" + (f" for {person}" if person else "")
    scope = token["scope"]
    if scope == "read" and method not in SAFE_METHODS:
        audit.record(name, ip, "API change refused (read-only token)", ok=False)
        raise PermissionError("This API token can only read. Create a token with the write scope to make changes.", 403)
    if scope == "instances" and not _connected_app_may_call(method, path_with_query):
        audit.record(name, ip, "API call refused (instances scope)", ok=False, detail=f"{method} {path_with_query}")
        raise PermissionError(
            "This API token has the instances scope: it can list, create, start, pause, restart, back up, extend "
            "and delete instances, nothing else.",
            403,
        )
    if scope == "developer" and not _may_call(DEVELOPER_ENDPOINTS, method, path_with_query):
        audit.record(name, ip, "API call refused (developer scope)", ok=False, detail=f"{method} {path_with_query}")
        raise PermissionError(
            "This API token has the developer scope: it can read projects, branches, instances, logs, jobs and "
            "caught e-mails, start, pause, restart and update instances, and create development instances; "
            "nothing else.",
            403,
        )
    return {
        "name": name,
        "role": SCOPE_ROLES.get(scope, "viewer"),
        "token_id": str(token["id"]),
        "owner": str(token.get("created_by") or ""),  # whose teams and grants apply (projects/access.py)
    }


def _path_with_query() -> str:
    query = request.query_string.decode()
    return f"{request.path}?{query}" if query else request.path


def _authenticate_api_call() -> None:
    """Accept an /api/v1 call with an API token only when its signature is valid; act as "api:<token name>"."""
    if request.content_length and request.mimetype != "application/json" and not _upload_piece():
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
    if response.headers.pop("X-Sudoo-Forwarded", None) is not None:
        # an answer of another server's agent: whatever it holds, never a page or a script on the panel's origin
        response.headers["Content-Security-Policy"] = FORWARDED_POLICY
    if response.headers.pop("X-Sudoo-Frame", None) == "caught-mail" and response.mimetype == "text/html":
        # a caught e-mail's HTML (web/v1/caught_mail.py): shown only in a sandboxed frame of the panel itself
        from .v1.caught_mail import HTML_POLICY

        response.headers["Content-Security-Policy"] = HTML_POLICY
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
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


def _instance_in_path() -> str | None:
    """The instance named in the path (/api/v1/instances/<name>/...), when the endpoint is about one."""
    name = (request.view_args or {}).get("name")
    if isinstance(name, str) and request.path.startswith(f"{API_PREFIX}instances/"):
        return name
    return None


def can(permission: str) -> bool:
    """Whether the caller has `permission`: their role's (roles.py), and in an instance of a project (the instance
    the path names) what their access there gives (projects/access.py: role ∩ grant, locked branches)."""
    name = _instance_in_path()
    if name is not None:
        return access.allowed_instance(g.user, permission, name)
    return permission in access.role_permissions(g.user)


def message_for_user(text: str | None) -> str | None:
    """`text`, followed by who to contact when the caller cannot fix things themselves."""
    if getattr(g, "user", None) and not can("accounts.manage"):
        return f"{text} {settings.contact_sentence()}"
    return text


def refuse_unless(permission: str, what: str, instance: str | None = None) -> None:
    """403 (404 for an instance the caller may not see) unless the caller has `permission`, in `instance` (default:
    the instance the path names); audit-logged."""
    name = instance or _instance_in_path()
    if name is not None:
        found = access.instance_refusal(g.user, permission, name)
    elif permission not in access.reach_permissions(g.user):  # each project's role is checked again by the endpoint
        found = (403, "")
    else:
        found = None
    if found is None:
        return
    status, message = found
    audit.record(g.user["name"], client_ip(), f"refused: {what}", instance=name, ok=False)
    if (not message or message.startswith("Your role cannot")) and permission not in access.reach_permissions(g.user):
        needed = roles.PERMISSIONS[permission].lower()
        abort(403, f"Your role cannot do this ({needed}). {settings.contact_sentence()}")
    abort(status, message)


def require[**P, R](permission: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: the endpoint needs `permission` (in the instance the path names, when it names one)."""

    def decorate(view: Callable[P, R]) -> Callable[P, R]:
        @wraps(view)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            refuse_unless(permission, request.path)
            return view(*args, **kwargs)

        return wrapper

    return decorate


def refuse_without_feature(feature: str, what: str) -> None:
    """403 when the panel's plan does not include `feature` (entitlements.py), with where to get it; audit-logged."""
    from sudoo import entitlements

    refused = entitlements.feature_refusal(feature)
    if refused:
        audit.record(g.user["name"], client_ip(), f"refused: {what} (plan)", ok=False, detail=refused[:300])
        abort(403, refused)


class PremiumRequired(HTTPException):
    """402: the call needs a premium package that is not installed (premium/); the description names the plans."""

    code = 402


def refuse_without_premium(package_id: str, what: str) -> None:
    """402 when the premium package `package_id` does not run in this process, with which plans have it; logged."""
    from sudoo import premium

    if premium.loaded(package_id):
        return
    refused = premium.refusal(package_id)
    audit.record(g.user["name"], client_ip(), f"refused: {what} (premium feature not installed)", ok=False,
                 detail=refused[:300])  # fmt: skip
    raise PremiumRequired(refused)


def log(event: str, instance: str | None = None, detail: str | None = None, ok: bool = True) -> None:
    """Audit-log an event of the signed-in user."""
    audit.record(g.user["name"], client_ip(), event, instance=instance, ok=ok, detail=detail)
