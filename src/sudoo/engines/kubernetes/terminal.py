"""The web shell on the Kubernetes engine: exec with a terminal, over the API's WebSocket (streams.Session).

Exec has no environment, working folder or user of its own: the process starts as the pod's user (Odoo's or
PostgreSQL's, never root: Pod Security forbids it), and its argv is wrapped so it starts in the plan's folder with the
plan's environment (SUDOO_SHELL marks every process of the shell, as with Docker, for hang_up).
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from typing import Any

from ..base import TerminalError, TerminalStream
from . import manifests, streams
from .api import ApiError

_PENDING: dict[str, tuple[str, str, str, list[str], int, int]] = {}
_OPEN: dict[str, streams.Session] = {}
_WRAP = 'cd "$1" 2>/dev/null || cd /; shift; exec env "$@"'


def _target(container: str) -> tuple[str, str]:
    """(instance, service) of a plan's container name, odoo-<instance>-<service>-1 (store.container)."""
    inner = container.removeprefix("odoo-").removesuffix("-1")
    name, _, service = inner.rpartition("-")
    return name, service


async def _running(name: str, service: str) -> dict[str, Any]:
    from .engine import api, running_pod

    try:
        pod = await asyncio.to_thread(lambda: running_pod(api(), name, service))
    except ApiError as error:
        raise TerminalError(f"Kubernetes: {error}", error.status) from None
    if pod is None:
        raise TerminalError("The instance is not running.", 404)
    return pod


class KubernetesTerminal:
    async def check(self, plan: Any) -> None:
        from sudoo.webshell.policy import Refused

        if plan.container_user == "root":
            raise Refused(400, "root-target", "Shells as root are not possible on the Kubernetes engine.")
        pod = await _running(plan.instance, plan.service)
        meta, spec = pod["metadata"], pod.get("spec") or {}
        labels = meta.get("labels") or {}
        ours = (
            meta.get("namespace") == manifests.namespace_of(plan.instance)
            and labels.get("app.kubernetes.io/managed-by") == "sudoo"
            and labels.get("sudoo.sh/instance") == plan.instance
            and labels.get("sudoo.sh/role") == plan.service
        )
        if not ours:
            raise Refused(409, "not-ours", "This pod is not one the panel made for this instance.")
        reaching = (
            spec.get("hostNetwork")
            or spec.get("hostPID")
            or spec.get("hostIPC")
            or any((c.get("securityContext") or {}).get("privileged") for c in spec.get("containers") or [])
        )
        if reaching:
            raise Refused(409, "unsafe", "This pod was changed to reach the server itself; no shell is opened in it.")

    async def create(
        self, plan: Any, argv: list[str], *, env: dict[str, str], workdir: str | None, cols: int, rows: int
    ) -> str:
        pod = await _running(plan.instance, plan.service)
        process_id = secrets.token_hex(8)
        variables = [f"{key}={value}" for key, value in env.items()]
        command = ["sh", "-c", _WRAP, "sudoo-shell", workdir or "/", *variables, *argv]
        _PENDING[process_id] = (
            manifests.namespace_of(plan.instance),
            pod["metadata"]["name"],
            plan.service,
            command,
            cols,
            rows,
        )
        return process_id

    async def start(self, process_id: str) -> TerminalStream:
        from .engine import api

        namespace, pod, container, command, cols, rows = _PENDING.pop(process_id)
        try:
            session, socket = await streams.connect(api(), namespace, pod, container, command, stdin=True, tty=True)
        except ApiError as error:
            raise TerminalError(f"Kubernetes: {error}", error.status) from None
        found = streams.Session(session, socket)
        _OPEN[process_id] = found
        await found.resize(cols, rows)
        return _Stream(process_id, found)

    async def resize(self, process_id: str, cols: int, rows: int) -> None:
        found = _OPEN.get(process_id)
        if found is None:
            raise TerminalError("The shell has ended.")
        await found.resize(cols, rows)

    async def hang_up(self, target: str, session_id: str, user: str) -> None:
        """Closing the socket ends the terminal (its processes get SIGHUP); processes left in the background with
        the session's mark are ended by the same script as with Docker."""
        from sudoo.webshell.docker_exec import HANG_UP_SCRIPT

        from .engine import api, running_pod

        if not session_id.isalnum():
            return
        name, service = _target(target)
        with contextlib.suppress(Exception):
            client = api()
            pod = await asyncio.to_thread(lambda: running_pod(client, name, service))
            if pod is None:
                return
            await asyncio.wait_for(
                streams.run(
                    client, manifests.namespace_of(name), pod["metadata"]["name"], service,
                    ["sh", "-c", HANG_UP_SCRIPT, "sudoo-hang-up", session_id], None, lambda _: None, lambda _: None,
                ),
                10,
            )  # fmt: skip


class _Stream:
    def __init__(self, process_id: str, session: streams.Session) -> None:
        self._id, self._session = process_id, session

    async def read(self) -> bytes:
        return await self._session.read()

    async def write(self, data: bytes) -> None:
        await self._session.write(data)

    def close(self) -> None:
        _OPEN.pop(self._id, None)
        self._session.close()
