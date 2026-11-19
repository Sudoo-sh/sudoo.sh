"""The agent of a server other than the main one (`sudoo.sh agent`, container sudoo-agent, the panel's image).

It runs on its server what the panel runs on the main one, for the instances of that server: their jobs (Docker
Compose, backups, logs, metrics, resources, custom domains), the automatic work (pausing, waking, daily backups,
certificate renewals, expiry), with its own gateway service and mail catcher next to it. It has no web page, no
accounts and listens on no port: it connects out to the main panel (links.py) and does what the panel asks, for the
person the panel names, with exactly the permissions the panel gives (forward.py).

    sudoo.sh agent join <code>   join the panel with a join code: make this server's key pair, print its fingerprint
    sudoo.sh agent status        joined or not, approved or not, connected or not, the fingerprint
    sudoo.sh agent leave         forget the panel and the key (the instances stay)
    sudoo.sh agent               run (the container's command)

On its own when the panel is away: the channel is opened again and again (waiting longer each time, up to a
minute), while everything above keeps running from the agent's own copy of the panel's last instructions. A job that
needs the panel (a GitHub token for private code) waits for it for SERVERS_WAIT_MINUTES, then fails with a clear
message. The agent keeps its key in <ODOO_ROOT>/agent/agent.key (root only) and what it pinned in agent.json.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import platform
import shutil
import signal
import socket
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from sudoo import servers
from sudoo.config import live, settings
from sudoo.core import vault

from . import trust

FORWARDED_ENV = "sudoo.forwarded_user"
TOPICS = frozenset({"instance", "instance.deleted", "job", "job.output", "domains"})
SOCKET_ENV = "SUDOO_AGENT_SOCKET"
MAX_BACKOFF_SECONDS = 60
_STATE_LOCK = threading.Lock()


class Refused(Exception):
    """The panel refused this server: (state, message)."""

    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state


# ---------------------------------------------------------------- files


def state_dir() -> Path:
    return settings.odoo_root / "agent"


def key_file() -> Path:
    return state_dir() / "agent.key"


def state_file() -> Path:
    return state_dir() / "agent.json"


def read_state() -> dict[str, Any]:
    try:
        found = json.loads(state_file().read_text())
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def write_state(changes: dict[str, Any]) -> dict[str, Any]:
    with _STATE_LOCK:
        found = {**read_state(), **changes}
        state_dir().mkdir(parents=True, exist_ok=True)
        os.chmod(state_dir(), 0o700)
        vault.write_private(state_file(), json.dumps(found, indent=1))
    return found


def own_key() -> Any:
    state_dir().mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir(), 0o700)
    return trust.load_or_create_key(key_file())


# ---------------------------------------------------------------- joining


def host_facts() -> dict[str, Any]:
    """What the agent tells the panel about its server (no secrets)."""
    from importlib.metadata import version

    facts: dict[str, Any] = {
        "hostname": socket.gethostname()[:120],
        "os": f"{platform.system()} {platform.release()}"[:120],
        "architecture": platform.machine()[:40],
        "cpus": os.cpu_count() or 1,
        "version": version("sudoo"),
    }
    with contextlib.suppress(OSError, ValueError):
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
        facts["memory_bytes"] = int(pages * size)
    with contextlib.suppress(OSError):
        usage = shutil.disk_usage(settings.odoo_root)
        facts["disk_bytes"], facts["disk_free_bytes"] = int(usage.total), int(usage.free)
    return facts


def _post(url: str, body: dict[str, Any], timeout: float = 30) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:  # noqa: S310 - the panel's https address
            return answer.status, json.loads(answer.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"{}")
        except ValueError:
            return error.code, {}


def join(code_text: str) -> dict[str, Any]:
    """Join the panel named in the join code; raises ValueError with what went wrong."""
    code = trust.JoinCode.decode(code_text)
    found = read_state()
    if found.get("server_id") and found.get("status") not in ("revoked",):
        raise ValueError(
            f"This server joined a panel already ({found.get('panel_url')}). Run `sudoo.sh agent leave` first."
        )
    key = own_key()
    public = trust.public_text(key)
    body = {
        "server": code.server_id,
        "secret": code.secret,
        "public_key": public,
        "signature": trust.sign(key, trust.join_message(code.server_id, code.secret, public)),
        "facts": host_facts(),
    }
    try:
        status, answer = _post(f"{code.panel_url}/api/v1/servers/join", body)
    except (OSError, ValueError) as error:
        raise ValueError(f"The panel at {code.panel_url} could not be reached: {error}") from error
    if status != 201:
        raise ValueError(str(answer.get("detail") or f"The panel refused the join (HTTP {status})."))
    data = answer.get("data") or {}
    panel_key = str(data.get("panel_key") or "")
    try:
        trust.public_from_text(panel_key)
    except ValueError:
        raise ValueError("The panel's answer has no valid key: nothing was changed.") from None
    if trust.key_sha256(panel_key) != code.panel_key_sha256:
        raise ValueError(
            "The panel's key is not the one in the join code: this may not be your panel. Nothing was changed."
        )
    return write_state(
        {
            "server_id": code.server_id,
            "name": str(data.get("name") or ""),
            "panel_url": code.panel_url,
            "panel_key": panel_key,
            "fingerprint": trust.fingerprint(public),
            "panel_fingerprint": trust.fingerprint(panel_key),
            "status": "joined",
            "joined_at": time.time(),
        }
    )


def status() -> dict[str, Any]:
    found = read_state()
    if not found.get("server_id"):
        return {"joined": False}
    return {
        "joined": True,
        "server_id": found["server_id"],
        "name": found.get("name", ""),
        "panel_url": found.get("panel_url", ""),
        "status": found.get("status", ""),
        "fingerprint": found.get("fingerprint", ""),
        "panel_fingerprint": found.get("panel_fingerprint", ""),
        "connected_at": found.get("connected_at"),
        "last_contact_at": found.get("last_contact_at"),
    }


def leave() -> bool:
    """Forget the panel and this server's key. The instances on this server stay as they are."""
    had = bool(read_state())
    for path in (state_file(), key_file()):
        path.unlink(missing_ok=True)
    return had


# ---------------------------------------------------------------- what the panel decided


def apply(state: dict[str, Any]) -> None:
    """Take what the panel pushed: its settings (in memory, on top of this server's .env) and its decisions."""
    servers.PANEL.clear()
    servers.PANEL.update({k: v for k, v in state.items() if k != "settings"})
    values = state.get("settings")
    if isinstance(values, dict):
        before = {name: getattr(settings, name) for name in values}
        try:
            live.use(values)
        except ValueError as error:
            print(f"agent: the panel's settings were not taken: {error}", flush=True)
            return
        changed = {k: getattr(settings, k) for k in values if before.get(k) != getattr(settings, k)}
        if changed:
            live.notify(changed)


# ---------------------------------------------------------------- answering the panel


def _forwarded(app: Any, args: dict[str, Any]) -> dict[str, Any]:
    """An API request the panel passes on, run through this server's copy of the API for the person it names."""
    user = args.get("user")
    path = str(args.get("path") or "")
    if not isinstance(user, dict) or not path.startswith("/api/v1/") or not isinstance(user.get("permissions"), list):
        return {"status": 400, "mimetype": "application/json", "headers": {}, "body": ""}
    identity = {
        "name": str(user.get("name") or "panel")[:200],
        "role": str(user.get("role") or "viewer"),
        "permissions": [str(p) for p in user["permissions"]],
        "via": "panel",
    }
    body = str(args.get("body") or "")
    client = app.test_client()
    answer = client.open(
        path,
        method=str(args.get("method") or "GET"),
        query_string=str(args.get("query") or ""),
        data=body.encode() if body else None,
        content_type="application/json" if body else None,
        environ_base={FORWARDED_ENV: identity, "REMOTE_ADDR": str(args.get("ip") or "127.0.0.1")},
    )
    headers = {k: v for k, v in answer.headers.items() if k in ("Location", "Content-Location", "Content-Disposition",
                                                               "Retry-After", "X-Sudoo-Frame")}  # fmt: skip
    return {
        "status": answer.status_code,
        "mimetype": answer.mimetype,
        "headers": headers,
        "body": base64.b64encode(answer.get_data()).decode(),
    }


def _create(args: dict[str, Any]) -> dict[str, Any]:
    """A creation the panel planned (instances/planning.build, but the ports): its job, as the API shows it."""
    from sudoo import exposure
    from sudoo.instances import planning, store
    from sudoo.web.v1.resources import job_resource

    plan = args.get("plan")
    if not isinstance(plan, dict) or not store.NAME_RE.match(str(plan.get("name") or "")):
        raise LookupError("not a plan")
    if store.folder(plan["name"]).exists():
        raise FileExistsError(plan["name"])
    plan = {**plan, "ports": store.reserve_ports(exposure.provider().instance_ports())}
    job = planning.start(plan, str(args.get("user") or "system"))
    return job_resource(job)


def _area(args: dict[str, Any]) -> Path:
    from sudoo.instances import backups

    if args.get("area") != "backups":
        raise PermissionError("only backups are read or written")
    name = str(args.get("name") or "")
    part = name.endswith(".part")
    if not backups.FILE_RE.match(name[:-5] if part else name):
        raise PermissionError("not a backup name")
    return settings.backups_dir / name


def _file_stat(args: dict[str, Any]) -> dict[str, Any]:
    path = _area(args)
    if not path.is_file():
        raise FileNotFoundError(path.name)
    return {"size": path.stat().st_size}


def _file_read(args: dict[str, Any]) -> dict[str, Any]:
    path = _area(args)
    offset, length = int(args.get("offset") or 0), min(int(args.get("length") or 0), 64 * 1024 * 1024)
    with path.open("rb") as fh:
        fh.seek(max(0, offset))
        return {"data": base64.b64encode(fh.read(max(0, length))).decode()}


def _file_write(args: dict[str, Any]) -> dict[str, Any]:
    """Pieces of a backup copied here from another server: written to <name>.part, renamed when complete."""
    path = _area({**args, "name": str(args.get("name") or "") + ".part"})
    offset = int(args.get("offset") or 0)
    data = base64.b64decode(str(args.get("data") or ""))
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    mode = "r+b" if path.exists() and offset else "wb"
    with path.open(mode) as fh:
        fh.seek(offset)
        fh.write(data)
    if args.get("last"):
        path.replace(path.with_name(path.name[:-5]))
    return {"written": len(data)}


def _file_remove(args: dict[str, Any]) -> dict[str, Any]:
    path = _area(args)
    path.unlink(missing_ok=True)
    path.with_name(path.name + ".part").unlink(missing_ok=True)
    return {"removed": True}


def answer_call(app: Any, op: str, args: dict[str, Any]) -> Any:
    """What the agent does for the panel; raises to answer with an error."""
    if op == "http":
        return _forwarded(app, args)
    if op == "create":
        return _create(args)
    if op == "file.stat":
        return _file_stat(args)
    if op == "file.read":
        return _file_read(args)
    if op == "file.write":
        return _file_write(args)
    if op == "file.remove":
        return _file_remove(args)
    if op == "ping":
        return {"at": time.time()}
    raise LookupError(op)


def _error_of(error: Exception) -> dict[str, Any]:
    if isinstance(error, FileNotFoundError):
        return {"status": 404, "message": "This file does not exist on the server."}
    if isinstance(error, FileExistsError):
        return {"status": 409, "message": f"An instance called {error} exists on the server already."}
    if isinstance(error, PermissionError):
        return {"status": 403, "message": str(error)}
    if isinstance(error, LookupError):
        return {"status": 404, "message": f"The server does not know this request ({error})."}
    return {"status": 500, "message": "The request failed on the server."}


# ---------------------------------------------------------------- reporting


def instance_facts(name: str) -> dict[str, str]:
    """The .env facts the panel keeps (inventory.ENV_KEYS); while it is being created, what its plan says."""
    from sudoo.core import jobs
    from sudoo.instances import store

    from .inventory import ENV_KEYS

    if not store.exists(name):
        job = jobs.creating(name)
        plan = job.details if job else {}
        pairs = (("STAGE", "stage"), ("PROJECT", "project"), ("GIT_BRANCH", "branch"), ("ODOO_VERSION", "version"),
                 ("EDITION", "edition"), ("CREATED_BY", "created_by"))  # fmt: skip
        return {key: str(plan[field]) for key, field in pairs if plan.get(field)}
    found = store.read_env(name)
    return {key: found[key] for key in ENV_KEYS if key in found}


def report() -> dict[str, Any]:
    """Everything the panel shows about this server: its instances, recent jobs, figures and room."""
    from sudoo.core import jobs
    from sudoo.instances import capacity, metrics, store
    from sudoo.web.v1.resources import instance_resource, job_resource

    entries = []
    for name in sorted(set(store.names()) | set(jobs.being_created())):
        try:
            entries.append({"name": name, "resource": instance_resource(name), "env": instance_facts(name)})
        except Exception as error:  # noqa: BLE001 - one broken instance must not hide the others
            print(f"agent: {name} not reported: {error!r}", flush=True)
    snapshot = metrics.snapshot()
    try:
        room = capacity.current() or {}
    except Exception:  # noqa: BLE001 - figures may not be measured yet
        room = {}
    return {
        "t": "report",
        "facts": host_facts(),
        "instances": entries,
        "jobs": [job_resource(job) for job in list(jobs.JOBS.values())[-50:] if not jobs.is_panel_job(job.instance)],
        "metrics": {"at": snapshot.get("at"), "server": snapshot.get("server") or {}},
        "capacity": room,
    }


# ---------------------------------------------------------------- the channel


class Channel:
    """The agent's connection to the panel; one at a time, opened again whenever it ends."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.ws: Any = None
        self.seal: trust.Seal | None = None
        self.send_lock: asyncio.Lock | None = None
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.next_id = 0
        self.up = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None

    async def send(self, body: dict[str, Any]) -> None:
        if self.ws is None or self.seal is None or self.send_lock is None:
            raise ConnectionError("not connected")
        async with self.send_lock:
            await self.ws.send_str(self.seal.seal(body))

    def send_soon(self, body: dict[str, Any]) -> None:
        """From any thread: send when connected; dropped otherwise (the next report brings everything)."""
        if self.loop is not None and self.up.is_set():
            asyncio.run_coroutine_threadsafe(self._send_quietly(body), self.loop)

    async def _send_quietly(self, body: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await self.send(body)

    async def call(self, op: str, args: dict[str, Any], timeout: float = 60) -> Any:
        self.next_id += 1
        call_id = self.next_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.pending[call_id] = future
        try:
            await self.send({"t": "call", "id": call_id, "op": op, "args": args})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(call_id, None)

    def call_waiting(self, op: str, args: dict[str, Any]) -> Any:
        """From any thread: ask the panel, waiting for the channel to be up for SERVERS_WAIT_MINUTES (the panel's
        value). Raises TimeoutError when it does not come back in time."""
        minutes = float(servers.PANEL.get("wait_minutes") or settings.servers_wait_minutes)
        deadline = time.monotonic() + minutes * 60
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or not self.up.wait(min(left, 30)):
                if time.monotonic() >= deadline:
                    raise TimeoutError("the main panel could not be reached")
                continue
            assert self.loop is not None
            try:
                return asyncio.run_coroutine_threadsafe(self.call(op, args), self.loop).result(70)
            except (ConnectionError, TimeoutError, asyncio.CancelledError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("the main panel could not be reached") from None

    async def run_forever(self) -> None:
        self.loop = asyncio.get_running_loop()
        backoff = 1.0
        while True:
            found = read_state()
            if not found.get("server_id"):
                await asyncio.sleep(30)
                continue
            if found.get("status") == "revoked":
                await asyncio.sleep(300)  # stays revoked until `agent leave` and a new join
                continue
            try:
                await self.session(found)
                backoff = 1.0
            except Refused as refused:
                if refused.state == "revoked":
                    write_state({"status": "revoked"})
                    print("agent: the panel revoked this server; run `sudoo-install agent leave`.", flush=True)
                elif refused.state == "joined":
                    write_state({"status": "joined"})
                    backoff = 15.0  # waiting for an admin's approval in the panel
                else:
                    print(f"agent: the panel refused the connection: {refused}", flush=True)
            except Exception as error:  # noqa: BLE001 - the panel is away: try again a little later
                print(f"agent: no connection to the panel ({error.__class__.__name__}: {error})", flush=True)
            finally:
                self.up.clear()
                self.ws = None
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("the channel closed"))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    async def session(self, found: dict[str, Any]) -> None:
        import aiohttp

        from . import links

        key = own_key()
        panel_key = trust.public_from_text(found["panel_key"])
        headers, nonce = trust.connect_headers(key, found["server_id"])
        url = found["panel_url"].replace("https://", "wss://", 1).replace("http://", "ws://", 1) + links.PATH
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            try:
                ws = await client.ws_connect(url, headers=headers, max_msg_size=links.MAX_MESSAGE_BYTES, heartbeat=30)
            except aiohttp.WSServerHandshakeError as error:
                states = {409: "joined", 410: "revoked", 404: "unknown", 401: "refused"}
                raise Refused(states.get(error.status, "refused"), f"HTTP {error.status}") from error
            async with ws:
                first = await ws.receive(timeout=30)
                if first.type != aiohttp.WSMsgType.TEXT:
                    raise ConnectionError("the panel closed the channel before its welcome")
                _, session = trust.check_welcome(first.data, panel_key, found["server_id"], nonce)
                self.ws, self.send_lock = ws, asyncio.Lock()
                self.seal = trust.Seal(key, panel_key, session, trust.AGENT)
                write_state({"status": "active", "connected_at": time.time()})
                self.up.set()
                print(f"agent: connected to {found['panel_url']}", flush=True)
                tasks = [asyncio.create_task(self.keep_reporting()), asyncio.create_task(self.pass_events())]
                try:
                    async for message in ws:
                        if message.type != aiohttp.WSMsgType.TEXT:
                            break
                        await self.received(self.seal.open(message.data))
                finally:
                    for task in tasks:
                        task.cancel()

    async def received(self, body: dict[str, Any]) -> None:
        kind = body.get("t")
        loop = asyncio.get_running_loop()
        if kind == "call":
            asyncio.create_task(self.answer(body))
        elif kind == "reply":
            future = self.pending.get(body.get("id") or 0)
            if future is not None and not future.done():
                if body.get("ok"):
                    future.set_result(body.get("result"))
                else:
                    future.set_exception(RuntimeError(str((body.get("error") or {}).get("message") or "refused")))
        elif kind == "push" and isinstance(body.get("state"), dict):
            await loop.run_in_executor(None, apply, body["state"])
            write_state({"last_contact_at": time.time()})
        elif kind == "revoked":
            write_state({"status": "revoked"})
            raise Refused("revoked", "revoked by the panel")

    async def answer(self, body: dict[str, Any]) -> None:
        call_id, op, args = body.get("id"), str(body.get("op") or ""), body.get("args") or {}
        try:
            result = await asyncio.get_running_loop().run_in_executor(None, answer_call, self.app, op, args)
            reply: dict[str, Any] = {"t": "reply", "id": call_id, "ok": True, "result": result}
        except Exception as error:  # noqa: BLE001 - the panel always gets an answer
            if not isinstance(error, FileNotFoundError | FileExistsError | PermissionError | LookupError):
                print(f"agent: {op} failed: {error!r}", flush=True)
            reply = {"t": "reply", "id": call_id, "ok": False, "error": _error_of(error)}
        with contextlib.suppress(Exception):
            await self.send(reply)

    async def keep_reporting(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            with contextlib.suppress(Exception):
                await self.send(await loop.run_in_executor(None, report))
                await loop.run_in_executor(None, write_state, {"last_contact_at": time.time()})
            await asyncio.sleep(float(servers.PANEL.get("heartbeat_seconds") or settings.servers_heartbeat_seconds))

    async def pass_events(self) -> None:
        """Send the panel this server's live events (instances, jobs, logs, domains) as they happen."""
        from sudoo.core.events import BUS
        from sudoo.instances import store

        loop = asyncio.get_running_loop()
        subscription, _ = BUS.subscribe(TOPICS)
        try:
            while True:
                event = await subscription.queue.get()
                if event is None:
                    subscription, _ = BUS.subscribe(TOPICS)
                    continue
                data = event.data if event.data is not None else await loop.run_in_executor(None, BUS.data_of, event)
                message: dict[str, Any] = {"t": "event", "topic": event.topic, "key": event.key, "data": data}
                if event.topic == "instance" and store.NAME_RE.match(event.key):
                    message["env"] = await loop.run_in_executor(None, instance_facts, event.key)
                await self.send(message)
        finally:
            BUS.unsubscribe(subscription)


CHANNEL: list[Channel] = []


# ---------------------------------------------------------------- GitHub tokens for git


def git_token(owner: str) -> str:
    """A GitHub App token for `owner`'s repositories, from the main panel (waiting for it when it is away)."""
    if not CHANNEL:
        return ""
    result = CHANNEL[0].call_waiting("git_token", {"owner": owner})
    return str(result.get("token") or "") if isinstance(result, dict) else ""


class _TokenHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        owner = self.rfile.readline(200).decode(errors="replace").strip()
        try:
            token = git_token(owner) if owner else ""
        except Exception as error:  # noqa: BLE001 - git gets no token and says it cannot read the repository
            print(f"agent: no GitHub token for {owner}: {error}", flush=True)
            token = ""
        self.wfile.write(token.encode() + b"\n")


def _serve_tokens() -> None:
    """A socket only root on this server can open, where git's credential helper asks for tokens."""
    path = settings.memory_dir / f"sudoo-agent-{os.getpid()}.sock"
    path.unlink(missing_ok=True)
    server = socketserver.ThreadingUnixStreamServer(str(path), _TokenHandler)
    os.chmod(path, 0o600)
    os.environ[SOCKET_ENV] = str(path)
    threading.Thread(target=server.serve_forever, daemon=True, name="agent-tokens").start()


def token_from_agent(owner: str) -> str:
    """Used by git's credential helper in an agent: ask the agent process for a token."""
    path = os.environ.get(SOCKET_ENV, "")
    if not path:
        return ""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(float(settings.servers_wait_minutes) * 60 + 60)
        client.connect(path)
        client.sendall(owner.encode() + b"\n")
        return client.makefile("rb").readline().decode().strip()


# ---------------------------------------------------------------- running


def _send_audit(entry: dict[str, Any]) -> None:
    if CHANNEL:
        CHANNEL[0].send_soon({"t": "audit", "entry": entry})


def run() -> None:
    """The agent's service: this server's work, and the channel to the panel."""
    servers.become_agent()
    found = read_state()
    if not found.get("server_id"):
        print("agent: this server has not joined a panel yet: sudoo.sh agent join <code>", flush=True)
    from sudoo.core import audit
    from sudoo.web import create_app

    app = create_app()
    channel = Channel(app)
    CHANNEL.append(channel)
    audit.LISTENERS.append(_send_audit)
    _serve_tokens()
    from sudoo.instances import gateway

    gateway.submit(channel.run_forever())
    print(f"agent: running for {found.get('panel_url') or 'no panel yet'}", flush=True)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    sys.exit(0)
