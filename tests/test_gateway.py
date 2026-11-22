"""The gateway in front of an instance's Odoo: what it passes on and what it never does (instances/gateway.py).

A small aiohttp application stands in for Odoo; the gateway's own handler serves the instance on a test server.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import aiohttp
import pytest
from aiohttp import WSCloseCode, WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from sudoo import exposure
from sudoo.config import settings
from sudoo.instances import gateway, store

NAME = "gw-test"
WHO = web.AppKey("who", str)  # which stand-in server answered
RECEIVED: dict[str, Any] = {}


async def _echo(request: web.Request) -> web.Response:
    RECEIVED["headers"] = list(request.headers.items())
    response = web.json_response({"ok": True})
    response.set_cookie("session_id", "odoo-session")
    response.set_cookie("sudoo_session", "planted-by-odoo")
    response.headers["Connection"] = "X-Secret-Hop"
    response.headers["X-Secret-Hop"] = "must not reach the visitor"
    return response


async def _upload(request: web.Request) -> web.Response:
    digest, size = hashlib.sha256(), 0
    async for chunk in request.content.iter_any():
        digest.update(chunk)
        size += len(chunk)
    RECEIVED["upload_headers"] = dict(request.headers)
    return web.json_response({"size": size, "sha256": digest.hexdigest()})


async def _download(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse()
    response.content_length = 3 * 2**20
    await response.prepare(request)
    for _ in range(3):
        await response.write(b"\1" * 2**20)
    await response.write_eof()
    return response


async def _socket(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=0)
    await ws.prepare(request)
    async for message in ws:
        if message.type == WSMsgType.TEXT:
            await ws.send_str(message.data)
    return ws


def _odoo() -> web.Application:
    app = web.Application(client_max_size=2**30)
    app.router.add_get("/echo", _echo)
    app.router.add_post("/upload", _upload)
    app.router.add_get("/download", _download)
    app.router.add_get("/websocket", _socket)
    return app


class _Settings:
    """The panel's settings, with some values replaced for one test."""

    def __init__(self, **values: Any) -> None:
        self._values = values

    def __getattr__(self, name: str) -> Any:
        return self._values[name] if name in self._values else getattr(settings, name)


Scenario = Callable[[TestClient[web.Request, web.Application]], Awaitable[None]]


def _run(scenario: Scenario) -> None:
    """Run `scenario` with a client of the gateway in front of the stand-in Odoo."""

    async def main() -> None:
        odoo = TestServer(_odoo())
        await odoo.start_server()
        folder = store.folder(NAME)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "docker-compose.yml").write_text("services: {}\n")
        store.write_env(NAME, {"ODOO_PORT": odoo.port, "GATEWAY_PORT": 1})
        await gateway._create_session()
        app = web.Application()
        app[gateway.INSTANCE] = NAME
        app.router.add_route("*", "/{tail:.*}", gateway._handle)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            await scenario(client)
        finally:
            await client.close()
            assert gateway._SESSION is not None
            await gateway._SESSION.close()
            await odoo.close()
            shutil.rmtree(folder)  # other tests list the instances

    asyncio.run(main())


def _received(name: str) -> list[str]:
    return [value for key, value in RECEIVED["headers"] if key.lower() == name.lower()]


def test_headers_passed_on_without_panel_cookies_hop_by_hop_or_forged_addresses() -> None:
    async def scenario(client: TestClient[web.Request, web.Application]) -> None:
        answer = await client.get(
            "/echo",
            headers=[
                ("Cookie", "session_id=abc; sudoo_session=panel-secret"),
                ("Cookie", "frontend_lang=en_US;  sudoo_session=again"),
                ("Connection", "keep-alive, X-Private"),
                ("X-Private", "only for the next hop"),
                ("Proxy-Connection", "keep-alive"),
                ("X-Forwarded-For", "6.6.6.6"),
                ("X-Forwarded-Proto", "http"),
                ("X-Forwarded-Host", "evil.example"),
                ("CF-Connecting-IP", "203.0.113.7"),
                ("X-Repeated", "one"),
                ("X-Repeated", "two"),
            ],
            skip_auto_headers=("Accept-Encoding",),
        )
        assert answer.status == 200
        assert _received("Cookie") == ["session_id=abc; frontend_lang=en_US"]
        assert "sudoo_session" not in json.dumps(RECEIVED["headers"])
        assert _received("X-Private") == [] and _received("Proxy-Connection") == []
        # Odoo believes the last address: the one Cloudflare reported, never the visitor's own claim
        assert _received("X-Forwarded-For") == ["6.6.6.6, 203.0.113.7"]
        assert _received("X-Forwarded-Proto") == ["https"]
        assert _received("X-Forwarded-Host") == [f"127.0.0.1:{client.port}"]
        assert _received("X-Repeated") == ["one", "two"]
        assert _received("Accept-Encoding") == []  # never asks Odoo for an encoding the visitor cannot read
        assert answer.cookies["session_id"].value == "odoo-session"
        assert "sudoo_session" not in answer.cookies  # Odoo cannot set the panel's cookie
        assert "X-Secret-Hop" not in answer.headers

    _run(scenario)


def test_without_a_trusted_proxy_the_connection_address_is_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    class Direct:
        client_ip_header = None
        https = False

    monkeypatch.setattr(exposure, "provider", lambda: Direct())

    async def scenario(client: TestClient[web.Request, web.Application]) -> None:
        answer = await client.get("/echo", headers={"X-Forwarded-For": "6.6.6.6", "CF-Connecting-IP": "6.6.6.7"})
        assert answer.status == 200
        assert _received("X-Forwarded-For") == ["6.6.6.6, 127.0.0.1"]
        assert _received("X-Forwarded-Proto") == ["http"]

    _run(scenario)


def test_uploads_are_streamed_whole_with_or_without_a_length() -> None:
    payload = bytes(range(256)) * 20_000  # about 5 MB

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(payload), 100_000):
            yield payload[start : start + 100_000]

    async def scenario(client: TestClient[web.Request, web.Application]) -> None:
        expected = {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        answer = await client.post("/upload", data=payload)
        assert answer.status == 200 and await answer.json() == expected
        assert RECEIVED["upload_headers"]["Content-Length"] == str(len(payload))
        answer = await client.post("/upload", data=chunks())  # chunked: no length known in advance
        assert answer.status == 200 and await answer.json() == expected
        download = await client.get("/download")
        assert download.status == 200 and len(await download.read()) == 3 * 2**20

    _run(scenario)


def test_uploads_beyond_the_limit_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway, "settings", _Settings(gateway_max_upload_mb=1))
    RECEIVED.pop("upload_headers", None)

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(30):
            yield b"x" * 100_000

    async def scenario(client: TestClient[web.Request, web.Application]) -> None:
        answer = await client.post("/upload", data=b"x" * (2**20 + 1))
        assert answer.status == 413
        assert "upload_headers" not in RECEIVED  # refused before Odoo was asked
        answer = await client.post("/upload", data=chunks())
        assert answer.status == 413

    _run(scenario)


def test_a_body_that_stops_arriving_is_given_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway, "settings", _Settings(gateway_idle_timeout_seconds=0.3))

    async def slow() -> AsyncIterator[bytes]:
        yield b"x" * 1000
        await asyncio.sleep(2)
        yield b"x"

    async def scenario(client: TestClient[web.Request, web.Application]) -> None:
        try:
            answer = await client.post("/upload", data=slow())
        except aiohttp.ClientError:
            return  # the gateway may also close the connection while the client is still sending
        assert answer.status == 408

    _run(scenario)


def test_websocket_messages_pass_and_oversized_ones_close_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway, "settings", _Settings(gateway_websocket_max_message_mb=1))

    async def scenario(client: TestClient[web.Request, web.Application]) -> None:
        async with client.ws_connect("/websocket") as ws:
            await ws.send_str("hello")
            assert (await ws.receive(timeout=5)).data == "hello"
            await ws.send_str("x" * (2**20 + 10))
            message = await ws.receive(timeout=5)
            assert message.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED)
            assert ws.close_code in (WSCloseCode.MESSAGE_TOO_BIG, None) or message.data == WSCloseCode.MESSAGE_TOO_BIG

    _run(scenario)


def test_ambiguous_request_framing_is_refused_before_odoo() -> None:
    """A request whose length is ambiguous (request smuggling) never reaches Odoo."""
    RECEIVED.pop("upload_headers", None)

    async def scenario(client: TestClient[web.Request, web.Application]) -> None:
        for framing in (
            b"Content-Length: 4\r\nTransfer-Encoding: chunked\r\n",
            b"Content-Length: 4\r\nContent-Length: 5\r\n",
            b"Transfer-Encoding: chunked, identity\r\n",
        ):
            reader, writer = await asyncio.open_connection("127.0.0.1", client.port)
            writer.write(b"POST /upload HTTP/1.1\r\nHost: x\r\n" + framing + b"\r\n0\r\n\r\nabcde")
            await writer.drain()
            assert (await asyncio.wait_for(reader.readline(), 5)).split()[1] == b"400"
            writer.close()
        assert "upload_headers" not in RECEIVED

    _run(scenario)


async def _poll(request: web.Request) -> web.Response:
    await asyncio.sleep(1.2)  # longer than the header timeout below: a request being answered is not cut short
    return web.json_response({"ok": True})


def test_slow_headers_are_cut_off_and_connections_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gateway,
        "settings",
        _Settings(gateway_header_timeout_seconds=0.5, gateway_max_connections=3, gateway_max_connections_total=100),
    )

    async def main() -> None:
        odoo_app = _odoo()
        odoo_app.router.add_get("/longpolling/poll", _poll)
        odoo = TestServer(odoo_app)
        await odoo.start_server()
        folder = store.folder(NAME)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "docker-compose.yml").write_text("services: {}\n")
        store.write_env(NAME, {"ODOO_PORT": odoo.port, "GATEWAY_PORT": 1})
        await gateway._create_session()
        runner, listener = await gateway._serve(NAME, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        loop = asyncio.get_running_loop()
        try:
            # headers that never finish: the connection is closed after the header timeout
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /echo HTTP/1.1\r\nHost: x\r\n")
            began = loop.time()
            assert await asyncio.wait_for(reader.read(), 3) == b""
            assert loop.time() - began < 2
            writer.close()
            await asyncio.sleep(0.1)
            # three connections are allowed at once; the fourth is answered 503 and closed
            held = [await asyncio.open_connection("127.0.0.1", port) for _ in range(3)]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            assert (await asyncio.wait_for(reader.read(), 3)).startswith(b"HTTP/1.1 503")
            writer.close()
            for _, idle in held:
                idle.close()
            await asyncio.sleep(0.1)
            assert gateway._OPEN[NAME] == 0
            # long polling and WebSockets outlast the header timeout
            async with aiohttp.ClientSession() as visitor:
                async with visitor.get(f"http://127.0.0.1:{port}/longpolling/poll") as answer:
                    assert answer.status == 200
                async with visitor.ws_connect(f"http://127.0.0.1:{port}/websocket") as ws:
                    await asyncio.sleep(1)
                    await ws.send_str("still here")
                    assert (await ws.receive(timeout=5)).data == "still here"
        finally:
            listener.close()
            await runner.cleanup()
            assert gateway._SESSION is not None
            await gateway._SESSION.close()
            await odoo.close()
            shutil.rmtree(folder)

    asyncio.run(main())


def test_with_workers_the_bus_goes_to_odoos_gevent_server() -> None:
    """With workers, Odoo serves WebSockets (17 and later) and long polling (15 and earlier) on its gevent port only:
    the gateway sends them there, and everything else to the HTTP port."""

    async def where(request: web.Request) -> web.Response:
        return web.json_response({"server": request.app[WHO], "path": request.path})

    async def tagged_socket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                await ws.send_str(f"{request.app[WHO]}:{message.data}")
        return ws

    def server(who: str) -> web.Application:
        app = web.Application()
        app[WHO] = who
        app.router.add_get("/websocket", tagged_socket)
        app.router.add_route("*", "/{tail:.*}", where)
        return app

    async def main() -> None:
        http, gevent = TestServer(server("http")), TestServer(server("gevent"))
        await http.start_server()
        await gevent.start_server()
        folder = store.folder(NAME)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "docker-compose.yml").write_text("services: {}\n")
        store.write_env(NAME, {"ODOO_PORT": http.port, "GATEWAY_PORT": 1, "ODOO_GEVENT_PORT": gevent.port,
                               "ODOO_WORKERS": 2})  # fmt: skip
        await gateway._create_session()
        app = web.Application()
        app[gateway.INSTANCE] = NAME
        app.router.add_route("*", "/{tail:.*}", gateway._handle)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            async with client.ws_connect("/websocket") as ws:
                await ws.send_str("hello")
                assert (await ws.receive(timeout=5)).data == "gevent:hello"
            assert (await (await client.post("/longpolling/poll", json={})).json())["server"] == "gevent"
            assert (await (await client.get("/web/login")).json())["server"] == "http"
            store.update_env(NAME, ODOO_WORKERS=0)  # threaded mode again: everything to the HTTP port
            async with client.ws_connect("/websocket") as ws:
                await ws.send_str("hello")
                assert (await ws.receive(timeout=5)).data == "http:hello"
            assert (await (await client.post("/longpolling/poll", json={})).json())["server"] == "http"
        finally:
            await client.close()
            assert gateway._SESSION is not None
            await gateway._SESSION.close()
            await http.close()
            await gevent.close()
            shutil.rmtree(folder)

    asyncio.run(main())
