"""The web shell (webshell/, web/v1/shell.py, web/v1/shell_socket.py): permissions and their settings, production
and root rules, one-time tickets bound to a session and an instance, the WebSocket's Origin check, limits, idle and
maximum length, audit entries, recordings, and ending every process when the socket closes. Docker never runs: the
Engine API is replaced by a fake whose process echoes what is typed (test_shell_docker.py uses a real container)."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from sudoo import accounts, roles
from sudoo.config import live, settings
from sudoo.core import audit
from sudoo.instances import monitor, odoo, store
from sudoo.storage import storage
from sudoo.webshell import docker_exec, policy, recordings, sessions, tickets, welcome

Client = tuple[FlaskClient, dict[str, str]]
DEV, PROD = "shellbox", "shellprod"
PROTOCOL = "sudoo.shell.v1"


def _instance(name: str, stage: str) -> None:
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": f"{name}_db", "GIT_BRANCH": "main", "STAGE": stage},
    )


@pytest.fixture(autouse=True)
def instances() -> Iterator[None]:
    _instance(DEV, "development")
    _instance(PROD, "production")
    yield
    for name in (DEV, PROD):
        shutil.rmtree(store.folder(name), ignore_errors=True)
        monitor.STATUS.pop(name, None)
    live.change(dict.fromkeys(live.overrides()))
    tickets.clear()
    sessions.OPEN.clear()
    for key in list(storage().all(recordings.COLLECTION)):
        storage().delete(recordings.COLLECTION, key)
    shutil.rmtree(recordings.folder(), ignore_errors=True)


@pytest.fixture(scope="module")
def plain_admin(app: Flask) -> Client:
    accounts.create("shelladmin", "admin", "harbor-quartz-melon-58")
    return sign_in(app, "shelladmin", "harbor-quartz-melon-58")


@pytest.fixture(scope="module")
def viewer(app: Flask) -> Client:
    accounts.create("shellviewer", "viewer", "tundra-velvet-crane-63")
    return sign_in(app, "shellviewer", "tundra-velvet-crane-63")


def _ticket(client: Client, name: str = DEV, **body: Any) -> Any:
    flask_client, headers = client
    return flask_client.post(f"/api/v1/instances/{name}/shell/tickets", json=body, headers=headers)


# ---------------------------------------------------------------- permissions


def test_who_may_open_shells_by_default() -> None:
    assert {"instances.shell", "instances.database_shell", "shells.supervise"} <= roles.permissions("super_admin")
    assert {"instances.shell", "instances.database_shell"} <= roles.permissions("admin")
    assert "shells.supervise" not in roles.permissions("admin")
    for role in ("maintainer", "developer", "viewer"):
        assert not roles.SHELL_PERMISSIONS & roles.permissions(role)
    assert not roles.SHELL_PERMISSIONS & roles.permissions(roles.CONNECTED_APP)


def test_the_admin_opens_shells_to_maintainers_or_developers_never_to_viewers() -> None:
    live.change({"shell_roles": "maintainers"})
    assert "instances.shell" in roles.permissions("maintainer")
    assert "instances.shell" not in roles.permissions("developer")
    live.change({"shell_roles": "developers"})
    assert "instances.shell" in roles.permissions("maintainer") and "instances.shell" in roles.permissions("developer")
    assert "instances.database_shell" not in roles.permissions("developer")  # psql stays with the administrators
    assert not roles.SHELL_PERMISSIONS & roles.permissions("viewer")


def test_switching_shells_off_takes_them_from_everyone(admin: Client) -> None:
    live.change({"shell_enabled": False})
    assert not roles.SHELL_PERMISSIONS & roles.permissions("super_admin")
    refused = _ticket(admin)
    assert refused.status_code == 403 and "switched off" in refused.get_json()["detail"]


def test_tickets_follow_the_permissions(admin: Client, plain_admin: Client, operator: Client, viewer: Client) -> None:
    assert _ticket(admin).status_code == 201
    assert _ticket(plain_admin, target="psql").status_code == 201
    for client in (operator, viewer):
        refused = _ticket(client)
        assert refused.status_code == 403 and "Your role cannot" in refused.get_json()["detail"]
    live.change({"shell_roles": "developers"})
    assert _ticket(operator).status_code == 201
    assert _ticket(operator, target="psql").status_code == 403
    assert _ticket(viewer).status_code == 403
    assert _ticket(admin, target="bash").status_code == 400
    assert _ticket(admin, name="nothing-here").status_code == 404


def test_a_ticket_needs_the_csrf_token_and_the_panels_origin(admin: Client) -> None:
    client, headers = admin
    path = f"/api/v1/instances/{DEV}/shell/tickets"
    assert client.post(path, json={}, headers={"Origin": headers["Origin"]}).status_code == 403
    assert client.post(path, json={}, headers={**headers, "Origin": "https://evil.example"}).status_code == 403


def test_api_tokens_never_get_a_ticket() -> None:
    user = {"name": "api:robot", "role": "admin", "token_id": "t1"}
    with pytest.raises(policy.Refused) as refused:
        policy.plan(user, DEV, "shell")
    assert refused.value.code == "token"


def test_production_needs_a_confirmation_each_time_and_super_admins_by_default(
    admin: Client, plain_admin: Client
) -> None:
    unconfirmed = _ticket(admin, name=PROD)
    assert unconfirmed.status_code == 400 and "confirm" in unconfirmed.get_json()["detail"]
    confirmed = _ticket(admin, name=PROD, confirm_production=True)
    assert confirmed.status_code == 201 and confirmed.get_json()["data"]["production"] is True
    assert _ticket(plain_admin, name=PROD, confirm_production=True).status_code == 403
    live.change({"shell_production": "allowed"})
    assert _ticket(plain_admin, name=PROD, confirm_production=True).status_code == 201
    assert _ticket(plain_admin, name=PROD).status_code == 400  # still confirmed each time
    live.change({"shell_production": "off"})
    assert _ticket(admin, name=PROD, confirm_production=True).status_code == 403
    assert _ticket(admin).status_code == 201  # other instances are not concerned


def test_root_only_when_switched_on_for_super_admins_and_the_odoo_container(admin: Client, plain_admin: Client) -> None:
    off = _ticket(admin, as_root=True)
    assert off.status_code == 403 and "Root shells are switched off" in off.get_json()["detail"]
    live.change({"shell_root": True})
    root = _ticket(admin, as_root=True)
    assert root.status_code == 201
    assert root.get_json()["data"]["container_user"] == "root" and root.get_json()["data"]["recorded"] is True
    assert _ticket(plain_admin, as_root=True).status_code == 403
    assert _ticket(admin, as_root=True, target="psql").status_code == 400
    assert _ticket(admin).get_json()["data"]["container_user"] == "odoo"


def test_what_each_target_runs() -> None:
    user = {"name": "admin1", "role": "super_admin"}
    shell = policy.plan(user, DEV, "shell")
    assert shell.container == f"odoo-{DEV}-odoo-1" and shell.container_user == "odoo" and not shell.root
    assert shell.argv[:3] == ("sh", "-c", policy.NICE) and shell.argv[-1] == welcome.START
    odoo_shell = policy.plan(user, DEV, "odoo-shell")
    assert odoo_shell.argv[4:] == ("odoo", "shell", "-c", "/etc/odoo/odoo.conf", "-d", f"{DEV}_db", "--no-http")
    psql = policy.plan(user, DEV, "psql")
    assert psql.container == f"odoo-{DEV}-db-1" and psql.container_user == "postgres"
    assert psql.argv[4:] == ("psql", "-U", "odoo", "-d", f"{DEV}_db")


def test_the_options_say_what_may_be_opened_and_why_not(admin: Client, operator: Client) -> None:
    client, _ = admin
    found = client.get(f"/api/v1/instances/{PROD}/shell").get_json()["data"]
    assert found["production"] and found["targets"]["shell"]["allowed"] and found["targets"]["shell"]["recorded"]
    assert found["root"] == {
        "allowed": False,
        "code": "root-off",
        "reason": "Root shells are switched off on this panel.",
    }
    mine = operator[0].get(f"/api/v1/instances/{DEV}/shell").get_json()["data"]
    assert not mine["targets"]["shell"]["allowed"] and mine["targets"]["shell"]["code"] == "permission"


def test_no_ticket_for_a_sleeping_instance(admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor, "status", lambda name: "paused")
    refused = _ticket(admin)
    assert refused.status_code == 409 and "not running" in refused.get_json()["detail"]


# ---------------------------------------------------------------- tickets


def _issue(**changes: Any) -> str:
    values: dict[str, Any] = {"user": "admin1", "sid": "s1", "instance": DEV, "target": "shell"}
    values.update(as_root=False, confirmed=False, ip="1.2.3.4")
    values.update(changes)
    token, seconds = tickets.issue(**values)
    assert seconds == settings.shell_ticket_seconds
    return token


def test_a_ticket_works_once() -> None:
    token = _issue()
    found = tickets.take(token)
    assert found is not None and (found.user, found.sid, found.instance) == ("admin1", "s1", DEV)
    assert tickets.take(token) is None
    assert tickets.take("") is None and tickets.take("x" * 500) is None


def test_a_ticket_expires_within_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _issue()
    later = time.time() + settings.shell_ticket_seconds + 1
    monkeypatch.setattr(tickets.time, "time", lambda: later)
    assert tickets.take(token) is None


def test_only_its_hash_is_kept_and_waiting_tickets_are_limited() -> None:
    token = _issue()
    assert token not in tickets._TICKETS and len(next(iter(tickets._TICKETS))) == 64
    for _ in range(tickets.MAX_WAITING_PER_USER - 1):
        _issue()
    with pytest.raises(OverflowError):
        _issue()
    assert tickets.waiting(user="admin1") == tickets.MAX_WAITING_PER_USER
    _issue(user="someone")  # others are not concerned


# ---------------------------------------------------------------- containers


def _container(name: str = DEV, service: str = "odoo", **changes: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "Name": f"/odoo-{name}-{service}-1",
        "Config": {"Labels": {odoo.LABEL_MANAGED: "true", odoo.LABEL_INSTANCE: name, odoo.LABEL_SERVICE: service}},
        "State": {"Running": True},
        "HostConfig": {"Privileged": False, "PidMode": "", "NetworkMode": "odoo-x_default"},
        "Mounts": [{"Source": "/var/lib/docker/volumes/x/_data"}],
    }
    found.update(changes)
    return found


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"Name": "/sudoo-panel"}, "not-ours"),
        ({"Config": {"Labels": {}}}, "not-ours"),
        ({"State": {"Running": False}}, "not-running"),
        ({"HostConfig": {"Privileged": True}}, "exposed"),
        ({"HostConfig": {"PidMode": "host"}}, "exposed"),
        ({"HostConfig": {"NetworkMode": "host"}}, "exposed"),
        ({"Mounts": [{"Source": "/var/run/docker.sock"}]}, "exposed"),
    ],
)
def test_a_shell_reaches_only_the_instances_own_container(changes: dict[str, Any], code: str) -> None:
    plan = policy.plan({"name": "admin1", "role": "super_admin"}, DEV, "shell")
    policy.check_container(_container(), plan)
    with pytest.raises(policy.Refused) as refused:
        policy.check_container(_container(**changes), plan)
    assert refused.value.code == code
    psql = policy.plan({"name": "admin1", "role": "super_admin"}, DEV, "psql")
    with pytest.raises(policy.Refused):
        policy.check_container(_container(), psql)  # the Odoo container is not the database's


# ---------------------------------------------------------------- the WebSocket (fake Docker)


class FakeStream:
    """A process that echoes what is typed."""

    def __init__(self) -> None:
        self.output: asyncio.Queue[bytes] = asyncio.Queue()
        self.typed: list[bytes] = []
        self.closed = False

    async def read(self) -> bytes:
        return await self.output.get()

    async def write(self, data: bytes) -> None:
        self.typed.append(data)
        await self.output.put(b"echo:" + data)

    def close(self) -> None:
        self.closed = True


@dataclass
class Engine:
    container: dict[str, dict[str, Any]] = field(default_factory=dict)
    created: list[dict[str, Any]] = field(default_factory=list)
    resized: list[tuple[int, int]] = field(default_factory=list)
    hung_up: list[tuple[str, str, str]] = field(default_factory=list)
    streams: list[FakeStream] = field(default_factory=list)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    fake = Engine()

    async def inspect_container(name: str) -> dict[str, Any]:
        if name in fake.container:
            return fake.container[name]
        _, instance, service, _ = name.split("-")
        return _container(instance, service)

    async def create(container: str, argv: list[str], **kwargs: Any) -> str:
        fake.created.append({"container": container, "argv": argv, **kwargs})
        return f"exec{len(fake.created)}"

    async def start(exec_id: str) -> FakeStream:
        stream = FakeStream()
        fake.streams.append(stream)
        return stream

    async def resize(exec_id: str, cols: int, rows: int) -> None:
        fake.resized.append((cols, rows))

    async def hang_up(container: str, session_id: str, user: str) -> None:
        fake.hung_up.append((container, session_id, user))

    for name, fn in [("inspect_container", inspect_container), ("create", create), ("start", start),
                     ("resize", resize), ("hang_up", hang_up)]:  # fmt: skip
        monkeypatch.setattr(docker_exec, name, fn)
    return fake


def _cookie(client: Client) -> str:
    cookie = client[0].get_cookie("sudoo_session")
    assert cookie is not None
    return f"sudoo_session={cookie.value}"


async def _connect(
    server: TestClient, client: Client, token: str, name: str = DEV, origin: str | None = None, **extra: Any
) -> aiohttp.ClientWebSocketResponse:
    headers = {"Cookie": _cookie(client), "Origin": origin or f"http://{server.host}:{server.port}"}
    protocols = extra.pop("protocols", (PROTOCOL, f"ticket.{token}"))
    return await server.ws_connect(
        f"/api/v1/instances/{name}/shell/socket?cols=100&rows=30", headers=headers, protocols=protocols, **extra
    )


async def _status_of(server: TestClient, client: Client, token: str, **extra: Any) -> int:
    try:
        ws = await _connect(server, client, token, **extra)
    except aiohttp.WSServerHandshakeError as error:
        return error.status
    await ws.close()
    return 101


async def _json(ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
    while True:
        message = await asyncio.wait_for(ws.receive(), 5)
        if message.type == aiohttp.WSMsgType.TEXT:
            found: dict[str, Any] = json.loads(message.data)
            return found
        if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
            raise AssertionError("closed before a text message")


async def _bytes(ws: aiohttp.ClientWebSocketResponse) -> bytes:
    message = await asyncio.wait_for(ws.receive(), 5)
    assert message.type == aiohttp.WSMsgType.BINARY, message
    data: bytes = message.data
    return data


def _run(app: Flask, scenario: Any) -> Any:
    from sudoo.web.v1.events import make_app

    async def main() -> Any:
        async with TestClient(TestServer(make_app(app))) as server:
            return await scenario(server)

    return asyncio.run(main())


async def _until(condition: Any, seconds: float = 5) -> None:
    for _ in range(int(seconds * 20)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


def _events(name: str = DEV) -> list[dict[str, Any]]:
    return [e for e in audit.latest(50, instance=name) if e["event"].startswith("shell")]


def test_a_shell_session_from_ticket_to_hang_up(app: Flask, admin: Client, engine: Engine) -> None:
    token = _ticket(admin).get_json()["data"]["ticket"]

    async def scenario(server: TestClient) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
        ws = await _connect(server, admin, token)
        assert ws.protocol == PROTOCOL
        ready = await _json(ws)
        await ws.send_bytes(b"ls\r")
        echoed = await _bytes(ws)
        await ws.send_str(json.dumps({"type": "resize", "cols": 120, "rows": 40}))
        await ws.send_str(json.dumps({"type": "resize", "cols": 99999, "rows": 40}))  # ignored
        await _until(lambda: engine.resized == [(120, 40)])
        await ws.close()
        await _until(lambda: not sessions.OPEN and len(engine.hung_up) == 1)
        return ready, echoed, engine.created[0]

    ready, echoed, created = _run(app, scenario)
    assert ready["type"] == "ready" and ready["instance"] == DEV and ready["container_user"] == "odoo"
    assert ready["user"] == "admin1" and ready["recorded"] is False
    assert echoed == b"echo:ls\r"
    assert created["container"] == f"odoo-{DEV}-odoo-1" and created["user"] == "odoo"
    assert (created["cols"], created["rows"]) == (100, 30) and created["workdir"] == odoo.CUSTOM_ADDONS_MOUNT
    assert created["env"]["SUDOO_SHELL"] == ready["session"] and created["env"]["TERM"] == "xterm-256color"
    assert created["argv"][-3:] == ["sh", "-c", welcome.START] and created["env"]["SUDOO_RC"] == welcome.rc()
    assert created["env"]["SUDOO_STAGE"] == ready["stage"] == "development" and created["env"]["HOSTNAME"] == DEV
    assert engine.hung_up == [(f"odoo-{DEV}-odoo-1", ready["session"], "odoo")] and engine.streams[0].closed
    closed, opened = _events()[:2]
    assert opened["event"] == "shell opened" and opened["user"] == "admin1" and ready["session"] in opened["detail"]
    assert closed["event"] == "shell closed" and "closed" in closed["detail"] and "min" in closed["detail"]
    document = recordings.get(ready["session"])
    assert document is not None and document["ended"] and document["reason"] == "closed" and document["duration"] >= 0


def test_the_socket_checks_origin_ticket_and_session(app: Flask, admin: Client, engine: Engine) -> None:
    async def scenario(server: TestClient) -> list[int]:
        found = []
        token = _ticket(admin).get_json()["data"]["ticket"]
        found.append(await _status_of(server, admin, token, origin="https://evil.example"))
        found.append(await _status_of(server, admin, token, protocols=(PROTOCOL,)))
        found.append(await _status_of(server, admin, "made-up"))
        found.append(await _status_of(server, admin, token))  # the evil-origin attempt did not use it
        found.append(await _status_of(server, admin, token))  # used
        return found

    statuses = _run(app, scenario)
    assert statuses == [403, 400, 401, 101, 401]
    assert not sessions.OPEN


def test_a_ticket_is_bound_to_its_session_and_instance(
    app: Flask, admin: Client, passwords: dict[str, str], engine: Engine
) -> None:
    other_session = sign_in(app, "admin1", passwords["admin"])  # the same person, another session (sid)

    async def scenario(server: TestClient) -> list[int]:
        return [
            await _status_of(server, other_session, _ticket(admin).get_json()["data"]["ticket"]),
            await _status_of(server, admin, _ticket(admin).get_json()["data"]["ticket"], name=PROD),
        ]

    assert _run(app, scenario) == [403, 403]
    assert any(e["event"] == "shell refused" and not e["ok"] for e in audit.latest(20))


def test_a_ticket_expires_before_the_socket_opens(
    app: Flask, admin: Client, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _ticket(admin).get_json()["data"]["ticket"]
    later = time.time() + settings.shell_ticket_seconds + 1
    monkeypatch.setattr(tickets.time, "time", lambda: later)

    async def scenario(server: TestClient) -> int:
        return await _status_of(server, admin, token)

    assert _run(app, scenario) == 401


def test_rules_are_checked_again_when_the_socket_opens(app: Flask, admin: Client, engine: Engine) -> None:
    token = _ticket(admin).get_json()["data"]["ticket"]
    live.change({"shell_enabled": False})

    async def scenario(server: TestClient) -> int:
        return await _status_of(server, admin, token)

    assert _run(app, scenario) == 403


def test_no_shell_in_a_container_that_is_not_the_instances(app: Flask, admin: Client, engine: Engine) -> None:
    engine.container[f"odoo-{DEV}-odoo-1"] = _container(HostConfig={"Privileged": True})
    token = _ticket(admin).get_json()["data"]["ticket"]

    async def scenario(server: TestClient) -> int:
        return await _status_of(server, admin, token)

    assert _run(app, scenario) == 409
    assert engine.created == [] and not sessions.OPEN


def test_shells_per_person_and_per_instance_are_limited(
    app: Flask, admin: Client, plain_admin: Client, engine: Engine
) -> None:
    live.change({"shell_max_per_user": 1, "shell_max_per_instance": 1})

    async def scenario(server: TestClient) -> tuple[int, int, int, int]:
        first = _ticket(admin).get_json()["data"]["ticket"]
        second = _ticket(admin)  # the first ticket is waiting: it counts
        ws = await _connect(server, admin, first)
        await _json(ws)
        other = _ticket(plain_admin)  # the instance is full
        live.change({"shell_max_per_user": 2, "shell_max_per_instance": 2})
        race = _ticket(admin).get_json()["data"]["ticket"]
        live.change({"shell_max_per_user": 1, "shell_max_per_instance": 1})
        late = await _status_of(server, admin, race)  # checked again when the socket opens
        await ws.close()
        return second.status_code, other.status_code, late, len(engine.created)

    assert _run(app, scenario) == (429, 429, 429, 1)


async def _closed_reason(ws: aiohttp.ClientWebSocketResponse, seconds: float = 8) -> tuple[list[str], str]:
    notices = []
    while True:
        found = await asyncio.wait_for(_json(ws), seconds)
        if found["type"] == "notice":
            notices.append(found["code"])
        if found["type"] == "closed":
            return notices, str(found["reason"])


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Settings the tests can make shorter than the panel allows (the watch reads them every second)."""
    quick = types.SimpleNamespace(
        shell_idle_minutes=60.0, shell_max_minutes=60.0, shell_recording_max_mb=5.0, shell_max_per_user=5,
        shell_max_per_instance=5,
    )  # fmt: skip
    monkeypatch.setattr(sessions, "settings", quick)
    monkeypatch.setattr(sessions, "WARN_SECONDS", 1)
    return quick


def test_an_idle_shell_closes_after_a_notice(app: Flask, admin: Client, engine: Engine, fast: Any) -> None:
    fast.shell_idle_minutes = 2.5 / 60
    token = _ticket(admin).get_json()["data"]["ticket"]

    async def scenario(server: TestClient) -> tuple[list[str], str]:
        ws = await _connect(server, admin, token)
        await _json(ws)
        return await _closed_reason(ws)

    notices, reason = _run(app, scenario)
    assert notices == ["idle"] and reason == "idle"
    assert len(engine.hung_up) == 1 and not sessions.OPEN


def test_a_shell_closes_at_its_maximum_length(app: Flask, admin: Client, engine: Engine, fast: Any) -> None:
    fast.shell_max_minutes = 2.5 / 60
    token = _ticket(admin).get_json()["data"]["ticket"]

    async def scenario(server: TestClient) -> tuple[list[str], str]:
        ws = await _connect(server, admin, token)
        await _json(ws)
        for _ in range(3):  # typing does not make it longer
            await ws.send_bytes(b"x")
            await asyncio.sleep(0.5)
        return await _closed_reason(ws)

    notices, reason = _run(app, scenario)
    assert "ending" in notices and reason == "max-length"


def test_a_shell_closes_when_its_panel_session_signs_out(
    app: Flask, passwords: dict[str, str], engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "RECHECK_SECONDS", 0)
    mine = sign_in(app, "admin1", passwords["admin"])
    token = _ticket(mine).get_json()["data"]["ticket"]

    async def scenario(server: TestClient) -> tuple[list[str], str]:
        ws = await _connect(server, mine, token)
        await _json(ws)
        mine[0].delete("/api/v1/session", headers=mine[1])
        return await _closed_reason(ws)

    assert _run(app, scenario)[1] == "not-allowed"
    assert engine.hung_up


def test_the_process_ending_closes_the_shell(app: Flask, admin: Client, engine: Engine) -> None:
    token = _ticket(admin).get_json()["data"]["ticket"]

    async def scenario(server: TestClient) -> str:
        ws = await _connect(server, admin, token)
        await _json(ws)
        engine.streams[0].output.put_nowait(b"")  # exit
        return (await _closed_reason(ws))[1]

    assert _run(app, scenario) == "exit"
    assert len(engine.hung_up) == 1  # background jobs of an exited shell end too


def test_a_super_admin_sees_and_ends_open_shells(
    app: Flask, admin: Client, plain_admin: Client, engine: Engine
) -> None:
    token = _ticket(plain_admin).get_json()["data"]["ticket"]

    async def scenario(server: TestClient) -> tuple[list[dict[str, Any]], int, int, str]:
        ws = await _connect(server, plain_admin, token)
        ready = await _json(ws)
        listed = admin[0].get("/api/v1/shells").get_json()["data"]
        forbidden = plain_admin[0].get("/api/v1/shells").status_code
        ended = admin[0].delete(f"/api/v1/shells/{ready['session']}", headers=admin[1]).status_code
        reason = (await _closed_reason(ws))[1]
        await _until(lambda: (recordings.get(ready["session"]) or {}).get("ended"))
        return listed, forbidden, ended, reason

    listed, forbidden, ended, reason = _run(app, scenario)
    assert [s["user"] for s in listed if s["open"]] == ["shelladmin"] and forbidden == 403
    assert ended == 204 and reason == "ended"
    assert admin[0].delete("/api/v1/shells/nothing", headers=admin[1]).status_code == 404
    past = admin[0].get(f"/api/v1/shells?instance={DEV}").get_json()["data"]
    assert past[0]["open"] is False and past[0]["reason"] == "ended" and past[0]["recording"] is False


# ---------------------------------------------------------------- recordings


def test_production_shells_are_recorded_encrypted_and_played_by_super_admins_only(
    app: Flask, admin: Client, plain_admin: Client, engine: Engine
) -> None:
    token = _ticket(admin, name=PROD, confirm_production=True).get_json()["data"]["ticket"]

    async def scenario(server: TestClient) -> str:
        ws = await _connect(server, admin, token, name=PROD)
        ready = await _json(ws)
        assert ready["recorded"] is True and ready["production"] is True
        await ws.send_bytes("secret-pässword\r".encode())
        await _bytes(ws)
        await ws.send_str(json.dumps({"type": "resize", "cols": 90, "rows": 20}))
        await _until(lambda: engine.resized)
        await ws.close()
        await _until(lambda: not sessions.OPEN)
        return str(ready["session"])

    session_id = _run(app, scenario)
    raw = (recordings.folder() / f"{session_id}.cast").read_text()
    assert "secret" not in raw and raw.startswith("enc:v1:")
    assert oct((recordings.folder() / f"{session_id}.cast").stat().st_mode & 0o777) == "0o600"
    assert plain_admin[0].get(f"/api/v1/shells/{session_id}/recording").status_code == 403
    played = admin[0].get(f"/api/v1/shells/{session_id}/recording").get_json()["data"]
    assert played["header"]["version"] == 2 and played["header"]["sudoo"]["instance"] == PROD
    kinds = {kind: text for _, kind, text in played["events"] if kind in ("i", "o", "r")}
    assert kinds == {"i": "secret-pässword\r", "o": "echo:secret-pässword\r", "r": "90x20"}
    assert played["session"]["recording"] is True
    assert audit.latest(1)[0]["event"] == "shell recording played"


def test_other_shells_are_recorded_only_when_asked(admin: Client) -> None:
    assert _ticket(admin).get_json()["data"]["recorded"] is False
    live.change({"shell_recording": "all"})
    assert _ticket(admin).get_json()["data"]["recorded"] is True
    tickets.clear()  # waiting tickets count toward the open shells
    live.change({"shell_recording": "off"})
    assert _ticket(admin, name=PROD, confirm_production=True).get_json()["data"]["recorded"] is False


def test_a_full_recording_keeps_input_then_closes_the_shell(tmp_path: Any) -> None:
    recorder = recordings.Recorder("abc123full", {"version": 2}, limit_bytes=2048)
    recorder.output(b"o" * 1500)
    recorder.output(b"o" * 1500)  # beyond: no longer recorded
    assert recorder.output_full
    assert recorder.input(b"i" * 1000)
    assert not recorder.input(b"i" * 2000)  # input alone beyond twice the size: the shell must close
    recorder.close()
    _, events = recordings.read("abc123full")
    assert [kind for _, kind, _ in events] == ["o", "m", "i"]


def test_old_recordings_are_deleted_and_their_total_size_is_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    for n, started in enumerate((now - 40 * 86400, now - 86400, now - 60)):
        session_id = f"rec{n}"
        recordings.save({"id": session_id, "started": started, "ended": started + 5, "recorded": True})
        recorder = recordings.Recorder(session_id, {"version": 2}, limit_bytes=10 * 1024 * 1024)
        recorder.output(b"x" * 300_000)
        recorder.close()
    recordings.prune(now)
    assert recordings.get("rec0") is None and not recordings.exists("rec0")  # older than 30 days
    assert recordings.exists("rec1") and recordings.exists("rec2")
    live.change({"shell_recordings_total_mb": 10})
    monkeypatch.setattr(recordings, "MB", 100_000)  # 10 "MB" = 1,000,000 bytes: three recordings of 400 KB are too many
    recorder = recordings.Recorder("rec3", {"version": 2}, limit_bytes=10 * 1024 * 1024)
    recorder.output(b"x" * 300_000)
    recorder.close()
    recordings.prune(now)
    assert not recordings.exists("rec1")  # the oldest went first
    removed = recordings.get("rec1")
    assert removed is not None and removed["recording_removed"]
