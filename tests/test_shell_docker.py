"""The web shell against a real, disposable Docker container: the Engine API calls (exec, terminal size), the whole
path from ticket to WebSocket, and that closing the socket ends every process of the shell, background and nohup
ones included.

Skipped unless SUDOO_DOCKER_TESTS=1 (it needs a Docker daemon). The container is hardened like an instance's
(read-only, every capability dropped, no new privileges, an init process) and removed afterwards; the image is
SUDOO_DOCKER_TEST_IMAGE (default alpine:3.22; odoo:19.0 works too).

    SUDOO_DOCKER_TESTS=1 uv run pytest tests/test_shell_docker.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import Iterator

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer
from flask import Flask
from flask.testing import FlaskClient

from sudoo.config import live
from sudoo.instances import odoo, store
from sudoo.webshell import docker_exec, policy, sessions

pytestmark = pytest.mark.skipif(
    os.environ.get("SUDOO_DOCKER_TESTS") != "1", reason="needs Docker (SUDOO_DOCKER_TESTS=1)"
)

Client = tuple[FlaskClient, dict[str, str]]
NAME = "shelldock"
CONTAINER = f"odoo-{NAME}-odoo-1"
IMAGE = os.environ.get("SUDOO_DOCKER_TEST_IMAGE", "alpine:3.22")


def _processes() -> str:
    every = "ps -eo pid,user,args 2>/dev/null || ps -o pid,user,args"  # procps needs -e, BusyBox lists all anyway
    return subprocess.run(["docker", "exec", CONTAINER, "sh", "-c", every], capture_output=True, text=True).stdout


@pytest.fixture(scope="module", autouse=True)
def container() -> Iterator[None]:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    labels = {odoo.LABEL_MANAGED: "true", odoo.LABEL_INSTANCE: NAME, odoo.LABEL_SERVICE: "odoo"}
    subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER, "--read-only", "--cap-drop", "ALL", "--init",
         "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp", "--pids-limit", "128", "--memory", "128m",
         *(arg for key, value in labels.items() for arg in ("--label", f"{key}={value}")),
         "--user", "1000:1000", "--entrypoint", "sleep", IMAGE, "infinity"],
        check=True, capture_output=True,
    )  # fmt: skip
    folder = store.folder(NAME)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(NAME, {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": "shelldock", "GIT_BRANCH": ""})
    yield
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    shutil.rmtree(folder, ignore_errors=True)
    live.change(dict.fromkeys(live.overrides()))


def test_the_engine_api_runs_resizes_and_hangs_up() -> None:
    async def scenario() -> tuple[bytes, str, str]:
        exec_id = await docker_exec.create(
            CONTAINER, ["sh"], user="1000", env={"SUDOO_SHELL": "dock1", "TERM": "xterm"}, workdir="/tmp", cols=90,
            rows=20,
        )  # fmt: skip
        stream = await docker_exec.start(exec_id)
        await docker_exec.resize(exec_id, 101, 33)
        await stream.write(b"stty size; pwd; sleep 300 & nohup sleep 301 >/dev/null 2>&1 &\n")
        output = b""
        while b"/tmp" not in output or b"33 101" not in output:
            output += await asyncio.wait_for(stream.read(), 5)
        stream.close()
        before = _processes()
        await docker_exec.hang_up(CONTAINER, "dock1", "1000")
        return output, before, _processes()

    output, before, after = asyncio.run(scenario())
    assert b"33 101" in output and b"/tmp" in output
    assert "sleep 300" in before and "sleep 301" in before  # closing the connection alone leaves them running
    assert "sleep 300" not in after and "sleep 301" not in after and "sleep infinity" in after


def test_from_ticket_to_closed_socket_with_a_real_container(
    app: Flask, admin: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo.web.v1.events import make_app

    # the test image has no "odoo" user and no add-ons folder
    monkeypatch.setitem(policy.CONTAINER_USER, "odoo", "1000")
    monkeypatch.setattr(odoo, "CUSTOM_ADDONS_MOUNT", "/tmp")

    live.change({"shell_root": False})
    client, headers = admin
    answer = client.post(f"/api/v1/instances/{NAME}/shell/tickets", json={"target": "shell"}, headers=headers)
    assert answer.status_code == 201, answer.get_json()
    ticket = answer.get_json()["data"]["ticket"]
    cookie = client.get_cookie("sudoo_session")
    assert cookie is not None

    async def scenario() -> tuple[str, str]:
        async with TestClient(TestServer(make_app(app))) as server:
            ws = await server.ws_connect(
                f"/api/v1/instances/{NAME}/shell/socket?cols=100&rows=30",
                headers={"Cookie": f"sudoo_session={cookie.value}", "Origin": f"http://{server.host}:{server.port}"},
                protocols=("sudoo.shell.v1", f"ticket.{ticket}"),
            )
            ready = await ws.receive_json(timeout=10)
            assert ready["type"] == "ready"
            await ws.send_bytes(b"echo marker-$((6*7)); sleep 302 &\n")
            seen = b""
            while b"marker-42" not in seen:
                message = await ws.receive(timeout=10)
                assert message.type == aiohttp.WSMsgType.BINARY, message
                seen += message.data
            before = _processes()
            await ws.close()
            for _ in range(100):
                if not sessions.OPEN:
                    break
                await asyncio.sleep(0.1)
            return before, _processes()

    before, after = asyncio.run(scenario())
    assert "sleep 302" in before and "sleep 302" not in after
