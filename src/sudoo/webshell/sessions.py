"""Open shells: how many there are, and the bridge between a browser's WebSocket and a process in a container.

Messages on the socket (web/v1/shell_socket.py opens it):

    browser -> panel   binary: what is typed (bytes for the terminal)
                       text:   {"type": "resize", "cols": 120, "rows": 32}
    panel -> browser   binary: what the terminal shows
                       text:   {"type": "ready", ...}   the shell is open: where, as whom, its limits, recorded or not
                               {"type": "notice", "code": "idle" | "ending", "seconds": 60}   it closes soon
                               {"type": "closed", "reason": ..., "detail": ...}   it closed, and why

A shell closes (reason) when its process ends ("exit"), the browser goes ("closed"), nobody typed for
SHELL_IDLE_MINUTES ("idle"), it is open for SHELL_MAX_MINUTES ("max-length"), its panel session ends or the
person may no longer open it ("not-allowed", checked every RECHECK_SECONDS), a super admin ends it ("ended"), the
panel stops ("stopping"), its recording is full ("recording-full") or something fails ("error"). Whatever the
reason, every process of the shell is then ended (docker_exec.hang_up).

Limits: SHELL_MAX_PER_USER shells per person, SHELL_MAX_PER_INSTANCE per instance and MAX_OPEN on the panel,
counted with the tickets not used yet. The processes run inside the instance's container, so its memory, processor
and process limits apply to them too, and they run with a lower priority than Odoo (policy.NICE).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType, web

from sudoo.config import settings

from . import docker_exec, tickets
from .policy import Plan, Refused
from .recordings import MB, Recorder

MAX_OPEN = 100  # shells open on the panel at the same time, whatever the other limits say
RECHECK_SECONDS = 15
WARN_SECONDS = 60  # a notice this long before an idle or too long shell closes
MAX_INPUT_BYTES = 64 * 1024  # one message from the browser (a paste); the socket refuses larger ones
COLS, ROWS = (2, 1000), (1, 500)  # sizes accepted for a terminal


@dataclass
class Session:
    id: str
    user: str
    ip: str
    plan: Plan
    cols: int = 80
    rows: int = 24
    started: float = field(default_factory=time.time)
    reason: str | None = None
    detail: str = ""
    recording_bytes: int = 0
    _loop: asyncio.AbstractEventLoop | None = None
    _stopped: asyncio.Event | None = None

    def stop(self, reason: str, detail: str = "") -> None:
        """Close the shell (on the event loop); the first reason given is the one kept."""
        if self.reason is None:
            self.reason, self.detail = reason, detail
        if self._stopped is not None:
            self._stopped.set()

    def stop_from_thread(self, reason: str) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.stop, reason)
        elif self.reason is None:
            self.reason = reason  # not started yet: serve() sees it and ends at once


OPEN: dict[str, Session] = {}
_LOCK = threading.Lock()


def new_id() -> str:
    return secrets.token_hex(12)


def open_sessions(*, user: str | None = None, instance: str | None = None) -> list[Session]:
    with _LOCK:
        return [
            s
            for s in OPEN.values()
            if (user is None or s.user == user) and (instance is None or s.plan.instance == instance)
        ]


def _beyond_limits(mine: int, here: int, total: int, instance: str) -> Refused | None:
    if mine >= settings.shell_max_per_user:
        return Refused(429, "too-many-mine", f"You already have {mine} shells open: close one first.")
    if here >= settings.shell_max_per_instance:
        return Refused(429, "too-many-here", f"{instance} already has {here} shells open: close one first.")
    if total >= MAX_OPEN:
        return Refused(503, "too-many", "Too many shells are open on this panel. Try again later.")
    return None


def room_for(user: str, instance: str) -> Refused | None:
    """Why one more shell of `user` in `instance` would be beyond the limits, counting the tickets not used yet
    (None: it is not)."""
    with _LOCK:
        mine = sum(1 for s in OPEN.values() if s.user == user)
        here = sum(1 for s in OPEN.values() if s.plan.instance == instance)
        total = len(OPEN)
    mine += tickets.waiting(user=user)
    here += tickets.waiting(instance=instance)
    return _beyond_limits(mine, here, total + tickets.waiting(), instance)


def register(session: Session) -> None:
    """Count `session` as open, or raise Refused when it would be beyond the limits (checked again here, without
    tickets: two tickets may have been issued at the same time)."""
    with _LOCK:
        mine = sum(1 for s in OPEN.values() if s.user == session.user)
        here = sum(1 for s in OPEN.values() if s.plan.instance == session.plan.instance)
        refusal = _beyond_limits(mine, here, len(OPEN), session.plan.instance)
        if refusal is not None:
            raise refusal
        OPEN[session.id] = session


def unregister(session_id: str) -> None:
    with _LOCK:
        OPEN.pop(session_id, None)


def end(session_id: str, reason: str = "ended") -> bool:
    """Close an open shell from any thread; False when it is not open."""
    with _LOCK:
        found = OPEN.get(session_id)
    if found is None:
        return False
    found.stop_from_thread(reason)
    return True


def end_all(reason: str) -> None:
    for session in open_sessions():
        session.stop_from_thread(reason)


def clamp_size(cols: object, rows: object) -> tuple[int, int] | None:
    """(cols, rows) when both are whole numbers in the accepted ranges, else None."""
    if not isinstance(cols, int) or not isinstance(rows, int) or isinstance(cols, bool) or isinstance(rows, bool):
        return None
    if not (COLS[0] <= cols <= COLS[1] and ROWS[0] <= rows <= ROWS[1]):
        return None
    return cols, rows


# ---------------------------------------------------------------- the bridge


async def serve(ws: web.WebSocketResponse, session: Session, still_allowed: Callable[[], bool]) -> None:
    """Run the shell until it closes (session.reason says why), then end its processes and tell the browser why.
    `still_allowed` (blocking, run in a thread) tells whether the person may still use it. The socket itself is
    left open: close_socket() closes it."""
    loop = asyncio.get_running_loop()
    session._loop, session._stopped = loop, asyncio.Event()
    plan = session.plan
    stream: docker_exec.Stream | None = None
    recorder: Recorder | None = None
    created = False
    try:
        if session.reason is not None:  # ended (by a super admin) before it even started
            return
        env = {"TERM": "xterm-256color", "COLORTERM": "truecolor", "LANG": "C.UTF-8", "SUDOO_SHELL": session.id}
        exec_id = await docker_exec.create(
            plan.container,
            list(plan.argv),
            user=plan.container_user,
            env=env,
            workdir=plan.workdir,
            cols=session.cols,
            rows=session.rows,
        )
        created = True
        stream = await docker_exec.start(exec_id)
        if plan.recorded:
            header = {
                "version": 2,
                "width": session.cols,
                "height": session.rows,
                "timestamp": int(session.started),
                "title": f"{plan.instance} ({plan.target}) by {session.user}",
                "env": {"TERM": env["TERM"]},
                "sudoo": _summary(session),
            }
            limit = int(settings.shell_recording_max_mb * MB)
            recorder = await loop.run_in_executor(None, Recorder, session.id, header, limit)
        await ws.send_str(json.dumps({"type": "ready", **_summary(session)}))
        if session.reason is None:
            await _bridge(ws, session, stream, exec_id, recorder, still_allowed)
    except docker_exec.DockerError as error:
        session.stop("error", str(error))
    except ConnectionResetError:
        session.stop("closed")
    except OSError as error:  # the recording cannot be written: no unrecorded shell where one is required
        session.stop("error", f"The recording could not be written ({error.strerror or error}).")
    except asyncio.CancelledError:
        session.stop("closed")
        raise
    finally:
        if stream is not None:
            stream.close()
        if recorder is not None:
            recorder.note(f"closed: {session.reason or 'closed'}")
            session.recording_bytes = recorder.close()
        if created:
            await asyncio.shield(docker_exec.hang_up(plan.container, session.id, plan.container_user))
        if not ws.closed:  # say why; the caller closes the socket once the shell is written down
            with contextlib.suppress(ConnectionResetError):
                await ws.send_str(json.dumps({"type": "closed", "reason": session.reason, "detail": session.detail}))


def _summary(session: Session) -> dict[str, Any]:
    plan = session.plan
    return {
        "session": session.id,
        "instance": plan.instance,
        "target": plan.target,
        "container": plan.container,
        "container_user": plan.container_user,
        "user": session.user,
        "root": plan.root,
        "production": plan.production,
        "recorded": plan.recorded,
        "idle_seconds": int(settings.shell_idle_minutes * 60),
        "max_seconds": int(settings.shell_max_minutes * 60),
        "started": int(session.started),
    }


async def _bridge(
    ws: web.WebSocketResponse,
    session: Session,
    stream: docker_exec.Stream,
    exec_id: str,
    recorder: Recorder | None,
    still_allowed: Callable[[], bool],
) -> None:
    loop = asyncio.get_running_loop()
    last_input = loop.time()
    opened = loop.time()

    async def output() -> None:
        while True:
            data = await stream.read()
            if not data:
                session.stop("exit")
                return
            if recorder is not None:
                recorder.output(data)
            await ws.send_bytes(data)

    async def typed() -> None:
        nonlocal last_input
        async for message in ws:
            if message.type == WSMsgType.BINARY:
                if recorder is not None and not recorder.input(message.data):
                    session.stop("recording-full", "The recording of this shell is full.")
                    return
                last_input = loop.time()
                await stream.write(message.data)
            elif message.type == WSMsgType.TEXT:
                await _control(message.data, session, exec_id, recorder)
            else:
                break
        session.stop("closed")

    async def watch() -> None:
        warned_idle = warned_end = False
        checked = loop.time()
        while True:
            await asyncio.sleep(1)
            now = loop.time()
            idle_left = settings.shell_idle_minutes * 60 - (now - last_input)
            end_left = settings.shell_max_minutes * 60 - (now - opened)
            if idle_left <= 0:
                session.stop("idle")
                return
            if end_left <= 0:
                session.stop("max-length")
                return
            if idle_left <= WARN_SECONDS and not warned_idle:
                warned_idle = True
                await ws.send_str(json.dumps({"type": "notice", "code": "idle", "seconds": int(idle_left)}))
            elif idle_left > WARN_SECONDS:
                warned_idle = False
            if end_left <= WARN_SECONDS and not warned_end:
                warned_end = True
                await ws.send_str(json.dumps({"type": "notice", "code": "ending", "seconds": int(end_left)}))
            if now - checked >= RECHECK_SECONDS:
                checked = now
                if not await loop.run_in_executor(None, still_allowed):
                    session.stop("not-allowed")
                    return

    tasks = [asyncio.create_task(_quietly(work(), session)) for work in (output, typed, watch)]
    assert session._stopped is not None
    try:
        await session._stopped.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _quietly(work: Awaitable[None], session: Session) -> None:
    """Run one side of the bridge; a broken connection on either side closes the shell."""
    try:
        await work
    except (ConnectionResetError, ConnectionError, OSError):
        session.stop("closed")
    except docker_exec.DockerError as error:
        session.stop("error", str(error))


async def _control(raw: str, session: Session, exec_id: str, recorder: Recorder | None) -> None:
    """A text message from the browser: only {"type": "resize"} is known; anything else is ignored."""
    try:
        message = json.loads(raw)
    except ValueError:
        return
    if not isinstance(message, dict) or message.get("type") != "resize":
        return
    size = clamp_size(message.get("cols"), message.get("rows"))
    if size is None or size == (session.cols, session.rows):
        return
    session.cols, session.rows = size
    if recorder is not None:
        recorder.resize(*size)
    with contextlib.suppress(docker_exec.DockerError):  # the process has just ended: the shell closes anyway
        await docker_exec.resize(exec_id, *size)


async def close_socket(ws: web.WebSocketResponse, session: Session) -> None:
    """Close the socket (the browser answers the closing handshake, or it times out)."""
    if not ws.closed:
        with contextlib.suppress(ConnectionResetError):
            await ws.close(code=1000 if session.reason in ("exit", "closed") else 4000)
