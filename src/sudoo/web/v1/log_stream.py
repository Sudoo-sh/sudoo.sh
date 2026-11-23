"""GET /api/v1/instances/<name>/logs/stream?log=odoo.log&tail=300&q=: follow an instance's log (Server-Sent Events).

Served on the asyncio loop of the live events (events.py, 127.0.0.1:EVENTS_PORT; the web service forwards this path
there), like the shells' WebSockets: a log followed for an hour is a coroutine, not one of gunicorn's 32 threads.
Same callers and checks as the live events (session cookie with Origin, or an API token with a signature), and
instances.logs in the instance (projects/access.py), as GET /api/v1/instances/<name>/logs.

`log` is odoo.log (the default; install.log and update.log are read in pages, GET .../logs/entries, and jobs'
technical logs are server logs, never streamed); `tail` the number of earlier lines sent first (0-5000, default 300);
the filters of the pages (instances/logs.filters_from: `level`, `logger`, `q`, `regex`, `since`, `until`,
`tracebacks`) keep whole entries: a line in Odoo's format with its traceback. An entry is sent once it is complete
(the next one started, or nothing came for BATCH_SECONDS). Events:

    event: lines   data: {"lines": [...], "ids": [...]}    id: where to resume (a time stamp)
    event: end     data: {"reason": "max_duration" | "stopped" | "slow_pattern"}

`ids` gives each line's id (Docker's time stamp, nine decimals), the id GET .../logs/entries?around= takes.

The stream ends after MAX_SECONDS ("max_duration"): the client opens it again with Last-Event-ID (or ?after=<id>)
and continues where it stopped, so a forgotten tab cannot hold a connection for ever. It also ends when Odoo's
container stops ("stopped"). Lines are read from Docker only as fast as they can be sent: a slow client slows the reading (the pipe fills and Docker waits), nothing piles up in memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from typing import Any

from aiohttp import web

from sudoo.instances import logs, store
from sudoo.projects import access

PATH = "/api/v1/instances/{name}/logs/stream"
MAX_SECONDS = 300
"""A stream ends after this long; the client continues in a new one."""
MAX_PER_CALLER = 4
MAX_STREAMS = 100
HEARTBEAT_SECONDS = 20
BATCH_LINES = 200
BATCH_SECONDS = 0.25
MAX_TAIL = 5000
_RECHECK_SECONDS = 60
_LINE_LIMIT = 4 * 2**20  # a longer line ends the stream rather than fill the memory
_STAMP_RE = re.compile(r"^\d{4}-\d\d-\d\dT[0-9:.]+(Z|[+-]\d\d:\d\d)$")
_OPEN: dict[str, int] = {}  # caller -> open log streams; event loop thread only


def add_routes(app: web.Application) -> None:
    app.router.add_get(PATH, handle)


def _problem(request: web.Request, status: int, title: str, detail: str) -> web.Response:
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail, "instance": request.path}
    return web.json_response(body, status=status, content_type="application/problem+json")


def _frame(event: str, data: Any, event_id: str | None = None) -> bytes:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def _resume_from(request: web.Request) -> str:
    return (request.headers.get("Last-Event-ID") or request.query.get("after", "")).strip()[:64]


async def handle(request: web.Request) -> web.StreamResponse:
    from .events import _caller, _caller_key

    user, session, refusal = _caller(request)
    if refusal is not None or user is None:
        return refusal or _problem(request, 401, "Unauthorized", "Your session has ended. Please sign in again.")
    name = request.match_info["name"]
    if not store.NAME_RE.match(name):
        return _problem(request, 404, "Not Found", "This instance does not exist.")
    loop = asyncio.get_running_loop()
    refused = await loop.run_in_executor(None, access.instance_refusal, user, "instances.logs", name)
    if refused is not None:
        status, message = refused
        title = "Not Found" if status == 404 else "Forbidden"
        return _problem(request, status, title, message or "Your role cannot read this instance's logs.")
    if not await loop.run_in_executor(None, store.exists, name):
        return _problem(request, 404, "Not Found", "This instance does not exist.")
    log = request.query.get("log", logs.ODOO_LOG)
    if log != logs.ODOO_LOG:
        return _problem(request, 400, "Bad Request", f"Only {logs.ODOO_LOG} can be followed.")
    raw_tail = request.query.get("tail", "300")
    if not re.fullmatch(r"[0-9]{1,5}", raw_tail) or int(raw_tail) > MAX_TAIL:
        return _problem(request, 400, "Bad Request", f"tail must be a whole number from 0 to {MAX_TAIL}.")
    try:  # compiling a pattern runs its probes (at most a few tens of milliseconds): off the loop
        filters = await loop.run_in_executor(None, logs.filters_from, dict(request.query))
    except ValueError as error:
        return _problem(request, 400, "Bad Request", str(error))
    key = _caller_key(user)
    if _OPEN.get(key, 0) >= MAX_PER_CALLER or sum(_OPEN.values()) >= MAX_STREAMS:
        refused_now = _problem(request, 429, "Too Many Requests", "Too many logs followed at once. Close one first.")
        refused_now.headers["Retry-After"] = "10"
        return refused_now
    _OPEN[key] = _OPEN.get(key, 0) + 1
    try:
        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            }
        )
        await response.prepare(request)
        await response.write(b"retry: 2000\n\n")
        follow = Follow(response, filters, user, session)
        with contextlib.suppress(ConnectionResetError):  # the client went away
            await follow.odoo(name, int(raw_tail), _resume_from(request))
        return response
    finally:
        _OPEN[key] -= 1
        if not _OPEN[key]:
            del _OPEN[key]


class Follow:
    """Sends the entries of one log that pass the filters to one client, in batches, until MAX_SECONDS have passed.

    The lines of the entry being written wait in `entry` until it is complete (a new entry starts, or nothing comes
    for BATCH_SECONDS); only then is it kept or dropped as a whole, and the resume id moves past it."""

    def __init__(
        self,
        response: web.StreamResponse,
        filters: logs.Filters,
        user: dict[str, str],
        session: dict[str, Any] | None,
    ) -> None:
        self.response = response
        self.filters = filters
        self.user = user
        self.session = session
        self.loop = asyncio.get_running_loop()
        self.deadline = self.loop.time() + MAX_SECONDS
        self.checked = self.loop.time()
        self.batch: list[str] = []
        self.ids: list[str] = []
        self.entry: logs.Entry | None = None
        self.entry_ids: list[str] = []
        self.entry_stamp: str | None = None  # the raw stamp of the entry's last line (where to resume after it)
        self.last_id: str | None = None
        self.sent_at = self.loop.time()

    async def flush(self) -> None:
        if self.batch:
            await self.response.write(_frame("lines", {"lines": self.batch, "ids": self.ids}, self.last_id))
            self.batch, self.ids = [], []
            self.sent_at = self.loop.time()
        self.filters.spent = 0.0  # a pattern's time limit holds for each batch, not for the whole stream

    async def complete(self) -> None:
        """Decide the entry waiting: keep it (all its lines) or drop it. SlowPattern from the filters goes up."""
        entry, self.entry = self.entry, None
        if entry is None:
            return
        if self.entry_stamp is not None:
            self.last_id = self.entry_stamp
        if self.filters.empty or self.filters.keep(entry):
            self.batch.extend(entry.lines)
            self.ids.extend(self.entry_ids)
        self.entry_ids = []
        if len(self.batch) >= BATCH_LINES:
            await self.flush()

    async def add(self, text: str, stamp: str | None) -> None:
        if self.entry is not None and self.entry.head is not None and logs.parse_line(text) is None:
            self.entry.lines.append(text)
        else:
            await self.complete()
            line_id = logs.normal_stamp(stamp or "")
            self.entry = logs.Entry(line_id, [text], logs.parse_line(text))
        self.entry_ids.append(logs.normal_stamp(stamp or ""))
        if stamp is not None:
            self.entry_stamp = stamp

    async def end(self, reason: str) -> None:
        with contextlib.suppress(logs.SlowPattern):
            await self.complete()
        await self.flush()
        await self.response.write(_frame("end", {"reason": reason}, self.last_id))

    async def allowed(self) -> bool:
        """Whether the caller may still read (checked every minute, as the live events do)."""
        from .events import _still_allowed

        if self.loop.time() - self.checked < _RECHECK_SECONDS:
            return True
        self.checked = self.loop.time()
        return bool(await self.loop.run_in_executor(None, _still_allowed, self.user, self.session))

    def remaining(self) -> float:
        return self.deadline - self.loop.time()

    async def odoo(self, name: str, tail: int, after: str) -> None:
        since = after if _STAMP_RE.match(after) else None
        proc = await asyncio.create_subprocess_exec(
            *logs.odoo_log_command(name, tail, follow=True, since=since),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=_LINE_LIMIT,
        )
        assert proc.stdout is not None
        resume = logs.normal_stamp(since or "")
        try:
            while True:
                if self.remaining() <= 0:
                    await self.end("max_duration")
                    return
                if not await self.allowed():
                    return
                waiting = self.batch or self.entry is not None
                wait = BATCH_SECONDS if waiting else min(HEARTBEAT_SECONDS, self.remaining())
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), max(wait, 0.01))
                except TimeoutError:
                    if waiting:
                        await self.complete()
                        await self.flush()
                    elif self.loop.time() - self.sent_at >= HEARTBEAT_SECONDS:
                        await self.response.write(b": ping\n\n")
                        self.sent_at = self.loop.time()
                    continue
                except ValueError:  # a line longer than _LINE_LIMIT
                    await self.end("stopped")
                    return
                if not raw:  # the container stopped, or does not exist
                    await self.end("stopped")
                    return
                stamp, text = logs.split_stamp(raw.decode(errors="replace").rstrip("\n"))
                if resume and logs.normal_stamp(stamp) == resume:  # --since includes the line it starts from
                    continue
                await self.add(text, stamp or None)
                if self.loop.time() - self.sent_at >= BATCH_SECONDS:
                    await self.flush()  # the entry being written stays until it is complete
        except logs.SlowPattern:
            await self.flush()
            await self.response.write(_frame("end", {"reason": "slow_pattern"}, self.last_id))
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()


def open_streams() -> int:
    """How many logs are followed now (for tests and the server's figures)."""
    return sum(_OPEN.values())
