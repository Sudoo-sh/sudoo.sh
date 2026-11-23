"""Where the topology's figures come from: the engine that runs the instances.

An engine is any object with `read() -> RawTopology` (contract.py). The Docker engine below reads what the panel
already measures (instances/monitor.py every MONITOR_INTERVAL, the gateway's request counts in
instances/metrics.py) and runs no command of its own, so reading it is cheap. Another engine (the Kubernetes
engine) registers itself with `use(engine)` when it starts; the snapshot then describes its namespaces, nodes and
replicas in the same contract, and the hub and the staff console need no change.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from sudoo.config import settings

from .contract import RawComponent, RawInstance, RawNode, RawPart, RawTopology, Replicas


class Engine(Protocol):
    name: str

    def read(self) -> RawTopology: ...


_CURRENT: list[Engine] = []


def use(engine: Engine | None) -> None:
    """Make `engine` the source of the topology (None: back to Docker)."""
    _CURRENT[:] = [engine] if engine is not None else []


def current() -> Engine:
    return _CURRENT[0] if _CURRENT else DockerEngine()


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


class DockerEngine:
    """One Compose project per instance on this server, plus the instances other servers' agents report."""

    name = "docker"

    def read(self) -> RawTopology:
        from sudoo import projects
        from sudoo.instances import metrics, monitor, stages, store
        from sudoo.premium import servers

        main = str(servers.reference(servers.MAIN).get("name") or settings.server_name)
        server = metrics.SERVER
        nodes: dict[str, RawNode] = {
            main: RawNode(
                name=main,
                role="main",
                online=True,
                cores=_int(server.get("cores")),
                cpu_percent=_float(server.get("cpu_percent")),
                memory_total_bytes=_int(server.get("memory_total_bytes")),
                memory_used_bytes=_int(server.get("memory_used_bytes")),
            )
        }
        parts = [
            RawPart(
                kind=kind,
                node=main,
                replicas=Replicas(int(figures.get("containers") or 1), int(figures.get("containers") or 1)),
                cpu_percent=_float(figures.get("cpu_percent")),
                memory_bytes=_int(figures.get("memory_bytes")),
            )
            for kind, figures in sorted(metrics.PANEL.items())
            if kind in ("backend", "web", "gateway", "mail", "connectors")
        ]
        if not any(part.kind == "backend" for part in parts):
            parts.insert(0, RawPart(kind="backend", node=main))

        project_names = {str(p.get("id")): str(p.get("name") or p.get("id")) for p in projects.all_projects()}
        instances: list[RawInstance] = []
        for name in store.names():
            try:
                env = store.read_env(name)
            except OSError:
                continue
            measured = monitor.STATUS.get(name, {})
            figures = metrics.INSTANCES.get(name, {})
            status = monitor.status(name)
            odoo_up = bool(measured.get("running"))
            db_up = bool(measured.get("db_running"))
            healthy = bool(measured.get("healthy"))
            project = env.get("PROJECT") or None
            instances.append(
                RawInstance(
                    name=name,
                    node=main,
                    status=status,
                    stage=stages.of(env),
                    group_kind="compose",
                    group=f"odoo-{name}",
                    project=project,
                    project_name=project_names.get(project or ""),
                    odoo_version=env.get("ODOO_VERSION") or None,
                    edition="enterprise" if env.get("EDITION") == "enterprise" else "community",
                    components=(
                        RawComponent("gateway", Replicas(1, 1), healthy=True),
                        RawComponent(
                            "odoo",
                            Replicas(int(odoo_up), 1),
                            healthy=healthy if odoo_up else None,
                            cpu_percent=_float(measured.get("cpu")),
                            memory_bytes=_int(measured.get("memory")),
                        ),
                        RawComponent("postgres", Replicas(int(db_up), 1), healthy=db_up if odoo_up else None),
                    ),
                    requests_per_minute=_float(figures.get("requests_per_minute")),
                    error_rate_percent=_float(figures.get("error_rate_percent")),
                    response_ms_p95=_float(figures.get("response_ms_p95")),
                )
            )
        for name in servers.remote_names():
            found = servers.remote(name) or {}
            server_id = str(found.get("server") or "")
            reference = servers.reference(server_id) if server_id else {}
            node = str(reference.get("name") or server_id or "server")
            if node not in nodes:
                nodes[node] = RawNode(name=node, role="server", online=bool(reference.get("online", True)))
                parts.append(RawPart(kind="agent", node=node, running=bool(reference.get("online", True))))
            env = servers.env(name)
            resource = servers.remote_resource(name) or {}
            status = str(resource.get("status") or "unknown")
            running = status in ("online", "starting", "busy")
            project = env.get("PROJECT") or None
            instances.append(
                RawInstance(
                    name=name,
                    node=node,
                    status=status,
                    stage=stages.of(env),
                    group_kind="compose",
                    group=f"odoo-{name}",
                    project=project,
                    project_name=project_names.get(project or ""),
                    odoo_version=env.get("ODOO_VERSION") or None,
                    edition="enterprise" if env.get("EDITION") == "enterprise" else "community",
                    components=(
                        RawComponent("gateway", Replicas(1, 1), healthy=True),
                        RawComponent("odoo", Replicas(int(running), 1), healthy=(status == "online") if running else None),
                        RawComponent("postgres", Replicas(int(running), 1), healthy=True if running else None),
                    ),
                )
            )
        return RawTopology(
            engine=self.name,
            nodes=tuple(nodes.values()),
            parts=tuple(parts),
            instances=tuple(instances),
            generated_at=float(metrics.snapshot().get("at") or time.time()),
        )
