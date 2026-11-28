"""The gateway service (instances/gateway_service.py) and the panel's link to it (instances/gateway_link.py).

The gateway service runs every instance's gateway port in its own process, so instances keep answering while the
panel is down; the panel hands it the instances (routes.json), collects its counts and visits, and starts paused
instances for it. A small aiohttp application stands in for Odoo.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from sudoo.config import live, settings
from sudoo.core.state import STATE
from sudoo.instances import gateway, gateway_health, gateway_link, gateway_service, metrics, store

REPO = Path(__file__).resolve().parent.parent
NAME = "gws-test"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def _odoo_app() -> web.Application:
    async def echo(request: web.Request) -> web.Response:
        return web.json_response({"cookie": request.headers.get("Cookie"), "path": request.path})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", echo)
    return app


def _routes(gateway_port: int, odoo_port: int, mode: str = "service", switched_off: bool = False) -> str:
    edge = gateway_service.Edge("127.0.0.1", True, "CF-Connecting-IP")
    values = {key: getattr(settings, key) for key in gateway_service.ROUTED_SETTINGS}
    entry = {"gateway_port": gateway_port, "odoo_port": odoo_port, "switched_off": switched_off}
    return gateway_service.routes_document(mode, edge, values, {NAME: entry})


def _make_instance(gateway_port: int = 1, odoo_port: int = 2) -> None:
    folder = store.folder(NAME)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(NAME, {"GATEWAY_PORT": gateway_port, "ODOO_PORT": odoo_port})


def _remove_instance() -> None:
    shutil.rmtree(store.folder(NAME), ignore_errors=True)  # other tests list the instances
    STATE.remove(NAME)


# --------------------------------------------------------------------------- routes.json


def test_routes_are_checked_and_a_bad_file_keeps_the_last_good_routes(tmp_path: Path) -> None:
    good = json.loads(_routes(8100, 28100))
    routes = gateway_service.parse_routes(good)
    assert routes.wanted() == {NAME: ("127.0.0.1", 8100)}
    assert routes.instances[NAME][1] == gateway.Route(28100, False)
    assert routes.edge.client_ip_header == "CF-Connecting-IP"
    # only the gateway's own settings are taken
    assert gateway_service.parse_routes({**good, "settings": {"smtp_password": "x"}}).settings == {}
    # embedded: the panel serves the instances, the service holds nothing
    assert gateway_service.parse_routes({**good, "mode": "embedded"}).wanted() == {}
    for bad in (
        {**good, "format": 99},
        {**good, "mode": "both"},
        {**good, "edge": {**good["edge"], "listen_host": "example.com"}},
        {**good, "edge": {**good["edge"], "client_ip_header": "X: injected"}},
        {**good, "settings": "gateway_max_upload_mb=1"},
        {**good, "instances": {"../etc": {"gateway_port": 1, "odoo_port": 2}}},
        {**good, "instances": {NAME: {"gateway_port": 70000, "odoo_port": 2}}},
        {
            **good,
            "instances": {NAME: {"gateway_port": 8100, "odoo_port": 2}, "b": {"gateway_port": 8100, "odoo_port": 3}},
        },
    ):
        with pytest.raises(ValueError):
            gateway_service.parse_routes(bad)

    host = gateway_service.ServiceHost()
    file = gateway_service.path(gateway_service.ROUTES)
    gateway_service.prepare_dir()
    try:
        file.write_text(_routes(8100, 28100))
        host.read_routes()
        assert host.route(NAME) == gateway.Route(28100, False)
        file.write_text("{not json")
        host.read_routes()
        assert host.route(NAME) == gateway.Route(28100, False) and host.routes_problem
        file.write_text(_routes(8100, 28100, switched_off=True))
        host.read_routes()
        assert host.route(NAME) == gateway.Route(28100, True) and not host.routes_problem
    finally:
        file.unlink(missing_ok=True)
        live.use({})
        live._frozen = False  # this test process is the panel again: it reads settings.json


def test_the_panel_hands_over_its_settings_to_the_gateway_service() -> None:
    document = json.loads(_routes(8100, 28100))
    document["settings"]["gateway_max_upload_mb"] = 7
    document["settings"]["admin_contact_name"] = "Sam"
    host = gateway_service.ServiceHost()
    gateway_service.prepare_dir()
    file = gateway_service.path(gateway_service.ROUTES)
    try:
        file.write_text(json.dumps(document))
        host.read_routes()
        assert settings.gateway_max_upload_mb == 7 and "Sam" in settings.contact_sentence()
        document["settings"]["gateway_max_upload_mb"] = 0  # not valid: refused as a whole
        file.write_text(json.dumps(document))
        host.read_routes()
        assert settings.gateway_max_upload_mb == 7 and host.routes_problem
    finally:
        file.unlink(missing_ok=True)
        live.use({})
        live._frozen = False  # this test process is the panel again: it reads settings.json
    assert settings.gateway_max_upload_mb == 2048


def test_the_panel_writes_the_routes_of_its_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_instance(8123, 28123)
    try:
        routes = gateway_service.parse_routes(json.loads(gateway_link.build_routes("service")))
        assert routes.instances[NAME] == (8123, gateway.Route(28123, False))
        STATE.update(NAME, switched_off=True)
        routes = gateway_service.parse_routes(json.loads(gateway_link.build_routes("service")))
        assert routes.instances[NAME][1].switched_off
        # being deleted: no longer routed although its folder is still there
        monkeypatch.setattr(gateway_link, "_WITHDRAWN", {NAME})
        assert NAME not in gateway_service.parse_routes(json.loads(gateway_link.build_routes("service"))).instances
    finally:
        _remove_instance()


# --------------------------------------------------------------------------- listeners


def test_listeners_follow_the_wanted_ports_and_retry_a_taken_one() -> None:
    async def main() -> None:
        first, second = _free_port(), _free_port()
        try:
            await gateway.reconcile({NAME: ("127.0.0.1", first)})
            assert gateway.listening() == {NAME: first}
            await gateway.reconcile({NAME: ("127.0.0.1", second)})  # the port changed
            assert gateway.listening() == {NAME: second}
            with pytest.raises(OSError):
                socket.create_connection(("127.0.0.1", first), timeout=1).close()
            await gateway.reconcile({})
            assert gateway.listening() == {}
            # a port another program holds (without SO_REUSEPORT): reported, then taken once it is free
            squatter = socket.socket()
            squatter.bind(("127.0.0.1", first))
            squatter.listen()
            await gateway.reconcile({NAME: ("127.0.0.1", first)})
            assert gateway.listening() == {} and "used by another program" in gateway.LISTENER_ERRORS[NAME]
            squatter.close()
            await gateway.reconcile({NAME: ("127.0.0.1", first)})
            assert gateway.listening() == {NAME: first} and NAME not in gateway.LISTENER_ERRORS
        finally:
            await gateway.reconcile({})

    asyncio.run(main())


# --------------------------------------------------------------------------- waking, with the panel up and down


class _Job:
    kind = "wake"
    started = 1_000.0


def test_wake_through_the_panel_and_starting_soon_while_it_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    woken: list[str] = []

    def wake(name: str) -> Any:
        woken.append(name)
        return _Job()

    monkeypatch.setattr(gateway, "_wake", wake)
    monkeypatch.setattr(gateway, "_WAKE_REQUESTED", {})
    closed_port = _free_port()  # nothing listens: Odoo is paused
    _make_instance(odoo_port=closed_port)
    gateway_service.prepare_dir()

    async def main() -> None:
        host = gateway_service.ServiceHost()
        host.routes = gateway_service.parse_routes(json.loads(_routes(_free_port(), closed_port)))
        gateway.use(host)
        await gateway._create_session()
        port = _free_port()
        await gateway.reconcile({NAME: ("127.0.0.1", port)})
        url = f"http://127.0.0.1:{port}/odoo"
        html = {"Accept": "text/html"}
        panel = await gateway_link.serve_wake()  # the panel is up
        try:
            async with aiohttp.ClientSession() as visitor:
                async with visitor.get(url, headers=html) as answer:
                    assert answer.status == 503 and "Starting up" in await answer.text()
                assert woken == [NAME]
                async with visitor.get(url, headers=html) as answer:  # within ASK_SECONDS: not asked again
                    assert "Starting up" in await answer.text()
                assert woken == [NAME]

                await panel.cleanup()  # the panel goes down
                host.answers.clear()
                async with visitor.get(url, headers=html) as answer:
                    assert answer.status == 503 and answer.headers["Retry-After"] == "4"
                    page = await answer.text()
                    assert "Starting soon" in page and 'http-equiv="refresh"' in page
                assert NAME in host.waiting_since and woken == [NAME]

                panel = await gateway_link.serve_wake()  # back: the next try reaches it
                host.answers.clear()
                gateway._WAKE_REQUESTED.clear()
                async with visitor.get(url, headers=html) as answer:
                    assert "Starting up" in await answer.text()
                assert woken == [NAME, NAME] and NAME not in host.waiting_since

                STATE.update(NAME, switched_off=True)  # switched off in the panel: nothing is started
                host.answers.clear()
                async with visitor.get(url) as answer:
                    assert answer.status == 503 and (await answer.text()).startswith("Switched off")
                assert woken == [NAME, NAME]
        finally:
            await panel.cleanup()
            await gateway.reconcile({})
            await host.close()
            assert gateway._SESSION is not None
            await gateway._SESSION.close()
            gateway.use(gateway.PanelHost())

    try:
        asyncio.run(main())
    finally:
        _remove_instance()


# --------------------------------------------------------------------------- the panel's side: hand-over


def test_when_the_panel_serves_the_instances_itself() -> None:
    takeover = settings.gateway_takeover_seconds
    # no gateway service (or it stopped normally and removed its socket): at once
    assert gateway_link.panel_should_serve(100, None, 100, socket_exists=False)
    # a socket that does not answer: only after GATEWAY_TAKEOVER_SECONDS without an answer
    assert not gateway_link.panel_should_serve(100 + takeover - 1, None, 100, socket_exists=True)
    assert gateway_link.panel_should_serve(100 + takeover, None, 100, socket_exists=True)
    assert not gateway_link.panel_should_serve(500, 500 - takeover + 1, 100, socket_exists=True)
    assert gateway_link.panel_should_serve(500, 500 - takeover, 100, socket_exists=True)


_LOOP_STARTED: list[bool] = []


def _panel_loop() -> None:
    if not _LOOP_STARTED:
        # the panel's gateway loop, without its client session (nothing is forwarded here)
        threading.Thread(target=gateway._LOOP.run_forever, daemon=True, name="gateway").start()
        _LOOP_STARTED.append(True)


def test_handover_between_the_panel_and_the_gateway_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """The panel serves while the service is missing, hands each port back once the service reports it as its
    own, and takes the service's counts and visits."""
    _panel_loop()
    port = _free_port()
    _make_instance(port, _free_port())
    gateway_service.prepare_dir()
    sock = gateway_service.path(gateway_service.GATEWAY_SOCKET)
    sock.unlink(missing_ok=True)
    answers: list[dict[str, Any] | None] = []
    monkeypatch.setattr(gateway_link, "ask_gateway", lambda timeout=5: answers.pop(0) if answers else None)
    monkeypatch.setattr(gateway_link, "LINK", gateway_link._Link())
    monkeypatch.setattr(gateway_link.audit, "record", lambda *args, **kwargs: None)
    try:
        # 1. no gateway service: the panel listens itself, at once
        gateway_link.sync()
        assert gateway.listening() == {NAME: port}
        assert gateway_service.parse_routes(json.loads(gateway_service.path("routes.json").read_text())).instances

        # 2. the service answers and holds the port: the panel gives it back; the first counts are left out
        listening = {"listening": {NAME: port}, "errors": {}}
        answers.append({**listening, "requests": {NAME: {"count": 50, "errors": 0, "durations_ms": [1.0]}}})
        gateway_link.sync()
        assert gateway.listening() == {}
        answers.append({**listening, "requests": {NAME: {"count": 2, "errors": 1, "durations_ms": [10.0, 30.0]}}})
        gateway_link.sync()
        counted = metrics._take_requests()[NAME]
        assert (counted.count, counted.errors, counted.durations_ms) == (2, 1, [10.0, 30.0])

        # 3. the service stops answering but its socket is there (it hangs): the panel waits, then takes over
        sock.touch()
        gateway_link.sync()
        assert gateway.listening() == {}
        gateway_link.LINK.last_seen = gateway_link.LINK.started = time.time() - settings.gateway_takeover_seconds
        gateway_link.sync()
        assert gateway.listening() == {NAME: port}

        # 4. visits the service recorded while the panel was away are taken into the state
        STATE.update(NAME, last_visit=1_000.0, used_since=None)
        visit = time.time() - 5
        gateway_service.path(gateway_service.VISITS).write_text(json.dumps({"format": 1, "visits": {NAME: visit}}))
        gateway_link.sync()
        assert STATE.get(NAME)["last_visit"] == visit and STATE.get(NAME)["used_since"] == visit
        assert not STATE.merge_visit(NAME, visit - 60)  # older: ignored
    finally:
        gateway.run(gateway.reconcile({}))
        sock.unlink(missing_ok=True)
        gateway_service.path(gateway_service.VISITS).unlink(missing_ok=True)
        gateway_service.path(gateway_service.ROUTES).unlink(missing_ok=True)
        _remove_instance()


# --------------------------------------------------------------------------- the real process, without the panel


async def _wait_for(path: Path, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path} did not appear")
        await asyncio.sleep(0.1)


def test_the_gateway_service_process_serves_instances_while_no_panel_runs() -> None:
    """`sudoo.sh gateway` in its own process, with nothing of the panel running: it takes a port the panel still
    holds (SO_REUSEPORT), answers visitors, records visits for the panel, shows "Starting soon" for a paused
    instance, and on a normal stop removes its socket before closing the ports."""
    root = Path(tempfile.mkdtemp(prefix="sudoo-gws-"))
    (root / "gateway").mkdir(mode=0o700)
    environment = {**os.environ, "ODOO_ROOT": str(root)}

    async def main() -> None:
        odoo = TestServer(await _odoo_app())
        await odoo.start_server()
        port = _free_port()
        (root / "gateway" / "routes.json").write_text(_routes(port, odoo.port))
        # the panel still serves this instance (as it does before a hand-over)
        runner, panel_listener = await gateway._serve(NAME, "127.0.0.1", port)
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "from sudoo.cli import main; main(['gateway'])", env=environment
        )
        try:
            control = root / "gateway" / "gateway.sock"
            await _wait_for(control)
            connector = aiohttp.UnixConnector(path=str(control))
            async with (
                aiohttp.ClientSession(connector=connector) as panel,
                panel.get("http://gateway/healthz") as answer,
            ):
                status = await answer.json()
            assert status["listening"] == {NAME: port} and status["errors"] == {}
            assert gateway_health.answers(str(control))  # the container's health check
            panel_listener.close()  # the panel hands the port over
            await runner.cleanup()

            async with aiohttp.ClientSession() as visitor:
                for _ in range(3):
                    url = f"http://127.0.0.1:{port}/odoo/discuss"
                    async with visitor.get(url, headers={"Cookie": "sudoo_session=secret; session_id=x"}) as answer:
                        assert answer.status == 200
                        assert await answer.json() == {"cookie": "session_id=x", "path": "/odoo/discuss"}
                connector = aiohttp.UnixConnector(path=str(control))
                async with (
                    aiohttp.ClientSession(connector=connector) as panel,
                    panel.post("http://gateway/sync") as answer,
                ):
                    assert (await answer.json())["requests"][NAME]["count"] == 3
                deadline = time.monotonic() + 15  # visits are written every few seconds
                while not gateway_service.read_visits(root / "gateway" / "visits.json"):
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.2)

                await odoo.close()  # Odoo paused, no panel to start it
                async with visitor.get(f"http://127.0.0.1:{port}/odoo", headers={"Accept": "text/html"}) as answer:
                    assert answer.status == 503 and "Starting soon" in await answer.text()

            process.send_signal(signal.SIGTERM)
            await asyncio.sleep(1)
            assert not control.exists()  # removed first: a panel takes over at once
            socket.create_connection(("127.0.0.1", port), timeout=1).close()  # still open during the hand-over
            assert await asyncio.wait_for(process.wait(), 15) == 0
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            await odoo.close()

    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_health_check_needs_a_running_service(tmp_path: Path) -> None:
    assert not gateway_health.answers(str(tmp_path / "gateway.sock"))


def test_sockets_move_to_a_short_folder_when_odoo_root_is_long(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Unix socket address holds 107 bytes: under a deep ODOO_ROOT the sockets go to a private folder named after
    GATEWAY_DIR, the same for the panel, the gateway service and the health check."""
    short = Path(tempfile.mkdtemp(prefix="sgw-"))
    monkeypatch.setattr(gateway_health, "SHORT_BASE", str(short))
    try:
        assert gateway_service.socket_path("gateway.sock").parent == settings.gateway_dir  # short enough: unchanged
        root = tmp_path / ("deep-" * 20)
        folder = str(root / "gateway")
        sock = gateway_health.socket_file(folder, "gateway.sock")
        assert len(os.fsencode(os.path.join(folder, "gateway.sock"))) > gateway_health.MAX_SOCKET_PATH
        assert sock == os.path.join(gateway_health.short_dir(folder), "gateway.sock") and sock.startswith(str(short))
        panel = gateway_health.socket_file(folder + "/", "panel.sock")
        assert panel == os.path.join(os.path.dirname(sock), "panel.sock")
        monkeypatch.setenv("ODOO_ROOT", str(root))
        assert gateway_health.socket_path() == sock  # the container's health check finds it too

        gateway_health.prepare_short_dir(folder)
        assert (os.stat(os.path.dirname(sock)).st_mode & 0o777) == 0o700
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(sock)  # "AF_UNIX path too long" with the path in GATEWAY_DIR

        other = str(tmp_path / ("other-" * 20) / "gateway")
        os.symlink(tmp_path, gateway_health.short_dir(other))  # a link put there by someone else is refused
        with pytest.raises(OSError):
            gateway_health.prepare_short_dir(other)
    finally:
        shutil.rmtree(short, ignore_errors=True)


def test_the_client_session_is_closed_when_the_backend_stops(tmp_path: Path) -> None:
    """No "Unclosed client session" from aiohttp when the panel's process ends."""
    script = (
        "import threading, time\n"
        "from sudoo.instances import gateway\n"
        "threading.Thread(target=gateway._run_loop, daemon=True).start()\n"
        "while gateway._SESSION is None: time.sleep(0.01)\n"
        "gateway.close_session()\n"
        "print(gateway._SESSION.closed)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "ODOO_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "True" and "Unclosed client session" not in done.stderr
