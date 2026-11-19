"""API requests about an instance of another server, answered by that server's agent.

The panel authenticates the caller as for any request (web/security.py), then, before any endpoint runs, looks at
what the request is about:

    /api/v1/instances/<name>[/...]       an instance of another server (inventory.server_of)
    /api/v1/jobs/<id>[/log]              a job another server reported
    /api/v1/backups?server=<id>          the backups of another server
    /api/v1/backups/<file>?server=<id>   one of them (downloads are copied piece by piece)

It works out what the caller may do in that instance (projects/access.py: role, project and branch access, locked
branch) and sends the request with that list of permissions to the agent (links.call "http"), which runs the same
endpoint on its server for that caller (agent.py): every check of the endpoint applies there, with what the panel
allows. Promotions into production are checked here too, against every server's production instances. The answer
goes back as it came, an instance with its server, its project and its branch's lock as the panel knows them.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterator
from typing import Any

from flask import Response, abort, g, request

from sudoo import servers
from sudoo.config import settings

from . import inventory, links, registry
from .placement import decorate

INSTANCE_PATH = re.compile(r"^/api/v1/instances/([a-z0-9][a-z0-9-]{0,30})(/.*)?$")
JOB_PATH = re.compile(r"^/api/v1/jobs/([A-Za-z0-9]{1,40})(/log)?$")
BACKUP_PATH = re.compile(r"^/api/v1/backups(/[^/]+)?$")
PASSED_HEADERS = ("Location", "Content-Location", "Content-Disposition", "Retry-After", "X-Sudoo-Frame")
MAX_ANSWER_BYTES = 32 * 1024 * 1024


def stub(name: str, facts: dict[str, str]) -> dict[str, Any]:
    """An instance resource from the facts alone (before its agent's first full report of it)."""
    return {
        "name": name,
        "status": "unknown",
        "stage": facts.get("STAGE") or "development",
        "url": None,
        "odoo": {"version": facts.get("ODOO_VERSION", ""), "edition": facts.get("EDITION") or "community",
                 "database": facts.get("DB_NAME", "")},  # fmt: skip
        "source": "plain" if facts.get("SOURCE") == "plain" else "repository",
        "git": {"repository": None, "branch": facts.get("GIT_BRANCH") or None, "branch_deleted_on_github": False,
                "commit": None},  # fmt: skip
        "settings": {
            "auto_update": facts.get("AUTO_UPDATE") == "true",
            "auto_upgrade": facts.get("AUTO_UPGRADE") == "true",
            "auto_pause": facts.get("AUTO_PAUSE") == "true",
            "delete_after_days": int(facts.get("DELETE_AFTER_DAYS") or 0),
        },
        "usage": {"cpu_percent": 0, "memory_bytes": 0},
        "uptime": None,
        "gateway_error": None,
        "drift": None,
        "update_failure": None,
        "created_at": None,
        "created_by": facts.get("CREATED_BY") or None,
        "last_visit_at": None,
        "pauses_at": None,
        "expires_at": None,
        "current_job": None,
        "links": {"self": f"/api/v1/instances/{name}"},
    }


def remote_resource(name: str, full: bool = False) -> dict[str, Any] | None:
    """The last resource the server of `name` reported, decorated; None when it is not another server's."""
    found = inventory.remote(name)
    if found is None:
        return None
    resource = {**stub(name, found.get("env") or {}), **(found.get("resource") or {})}
    if not links.online(found["server"]):
        resource["status"] = "unreachable"
        resource["current_job"] = None
    if full:
        for key in ("recent_commits", "submodules", "backups", "recent_jobs"):
            resource.setdefault(key, [])
    return decorate(resource, found["server"])


# ---------------------------------------------------------------- what a request is about


def target() -> tuple[str, str | None] | None:
    """(server id, instance name or None) when the request is for another server, else None."""
    path = request.path
    found = INSTANCE_PATH.match(path)
    if found and found.group(2) == "/move":
        return None  # a move is the panel's own job (servers/move.py), whichever server the instance is on
    if found:
        server_id = inventory.server_of(found.group(1))
        return (server_id, found.group(1)) if server_id != servers.MAIN else None
    found = JOB_PATH.match(path)
    if found:
        from sudoo.core import jobs

        if found.group(1) in jobs.JOBS:
            return None
        server_id = inventory.job_server(found.group(1)) or servers.MAIN
        if server_id == servers.MAIN:
            return None
        job = next((j for j in inventory.remote_jobs() if j.get("id") == found.group(1)), {})
        return server_id, str(job.get("instance") or "") or None
    if BACKUP_PATH.match(path):
        server_id = request.args.get("server", "")
        if server_id and server_id != servers.MAIN:
            return server_id, None
    return None


def _scope(name: str | None) -> tuple[Any, str | None]:
    """(project, branch) of an instance of another server, from what its agent reported."""
    from sudoo import projects

    if not name:
        return None, None
    facts = inventory.env(name)
    project = projects.get(facts.get("PROJECT") or "") if facts.get("PROJECT") else None
    return project, (facts.get("GIT_BRANCH") or None) if project else None


def permissions_for(name: str | None) -> frozenset[str]:
    from sudoo.projects import access

    project, branch = _scope(name)
    return access.permissions(g.user, project, branch)


def before_request() -> Response | None:
    """Flask hook (after authentication): send the request to the agent of the server it is about."""
    if servers.is_agent() or not request.path.startswith("/api/v1/") or g.get("user") is None:
        return None
    found = target()
    if found is None:
        return None
    server_id, name = found
    record = registry.get(server_id)
    if record is None:
        abort(404, "This server does not exist.")
    allowed = permissions_for(name)
    if name and "instances.view" not in allowed:
        abort(404, "This instance does not exist.")
    if name is None and BACKUP_PATH.match(request.path) and "backups.manage" not in allowed:
        abort(403, "Your role cannot do this (manage backups).")
    if name:
        _check_production_promotion(name)
    if not links.connected(server_id):
        abort(
            503,
            f"The server {record['name']} is offline (its agent is not connected). Its instances keep serving "
            "there; try again once it is back.",
        )
    if request.method == "GET" and BACKUP_PATH.match(request.path) and request.path.count("/") == 4:
        return _download(server_id, request.path.rsplit("/", 1)[1])
    return send(server_id, request.method, request.path, request.query_string.decode(), request.get_data(), allowed)


def _check_production_promotion(name: str) -> None:
    """A change into production is counted against every server's production branches and instances."""
    if request.method != "PATCH" or request.path != f"/api/v1/instances/{name}":
        return
    try:
        data = json.loads(request.get_data() or b"{}")
    except ValueError:
        return
    if not isinstance(data, dict) or data.get("stage") != "production":
        return
    facts = inventory.env(name)
    if facts.get("STAGE") == "production":
        return
    from sudoo.instances import stages
    from sudoo.web.v1.instances import _project_production_refusal

    refused = stages.production_refusal(exclude=name) or _project_production_refusal(
        facts.get("PROJECT"), "", facts.get("GIT_BRANCH", ""), name
    )
    if refused:
        abort(409, refused)


def identity(allowed: frozenset[str]) -> dict[str, Any]:
    """Who the agent acts for: the caller's name and role, and exactly what the panel allows them there."""
    return {"name": str(g.user["name"]), "role": str(g.user["role"]), "permissions": sorted(allowed)}


def send(
    server_id: str, method: str, path: str, query: str, body: bytes, allowed: frozenset[str], user: Any = None
) -> Response:
    """Pass a request to a server's agent; its answer as a Flask response."""
    args = {
        "method": method,
        "path": path,
        "query": query,
        "body": body.decode("utf-8", "replace") if body else "",
        "user": user or identity(allowed),
        "ip": request.remote_addr if request else None,
    }
    try:
        answer = links.call(server_id, "http", args)
    except links.Offline:
        abort(503, f"The server {registry.name_of(server_id)} went offline. Try again once it is back.")
    except links.RemoteError as error:
        abort(error.status if 400 <= error.status < 600 else 502, error.message)
    return _response(server_id, path, answer)


def _response(server_id: str, path: str, answer: Any) -> Response:
    if not isinstance(answer, dict):
        abort(502, "The server's answer could not be read.")
    raw = base64.b64decode(answer.get("body") or "")
    status = int(answer.get("status") or 502)
    mimetype = str(answer.get("mimetype") or "application/json")
    if mimetype in ("application/json", "application/problem+json") and raw:
        raw = _adjust(server_id, path, raw)
    response = Response(raw, status=status, mimetype=mimetype)
    for header, value in (answer.get("headers") or {}).items():
        if header in PASSED_HEADERS and isinstance(value, str):
            response.headers[header] = value
    return response


def _adjust(server_id: str, path: str, raw: bytes) -> bytes:
    """Instances get their server, project and lock; jobs are remembered as that server's."""
    try:
        document = json.loads(raw)
    except ValueError:
        return raw
    data = document.get("data") if isinstance(document, dict) else None
    if isinstance(data, dict):
        if INSTANCE_PATH.fullmatch(path) and path.count("/") == 4 and "status" in data and "odoo" in data:
            document["data"] = decorate(data, server_id)
        elif isinstance(data.get("id"), str) and "steps" in data and "instance" in data:
            inventory.job_changed(server_id, data)
    if isinstance(data, list) and BACKUP_PATH.fullmatch(path):
        from sudoo.projects import access

        names = {
            str(b.get("instance")) for b in data if isinstance(b, dict) and inventory.is_remote(str(b.get("instance")))
        }
        shown = set(access.visible_instances(g.user, names))
        document["data"] = [b for b in data if not isinstance(b, dict) or str(b.get("instance")) not in names - shown]
    return json.dumps(document, separators=(",", ":"), default=str).encode()


def _download(server_id: str, file_name: str) -> Response:
    """A backup of another server, copied to the browser piece by piece (it may be large)."""
    from sudoo.instances import backups
    from sudoo.projects import access

    match = backups.FILE_RE.match(file_name)
    if not match:
        abort(404, "This backup does not exist.")
    instance = match.group("instance")
    if instance and inventory.is_remote(instance) and not access.allowed(g.user, "backups.manage", *_scope(instance)):
        abort(404, "This backup does not exist.")
    try:
        stat = links.call(server_id, "file.stat", {"area": "backups", "name": file_name})
    except links.Offline:
        abort(503, f"The server {registry.name_of(server_id)} went offline.")
    except links.RemoteError as error:
        abort(error.status if 400 <= error.status < 600 else 502, error.message)
    size = int(stat.get("size") or 0)
    chunk = int(settings.move_chunk_mb) * 1024 * 1024

    def pieces() -> Iterator[bytes]:
        offset = 0
        while offset < size:
            piece = links.call(
                server_id, "file.read", {"area": "backups", "name": file_name, "offset": offset, "length": chunk}
            )
            data = base64.b64decode(piece.get("data") or "")
            if not data:
                break
            offset += len(data)
            yield data

    from sudoo.web.security import log

    log("backup downloaded", detail=f"{file_name} (on {registry.name_of(server_id)})")
    response = Response(pieces(), mimetype="application/zip")
    response.headers["Content-Disposition"] = f'attachment; filename="{file_name}"'
    response.headers["Content-Length"] = str(size)
    return response


def install(app: Any) -> None:
    app.before_request(before_request)
