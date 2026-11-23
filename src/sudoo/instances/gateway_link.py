"""The panel's side of the gateway service (GATEWAY_MODE=service; gateway_service.py is the other side).

Every ROUND_SECONDS, and at once when an instance is created or deleted (gateway.add, gateway.remove), the panel:
    1. writes routes.json when it changed: every instance with its two ports and whether it is switched off, the
       exposure's listen address and trusted visitor-address header, and the gateway's current settings;
    2. asks the gateway service to sync (gateway.sock): it reads routes.json, reconciles its listeners and answers
       with them, their errors (shown on the instance's page) and the requests it counted since the last round,
       which are added to the metrics (metrics.py);
    3. reads visits.json and takes the newer visits into the instances' state (automatic pause, daily backups).

It also serves panel.sock, where the gateway service asks POST /wake/<name> when an instance's Odoo does not answer:
the panel starts it (gateway.wake_decision) and says which page to show; and POST /connect/<name> when a Connect as
link is opened (connect_as.py).

Hand-over. While the gateway service cannot be reached, the panel serves the instances itself, with gateway.py's
listeners in this process, exactly as in embedded mode: at once when gateway.sock does not exist (no service runs, or
it stopped normally and removed it), after GATEWAY_TAKEOVER_SECONDS when it exists but does not answer (the service
hangs or crashed). When the service answers again, the panel closes each listener the service reports as its own.
Both open the ports with SO_REUSEPORT, so for a moment both may accept visitors; that is harmless, both forward the
same way.

In embedded mode the panel only writes routes.json with mode "embedded", so a gateway service that still runs gives
the ports back.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import socket
import threading
import time
from typing import Any

from aiohttp import web

from sudoo import exposure
from sudoo.config import settings
from sudoo.core import audit
from sudoo.core.state import STATE
from sudoo.core.vault import chmod_private, write_private
from sudoo.instances import gateway, metrics, store
from sudoo.instances.gateway_service import (
    GATEWAY_SOCKET,
    PANEL_SOCKET,
    ROUTED_SETTINGS,
    ROUTES,
    VISITS,
    Edge,
    parse_routes,
    path,
    prepare_dir,
    read_visits,
    routes_document,
    socket_path,
)

ROUND_SECONDS = 2.0
_LOCK = threading.Lock()
_WITHDRAWN: set[str] = set()  # instances being deleted: no longer routed, although their folder still exists


class _Link:
    """What the panel knows about the gateway service."""

    def __init__(self) -> None:
        self.started = time.time()
        self.last_seen: float | None = None  # last answer from the service
        self.collect = False  # False after a gap: the first counts cover an unknown time and are left out
        self.written: str | None = None  # routes.json as last written
        self.visits_seen: tuple[int, int] | None = None
        self.serving_reported = False


LINK = _Link()


def panel_should_serve(now: float, last_seen: float | None, started: float, socket_exists: bool) -> bool:
    """Whether the panel serves the instances itself, the gateway service not answering: at once when its
    socket does not exist, after GATEWAY_TAKEOVER_SECONDS without an answer otherwise."""
    if not socket_exists:
        return True
    return now - max(last_seen or 0.0, started) >= settings.gateway_takeover_seconds


# --------------------------------------------------------------------------- routes.json


def build_routes(mode: str) -> str:
    provider = exposure.provider()
    edge = Edge(
        provider.listen_host, provider.https, provider.client_ip_header, bool(getattr(provider, "gateway_tls", False))
    )
    values = {key: getattr(settings, key) for key in ROUTED_SETTINGS}
    names = store.names()
    _WITHDRAWN.intersection_update(names)
    instances: dict[str, dict[str, Any]] = {}
    for name in names:
        if name in _WITHDRAWN:
            continue
        env = store.read_env(name)
        try:
            ports = int(env["GATEWAY_PORT"]), int(env["ODOO_PORT"])
        except (KeyError, ValueError):
            continue  # still being created
        switched_off = bool(STATE.get(name).get("switched_off"))
        instances[name] = {"gateway_port": ports[0], "odoo_port": ports[1], "switched_off": switched_off}
        websocket = gateway.websocket_port(env)
        if websocket:
            instances[name]["websocket_port"] = websocket
    return routes_document(mode, edge, values, instances, domain_routes(set(instances)))


def domain_routes(instances: set[str] | None = None) -> gateway.DomainRoutes:
    """The custom domains the gateway answers on ports 80/443 (custom_domains.gateway_hosts), of instances routed
    now."""
    from sudoo.instances import custom_domains as domains

    hosts = {
        domain: gateway.DomainHost(entry["instance"], bool(entry["certificate"]))
        for domain, entry in domains.gateway_hosts().items()
        if instances is None or entry["instance"] in instances
    }
    panel_port, server_names = None, frozenset[str]()
    if getattr(exposure.provider(), "serves_panel", False):  # EXPOSURE=direct: the panel's address too
        from sudoo.instances import panel_address

        name = panel_address.domain()
        if name and name not in hosts:
            hosts[name] = gateway.DomainHost("", panel_address.certificate_ready(), panel=True)
        portal = panel_address.portal_domain()  # the client portal: a second name of the panel (PORTAL_DOMAIN)
        if portal and portal not in hosts:
            hosts[portal] = gateway.DomainHost(
                "", panel_address.certificate_ready(panel_address.PORTAL_KEY), panel=True
            )
        panel_port = settings.panel_port
        server_names = frozenset(panel_address.wanted_names())
    return gateway.DomainRoutes(settings.domain_http_port, settings.domain_https_port, hosts, panel_port, server_names)


def refresh_domains() -> None:
    """The custom domains changed: in service mode a round now (routes.json, and the panel's own listeners while
    it serves the instances itself); embedded: this process's listeners."""
    if settings.gateway_mode == "service":
        if LINK.written is not None:  # the link runs (not before the panel started it)
            sync()
        return
    wanted = {name: address for name, address in gateway._PORTS.items() if not name.startswith("*")}
    gateway.run(gateway.reconcile(wanted, domain_routes()))


def write_routes(text: str) -> None:
    if text == LINK.written and path(ROUTES).exists():
        return
    write_private(path(ROUTES), text, within=settings.gateway_dir)
    LINK.written = text


# --------------------------------------------------------------------------- the control channel


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("gateway", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(self.timeout)
        if os.path.islink(self.socket_path):  # the gateway's folder: a link there is not the gateway's socket
            conn.close()
            raise OSError(f"{self.socket_path} is a link")
        conn.connect(self.socket_path)
        self.sock = conn


def ask_gateway(timeout: float = 5) -> dict[str, Any] | None:
    """POST /sync to the gateway service: its answer, or None when it cannot be reached."""
    conn = _UnixHTTPConnection(str(socket_path(GATEWAY_SOCKET)), timeout)
    try:
        conn.request("POST", "/sync")
        answer = conn.getresponse()
        if answer.status != 200:
            return None
        data = json.loads(answer.read())
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()
    if not isinstance(data, dict) or not isinstance(data.get("listening"), dict):
        return None
    return data


def _numbers(values: Any) -> list[float]:
    if not isinstance(values, list):
        return []
    return [float(v) for v in values[: metrics.MAX_DURATIONS] if isinstance(v, int | float) and not isinstance(v, bool)]


def _take_counts(requests: Any, known: set[str]) -> None:
    if not isinstance(requests, dict):
        return
    for name, counted in requests.items():
        if name not in known or not isinstance(counted, dict):
            continue
        count, errors = counted.get("count"), counted.get("errors")
        if isinstance(count, int) and isinstance(errors, int) and 0 <= errors <= count:
            metrics.add_requests(name, count, errors, _numbers(counted.get("durations_ms")))


# --------------------------------------------------------------------------- one round


def sync() -> None:
    """One round (see the module's description). Called from other threads."""
    with _LOCK:
        text = build_routes("service")
        write_routes(text)
        answer = ask_gateway()
        now = time.time()
        known = set(store.names())
        if answer is not None:
            LINK.last_seen = now
            if LINK.collect:
                _take_counts(answer.get("requests"), known)
            LINK.collect = True
            theirs = {name for name in answer["listening"] if isinstance(name, str)}
            for name in set(gateway.listening()) & theirs:  # hand the ports back
                gateway.run(gateway._unlisten(name))
            if LINK.serving_reported and not gateway.listening():
                LINK.serving_reported = False
                print("The gateway service serves the instances again", flush=True)
            errors = answer.get("errors")
            errors = errors if isinstance(errors, dict) else {}
            mine = set(gateway.listening())
            gateway.LISTENER_ERRORS.clear()
            domain_listeners = {gateway.HTTP_LISTENER, gateway.HTTPS_LISTENER}
            gateway.LISTENER_ERRORS.update(
                {
                    name: str(text)[:200]
                    for name, text in errors.items()
                    if (name in known or name in domain_listeners) and name not in mine
                }
            )
        else:
            LINK.collect = False
            if panel_should_serve(now, LINK.last_seen, LINK.started, socket_path(GATEWAY_SOCKET).exists()):
                routes = parse_routes(json.loads(text))
                gateway.run(gateway.reconcile(routes.wanted(), routes.served_domains()))
                if not LINK.serving_reported:
                    LINK.serving_reported = True
                    print("The gateway service does not answer: the panel serves the instances itself", flush=True)
                    audit.record(
                        "panel", None, "gateway service not reachable", ok=False,
                        detail="The panel serves the instances itself until the gateway service is back.",
                    )  # fmt: skip
        merge_visits(known)


def merge_visits(known: set[str]) -> None:
    """Take the visits the gateway service recorded (visits.json) into the instances' state."""
    file = path(VISITS)
    try:
        stat = file.stat()
    except FileNotFoundError:
        return
    seen = (stat.st_mtime_ns, stat.st_size)
    if seen == LINK.visits_seen:
        return
    LINK.visits_seen = seen
    for name, when in read_visits(file).items():
        if name in known:
            STATE.merge_visit(name, when)


def _forever() -> None:
    while True:
        time.sleep(ROUND_SECONDS)
        try:
            sync()
        except Exception as error:  # noqa: BLE001 - the link must keep running; the next round tries again
            print(f"Gateway link: {error!r}", flush=True)


# --------------------------------------------------------------------------- panel.sock


async def _wake(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if not store.exists(name):
        return web.json_response({"page": "gone"}, status=404)
    page, since = await asyncio.to_thread(gateway.wake_decision, name)
    return web.json_response({"page": page, "since": since})


async def _connect(request: web.Request) -> web.Response:
    """A Connect as link opened on the gateway service: use up the token, write the Odoo session (connect_as.py)."""
    from sudoo.instances import connect_as

    name = request.match_info["name"]
    try:
        data = await request.json()
        token, ip = str(data.get("token") or ""), str(data.get("ip") or "")[:64]
    except (ValueError, AttributeError):
        return web.json_response({"error": "The link is not valid."}, status=400)
    if not store.NAME_RE.match(name) or not store.exists(name):
        return web.json_response({"error": "This instance does not exist."}, status=404)
    try:
        sid = await asyncio.to_thread(connect_as.connect, name, token, ip)
    except connect_as.ConnectError as error:
        return web.json_response({"error": str(error)}, status=403)
    return web.json_response({"sid": sid})


async def serve_wake() -> web.AppRunner:
    """Serve POST /wake/<name> and POST /connect/<name> on panel.sock (on the gateway's event loop)."""
    app = web.Application()
    app.router.add_post("/wake/{name}", _wake)
    app.router.add_post("/connect/{name}", _connect)
    runner = web.AppRunner(app, access_log=None, handle_signals=False)
    await runner.setup()
    sock = socket_path(PANEL_SOCKET)
    sock.unlink(missing_ok=True)
    await web.UnixSite(runner, str(sock)).start()
    chmod_private(sock)  # never through a link the gateway swapped in
    return runner


# --------------------------------------------------------------------------- called by gateway.py


def start() -> None:
    """When the panel starts (gateway.start)."""
    prepare_dir()
    if settings.gateway_mode == "embedded":
        with _LOCK:
            write_routes(build_routes("embedded"))
        ask_gateway()  # a gateway service that still runs gives the ports back at once
        return
    gateway.submit(serve_wake())
    sync()  # now: when no gateway service runs, the panel serves the instances from the start
    threading.Thread(target=_forever, daemon=True, name="gateway-link").start()


def add(name: str) -> None:
    _WITHDRAWN.discard(name)
    sync()


def remove(name: str) -> None:
    _WITHDRAWN.add(name)
    sync()
