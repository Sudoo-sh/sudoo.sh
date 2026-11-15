"""A stand-in for Odoo behind the gateway, for the load tests (perf/README.md): answers at once, so what is measured
is the gateway, not Odoo.

    uv run python perf/stub_odoo.py [--port 19300] [--processes 4]

    GET  /web                    a 25 KB page, with a session cookie
    GET  /web/assets/<any>.js    a 400 KB script (as Odoo serves its bundles: not compressed)
    POST /web/dataset/call_kw    a small JSON-RPC answer
    GET  /longpolling/poll       answers after 2 seconds (Odoo's long polling)
    GET  /websocket              echoes every message (Odoo's bus)
    POST /web/binary/upload      reads the body and answers with its size
    GET  /web/content/download?mb=N   N megabytes
    GET  /echo-headers           the request headers it received, as JSON (security checks)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import signal
import socket
import sys

from aiohttp import WSMsgType, web

PAGE = ("<!doctype html><html><head><title>Odoo</title></head><body>" + "<div class='o_row'>row</div>" * 900)[
    :25_000
].encode()
SCRIPT = b"/* odoo bundle */\n" + b"odoo.define('x', function(){ return 42; });\n" * 9300
CHUNK = b"\0" * 2**20


async def page(_: web.Request) -> web.Response:
    response = web.Response(body=PAGE, content_type="text/html")
    response.set_cookie("session_id", "stub-session", httponly=True)
    return response


async def script(_: web.Request) -> web.Response:
    return web.Response(body=SCRIPT, content_type="application/javascript")


async def rpc(request: web.Request) -> web.Response:
    await request.read()
    return web.json_response({"jsonrpc": "2.0", "id": 1, "result": [{"id": 1, "name": "Azure Interior"}]})


async def poll(_: web.Request) -> web.Response:
    await asyncio.sleep(2)
    return web.json_response([])


async def websocket(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async for message in ws:
        if message.type == WSMsgType.TEXT:
            await ws.send_str(message.data)
        elif message.type == WSMsgType.BINARY:
            await ws.send_bytes(message.data)
    return ws


async def upload(request: web.Request) -> web.Response:
    size = 0
    async for chunk in request.content.iter_any():
        size += len(chunk)
    return web.json_response({"received": size})


async def download(request: web.Request) -> web.StreamResponse:
    megabytes = int(request.query.get("mb", "10"))
    response = web.StreamResponse(headers={"Content-Type": "application/octet-stream"})
    response.content_length = megabytes * len(CHUNK)
    await response.prepare(request)
    for _ in range(megabytes):
        await response.write(CHUNK)
    await response.write_eof()
    return response


async def echo_headers(request: web.Request) -> web.Response:
    return web.json_response({"headers": [[k, v] for k, v in request.headers.items()], "remote": request.remote})


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "pass"})


def app() -> web.Application:
    application = web.Application(client_max_size=2**34)
    application.router.add_get("/web", page)
    application.router.add_get("/web/assets/{name}", script)
    application.router.add_post("/web/dataset/call_kw", rpc)
    application.router.add_get("/longpolling/poll", poll)
    application.router.add_get("/websocket", websocket)
    application.router.add_post("/web/binary/upload", upload)
    application.router.add_get("/web/content/download", download)
    application.router.add_get("/echo-headers", echo_headers)
    application.router.add_get("/web/health", health)
    return application


def serve(sock: socket.socket) -> None:
    web.run_app(app(), sock=sock, access_log=None, print=None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=19300)
    parser.add_argument("--processes", type=int, default=4)
    args = parser.parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    sock.listen(1024)
    sock.set_inheritable(True)
    print(json.dumps({"stub": f"http://127.0.0.1:{args.port}", "processes": args.processes}), flush=True)
    context = multiprocessing.get_context("fork")
    workers = [context.Process(target=serve, args=(sock,), daemon=True) for _ in range(args.processes)]
    for worker in workers:
        worker.start()

    def stop(*_: object) -> None:
        for worker in workers:
            worker.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    for worker in workers:
        worker.join()


if __name__ == "__main__":
    main()
