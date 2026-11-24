"""The Kubernetes objects of one instance, as plain dicts (applied by engine.py with server-side apply).

Everything lives in the instance's namespace, sudoo-<name> (docs/KUBERNETES_ENGINE.md, section 4):

    Namespace (Pod Security restricted)   RoleBinding sudoo-panel    ResourceQuota + LimitRange sudoo
    Secret odoo-config (odoo.conf)        Secret db (password)       PVCs odoo-data, db-data, addons
    StatefulSet + Service db (PostgreSQL) Deployment + Service odoo  NetworkPolicies (default deny + allowed paths)
    runner Jobs odoo-run-<id> (made on demand: runner_job)

Pods never get a ServiceAccount token, run as their image's own non-root user with every capability dropped, and
the Odoo pods (server and runners) carry the role label "odoo" the database's policy lets in.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from collections.abc import Mapping
from typing import Any

MANAGED = {"app.kubernetes.io/managed-by": "sudoo"}
PREFIX = "sudoo-"
SYSTEM_NAMESPACE = "sudoo-system"
PANEL_SERVICE_ACCOUNT = "sudoo-panel"
INSTANCE_ROLE = "sudoo-instance"  # the ClusterRole the panel binds in each of its namespaces (installer)

ODOO_UID, ODOO_GID = 100, 101  # the official Odoo image's user
POSTGRES_UID = 999  # the official PostgreSQL image's user
ODOO_HTTP, ODOO_GEVENT, POSTGRES_PORT = 8069, 8072, 5432
FILESTORE_ROOT = "/var/lib/odoo"
ADDONS_MOUNT = "/mnt/extra-addons"  # as with Docker (instances/odoo.py CUSTOM_ADDONS_MOUNT)
RUNNER_SECONDS = 6 * 3600  # longest a runner Job lives (a large restore or module installation)


def namespace_of(name: str) -> str:
    return f"{PREFIX}{name}"


def labels(name: str, role: str | None = None, component: str | None = None) -> dict[str, str]:
    found = {**MANAGED, "sudoo.sh/instance": name}
    if role:
        found["sudoo.sh/role"] = role
    if component:
        found["sudoo.sh/component"] = component
    return found


def _meta(kind: str, name: str, namespace: str, instance: str, **extra: Any) -> dict[str, Any]:
    from .api import API_VERSIONS

    return {
        "apiVersion": API_VERSIONS.get(kind, "v1"),
        "kind": kind,
        "metadata": {"name": name, "namespace": namespace, "labels": labels(instance), **extra},
    }


def _restricted_container() -> dict[str, Any]:
    return {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}, "runAsNonRoot": True}


def _pod_security(uid: int, gid: int) -> dict[str, Any]:
    return {
        "runAsNonRoot": True,
        "runAsUser": uid,
        "runAsGroup": gid,
        "fsGroup": gid,
        "fsGroupChangePolicy": "OnRootMismatch",
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def _memory(value: Any) -> str | None:
    from sudoo.instances.resources import memory_bytes

    found = memory_bytes(str(value or "0"))
    return f"{found}" if found else None


def container_resources(service: str, limits: Mapping[str, Any], requested: str) -> dict[str, Any]:
    """Requests and limits of the Odoo or database container, from the instance's resource limits
    (instances/resources.py, the same as with Docker): a limit only when one is set (else the LimitRange's)."""
    found: dict[str, dict[str, str]] = {"requests": {"cpu": "50m", "memory": requested}}
    memory = _memory(limits.get(f"{service}_memory_limit"))
    cpus = float(limits.get(f"{service}_cpu_limit") or 0)
    if memory or cpus:
        found["limits"] = {}
        if memory:
            found["limits"]["memory"] = memory
            from sudoo.instances.resources import memory_bytes

            if memory_bytes(requested.removesuffix("i")) > int(memory):  # "256Mi" in Docker units: "256M"
                found["requests"]["memory"] = memory
        if cpus:
            found["limits"]["cpu"] = f"{int(cpus * 1000)}m"
    return found


# ---------------------------------------------------------------- the namespace and its rules


def namespace(name: str, env: Mapping[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": namespace_of(name),
            "labels": {
                **labels(name),
                "sudoo.sh/stage": env.get("STAGE", "") or "development",
                "sudoo.sh/project": env.get("PROJECT", "") or "",
                "pod-security.kubernetes.io/enforce": "restricted",
                "pod-security.kubernetes.io/enforce-version": "latest",
                "pod-security.kubernetes.io/warn": "restricted",
                "pod-security.kubernetes.io/audit": "restricted",
            },
        },
    }


def role_binding(name: str) -> dict[str, Any]:
    found = _meta("RoleBinding", PANEL_SERVICE_ACCOUNT, namespace_of(name), name)
    found["roleRef"] = {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": INSTANCE_ROLE}
    found["subjects"] = [{"kind": "ServiceAccount", "name": PANEL_SERVICE_ACCOUNT, "namespace": SYSTEM_NAMESPACE}]
    return found


def quota(name: str, settings: Any) -> list[dict[str, Any]]:
    ns = namespace_of(name)
    storage = int(
        settings.kubernetes_db_size_gb + settings.kubernetes_files_size_gb + settings.kubernetes_addons_size_gb
    )
    resource_quota = _meta("ResourceQuota", "sudoo", ns, name)
    resource_quota["spec"] = {
        "hard": {
            "limits.cpu": str(settings.kubernetes_quota_cpu),
            "limits.memory": f"{int(settings.kubernetes_quota_memory_mb)}Mi",
            "requests.storage": f"{storage}Gi",
            "persistentvolumeclaims": "3",
            "pods": str(settings.kubernetes_quota_pods),
            "services": "2",
            "services.nodeports": "0",
            "services.loadbalancers": "0",
        }
    }
    limit_range = _meta("LimitRange", "sudoo", ns, name)
    limit_range["spec"] = {
        "limits": [
            {
                "type": "Container",
                "default": {"cpu": f"{int(settings.kubernetes_container_cpu * 1000)}m",
                            "memory": f"{int(settings.kubernetes_container_memory_mb)}Mi"},
                "defaultRequest": {"cpu": "50m", "memory": "128Mi"},
            }  # fmt: skip
        ]
    }
    return [resource_quota, limit_range]


def node_blocks(nodes: list[dict[str, Any]]) -> list[str]:
    """The addresses traffic from a node's own network stack has when it reaches a pod on another node (flannel's
    network address of that node's pod range, x.x.N.0, and its bridge, x.x.N.1): the gateway's path into a pod.
    Pods never get either address."""
    found: list[str] = []
    for node in nodes:
        cidrs = (node.get("spec") or {}).get("podCIDRs") or [(node.get("spec") or {}).get("podCIDR")]
        for cidr in cidrs:
            if not cidr:
                continue
            try:
                network = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                continue
            bits = 32 if network.version == 4 else 128
            for host in (network.network_address, network.network_address + 1):
                found.append(f"{host}/{bits}")
    return sorted(set(found))


def mail_blocks(host: str, resolve: Any = None) -> list[str]:
    """The addresses of the panel's outgoing mail server (SMTP_HOST), as /32 and /128 blocks: a network policy
    cannot name a host, so the name is looked up here, each time the instance is applied. A name that moves to
    another address is followed the next time. Loopback, link-local (cloud metadata), unspecified and multicast
    addresses are never allowed. Empty: no host, no address, or a name that does not resolve."""
    host = host.strip().strip("[]")
    if not host:
        return []
    try:
        found = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = (resolve or socket.getaddrinfo)(host, None, type=socket.SOCK_STREAM)
        except OSError:
            return []
        found = []
        for info in infos:
            try:
                found.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
    blocks = {
        f"{address}/{address.max_prefixlen}"
        for address in found
        if not (address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast)
    }
    return sorted(blocks)


def network_policies(
    name: str, gateway_blocks: list[str], ports: list[int], mail: tuple[list[str], int] | None = None
) -> list[dict[str, Any]]:
    """Default deny in both directions, then only: DNS for every pod; Odoo pods to their own database; the database
    from Odoo pods; Odoo's ports from the nodes (the gateway, on a node's host network). Nothing from or to another
    namespace. With mail = (addresses, port) (the panel's outgoing mail server, settings.kubernetes_mail_egress),
    Odoo pods may also open TCP connections to exactly those addresses on that port, and nowhere else. The policy
    odoo-to-mail is always present: without mail it has no rule, which allows nothing."""
    ns = namespace_of(name)

    def policy(policy_name: str, selector: dict[str, str], types: list[str], **rules: Any) -> dict[str, Any]:
        found = _meta("NetworkPolicy", policy_name, ns, name)
        found["spec"] = {"podSelector": {"matchLabels": selector}, "policyTypes": types, **rules}
        return found

    odoo_pods, db_pods = {"sudoo.sh/role": "odoo"}, {"sudoo.sh/role": "db"}
    dns = {
        "to": [
            {
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
            }
        ],
        "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
    }
    found = [
        policy("default-deny", {}, ["Ingress", "Egress"]),
        policy("dns", {}, ["Egress"], egress=[dns]),
        policy("odoo-to-db", odoo_pods, ["Egress"],
               egress=[{"to": [{"podSelector": {"matchLabels": db_pods}}],
                        "ports": [{"protocol": "TCP", "port": POSTGRES_PORT}]}]),
        policy("db-from-odoo", db_pods, ["Ingress"],
               ingress=[{"from": [{"podSelector": {"matchLabels": odoo_pods}}],
                         "ports": [{"protocol": "TCP", "port": POSTGRES_PORT}]}]),
    ]  # fmt: skip
    mail_blocks_found, mail_port = mail or ([], 0)
    found.append(
        policy("odoo-to-mail", odoo_pods, ["Egress"],
               egress=[{"to": [{"ipBlock": {"cidr": block}} for block in mail_blocks_found],
                        "ports": [{"protocol": "TCP", "port": mail_port}]}] if mail_blocks_found else [])
    )  # fmt: skip
    if gateway_blocks:
        found.append(
            policy(
                "odoo-from-gateway",
                {**odoo_pods, "sudoo.sh/component": "server"},
                ["Ingress"],
                ingress=[
                    {
                        "from": [{"ipBlock": {"cidr": block}} for block in gateway_blocks],
                        "ports": [{"protocol": "TCP", "port": port} for port in ports],
                    }
                ],
            )
        )
    return found


# ---------------------------------------------------------------- secrets and volumes


def secrets(name: str, conf_text: str, db_password: str) -> list[dict[str, Any]]:
    ns = namespace_of(name)
    config = _meta("Secret", "odoo-config", ns, name)
    config.update(type="Opaque", stringData={"odoo.conf": conf_text})
    db = _meta("Secret", "db", ns, name)
    db.update(type="Opaque", stringData={"password": db_password})
    return [config, db]


def volumes(name: str, settings: Any) -> list[dict[str, Any]]:
    found = []
    for claim, size in (
        ("odoo-data", settings.kubernetes_files_size_gb),
        ("db-data", settings.kubernetes_db_size_gb),
        ("addons", settings.kubernetes_addons_size_gb),
    ):
        pvc = _meta("PersistentVolumeClaim", claim, namespace_of(name), name)
        pvc["spec"] = {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": f"{int(size)}Gi"}},
            **({"storageClassName": settings.kubernetes_storage_class} if settings.kubernetes_storage_class else {}),
        }
        found.append(pvc)
    return found


def config_hash(conf_text: str) -> str:
    return hashlib.sha256(conf_text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- PostgreSQL


def database(name: str, image: str, limits: Mapping[str, Any], replicas: int) -> list[dict[str, Any]]:
    ns = namespace_of(name)
    args = []
    if int(limits.get("db_max_connections") or 0):
        args = ["postgres", "-c", f"max_connections={int(limits['db_max_connections'])}"]
    container: dict[str, Any] = {
        "name": "db",
        "image": image,
        "env": [
            {"name": "POSTGRES_USER", "value": "odoo"},
            {"name": "POSTGRES_DB", "value": "postgres"},
            {"name": "PGDATA", "value": "/var/lib/postgresql/data/pgdata"},
            {"name": "POSTGRES_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db", "key": "password"}}},
        ],
        "ports": [{"name": "postgres", "containerPort": POSTGRES_PORT}],
        "volumeMounts": [{"name": "data", "mountPath": "/var/lib/postgresql/data"},
                         {"name": "run", "mountPath": "/var/run/postgresql"}],  # fmt: skip
        "readinessProbe": {
            "exec": {"command": ["pg_isready", "-q", "-h", "127.0.0.1", "-U", "odoo"]},
            "periodSeconds": 3,
            "failureThreshold": 3,
        },
        "resources": container_resources("db", limits, "128Mi"),
        "securityContext": _restricted_container(),
        **({"args": args} if args else {}),
    }
    statefulset = _meta("StatefulSet", "db", ns, name)
    statefulset["spec"] = {
        "serviceName": "db",
        "replicas": replicas,
        "selector": {"matchLabels": labels(name, "db")},
        "template": {
            "metadata": {"labels": labels(name, "db", "server")},
            "spec": {
                "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "securityContext": _pod_security(POSTGRES_UID, POSTGRES_UID),
                "terminationGracePeriodSeconds": 60,
                "containers": [container],
                "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "db-data"}},
                            {"name": "run", "emptyDir": {}}],  # fmt: skip
            },
        },
    }
    service = _meta("Service", "db", ns, name)
    service["spec"] = {
        "type": "ClusterIP",
        "selector": labels(name, "db"),
        "ports": [{"name": "postgres", "port": POSTGRES_PORT, "targetPort": POSTGRES_PORT}],
    }
    return [statefulset, service]


# ---------------------------------------------------------------- Odoo


def odoo_pod_spec(name: str, image: str, limits: Mapping[str, Any], runner: bool = False) -> dict[str, Any]:
    """The pod of the instance's Odoo; a runner (runner=True) sleeps instead, so commands run in it through exec,
    with the add-ons volume writable (the panel copies the code into it)."""
    container: dict[str, Any] = {
        "name": "odoo",
        "image": image,
        "volumeMounts": [
            {"name": "config", "mountPath": "/etc/odoo", "readOnly": True},
            {"name": "data", "mountPath": FILESTORE_ROOT},
            {"name": "addons", "mountPath": ADDONS_MOUNT, "readOnly": not runner},
            {"name": "tmp", "mountPath": "/tmp"},
        ],
        "resources": container_resources("odoo", limits, "256Mi"),
        "securityContext": _restricted_container(),
    }
    if runner:
        container["command"] = ["sleep", str(RUNNER_SECONDS)]
    else:
        container["ports"] = [
            {"name": "http", "containerPort": ODOO_HTTP},
            {"name": "gevent", "containerPort": ODOO_GEVENT},
        ]
        container["readinessProbe"] = {"tcpSocket": {"port": ODOO_HTTP}, "periodSeconds": 5, "failureThreshold": 3}
    return {
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "securityContext": _pod_security(ODOO_UID, ODOO_GID),
        "terminationGracePeriodSeconds": 30,
        "containers": [container],
        "volumes": [
            {"name": "config", "secret": {"secretName": "odoo-config", "defaultMode": 0o440}},
            {"name": "data", "persistentVolumeClaim": {"claimName": "odoo-data"}},
            {"name": "addons", "persistentVolumeClaim": {"claimName": "addons"}},
            {"name": "tmp", "emptyDir": {}},
        ],
    }


def odoo(
    name: str, image: str, limits: Mapping[str, Any], replicas: int, conf_hash: str, ports: list[int]
) -> list[dict[str, Any]]:
    """The Deployment and the Service: the Service listens on the instance's own ports (ODOO_PORT, and
    ODOO_GEVENT_PORT with workers), so the gateway reaches it the way it reaches a Docker instance."""
    ns = namespace_of(name)
    deployment = _meta("Deployment", "odoo", ns, name)
    deployment["spec"] = {
        "replicas": replicas,
        "strategy": {"type": "Recreate"},  # ReadWriteOnce volumes: never two Odoo at once
        "selector": {"matchLabels": labels(name, "odoo", "server")},
        "template": {
            "metadata": {
                "labels": labels(name, "odoo", "server"),
                "annotations": {"sudoo.sh/config-hash": conf_hash},  # a changed odoo.conf restarts Odoo
            },
            "spec": odoo_pod_spec(name, image, limits),
        },
    }
    service = _meta("Service", "odoo", ns, name)
    service["spec"] = {
        "type": "ClusterIP",
        "selector": labels(name, "odoo", "server"),
        "ports": [
            {"name": "http", "port": ports[0], "targetPort": ODOO_HTTP},
            *([{"name": "gevent", "port": ports[1], "targetPort": ODOO_GEVENT}] if len(ports) > 1 else []),
        ],
    }
    return [deployment, service]


def runner_job(name: str, job_name: str, image: str, limits: Mapping[str, Any]) -> dict[str, Any]:
    """A short-lived Job whose pod sleeps beside Odoo with its volumes: odoo -i, odoo shell, the filestore of a
    backup or a restore, the code copied in, all through exec. Deleted when done; it ends by itself otherwise."""
    job = _meta("Job", job_name, namespace_of(name), name)
    job["metadata"]["labels"] = labels(name, "odoo", "runner")
    job["spec"] = {
        "backoffLimit": 0,
        "activeDeadlineSeconds": RUNNER_SECONDS,
        "ttlSecondsAfterFinished": 300,
        "template": {
            "metadata": {"labels": labels(name, "odoo", "runner")},
            "spec": {**odoo_pod_spec(name, image, limits, runner=True), "restartPolicy": "Never"},
        },
    }
    return job


def mail_egress(env: Mapping[str, str], settings: Any, resolve: Any = None) -> tuple[list[str], int] | None:
    """The outgoing mail server the instance's Odoo may reach (see network_policies), or None. Only with
    KUBERNETES_MAIL_EGRESS on for the instance's stage ("production": production only; "all": every stage; Docker's
    staging and development e-mail goes to the mail catcher, which the Kubernetes engine does not have yet) and a
    mail server in the panel's settings (SMTP_HOST and SMTP_PORT), whatever SMTP_SECURITY is."""
    mode = settings.kubernetes_mail_egress
    if mode == "off" or not settings.smtp_host:
        return None
    if mode == "production" and (env.get("STAGE") or "development") != "production":
        return None
    blocks = mail_blocks(settings.smtp_host, resolve)
    return (blocks, int(settings.smtp_port)) if blocks else None
