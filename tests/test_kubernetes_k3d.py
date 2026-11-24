"""The Kubernetes engine against a real cluster (k3d or any k3s), through the panel's own restricted access.

Skipped unless SUDOO_K3D_TESTS=1. SUDOO_K3D_KUBECONFIG is the panel's kubeconfig (JSON, the sudoo-panel
ServiceAccount's token, as the installer's k3s-prepare.sh writes it); the cluster needs that access applied
(`bash k3s-prepare.sh print-manifests | kubectl apply -f -`, with k3s-prepare.sh from the Sudoo-sh/sudoo-installer
repository, or from a built installer: the lines between `cat <<'SUDOO_K3S_PREPARE'` and `SUDOO_K3S_PREPARE`) and its default network policy controller.
Two throw-away instances are created and their namespaces deleted afterwards. The first run downloads odoo:18.0 and
postgres:16 on the cluster's nodes.

    SUDOO_K3D_TESTS=1 SUDOO_K3D_KUBECONFIG=/path/kubeconfig.json uv run pytest tests/test_kubernetes_k3d.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from sudoo import engines
from sudoo.config import settings as _REAL
from sudoo.engines.kubernetes import cluster, engine
from sudoo.engines.kubernetes.api import Access, Api, ApiError
from sudoo.instances import store

pytestmark = pytest.mark.skipif(
    os.environ.get("SUDOO_K3D_TESTS") != "1", reason="needs a k3s cluster (SUDOO_K3D_TESTS=1)"
)

NAMES = ("k3dtest-a", "k3dtest-b")


class Job:
    def write(self, text: str) -> None:
        print(text, end="")

    def step(self, text: str) -> None:
        print(f"step: {text}")


@pytest.fixture(scope="module", autouse=True)
def instances() -> Iterator[None]:
    config = Path(os.environ["SUDOO_K3D_KUBECONFIG"])
    os.environ["KUBERNETES_KUBECONFIG"] = str(config)  # the engine's command line (a subprocess) reads it
    client = Api(Access.load("kubeconfig", config))
    kept = engine.api, cluster.api
    engine.api = cluster.api = lambda: client  # type: ignore[assignment]
    for index, name in enumerate(NAMES):
        folder = store.folder(name)
        (folder / "config").mkdir(parents=True, exist_ok=True)
        (folder / "addons").mkdir(exist_ok=True)
        store.write_env(
            name,
            {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": name.replace("-", "_"), "DB_PASSWORD": "pw",
             "ODOO_PORT": 28600 + index, "GATEWAY_PORT": 8600 + index, "STAGE": "development", "ENGINE": "kubernetes"},
        )  # fmt: skip
        store.config_file(name).write_text("[options]\ndb_host = db\ndb_user = odoo\ndb_password = pw\n")
    kube = engines.named("kubernetes")
    for name in NAMES:
        kube.apply(Job(), name)  # type: ignore[arg-type]
        kube.up(Job(), name, "db", timeout=600)  # type: ignore[arg-type]
    yield
    for name in NAMES:
        kube.remove(Job(), name, checked=False)  # type: ignore[arg-type]
        shutil.rmtree(store.folder(name), ignore_errors=True)
    engine.api, cluster.api = kept  # type: ignore[assignment]
    os.environ.pop("KUBERNETES_KUBECONFIG", None)


def _run(cmd: list[str], data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    found = subprocess.run(cmd, input=data, capture_output=True, timeout=900)
    print(cmd[3:], found.returncode, found.stdout[-500:], found.stderr[-500:])  # shown when a test fails
    return found


def test_exec_runs_in_the_database_and_reads_standard_input() -> None:
    kube = engines.of(NAMES[0])
    assert (
        _run(
            kube.exec_cmd(NAMES[0], "db", "psql", "-U", "odoo", "-d", "postgres", "-tAc", "SELECT 40 + 2")
        ).stdout.strip()
        == b"42"
    )
    fed = _run(
        kube.exec_cmd(NAMES[0], "db", "psql", "-U", "odoo", "-d", "postgres", "-tA", stdin=True), b"SELECT 'fed';\n"
    )
    assert fed.returncode == 0 and fed.stdout.strip() == b"fed"  # the input's end reached psql (v5 close)
    assert _run(kube.exec_cmd(NAMES[0], "db", "sh", "-c", "exit 7")).returncode == 7


def test_a_runner_job_has_odoo_its_volumes_and_its_database() -> None:
    kube = engines.of(NAMES[0])
    found = _run(kube.odoo_cmd(NAMES[0], "--version"))
    assert found.returncode == 0 and b"Odoo Server 18.0" in found.stdout
    reach = _run(
        kube.filestore_cmd(NAMES[0], "id -u && python3 -c \"import socket; socket.create_connection(('db', 5432), 5)\"")
    )
    assert reach.returncode == 0 and reach.stdout.strip() == b"100"  # Odoo's own user, never root


def test_another_instance_cannot_reach_this_database() -> None:
    kube = engines.of(NAMES[1])
    probe = f"pg_isready -q -h db.sudoo-{NAMES[0]}.svc.cluster.local -p 5432 -t 5"
    assert _run(kube.exec_cmd(NAMES[1], "db", "sh", "-c", probe)).returncode != 0
    own = "pg_isready -q -h 127.0.0.1 -p 5432 -t 5"
    assert _run(kube.exec_cmd(NAMES[1], "db", "sh", "-c", own)).returncode == 0


def test_the_panel_cannot_leave_its_namespaces() -> None:
    client = engine.api()
    with pytest.raises(ApiError) as refused:
        client.list("Secret", "kube-system")
    assert refused.value.status == 403
    with pytest.raises(ApiError):
        client.create({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "not-sudoo"}})
    with pytest.raises(ApiError):
        client.create(
            {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "x", "namespace": "kube-system"},
             "stringData": {"a": "b"}}
        )  # fmt: skip
    assert cluster.nodes() and all(node["version"] for node in cluster.nodes())


def _reaches(kube: object, name: str, host: str, port: int) -> bool:
    probe = f"python3 -c \"import socket; socket.create_connection(('{host}', {port}), 5)\""
    return _run(kube.filestore_cmd(name, probe)).returncode == 0  # type: ignore[attr-defined]


def test_outgoing_mail_opens_only_the_configured_server_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cluster's own API server stands in for a mail server (any TCP listener the nodes reach would do): its
    node address on 6443 is closed by default, opens with KUBERNETES_MAIL_EGRESS, and stays closed on other ports
    and for the other instance. SUDOO_K3D_MAIL_TARGET=<ip>:<port> chooses another listener."""
    name, other = NAMES
    kube = engines.of(name)
    target = os.environ.get("SUDOO_K3D_MAIL_TARGET", "")
    if not target:
        addresses = [
            a["address"]
            for node in engine.api().list("Node")
            for a in (node.get("status") or {}).get("addresses") or []
            if a.get("type") == "InternalIP" and ":" not in a["address"]
        ]
        assert addresses, "no node address to use as the mail server; set SUDOO_K3D_MAIL_TARGET"
        target = f"{addresses[0]}:6443"
    host, port = target.rsplit(":", 1)
    assert not _reaches(kube, name, host, int(port))  # default deny, mail egress off

    monkeypatch.setattr(engine, "settings", _Opened(kubernetes_mail_egress="all", smtp_host=host, smtp_port=int(port)))
    engines.named("kubernetes").apply(Job(), name)  # type: ignore[arg-type]
    time.sleep(5)  # the policy controller needs a moment
    assert _reaches(kube, name, host, int(port))  # the configured server and port
    assert not _reaches(kube, name, host, int(port) + 1)  # nothing else on that server
    assert not _reaches(kube, name, "198.51.100.7", int(port))  # nor any other server
    assert not _reaches(engines.of(other), other, host, int(port))  # nor an instance that was not applied again

    monkeypatch.setattr(engine, "settings", _Opened(kubernetes_mail_egress="off"))
    engines.named("kubernetes").apply(Job(), name)  # type: ignore[arg-type]
    time.sleep(5)
    assert not _reaches(kube, name, host, int(port))  # closed again


class _Opened:
    def __init__(self, **values: object) -> None:
        self._values = values

    def __getattr__(self, name: str) -> object:
        return self._values[name] if name in self._values else getattr(_REAL, name)
