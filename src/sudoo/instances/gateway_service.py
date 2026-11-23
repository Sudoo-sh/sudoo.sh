"""The gateway service: the gateway (gateway.py) in its own process, the sudoo-gateway container (`sudoo.sh gateway`).

It holds every instance's gateway port, so visitors keep reaching the instances while the panel restarts or is
down. It has no Docker access, no `.env`, no secrets: it reads and writes only GATEWAY_DIR (<ODOO_ROOT>/gateway),
which it shares with the panel:

    routes.json    written by the panel (gateway_link.py): the instances (gateway port, Odoo port, switched off),
                   the address to listen on and the visitor-address header the exposure trusts, the gateway's
                   settings (limits, contact details for the pages) and the custom domains (their instance, whether
                   they have a certificate, ports 80/443); with EXPOSURE=direct also whether the gateway ports speak
                   https, the panel's name, the server's own addresses and the web service's port, where the panel's
                   visits go; the production access rules and the key of the panel's passes (access_gate.py). Read again whenever it changes; a file that is not valid is refused and the last valid
                   one kept.
    certs/         the custom domains' certificates and keys, acme-challenge/ the answers of running domain checks
                   (written by the panel, domains/certificates.py)
    geo/           the country database of the access rules (geoip.py), written by the panel
    visits.json    written here: the last visit of every instance. The panel reads it (automatic pause, daily
                   backups), also when it was down while the visits happened.
    gateway.sock   served here, for the panel: POST /sync (read routes.json now and reconcile the listeners; the
                   answer has the listeners, their errors and the requests counted since the last call, for the
                   metrics, and the uses of the panel's passes, for the audit log) and GET /healthz (the
                   container's health check).
    panel.sock     served by the panel: POST /wake/<name> starts a paused instance (Docker is the panel's alone) and answers
                   with the page to show and the public progress of the instance's job;
                   POST /connect/<name> {"token", "ip"} answers a Connect as link (connect_as.py) with {"sid"} or
                   {"error"}.

When the panel cannot be reached, a visitor of a paused instance gets "Starting soon" and the page asks again every
few seconds; running instances are not affected at all. Both sockets are Unix sockets in a folder only root can
open (mode 700): they are not reachable over the network, nor from the instances' containers.

On a normal stop the service removes gateway.sock first, waits a moment so the panel can take over the ports (both
open them with SO_REUSEPORT), then closes its listeners.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from sudoo.config import live, settings
from sudoo.core.vault import write_private
from sudoo.instances import access_gate, gateway, gateway_health, metrics, status_pages, store

ROUTES = "routes.json"
VISITS = "visits.json"
GATEWAY_SOCKET = "gateway.sock"
PANEL_SOCKET = "panel.sock"
FORMAT = 1  # version of routes.json and visits.json
CONNECT_SECONDS = 150  # how long a Connect as link may wait for the panel (it starts `odoo shell` in the instance)

ROUND_SECONDS = 2.0  # how often routes.json is checked and failed ports are tried again
VISITS_SECONDS = 5.0  # how often new visits are written
ASK_SECONDS = 3.0  # an instance's answer from the panel is reused this long (a page reloads every 4 seconds)
HANDOVER_SECONDS = 3.0  # on a normal stop: time the panel gets to take over the ports

# the settings the gateway uses; the panel hands over its current values in routes.json
ROUTED_SETTINGS = (
    "gateway_max_upload_mb",
    "gateway_idle_timeout_seconds",
    "gateway_websocket_max_message_mb",
    "gateway_header_timeout_seconds",
    "gateway_max_connections",
    "gateway_max_connections_total",
    "admin_contact_name",
    "admin_contact_phone",
    "admin_contact_email",
)
_HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


def path(file: str) -> Path:
    return settings.gateway_dir / file


def socket_path(name: str) -> Path:
    """A control socket: in GATEWAY_DIR, or in a short private folder when that path is too long for a Unix socket."""
    return Path(gateway_health.socket_file(str(settings.gateway_dir), name))


def prepare_dir() -> None:
    """GATEWAY_DIR, readable by root only (the sockets in it are the control channel)."""
    settings.gateway_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(settings.gateway_dir, 0o700)
    gateway_health.prepare_short_dir(str(settings.gateway_dir))


# --------------------------------------------------------------------------- routes.json


@dataclass(frozen=True, slots=True)
class Edge:
    listen_host: str = "127.0.0.1"
    https: bool = False
    client_ip_header: str | None = None
    gateway_tls: bool = False  # EXPOSURE=direct: the gateway ports speak https, with the panel's own certificate


@dataclass(frozen=True)
class Routes:
    """routes.json, checked."""

    mode: str = "service"
    edge: Edge = field(default_factory=Edge)
    settings: dict[str, Any] = field(default_factory=dict)
    instances: dict[str, tuple[int, gateway.Route]] = field(default_factory=dict)  # name -> (gateway port, route)
    domains: gateway.DomainRoutes = field(default_factory=gateway.DomainRoutes)
    access: Any = None  # the production access rules, as access_gate.load() takes them

    def wanted(self) -> dict[str, tuple[str, int]]:
        """The listeners these routes ask for: none unless the gateways run here (mode "service"); the custom
        domains' two listeners come from `domains` (gateway.reconcile)."""
        if self.mode != "service":
            return {}
        return {name: (self.edge.listen_host, port) for name, (port, _) in self.instances.items()}

    def served_domains(self) -> gateway.DomainRoutes:
        """The custom domains these routes ask for: none unless the gateways run here."""
        return self.domains if self.mode == "service" else gateway.DomainRoutes()


def _port(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise ValueError(f"not a port: {value!r}")
    return value


def parse_routes(data: Any) -> Routes:
    """Routes from the content of routes.json; raises ValueError when anything in it is not as expected."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("not a routes file of a known format")
    mode = data.get("mode")
    if mode not in ("service", "embedded"):
        raise ValueError(f"unknown mode {mode!r}")
    raw_edge = data.get("edge")
    if not isinstance(raw_edge, dict):
        raise ValueError("no edge")
    listen_host = str(ipaddress.ip_address(str(raw_edge.get("listen_host"))))
    header = raw_edge.get("client_ip_header")
    if header is not None and not (isinstance(header, str) and _HEADER_RE.match(header)):
        raise ValueError("client_ip_header is not a header name")
    edge = Edge(listen_host, raw_edge.get("https") is True, header, raw_edge.get("gateway_tls") is True)
    values = data.get("settings", {})
    if not isinstance(values, dict):
        raise ValueError("no settings")
    # only the gateway's own settings; others (from a newer panel) are left out, not refused
    values = {key: value for key, value in values.items() if key in ROUTED_SETTINGS}
    instances: dict[str, tuple[int, gateway.Route]] = {}
    raw_instances = data.get("instances")
    if not isinstance(raw_instances, dict):
        raise ValueError("no instances")
    ports: set[int] = set()
    for name, entry in raw_instances.items():
        if not store.NAME_RE.match(name) or not isinstance(entry, dict):
            raise ValueError(f"not an instance: {name!r}")
        port = _port(entry.get("gateway_port"))
        if port in ports:
            raise ValueError(f"gateway port {port} given twice")
        ports.add(port)
        websocket = entry.get("websocket_port")  # absent in files of earlier panels, and in threaded mode
        instances[name] = (
            port,
            gateway.Route(
                _port(entry.get("odoo_port")),
                entry.get("switched_off") is True,
                None if websocket is None else _port(websocket),
                _odoo_host(entry.get("odoo_host", "127.0.0.1")),
            ),
        )
    access = data.get("access")  # absent in files of earlier panels: no rules
    access_gate.parse_section(access)  # refused here when not valid, with the rest of the file
    return Routes(mode, edge, dict(values), instances, _parse_domains(data.get("domains"), instances, ports), access)


def _odoo_host(value: Any) -> str:
    """An instance's Odoo address: an IP address only (the Kubernetes engine's Service address), never a name."""
    try:
        return str(ipaddress.ip_address(str(value)))
    except ValueError:
        raise ValueError(f"not an address: {str(value)[:60]!r}") from None


def _parse_domains(raw: Any, instances: dict[str, tuple[int, gateway.Route]], ports: set[int]) -> gateway.DomainRoutes:
    """The custom domains of routes.json (absent in files of earlier panels: none)."""
    if raw is None:
        return gateway.DomainRoutes()
    if not isinstance(raw, dict) or not isinstance(raw.get("hosts"), dict):
        raise ValueError("domains is not as expected")
    http_port, https_port = _port(raw.get("http_port")), _port(raw.get("https_port"))
    if http_port == https_port or {http_port, https_port} & ports:
        raise ValueError("the domains' ports are taken")
    hosts: dict[str, gateway.DomainHost] = {}
    for domain, entry in raw["hosts"].items():
        if not isinstance(domain, str) or not _DOMAIN_RE.match(domain) or not isinstance(entry, dict):
            raise ValueError(f"not a domain: {str(domain)[:80]!r}")
        if entry.get("panel") is True:  # the panel's own name (EXPOSURE=direct)
            hosts[domain] = gateway.DomainHost("", entry.get("certificate") is True, panel=True)
            continue
        if entry.get("instance") not in instances:
            raise ValueError(f"{domain}: unknown instance")
        hosts[domain] = gateway.DomainHost(str(entry["instance"]), entry.get("certificate") is True)
    panel_port = raw.get("panel_port")  # absent unless the panel is answered here
    if panel_port is not None:
        panel_port = _port(panel_port)
        if panel_port in ports | {http_port, https_port}:
            raise ValueError("the panel's port is taken")
    names = raw.get("server_names", [])
    if not isinstance(names, list) or len(names) > 16:
        raise ValueError("server_names is not as expected")
    server_names: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            raise ValueError("not a server name")
        try:
            server_names.add(str(ipaddress.ip_address(name)))
        except ValueError:
            if _DOMAIN_RE.match(name):  # anything else (a name without a dot) is left out
                server_names.add(name)
    return gateway.DomainRoutes(http_port, https_port, hosts, panel_port, frozenset(server_names))


def routes_document(
    mode: str,
    edge: Edge,
    values: dict[str, Any],
    instances: dict[str, dict[str, Any]],
    domains: gateway.DomainRoutes | None = None,
    access: dict[str, Any] | None = None,
) -> str:
    """The text of routes.json (written by the panel, gateway_link.py)."""
    document: dict[str, Any] = {
        "format": FORMAT,
        "mode": mode,
        "edge": {
            "listen_host": edge.listen_host, "https": edge.https, "client_ip_header": edge.client_ip_header,
            "gateway_tls": edge.gateway_tls,
        },  # fmt: skip
        "settings": values,
        "instances": instances,
    }
    if domains is not None:
        document["domains"] = {
            "http_port": domains.http_port,
            "https_port": domains.https_port,
            "hosts": {
                domain: (
                    {"panel": True, "certificate": host.certificate}
                    if host.panel
                    else {"instance": host.instance, "certificate": host.certificate}
                )
                for domain, host in domains.hosts.items()
            },
        }
        if domains.panel_port is not None:
            document["domains"]["panel_port"] = domains.panel_port
            document["domains"]["server_names"] = sorted(domains.server_names)
    if access is not None:
        document["access"] = access
    return json.dumps(document, indent=1, sort_keys=True)


# --------------------------------------------------------------------------- visits.json


def read_visits(file: Path) -> dict[str, float]:
    """The visits in visits.json ({name: Unix time}); entries that are not valid are left out."""
    try:
        data = json.loads(file.read_text())
    except (OSError, ValueError):
        return {}
    visits = data.get("visits") if isinstance(data, dict) and data.get("format") == FORMAT else None
    if not isinstance(visits, dict):
        return {}
    return {
        name: float(when)
        for name, when in visits.items()
        if isinstance(name, str)
        and store.NAME_RE.match(name)
        and isinstance(when, int | float)
        and not isinstance(when, bool)
        and 0 < when < time.time() + 300
    }


# --------------------------------------------------------------------------- the service's host


class ServiceHost:
    """The gateway in its own process: routes from the panel's routes.json, visits to visits.json, metrics counted
    until the panel collects them, paused instances started by asking the panel."""

    def __init__(self) -> None:
        self.routes = Routes()
        self.routes_seen: tuple[int, int] | None = None  # routes.json's (mtime_ns, size) when last read
        self.routes_problem = ""
        self.visits: dict[str, float] = {}
        self.visits_dirty = False
        self.waiting_since: dict[str, float] = {}  # instance -> when the panel could first not be asked
        self.answers: dict[str, tuple[float, status_pages.Situation]] = {}  # instance -> (asked at, answer)
        self._panel: aiohttp.ClientSession | None = None

    # ---- Host

    def route(self, name: str) -> gateway.Route | None:
        entry = self.routes.instances.get(name)
        return entry[1] if entry else None

    def edge(self) -> gateway.Edge:
        return self.routes.edge

    def visited(self, name: str) -> None:
        self.visits[name] = time.time()
        self.visits_dirty = True

    def answered(self, name: str, status: int, duration_ms: float) -> None:
        metrics.record_request(name, status, duration_ms)

    async def not_ready(self, request: web.Request, name: str) -> web.StreamResponse:
        found = await self.situation(name)
        return status_pages.render(request, name, found.page, since=found.since, progress=found.progress)

    async def situation(self, name: str) -> status_pages.Situation:
        return await self.ask_panel(name)

    # ---- asking the panel to start an instance

    async def ask_panel(self, name: str) -> status_pages.Situation:
        """The page for a visitor of an instance whose Odoo does not answer: the panel's ("waking", "busy",
        "off", "locked"; it starts the instance if needed) with the public progress of its job, or "waiting" while
        the panel cannot be reached."""
        now = time.time()
        cached = self.answers.get(name)
        if cached and now - cached[0] < ASK_SECONDS:
            return cached[1]
        progress = None
        try:
            if self._panel is None:
                self._panel = aiohttp.ClientSession(
                    connector=aiohttp.UnixConnector(path=str(socket_path(PANEL_SOCKET))),
                    timeout=aiohttp.ClientTimeout(total=5),
                )
            async with self._panel.post(f"http://panel/wake/{name}") as answer:
                answer.raise_for_status()
                data = await answer.json()
            page, since = data["page"], float(data["since"])
            if page not in ("waking", "busy", "upgrading", "off", "locked"):
                raise ValueError(page)
            progress = status_pages.clean_progress(data.get("progress"))
            self.waiting_since.pop(name, None)
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError, KeyError, TypeError):
            page, since = "waiting", self.waiting_since.setdefault(name, now)
        found = status_pages.Situation(page, since, progress)
        self.answers[name] = (now, found)
        return found

    async def connect(self, name: str, token: str, ip: str) -> tuple[str | None, str]:
        """A Connect as link: the panel uses up the token and writes the Odoo session (Docker is the panel's alone)."""
        try:
            async with (
                aiohttp.ClientSession(
                    connector=aiohttp.UnixConnector(path=str(socket_path(PANEL_SOCKET))),
                    timeout=aiohttp.ClientTimeout(total=CONNECT_SECONDS),
                ) as panel,
                panel.post(f"http://panel/connect/{name}", json={"token": token, "ip": ip}) as answer,
            ):
                data = await answer.json()
            sid = data.get("sid")
            if isinstance(sid, str) and sid:
                return sid, ""
            return None, str(data.get("error") or "The link is not valid.")
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError, AttributeError):
            return None, "The panel cannot be reached right now. Try again in a minute."

    async def close(self) -> None:
        if self._panel is not None:
            await self._panel.close()

    # ---- routes and visits

    def read_routes(self, force: bool = False) -> None:
        """Read routes.json when it changed (or `force`); keep the last valid routes when it is not valid."""
        file = path(ROUTES)
        try:
            stat = file.stat()
        except FileNotFoundError:
            return  # the panel has not written it yet: nothing to serve, or keep what was served
        seen = (stat.st_mtime_ns, stat.st_size)
        if seen == self.routes_seen and not force:
            return
        self.routes_seen = seen
        try:
            routes = parse_routes(json.loads(file.read_text()))
            live.use(routes.settings)
            access_gate.load(routes.access)
        except (OSError, ValueError) as error:
            if str(error) != self.routes_problem:
                self.routes_problem = str(error)
                print(f"{ROUTES} not used ({error}); keeping the routes read before", flush=True)
            return
        self.routes_problem = ""
        self.routes = routes

    def load_visits(self) -> None:
        self.visits = read_visits(path(VISITS))

    def save_visits(self) -> None:
        if not self.visits_dirty:
            return
        self.visits_dirty = False
        if self.routes.instances:  # forget the visits of deleted instances
            self.visits = {name: when for name, when in self.visits.items() if name in self.routes.instances}
        write_private(path(VISITS), json.dumps({"format": FORMAT, "visits": self.visits}))

    def status(self, drain: bool) -> dict[str, Any]:
        """The control channel's answer: what is served, and (with `drain`) the requests counted since the last
        time the panel collected them."""
        answer: dict[str, Any] = {
            "mode": self.routes.mode,
            "listening": gateway.listening(),
            "errors": dict(gateway.LISTENER_ERRORS),
            "connections": {name: count for name, count in gateway._OPEN.items() if count},
            "routes_problem": self.routes_problem or None,
        }
        if drain:
            answer["requests"] = {
                name: {"count": counted.count, "errors": counted.errors, "durations_ms": counted.durations_ms}
                for name, counted in metrics._take_requests().items()
            }
            answer["passes"] = access_gate.take_used()
        return answer


# --------------------------------------------------------------------------- the process


class Service:
    def __init__(self, host: ServiceHost | None = None) -> None:
        self.host = host or ServiceHost()
        self.stopping = asyncio.Event()
        self._control: web.AppRunner | None = None

    async def refresh(self, force: bool = False) -> None:
        self.host.read_routes(force)
        await gateway.reconcile(self.host.routes.wanted(), self.host.routes.served_domains())

    async def _sync(self, _: web.Request) -> web.Response:
        await self.refresh(force=True)
        return web.json_response(self.host.status(drain=True))

    async def _health(self, _: web.Request) -> web.Response:
        return web.json_response(self.host.status(drain=False))

    async def start(self) -> None:
        prepare_dir()
        self.host.load_visits()
        gateway.use(self.host)
        await gateway._create_session()
        await self.refresh(force=True)
        app = web.Application()
        app.router.add_post("/sync", self._sync)
        app.router.add_get("/healthz", self._health)
        self._control = web.AppRunner(app, access_log=None, handle_signals=False)
        await self._control.setup()
        sock = socket_path(GATEWAY_SOCKET)
        sock.unlink(missing_ok=True)  # left by a service that did not stop normally
        await web.UnixSite(self._control, str(sock)).start()
        os.chmod(sock, 0o600)

    async def run(self) -> None:
        """Serve until stopped (SIGTERM, SIGINT or stop())."""
        try:
            await self.start()
        except BaseException:
            if gateway._SESSION is not None:  # closed here: aiohttp would report it at exit
                await gateway._SESSION.close()
            raise
        loop = asyncio.get_running_loop()
        for stop_signal in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, RuntimeError):  # not in the main thread (tests)
                loop.add_signal_handler(stop_signal, self.stopping.set)
        print(f"Gateway service started: {len(gateway.listening())} instance(s)", flush=True)
        saved = time.monotonic()
        while not self.stopping.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), ROUND_SECONDS)
            if self.stopping.is_set():
                break
            await self.refresh()
            if time.monotonic() - saved >= VISITS_SECONDS:
                self.host.save_visits()
                saved = time.monotonic()
        await self.stop()

    async def stop(self, handover: float = HANDOVER_SECONDS) -> None:
        """Remove the control socket (the panel then takes over the ports), wait `handover` seconds, close."""
        socket_path(GATEWAY_SOCKET).unlink(missing_ok=True)
        if self._control is not None:
            await self._control.cleanup()
            self._control = None
        if handover and gateway.listening():
            await asyncio.sleep(handover)
        await gateway.reconcile({}, gateway.DomainRoutes())
        self.host.save_visits()
        await self.host.close()
        if gateway._SESSION is not None:
            await gateway._SESSION.close()
        print("Gateway service stopped", flush=True)


def check() -> bool:
    """True when the gateway service answers on its control socket (as the container's health check asks)."""
    return gateway_health.answers(str(socket_path(GATEWAY_SOCKET)))


def main(health_check: bool = False) -> None:
    """`sudoo.sh gateway`: run the gateway service (`--check`: exit 0 when a running one answers)."""
    if health_check:
        sys.exit(0 if check() else 1)
    asyncio.set_event_loop(gateway._LOOP)
    gateway._LOOP.run_until_complete(Service().run())
