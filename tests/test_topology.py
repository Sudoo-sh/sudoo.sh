"""The topology shared with Sudoo.sh: the snapshot built from the Docker engine and from a Kubernetes-like engine,
the privacy rules (off by default, pseudonyms, names only when asked, nothing else), the size limit, and the
signed sending loop that withdraws what was shared when it is switched off."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Iterator
from typing import Any

import pytest

from sudoo import projects
from sudoo.config import live, settings
from sudoo.hub import client
from sudoo.hub.client import Answer, HubRefused
from sudoo.instances import metrics, monitor, store
from sudoo.storage import storage
from sudoo.topology import builder, engines, privacy, share
from sudoo.topology.contract import (
    COMPONENT_KINDS,
    HEALTH,
    INSTANCE_STATUSES,
    SCHEMA,
    InstanceFacts,
    RawComponent,
    RawInstance,
    RawNode,
    RawPart,
    RawTopology,
    Replicas,
)

SHOP, TEST, IDLE = "topo-shop", "topo-shop-test", "topo-old"
PROJECT = "topo-acme"
SECRET = "s3cret-Password-123"


@pytest.fixture(autouse=True)
def docker_instances() -> Iterator[None]:
    storage().put(projects.COLLECTION, PROJECT, {"id": PROJECT, "name": "Acme ERP", "repository": "acme/erp"})
    envs = {
        SHOP: {"PROJECT": PROJECT, "STAGE": "production", "ODOO_VERSION": "18.0", "EDITION": "enterprise"},
        TEST: {"PROJECT": PROJECT, "STAGE": "staging", "ODOO_VERSION": "18.0"},
        IDLE: {"STAGE": "development", "ODOO_VERSION": "17.0"},
    }
    for name, env in envs.items():
        store.folder(name).mkdir(parents=True, exist_ok=True)
        (store.folder(name) / "docker-compose.yml").write_text("services: {}\n")
        store.write_env(
            name,
            {**env, "ODOO_PORT": "18069", "GIT_BRANCH": "feature/secret-plan", "ADMIN_PASSWORD": SECRET,
             "DOMAIN": "erp.example.com"},
        )  # fmt: skip
    monitor.STATUS.update(
        {
            SHOP: {"running": True, "db_running": True, "healthy": True, "cpu": 12.5, "memory": 700 * 1024**2},
            TEST: {"running": True, "db_running": False, "healthy": True, "cpu": 1.0, "memory": 300 * 1024**2},
            IDLE: {"running": False, "db_running": False, "healthy": False, "cpu": 0, "memory": 0},
        }
    )
    metrics.SERVER.update(cores=8, cpu_percent=35.0, memory_total_bytes=16 * 1024**3, memory_used_bytes=6 * 1024**3)
    metrics.PANEL.update(
        backend={"cpu_percent": 1.0, "memory_bytes": 200 * 1024**2, "containers": 1},
        web={"cpu_percent": 0.1, "memory_bytes": 20 * 1024**2, "containers": 1},
        mail={"cpu_percent": 0.0, "memory_bytes": 30 * 1024**2, "containers": 1},
    )
    metrics.INSTANCES.update(
        {
            SHOP: {"requests_per_minute": 120.0, "error_rate_percent": 5.0, "response_ms_p95": 180.0},
            TEST: {"requests_per_minute": 3.0, "error_rate_percent": None, "response_ms_p95": None},
        }
    )
    builder.forget()
    yield
    for name in envs:
        shutil.rmtree(store.folder(name), ignore_errors=True)
        monitor.STATUS.pop(name, None)
        metrics.INSTANCES.pop(name, None)
    metrics.SERVER.clear()
    metrics.PANEL.clear()
    storage().delete(projects.COLLECTION, PROJECT)
    storage().delete(privacy.COLLECTION, "state")
    live.change(dict.fromkeys(live.overrides()))
    engines.use(None)
    builder.forget()


def _docker(include_names: bool = False) -> dict[str, Any]:
    return builder.build(engines.DockerEngine().read(), include_names, "2.0.0")


def _by_name(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    return next(i for i in snapshot["instances"] if i["id"] == privacy.pseudonym("instance", name))


def test_docker_snapshot_follows_the_contract() -> None:
    snapshot = _docker()
    assert snapshot["schema"] == SCHEMA and snapshot["engine"] == "docker" and snapshot["names"] == "hidden"
    (node,) = snapshot["nodes"]
    assert node["role"] == "main" and node["status"] == "healthy" and node["cpu"] == {"cores": 8, "percent": 35.0}
    assert node["memory"]["percent"] == 37.5
    assert [p["kind"] for p in snapshot["panel"]["parts"]] == ["backend", "mail", "web"]
    shop = _by_name(snapshot, SHOP)
    assert shop["stage"] == "production" and shop["status"] == "online" and shop["health"] == "healthy"
    assert shop["odoo_version"] == "18.0" and shop["edition"] == "enterprise" and shop["group"]["kind"] == "compose"
    assert [c["kind"] for c in shop["components"]] == list(COMPONENT_KINDS)
    assert shop["traffic"] == {"requests_per_minute": 120.0, "errors_per_minute": 6.0, "response_ms_p95": 180.0}
    assert shop["project"] == privacy.pseudonym("project", PROJECT)
    test = _by_name(snapshot, TEST)
    assert test["health"] == "down"  # its database is not running
    assert _by_name(snapshot, IDLE)["health"] == "idle" and _by_name(snapshot, IDLE)["project"] is None
    assert snapshot["projects"] == [{"id": privacy.pseudonym("project", PROJECT), "instances": 2}]
    edges = {(e["source"], e["target"]): e for e in snapshot["edges"]}
    assert edges[("internet", "panel/web")]["requests_per_minute"] is None
    gateway, odoo, postgres = (f"{shop['id']}/{k}" for k in COMPONENT_KINDS)
    assert edges[("internet", gateway)]["requests_per_minute"] == 120.0
    assert edges[(gateway, odoo)]["errors_per_minute"] == 6.0
    assert edges[(odoo, postgres)]["estimated"] is True
    assert (odoo, "panel/mail") not in edges  # production never sends to the mail catcher
    assert (f"{test['id']}/odoo", "panel/mail") in edges
    for item in snapshot["instances"]:
        assert item["status"] in INSTANCE_STATUSES and item["health"] in HEALTH


def test_names_are_hidden_unless_asked_and_nothing_else_leaves() -> None:
    hidden = json.dumps(_docker())
    for real in (SHOP, PROJECT, "Acme ERP", settings.server_name, SECRET, "secret-plan", "erp.example.com", "18069"):
        assert real not in hidden
    assert '"name"' not in hidden
    shown = _docker(include_names=True)
    assert shown["names"] == "included" and _by_name(shown, SHOP)["name"] == SHOP
    assert shown["projects"][0]["name"] == "Acme ERP" and shown["nodes"][0]["name"] == settings.server_name
    text = json.dumps(shown)
    for never in (SECRET, "secret-plan", "erp.example.com", "18069"):
        assert never not in text


def test_pseudonyms_are_stable_and_change_after_forgetting() -> None:
    first = privacy.pseudonym("instance", SHOP)
    assert first == privacy.pseudonym("instance", SHOP) and first.startswith("i-") and len(first) == 12
    assert first != privacy.pseudonym("instance", TEST) and first[2:] != privacy.pseudonym("project", SHOP)[2:]
    privacy.forget()
    assert privacy.pseudonym("instance", SHOP) != first


class FakeKubernetes:
    """A stand-in for the Kubernetes engine: two worker nodes, namespaces, several Odoo replicas."""

    name = "kubernetes"

    def read(self) -> RawTopology:
        def instance(name: str, ready: int, status: str = "online", node: str = "worker-1") -> RawInstance:
            return RawInstance(
                name=name,
                node=node,
                status=status,
                stage="production",
                group_kind="namespace",
                group="sudoo-acme-production",
                project="acme",
                project_name="Acme",
                odoo_version="19.0",
                edition="community",
                components=(
                    RawComponent("gateway", Replicas(2, 2), healthy=True),
                    RawComponent("odoo", Replicas(ready, 3), healthy=True),
                    RawComponent("postgres", Replicas(1, 1), healthy=True),
                ),
                requests_per_minute=900.0,
                error_rate_percent=0.5,
            )

        return RawTopology(
            engine="kubernetes",
            nodes=(
                RawNode("cp-1", "control-plane", cores=4, cpu_percent=20, memory_total_bytes=8, memory_used_bytes=2),
                RawNode("worker-1", "worker", cores=16, cpu_percent=95, memory_total_bytes=64, memory_used_bytes=32),
                RawNode("worker-2", "worker", online=False),
            ),
            parts=(RawPart("backend", "cp-1"), RawPart("gateway", "cp-1", replicas=Replicas(1, 2))),
            instances=(instance("acme-main", 3), instance("acme-hotfix", 1), instance("acme-down", 0, "unknown")),
            generated_at=time.time(),
        )


def test_kubernetes_engine_uses_the_same_contract() -> None:
    engines.use(FakeKubernetes())
    snapshot = builder.snapshot()
    assert snapshot["engine"] == "kubernetes"
    statuses = {n["role"]: n["status"] for n in snapshot["nodes"] if n["role"] != "worker"}
    assert statuses == {"control-plane": "healthy"}
    assert sorted(n["status"] for n in snapshot["nodes"] if n["role"] == "worker") == ["degraded", "down"]
    parts = {p["kind"]: p for p in snapshot["panel"]["parts"]}
    assert parts["gateway"]["health"] == "degraded" and parts["gateway"]["replicas"] == {"ready": 1, "desired": 2}
    main = _by_name(snapshot, "acme-main")
    assert main["group"]["kind"] == "namespace" and main["health"] == "healthy"
    assert main["components"][1]["replicas"] == {"ready": 3, "desired": 3}
    assert _by_name(snapshot, "acme-hotfix")["health"] == "degraded"
    assert _by_name(snapshot, "acme-down")["status"] == "unknown"
    assert main["group"]["id"] == _by_name(snapshot, "acme-hotfix")["group"]["id"]  # one namespace
    assert main["traffic"]["errors_per_minute"] == 4.5


def test_snapshot_is_cached_for_a_few_seconds() -> None:
    calls: list[int] = []

    class Counting(FakeKubernetes):
        def read(self) -> RawTopology:
            calls.append(1)
            return super().read()

    engines.use(Counting())
    builder.snapshot(now=100.0)
    builder.snapshot(now=105.0)
    assert len(calls) == 1
    builder.snapshot(now=100.0 + builder.CACHE_SECONDS + 1)
    assert len(calls) == 2


def test_free_text_from_an_engine_never_reaches_the_snapshot() -> None:
    raw = RawTopology(
        engine="nomad",
        nodes=(RawNode("n", "boss"),),
        parts=(RawPart("shell", "n"),),
        instances=(
            RawInstance("x", "n", "on fire: see https://evil", "prod", "vm", "g", odoo_version="18.0; rm -rf /",
                        edition="gold", components=(RawComponent("redis", Replicas(1, 1)),)),
        ),  # fmt: skip
        generated_at=1.0,
    )
    snapshot = builder.build(raw)
    (item,) = snapshot["instances"]
    assert (
        snapshot["engine"] == "docker" and snapshot["nodes"][0]["role"] == "worker" and snapshot["panel"]["parts"] == []
    )
    assert item["status"] == "unknown" and item["stage"] == "development" and item["group"]["kind"] == "compose"
    assert item["odoo_version"] is None and item["edition"] is None and item["components"] == []


def test_a_large_snapshot_is_cut_to_fit() -> None:
    many = tuple(
        RawInstance(f"inst-{n}", "n", "online" if n % 2 else "stopped", "development", "compose", f"g{n}",
                    components=(RawComponent("odoo", Replicas(1, 1), healthy=True),), requests_per_minute=float(n))
        for n in range(400)
    )  # fmt: skip
    full = builder.build(RawTopology("docker", (RawNode("n", "main"),), (), many, 1.0))
    fitted = builder.fit(full, max_bytes=40_000)
    assert len(builder.encoded(fitted)) <= 40_000 and fitted["truncated"]
    assert fitted["omitted_instances"] == 400 - len(fitted["instances"])
    kept = {i["id"] for i in fitted["instances"]}
    assert all(i["health"] != "idle" for i in fitted["instances"])  # stopped ones go first
    assert all(e["target"].split("/")[0] in kept for e in fitted["edges"] if e["target"].startswith("i-"))


def test_sharing_is_off_by_default_and_needs_the_marketplace(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(client, "request", lambda method, path, body=None, **_: sent.append((method, path)))
    assert settings.share_topology_with_sudoo is False and settings.topology_include_names is False
    assert not share.enabled()
    share.step()
    live.change({"share_topology_with_sudoo": True})
    assert not share.enabled()  # the marketplace is off
    share.step()
    assert sent == []


def test_sending_and_withdrawing(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str, Any]] = []

    def request(method: str, path: str, body: Any = None, **_: Any) -> Answer:
        sent.append((method, path, body))
        return Answer(202 if method == "POST" else 204, {}, {"accepted": True, "next_seconds": 45} if body else None)

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(client, "sudoo_version", lambda: "2.0.0")
    live.change({"marketplace": True, "share_topology_with_sudoo": True})
    assert share.step() == 45.0
    method, path, body = sent[-1]
    assert (method, path) == ("POST", share.PATH) and body["schema"] == SCHEMA and body["names"] == "hidden"
    assert share.state()["shared"] is True
    before = privacy.pseudonym("instance", SHOP)
    live.change({"share_topology_with_sudoo": None})
    share.step()
    assert sent[-1][:2] == ("DELETE", share.PATH) and share.state()["shared"] is False
    assert privacy.pseudonym("instance", SHOP) != before  # the key was forgotten
    share.step()
    assert len(sent) == 2  # withdrawn once


def test_switching_the_marketplace_off_forgets_the_key_now_and_withdraws_when_it_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, str, Any]] = []

    def request(method: str, path: str, body: Any = None, **_: Any) -> Answer:
        sent.append((method, path, body))
        return Answer(202 if method == "POST" else 204, {}, {"accepted": True, "next_seconds": 45} if body else None)

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(client, "sudoo_version", lambda: "2.0.0")
    live.change({"marketplace": True, "share_topology_with_sudoo": True})
    share.step()
    assert share.state()["shared"] is True and privacy.has_key()
    before = privacy.pseudonym("instance", SHOP)
    sent.clear()
    live.change({"marketplace": False, "share_topology_with_sudoo": None})
    share.step()
    assert sent == []  # the hub is not contacted while the marketplace is off
    assert not privacy.has_key() and share.state()["shared"] is True  # the key is gone, the DELETE is still owed
    share.step()
    assert sent == [] and not privacy.has_key()  # and nothing is made again meanwhile
    live.change({"marketplace": True})
    share.step()
    assert [(m, p) for m, p, _ in sent] == [("DELETE", share.PATH)] and share.state()["shared"] is False
    assert privacy.pseudonym("instance", SHOP) != before


def test_the_hub_can_stop_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(method: str, path: str, body: Any = None, **_: Any) -> Answer:
        raise HubRefused(403, "Topology sharing is paused.", "topology_disabled")

    monkeypatch.setattr(client, "request", refuse)
    live.change({"marketplace": True, "share_topology_with_sudoo": True})
    assert share.step() == share.DISABLED_WAIT_SECONDS
    assert share.state()["error"] == "topology_disabled"


class KubernetesContributor:
    """What the Kubernetes engine adds for its instances (engines.contribute): its nodes, namespaces and replicas."""

    def nodes(self) -> list[RawNode]:
        return [RawNode("k3s-cp", "control-plane", cores=4, cpu_percent=10.0), RawNode("k3s-w1", "worker", cores=8)]

    def instance(self, name: str, env: dict[str, str]) -> InstanceFacts | None:
        if name != SHOP:
            return None
        return InstanceFacts(node="k3s-w1", group=f"sudoo-{name}", replicas={"odoo": Replicas(2, 3)})


def test_an_engine_contributes_nodes_namespaces_and_replicas() -> None:
    env = store.read_env(SHOP)
    store.write_env(SHOP, {**env, "ENGINE": "kubernetes"})
    engines.contribute("kubernetes", KubernetesContributor())
    try:
        snapshot = _docker()
        roles = sorted(n["role"] for n in snapshot["nodes"])
        assert roles == ["control-plane", "main", "worker"]
        shop = _by_name(snapshot, SHOP)
        assert shop["group"]["kind"] == "namespace" and shop["node"] == privacy.pseudonym("node", "k3s-w1")
        assert shop["components"][1]["replicas"] == {"ready": 2, "desired": 3} and shop["health"] == "degraded"
        assert _by_name(snapshot, TEST)["group"]["kind"] == "compose"  # still on Docker
        assert snapshot["engine"] == "docker"  # mixed: Docker and Kubernetes instances
    finally:
        engines.contribute("kubernetes", None)
