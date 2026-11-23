"""The gateway: receives every visit to an instance and forwards it to Odoo.

Each instance has its own gateway port (GATEWAY_PORT), which the exposure plugin makes reachable: on 127.0.0.1 for
a tunnel connector or a proxy on this server, or on every interface when visitors connect to the server directly
(EXPOSURE=direct: over https, with the panel's own certificate). Names (managed names, custom domains and, with
EXPOSURE=direct, the panel's own address) are answered on ports 80 and 443, each with its certificate.
The gateway forwards HTTP and WebSocket traffic to Odoo (127.0.0.1:ODOO_PORT) and, because it sees every
request, it can:
    - record the last visit (used for automatic pausing)
    - start a paused instance on the first request and show a "Starting up" page meanwhile
    - show "Update in progress" / "Switched off" pages, with the job's progress (status_pages.py)
    - apply a production instance's access rules (access_gate.py)
    - keep the panel's cookies away from Odoo
    - count requests, errors and response times for the metrics (metrics.py)

Where it runs is the GATEWAY_MODE setting:
    service   (the default) its own process, the sudoo-gateway container (gateway_service.py), which keeps the
              instances answering while the panel restarts or is down; the panel hands it the instances and asks
              it to start paused ones (gateway_link.py)
    embedded  inside the panel process, on the event loop below

Either way, this module is the gateway itself. What it needs from the process it runs in (where an instance's Odoo
is, what to do with a visit, how to wake a paused instance) is a Host: PanelHost in the panel, gateway_service's
ServiceHost in the gateway service. The panel always runs this module's event loop in a background thread: the live
event stream uses it (web/v1/events.py), and in service mode the panel serves the instances itself on it while the
gateway service is not running. Listeners set SO_REUSEPORT, so the panel and the gateway service can hold the same
port for a moment while one hands it over to the other.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import contextlib
import ssl
import threading
import time
import weakref
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import aiohttp
from aiohttp import web
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from sudoo import exposure
from sudoo.config import settings
from sudoo.core import jobs
from sudoo.core.state import STATE
from sudoo.instances import access_gate, metrics, status_pages, store
from sudoo.instances.monitor import STATUS

_LOOP = asyncio.new_event_loop()
_SESSION: aiohttp.ClientSession | None = None
_RUNNERS: dict[str, web.AppRunner] = {}
_LISTENERS: dict[str, asyncio.Server] = {}
_PORTS: dict[str, tuple[str, int]] = {}  # instance name -> (address, port) it listens on
_TLS: dict[str, bool] = {}  # instance name -> whether its gateway port speaks https (EXPOSURE=direct)
LISTENER_ERRORS: dict[str, str] = {}
_WAKE_REQUESTED: dict[str, float] = {}
_wake: Callable[[str], jobs.Job] | None = None  # callable(name) -> Job; set by start()

# hop-by-hop headers (RFC 9110, 7.6.1) and those the gateway sets itself; any header named in Connection is one too
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "expect",
        "host",
    }
)
_SET_BY_GATEWAY = frozenset({"cookie", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-sudoo-client-ip"})
_NOT_A_VISIT = ("/websocket", "/longpolling", "/web/health", "/bus/")  # background traffic of an open tab
_PANEL_COOKIES = {"sudoo_session", access_gate.PASS_COOKIE}  # never sent to Odoo, nor set by it
_UPSTREAM_DOWN = (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, aiohttp.ClientOSError)
_CLIENT_GONE = (ConnectionResetError, aiohttp.ClientConnectionResetError, aiohttp.ClientPayloadError)
INSTANCE = web.AppKey("instance", str)  # the instance a gateway application serves
_READ_BUFFER = 2**20  # bytes read from a socket at a time: large uploads and downloads in fewer, larger steps


class _OdooDown(Exception):
    """Odoo did not accept the connection (paused, starting or stopped)."""


class _BadBody(Exception):
    """The visitor's request body is larger than GATEWAY_MAX_UPLOAD_MB, or stopped arriving."""

    def __init__(self, status: int, text: str) -> None:
        super().__init__(text)
        self.status = status


# --------------------------------------------------------------------------- where the gateway runs


@dataclass(frozen=True, slots=True)
class Route:
    """Where an instance's Odoo is, and whether visitors may reach it."""

    odoo_port: int
    switched_off: bool = False
    websocket_port: int | None = None  # Odoo's gevent server, when it runs with workers (resources.py)


class Edge(Protocol):
    """What the gateway needs to know about the exposure in front of it (an exposure plugin is one)."""

    @property
    def https(self) -> bool: ...

    @property
    def client_ip_header(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class DirectEdge:
    """Custom domains (ports 80 and 443): visitors connect straight to the gateway, over https on 443; no header
    can be trusted for their address."""

    https: bool = True
    client_ip_header: str | None = None


@dataclass(frozen=True, slots=True)
class DomainHost:
    """A custom domain the gateway answers on ports 80/443 (CUSTOM_DOMAINS=acme), and whether it has a certificate.
    `panel`: the panel's own name (EXPOSURE=direct), forwarded to the web service; `instance` is then empty."""

    instance: str
    certificate: bool = False
    panel: bool = False


@dataclass(frozen=True)
class DomainRoutes:
    """Every custom domain, and the ports they are answered on (all of the server's addresses)."""

    http_port: int = 80
    https_port: int = 443
    hosts: Mapping[str, DomainHost] = field(default_factory=dict)
    panel_port: int | None = None
    """EXPOSURE=direct: the web service's port; the panel's name and the server's own addresses are forwarded there."""
    server_names: frozenset[str] = frozenset()
    """EXPOSURE=direct: the server's own addresses (and PUBLIC_HOST), answered with the panel's own certificate."""

    def wanted(self) -> dict[str, tuple[str, int]]:
        """The two listeners, when there is any domain or the panel is answered here ("" listens on every address,
        IPv4 and IPv6)."""
        if not self.hosts and self.panel_port is None:
            return {}
        return {HTTP_LISTENER: ("", self.http_port), HTTPS_LISTENER: ("", self.https_port)}


HTTP_LISTENER, HTTPS_LISTENER = "*http", "*https"  # listener names next to the instances' (never an instance name)
SERVER_CERTIFICATE = "*server"  # the panel's own certificate, in _CONTEXT_DOMAIN (never a domain)
PANEL_CLIENT_IP_HEADER = "X-Sudoo-Client-IP"  # the visitor's address, for the panel (exposure/direct.py)
CHALLENGE_PATH = "/.well-known/acme-challenge/"
DOMAINS = DomainRoutes()  # the custom domains served now (set by reconcile)


class Host(Protocol):
    """What the gateway needs from the process it runs in."""

    def route(self, name: str) -> Route | None:
        """The instance's route, or None when it does not exist (any more)."""
        ...

    def edge(self) -> Edge: ...

    def visited(self, name: str) -> None:
        """Someone opened the instance (not the background traffic of an open tab)."""
        ...

    def answered(self, name: str, status: int, duration_ms: float) -> None:
        """Odoo answered a request (for the metrics)."""
        ...

    async def not_ready(self, request: web.Request, name: str) -> web.StreamResponse:
        """Odoo did not accept the connection: the answer for the visitor (and start the instance if needed)."""
        ...

    async def situation(self, name: str) -> status_pages.Situation:
        """Odoo did not accept the connection: why, with the public progress of the job under way (and start the
        instance if needed)."""
        ...

    async def connect(self, name: str, token: str, ip: str) -> tuple[str | None, str]:
        """A Connect as link was opened (connect_as.py): (Odoo session id, "") when its token was valid and the
        session was written, else (None, why)."""
        ...


def websocket_port(env: Mapping[str, str]) -> int | None:
    """Where the instance's WebSockets go when its containers run with Odoo workers (ODOO_WORKERS, written by the
    panel when it starts them): Odoo's gevent server (ODOO_GEVENT_PORT); None: to the Odoo port, threaded mode."""
    if env.get("ODOO_WORKERS", "0") in ("", "0") or not env.get("ODOO_GEVENT_PORT", "").isdigit():
        return None
    return int(env["ODOO_GEVENT_PORT"])


class PanelHost:
    """The gateway inside the panel process: instances from their folders, state, jobs and metrics in memory."""

    def route(self, name: str) -> Route | None:
        if not store.exists(name):
            return None
        env = store.read_env(name)
        return Route(int(env["ODOO_PORT"]), bool(STATE.get(name).get("switched_off")), websocket_port(env))

    def edge(self) -> Edge:
        return exposure.provider()

    def visited(self, name: str) -> None:
        STATE.record_visit(name)

    def answered(self, name: str, status: int, duration_ms: float) -> None:
        metrics.record_request(name, status, duration_ms)

    async def not_ready(self, request: web.Request, name: str) -> web.StreamResponse:
        found = await self.situation(name)
        return status_pages.render(request, name, found.page, since=found.since, progress=found.progress)

    async def situation(self, name: str) -> status_pages.Situation:
        page, since = wake_decision(name)
        return status_pages.Situation(page, since, public_progress(name))

    async def connect(self, name: str, token: str, ip: str) -> tuple[str | None, str]:
        from sudoo.instances import connect_as

        try:
            return await asyncio.to_thread(connect_as.connect, name, token, ip), ""
        except connect_as.ConnectError as error:
            return None, str(error)


HOST: Host = PanelHost()


def use(host: Host) -> None:
    """Serve the instances with another host (the gateway service does, gateway_service.py)."""
    global HOST
    HOST = host


# --------------------------------------------------------------------------- lifecycle (in the panel)


def start(wake: Callable[[str], jobs.Job]) -> None:
    """Start the event loop thread and the gateway as GATEWAY_MODE says: a listener for every instance here
    (embedded), or the link to the gateway service (service). `wake(name)` starts a wake-up job."""
    from sudoo.instances import gateway_link

    global _wake
    _wake = wake
    threading.Thread(target=_run_loop, daemon=True, name="gateway").start()
    gateway_link.start()
    if settings.gateway_mode == "embedded":
        for name in store.names():
            add(name)
        refresh_domains()


def _run_loop() -> None:
    asyncio.set_event_loop(_LOOP)
    _LOOP.run_until_complete(_create_session())
    _LOOP.run_forever()


async def _create_session() -> None:
    global _SESSION
    _SESSION = aiohttp.ClientSession(
        auto_decompress=False,
        cookie_jar=aiohttp.DummyCookieJar(),
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=5),
        # connections to Odoo are kept open and reused (keep-alive), however many visits run at once
        connector=aiohttp.TCPConnector(limit=0),
        read_bufsize=_READ_BUFFER,
        skip_auto_headers=("User-Agent", "Accept", "Accept-Encoding"),  # only the visitor's own are passed on
    )


def close_session() -> None:
    """When the backend stops: close the connections to Odoo on the gateway's loop (else aiohttp reports an unclosed
    client session at exit)."""
    session = _SESSION
    if session is None or session.closed or not _LOOP.is_running():
        return
    with contextlib.suppress(Exception):
        run(session.close(), timeout=5)


atexit.register(close_session)


def loop() -> asyncio.AbstractEventLoop:
    """The event loop the gateway, the live event stream and the servers' channels share."""
    return _LOOP


def submit[T](coroutine: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
    """Run a coroutine on the gateway's event loop (shared with the live event stream, web/v1/events.py)."""
    return asyncio.run_coroutine_threadsafe(coroutine, _LOOP)


def run[T](coroutine: Coroutine[Any, Any, T], timeout: float = 15) -> T:
    """Run a coroutine on the gateway's event loop and wait for its result (from another thread)."""
    return submit(coroutine).result(timeout)


def add(name: str) -> None:
    """Serve the instance on its gateway port (called from other threads): here (embedded), or by the gateway
    service, which is asked to reconcile its listeners at once (service)."""
    if settings.gateway_mode == "service":
        from sudoo.instances import gateway_link

        gateway_link.add(name)
        return
    provider = exposure.provider()
    tls = bool(getattr(provider, "gateway_tls", False))
    run(_listen(name, provider.listen_host, int(store.read_env(name)["GATEWAY_PORT"]), tls))


def remove(name: str) -> None:
    """Stop serving the instance (it is being deleted)."""
    if settings.gateway_mode == "service":
        from sudoo.instances import gateway_link

        gateway_link.remove(name)
    run(_unlisten(name))  # in service mode too: the panel may be serving it while the gateway service is down


# --------------------------------------------------------------------------- listeners (on the event loop)


def listening() -> dict[str, int]:
    """The instances served here, with their gateway port."""
    return {name: port for name, (_, port) in _PORTS.items()}


async def reconcile(wanted: Mapping[str, tuple[str, int]], domains: DomainRoutes | None = None) -> None:
    """Listen exactly on `wanted` ({name: (address, port)}): stop the listeners not wanted (or on another address
    or port), start the missing ones. A port another program holds is recorded in LISTENER_ERRORS and tried again
    at the next call. `domains` (when given) become the custom domains served, and their listeners (HTTP_LISTENER,
    HTTPS_LISTENER) are added to `wanted`."""
    global DOMAINS
    if domains is not None:
        DOMAINS = domains
        wanted = {**wanted, **domains.wanted()}
    tls = bool(getattr(HOST.edge(), "gateway_tls", False))  # the instances' ports: https with EXPOSURE=direct
    for name in list(_PORTS):
        if _PORTS[name] != wanted.get(name) or (not name.startswith("*") and _TLS.get(name, False) != tls):
            await _unlisten(name)
    for name in set(LISTENER_ERRORS) - set(wanted):
        LISTENER_ERRORS.pop(name, None)
    for name, (host, port) in wanted.items():
        if name not in _PORTS:
            await _listen(name, host, port, tls and not name.startswith("*"))


async def _listen(name: str, host: str, port: int, tls: bool = False) -> None:
    if name in _RUNNERS:
        return
    try:
        runner, listener = await _serve(name, host, port, tls)
    except OSError as exc:
        LISTENER_ERRORS[name] = f"Gateway port {port} is used by another program ({exc.strerror})."
        return
    LISTENER_ERRORS.pop(name, None)
    _RUNNERS[name] = runner
    _LISTENERS[name] = listener
    _PORTS[name] = (host, port)
    _TLS[name] = tls


async def _serve(name: str, host: str, port: int, tls: bool = False) -> tuple[web.AppRunner, asyncio.Server]:
    """The gateway application of instance `name`, listening on host:port.

    A connection has GATEWAY_HEADER_TIMEOUT_SECONDS to send a request's line and headers (aiohttp's keep-alive
    timeout, which runs from the moment a connection opens and again after every answer, and is not counted while
    a request is being handled: long polling, WebSockets and slow uploads are not cut short). Connections are
    counted (_Counted): beyond GATEWAY_MAX_CONNECTIONS for one instance or GATEWAY_MAX_CONNECTIONS_TOTAL for all,
    a new one is answered 503 and closed, so slow or idle visitors cannot use up the server's open files.

    The port is opened with SO_REUSEPORT: the panel and the gateway service (same user) can both hold it while one
    hands it over to the other, so visitors are not turned away in between."""
    app = web.Application()  # bodies are never read whole: they are passed on as they arrive (_body)
    app[INSTANCE] = name
    handler = {HTTP_LISTENER: _handle_domain_http, HTTPS_LISTENER: _handle_domain_https}.get(name, _handle)
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(
        app,
        access_log=None,
        handle_signals=False,
        read_bufsize=_READ_BUFFER,
        keepalive_timeout=settings.gateway_header_timeout_seconds,
    )
    await runner.setup()
    server = runner.server
    assert server is not None  # set by setup()
    try:
        listener = await asyncio.get_running_loop().create_server(
            lambda: _Counted(name, server()),
            host or None,
            port,
            reuse_address=True,
            reuse_port=True,
            ssl=_tls_server() if name == HTTPS_LISTENER else _server_tls() if tls else None,
        )
    except OSError:
        await runner.cleanup()
        raise
    return runner, listener


async def _unlisten(name: str) -> None:
    _PORTS.pop(name, None)
    _TLS.pop(name, None)
    listener = _LISTENERS.pop(name, None)
    if listener:
        listener.close()  # no new connections; the runner closes the open ones
    runner = _RUNNERS.pop(name, None)
    if runner:
        await runner.cleanup()


_OPEN: dict[str, int] = {}  # instance name -> visitors' connections open to its gateway
_BUSY = b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 5\r\nConnection: close\r\n\r\n"


class _Counted(asyncio.Protocol):
    """A visitor's connection to a gateway, counted against GATEWAY_MAX_CONNECTIONS (per instance) and
    GATEWAY_MAX_CONNECTIONS_TOTAL. One over either limit is answered 503 and closed at once; the others are handed
    to aiohttp's handler unchanged. Runs on the gateway's event loop only, so the counts need no lock."""

    def __init__(self, name: str, handler: asyncio.Protocol) -> None:
        self.name = name
        self.handler = handler
        self.counted = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        own_limit = not self.name.startswith("*")  # the custom domains' listeners count only towards the total
        if (
            own_limit
            and _OPEN.get(self.name, 0) >= settings.gateway_max_connections
            or sum(_OPEN.values()) >= settings.gateway_max_connections_total
        ):
            if isinstance(transport, asyncio.WriteTransport):
                transport.write(_BUSY)
            transport.close()
            return
        self.counted = True
        _OPEN[self.name] = _OPEN.get(self.name, 0) + 1
        self.handler.connection_made(transport)

    def connection_lost(self, exc: Exception | None) -> None:
        if self.counted:
            self.counted = False
            _OPEN[self.name] -= 1
            self.handler.connection_lost(exc)

    def data_received(self, data: bytes) -> None:
        if self.counted:
            self.handler.data_received(data)

    def eof_received(self) -> bool | None:
        return self.handler.eof_received() if self.counted else None

    def pause_writing(self) -> None:
        if self.counted:
            self.handler.pause_writing()

    def resume_writing(self) -> None:
        if self.counted:
            self.handler.resume_writing()


# --------------------------------------------------------------------------- requests


async def _handle(request: web.Request) -> web.StreamResponse:
    if request.path.startswith(CHALLENGE_PATH):  # a domain check through the visitor's own proxy (external)
        answer = _challenge(request)
        if answer is not None:
            return answer
    return await _to_instance(request, request.app[INSTANCE], HOST.edge())


async def _to_instance(request: web.Request, name: str, edge: Edge) -> web.StreamResponse:
    route = HOST.route(name)
    if route is None:
        return status_pages.render(request, name, "gone")
    refused = access_gate.check(request, name, edge.client_ip_header, edge.https or request.secure)
    if refused is not None:  # production access rules, or a pass from the panel (access_gate.py)
        return refused
    if request.path == status_pages.STATUS_PATH:
        return await _status(request, name, route, edge)
    if not request.path.startswith(_NOT_A_VISIT):
        HOST.visited(name)
    if route.switched_off:
        return status_pages.render(request, name, "off")
    if request.path == CONNECT_PATH:
        return await _connect(request, name, edge)
    odoo_url = f"http://127.0.0.1:{route.odoo_port}"
    try:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            if route.websocket_port:  # Odoo with workers serves WebSockets on its gevent port only
                odoo_url = f"http://127.0.0.1:{route.websocket_port}"
            return await _forward_websocket(request, odoo_url, edge)
        if route.websocket_port and request.path.startswith(LONGPOLLING_PATH):  # Odoo 15 and earlier, with workers
            odoo_url = f"http://127.0.0.1:{route.websocket_port}"
        return await _forward_http(request, odoo_url, name, edge)
    except _OdooDown:
        return await HOST.not_ready(request, name)


_STATUS_ASKS: dict[str, tuple[float, int]] = {}  # visitor's address -> (minute, asks)
STATUS_ASKS_PER_MINUTE = 60


async def _status(request: web.Request, name: str, route: Route, edge: Edge) -> web.StreamResponse:
    """The public status address of an instance's pages (status_pages.status_json): its state and the public progress
    of its job, nothing else; at most STATUS_ASKS_PER_MINUTE asks a minute from one address. Never passed to Odoo."""
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Robots-Tag": "noindex"}
    if request.method != "GET":
        return web.json_response({"error": "Use GET."}, status=405, headers=headers)
    visitor = access_gate.visitor_ip(request, edge.client_ip_header)
    minute = time.time() // 60
    seen = _STATUS_ASKS.get(visitor)
    asks = seen[1] + 1 if seen and seen[0] == minute else 1
    if len(_STATUS_ASKS) > 10_000:
        _STATUS_ASKS.clear()
    _STATUS_ASKS[visitor] = (minute, asks)
    if asks > STATUS_ASKS_PER_MINUTE:
        return web.json_response({"error": "Too many requests."}, status=429, headers={**headers, "Retry-After": "30"})
    if route.switched_off:
        return web.json_response({"state": "off"}, headers=headers)
    if await _odoo_answers(route.odoo_port):
        return web.json_response(status_pages.status_json(None), headers=headers)
    return web.json_response(status_pages.status_json(await HOST.situation(name)), headers=headers)


async def _odoo_answers(port: int) -> bool:
    """Whether Odoo accepts a connection on its port now."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 1.5)
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


LONGPOLLING_PATH = "/longpolling/"  # the bus of Odoo 15 and earlier: served by the gevent server too, with workers
CONNECT_PATH = "/__sudoo/connect"  # connect_as.PATH: a Connect as link, answered here, never passed to Odoo
_CONNECT_PAGE = (
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Connect as</title>"
    "<body style='font:16px system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 16px;color:#1d1d1f'>"
    "<h1 style='font-size:20px'>This link cannot sign you in</h1><p>{}</p></body>"
)


async def _connect(request: web.Request, name: str, edge: Edge) -> web.StreamResponse:
    """A Connect as link: its token is used up; when it was valid, Odoo's session cookie is set for this address and
    the visitor goes to Odoo's backend, signed in as the chosen user. The answer is never cached, and the link is
    not passed on as the referrer."""
    import html

    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}
    if request.method != "GET":
        return web.Response(status=405, text="Use GET.", headers=headers)
    visitor = access_gate.visitor_ip(request, edge.client_ip_header)
    sid, why = await HOST.connect(name, request.query.get("token", ""), visitor)
    if sid is None:
        page = _CONNECT_PAGE.format(html.escape(why or "The link is not valid."))
        return web.Response(status=403, text=page, content_type="text/html", headers=headers)
    answer = web.Response(status=302, headers={**headers, "Location": "/web"})
    answer.set_cookie(
        "session_id", sid, path="/", httponly=True, samesite="Lax", secure=edge.https, max_age=7 * 24 * 3600
    )
    return answer


# --------------------------------------------------------------------------- custom domains (ports 80 and 443)


def _challenge(request: web.Request) -> web.Response | None:
    """The answer to an HTTP-01 check (or the panel's own probe) while one runs, else None."""
    from sudoo.instances import custom_domains

    answer = custom_domains.challenge_answer(request.path[len(CHALLENGE_PATH) :])
    return None if answer is None else web.Response(body=answer, content_type="text/plain")


def _domain_of(request: web.Request) -> str:
    return (request.url.host or "").lower().rstrip(".")


def _https_port() -> str:
    return "" if DOMAINS.https_port == 443 else f":{DOMAINS.https_port}"


def _server_name(domain: str) -> bool:
    """One of the server's own addresses (or PUBLIC_HOST) while the panel is answered here (EXPOSURE=direct)."""
    return DOMAINS.panel_port is not None and domain in DOMAINS.server_names


async def _handle_domain_http(request: web.Request) -> web.StreamResponse:
    """Port 80: HTTP-01 checks, then a permanent redirect to https once the domain has its certificate (the panel's
    own addresses: at once, to the panel's own certificate)."""
    domain = _domain_of(request)
    entry = DOMAINS.hosts.get(domain)
    if entry is None and _server_name(domain):
        host = f"[{domain}]" if ":" in domain else domain
        raise web.HTTPFound(f"https://{host}{_https_port()}{request.raw_path}")
    if entry is None:
        return web.Response(status=404, text="This domain is not served here.")
    if request.path.startswith(CHALLENGE_PATH):
        return _challenge(request) or web.Response(status=404, text="No check is running for this token.")
    if not entry.certificate:
        return web.Response(
            status=503, text="https for this domain is being set up. Try again in a few minutes.",
            headers={"Retry-After": "120"},
        )  # fmt: skip
    raise web.HTTPPermanentRedirect(f"https://{domain}{_https_port()}{request.raw_path}")


async def _handle_domain_https(request: web.Request) -> web.StreamResponse:
    """Port 443: the instance of the domain (or the panel, for its name), when the name the browser asked for (SNI)
    is the same as Host. With EXPOSURE=direct, the server's own addresses and a panel name without its certificate
    yet are the panel, on the panel's own certificate."""
    domain = _domain_of(request)
    entry = DOMAINS.hosts.get(domain)
    ssl_object = request.transport.get_extra_info("ssl_object") if request.transport else None
    asked = _CONTEXT_DOMAIN.get(ssl_object.context, "") if ssl_object is not None else ""
    if entry is not None and entry.certificate and asked == domain:
        if entry.panel:
            return await _to_panel(request)
        return await _to_instance(request, entry.instance, DirectEdge())
    if asked == SERVER_CERTIFICATE and (_server_name(domain) or (entry is not None and entry.panel)):
        return await _to_panel(request)
    return web.Response(status=421, text="This domain is not served on this connection.")


async def _to_panel(request: web.Request) -> web.StreamResponse:
    """Forward a visit of the panel's address to the web service (127.0.0.1:PANEL_PORT), cookies included, with the
    visitor's address in X-Sudoo-Client-IP."""
    panel_url = f"http://127.0.0.1:{DOMAINS.panel_port}"
    try:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await _forward_websocket(request, panel_url, DirectEdge(), panel=True)
        return await _forward_http(request, panel_url, "", DirectEdge(), panel=True)
    except _OdooDown:
        return web.Response(
            status=502, text="The panel is not answering right now. Try again in a moment.",
            headers={"Retry-After": "5"},
        )  # fmt: skip


_CONTEXTS: dict[str, tuple[tuple[int, int], ssl.SSLContext]] = {}  # domain -> (file's mtime and size, context)
# context -> its domain: the name the browser asked for (SNI) of a connection, from the context chosen for it
_CONTEXT_DOMAIN: weakref.WeakKeyDictionary[ssl.SSLContext, str] = weakref.WeakKeyDictionary()


def _new_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    return context


def _domain_context(domain: str) -> ssl.SSLContext | None:
    """The TLS context with the domain's certificate, loaded again when its file changed (a renewal)."""
    from sudoo.instances import custom_domains

    file = custom_domains.cert_file(domain)
    try:
        stat = file.stat()
    except OSError:
        return None
    seen = (stat.st_mtime_ns, stat.st_size)
    cached = _CONTEXTS.get(domain)
    if cached and cached[0] == seen:
        return cached[1]
    context = _new_context()
    try:
        context.load_cert_chain(file)
    except (OSError, ssl.SSLError) as error:
        print(f"Certificate of {domain} not usable: {error}", flush=True)
        return None
    _CONTEXTS[domain] = (seen, context)
    _CONTEXT_DOMAIN[context] = domain
    return context


def _choose_certificate(ssl_object: ssl.SSLObject, server_name: str | None, _: ssl.SSLContext) -> int | None:
    """SNI: the certificate of the domain asked for; an unknown name (or none) ends the handshake. While the panel is
    answered here (EXPOSURE=direct), no name, one of the server's own and the panel's name before its certificate
    get the panel's own certificate."""
    domain = (server_name or "").lower().rstrip(".")
    entry = DOMAINS.hosts.get(domain)
    context = _domain_context(domain) if entry and entry.certificate else None
    panel = not domain or domain in DOMAINS.server_names or (entry is not None and entry.panel)
    if context is None and DOMAINS.panel_port is not None and panel:
        context = _server_context()
    if context is None:
        return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
    ssl_object.context = context
    return None


def _tls_server() -> ssl.SSLContext:
    context = _new_context()
    context.sni_callback = _choose_certificate
    return context


def _server_context() -> ssl.SSLContext | None:
    """The TLS context with the panel's own certificate (instances/panel_address.py), loaded again when it changed."""
    from sudoo.instances import custom_domains

    file = custom_domains.cert_file(custom_domains.SERVER_CERT)
    try:
        stat = file.stat()
    except OSError:
        return None
    seen = (stat.st_mtime_ns, stat.st_size)
    cached = _CONTEXTS.get(SERVER_CERTIFICATE)
    if cached and cached[0] == seen:
        return cached[1]
    context = _new_context()
    try:
        context.load_cert_chain(file)
    except (OSError, ssl.SSLError) as error:
        print(f"The panel's own certificate is not usable: {error}", flush=True)
        return None
    _CONTEXTS[SERVER_CERTIFICATE] = (seen, context)
    _CONTEXT_DOMAIN[context] = SERVER_CERTIFICATE
    return context


def _choose_server_certificate(ssl_object: ssl.SSLObject, _name: str | None, _: ssl.SSLContext) -> int | None:
    context = _server_context()
    if context is None:
        return ssl.ALERT_DESCRIPTION_INTERNAL_ERROR
    ssl_object.context = context
    return None


def _server_tls() -> ssl.SSLContext:
    """An instance's gateway port with EXPOSURE=direct: https with the panel's own certificate."""
    context = _new_context()
    context.sni_callback = _choose_server_certificate
    return context


def refresh_domains() -> None:
    """The custom domains changed: serve them now (called from other threads)."""
    from sudoo.instances import gateway_link

    gateway_link.refresh_domains()


def _named_in_connection(headers: CIMultiDictProxy[str]) -> set[str]:
    """Headers a sender declared hop-by-hop by naming them in Connection (RFC 9110, 7.6.1)."""
    return {token.strip().lower() for value in headers.getall("Connection", ()) for token in value.split(",")}


def _forward_headers(request: web.Request, provider: Edge, panel: bool = False) -> CIMultiDict[str]:
    """Request headers for Odoo: the visitor's own, in order and repeated ones kept, without hop-by-hop headers and
    panel cookies, and with forwarding headers the visitor cannot forge.

    Odoo runs with proxy_mode = True and believes the last address of X-Forwarded-For (werkzeug's ProxyFix), so the
    gateway appends the address it can vouch for: the one the exposure plugin's own proxy reports
    (Cloudflare's CF-Connecting-IP), or else the address the connection comes from. Whatever a visitor put in front
    stays in front, where Odoo does not look; X-Forwarded-Proto and X-Forwarded-Host are always the gateway's.

    `panel`: a visit of the panel's own address (EXPOSURE=direct): its cookies pass, and X-Sudoo-Client-IP carries
    the address the connection comes from (any the visitor sent is dropped).
    """
    dropped = _HOP_BY_HOP | _SET_BY_GATEWAY | _named_in_connection(request.headers)
    headers: CIMultiDict[str] = CIMultiDict(
        (key, value) for key, value in request.headers.items() if key.lower() not in dropped
    )
    headers["Host"] = request.host
    cookies = [
        crumb.strip()
        for value in request.headers.getall("Cookie", ())
        for crumb in value.split(";")
        if crumb.strip() and (panel or crumb.split("=", 1)[0].strip() not in _PANEL_COOKIES)
    ]
    if cookies:  # a browser may send its cookies in several Cookie headers (HTTP/2): Odoo gets them as one
        headers["Cookie"] = "; ".join(cookies)
    visitor = access_gate.visitor_ip(request, getattr(provider, "client_ip_header", None))
    chain = ", ".join(request.headers.getall("X-Forwarded-For", ()))
    headers["X-Forwarded-For"] = f"{chain}, {visitor}" if chain else visitor
    if panel:
        headers[PANEL_CLIENT_IP_HEADER] = visitor
    headers["X-Forwarded-Proto"] = "https" if provider.https else request.scheme
    headers["X-Forwarded-Host"] = request.host
    return headers


async def _body(request: web.Request, limit: int, idle: float) -> AsyncIterator[bytes]:
    """The visitor's request body, passed on piece by piece as it arrives (an upload of several gigabytes never sits
    in memory), refused beyond `limit` bytes or when nothing arrives for `idle` seconds."""
    received = 0
    while True:
        try:
            async with asyncio.timeout(idle):
                chunk = await request.content.readany()
        except TimeoutError as exc:
            raise _BadBody(408, "The request body stopped arriving.") from exc
        if not chunk:
            return
        received += len(chunk)
        if received > limit:
            raise _BadBody(413, _too_large())
        yield chunk


def _bad_body(exc: BaseException) -> _BadBody | None:
    """The _BadBody behind an error of the upstream request, if that is what it is."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, _BadBody):
            return seen
        seen = seen.__cause__ or seen.__context__
    return None


def _too_large() -> str:
    return f"The request is larger than this server accepts ({settings.gateway_max_upload_mb} MB)."


async def _forward_http(
    request: web.Request, odoo_url: str, name: str, edge: Edge, panel: bool = False
) -> web.StreamResponse:
    assert _SESSION is not None  # created when the event loop starts
    headers = _forward_headers(request, edge, panel)
    body: AsyncIterator[bytes] | None = None
    if request.body_exists:
        limit = settings.gateway_max_upload_mb * 2**20
        if request.content_length is not None:
            if request.content_length > limit:
                return web.Response(status=413, text=_too_large())
            headers["Content-Length"] = str(request.content_length)  # passed on as it came, not chunked
        body = _body(request, limit, settings.gateway_idle_timeout_seconds)
    began = time.perf_counter()
    try:
        upstream = await _SESSION.request(
            request.method,
            URL(odoo_url + request.raw_path, encoded=True),
            headers=headers,
            data=body,
            allow_redirects=False,
        )
    except (_BadBody, aiohttp.ClientError) as exc:
        refused = _bad_body(exc)
        if refused is not None:  # the visitor's body was refused while it was being passed on
            return web.Response(status=refused.status, text=str(refused))
        if isinstance(exc, _UPSTREAM_DOWN):
            raise _OdooDown from exc
        raise
    if not panel and not request.path.startswith(_NOT_A_VISIT):  # long-polling would distort the response times
        HOST.answered(name, upstream.status, (time.perf_counter() - began) * 1000)
    async with upstream:
        response = web.StreamResponse(status=upstream.status, reason=upstream.reason)
        dropped = (_HOP_BY_HOP - {"content-length"}) | _named_in_connection(upstream.headers)
        for key, value in upstream.headers.items():
            lowered = key.lower()
            if lowered in dropped:
                continue
            if not panel and lowered == "set-cookie" and value.split("=", 1)[0].strip() in _PANEL_COOKIES:
                continue
            response.headers.add(key, value)
        try:
            await response.prepare(request)
            async for chunk in upstream.content.iter_any():  # whatever has arrived, at once (long polling, files)
                await response.write(chunk)
            await response.write_eof()
        except _CLIENT_GONE:
            pass  # the visitor closed the tab or cancelled a download
        return response


async def _forward_websocket(
    request: web.Request, odoo_url: str, edge: Edge, panel: bool = False
) -> web.StreamResponse:
    assert _SESSION is not None  # created when the event loop starts
    headers = CIMultiDict(
        (key, value)
        for key, value in _forward_headers(request, edge, panel).items()
        if not key.lower().startswith("sec-websocket")
    )
    try:
        upstream = await _SESSION.ws_connect(
            URL(odoo_url.replace("http", "ws", 1) + request.raw_path, encoded=True),
            headers=headers,
            autoping=False,
            max_msg_size=settings.gateway_websocket_max_message_mb * 2**20,
        )
    except _UPSTREAM_DOWN as exc:
        raise _OdooDown from exc
    except aiohttp.WSServerHandshakeError as exc:
        return web.Response(status=exc.status)
    # a message is held whole in memory before it is passed on: larger ones close the connection (code 1009)
    downstream = web.WebSocketResponse(autoping=False, max_msg_size=settings.gateway_websocket_max_message_mb * 2**20)
    await downstream.prepare(request)

    async def pump(
        source: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
        target: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
    ) -> None:
        async for message in source:
            if message.type == aiohttp.WSMsgType.TEXT:
                await target.send_str(message.data)
            elif message.type == aiohttp.WSMsgType.BINARY:
                await target.send_bytes(message.data)
            elif message.type == aiohttp.WSMsgType.PING:
                await target.ping(message.data)
            elif message.type == aiohttp.WSMsgType.PONG:
                await target.pong(message.data)
            else:
                break

    tasks = [asyncio.ensure_future(pump(downstream, upstream)), asyncio.ensure_future(pump(upstream, downstream))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await upstream.close()
        await downstream.close()
    return downstream


def _locked(name: str) -> bool:
    from sudoo.projects import access  # late: projects/ imports the instances package

    try:
        return access.instance_lock(name) is not None
    except Exception:  # noqa: BLE001 - a broken project document must not stop the visitor's page
        return False


def public_progress(name: str) -> dict[str, Any] | None:
    """What a visitor may see of the instance's job under way (status_pages.public_progress), or None."""
    return status_pages.public_progress(jobs.ACTIVE_JOB.get(name))


def wake_decision(name: str) -> tuple[str, float]:
    """Odoo is not answering: start the instance if it is not running (once a minute at most). Returns the page
    for its visitors ("waking", "busy" while another job runs, "upgrading" while a database upgrade runs, "off" when it is switched off, "locked" when it is
    paused and its branch is locked: a visit never starts it) and since when."""
    if STATE.get(name).get("switched_off"):
        return "off", time.time()
    job: jobs.Job | None = jobs.ACTIVE_JOB.get(name)
    if job is None and not STATUS.get(name, {}).get("running") and _locked(name):
        return "locked", time.time()
    if job is None and not STATUS.get(name, {}).get("running") and time.time() - _WAKE_REQUESTED.get(name, 0) > 60:
        _WAKE_REQUESTED[name] = time.time()
        assert _wake is not None  # set by start() before any listener exists
        job = _wake(name)
    if job is not None and job.kind == "db-upgrade":
        return "upgrading", job.started
    if job is not None and job.kind not in ("wake", "restart", "create"):
        return "busy", job.started
    return "waking", job.started if job else _WAKE_REQUESTED.get(name, time.time())
