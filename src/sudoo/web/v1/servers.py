"""Servers: the main server and the other servers whose agents the panel manages (servers/).

    GET    /api/v1/servers                     every server: status, online, last seen, region, room, instances
    POST   /api/v1/servers                     add one {"name", "region", "capacity": {"max_instances"}}
                                               -> 201 with its join code and the command to run on it (shown once)
    GET    /api/v1/servers/<id>                one server (its fingerprint and facts for servers.manage)
    PATCH  /api/v1/servers/<id>                {"name", "region", "capacity"}; for "main": name and region
    POST   /api/v1/servers/<id>/join-code      a new join code for a server that has not joined yet
    POST   /api/v1/servers/<id>/approve        {"fingerprint": what the server printed} -> the server is active
    POST   /api/v1/servers/<id>/revoke         {"confirm": "<name>"}: its key is refused from now on
    DELETE /api/v1/servers/<id>                remove a revoked server, or one that never joined, from the list;
                                               {"forget_instances": true} also forgets what it reported
    POST   /api/v1/servers/join                (no session: a new server's agent) {"server", "secret",
                                               "public_key", "signature", "facts"} -> 201 {"panel_key", ...}

Seeing the list needs instances.view (choosing where a new instance goes); the fingerprints, the facts of each
server and every change need servers.manage. Adding a server beyond the plan's servers is refused (403) with where
to get more (servers/registry.py).
"""

from __future__ import annotations

import time
from importlib.metadata import version
from typing import Any

from flask import Blueprint, Response, abort, request

from sudoo import accounts, servers
from sudoo.config import live, settings
from sudoo.core import events
from sudoo.servers import inventory, links, placement, registry, trust

from .. import validation as check
from ..security import can, client_ip, log, require
from .responses import iso, no_content, ok

blueprint = Blueprint("servers", __name__)

INSTALLER_URL = "https://github.com/Sudoo-sh/sudoo.sh/releases/{release}/sudoo-install.sh"


def install_command(code: str) -> str:
    """What the admin runs on the new server: the installer of the panel's own version, as the agent."""
    current = version("sudoo")
    plain = current.replace(".", "").isdigit() and current.count(".") == 2
    release = f"download/v{current}" if plain else "latest/download"
    pinned = f" --version {current}" if plain else " --version edge"
    return (
        f"curl -fsSL {INSTALLER_URL.format(release=release)} | sudo bash -s -- agent --join '{code}'{pinned}"
    )  # fmt: skip


def server_resource(record: dict[str, Any], full: bool = False) -> dict[str, Any]:
    server_id = record["id"]
    main = server_id == servers.MAIN
    report = {} if main else inventory.report_of(server_id)
    resource: dict[str, Any] = {
        "id": server_id,
        "name": record.get("name") or server_id,
        "region": record.get("region") or "",
        "main": main,
        "status": record.get("status"),
        "online": links.online(server_id),
        "last_seen_at": None if main else iso(record.get("last_seen")),
        "version": version("sudoo") if main else (record.get("version") or None),
        "instances": inventory.count(server_id),
        "capacity": {"max_instances": (record.get("capacity") or {}).get("max_instances")},
        "room": placement.room(server_id),
        "metrics": placement.figures(server_id, report),
        "created_at": iso(record.get("created_at")),
        "links": {"self": f"/api/v1/servers/{server_id}"},
    }
    if record.get("status") == registry.WAITING:
        resource["code_expires_at"] = iso(record.get("code_expires_at"))
        resource["code_expired"] = float(record.get("code_expires_at") or 0) < time.time()
    if full and can("servers.manage"):
        resource |= {
            "fingerprint": record.get("fingerprint"),
            "facts": record.get("facts") or ({} if not main else _main_facts()),
            "created_by": record.get("created_by"),
            "joined_at": iso(record.get("joined_at")),
            "approved_at": iso(record.get("approved_at")),
            "approved_by": record.get("approved_by"),
            "revoked_at": iso(record.get("revoked_at")),
            "revoked_by": record.get("revoked_by"),
            "connected_since": iso(links.LINKS[server_id].connected_at) if server_id in links.LINKS else None,
        }
    return resource


def _main_facts() -> dict[str, Any]:
    from sudoo.servers.agent import host_facts

    return host_facts()


def _refused(error: registry.Refused) -> None:
    abort(error.status, error.message)


def _record(server_id: str) -> dict[str, Any]:
    if server_id == servers.MAIN:
        return registry.main_record()
    found = registry.get(server_id)
    if found is None:
        abort(404, "This server does not exist.")
    return found


# ---------------------------------------------------------------- reading


@blueprint.get("/servers")
@require("instances.view")
def list_servers() -> Response:
    records = [registry.main_record(), *registry.all_servers()]
    return ok(
        [server_resource(r) for r in records],
        meta={
            "limit": placement.server_limit(),
            "counted": registry.counted(),
            "refusal": registry.limit_refusal(),
            "panel_url": registry.panel_url() or None,
        },
    )


@blueprint.get("/servers/<server_id>")
@require("instances.view")
def get_server(server_id: str) -> Response:
    return ok(server_resource(_record(server_id), full=True))


# ---------------------------------------------------------------- changing


@blueprint.post("/servers")
@require("servers.manage")
def add() -> Response:
    data = check.body()
    try:
        record, code = registry.create(
            check.text(data, "name", max_length=60),
            check.text(data, "region", max_length=60),
            data.get("capacity"),
            _user(),
        )
    except registry.Refused as error:
        log("server not added", ok=False, detail=error.message[:200])
        _refused(error)
    log("server added", detail=f"{record['name']} ({record['id']})")
    events.publish("server", record["id"])
    response = ok(
        server_resource(record, full=True)
        | {"join_code": code, "install_command": install_command(code)},
        status=201,
    )  # fmt: skip
    response.headers["Location"] = f"/api/v1/servers/{record['id']}"
    return response


def _user() -> str:
    from flask import g

    return str(g.user["name"])


@blueprint.patch("/servers/<server_id>")
@require("servers.manage")
def change(server_id: str) -> Response:
    data = check.body()
    if set(data) - {"name", "region", "capacity"}:
        abort(400, "Send only name, region or capacity.")
    if server_id == servers.MAIN:
        if "capacity" in data:
            abort(400, "The main server's room is set by its capacity settings (Settings, Capacity).")
        values: dict[str, Any] = {}
        if "name" in data:
            values["server_name"] = " ".join(check.text(data, "name", max_length=40).split()) or "Main"
        if "region" in data:
            values["server_region"] = " ".join(check.text(data, "region", max_length=40).split())
        try:
            live.change(values)
        except ValueError as error:
            abort(400, str(error))
        log("main server changed", detail=", ".join(sorted(values)))
        events.publish("server", servers.MAIN)
        return ok(server_resource(registry.main_record(), full=True))
    try:
        record = registry.update(server_id, dict(data))
    except registry.Refused as error:
        _refused(error)
    log("server changed", detail=f"{record['name']} ({server_id}): {', '.join(sorted(data))}")
    events.publish("server", server_id)
    links.push_all()
    return ok(server_resource(record, full=True))


@blueprint.post("/servers/<server_id>/join-code")
@require("servers.manage")
def join_code(server_id: str) -> Response:
    try:
        record, code = registry.new_code(server_id, _user())
    except registry.Refused as error:
        _refused(error)
    log("server join code created", detail=f"{record['name']} ({server_id})")
    events.publish("server", server_id)
    return ok(server_resource(record, full=True) | {"join_code": code, "install_command": install_command(code)})


@blueprint.post("/servers/<server_id>/approve")
@require("servers.manage")
def approve(server_id: str) -> Response:
    data = check.body()
    try:
        record = registry.approve(server_id, check.text(data, "fingerprint", max_length=40), _user())
    except registry.Refused as error:
        log("server approval refused", ok=False, detail=f"{server_id}: {error.message[:120]}")
        _refused(error)
    log("server approved", detail=f"{record['name']} ({server_id}), key {record['fingerprint']}")
    events.publish("server", server_id)
    return ok(server_resource(record, full=True))


@blueprint.post("/servers/<server_id>/revoke")
@require("servers.manage")
def revoke(server_id: str) -> Response:
    record = _record(server_id)
    if server_id == servers.MAIN:
        abort(409, "The main server cannot be revoked.")
    if check.text(check.body(), "confirm", max_length=60) != record["name"]:
        abort(400, "Type the server's name exactly to confirm.")
    record = registry.revoke(server_id, _user())
    links.close(server_id, "revoked")
    log("server revoked", detail=f"{record['name']} ({server_id})")
    events.publish("server", server_id)
    return ok(server_resource(record, full=True))


@blueprint.delete("/servers/<server_id>")
@require("servers.manage")
def remove(server_id: str) -> Response:
    record = _record(server_id)
    if server_id == servers.MAIN:
        abort(409, "The main server cannot be removed.")
    data = check.body() if request.get_data() else {}
    forget = check.flag(data, "forget_instances") if data else False
    try:
        registry.remove(server_id, has_instances=bool(inventory.remote_names(server_id)) and not forget)
    except registry.Refused as error:
        _refused(error)
    inventory.forget_server(server_id)
    log("server removed", detail=f"{record['name']} ({server_id})" + (", instances forgotten" if forget else ""))
    events.publish("server", server_id)
    return no_content()


# ---------------------------------------------------------------- moving an instance


@blueprint.post("/instances/<name>/move")
@require("instances.move")
def move_instance(name: str) -> Response:
    """Move an instance to another server (servers/move.py): a job, with downtime while it runs. A production
    instance needs "confirm_production": true."""
    from sudoo.core import jobs
    from sudoo.instances import stages
    from sudoo.servers import move

    from .resources import job_resource

    data = check.body()
    check.instance_name(name)
    if not inventory.exists(name):
        abort(404, "This instance does not exist.")
    target = check.text(data, "server", max_length=20)
    source = inventory.server_of(name)
    if not target or target == source:
        abort(400, "Choose another server than the one the instance runs on.")
    if stages.is_production(inventory.env(name)) and check.flag(data, "confirm_production") is not True:
        abort(
            400,
            f"{name} is a production instance: it is offline while it moves. Confirm " '("confirm_production": true).',
        )
    if jobs.busy(name) or (source != servers.MAIN and (inventory.remote(name) or {}).get("resource", {}).get(
            "current_job")):  # fmt: skip
        abort(409, f"{name} is busy with another job. Try again when it has finished.")
    if move.running_moves() >= settings.move_at_once:
        abort(409, f"{settings.move_at_once} move(s) run already (Moves at the same time). Try again later.")
    if source != servers.MAIN and not links.connected(source):
        abort(503, f"The server {registry.name_of(source)} is offline: the instance cannot be moved now.")
    try:
        placement.choose(target)
    except placement.Refused as error:
        abort(error.status, error.message)
    job = jobs.start(name, f"Move to {registry.name_of(target)}", "move", move.run, name, source, target,
                     user=_user())  # fmt: skip
    log("instance move started", instance=name, detail=f"{registry.name_of(source)} -> {registry.name_of(target)}")
    response = ok(job_resource(job), status=202)
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return response


# ---------------------------------------------------------------- joining (a new server's agent)


@blueprint.post("/servers/join")
def join() -> Response:
    ip = client_ip()
    if accounts.THROTTLE.seconds_to_wait(ip, "server-join"):
        abort(429, "Too many refused join requests from this address. Try again in 15 minutes.")
    data = check.body()
    server_id = check.text(data, "server", max_length=20)
    if not trust.SERVER_ID_RE.match(server_id):
        accounts.THROTTLE.failed(ip, "server-join")
        abort(403, "This join code is not valid: it is wrong, used already or expired. Create a new one.")
    raw = data.get("facts")
    facts: dict[str, Any] = raw if isinstance(raw, dict) else {}
    try:
        record = registry.join(
            server_id,
            check.text(data, "secret", max_length=100),
            check.text(data, "public_key", max_length=100),
            check.text(data, "signature", max_length=200),
            facts,
        )
    except registry.Refused as error:
        accounts.THROTTLE.failed(ip, "server-join")
        from sudoo.core import audit

        audit.record("server", ip, "server join refused", ok=False, detail=f"{server_id}: {error.message[:150]}")
        _refused(error)
    from sudoo.core import audit

    audit.record(
        "server", ip, "server joined, waiting for approval",
        detail=f"{record['name']} ({server_id}), key {record['fingerprint']}",
    )  # fmt: skip
    events.publish("server", server_id)
    return ok(
        {
            "server": server_id,
            "name": record["name"],
            "fingerprint": record["fingerprint"],
            "panel_key": registry.panel_public(),
            "panel_fingerprint": trust.fingerprint(registry.panel_public()),
        },
        status=201,
    )
