"""The topology contract, version 1: what an engine reports (the Raw* records below) and what the panel sends.

An engine (Docker today, Kubernetes later) describes what runs in engine-neutral records: the servers or cluster
nodes (RawNode), the panel's own parts (RawPart) and every instance (RawInstance) with its components (gateway,
Odoo, PostgreSQL), their replicas and health, and the traffic the gateway counted. builder.py turns them into the
snapshot below, with pseudonymous ids and no names unless the administrator chose to include them.

The snapshot (JSON, at most MAX_BYTES once encoded), schema "sudoo.topology/v1":

    schema          "sudoo.topology/v1"; a later version changes this string, readers refuse what they do not know
    generated_at    Unix time (seconds) the figures were read
    engine          "docker" or "kubernetes"
    names           "hidden" (default: no "name" anywhere) or "included" (TOPOLOGY_INCLUDE_NAMES)
    truncated       true when instances were left out to stay under MAX_BYTES (omitted_instances says how many)
    panel           {"version", "parts": [part]}: the panel's backend, web, gateway, mail catcher and connectors
    nodes           [node]: the servers (docker) or cluster nodes (kubernetes)
    projects        [project]: {"id", "name"?, "instances": count}
    instances       [instance]
    edges           [edge]: who sends requests to whom, with the rates of the last minute

    node            {"id", "name"?, "role", "status", "cpu": {"cores", "percent"}, "memory": {"total_bytes",
                    "used_bytes", "percent"}}; role: main, server (another server of the panel), control-plane,
                    worker; status: healthy, degraded, down, unknown
    part            {"id": "panel/<kind>", "kind", "node", "health", "replicas": {"ready", "desired"}, "cpu_percent",
                    "memory_bytes"}; kind: backend, web, gateway, mail, connectors, agent
    instance        {"id", "name"?, "project" (a project id or null), "stage", "status", "health", "odoo_version",
                    "edition", "node", "group": {"kind": "compose" | "namespace", "id"}, "components": [component],
                    "traffic": {"requests_per_minute", "errors_per_minute", "response_ms_p95"}}
    component       {"id": "<instance id>/<kind>", "kind": gateway | odoo | postgres, "health", "replicas":
                    {"ready", "desired"}, "cpu_percent", "memory_bytes"}
    edge            {"id", "source", "target", "kind": http | sql | smtp | api, "requests_per_minute",
                    "errors_per_minute", "estimated"}; source and target are a component, a part, or "internet";
                    null rates: not measured; estimated: derived, not counted (Odoo -> PostgreSQL follows Odoo's
                    requests)

Ids are short pseudonyms (privacy.py): stable for one panel, meaningless elsewhere. Statuses and health words are
the fixed lists below. Nothing else is sent: no addresses, domains, branch names, commits, users, e-mail, settings,
logs or database content.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCHEMA = "sudoo.topology/v1"
MAX_BYTES = 256 * 1024
"""Largest snapshot the panel sends (the hub refuses larger ones): instances are left out beyond it."""
MAX_NODES = 64
MAX_INSTANCES = 1000

ENGINES = ("docker", "kubernetes")
NODE_ROLES = ("main", "server", "control-plane", "worker")
NODE_STATUSES = ("healthy", "degraded", "down", "unknown")
HEALTH = ("healthy", "degraded", "down", "idle", "unknown")
"""idle: stopped on purpose (paused, switched off, stopped); down: should run and does not."""
INSTANCE_STATUSES = ("online", "starting", "busy", "paused", "stopped", "switched_off", "unknown")
STAGES = ("production", "staging", "development")
PART_KINDS = ("backend", "web", "gateway", "mail", "connectors", "agent")
COMPONENT_KINDS = ("gateway", "odoo", "postgres")
EDGE_KINDS = ("http", "sql", "smtp", "api")
GROUP_KINDS = ("compose", "namespace")
INTERNET = "internet"

DEGRADED_PERCENT = 90.0
"""A node using this share of its CPU or memory, or more, is degraded."""


@dataclass(frozen=True, slots=True)
class Replicas:
    ready: int
    desired: int


@dataclass(frozen=True, slots=True)
class RawNode:
    """A server (docker) or a cluster node (kubernetes), by its real name: builder.py decides what is sent."""

    name: str
    role: str
    online: bool = True
    cores: int | None = None
    cpu_percent: float | None = None
    memory_total_bytes: int | None = None
    memory_used_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class RawPart:
    kind: str
    node: str  # a RawNode's name
    running: bool = True
    replicas: Replicas = field(default_factory=lambda: Replicas(1, 1))
    cpu_percent: float | None = None
    memory_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class RawComponent:
    kind: str
    replicas: Replicas
    healthy: bool | None = None  # None: not checked
    cpu_percent: float | None = None
    memory_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class RawInstance:
    name: str
    node: str  # a RawNode's name
    status: str
    stage: str
    group_kind: str  # compose (docker) or namespace (kubernetes)
    group: str  # the compose project's or namespace's real name
    project: str | None = None  # the project's id in the panel, None for a standalone instance
    project_name: str | None = None
    odoo_version: str | None = None
    edition: str | None = None
    components: tuple[RawComponent, ...] = ()
    requests_per_minute: float | None = None
    error_rate_percent: float | None = None
    response_ms_p95: float | None = None


@dataclass(frozen=True, slots=True)
class RawTopology:
    """Everything an engine reports at one moment."""

    engine: str
    nodes: tuple[RawNode, ...]
    parts: tuple[RawPart, ...]
    instances: tuple[RawInstance, ...]
    generated_at: float
