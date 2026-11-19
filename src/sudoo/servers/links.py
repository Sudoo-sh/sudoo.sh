"""The panel's side of the agents' channels: one WebSocket per active server, opened by its agent (so the other
servers need no open port), on the asyncio loop the live events and the gateway share (GET /api/v1/servers/channel).

Opening: the agent's signed request (trust.check_connect) for an *active* server (registry.py) is answered with the
panel's signed welcome; a server that waits for approval gets 403 {"status": "joined"}, a revoked one 403
{"status": "revoked"}, and the agent stops or waits accordingly. One channel per server: a new one closes the old.

Messages (each a trust.Seal frame), {"t": ...}:
    panel -> agent   call {id, op, args}, reply {id, ok, result | error}, push {state}, revoked
    agent -> panel   report {...} (every SERVERS_HEARTBEAT_SECONDS), event {topic, key, data, env}, audit {entry},
                     call {id, op, args} (what an agent needs from the panel: a GitHub token), reply {...}

The rest of the panel calls `call(server_id, op, args)` from any thread; it raises Offline when the server's agent is
not connected and RemoteError when the agent answered with an error.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import itertools
import json
import re
import time
from collections.abc import Callable
from typing import Any

from aiohttp import WSMsgType, web

from sudoo import servers
from sudoo.config import settings
from sudoo.core import audit, events

from . import inventory, registry, trust

PATH = "/api/v1/servers/channel"
MAX_MESSAGE_BYTES = 96 * 1024 * 1024  # a piece of a backup being moved (MOVE_CHUNK_MB, base64) fits easily
FORWARDED_TOPICS = frozenset({"instance", "instance.deleted", "job", "job.output", "domains"})


class Offline(Exception):
    """The server's agent is not connected."""


class RemoteError(Exception):
    """The agent answered a call with an error: (HTTP-like status, message)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Link:
    """One connected agent."""

    def __init__(self, server_id: str, ws: web.WebSocketResponse, seal: trust.Seal) -> None:
        self.server_id = server_id
        self.ws = ws
        self.seal = seal
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.ids = itertools.count(1)
        self.send_lock = asyncio.Lock()
        self.connected_at = time.time()
        self.pushed = ""

    async def send(self, body: dict[str, Any]) -> None:
        async with self.send_lock:  # frames are numbered: sealed and sent in the same order
            await self.ws.send_str(self.seal.seal(body))

    async def call(self, op: str, args: dict[str, Any], timeout: float) -> Any:
        call_id = next(self.ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.pending[call_id] = future
        try:
            await self.send({"t": "call", "id": call_id, "op": op, "args": args})
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise RemoteError(504, f"The server {registry.name_of(self.server_id)} did not answer in time.") from None
        finally:
            self.pending.pop(call_id, None)

    def fail_pending(self) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(Offline(self.server_id))


LINKS: dict[str, Link] = {}  # server id -> its connected agent; event loop thread only
NONCES = trust.NonceCache()
PANEL_CALLS: dict[str, Callable[[str, dict[str, Any]], Any]] = {}
"""What agents may ask the panel: op -> fn(server id, args) -> result (run in a thread; raise RemoteError)."""


def connected(server_id: str) -> bool:
    return server_id in LINKS


def online(server_id: str) -> bool:
    """The main server always; another server while its agent's channel is open."""
    return server_id == servers.MAIN or connected(server_id)


# ---------------------------------------------------------------- calls from the rest of the panel


def _loop() -> asyncio.AbstractEventLoop:
    from sudoo.instances import gateway

    return gateway.loop()


def call(server_id: str, op: str, args: dict[str, Any], timeout: float | None = None) -> Any:
    """Ask a server's agent to do `op`; wait for its answer. From any thread but the event loop's."""
    link = LINKS.get(server_id)
    if link is None:
        raise Offline(server_id)
    wait = float(timeout or settings.servers_request_seconds)
    future = asyncio.run_coroutine_threadsafe(link.call(op, args, wait), _loop())
    try:
        return future.result(wait + 5)
    except TimeoutError:
        future.cancel()
        raise RemoteError(504, f"The server {registry.name_of(server_id)} did not answer in time.") from None


def push_all() -> None:
    """Send every connected agent what the panel decided (state()), when it changed. From any thread."""
    for server_id in list(LINKS):
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(_push(server_id), _loop())


def close(server_id: str, reason: str = "revoked") -> None:
    """Close a server's channel (revoked: the agent is told first). From any thread."""
    link = LINKS.get(server_id)
    if link is not None:
        asyncio.run_coroutine_threadsafe(_close(link, reason), _loop())


async def _close(link: Link, reason: str) -> None:
    with contextlib.suppress(Exception):
        if reason == "revoked":
            await link.send({"t": "revoked"})
        await link.ws.close(code=4003, message=reason.encode())


# ---------------------------------------------------------------- what the panel tells agents


NOT_PUSHED = frozenset(
    {
        "brand_color",
        "default_language",
        "licence_required_version",
        "link_deadline_days",
        "require_second_factor",
        "session_idle_minutes",
        "session_max_hours",
        "rate_limit_reads_per_minute",
        "rate_limit_changes_per_minute",
        "max_instances",
        "max_instances_per_user",
        "create_cooldown_minutes",
        "poll_minutes",
        "mail_from",
        "smtp_host",
        "smtp_port",
        "smtp_security",
        "smtp_username",
        "mail_templates_dir",
        "recovery_codes_count",
        "invitation_hours",
        "invitation_password_fallback",
        "invitations_per_hour",
        "sign_in_email",
        "sign_in_email_opt_out",
        "admin_contact_name",
        "admin_contact_phone",
        "admin_contact_email",
        "marketplace",
        "docs_url",
        "support_url",
        "hub_support_url",
        "plan_pricing_url",
        "plan_account_url",
        "enterprise_path",
        "enterprise_git_url",
        "enterprise_git_branch",
        "enterprise_marker",
    }
)
"""Panel settings an agent does not take: the panel's own (sign-in, e-mail, quotas, links) and what belongs to each
server (where its Enterprise add-ons are). Secrets are never sent (config.SECRET), nor off-site, alert, project and
server settings."""


def pushed_settings() -> dict[str, Any]:
    from sudoo.config import ALERT_SETTINGS, EDITABLE, OFFSITE_SETTINGS, PROJECT_SETTINGS, SECRET, SERVER_SETTINGS

    skipped = NOT_PUSHED | set(SECRET) | set(OFFSITE_SETTINGS) | set(ALERT_SETTINGS) | set(PROJECT_SETTINGS)
    skipped |= set(SERVER_SETTINGS)
    return {name: getattr(settings, name) for name in EDITABLE if name not in skipped}


def state(server_id: str) -> dict[str, Any]:
    """What an agent applies on its server: the panel's settings (pushed_settings), the plan, the production
    instances and branches of every server, its locked instances, whether the link deadline limits managing."""
    from sudoo import entitlements, link_deadline
    from sudoo.instances import stages
    from sudoo.projects import access

    found = entitlements.current()
    record = registry.get(server_id) or {}
    return {
        "server": {"id": server_id, "name": record.get("name", ""), "region": record.get("region", "")},
        "settings": {
            k: (v if isinstance(v, int | float | bool | str) else str(v)) for k, v in pushed_settings().items()
        },
        "heartbeat_seconds": settings.servers_heartbeat_seconds,
        "wait_minutes": settings.servers_wait_minutes,
        "entitlements": {"values": found.values, "source": found.source, "linked": found.linked},
        "production_keys": entitlements.production_keys(),
        "production_instances": stages.production_instances(),
        "link_restricted": link_deadline.restricted(),
        "locked": [name for name in inventory.remote_names(server_id) if access.instance_lock(name) is not None],
    }


async def _push(server_id: str) -> None:
    link = LINKS.get(server_id)
    if link is None:
        return
    loop = asyncio.get_running_loop()
    body = await loop.run_in_executor(None, state, server_id)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    if digest != link.pushed:
        link.pushed = digest
        await link.send({"t": "push", "state": body})


# ---------------------------------------------------------------- the channel


def _refusal(status: int, state_name: str, detail: str) -> web.Response:
    return web.json_response({"status": state_name, "detail": detail}, status=status)


async def channel(request: web.Request) -> web.StreamResponse:
    loop = asyncio.get_running_loop()
    server_id = request.headers.get("X-Sudoo-Server", "")
    record = await loop.run_in_executor(None, registry.get, server_id) if trust.SERVER_ID_RE.match(server_id) else None
    if record is None or not record.get("public_key"):
        return _refusal(404, "unknown", "This server is not known to the panel.")
    key = trust.public_from_text(record["public_key"])
    try:
        _, agent_nonce = trust.check_connect(request.headers, key, NONCES)
    except trust.ChannelError as error:
        audit.record("server", request.remote, "server connection refused", ok=False, detail=f"{server_id}: {error}")
        return _refusal(401, "refused", str(error))
    if record["status"] != registry.ACTIVE:  # 409: waiting for approval; 410: revoked (the agent stops trying)
        status = {registry.JOINED: 409, registry.REVOKED: 410}.get(record["status"], 403)
        return _refusal(status, record["status"], "This server is not approved: " + record["status"])
    ws = web.WebSocketResponse(max_msg_size=MAX_MESSAGE_BYTES, heartbeat=30)
    await ws.prepare(request)
    text, session = trust.welcome(
        registry.panel_key(), server_id, agent_nonce, {"server": server_id, "name": record["name"], "at": time.time()}
    )
    await ws.send_str(text)
    link = Link(server_id, ws, trust.Seal(registry.panel_key(), key, session, trust.PANEL))
    old = LINKS.get(server_id)
    LINKS[server_id] = link
    if old is not None:
        await _close(old, "replaced")
    await loop.run_in_executor(None, registry.seen, server_id)
    audit.record("server", request.remote, "server connected", detail=f"{record['name']} ({server_id})")
    events.publish("server", server_id)
    pusher = asyncio.create_task(_keep_pushing(link))
    try:
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                break
            try:
                body = link.seal.open(message.data)
            except trust.ChannelError as error:
                audit.record("server", request.remote, "server frame refused", ok=False, detail=f"{server_id}: {error}")
                break
            await _received(link, body)
    finally:
        pusher.cancel()
        link.fail_pending()
        if LINKS.get(server_id) is link:
            del LINKS[server_id]
            events.publish("server", server_id)
        with contextlib.suppress(Exception):
            await ws.close()
    return ws


async def _keep_pushing(link: Link) -> None:
    while True:
        with contextlib.suppress(Exception):
            await _push(link.server_id)
        await asyncio.sleep(settings.servers_heartbeat_seconds)


async def _received(link: Link, body: dict[str, Any]) -> None:
    kind = body.get("t")
    loop = asyncio.get_running_loop()
    if kind == "reply":
        future = link.pending.get(body.get("id") or 0)
        if future is not None and not future.done():
            if body.get("ok"):
                future.set_result(body.get("result"))
            else:
                error = body.get("error") or {}
                future.set_exception(RemoteError(int(error.get("status") or 500), str(error.get("message") or "")))
    elif kind == "report":
        await loop.run_in_executor(None, _report, link.server_id, body)
    elif kind == "event":
        await loop.run_in_executor(None, _event, link.server_id, body)
    elif kind == "audit":
        await loop.run_in_executor(None, _audit, link.server_id, body.get("entry"))
    elif kind == "call":
        asyncio.create_task(_answer(link, body))


def _report(server_id: str, body: dict[str, Any]) -> None:
    registry.seen(server_id, body.get("facts") if isinstance(body.get("facts"), dict) else None)
    inventory.report(server_id, body)
    events.publish("server", server_id)


def _event(server_id: str, body: dict[str, Any]) -> None:
    topic, key, data = body.get("topic"), str(body.get("key") or ""), body.get("data")
    if topic not in FORWARDED_TOPICS:
        return
    if topic == "instance" and isinstance(data, dict):
        inventory.instance_changed(server_id, key, data, body.get("env"))
        if not inventory.is_remote(key):
            return  # a name the main server or another server has: not this one's
        from .placement import decorate

        data = decorate(data, server_id)
    elif topic == "instance.deleted":
        inventory.instance_deleted(server_id, key)
    elif topic == "job" and isinstance(data, dict):
        inventory.job_changed(server_id, data)
    events.publish(str(topic), key, data)


def _audit(server_id: str, entry: Any) -> None:
    if not isinstance(entry, dict):
        return
    detail = str(entry.get("detail") or "")
    audit.record(
        str(entry.get("user") or "server")[:120],
        str(entry.get("ip") or "")[:64] or None,
        str(entry.get("event") or "")[:200],
        instance=str(entry.get("instance") or "")[:40] or None,
        ok=bool(entry.get("ok", True)),
        detail=(f"{detail} " if detail else "") + f"(on {registry.name_of(server_id)})",
    )


async def _answer(link: Link, body: dict[str, Any]) -> None:
    call_id, op, args = body.get("id"), str(body.get("op") or ""), body.get("args") or {}
    handler = PANEL_CALLS.get(op)
    try:
        if handler is None or not isinstance(args, dict):
            raise RemoteError(404, f"The panel does not know {op[:40]}.")
        result = await asyncio.get_running_loop().run_in_executor(None, handler, link.server_id, args)
        reply: dict[str, Any] = {"t": "reply", "id": call_id, "ok": True, "result": result}
    except RemoteError as error:
        reply = {"t": "reply", "id": call_id, "ok": False, "error": {"status": error.status, "message": error.message}}
    except Exception as error:  # noqa: BLE001 - the agent gets an answer whatever happens
        print(f"servers: call {op} of {link.server_id} failed: {error!r}", flush=True)
        reply = {"t": "reply", "id": call_id, "ok": False, "error": {"status": 500, "message": "failed on the panel"}}
    with contextlib.suppress(Exception):
        await link.send(reply)


def add_routes(app: web.Application) -> None:
    app.router.add_get(PATH, channel)


# ---------------------------------------------------------------- what agents may ask


OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
PENDING_OWNERS: dict[str, dict[str, float]] = {}  # server id -> {owner: until}: creations passed on just now


def owner_of(repository: Any) -> str:
    """The GitHub account of a repository address (https://github.com/<owner>/<repo>), lower case; "" otherwise."""
    from sudoo.repos import github

    name = github.repo_name(str(repository or "")) or ""
    return name.split("/")[0].lower() if "/" in name else ""


def expect_owner(server_id: str, repository: str) -> None:
    """A creation of an instance of `repository` was passed to the server: its agent may ask for a token for it."""
    owner = owner_of(repository)
    if owner:
        PENDING_OWNERS.setdefault(server_id, {})[owner] = time.time() + 6 * 3600


def git_token(server_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """A GitHub App installation token for an account whose repositories the server's instances use."""
    from sudoo.repos import github_app

    owner = str(args.get("owner") or "")
    if not OWNER_RE.match(owner):
        raise RemoteError(400, "not a GitHub account")
    used = {
        owner_of(((entry.get("resource") or {}).get("git") or {}).get("repository"))
        for name in inventory.remote_names(server_id)
        for entry in [inventory.remote(name) or {}]
    }
    pending = {o for o, until in PENDING_OWNERS.get(server_id, {}).items() if until > time.time()}
    if owner.lower() not in used | pending:
        audit.record("server", None, "GitHub token refused", ok=False, detail=f"{registry.name_of(server_id)}: {owner}")
        raise RemoteError(403, "no instance of this server uses that GitHub account")
    try:
        token = github_app.token_for(owner)
    except github_app.GitHubError as error:
        raise RemoteError(502, str(error)) from error
    return {"token": token}


PANEL_CALLS["git_token"] = git_token
