"""Builds the topology snapshot (contract.py) from what the engine reports, with the privacy rules applied.

`snapshot()` is what the sharing loop sends: built at most every CACHE_SECONDS (the figures it reads change every
MONITOR_INTERVAL anyway), so asking for it often costs nothing. `build(raw, include_names)` is the pure part, tested
with a Docker and a Kubernetes engine.

Privacy: every id is a pseudonym (privacy.py); a "name" key appears only when `include_names` is true; free text
from the engine never reaches the snapshot: statuses, stages, kinds and versions are checked against fixed lists
and patterns, anything else becomes "unknown" or null.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from typing import Any

from sudoo.config import settings

from . import engines
from .contract import (
    COMPONENT_KINDS,
    DEGRADED_PERCENT,
    ENGINES,
    GROUP_KINDS,
    INSTANCE_STATUSES,
    INTERNET,
    MAX_BYTES,
    MAX_INSTANCES,
    MAX_NODES,
    NODE_ROLES,
    PART_KINDS,
    SCHEMA,
    STAGES,
    RawComponent,
    RawInstance,
    RawNode,
    RawPart,
    RawTopology,
)
from .privacy import pseudonym

CACHE_SECONDS = 10.0
ODOO_VERSION = re.compile(r"^(saas-)?\d{1,2}\.\d{1,2}$")
NAME_LENGTH = 80

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


def _name(value: str | None) -> str | None:
    return None if value is None else str(value)[:NAME_LENGTH]


def _percent(used: int | None, total: int | None) -> float | None:
    return round(100 * used / total, 1) if used is not None and total else None


def _node_status(node: RawNode) -> str:
    if not node.online:
        return "down"
    memory = _percent(node.memory_used_bytes, node.memory_total_bytes)
    if node.cpu_percent is None and memory is None:
        return "unknown" if node.role != "server" else "healthy"
    if (node.cpu_percent or 0) >= DEGRADED_PERCENT or (memory or 0) >= DEGRADED_PERCENT:
        return "degraded"
    return "healthy"


def _component_health(component: RawComponent, instance_status: str) -> str:
    replicas = component.replicas
    if replicas.desired <= 0 or instance_status in ("paused", "stopped", "switched_off"):
        return "idle"
    if replicas.ready <= 0:
        return "down" if instance_status not in ("starting", "busy") else "degraded"
    if replicas.ready < replicas.desired or component.healthy is False:
        return "degraded"
    return "healthy"


def _instance_health(components: list[dict[str, Any]], status: str) -> str:
    words = [c["health"] for c in components]
    if status in ("paused", "stopped", "switched_off"):
        return "idle"
    if status == "unknown" or not words:
        return "unknown"
    if "down" in words:
        return "down"
    if "degraded" in words or status in ("starting", "busy"):
        return "degraded"
    return "healthy"


def _replicas(component: RawComponent | RawPart) -> dict[str, int]:
    return {"ready": max(0, int(component.replicas.ready)), "desired": max(0, int(component.replicas.desired))}


def _rates(rpm: float | None, error_percent: float | None) -> tuple[float | None, float | None]:
    if rpm is None:
        return None, None
    errors = round(rpm * (error_percent or 0) / 100, 2)
    return round(rpm, 1), errors


def build(raw: RawTopology, include_names: bool = False, sudoo_version: str = "") -> dict[str, Any]:
    """The snapshot (contract.py) of what `raw` describes."""
    names = include_names

    def named(document: dict[str, Any], real: str | None) -> dict[str, Any]:
        if names and real:
            document["name"] = _name(real)
        return document

    node_ids: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    for node in raw.nodes[:MAX_NODES]:
        node_id = pseudonym("node", node.name)
        node_ids[node.name] = node_id
        nodes.append(
            named(
                {
                    "id": node_id,
                    "role": node.role if node.role in NODE_ROLES else "worker",
                    "status": _node_status(node),
                    "cpu": {"cores": node.cores, "percent": _round(node.cpu_percent)},
                    "memory": {
                        "total_bytes": node.memory_total_bytes,
                        "used_bytes": node.memory_used_bytes,
                        "percent": _percent(node.memory_used_bytes, node.memory_total_bytes),
                    },
                },
                node.name,
            )
        )
    main_node = next((node_ids[n.name] for n in raw.nodes if n.role in ("main", "control-plane")), None)

    parts: list[dict[str, Any]] = []
    for part in raw.parts:
        if part.kind not in PART_KINDS:
            continue
        part_id = f"panel/{part.kind}" if part.kind != "agent" else f"panel/agent/{node_ids.get(part.node, 'x')}"
        health = "healthy" if part.running and part.replicas.ready >= part.replicas.desired else "down"
        if part.running and 0 < part.replicas.ready < part.replicas.desired:
            health = "degraded"
        parts.append(
            {
                "id": part_id,
                "kind": part.kind,
                "node": node_ids.get(part.node, main_node),
                "health": health,
                "replicas": _replicas(part),
                "cpu_percent": _round(part.cpu_percent, 2),
                "memory_bytes": part.memory_bytes,
            }
        )
    kinds = {part["kind"] for part in parts}

    instances: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    projects: Counter[str] = Counter()
    project_names: dict[str, str | None] = {}
    if "web" in kinds:
        edges.append(_edge(INTERNET, "panel/web", "api"))
        edges.append(_edge("panel/web", "panel/backend", "api"))
    else:
        edges.append(_edge(INTERNET, "panel/backend", "api"))
    for item in raw.instances[:MAX_INSTANCES]:
        document, links = _instance(item, node_ids, main_node, names, mail="mail" in kinds)
        instances.append(document)
        edges.extend(links)
        if document["project"]:
            projects[document["project"]] += 1
            project_names[document["project"]] = item.project_name or item.project
    return {
        "schema": SCHEMA,
        "generated_at": int(raw.generated_at),
        "engine": raw.engine if raw.engine in ENGINES else "docker",
        "names": "included" if names else "hidden",
        "truncated": len(raw.instances) > MAX_INSTANCES,
        "omitted_instances": max(0, len(raw.instances) - MAX_INSTANCES),
        "panel": {"version": sudoo_version[:32], "parts": parts},
        "nodes": nodes,
        "projects": [
            named({"id": project_id, "instances": count}, project_names.get(project_id))
            for project_id, count in sorted(projects.items())
        ],
        "instances": instances,
        "edges": edges,
    }


def _edge(
    source: str,
    target: str,
    kind: str,
    rpm: float | None = None,
    errors: float | None = None,
    estimated: bool = False,
) -> dict[str, Any]:
    return {
        "id": f"{source}>{target}",
        "source": source,
        "target": target,
        "kind": kind,
        "requests_per_minute": rpm,
        "errors_per_minute": errors,
        "estimated": estimated,
    }


def _instance(
    item: RawInstance, node_ids: dict[str, str], main_node: str | None, names: bool, mail: bool
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    instance_id = pseudonym("instance", item.name)
    status = item.status if item.status in INSTANCE_STATUSES else "unknown"
    components = [
        {
            "id": f"{instance_id}/{component.kind}",
            "kind": component.kind,
            "health": _component_health(component, status),
            "replicas": _replicas(component),
            "cpu_percent": _round(component.cpu_percent, 2),
            "memory_bytes": component.memory_bytes,
        }
        for component in item.components
        if component.kind in COMPONENT_KINDS
    ]
    rpm, errors = _rates(item.requests_per_minute, item.error_rate_percent)
    if status in ("paused", "stopped", "switched_off") and rpm is None:
        rpm, errors = 0.0, 0.0
    document: dict[str, Any] = {
        "id": instance_id,
        "project": pseudonym("project", item.project) if item.project else None,
        "stage": item.stage if item.stage in STAGES else "development",
        "status": status,
        "health": _instance_health(components, status),
        "odoo_version": item.odoo_version if item.odoo_version and ODOO_VERSION.match(item.odoo_version) else None,
        "edition": item.edition if item.edition in ("community", "enterprise") else None,
        "node": node_ids.get(item.node, main_node),
        "group": {
            "kind": item.group_kind if item.group_kind in GROUP_KINDS else "compose",
            "id": pseudonym("group", item.group),
        },
        "components": components,
        "traffic": {
            "requests_per_minute": rpm,
            "errors_per_minute": errors,
            "response_ms_p95": _round(item.response_ms_p95),
        },
    }
    if names:
        document = {"id": instance_id, "name": _name(item.name), **{k: v for k, v in document.items() if k != "id"}}
    kinds = {c["kind"] for c in components}
    links: list[dict[str, Any]] = []
    gateway, odoo, postgres = (f"{instance_id}/{kind}" for kind in COMPONENT_KINDS)
    if "gateway" in kinds:
        links.append(_edge(INTERNET, gateway, "http", rpm, errors))
        if "odoo" in kinds:
            links.append(_edge(gateway, odoo, "http", rpm, errors))
    if "odoo" in kinds and "postgres" in kinds:
        links.append(_edge(odoo, postgres, "sql", rpm, 0.0 if rpm is not None else None, estimated=True))
    if mail and "odoo" in kinds and document["stage"] != "production":
        links.append(_edge(odoo, "panel/mail", "smtp"))
    return document, links


def encoded(snapshot: dict[str, Any]) -> bytes:
    return json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False).encode()


def fit(snapshot: dict[str, Any], max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    """The snapshot, with instances left out until it fits in `max_bytes`: stopped ones first, then the quietest.
    Their edges go with them; `truncated` and `omitted_instances` say so."""
    if len(encoded(snapshot)) <= max_bytes:
        return snapshot
    order = sorted(
        snapshot["instances"],
        key=lambda i: (i["health"] != "idle", i["traffic"]["requests_per_minute"] or 0.0),
    )
    keep = list(order)
    trimmed = dict(snapshot)
    while keep:
        drop = max(1, len(keep) // 10)
        keep = keep[drop:]
        kept = {i["id"] for i in keep}
        trimmed["instances"] = [i for i in snapshot["instances"] if i["id"] in kept]
        trimmed["edges"] = [
            e
            for e in snapshot["edges"]
            if not _instance_edge(e) or e["source"].split("/")[0] in kept or e["target"].split("/")[0] in kept
        ]
        trimmed["truncated"] = True
        trimmed["omitted_instances"] = snapshot["omitted_instances"] + len(snapshot["instances"]) - len(keep)
        if len(encoded(trimmed)) <= max_bytes:
            break
    counts = Counter(i["project"] for i in trimmed["instances"] if i["project"])
    trimmed["projects"] = [{**p, "instances": counts[p["id"]]} for p in snapshot["projects"] if counts[p["id"]]]
    return trimmed


def _instance_edge(edge: dict[str, Any]) -> bool:
    return any(end.startswith("i-") for end in (edge["source"], edge["target"]))


def snapshot(now: float | None = None) -> dict[str, Any]:
    """The current snapshot, built at most every CACHE_SECONDS (and again when the names choice changed)."""
    from sudoo.hub import client

    now = time.monotonic() if now is None else now
    include = bool(settings.topology_include_names)
    with _LOCK:
        if _CACHE and now - float(_CACHE["at"]) < CACHE_SECONDS and _CACHE["names"] == include:
            return dict(_CACHE["snapshot"])
        built = fit(build(engines.current().read(), include, client.sudoo_version()))
        _CACHE.update(at=now, names=include, snapshot=built)
        return dict(built)


def forget() -> None:
    with _LOCK:
        _CACHE.clear()
