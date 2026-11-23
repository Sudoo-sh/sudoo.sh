"""GET /api/v1/events: the live event stream (Server-Sent Events).

A client opens one long-lived request and receives every change as it happens:

    id: 4182
    event: job
    data: {"key": "a12fff66a05d", "data": {<the job, as GET /api/v1/jobs/<id> shows it>}}

Topics: "instance" (an instance changed; data is the instance), "instance.deleted", "project" (a project changed;
data is the project as GET /api/v1/projects lists it, or {"id", "deleted": true}), "build" (an entry of a branch's
history was added or changed: a build, a stage change, a restore, as GET /api/v1/projects/<id>/builds/<id> shows
it, or {"id", "project", "branch", "removed": true}), "job" (a job started, moved to
another step, reported progress or ended; never its technical log, which stays in the server's log), "metrics" (resource use, every METRICS_INTERVAL_SECONDS),
"settings", "plugins" (an installed plugin changed), "marketplace" (the connection to the hub changed); the last
two carry no details, only that something changed; and "alert" (a new or changed alert, as GET /api/v1/alerts shows
it; only for callers that may read the audit log), "offsite" (an off-site copy changed; its id, no details; only
for callers that may manage backups). Instances, jobs and projects hidden from the caller by project access
(projects/access.py) are left out, and so are their figures in "metrics"; a project the caller can no longer see is
sent as deleted. `?topics=job,instance` limits the stream to some topics. A
comment line is sent every 20 seconds so proxies keep the connection open.

After a lost connection, the browser's EventSource reconnects by itself and sends Last-Event-ID; the stream then
starts with what was missed (the latest state of each instance and job). When too much was missed, the first
event is "reset": the client reloads its data.

The stream runs on the asyncio event loop shared with the gateway, not in the web server's threads, so a thousand
open tabs cost a thousand small coroutines, not a thousand threads. It listens on 127.0.0.1:EVENTS_PORT; the web
service forwards /api/v1/events to it. It accepts the same callers as the rest of the API: the panel's session
cookie (the browser), or an API token with a signature (other systems; X-Sudoo-Acting-For is accepted and signed
as on every API call). A token with the instances scope cannot open the stream (security.CONNECTED_APP_ENDPOINTS):
it follows its jobs with GET /api/v1/jobs/<id>. Every minute an open stream checks that its
caller is still allowed (the session was not signed out or ended, the account and the token still exist) and ends
when not. One account or token holds at most MAX_PER_CALLER streams at once (429 beyond), and the panel at most
MAX_CLIENTS (503 beyond).
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from typing import Any

from aiohttp import web
from flask import Flask

from sudoo import api_tokens
from sudoo.config import settings
from sudoo.core import jobs
from sudoo.core.events import BUS, Event
from sudoo.instances import gateway
from sudoo.projects import access

from ..security import api_caller, client_ip_of, user_of_session
from . import log_stream, shell_socket

PATH = "/api/v1/events"
FLASK_APP = web.AppKey("flask", Flask)
TOPICS = frozenset(
    {
        "instance",
        "instance.deleted",
        "project",
        "build",
        "domains",
        "job",
        "metrics",
        "settings",
        "plugins",
        "marketplace",
        "alert",
        "offsite",
        "server",
        "managed_names",
        "support",
        "premium",
    }
)
RESTRICTED = {
    "alert": "audit.view",
    "offsite": "backups.manage",
}  # topic -> the permission a caller needs to receive it
HEARTBEAT_SECONDS = 20
MAX_CLIENTS = 500
MAX_PER_CALLER = 8  # streams one account or token may hold at once: one caller cannot take every slot
_RECHECK_SECONDS = 60  # how often a long stream checks that its caller is still allowed
_OPEN: dict[str, int] = {}  # caller -> open streams; event loop thread only


def start(flask_app: Flask) -> None:
    """Serve the stream, and the shells' WebSockets, on the gateway's event loop (called when the panel starts)."""
    gateway.submit(_serve(make_app(flask_app)))
    gateway.submit(shell_socket.end_left_over())


async def _serve(app: web.Application) -> None:
    runner = web.AppRunner(app, access_log=None, handle_signals=False)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", settings.events_port, reuse_address=True).start()


def make_app(flask_app: Flask) -> web.Application:
    app = web.Application()
    app[FLASK_APP] = flask_app
    app.router.add_get(PATH, stream)
    shell_socket.add_routes(app)  # the web shell's WebSockets (shell_socket.py) share this server
    log_stream.add_routes(app)  # followed logs (log_stream.py) too
    from sudoo.premium import servers

    servers.add_routes(app)  # several servers: the channels of other servers' agents
    return app


# ---------------------------------------------------------------- who is calling


def _problem(status: int, title: str, detail: str) -> web.Response:
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail, "instance": PATH}
    return web.json_response(body, status=status, content_type="application/problem+json")


def _session_of(request: web.Request) -> dict[str, Any] | None:
    """The panel's session cookie, verified with the panel's own key (as Flask reads it)."""
    flask_app = request.app[FLASK_APP]
    cookie = request.cookies.get(flask_app.config["SESSION_COOKIE_NAME"])
    serializer = flask_app.session_interface.get_signing_serializer(flask_app)  # type: ignore[attr-defined]
    if not cookie or serializer is None:
        return None
    try:
        data = serializer.loads(cookie, max_age=int(flask_app.permanent_session_lifetime.total_seconds()))
    except Exception:  # noqa: BLE001 - a forged, old or damaged cookie is simply not a session
        return None
    return data if isinstance(data, dict) else None


def _caller(request: web.Request) -> tuple[dict[str, str] | None, dict[str, Any] | None, web.Response | None]:
    """(user, session, refusal). Exactly one of user and refusal is set."""
    ip = client_ip_of(request.headers, request.remote or "")
    if "Authorization" in request.headers:
        try:
            return api_caller(request.headers, "GET", request.path_qs, b"", ip), None, None
        except PermissionError as error:
            status = error.args[1]
            titles = {400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 429: "Too Many Requests"}
            return None, None, _problem(status, titles[status], error.args[0])
    origin = request.headers.get("Origin")
    if origin and origin.split("://", 1)[-1] != request.headers.get("X-Forwarded-Host", request.host):
        return None, None, _problem(403, "Forbidden", "Request blocked: it did not come from this panel.")
    session = _session_of(request)
    user = user_of_session(session) if session else None
    if user is None:
        return None, None, _problem(401, "Unauthorized", "Your session has ended. Please sign in again.")
    return user, session, None


# ---------------------------------------------------------------- the stream


def _frame(event: Event, data: Any) -> bytes:
    payload = json.dumps({"key": event.key, "data": data}, separators=(",", ":"), default=str)
    return f"id: {event.id}\nevent: {event.topic}\ndata: {payload}\n\n".encode()


_FRAMES: OrderedDict[int, tuple[Event, bytes]] = OrderedDict()  # event id -> (event, frame); event loop thread only
_FRAMES_KEPT = 256


def _published_frame(event: Event) -> bytes:
    """The frame of an event published with its data: the same bytes for every client, so built once (a metrics
    event for 500 open tabs is serialised once, not 500 times)."""
    found = _FRAMES.get(event.id)
    if found is not None and found[0] is event:
        return found[1]
    frame = _frame(event, event.data)
    _FRAMES[event.id] = (event, frame)
    while len(_FRAMES) > _FRAMES_KEPT:
        _FRAMES.popitem(last=False)
    return frame


def _topics(request: web.Request, user: dict[str, str]) -> frozenset[str] | None:
    """The topics asked for (None: all), without those the caller may not receive (RESTRICTED)."""
    asked = {t.strip() for t in request.query.get("topics", "").split(",") if t.strip()}
    allowed = access.role_permissions(user)
    hidden = {topic for topic, permission in RESTRICTED.items() if permission not in allowed}
    if not hidden:
        return frozenset(asked & TOPICS) if asked else None
    return frozenset((asked or TOPICS) & TOPICS - hidden)


def _sees_everything(user: dict[str, str]) -> bool:
    return access.sees_everything(user)


def for_caller(user: dict[str, str], event: Event, data: Any) -> Any:
    """The event's data as `user` may receive it (projects/access.py): None for an instance, job or project hidden
    from them (a project they lost access to is sent as deleted), metrics without the hidden instances."""
    if data is None or access.sees_everything(user):
        return data
    topic, key = event.topic, event.key
    if topic in ("instance", "instance.deleted", "domains"):
        return data if access.sees_instance(user, key) else None
    if topic == "job":
        job = jobs.JOBS.get(key)
        instance = job.instance if job is not None else (data.get("instance") if isinstance(data, dict) else None)
        if not instance:  # a job whose instance is not known here (another server's): only for who sees everything
            return None
        if not jobs.is_panel_job(str(instance)) and not access.sees_instance(user, str(instance)):
            return None
        return data
    if topic == "build" and isinstance(data, dict):  # an entry of a branch's history: as its branch
        from sudoo import projects as model

        found = model.get(str(data.get("project") or ""))
        return data if found is not None and access.sees_branch(user, found, str(data.get("branch") or "")) else None
    if topic == "metrics" and isinstance(data, dict):
        return {**data, "instances": access.visible_map(user, data.get("instances") or {})}
    if topic == "project" and isinstance(data, dict) and not data.get("deleted"):
        from sudoo import projects as model

        from .projects import project_resource

        found = model.get(key)
        if found is None or not access.sees_project(user, found):
            return {"id": key, "deleted": True}
        return project_resource(found, user)
    return data


def _last_id(request: web.Request) -> int | None:
    given = request.headers.get("Last-Event-ID") or request.query.get("last_event_id", "")
    return int(given) if given.isdigit() else None


def _still_allowed(user: dict[str, str], session: dict[str, Any] | None) -> bool:
    """Whether an open stream's caller may still receive events: its session was not signed out or ended (password
    changed, account deleted), or its API token was not revoked."""
    if session is not None:
        return user_of_session(session, check_idle=False) is not None
    token_id = user.get("token_id")
    return token_id is None or api_tokens.exists(token_id)


def _caller_key(user: dict[str, str]) -> str:
    token_id = user.get("token_id")
    return f"token:{token_id}" if token_id else f"user:{user['name']}"


async def stream(request: web.Request) -> web.StreamResponse:
    user, session, refusal = _caller(request)
    if refusal is not None or user is None:
        return refusal or _problem(401, "Unauthorized", "Your session has ended. Please sign in again.")
    key = _caller_key(user)
    if _OPEN.get(key, 0) >= MAX_PER_CALLER:
        refused = _problem(
            429,
            "Too Many Requests",
            f"At most {MAX_PER_CALLER} live connections at once. Close one, or try again later.",
        )
        refused.headers["Retry-After"] = "30"
        return refused
    if BUS.subscriber_count >= MAX_CLIENTS:
        busy = _problem(503, "Service Unavailable", "Too many open live connections. Try again in a minute.")
        busy.headers["Retry-After"] = "60"
        return busy
    _OPEN[key] = _OPEN.get(key, 0) + 1
    try:
        return await _send(request, user, session)
    finally:
        _OPEN[key] -= 1
        if not _OPEN[key]:
            del _OPEN[key]


async def _send(request: web.Request, user: dict[str, str], session: dict[str, Any] | None) -> web.StreamResponse:
    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",  # proxies must pass each event on at once
            "X-Content-Type-Options": "nosniff",
        }
    )
    await response.prepare(request)
    topics = _topics(request, user)
    subscription, last_before = BUS.subscribe(topics)
    loop = asyncio.get_running_loop()
    try:
        await response.write(f"retry: 3000\n: hello {user['name']}\n\n".encode())
        after = _last_id(request)
        if after is not None:
            missed = BUS.missed(after, last_before, topics)
            if missed is None:
                await response.write(f"id: {last_before}\nevent: reset\ndata: {{}}\n\n".encode())
            else:
                for old in missed:
                    data = await loop.run_in_executor(None, BUS.data_of, old)
                    data = await loop.run_in_executor(None, for_caller, user, old, data)
                    if data is not None:
                        await response.write(_frame(old, data))
        checked = loop.time()
        while True:
            try:
                event = await asyncio.wait_for(subscription.queue.get(), HEARTBEAT_SECONDS)
            except TimeoutError:
                await response.write(b": ping\n\n")
            else:
                if event is None:  # too slow: closed; the browser reconnects with Last-Event-ID
                    break
                if event.data is not None and await loop.run_in_executor(None, _sees_everything, user):
                    await response.write(_published_frame(event))
                else:
                    data = (
                        event.data if event.data is not None else await loop.run_in_executor(None, BUS.data_of, event)
                    )
                    data = await loop.run_in_executor(None, for_caller, user, event, data)
                    if data is not None:
                        await response.write(_frame(event, data))
            if loop.time() - checked > _RECHECK_SECONDS:
                checked = loop.time()
                if not await loop.run_in_executor(None, _still_allowed, user, session):  # signed out, token revoked
                    break
    except ConnectionResetError:
        pass  # the client went away
    finally:
        BUS.unsubscribe(subscription)
    return response
