"""Exec in a pod over the API's WebSocket, protocol v5.channel.k8s.io (asyncio, aiohttp).

Every message is binary: its first byte is the channel, the rest the data.

    0 stdin  1 stdout  2 stderr  3 error (the command's end: a JSON Status)  4 resize (JSON {"Width", "Height"})
    255 close: [255, channel] says that the sender closes that channel (v5; how stdin reaches its end, which v4 cannot
        say, so `psql < dump.sql` could never finish)

`run()` is a whole command (stdin from a binary file, stdout and stderr to others); `Session` is an interactive one
with a terminal, for the web shell (terminal.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from typing import IO, Any

import aiohttp

from .api import EXEC_PROTOCOL, Api, ApiError

STDIN, STDOUT, STDERR, ERROR, RESIZE, CLOSE = 0, 1, 2, 3, 4, 255
CHUNK = 64 * 1024


def exit_code(status: dict[str, Any]) -> int:
    """The exit code in channel 3's Status: 0 for Success, the ExitCode cause, else 1 (the API's own failure)."""
    if status.get("status") == "Success":
        return 0
    for cause in (status.get("details") or {}).get("causes") or []:
        if cause.get("reason") == "ExitCode":
            with contextlib.suppress(TypeError, ValueError):
                return int(cause.get("message"))
    return 1


async def connect(
    api: Api, namespace: str, pod: str, container: str, argv: list[str], *, stdin: bool, tty: bool
) -> tuple[aiohttp.ClientSession, aiohttp.ClientWebSocketResponse]:
    """Open the exec socket; raises ApiError (404: no such pod, 403: not allowed)."""
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15))
    try:
        socket = await session.ws_connect(
            api.exec_url(namespace, pod, container, argv, stdin, tty),
            protocols=(EXEC_PROTOCOL,),
            headers=api.access.headers(),
            ssl=api._ssl,
            max_msg_size=0,
            autoping=True,
        )
    except aiohttp.WSServerHandshakeError as error:
        await session.close()
        raise ApiError(f"exec refused: {error.message}", error.status) from None
    except (aiohttp.ClientError, OSError) as error:
        await session.close()
        raise ApiError(f"The Kubernetes API could not be reached: {error}") from None
    if socket.protocol != EXEC_PROTOCOL:
        await socket.close()
        await session.close()
        raise ApiError("The cluster does not speak v5.channel.k8s.io (Kubernetes 1.30 or newer is needed).")
    return session, socket


async def run(
    api: Api,
    namespace: str,
    pod: str,
    container: str,
    argv: list[str],
    stdin: IO[bytes] | None,
    stdout: Callable[[bytes], Any],
    stderr: Callable[[bytes], Any],
) -> int:
    """Run `argv` in the pod's container to its end; its exit code."""
    session, socket = await connect(api, namespace, pod, container, argv, stdin=stdin is not None, tty=False)
    code: int | None = None
    loop = asyncio.get_running_loop()

    async def feed() -> None:
        assert stdin is not None
        while chunk := await loop.run_in_executor(None, stdin.read, CHUNK):
            await socket.send_bytes(bytes([STDIN]) + chunk)
        await socket.send_bytes(bytes([CLOSE, STDIN]))

    feeder = asyncio.ensure_future(feed()) if stdin is not None else None
    try:
        async for message in socket:
            if message.type != aiohttp.WSMsgType.BINARY or not message.data:
                if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
                continue
            channel, data = message.data[0], message.data[1:]
            if channel == STDOUT:
                stdout(data)
            elif channel == STDERR:
                stderr(data)
            elif channel == ERROR and data:
                code = exit_code(json.loads(data))
    finally:
        if feeder is not None:
            feeder.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionError, aiohttp.ClientError):
                await feeder
        await socket.close()
        await session.close()
    return 1 if code is None else code


class Session:
    """An interactive process with a terminal (stdout and stderr merged by the terminal)."""

    def __init__(self, session: aiohttp.ClientSession, socket: aiohttp.ClientWebSocketResponse) -> None:
        self._session, self._socket = session, socket

    async def read(self) -> bytes:
        """The next output (b"" when the process ended)."""
        while True:
            message = await self._socket.receive()
            if message.type != aiohttp.WSMsgType.BINARY:
                return b""
            if message.data and message.data[0] in (STDOUT, STDERR) and len(message.data) > 1:
                return bytes(message.data[1:])
            if message.data and message.data[0] == ERROR:
                return b""

    async def write(self, data: bytes) -> None:
        await self._socket.send_bytes(bytes([STDIN]) + data)

    async def resize(self, cols: int, rows: int) -> None:
        await self._socket.send_bytes(bytes([RESIZE]) + json.dumps({"Width": cols, "Height": rows}).encode())

    def close(self) -> None:
        asyncio.ensure_future(self._aclose())

    async def _aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._socket.close()
        await self._session.close()
