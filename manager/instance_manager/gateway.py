"""The gateway: receives every visit to an instance and forwards it to Odoo.

Each instance has its own gateway port on 127.0.0.1 (GATEWAY_PORT), which its Cloudflare connector points at.
The gateway forwards HTTP and WebSocket traffic to Odoo (127.0.0.1:ODOO_PORT) and, because it sees every
request, it can:
    - record the last visit (used for automatic pausing)
    - start a paused instance on the first request and show a "Starting up" page meanwhile
    - show "Update in progress" / "Switched off" pages
    - keep the panel's cookies away from Odoo

It runs on its own asyncio event loop in a background thread of the panel process.
"""
import asyncio
import threading
import time

import aiohttp
from aiohttp import web
from yarl import URL

from . import instances, jobs, status_pages
from .monitor import STATUS
from .state import STATE

_LOOP = asyncio.new_event_loop()
_SESSION = None
_RUNNERS = {}
LISTENER_ERRORS = {}
_WAKE_REQUESTED = {}
_wake = None              # callable(name) -> Job; set by start()

_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
               "trailers", "transfer-encoding", "upgrade", "content-length", "expect", "host"}
_NOT_A_VISIT = ("/websocket", "/longpolling", "/web/health", "/bus/")   # background traffic of an open tab
_PANEL_COOKIES = {"oim_session"}
_UPSTREAM_DOWN = (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, aiohttp.ClientOSError)
_CLIENT_GONE = (ConnectionResetError, aiohttp.ClientConnectionResetError, aiohttp.ClientPayloadError)


class _OdooDown(Exception):
    """Odoo did not accept the connection (paused, starting or stopped)."""


# --------------------------------------------------------------------------- lifecycle

def start(wake):
    """Start the event loop thread and a listener for every instance. `wake(name)` starts a wake-up job."""
    global _wake
    _wake = wake
    threading.Thread(target=_run_loop, daemon=True, name="gateway").start()
    for name in instances.names():
        add(name)


def _run_loop():
    asyncio.set_event_loop(_LOOP)
    _LOOP.run_until_complete(_create_session())
    _LOOP.run_forever()


async def _create_session():
    global _SESSION
    _SESSION = aiohttp.ClientSession(auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar(),
                                     timeout=aiohttp.ClientTimeout(total=None, sock_connect=5),
                                     connector=aiohttp.TCPConnector(limit=0))


def add(name):
    """Listen on the instance's gateway port (called from other threads)."""
    asyncio.run_coroutine_threadsafe(_add(name), _LOOP).result(15)


def remove(name):
    asyncio.run_coroutine_threadsafe(_remove(name), _LOOP).result(15)


async def _add(name):
    if name in _RUNNERS:
        return
    port = int(instances.read_env(name)["GATEWAY_PORT"])
    app = web.Application(client_max_size=2 * 1024 ** 3)
    app["instance"] = name
    app.router.add_route("*", "/{tail:.*}", _handle)
    runner = web.AppRunner(app, access_log=None, handle_signals=False)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", port, reuse_address=True).start()
    except OSError as exc:
        LISTENER_ERRORS[name] = f"Gateway port {port} is used by another program ({exc.strerror})."
        await runner.cleanup()
        return
    LISTENER_ERRORS.pop(name, None)
    _RUNNERS[name] = runner


async def _remove(name):
    runner = _RUNNERS.pop(name, None)
    if runner:
        await runner.cleanup()


# --------------------------------------------------------------------------- requests

async def _handle(request):
    name = request.app["instance"]
    if not instances.exists(name):
        return status_pages.render(request, name, "gone")
    if not request.path.startswith(_NOT_A_VISIT):
        STATE.record_visit(name)
    if STATE.get(name).get("switched_off"):
        return status_pages.render(request, name, "off")
    odoo_url = f"http://127.0.0.1:{instances.read_env(name)['ODOO_PORT']}"
    try:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await _forward_websocket(request, odoo_url)
        return await _forward_http(request, odoo_url)
    except _OdooDown:
        return _not_ready(request, name)


def _forward_headers(request):
    """Request headers for Odoo: without hop-by-hop headers and panel cookies, with the visitor's details.

    Requests reach the gateway only from this server (the instance's connector), so the forwarding headers it
    adds (real visitor address, https) are trusted and passed on; Odoo runs with proxy_mode = True.
    """
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    headers["Host"] = request.host
    if "Cookie" in headers:
        kept = [c for c in headers.pop("Cookie").split(";") if c.split("=", 1)[0].strip() not in _PANEL_COOKIES]
        if kept:
            headers["Cookie"] = ";".join(kept).strip()
    headers.setdefault("X-Forwarded-For", request.remote or "")
    headers.setdefault("X-Forwarded-Proto", request.scheme)
    headers["X-Forwarded-Host"] = request.host
    return headers


async def _forward_http(request, odoo_url):
    body = await request.read() if request.body_exists else None
    try:
        upstream = await _SESSION.request(request.method, URL(odoo_url + request.raw_path, encoded=True),
                                          headers=_forward_headers(request), data=body, allow_redirects=False)
    except _UPSTREAM_DOWN as exc:
        raise _OdooDown from exc
    async with upstream:
        response = web.StreamResponse(status=upstream.status, reason=upstream.reason)
        for key, value in upstream.headers.items():
            if key.lower() in _HOP_BY_HOP - {"content-length"}:
                continue
            if key.lower() == "set-cookie" and value.split("=", 1)[0].strip() in _PANEL_COOKIES:
                continue
            response.headers.add(key, value)
        try:
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(65536):
                await response.write(chunk)
            await response.write_eof()
        except _CLIENT_GONE:
            pass          # the visitor closed the tab or cancelled a download
        return response


async def _forward_websocket(request, odoo_url):
    headers = {k: v for k, v in _forward_headers(request).items() if not k.lower().startswith("sec-websocket")}
    try:
        upstream = await _SESSION.ws_connect(URL(odoo_url.replace("http", "ws", 1) + request.raw_path, encoded=True),
                                             headers=headers, autoping=False, max_msg_size=0)
    except _UPSTREAM_DOWN as exc:
        raise _OdooDown from exc
    except aiohttp.WSServerHandshakeError as exc:
        return web.Response(status=exc.status)
    downstream = web.WebSocketResponse(autoping=False, max_msg_size=0)
    await downstream.prepare(request)

    async def pump(source, target):
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


def _not_ready(request, name):
    """Odoo is not answering: start it if it is not running (once a minute at most) and tell the visitor."""
    job = jobs.ACTIVE_JOB.get(name)
    if job is None and not STATUS.get(name, {}).get("running") and time.time() - _WAKE_REQUESTED.get(name, 0) > 60:
        _WAKE_REQUESTED[name] = time.time()
        job = _wake(name)
    if job is not None and job.kind not in ("wake", "restart", "create"):
        return status_pages.render(request, name, "busy", since=job.started)
    since = job.started if job else _WAKE_REQUESTED.get(name, time.time())
    return status_pages.render(request, name, "waking", since=since)
