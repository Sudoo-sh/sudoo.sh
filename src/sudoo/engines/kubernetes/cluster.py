"""The cluster as the panel shows it: its nodes, and the command that adds one.

    nodes()          every node: name, roles, ready or not, version, CPU and memory it offers, and what is used now
                     (metrics-server; None without it)
    join_command()   a k3s bootstrap token (a Secret in kube-system that k3s accepts for joining agents, expiring after
                     KUBERNETES_JOIN_TOKEN_MINUTES) and the exact `k3s agent` installation command with it. The token is
                     returned once and never stored by the panel. Its form is K10<sha256 of the cluster's CA>::<id>.<secret>:
                     the new node checks it joins this cluster.

The panel's ServiceAccount may create Secrets in kube-system and nothing else there; the cluster's admission policy
(scripts/install-k3s.sh, sudoo-panel-scope) refuses any Secret there that is not such a token.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import string
from datetime import UTC, datetime, timedelta
from typing import Any

from sudoo.config import settings

from . import manifests
from .api import ApiError
from .engine import _cpu, api, quantity_bytes

TOKEN_GROUP = "system:bootstrappers:k3s:default-node-token"
_ALPHABET = string.ascii_lowercase + string.digits
_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+\+k3s\d+$")


class JoinError(Exception):
    """A join command cannot be made (the message says what to set or fix)."""


def nodes() -> list[dict[str, Any]]:
    client = api()
    usage: dict[str, dict[str, Any]] = {}
    try:
        for metrics in client.list("NodeMetrics"):
            usage[metrics["metadata"]["name"]] = metrics.get("usage") or {}
    except ApiError:
        usage = {}
    found = []
    for node in client.list("Node"):
        meta, status = node["metadata"], node.get("status") or {}
        labels = meta.get("labels") or {}
        roles = sorted(key.split("/", 1)[1] for key in labels if key.startswith("node-role.kubernetes.io/"))
        capacity = status.get("allocatable") or status.get("capacity") or {}
        used = usage.get(meta["name"])
        addresses = {a.get("type"): a.get("address") for a in status.get("addresses") or []}
        found.append(
            {
                "name": meta["name"],
                "roles": roles or ["agent"],
                "ready": any(
                    c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions") or []
                ),
                "schedulable": not (node.get("spec") or {}).get("unschedulable"),
                "version": (status.get("nodeInfo") or {}).get("kubeletVersion", ""),
                "address": addresses.get("InternalIP", ""),
                "cpu": _cpu(str(capacity.get("cpu") or "0")),
                "memory_bytes": quantity_bytes(str(capacity.get("memory") or "0")),
                "cpu_used": None if used is None else round(_cpu(str(used.get("cpu") or "0")), 2),
                "memory_used_bytes": None if used is None else quantity_bytes(str(used.get("memory") or "0")),
                "created": meta.get("creationTimestamp"),
            }
        )
    return sorted(found, key=lambda n: ("control-plane" not in n["roles"], n["name"]))


def server_version(found: list[dict[str, Any]]) -> str:
    """The k3s version new nodes install: KUBERNETES_K3S_VERSION, else the control plane's own."""
    wanted = settings.kubernetes_k3s_version or next((n["version"] for n in found if "control-plane" in n["roles"]), "")
    if not _VERSION_RE.match(wanted):
        raise JoinError("Set KUBERNETES_K3S_VERSION to the k3s version of your server (for example v1.36.6+k3s1).")
    return wanted


def _ca_pem() -> str:
    """The cluster's certificate authority, as every namespace's kube-root-ca.crt ConfigMap holds it (the same bytes
    k3s hashes into its tokens)."""
    client = api()
    for found in (client.get("ConfigMap", "kube-root-ca.crt", ns) for ns in _own_namespaces(client)):
        if found:
            return str((found.get("data") or {}).get("ca.crt") or "")
    return client.access.ca_pem


def _own_namespaces(client: Any) -> list[str]:
    from sudoo.instances import store

    from .engine import read_record

    names = [read_record(name).get("namespace") for name in store.names()]
    return [str(ns) for ns in names if isinstance(ns, str) and ns.startswith(manifests.PREFIX)]


def join_command() -> dict[str, Any]:
    url = settings.kubernetes_join_url.rstrip("/")
    if not url.startswith("https://"):
        raise JoinError("Set KUBERNETES_JOIN_URL to the address new nodes reach your server's API at "
                        "(https://<address>:6443).")  # fmt: skip
    version = server_version(nodes())
    ca = _ca_pem()
    if not ca:
        raise JoinError("The cluster's certificate authority is unknown: check KUBERNETES_KUBECONFIG.")
    token_id = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    token_secret = "".join(secrets.choice(_ALPHABET) for _ in range(16))
    minutes = int(settings.kubernetes_join_token_minutes)
    expires = datetime.now(UTC) + timedelta(minutes=minutes)
    api().create(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "bootstrap.kubernetes.io/token",
            "metadata": {
                "name": f"bootstrap-token-{token_id}",
                "namespace": "kube-system",
                "labels": manifests.MANAGED,
            },
            # data, not stringData: the admission policy checks the values the API stores
            "data": {
                key: base64.b64encode(value.encode()).decode()
                for key, value in {
                    "token-id": token_id,
                    "token-secret": token_secret,
                    "expiration": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "usage-bootstrap-authentication": "true",
                    "usage-bootstrap-signing": "true",
                    "auth-extra-groups": TOKEN_GROUP,
                    "description": "Sudoo.sh: add a node",
                }.items()
            },
        }
    )
    token = f"K10{hashlib.sha256(ca.encode()).hexdigest()}::{token_id}.{token_secret}"
    command = (
        f"curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION='{version}' K3S_URL='{url}' "
        f"K3S_TOKEN='{token}' sh -s - agent"
    )
    return {"command": command, "expires": int(expires.timestamp()), "version": version, "token_id": token_id}
