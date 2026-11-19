"""Where a new instance goes, and how much room each server has.

A new instance goes to the server the person chose, else its project's default server (a project's
"default_server"), else the main server. The server must be active and connected, and have room: its own limit
(capacity.max_instances, set per server) and, as a warning, the panel's estimate of how many instances it runs
comfortably (instances/capacity.py, measured on that server by its agent).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sudoo import entitlements, servers

from . import inventory, links, registry


class Refused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def server_limit() -> int | None:
    """How many servers the plan allows (the main server included); None: no limit."""
    return entitlements.current().limit("servers")


def _estimate(server_id: str) -> dict[str, Any]:
    if server_id == servers.MAIN:
        from sudoo.instances import capacity

        try:
            return capacity.current() or {}
        except Exception:  # noqa: BLE001 - not measured yet
            return {}
    found = inventory.report_of(server_id).get("capacity")
    return found if isinstance(found, dict) else {}


def room(server_id: str) -> dict[str, Any]:
    """{"instances", "max_instances", "comfortable", "running", "headroom", "full", "warning"} of a server."""
    record = registry.main_record() if server_id == servers.MAIN else (registry.get(server_id) or {})
    limit = (record.get("capacity") or {}).get("max_instances")
    count = inventory.count(server_id)
    estimate = _estimate(server_id)
    return {
        "instances": count,
        "max_instances": limit,
        "comfortable": estimate.get("comfortable"),
        "running": estimate.get("running"),
        "headroom": estimate.get("headroom"),
        "full": bool(limit is not None and count >= int(limit)),
        "warning": estimate.get("alert") or estimate.get("message"),
    }


def figures(server_id: str, report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The latest CPU, memory and disk figures of a server (the main one's own, the others' last report)."""
    if server_id == servers.MAIN:
        from sudoo.instances import metrics

        server = dict(metrics.SERVER)
    else:
        server = dict(((report or inventory.report_of(server_id)).get("metrics") or {}).get("server") or {})
    return {key: server.get(key) for key in ("cpu_percent", "memory_percent", "disk_percent", "load_1m")}


def reference(server_id: str) -> dict[str, Any]:
    """{"id", "name", "region", "online"} of a server, as instances show it."""
    if server_id == servers.MAIN:
        record = registry.main_record()
    else:
        record = registry.get(server_id) or {"id": server_id, "name": server_id, "region": ""}
    return {
        "id": server_id,
        "name": record.get("name") or server_id,
        "region": record.get("region") or "",
        "online": links.online(server_id),
    }


def decorate(resource: dict[str, Any], server_id: str) -> dict[str, Any]:
    """An instance resource an agent built, with what only the panel knows: its server, project and lock."""
    from sudoo.web.v1.resources import branch_lock, project_reference

    name = str(resource.get("name") or "")
    facts = inventory.env(name) if name else {}
    project = facts.get("PROJECT") or None
    branch = facts.get("GIT_BRANCH") or None
    shown = dict(resource)
    shown["server"] = reference(server_id)
    shown["project"] = project_reference(project)
    shown["lock"] = branch_lock(project, branch)
    if not links.online(server_id) and shown.get("status") not in ("creating",):
        shown["status_on_server"] = shown.get("status")
    return shown


def choose(requested: str | None, project: Mapping[str, Any] | None = None) -> str:
    """The server a new instance goes to; raises Refused when it cannot go there."""
    server_id = requested or str((project or {}).get("default_server") or "") or servers.MAIN
    if server_id == servers.MAIN:
        return servers.MAIN
    record = registry.get(server_id)
    if record is None:
        raise Refused(400, "Choose a server from the list (Servers).")
    if record.get("status") != registry.ACTIVE:
        raise Refused(409, f"The server {record['name']} is not active ({record.get('status')}).")
    if not links.connected(server_id):
        raise Refused(
            503, f"The server {record['name']} is offline. Choose another server, or try again once it is back."
        )
    if room(server_id)["full"]:
        raise Refused(
            409,
            f"The server {record['name']} has {inventory.count(server_id)} instances, its limit. Choose another "
            "server, or raise its Instances at most (Servers).",
        )
    return server_id


def start_planned(plan: dict[str, Any], user: str, server_id: str = "") -> dict[str, Any]:
    """Start a planned creation (instances/planning.build) on `server_id` (the main server when empty, or when that
    server is not connected); the job as the API shows it. The panel's own creations use it (a development instance
    for a new branch goes to its project's default server)."""
    from sudoo.instances import planning, store
    from sudoo.web.v1.resources import job_resource

    if not server_id or server_id == servers.MAIN or not links.connected(server_id):
        return job_resource(planning.start(plan, user))
    store.release_ports(plan.pop("ports"))
    links.expect_owner(server_id, plan.get("repo") or "")
    job = links.call(server_id, "create", {"plan": plan, "user": user})
    planning.record_stage(plan, user)
    facts = {"STAGE": plan["stage"], "PROJECT": plan.get("project") or "", "GIT_BRANCH": plan["branch"],
             "ODOO_VERSION": plan["version"], "EDITION": plan["edition"]}  # fmt: skip
    inventory.instance_changed(server_id, plan["name"], {"name": plan["name"], "status": "creating"}, facts)
    inventory.job_changed(server_id, job)
    return dict(job)


def choices() -> list[dict[str, Any]]:
    """The servers a new instance may go to now: the main one and every active, connected one."""
    found = [{"id": servers.MAIN, "name": registry.main_record()["name"], "region": registry.main_record()["region"]}]
    for record in registry.all_servers():
        if record.get("status") == registry.ACTIVE and links.connected(record["id"]):
            found.append({"id": record["id"], "name": record["name"], "region": record.get("region") or ""})
    return found
