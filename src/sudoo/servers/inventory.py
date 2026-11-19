"""Every instance the panel manages, on every server: the main server's own (instances/store.py) and those the
agents of other servers report (their facts, their resource as the API shows it, their recent jobs).

The panel keeps what each agent last reported in memory and in storage (collection "server_inventory"), so the
instances of a server that is offline are still listed (with the server shown offline) after a restart of the panel.
Lists, names, counts and access checks go through here: `names()`, `env(name)`, `server_of(name)`. In an agent,
everything here is its own server's (nothing is remote).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from sudoo import servers
from sudoo.instances import store

COLLECTION = "server_inventory"
ENV_KEYS = (
    "ODOO_VERSION",
    "EDITION",
    "DB_NAME",
    "GIT_BRANCH",
    "SOURCE",
    "AUTO_UPDATE",
    "AUTO_UPGRADE",
    "AUTO_PAUSE",
    "DELETE_AFTER_DAYS",
    "CREATED_AT",
    "CREATED_BY",
    "STAGE",
    "PROJECT",
)
"""The facts of an instance's .env an agent reports (never a password, a key or a port)."""
JOBS_KEPT = 100

_LOCK = threading.Lock()
_SERVERS: dict[str, dict[str, Any]] = {}  # server id -> {"instances": {name: {...}}, "jobs": {id: resource}, ...}
_LOADED = threading.Event()


def _load() -> None:
    if _LOADED.is_set() or servers.is_agent():
        return
    from sudoo.storage import storage

    with _LOCK:
        if _LOADED.is_set():
            return
        for server_id, found in storage().all(COLLECTION).items():
            _SERVERS[server_id] = {
                "instances": dict(found.get("instances") or {}),
                "jobs": dict(found.get("jobs") or {}),
                "reported_at": found.get("reported_at"),
                "metrics": found.get("metrics") or {},
                "capacity": found.get("capacity") or {},
            }
        _LOADED.set()


def _save(server_id: str) -> None:
    from sudoo.storage import storage

    found = _SERVERS.get(server_id)
    if found is None:
        storage().delete(COLLECTION, server_id)
        return
    storage().put(
        COLLECTION,
        server_id,
        {
            "instances": found["instances"],
            "jobs": dict(list(found["jobs"].items())[-JOBS_KEPT:]),
            "reported_at": found.get("reported_at"),
            "metrics": found.get("metrics") or {},
            "capacity": found.get("capacity") or {},
        },
    )


# ---------------------------------------------------------------- reading


def server_of(name: str) -> str:
    """The server an instance runs on: the main server for its own instances (and for names nobody knows)."""
    if servers.is_agent() or store.exists(name):
        return servers.MAIN
    _load()
    for server_id, found in list(_SERVERS.items()):
        if name in found["instances"]:
            return server_id
    return servers.MAIN


def is_remote(name: str) -> bool:
    return server_of(name) != servers.MAIN


def remote(name: str) -> dict[str, Any] | None:
    """What the panel knows of an instance of another server: {"env", "resource", "server"}, or None."""
    if servers.is_agent() or store.exists(name):
        return None
    _load()
    for server_id, found in list(_SERVERS.items()):
        entry = found["instances"].get(name)
        if entry is not None:
            return {**entry, "server": server_id}
    return None


def remote_names(server_id: str | None = None) -> list[str]:
    if servers.is_agent():
        return []
    _load()
    local = set(store.names())
    return sorted(
        name
        for sid, found in list(_SERVERS.items())
        if server_id is None or sid == server_id
        for name in found["instances"]
        if name not in local
    )


def names() -> list[str]:
    """Every instance of every server, sorted."""
    return sorted(set(store.names()) | set(remote_names()))


def exists(name: str) -> bool:
    return store.exists(name) or remote(name) is not None


def env(name: str) -> dict[str, str]:
    """The facts of an instance's .env: all of it for the main server's, ENV_KEYS for another server's."""
    found = remote(name)
    if found is not None:
        return dict(found.get("env") or {})
    return store.read_env(name)


def server_ids() -> list[str]:
    _load()
    return sorted(_SERVERS)


def report_of(server_id: str) -> dict[str, Any]:
    _load()
    return dict(_SERVERS.get(server_id) or {})


def count(server_id: str) -> int:
    if server_id == servers.MAIN:
        return len(store.names())
    return len(remote_names(server_id))


# ---------------------------------------------------------------- jobs of other servers


def job_server(job_id: str) -> str | None:
    """The server a job of another server ran on, when the panel heard of it."""
    if servers.is_agent():
        return None
    _load()
    for server_id, found in list(_SERVERS.items()):
        if job_id in found["jobs"]:
            return server_id
    return None


def remote_jobs(instance: str | None = None) -> list[dict[str, Any]]:
    """Job resources reported by other servers, newest first."""
    if servers.is_agent():
        return []
    _load()
    found = [
        job
        for server in list(_SERVERS.values())
        for job in list(server["jobs"].values())
        if instance is None or job.get("instance") == instance
    ]
    return sorted(found, key=lambda job: str(job.get("started_at") or ""), reverse=True)


# ---------------------------------------------------------------- what agents report


def _server(server_id: str) -> dict[str, Any]:
    return _SERVERS.setdefault(server_id, {"instances": {}, "jobs": {}, "reported_at": None, "metrics": {}})


def clean_env(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {k: str(raw[k])[:200] for k in ENV_KEYS if k in raw and raw[k] is not None}


def report(server_id: str, body: dict[str, Any], now: float | None = None) -> None:
    """A full report of an agent: its instances replace what the panel knew of that server."""
    _load()
    instances: dict[str, Any] = {}
    taken = set(store.names()) | {
        name for sid, found in _SERVERS.items() if sid != server_id for name in found["instances"]
    }
    for entry in body.get("instances") or []:
        if not isinstance(entry, dict) or not store.NAME_RE.match(str(entry.get("name") or "")):
            continue
        name = str(entry["name"])
        if name in taken:  # the same name on two servers: the first one known keeps it
            continue
        resource = entry.get("resource") if isinstance(entry.get("resource"), dict) else {}
        instances[name] = {"env": clean_env(entry.get("env")), "resource": resource}
    jobs = {
        str(job["id"]): job
        for job in body.get("jobs") or []
        if isinstance(job, dict) and isinstance(job.get("id"), str) and len(job["id"]) <= 40
    }
    with _LOCK:
        server = _server(server_id)
        server["instances"] = instances
        server["jobs"] = dict(list({**server["jobs"], **jobs}.items())[-JOBS_KEPT:])
        server["reported_at"] = time.time() if now is None else now
        server["metrics"] = body.get("metrics") if isinstance(body.get("metrics"), dict) else {}
        server["capacity"] = body.get("capacity") if isinstance(body.get("capacity"), dict) else {}
        _save(server_id)


def instance_changed(server_id: str, name: str, resource: dict[str, Any], env_facts: Any = None) -> None:
    """A live event of an agent about one of its instances."""
    _load()
    if not store.NAME_RE.match(name or "") or store.exists(name):
        return
    with _LOCK:
        for sid, found in _SERVERS.items():
            if sid != server_id and name in found["instances"]:
                return
        entry = _server(server_id)["instances"].setdefault(name, {"env": {}, "resource": {}})
        entry["resource"] = resource
        if env_facts is not None:
            entry["env"] = clean_env(env_facts)


def instance_deleted(server_id: str, name: str) -> None:
    _load()
    with _LOCK:
        found = _SERVERS.get(server_id)
        if found is not None and found["instances"].pop(name, None) is not None:
            _save(server_id)


def job_changed(server_id: str, job: dict[str, Any]) -> None:
    _load()
    if not isinstance(job.get("id"), str):
        return
    with _LOCK:
        jobs = _server(server_id)["jobs"]
        jobs.pop(job["id"], None)
        jobs[job["id"]] = job
        while len(jobs) > JOBS_KEPT:
            jobs.pop(next(iter(jobs)))


def forget_server(server_id: str) -> None:
    _load()
    with _LOCK:
        _SERVERS.pop(server_id, None)
        _save(server_id)


def reset() -> None:
    """Forget everything (tests)."""
    with _LOCK:
        _SERVERS.clear()
        _LOADED.clear()
