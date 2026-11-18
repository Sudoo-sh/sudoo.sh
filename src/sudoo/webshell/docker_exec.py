"""Interactive processes in a container, through the Docker Engine API on the mounted socket (asyncio).

The docker command line would need a pseudo-terminal on the panel's side and signals to pass on the window size;
the Engine API offers each step directly, without a terminal or a thread in the panel:

    POST /containers/<name>/exec      create the process (command, user, environment, working folder, size)
    POST /exec/<id>/start             start it; the connection is then "hijacked" into the process's terminal
    POST /exec/<id>/resize            the browser's terminal changed size

Docker has no way to stop a process started this way: closing the connection leaves it running. So every process
of a shell carries SUDOO_SHELL=<session id> in its environment, and hang_up() ends them all: a short script in the
same container, as the same user, sends SIGHUP (what a closed terminal sends) to every process with that mark, then
SIGKILL a second later to those still there.

Only containers are reached, never the server: every call names a container that policy.check_container accepted.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import aiohttp

READ_BYTES = 32 * 1024
_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Ends every process of a session: $1 is the session id (hexadecimal), checked by the caller. POSIX sh, tr and grep
# only (Debian and Alpine images have them); the script's own processes do not carry the mark.
HANG_UP_SCRIPT = """\
mark="SUDOO_SHELL=$1"
for signal in HUP KILL; do
    found=""
    for d in /proc/[0-9]*; do
        p=${d#/proc/}
        [ "$p" = "$$" ] && continue
        if tr "\\000" "\\n" < "$d/environ" 2>/dev/null | grep -qxF "$mark"; then
            kill "-$signal" "$p" 2>/dev/null && found=1
        fi
    done
    [ -z "$found" ] && break
    [ "$signal" = HUP ] && sleep 1
done
exit 0
"""


class DockerError(Exception):
    """The Docker Engine refused or failed a call; `status` is its HTTP status (0: not reachable)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def socket_path() -> str:
    """The Docker socket: DOCKER_HOST when it names a unix socket, else the usual place (compose.yaml mounts it)."""
    host = os.environ.get("DOCKER_HOST", "")
    return host[len("unix://") :] if host.startswith("unix://") else "/var/run/docker.sock"


async def _call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """One JSON call to the Engine API; the decoded answer (None when empty)."""
    try:
        async with (
            aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=socket_path()), timeout=_TIMEOUT) as session,
            session.request(method, f"http://docker{path}", json=body) as answer,
        ):
            text = await answer.text()
            if answer.status >= 400:
                try:
                    message = json.loads(text).get("message", text)
                except ValueError:
                    message = text
                raise DockerError(str(message)[:300], answer.status)
            return json.loads(text) if text.strip() else None
    except (aiohttp.ClientError, OSError, TimeoutError) as error:
        raise DockerError(f"Docker is not reachable: {error}") from error


async def inspect_container(name: str) -> dict[str, Any]:
    """GET /containers/<name>/json; raises DockerError (status 404 when there is no such container)."""
    found = await _call("GET", f"/containers/{quote(name, safe='')}/json")
    return found if isinstance(found, dict) else {}


async def create(
    container: str, argv: list[str], *, user: str, env: dict[str, str], workdir: str | None, cols: int, rows: int
) -> str:
    """Create a process with a terminal in `container`; its exec id."""
    body: dict[str, Any] = {
        "AttachStdin": True,
        "AttachStdout": True,
        "AttachStderr": True,
        "Tty": True,
        "Cmd": argv,
        "User": user,
        "Env": [f"{key}={value}" for key, value in env.items()],
        "ConsoleSize": [rows, cols],
        "Privileged": False,
    }
    if workdir:
        body["WorkingDir"] = workdir
    found = await _call("POST", f"/containers/{quote(container, safe='')}/exec", body)
    return str(found["Id"])


async def resize(exec_id: str, cols: int, rows: int) -> None:
    await _call("POST", f"/exec/{exec_id}/resize?h={rows}&w={cols}")


async def info(exec_id: str) -> dict[str, Any]:
    found = await _call("GET", f"/exec/{exec_id}/json")
    return found if isinstance(found, dict) else {}


@dataclass
class Stream:
    """The terminal of a started process: what it shows (read) and what is typed into it (write)."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    async def read(self) -> bytes:
        """The next output (b"" when the process ended)."""
        return await self.reader.read(READ_BYTES)

    async def write(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    def close(self) -> None:
        self.writer.close()


async def start(exec_id: str) -> Stream:
    """Start the process and take over the connection as its terminal (raw bytes both ways, since it has a TTY)."""
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path(), limit=READ_BYTES * 2)
    except OSError as error:
        raise DockerError(f"Docker is not reachable: {error}") from error
    payload = json.dumps({"Detach": False, "Tty": True}).encode()
    writer.write(
        f"POST /exec/{exec_id}/start HTTP/1.1\r\nHost: docker\r\nContent-Type: application/json\r\n"
        f"Connection: Upgrade\r\nUpgrade: tcp\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload
    )
    await writer.drain()
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError) as error:
        writer.close()
        raise DockerError("Docker did not start the process.") from error
    status = head.split(b"\r\n", 1)[0].split(b" ")
    if len(status) < 2 or status[1] not in (b"101", b"200"):
        writer.close()
        raise DockerError(f"Docker did not start the process ({head.split(b'\r\n', 1)[0].decode(errors='replace')}).")
    return Stream(reader, writer)


async def run_detached(container: str, argv: list[str], *, user: str, wait_seconds: float = 5) -> int | None:
    """Run a short command in `container` without a terminal and wait (at most `wait_seconds`) until it ends; its
    exit code, or None when it did not end in time."""
    body = {"AttachStdout": False, "AttachStderr": False, "Tty": False, "Cmd": argv, "User": user}
    created = await _call("POST", f"/containers/{quote(container, safe='')}/exec", body)
    exec_id = str(created["Id"])
    await _call("POST", f"/exec/{exec_id}/start", {"Detach": True, "Tty": False})
    loop = asyncio.get_running_loop()
    until = loop.time() + wait_seconds
    while loop.time() < until:
        state = await info(exec_id)
        if not state.get("Running"):
            code = state.get("ExitCode")
            return int(code) if isinstance(code, int) else None
        await asyncio.sleep(0.2)
    return None


async def hang_up(container: str, session_id: str, user: str) -> None:
    """End every process of a session in `container` (see the module's description). Never raises."""
    if not session_id.isalnum():
        return
    with contextlib.suppress(DockerError):  # the container is gone or stopped: so are the session's processes
        await run_detached(container, ["sh", "-c", HANG_UP_SCRIPT, "sudoo-hang-up", session_id], user=user)
