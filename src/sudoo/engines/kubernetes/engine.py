"""The Kubernetes engine (beta): an instance as one namespace of a k3s (or any Kubernetes 1.30+) cluster.

How it maps the engine's steps (docs/KUBERNETES_ENGINE.md):

    apply             server-side apply of every object of manifests.py; the Odoo Service's cluster address goes
                      into the instance's .env (ODOO_HOST) for the gateway; the code is copied into the add-ons
                      volume when it changed (a runner Job, tar over exec)
    up / stop         the Deployment odoo and the StatefulSet db scaled to 1 or 0, waiting until they run or are gone
    restart           the Odoo pod deleted (its Deployment starts a new one)
    remove            the namespace deleted, with every object and volume in it
    commands          `python -m sudoo.engines.kubernetes exec|run|copy-out|logs ...` (cli.py): the same streams as
                      docker exec / compose run / docker cp / docker logs, through the API

The instance's folder also gets kubernetes.json (the namespace and what was last copied into the add-ons volume);
store.exists() accepts it as it accepts docker-compose.yml.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sudoo.config import settings
from sudoo.core.jobs import JobError

from ..base import Engine, Observation
from . import manifests
from .api import Access, Api, ApiError

if TYPE_CHECKING:
    from sudoo.core.jobs import Job

    from .terminal import KubernetesTerminal

KIND = "kubernetes"
RECORD = "kubernetes.json"
SERVICES = {"odoo": ("Deployment", "odoo"), "db": ("StatefulSet", "db")}
CLI = [sys.executable, "-m", "sudoo.engines.kubernetes"]

_API: list[tuple[tuple[Any, ...], Api]] = []
_API_LOCK = threading.Lock()


def api() -> Api:
    """The client for the configured cluster (made again when the settings that say where it is change)."""
    key = (
        settings.kubernetes_mode,
        str(settings.kubernetes_kubeconfig_path),
        settings.kubernetes_context,
        settings.kubernetes_request_timeout_seconds,
    )
    with _API_LOCK:
        if not _API or _API[0][0] != key:
            access = Access.load(
                settings.kubernetes_mode, settings.kubernetes_kubeconfig_path, settings.kubernetes_context
            )
            _API[:] = [(key, Api(access, settings.kubernetes_request_timeout_seconds))]
        return _API[0][1]


def record_path(name: str) -> Path:
    from sudoo.instances import store

    return store.folder(name) / RECORD


def read_record(name: str) -> dict[str, Any]:
    try:
        found = json.loads(record_path(name).read_text())
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}


def write_record(name: str, **values: Any) -> None:
    path = record_path(name)
    found = {**read_record(name), **values}
    path.touch(mode=0o600)
    path.write_text(json.dumps(found, indent=2))


def server_pods(client: Api, name: str, role: str) -> list[dict[str, Any]]:
    selector = f"sudoo.sh/instance={name},sudoo.sh/role={role},sudoo.sh/component=server"
    return client.list("Pod", manifests.namespace_of(name), selector)


def running_pod(client: Api, name: str, role: str) -> dict[str, Any] | None:
    """The instance's running Odoo or database pod (not one being deleted), or None."""
    for pod in server_pods(client, name, role):
        if (pod.get("status") or {}).get("phase") == "Running" and not pod["metadata"].get("deletionTimestamp"):
            return pod
    return None


def ready(pod: Mapping[str, Any]) -> bool:
    return any(
        c.get("type") == "Ready" and c.get("status") == "True"
        for c in (pod.get("status") or {}).get("conditions") or []
    )


def wait(what: str, check: Callable[[], bool], timeout: float, every: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise JobError(f"Kubernetes: {what} did not happen within {int(timeout)} s")
        time.sleep(every)


def _cpu(text: str) -> float:
    """CPU of metrics-server ("12345678n", "250m", "2") as a share of one core."""
    if text.endswith("n"):
        return int(text[:-1]) / 1e9
    if text.endswith("u"):
        return int(text[:-1]) / 1e6
    if text.endswith("m"):
        return int(text[:-1]) / 1e3
    return float(text or 0)


_UNITS = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "K": 1000, "M": 1000**2, "G": 1000**3, "k": 1000}


def quantity_bytes(text: str) -> int:
    for unit, factor in _UNITS.items():
        if text.endswith(unit):
            return int(float(text[: -len(unit)]) * factor)
    try:
        return int(float(text))
    except ValueError:
        return 0


def _since(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _uptime(started: float | None) -> str:
    if started is None:
        return ""
    seconds = max(0, int(time.time() - started))
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = seconds // size
            return f"Up {count} {unit}{'s' if count != 1 else ''}"
    return "Up less than a minute"


class KubernetesEngine(Engine):
    kind = KIND

    # ------------------------------------------------------------ definition

    def prepare_images(self, job: Job, version: str) -> None:
        job.step(f"Using Odoo {version} (each node of the cluster downloads it the first time it runs it)")

    def apply(self, job: Job, name: str, pull: bool = False) -> None:
        from sudoo.instances import resources, store

        client, env = api(), store.read_env(name)
        if env.get("EDITION") == "enterprise":
            raise JobError("Enterprise instances cannot run on the Kubernetes engine yet (beta).")
        conf_text = store.config_file(name).read_text()
        limits = resources.effective(name)
        ns = manifests.namespace_of(name)
        replicas = self._replicas(client, ns)
        ports = [int(env["ODOO_PORT"])]
        if env.get("ODOO_WORKERS", "0") not in ("", "0") and env.get("ODOO_GEVENT_PORT", "").isdigit():
            ports.append(int(env["ODOO_GEVENT_PORT"]))
        odoo_image = f"{settings.odoo_image}:{env['ODOO_VERSION']}"
        objects = [
            manifests.namespace(name, env),
            manifests.role_binding(name),
            *manifests.quota(name, settings),
            *manifests.network_policies(name, manifests.node_blocks(client.list("Node")), ports),
            *manifests.secrets(name, conf_text, env["DB_PASSWORD"]),
            *manifests.volumes(name, settings),
            *manifests.database(name, settings.postgres_image, limits, replicas["db"]),
            *manifests.odoo(name, odoo_image, limits, replicas["odoo"], manifests.config_hash(conf_text), ports),
        ]
        job.write(f"Applying {len(objects)} Kubernetes objects in namespace {ns}\n")
        for obj in objects:
            try:
                client.apply(obj)
            except ApiError as error:
                raise JobError(f"Kubernetes refused {obj['kind']} {obj['metadata']['name']}: {error}") from None
            if obj["kind"] == "RoleBinding":  # the panel's rights in the new namespace: wait until they apply
                wait("the panel's access to the namespace", lambda: self._can_read(client, ns), 30, 0.5)
        service = client.get("Service", "odoo", ns) or {}
        host = str((service.get("spec") or {}).get("clusterIP") or "")
        if host and env.get("ODOO_HOST") != host:
            store.update_env(name, ODOO_HOST=host)
        write_record(name, namespace=ns)
        self.sync_addons(job, name)

    def _replicas(self, client: Api, ns: str) -> dict[str, int]:
        """What runs now (so applying never starts or stops anything): 0 for a new instance."""
        found = {}
        for service, (kind, object_name) in SERVICES.items():
            current = client.get(kind, object_name, ns)
            found[service] = int(((current or {}).get("spec") or {}).get("replicas") or 0)
        return found

    @staticmethod
    def _can_read(client: Api, ns: str) -> bool:
        try:
            client.list("Secret", ns)
            return True
        except ApiError:
            return False

    def sync_addons(self, job: Job, name: str) -> None:
        """Copy the instance's checkout (without .git) into its add-ons volume when it changed since the last copy."""
        from sudoo.instances import store

        addons = store.addons_dir(name)
        fingerprint = _addons_fingerprint(addons)
        if read_record(name).get("addons") == fingerprint:
            return
        job.write("Copying the code into the add-ons volume\n")
        script = 'find "$1" -mindepth 1 -delete && tar -x -C "$1"'
        tar = ["tar", "-C", str(addons), "--exclude=.git", "-cf", "-", "."]
        with subprocess.Popen(tar, stdout=subprocess.PIPE) as producer:
            command = [*CLI, "run", name, "--", "sh", "-c", script, "sh", manifests.ADDONS_MOUNT]
            copied = subprocess.run(command, stdin=producer.stdout, capture_output=True, timeout=1800)
            producer.wait(timeout=60)
        if copied.returncode != 0 or producer.returncode != 0:
            detail = copied.stderr.decode(errors="replace").strip()[-300:]
            raise JobError(f"copying the code into the cluster failed: {detail}")
        write_record(name, addons=fingerprint)

    def remove_leftovers(self, job: Job, name: str) -> None:
        client, ns = api(), manifests.namespace_of(name)
        found = client.get("Namespace", ns)
        if found and (found["metadata"].get("labels") or {}).get("app.kubernetes.io/managed-by") == "sudoo":
            job.write(f"Removing the namespace {ns} of an earlier instance\n")
            client.delete("Namespace", ns)
            wait(f"the removal of namespace {ns}", lambda: client.get("Namespace", ns) is None, 600, 2)

    # ------------------------------------------------------------ lifecycle

    def _scale(self, name: str, service: str, replicas: int) -> None:
        kind, object_name = SERVICES[service]
        api().patch(kind, object_name, manifests.namespace_of(name), {"spec": {"replicas": replicas}})

    def up(
        self, job: Job, name: str, *services: str, timeout: float = 600, orphans: bool = False, recreate: bool = True
    ) -> None:
        client = api()
        wanted = list(services or ("db", "odoo"))
        try:
            for service in wanted:
                self._scale(name, service, 1)
            if "db" in wanted:
                wait("the database pod's start", lambda: self._ready(client, name, "db"), timeout, 2)
            if "odoo" in wanted:
                wait("the Odoo pod's start", lambda: running_pod(client, name, "odoo") is not None, timeout, 2)
        except ApiError as error:
            raise JobError(f"Kubernetes: {error}") from None

    @staticmethod
    def _ready(client: Api, name: str, role: str) -> bool:
        pod = running_pod(client, name, role)
        return pod is not None and ready(pod)

    def stop(self, job: Job, name: str, *services: str, timeout: float = 300, checked: bool = True) -> None:
        client = api()
        try:
            for service in services or ("odoo", "db"):
                self._scale(name, service, 0)

                def gone(role: str = service) -> bool:
                    return not server_pods(client, name, role)

                wait(f"the end of the {service} pod", gone, timeout, 2)
        except (ApiError, JobError) as error:
            if checked:
                raise JobError(f"Kubernetes: {error}") from None
            job.write(f"Stopping on Kubernetes: {error}\n")

    def restart(self, job: Job, name: str, service: str, timeout: float = 300) -> None:
        client, ns = api(), manifests.namespace_of(name)
        old = {p["metadata"]["name"] for p in server_pods(client, name, service)}
        for pod_name in old:
            client.delete("Pod", pod_name, ns)
        wait(
            f"the restart of {service}",
            lambda: (pod := running_pod(client, name, service)) is not None and pod["metadata"]["name"] not in old,
            timeout,
            2,
        )

    def remove(self, job: Job, name: str, timeout: float = 600, checked: bool = True) -> None:
        client, ns = api(), manifests.namespace_of(name)
        try:
            client.delete("Namespace", ns)
            wait(f"the removal of namespace {ns}", lambda: client.get("Namespace", ns) is None, timeout, 2)
        except (ApiError, JobError) as error:
            if checked:
                raise JobError(f"Kubernetes: {error}") from None
            job.write(f"Removing on Kubernetes: {error}\n")
        record_path(name).unlink(missing_ok=True)

    # ------------------------------------------------------------ commands

    def exec_cmd(self, name: str, service: str, *args: str, stdin: bool = False) -> list[str]:
        return [*CLI, "exec", name, service, *(["--stdin"] if stdin else []), "--", *args]

    def odoo_cmd(self, name: str, *args: str) -> list[str]:
        return [*CLI, "run", name, "--", "odoo", *args]

    def copy_out_cmd(self, name: str, service: str, path: str) -> list[str]:
        return [*CLI, "copy-out", name, service, path]

    def filestore_cmd(self, name: str, script: str, *args: str) -> list[str]:
        return [*CLI, "run", name, "--", "sh", "-c", script, "sh", *args]

    def logs_cmd(
        self, name: str, tail: int, follow: bool = False, since: str | None = None, until: str | None = None
    ) -> list[str]:
        cmd = [*CLI, "logs", name]
        cmd += ["--since", since] if since else ["--tail", str(tail)]
        if until:
            cmd += ["--until", until]
        if follow:
            cmd.append("--follow")
        return cmd

    def terminal(self) -> KubernetesTerminal:
        from .terminal import KubernetesTerminal

        return KubernetesTerminal()

    # ------------------------------------------------------------ facts

    def odoo_host(self, env: Mapping[str, str]) -> str:
        return env.get("ODOO_HOST") or "127.0.0.1"

    def observe(self, names: list[str]) -> dict[str, Observation]:
        client = api()
        found: dict[str, Observation] = {}
        for name in names:
            ns = manifests.namespace_of(name)
            try:
                pods = client.list("Pod", ns, f"sudoo.sh/instance={name},sudoo.sh/component=server")
            except ApiError:
                continue
            odoo_pod = next((p for p in pods if p["metadata"]["labels"].get("sudoo.sh/role") == "odoo"), None)
            db_pod = next((p for p in pods if p["metadata"]["labels"].get("sudoo.sh/role") == "db"), None)
            running = bool(odoo_pod and (odoo_pod.get("status") or {}).get("phase") == "Running")
            started = None
            if running and odoo_pod:
                statuses = (odoo_pod.get("status") or {}).get("containerStatuses") or [{}]
                started = _since(((statuses[0].get("state") or {}).get("running") or {}).get("startedAt"))
            cpu = memory = limit = 0.0
            try:
                for metrics in client.list("PodMetrics", ns) if pods else []:
                    for container in metrics.get("containers") or []:
                        usage = container.get("usage") or {}
                        cpu += _cpu(str(usage.get("cpu") or "0"))
                        memory += quantity_bytes(str(usage.get("memory") or "0"))
            except ApiError:
                pass  # no metrics-server: figures stay 0
            for pod in pods:
                for container in (pod.get("spec") or {}).get("containers") or []:
                    limit += quantity_bytes(
                        str(((container.get("resources") or {}).get("limits") or {}).get("memory") or "0")
                    )
            found[name] = Observation(
                running=running,
                db_running=bool(db_pod and ready(db_pod)),
                uptime=_uptime(started) if running else "",
                cpu=round(cpu * 100, 2),
                memory=int(memory),
                limit=int(limit),
                started=started,
            )
        return found


def _addons_fingerprint(addons: Path) -> str:
    """What the checkout is now: its commit, its submodules' commits and whether files changed (git status)."""
    from sudoo.core.shell import capture

    if not (addons / ".git").exists():
        return "empty"
    head = capture(["git", "-C", str(addons), "rev-parse", "-q", "--verify", "HEAD"]).strip()
    modules = capture(["git", "-C", str(addons), "submodule", "status", "--recursive"]).strip()
    changed = capture(["git", "-C", str(addons), "status", "--porcelain"]).strip()
    import hashlib

    return hashlib.sha256(f"{head}\n{modules}\n{changed}".encode()).hexdigest()[:32]
