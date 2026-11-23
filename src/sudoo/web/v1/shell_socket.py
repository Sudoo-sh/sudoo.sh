"""GET /api/v1/instances/<name>/shell/socket: the WebSocket of a shell (webshell/).

Served on the asyncio loop of the live events (events.py, 127.0.0.1:EVENTS_PORT; the web service forwards this path
there), because a shell is a long two-way connection that gunicorn's 32 threads should not hold.

Before the connection is upgraded, every one of these must hold, or it is refused with a plain HTTP error:

    1. the Origin header is present and is the panel's own address (a WebSocket is not covered by the same-origin
       policy, so another site could otherwise open one with the visitor's cookie);
    2. the browser offers the subprotocol "sudoo.shell.v1" and a ticket as a second one, "ticket.<ticket>" (a
       subprotocol, not the address, so the ticket never appears in a log line);
    3. the ticket is valid: issued less than SHELL_TICKET_SECONDS ago by POST .../shell/tickets, not used before
       (it is used up now, whatever happens next);
    4. the panel session cookie is valid, unrestricted, and is the very session the ticket was issued to (same
       person, same sid), for this instance;
    5. the rules still allow the shell (policy.plan), and the limits have room (sessions.register).

Then the connection is upgraded and sessions.serve runs the shell. Opening, refusing and closing are audit-logged.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import functools
import time
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from sudoo.core import audit
from sudoo.webshell import docker_exec, policy, recordings, sessions, tickets
from sudoo.webshell.policy import Refused

from ..security import audience, client_ip_of, user_of_session

PATH = "/api/v1/instances/{name}/shell/socket"
PROTOCOL = "sudoo.shell.v1"
TICKET_PREFIX = "ticket."
HEARTBEAT_SECONDS = 25


def add_routes(app: web.Application) -> None:
    app.router.add_get(PATH, handle)


def _problem(request: web.Request, status: int, title: str, detail: str) -> web.Response:
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail, "instance": request.path}
    return web.json_response(body, status=status, content_type="application/problem+json")


def same_origin(request: web.Request) -> bool:
    """The Origin header is present and names the address the request was sent to."""
    origin = request.headers.get("Origin", "")
    host = request.headers.get("X-Forwarded-Host", request.host)
    return bool(origin) and urlsplit(origin).netloc == host


def offered_ticket(request: web.Request) -> str | None:
    """The ticket offered as a subprotocol, when the shell's own subprotocol is offered too."""
    offered = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]
    token = next((p[len(TICKET_PREFIX) :] for p in offered if p.startswith(TICKET_PREFIX)), "")
    return token if PROTOCOL in offered and token else None


async def _blocking(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking function (storage, accounts) in a thread, off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(None, functools.partial(fn, *args, **kwargs))


def _refused(user: str, ip: str, instance: str, why: str) -> None:
    audit.record(user, ip, "shell refused", instance=instance, ok=False, detail=why)


async def handle(request: web.Request) -> web.StreamResponse:
    from .events import _session_of  # the panel's session cookie, read as Flask reads it

    name = request.match_info["name"]
    ip = client_ip_of(request.headers, request.remote or "")
    if not same_origin(request):
        await _blocking(_refused, "-", ip, name, "the connection did not come from the panel's page")
        return _problem(request, 403, "Forbidden", "Request blocked: it did not come from this panel.")
    token = offered_ticket(request)
    if token is None:
        return _problem(request, 400, "Bad Request", f'Offer the subprotocols "{PROTOCOL}" and "ticket.<ticket>".')
    ticket = tickets.take(token)  # used up now, whatever happens next
    session_data = _session_of(request) or {}
    user = await _blocking(user_of_session, session_data, True, audience(request.host)) if session_data else None
    if user is None:
        return _problem(request, 401, "Unauthorized", "Your session has ended. Please sign in again.")
    if ticket is None:
        await _blocking(_refused, user["name"], ip, name, "ticket unknown, used or expired")
        return _problem(request, 401, "Unauthorized", "This shell's ticket expired or was used. Open the shell again.")
    if (
        ticket.user != user["name"]
        or ticket.sid != session_data.get("sid")
        or ticket.instance != name
        or session_data.get("restricted")
    ):
        await _blocking(_refused, user["name"], ip, name, "ticket of another session or instance")
        return _problem(request, 403, "Forbidden", "This ticket was issued to another session or instance.")
    try:
        plan: policy.Plan = await _blocking(
            policy.plan, user, name, ticket.target, as_root=ticket.as_root, confirmed=ticket.confirmed
        )
    except Refused as refused:
        await _blocking(_refused, user["name"], ip, name, str(refused))
        return _problem(request, refused.status, "Refused", str(refused))
    size = sessions.clamp_size(_number(request.query.get("cols")), _number(request.query.get("rows")))
    cols, rows = size or (80, 24)
    session = sessions.Session(id=sessions.new_id(), user=user["name"], ip=ip, plan=plan, cols=cols, rows=rows)
    try:
        sessions.register(session)
    except Refused as refused:
        await _blocking(_refused, user["name"], ip, name, str(refused))
        return _problem(request, refused.status, "Too Many Requests", str(refused))
    try:
        policy.check_container(await docker_exec.inspect_container(plan.container), plan)
    except (Refused, docker_exec.DockerError) as error:
        sessions.unregister(session.id)
        status = error.status if isinstance(error, Refused) else 503
        if isinstance(error, docker_exec.DockerError) and error.status == 404:
            status, error = 409, Refused(
                409, "not-running", "The instance is not running: start it, then open the shell."
            )
        await _blocking(_refused, user["name"], ip, name, str(error))
        return _problem(request, status or 503, "Refused", str(error))
    ws: web.WebSocketResponse | None = None
    try:
        ws = web.WebSocketResponse(
            protocols=(PROTOCOL,),
            heartbeat=HEARTBEAT_SECONDS,
            max_msg_size=sessions.MAX_INPUT_BYTES,
            compress=False,
        )
        await ws.prepare(request)
        await _blocking(opened, session)
        allowed = functools.partial(_still_allowed, dict(session_data), plan)
        await sessions.serve(ws, session, allowed)
        return ws
    finally:
        sessions.unregister(session.id)
        await asyncio.shield(_blocking(closed, session))
        if ws is not None and ws.prepared:
            await sessions.close_socket(ws, session)


def _number(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() and len(value) <= 4 else None


def _still_allowed(session_data: dict[str, Any], plan: policy.Plan) -> bool:
    """The panel session is still valid (not signed out, password not changed) and the rules still allow the shell
    (role, SHELL_ENABLED, production and root rules)."""
    user = user_of_session(session_data, check_idle=False)
    if user is None:
        return False
    try:
        policy.plan(user, plan.instance, plan.target, as_root=plan.root, confirmed=True)
    except Refused:
        return False
    return True


# ---------------------------------------------------------------- audit and the list of past shells


def _document(session: sessions.Session) -> dict[str, Any]:
    plan = session.plan
    return {
        "id": session.id,
        "instance": plan.instance,
        "target": plan.target,
        "container": plan.container,
        "container_user": plan.container_user,
        "root": plan.root,
        "production": plan.production,
        "user": session.user,
        "ip": session.ip,
        "started": round(session.started),
        "ended": None,
        "reason": None,
        "recorded": plan.recorded,
    }


def opened(session: sessions.Session) -> None:
    plan = session.plan
    recordings.save(_document(session))
    as_whom = f"{plan.target} as {plan.container_user}" + (" (root)" if plan.root else "")
    recorded = "recorded" if plan.recorded else "not recorded"
    audit.record(
        session.user, session.ip, "shell opened", instance=plan.instance, detail=f"{as_whom}; {recorded}; {session.id}"
    )


def closed(session: sessions.Session) -> None:
    plan = session.plan
    ended = time.time()
    seconds = round(ended - session.started)
    reason = session.reason or "closed"
    document = {
        **_document(session),
        "ended": round(ended),
        "duration": seconds,
        "reason": reason,
        "detail": session.detail[:300],
        "recording_bytes": session.recording_bytes,
    }
    recordings.save(document)
    took = f"{seconds // 60} min {seconds % 60} s"
    detail = f"{plan.target}; {took}; {reason}" + (f" ({session.detail})" if session.detail else "")
    audit.record(
        session.user,
        session.ip,
        "shell closed",
        instance=plan.instance,
        ok=reason != "error",
        detail=f"{detail}; {session.id}",
    )
    recordings.prune()


# ---------------------------------------------------------------- when the panel starts and stops


async def end_left_over() -> None:
    """Shells left open when the panel last stopped (a crash, a restart): end their processes and close them in the
    list. Run once when the panel starts."""
    past: list[dict[str, Any]] = await _blocking(recordings.past, None, 10_000)
    unfinished = [d for d in past if not d.get("ended")]
    for document in unfinished:
        container, user, session_id = document.get("container"), document.get("container_user"), document.get("id")
        if isinstance(container, str) and isinstance(user, str) and isinstance(session_id, str):
            await docker_exec.hang_up(container, session_id, user)
        await _blocking(recordings.save, {**document, "ended": round(time.time()), "reason": "stopping"})


def _hang_up_on_exit() -> None:
    """When the panel stops, end the processes of open shells (the docker command line: the event loop may be gone
    already)."""
    import subprocess

    for session in sessions.open_sessions():
        plan = session.plan
        argv = ["sh", "-c", docker_exec.HANG_UP_SCRIPT, "sudoo-hang-up", session.id]
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["docker", "exec", "-u", plan.container_user, plan.container, *argv], timeout=10, capture_output=True
            )


atexit.register(_hang_up_on_exit)
