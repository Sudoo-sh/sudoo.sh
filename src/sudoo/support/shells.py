"""Support's shells: the web shell (webshell/) driven through the support channel instead of a browser WebSocket.

A shell is opened, written to, read from, resized and closed by separate requests from the hub (channel.py, kind
"shell"); `Relayed` stands in for the WebSocket that webshell/sessions.serve expects: what the shell shows is kept
as numbered frames until support reads them (`after`: the last frame seen), and what support types goes to the
process through a queue on the event loop. Everything else is the web shell's own: the same rules (policy.plan:
role, project access, locked branches, production, never root), the same limits, the same checks of the container,
the same audit entries and list of past shells. A support shell is always recorded, whatever SHELL_RECORDING says.

A shell belongs to the support session that opened it; it closes when that session ends, when its grant is
withdrawn or expires (at once: end_for_grant), and otherwise as every shell does (idle, too long, exit).
"""

from __future__ import annotations

import asyncio
import codecs
import json
import threading
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType

from sudoo.instances import gateway, monitor
from sudoo.webshell import docker_exec, policy, sessions
from sudoo.webshell.policy import Refused

READ_WAIT_SECONDS = 10  # a read waits this long for output
GATHER_SECONDS = 0.3  # once output comes, a little more is gathered into the same answer
OPEN_WAIT_SECONDS = 10
MAX_FRAMES = 1000
MAX_BUFFERED_BYTES = 512 * 1024
ASLEEP = ("paused", "switched_off", "stopped")


@dataclass
class _Message:
    type: WSMsgType
    data: Any


@dataclass
class Relayed:
    """The stand-in for a browser's WebSocket (send_str, send_bytes, iteration, closed)."""

    id: str
    session_id: str
    grant_id: str
    frames: list[dict[str, Any]] = field(default_factory=list)
    next_seq: int = 1
    buffered: int = 0
    finished: bool = False
    cond: threading.Condition = field(default_factory=threading.Condition)
    inbox: asyncio.Queue[_Message] | None = None
    session: sessions.Session | None = None
    _decoder: Any = field(default_factory=lambda: codecs.getincrementaldecoder("utf-8")("replace"))

    @property
    def closed(self) -> bool:
        return self.finished

    def _add(self, kind: str, data: Any) -> None:
        with self.cond:
            self.frames.append({"seq": self.next_seq, "type": kind, "data": data})
            self.next_seq += 1
            self.buffered += len(data) if isinstance(data, str) else 64
            while self.frames and (len(self.frames) > MAX_FRAMES or self.buffered > MAX_BUFFERED_BYTES):
                old = self.frames.pop(0)
                self.buffered -= len(old["data"]) if isinstance(old["data"], str) else 64
            if kind == "closed":
                self.finished = True
            self.cond.notify_all()

    async def send_str(self, text: str) -> None:
        try:
            message = json.loads(text)
        except ValueError:
            return
        kind = message.get("type") if isinstance(message, dict) else None
        self._add(kind if kind in ("ready", "notice", "closed") else "notice", message)

    async def send_bytes(self, data: bytes) -> None:
        text = self._decoder.decode(data)
        if text:
            self._add("output", text)

    async def _messages(self) -> AsyncIterator[_Message]:
        assert self.inbox is not None
        while True:
            message = await self.inbox.get()
            if message.type == WSMsgType.CLOSE:
                return
            yield message

    def __aiter__(self) -> AsyncIterator[_Message]:
        return self._messages()

    def put(self, message: _Message) -> None:
        """Hand something typed to the shell (from any thread)."""
        session = self.session
        if self.inbox is None or session is None or session._loop is None:
            return
        session._loop.call_soon_threadsafe(self.inbox.put_nowait, message)

    def read(self, after: int, wait: float) -> dict[str, Any]:
        deadline = time.monotonic() + wait
        with self.cond:
            while not any(f["seq"] > after for f in self.frames) and not self.finished:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
        if any(f["seq"] > after for f in self.frames) and not self.finished:
            time.sleep(GATHER_SECONDS)
        with self.cond:
            found = [dict(f) for f in self.frames if f["seq"] > after]
            return {"frames": found, "next": self.next_seq - 1, "closed": self.finished}


OPEN: dict[str, Relayed] = {}
_LOCK = threading.Lock()


def _problem(status: int, detail: str, code: str | None = None) -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = {"type": "about:blank", "status": status, "detail": detail}
    if code:
        body["code"] = code
    return status, body


def _still_allowed(user: Mapping[str, Any], shell: policy.Plan, session_id: str) -> bool:
    from . import channel, grant_of

    if grant_of(user) is None or channel.session_open(session_id, str(user["support"]["grant"])) is None:
        return False
    try:
        policy.plan(dict(user), shell.instance, shell.target, as_root=False, confirmed=True)
    except Refused:
        return False
    return True


def handle(user: Mapping[str, Any], step: Mapping[str, Any]) -> tuple[int, Any]:
    """One step of a support shell: open, write, read, resize, close."""
    op = step.get("op")
    if op == "open":
        return _open(user, step)
    with _LOCK:
        relayed = OPEN.get(str(step.get("shell") or ""))
    if relayed is None or relayed.session_id != user["support"]["session"]:
        return _problem(404, "There is no such shell of this support session.")
    if op == "read":
        after = step.get("after")
        return 200, relayed.read(after if isinstance(after, int) and after >= 0 else 0, READ_WAIT_SECONDS)
    if relayed.finished:
        return _problem(409, "This shell has closed.", "closed")
    if op == "write":
        data = step.get("data")
        if not isinstance(data, str) or len(data) > sessions.MAX_INPUT_BYTES:
            return _problem(400, "Send the text to type (at most 64 KB).")
        relayed.put(_Message(WSMsgType.BINARY, data.encode()))
        return 200, {}
    if op == "resize":
        size = sessions.clamp_size(step.get("cols"), step.get("rows"))
        if size is None:
            return _problem(400, "Give cols (2 to 1000) and rows (1 to 500).")
        relayed.put(_Message(WSMsgType.TEXT, json.dumps({"type": "resize", "cols": size[0], "rows": size[1]})))
        return 200, {}
    if op == "close":
        relayed.put(_Message(WSMsgType.CLOSE, None))
        return 200, {"closed": True}
    return _problem(400, 'Choose "open", "write", "read", "resize" or "close".')


def _open(user: Mapping[str, Any], step: Mapping[str, Any]) -> tuple[int, Any]:
    from sudoo.core import audit

    from .channel import IP

    name, target = str(step.get("instance") or ""), str(step.get("target") or "shell")
    confirmed = step.get("confirm_production") is True
    try:
        plan = policy.plan(dict(user), name, target, as_root=False, confirmed=confirmed)
    except Refused as refused:
        audit.record(user["name"], IP, "shell refused", instance=name or None, ok=False, detail=str(refused))
        return _problem(refused.status, str(refused), refused.code)
    if monitor.status(name) in ASLEEP:
        return _problem(409, "The instance is not running: start it, then open the shell.", "not-running")
    session = sessions.Session(id=sessions.new_id(), user=user["name"], ip=IP, plan=plan)
    if step.get("cols") is not None or step.get("rows") is not None:
        size = sessions.clamp_size(step.get("cols"), step.get("rows"))
        if size:
            session.cols, session.rows = size
    try:
        sessions.register(session)
    except Refused as refused:
        return _problem(refused.status, str(refused), refused.code)
    relayed = Relayed(id=session.id, session_id=str(user["support"]["session"]), grant_id=str(user["support"]["grant"]))
    relayed.session = session
    with _LOCK:
        OPEN[relayed.id] = relayed
    gateway.submit(_run(relayed, session, dict(user)))
    first = relayed.read(0, OPEN_WAIT_SECONDS)
    if first["closed"] and not any(f["type"] == "ready" for f in first["frames"]):
        closing: dict[str, Any] = next((f["data"] for f in first["frames"] if f["type"] == "closed"), {})
        return _problem(409, str(closing.get("detail") or "The shell could not be opened."), "not-opened")
    return 201, {
        "shell": relayed.id,
        "instance": plan.instance,
        "target": plan.target,
        "production": plan.production,
        "recorded": plan.recorded,
        **first,
    }


async def _run(relayed: Relayed, session: sessions.Session, user: dict[str, Any]) -> None:
    from sudoo.web.v1 import shell_socket

    loop = asyncio.get_running_loop()
    relayed.inbox = asyncio.Queue()
    plan = session.plan
    try:
        try:
            policy.check_container(await docker_exec.inspect_container(plan.container), plan)
        except (Refused, docker_exec.DockerError) as error:
            detail = str(error)
            if isinstance(error, docker_exec.DockerError) and error.status == 404:
                detail = "The instance is not running: start it, then open the shell."
            await loop.run_in_executor(None, shell_socket._refused, session.user, session.ip, plan.instance, detail)
            await relayed.send_str(json.dumps({"type": "closed", "reason": "error", "detail": detail}))
            return
        await loop.run_in_executor(None, shell_socket.opened, session)
        try:
            await sessions.serve(relayed, session, lambda: _still_allowed(user, plan, relayed.session_id))  # type: ignore[arg-type]
        finally:
            await asyncio.shield(loop.run_in_executor(None, shell_socket.closed, session))
    finally:
        sessions.unregister(session.id)
        if not relayed.finished:
            relayed._add("closed", {"type": "closed", "reason": session.reason or "closed", "detail": session.detail})
        loop.call_later(300, lambda: OPEN.pop(relayed.id, None))


def end_for_grant(grant_id: str) -> None:
    """Close every shell of a grant now (withdrawn or expired)."""
    for relayed in _matching(lambda r: r.grant_id == grant_id):
        if relayed.session is not None:
            relayed.session.stop_from_thread("not-allowed")


def end_for_session(session_id: str) -> None:
    for relayed in _matching(lambda r: r.session_id == session_id):
        if relayed.session is not None:
            relayed.session.stop_from_thread("not-allowed")


def _matching(test: Any) -> list[Relayed]:
    with _LOCK:
        return [r for r in OPEN.values() if test(r) and not r.finished]
