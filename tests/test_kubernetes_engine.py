"""The Kubernetes engine (beta) against a fake API: the objects of an instance and their isolation, applying,
starting and stopping, the commands it runs, the join command, the API endpoints and the gateway's route.

The same paths against a real k3d cluster: tests/test_kubernetes_k3d.py (SUDOO_K3D_TESTS=1)."""

from __future__ import annotations

import base64
import copy
import json
import shutil
from collections.abc import Iterator
from typing import Any

import pytest
from flask.testing import FlaskClient

from sudoo import engines
from sudoo.config import settings
from sudoo.engines.kubernetes import cluster, engine, manifests, streams
from sudoo.engines.kubernetes.api import Access, AccessError, ApiError
from sudoo.instances import gateway, gateway_service, store

NAME = "kube1"
NS = f"sudoo-{NAME}"
Client = tuple[FlaskClient, dict[str, str]]


class FakeApi:
    """The objects the panel applied, and pods that follow the replicas it asks for."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str | None, str], dict[str, Any]] = {}
        self.created: list[dict[str, Any]] = []
        self.deleted: list[tuple[str, str]] = []
        self.access = Access(server="https://k3s.test:6443", ca_pem="CA PEM\n", token="t")
        self.nodes = [
            {"metadata": {"name": "server-0", "labels": {"node-role.kubernetes.io/control-plane": "true"},
                          "creationTimestamp": "2026-11-23T10:00:00Z"},
             "spec": {"podCIDR": "10.42.0.0/24"},
             "status": {"conditions": [{"type": "Ready", "status": "True"}],
                        "allocatable": {"cpu": "4", "memory": "8Gi"},
                        "nodeInfo": {"kubeletVersion": "v1.36.6+k3s1"},
                        "addresses": [{"type": "InternalIP", "address": "10.0.0.2"}]}},
            {"metadata": {"name": "agent-0", "labels": {}}, "spec": {"podCIDRs": ["10.42.1.0/24"]},
             "status": {"conditions": [{"type": "Ready", "status": "False"}],
                        "capacity": {"cpu": "2", "memory": "4096Mi"}, "nodeInfo": {"kubeletVersion": "v1.36.6+k3s1"}}},
        ]  # fmt: skip

    def get(self, kind: str, name: str, namespace: str | None = None) -> dict[str, Any] | None:
        found = self.objects.get((kind, namespace, name))
        return copy.deepcopy(found) if found else None

    def list(self, kind: str, namespace: str | None = None, selector: str = "") -> list[dict[str, Any]]:
        if kind == "Node":
            return self.nodes
        if kind == "NodeMetrics":
            return [{"metadata": {"name": "server-0"}, "usage": {"cpu": "500m", "memory": "2Gi"}}]
        if kind == "PodMetrics":
            return [{"containers": [{"usage": {"cpu": "250000000n", "memory": "300Mi"}}]}]
        if kind == "Pod":
            wanted = dict(part.split("=") for part in selector.split(",")) if selector else {}
            return [
                p for p in self._pods(namespace) if all(p["metadata"]["labels"].get(k) == v for k, v in wanted.items())
            ]
        return [o for (k, ns, _), o in self.objects.items() if k == kind and ns == namespace]

    def _pods(self, namespace: str | None) -> list[dict[str, Any]]:
        pods = []
        for kind, role in (("Deployment", "odoo"), ("StatefulSet", "db")):
            found = self.objects.get((kind, namespace, role))
            if found and found["spec"]["replicas"]:
                labels = found["spec"]["template"]["metadata"]["labels"]
                pods.append({
                    "metadata": {"name": f"{role}-0", "namespace": namespace, "labels": labels},
                    "spec": found["spec"]["template"]["spec"],
                    "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                               "containerStatuses": [{"state": {"running": {"startedAt": "2026-11-23T10:00:00Z"}}}]},
                })  # fmt: skip
        return pods

    def apply(self, obj: dict[str, Any]) -> dict[str, Any]:
        stored = copy.deepcopy(obj)
        if obj["kind"] == "Service":
            stored["spec"]["clusterIP"] = "10.43.0.17" if obj["metadata"]["name"] == "odoo" else "10.43.0.18"
        self.objects[(obj["kind"], obj["metadata"].get("namespace"), obj["metadata"]["name"])] = stored
        return stored

    def patch(self, kind: str, name: str, namespace: str | None, changes: dict[str, Any]) -> dict[str, Any]:
        found = self.objects[(kind, namespace, name)]
        found["spec"].update(changes["spec"])
        return found

    def create(self, obj: dict[str, Any]) -> dict[str, Any]:
        self.created.append(obj)
        return obj

    def delete(self, kind: str, name: str, namespace: str | None = None) -> bool:
        self.deleted.append((kind, name))
        if kind == "Namespace":
            for key in [k for k in self.objects if k[1] == name or (k[0] == "Namespace" and k[2] == name)]:
                del self.objects[key]
        return True


class Job:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.lines.append(text)

    def step(self, text: str) -> None:
        self.lines.append(f"step: {text}")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    found = FakeApi()
    monkeypatch.setattr(engine, "api", lambda: found)
    monkeypatch.setattr(cluster, "api", lambda: found)
    monkeypatch.setattr(engine, "wait", lambda what, check, timeout, every=1.0: None if check() else pytest.fail(what))
    return found


@pytest.fixture
def instance() -> Iterator[str]:
    folder = store.folder(NAME)
    (folder / "config").mkdir(parents=True, exist_ok=True)
    (folder / "addons").mkdir(exist_ok=True)
    store.write_env(
        NAME,
        {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": NAME, "DB_PASSWORD": "pw", "ODOO_PORT": 28555,
         "GATEWAY_PORT": 8555, "STAGE": "development", "ENGINE": "kubernetes"},
    )  # fmt: skip
    store.config_file(NAME).write_text("[options]\ndb_host = db\ndb_password = pw\n")
    engine.write_record(NAME, addons="empty")  # nothing to copy: the add-ons folder is not a repository
    yield NAME
    shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------- the objects and their isolation


def test_the_namespace_enforces_pod_security_restricted() -> None:
    found = manifests.namespace(NAME, {"STAGE": "production", "PROJECT": "acme"})
    labels = found["metadata"]["labels"]
    assert found["metadata"]["name"] == NS
    assert labels["pod-security.kubernetes.io/enforce"] == "restricted"
    assert labels["app.kubernetes.io/managed-by"] == "sudoo"
    assert labels["sudoo.sh/stage"] == "production" and labels["sudoo.sh/project"] == "acme"
    binding = manifests.role_binding(NAME)
    assert binding["roleRef"]["name"] == "sudoo-instance" and binding["metadata"]["namespace"] == NS


def test_network_policies_deny_by_default_and_open_only_the_instance_s_own_paths() -> None:
    blocks = manifests.node_blocks(FakeApi().nodes)
    assert blocks == ["10.42.0.0/32", "10.42.0.1/32", "10.42.1.0/32", "10.42.1.1/32"]
    policies = {p["metadata"]["name"]: p["spec"] for p in manifests.network_policies(NAME, blocks, [28555])}
    assert policies["default-deny"] == {"podSelector": {"matchLabels": {}}, "policyTypes": ["Ingress", "Egress"]}
    # the database: only from this namespace's Odoo pods, on 5432 (no namespaceSelector: never another namespace)
    db_from = policies["db-from-odoo"]["ingress"][0]
    assert db_from["from"] == [{"podSelector": {"matchLabels": {"sudoo.sh/role": "odoo"}}}]
    assert db_from["ports"] == [{"protocol": "TCP", "port": 5432}]
    assert policies["odoo-to-db"]["egress"][0]["to"] == [{"podSelector": {"matchLabels": {"sudoo.sh/role": "db"}}}]
    gateway_in = policies["odoo-from-gateway"]["ingress"][0]
    assert [b["ipBlock"]["cidr"] for b in gateway_in["from"]] == blocks
    assert gateway_in["ports"] == [{"protocol": "TCP", "port": 28555}]
    assert all("namespaceSelector" not in str(p) or name == "dns" for name, p in policies.items())


def test_pods_run_restricted_without_a_service_account_token() -> None:
    limits = {"odoo_memory_limit": "2g", "odoo_cpu_limit": 1.5}
    deployment, service = manifests.odoo(NAME, "odoo:18.0", limits, 1, "abc", [28555, 28556])
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True and pod["securityContext"]["runAsUser"] == 100
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}, "runAsNonRoot": True,
    }  # fmt: skip
    assert container["resources"]["limits"] == {"memory": str(2 * 1024**3), "cpu": "1500m"}
    assert {"name": "addons", "mountPath": "/mnt/extra-addons", "readOnly": True} in container["volumeMounts"]
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    assert [p["port"] for p in service["spec"]["ports"]] == [28555, 28556] and service["spec"]["type"] == "ClusterIP"
    runner = manifests.runner_job(NAME, "odoo-run-1", "odoo:18.0", {})
    runner_pod = runner["spec"]["template"]["spec"]
    assert runner_pod["containers"][0]["command"][0] == "sleep" and runner_pod["restartPolicy"] == "Never"
    assert runner["spec"]["template"]["metadata"]["labels"]["sudoo.sh/role"] == "odoo"  # reaches its database
    statefulset, _ = manifests.database(NAME, "postgres:16", {"db_max_connections": 80}, 0)
    db_pod = statefulset["spec"]["template"]["spec"]
    assert db_pod["securityContext"]["runAsUser"] == 999 and db_pod["automountServiceAccountToken"] is False
    assert db_pod["containers"][0]["args"] == ["postgres", "-c", "max_connections=80"]


def test_quota_and_limits_come_from_the_settings() -> None:
    resource_quota, limit_range = manifests.quota(NAME, settings)
    hard = resource_quota["spec"]["hard"]
    assert hard["limits.memory"] == f"{settings.kubernetes_quota_memory_mb}Mi"
    assert hard["services.nodeports"] == "0" and hard["services.loadbalancers"] == "0"
    assert limit_range["spec"]["limits"][0]["default"]["memory"] == f"{settings.kubernetes_container_memory_mb}Mi"


# ---------------------------------------------------------------- applying, starting, stopping


def test_apply_writes_every_object_and_the_service_address(fake: FakeApi, instance: str) -> None:
    kube = engines.named("kubernetes")
    kube.apply(Job(), instance)  # type: ignore[arg-type]
    kinds = {key[0] for key in fake.objects}
    assert kinds >= {"Namespace", "RoleBinding", "ResourceQuota", "LimitRange", "NetworkPolicy", "Secret",
                     "PersistentVolumeClaim", "StatefulSet", "Deployment", "Service"}  # fmt: skip
    secret = fake.objects[("Secret", NS, "odoo-config")]
    assert "db_password = pw" in secret["stringData"]["odoo.conf"]
    assert fake.objects[("Deployment", NS, "odoo")]["spec"]["replicas"] == 0  # applying starts nothing
    assert store.read_env(instance)["ODOO_HOST"] == "10.43.0.17"
    assert kube.odoo_host(store.read_env(instance)) == "10.43.0.17"
    assert store.exists(instance) and instance in store.names()
    assert gateway.odoo_host(store.read_env(instance)) == "10.43.0.17"

    kube.up(Job(), instance)  # type: ignore[arg-type]
    assert fake.objects[("Deployment", NS, "odoo")]["spec"]["replicas"] == 1
    kube.apply(Job(), instance)  # type: ignore[arg-type]
    assert fake.objects[("StatefulSet", NS, "db")]["spec"]["replicas"] == 1  # applying keeps what runs
    seen = kube.observe([instance])[instance]
    assert seen.running and seen.db_running and seen.uptime.startswith("Up ")
    assert seen.cpu == 25.0 and seen.memory == 300 * 1024**2

    kube.stop(Job(), instance, "odoo", checked=False)  # type: ignore[arg-type]
    assert fake.objects[("Deployment", NS, "odoo")]["spec"]["replicas"] == 0
    kube.remove(Job(), instance)  # type: ignore[arg-type]
    assert ("Namespace", NS) in fake.deleted


def test_enterprise_is_refused(fake: FakeApi, instance: str) -> None:
    from sudoo.core.jobs import JobError

    store.update_env(instance, EDITION="enterprise")
    with pytest.raises(JobError, match="Enterprise"):
        engines.named("kubernetes").apply(Job(), instance)  # type: ignore[arg-type]


def test_commands_go_through_the_engine_s_command_line(instance: str) -> None:
    kube = engines.of(instance)
    assert kube.kind == "kubernetes"
    base = engine.CLI
    assert kube.exec_cmd(instance, "db", "psql", "-c", "SELECT 1") == [*base, "exec", NAME, "db", "--", "psql", "-c",
                                                                        "SELECT 1"]  # fmt: skip
    assert kube.exec_cmd(instance, "db", "psql", stdin=True)[len(base) :] == [
        "exec",
        "--stdin",
        NAME,
        "db",
        "--",
        "psql",
    ]
    assert kube.odoo_cmd(instance, "-i", "base")[len(base) :] == ["run", NAME, "--", "odoo", "-i", "base"]
    assert kube.copy_out_cmd(instance, "odoo", "/var/lib/odoo/filestore/x")[len(base) :] == [
        "copy-out", NAME, "odoo", "/var/lib/odoo/filestore/x"
    ]  # fmt: skip
    assert kube.logs_cmd(instance, 20, follow=True)[len(base) :] == ["logs", NAME, "--tail", "20", "--follow"]
    from sudoo.instances import backups, logs, odoo

    assert odoo.run_cmd(instance, "shell")[len(base) :] == ["run", NAME, "--", "odoo", "shell"]
    assert logs.odoo_log_command(instance, 5)[len(base) :] == ["logs", NAME, "--tail", "5"]
    assert backups.engines.of(instance).kind == "kubernetes"


def test_the_command_line_runs_exec_in_the_right_pod(
    fake: FakeApi, instance: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo.engines.kubernetes import cli

    fake.apply(manifests.database(NAME, "postgres:16", {}, 1)[0])
    ran: list[tuple[str, str, str, list[str], bool]] = []

    async def run(api: Any, namespace: str, pod: str, container: str, argv: list[str], stdin: Any, *_: Any) -> int:
        ran.append((namespace, pod, container, argv, stdin is not None))
        return 3

    monkeypatch.setattr(streams, "run", run)
    assert cli.main(["exec", NAME, "db", "--", "psql", "-c", "SELECT 1"]) == 3
    assert ran == [(NS, "db-0", "db", ["psql", "-c", "SELECT 1"], False)]
    assert cli.main(["exec", NAME, "odoo", "--", "true"]) == 1  # Odoo does not run: refused, with the reason
    assert cli.main(["exec", "../x", "db", "--", "true"]) == 2


def test_exit_codes_of_the_exec_status() -> None:
    assert streams.exit_code({"status": "Success"}) == 0
    failure = {"status": "Failure", "details": {"causes": [{"reason": "ExitCode", "message": "7"}]}}
    assert streams.exit_code(failure) == 7
    assert streams.exit_code({"status": "Failure", "message": "pod not found"}) == 1


# ---------------------------------------------------------------- access, nodes and joining


def test_access_comes_from_a_json_kubeconfig() -> None:
    config = {
        "current-context": "sudoo",
        "contexts": [{"name": "sudoo", "context": {"cluster": "k", "user": "u"}}],
        "clusters": [{"name": "k", "cluster": {"server": "https://127.0.0.1:6443/",
                                                "certificate-authority-data": base64.b64encode(b"CA").decode()}}],
        "users": [{"name": "u", "user": {"token": "secret-token"}}],
    }  # fmt: skip
    found = Access.from_kubeconfig(config)
    assert found.server == "https://127.0.0.1:6443" and found.ca_pem == "CA"
    assert found.headers() == {"Authorization": "Bearer secret-token"}
    with pytest.raises(AccessError):
        Access.from_kubeconfig({**config, "clusters": [{"name": "k", "cluster": {"server": "http://x"}}]})
    with pytest.raises(AccessError):
        Access.from_kubeconfig(config, "other")


def test_nodes_and_the_join_command(fake: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    found = cluster.nodes()
    assert [n["name"] for n in found] == ["server-0", "agent-0"]
    assert found[0]["roles"] == ["control-plane"] and found[0]["ready"] and found[0]["cpu"] == 4
    assert found[0]["memory_bytes"] == 8 * 1024**3 and found[0]["cpu_used"] == 0.5
    assert found[1]["roles"] == ["agent"] and not found[1]["ready"] and found[1]["cpu_used"] is None

    with pytest.raises(cluster.JoinError, match="KUBERNETES_JOIN_URL"):
        cluster.join_command()
    monkeypatch.setattr(cluster, "settings", _Settings(kubernetes_join_url="https://10.0.0.2:6443"))
    joined = cluster.join_command()
    secret = fake.created[-1]
    assert secret["type"] == "bootstrap.kubernetes.io/token" and secret["metadata"]["namespace"] == "kube-system"
    data = {k: base64.b64decode(v).decode() for k, v in secret["data"].items()}
    assert data["auth-extra-groups"] == cluster.TOKEN_GROUP and data["expiration"].endswith("Z")
    import hashlib

    token = f"K10{hashlib.sha256(b'CA PEM' + b'\n').hexdigest()}::{data['token-id']}.{data['token-secret']}"
    assert joined["command"] == (
        "curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION='v1.36.6+k3s1' K3S_URL='https://10.0.0.2:6443' "
        f"K3S_TOKEN='{token}' sh -s - agent"
    )


class _Settings:
    def __init__(self, **values: Any) -> None:
        self._values = values

    def __getattr__(self, name: str) -> Any:
        return self._values[name] if name in self._values else getattr(settings, name)


# ---------------------------------------------------------------- the gateway and the API


def test_the_gateway_service_takes_an_ip_address_only() -> None:
    edge = gateway_service.Edge("127.0.0.1", True, None)
    values = {key: getattr(settings, key) for key in gateway_service.ROUTED_SETTINGS}
    entry = {"gateway_port": 8555, "odoo_port": 28555, "switched_off": False, "odoo_host": "10.43.0.17"}
    good = json.loads(gateway_service.routes_document("service", edge, values, {NAME: entry}))
    assert gateway_service.parse_routes(good).instances[NAME][1] == gateway.Route(28555, False, None, "10.43.0.17")
    for bad in ("db.example.com", "10.43.0.17; rm", ""):
        with pytest.raises(ValueError):
            gateway_service.parse_routes({**good, "instances": {NAME: {**entry, "odoo_host": bad}}})


def test_the_api_while_the_beta_is_off(app: Any, admin: Client) -> None:
    client, headers = admin
    found = client.get("/api/v1/kubernetes").get_json()["data"]
    assert found["enabled"] is False and found["engines"] == ["docker"] and found["nodes"] == []
    assert client.post("/api/v1/kubernetes/join-command", json={}, headers=headers).status_code == 409
    plan = client.get("/api/v1/instance-plan?version=18.0&edition=community").get_json()["data"]
    assert plan["engines"] == ["docker"]
    refused = client.post(
        "/api/v1/instances", json={"version": "18.0", "edition": "community", "engine": "kubernetes"}, headers=headers
    )
    assert refused.status_code == 400 and "engine" in refused.get_json()["detail"]


def test_an_api_error_carries_its_status() -> None:
    error = ApiError("forbidden", 403, "Forbidden")
    assert error.status == 403 and error.reason == "Forbidden"
