"""What the Kubernetes engine adds to the topology shared with Sudoo.sh (sudoo/topology/engines.py, a contributor):
the cluster's nodes, and for each instance its namespace, the node of its Odoo pod and its replicas.

Registered when the engine is first built (engines.named). Called from the topology loop's thread at most every
10 seconds; an instance it cannot describe gives None, never an error.
"""

from __future__ import annotations

from typing import Any

from sudoo.topology.contract import InstanceFacts, RawNode, Replicas

from . import cluster, manifests
from .api import AccessError, ApiError


def _replicas(found: dict[str, Any] | None) -> Replicas | None:
    if not found:
        return None
    return Replicas(
        ready=int((found.get("status") or {}).get("readyReplicas") or 0),
        desired=int((found.get("spec") or {}).get("replicas") or 0),
    )


class KubernetesTopology:
    def nodes(self) -> list[RawNode]:
        try:
            found = cluster.nodes()
        except (AccessError, ApiError):
            return []
        return [
            RawNode(
                name=node["name"],
                role="control-plane" if "control-plane" in node["roles"] else "worker",
                online=bool(node["ready"]),
                cores=int(node["cpu"]) if node["cpu"] else None,
                cpu_percent=(
                    round(100 * node["cpu_used"] / node["cpu"], 1)
                    if node["cpu_used"] is not None and node["cpu"]
                    else None
                ),
                memory_total_bytes=int(node["memory_bytes"]) or None,
                memory_used_bytes=node["memory_used_bytes"],
            )
            for node in found
        ]

    def instance(self, name: str, env: dict[str, str]) -> InstanceFacts | None:
        from .engine import api, server_pods

        try:
            client = api()
            namespace = manifests.namespace_of(name)
            pods = server_pods(client, name, "odoo")
            replicas = {
                kind: found
                for kind, found in (
                    ("odoo", _replicas(client.get("Deployment", "odoo", namespace))),
                    ("postgres", _replicas(client.get("StatefulSet", "db", namespace))),
                )
                if found is not None
            }
        except (AccessError, ApiError):
            return None
        node = next((str((p.get("spec") or {}).get("nodeName") or "") for p in pods), "")
        return InstanceFacts(node=node, group=namespace, replicas=replicas)
