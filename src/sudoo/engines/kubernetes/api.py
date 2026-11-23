"""The Kubernetes API over plain HTTPS, with the panel's ServiceAccount token (no client library).

    Access.load()          where the API is and how the panel proves who it is: a kubeconfig file
                           (KUBERNETES_KUBECONFIG, JSON, as the installer's k3s-prepare.sh writes it; a context's
                           cluster server and certificate authority, and its user's token or client certificate) or
                           the in-cluster ServiceAccount (KUBERNETES_MODE=in-cluster)
    Api.get/list/apply/patch/delete     JSON requests; apply is server-side apply (field manager "sudoo")
    Api.exec_socket(...)   the WebSocket of a pod's exec, protocol v5.channel.k8s.io (streams.py speaks it)
    Api.log_lines(...)     a pod's log, line by line (follow keeps reading)

Every path is built from names the panel gives (instance names, fixed object names); nothing a visitor typed goes
into a path or a query without urlencode/quote. Errors raise ApiError with the API's own status and message.
"""

from __future__ import annotations

import base64
import builtins
import contextlib
import json
import os
import ssl
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

IN_CLUSTER = Path("/var/run/secrets/kubernetes.io/serviceaccount")
FIELD_MANAGER = "sudoo"
EXEC_PROTOCOL = "v5.channel.k8s.io"


class ApiError(Exception):
    """The API refused or failed a request; `status` is its HTTP status (0: not reachable)."""

    def __init__(self, message: str, status: int = 0, reason: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


class AccessError(Exception):
    """The panel's access to the cluster is not set up (the message says what to do)."""


@dataclass(frozen=True)
class Access:
    server: str  # https://host:port, no trailing slash
    ca_pem: str = ""  # the cluster's certificate authority ("": the system's)
    token: str = ""
    client_cert_pem: str = ""
    client_key_pem: str = ""
    insecure: bool = False  # only from a kubeconfig that says so (never written by the installer)

    @classmethod
    def load(cls, mode: str, kubeconfig: Path, context: str = "") -> Access:
        if mode == "in-cluster":
            host, port = os.environ.get("KUBERNETES_SERVICE_HOST"), os.environ.get("KUBERNETES_SERVICE_PORT", "443")
            if not host or not (IN_CLUSTER / "token").exists():
                raise AccessError("KUBERNETES_MODE=in-cluster, but the panel does not run in a Kubernetes pod.")
            return cls(
                server=f"https://{_host_for_url(host)}:{port}",
                ca_pem=(IN_CLUSTER / "ca.crt").read_text(),
                token=(IN_CLUSTER / "token").read_text().strip(),
            )
        try:
            config = json.loads(kubeconfig.read_text())
        except FileNotFoundError:
            raise AccessError(
                f"No kubeconfig at {kubeconfig}: run sudo sudoo-install engine kubernetes, which writes it, or set "
                "KUBERNETES_KUBECONFIG."
            ) from None
        except ValueError:
            raise AccessError(
                f"{kubeconfig} is not JSON. Write it as JSON: kubectl config view --raw --flatten -o json"
            ) from None
        return cls.from_kubeconfig(config, context)

    @classmethod
    def from_kubeconfig(cls, config: Mapping[str, Any], context: str = "") -> Access:
        def named(kind: str, name: str) -> dict[str, Any]:
            for item in config.get(kind) or []:
                if isinstance(item, dict) and item.get("name") == name:
                    return dict(item.get(kind[:-1]) or {})
            raise AccessError(f"The kubeconfig has no {kind[:-1]} named {name!r}.")

        chosen = named("contexts", context or str(config.get("current-context") or ""))
        cluster, user = named("clusters", str(chosen.get("cluster"))), named("users", str(chosen.get("user")))
        server = str(cluster.get("server") or "").rstrip("/")
        if not server.startswith("https://"):
            raise AccessError("The kubeconfig's cluster must be reached over https.")
        return cls(
            server=server,
            ca_pem=_pem(cluster.get("certificate-authority-data"), cluster.get("certificate-authority")),
            token=str(user.get("token") or "")
            or (Path(user["tokenFile"]).read_text().strip() if user.get("tokenFile") else ""),
            client_cert_pem=_pem(user.get("client-certificate-data"), user.get("client-certificate")),
            client_key_pem=_pem(user.get("client-key-data"), user.get("client-key")),
            insecure=bool(cluster.get("insecure-skip-tls-verify")),
        )

    def ssl_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(cadata=self.ca_pem or None)
        if self.insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        if self.client_cert_pem:
            # load_cert_chain reads files: a private folder in memory, removed at once
            with tempfile.TemporaryDirectory(prefix="sudoo-kube-") as folder:
                cert, key = Path(folder) / "cert.pem", Path(folder) / "key.pem"
                for path, text in ((cert, self.client_cert_pem), (key, self.client_key_pem)):
                    path.touch(mode=0o600)
                    path.write_text(text)
                context.load_cert_chain(cert, key)
        return context

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def ca_hash(self) -> str:
        """SHA-256 of the cluster's certificate authority as PEM text: what k3s puts after K10 in a join token."""
        import hashlib

        return hashlib.sha256(self.ca_pem.encode()).hexdigest()


def _pem(data: Any, file: Any) -> str:
    if data:
        return base64.b64decode(str(data)).decode()
    return Path(str(file)).read_text() if file else ""


def _host_for_url(host: str) -> str:
    return f"[{host}]" if ":" in host else host


# ---------------------------------------------------------------- paths

# (API prefix, plural, namespaced) of every kind the panel touches
KINDS: dict[str, tuple[str, str, bool]] = {
    "Namespace": ("/api/v1", "namespaces", False),
    "Node": ("/api/v1", "nodes", False),
    "Pod": ("/api/v1", "pods", True),
    "Service": ("/api/v1", "services", True),
    "Secret": ("/api/v1", "secrets", True),
    "ConfigMap": ("/api/v1", "configmaps", True),
    "PersistentVolumeClaim": ("/api/v1", "persistentvolumeclaims", True),
    "ResourceQuota": ("/api/v1", "resourcequotas", True),
    "LimitRange": ("/api/v1", "limitranges", True),
    "Deployment": ("/apis/apps/v1", "deployments", True),
    "StatefulSet": ("/apis/apps/v1", "statefulsets", True),
    "Job": ("/apis/batch/v1", "jobs", True),
    "NetworkPolicy": ("/apis/networking.k8s.io/v1", "networkpolicies", True),
    "RoleBinding": ("/apis/rbac.authorization.k8s.io/v1", "rolebindings", True),
    "PodMetrics": ("/apis/metrics.k8s.io/v1beta1", "pods", True),
    "NodeMetrics": ("/apis/metrics.k8s.io/v1beta1", "nodes", False),
}

API_VERSIONS = {
    "Deployment": "apps/v1",
    "StatefulSet": "apps/v1",
    "Job": "batch/v1",
    "NetworkPolicy": "networking.k8s.io/v1",
    "RoleBinding": "rbac.authorization.k8s.io/v1",
}


def path(kind: str, namespace: str | None = None, name: str | None = None, sub: str | None = None) -> str:
    prefix, plural, namespaced = KINDS[kind]
    found = prefix
    if namespaced and namespace:
        found += f"/namespaces/{quote(namespace, safe='')}"
    found += f"/{plural}"
    if name:
        found += f"/{quote(name, safe='')}"
    if sub:
        found += f"/{sub}"
    return found


# ---------------------------------------------------------------- the client


class Api:
    """Requests to one cluster. Thread-safe: every request opens its own connection."""

    def __init__(self, access: Access, timeout: float = 30) -> None:
        self.access = access
        self.timeout = timeout
        self._ssl = access.ssl_context()

    def request(
        self,
        method: str,
        url_path: str,
        body: Any = None,
        content_type: str = "application/json",
        query: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = self.access.server + url_path + (f"?{urlencode(query, doseq=True)}" if query else "")
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method, headers=self.access.headers())
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout, context=self._ssl) as answer:
                raw = answer.read()
        except urllib.error.HTTPError as error:
            raise _api_error(error) from None
        except (OSError, ValueError) as error:
            raise ApiError(f"The Kubernetes API at {self.access.server} could not be reached: {error}") from None
        return json.loads(raw) if raw else None

    # ------------------------------------------------------------ objects

    def get(self, kind: str, name: str, namespace: str | None = None) -> dict[str, Any] | None:
        """The object, or None when there is none."""
        try:
            return dict(self.request("GET", path(kind, namespace, name)))
        except ApiError as error:
            if error.status == 404:
                return None
            raise

    def list(self, kind: str, namespace: str | None = None, selector: str = "") -> list[dict[str, Any]]:
        query = {"labelSelector": selector} if selector else None
        found = self.request("GET", path(kind, namespace), query=query) or {}
        return list(found.get("items") or [])

    def apply(self, obj: Mapping[str, Any]) -> dict[str, Any]:
        """Server-side apply: the panel owns the fields it sets (and takes them back if someone changed them)."""
        kind, meta = str(obj["kind"]), obj["metadata"]
        url_path = path(kind, meta.get("namespace"), meta["name"])
        query = {"fieldManager": FIELD_MANAGER, "force": "true"}
        return dict(self.request("PATCH", url_path, obj, "application/apply-patch+yaml", query))

    def patch(self, kind: str, name: str, namespace: str | None, changes: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.request("PATCH", path(kind, namespace, name), changes, "application/merge-patch+json"))

    def create(self, obj: Mapping[str, Any]) -> dict[str, Any]:
        kind, meta = str(obj["kind"]), obj["metadata"]
        return dict(self.request("POST", path(kind, meta.get("namespace")), obj))

    def delete(self, kind: str, name: str, namespace: str | None = None) -> bool:
        """Delete (its dependents in the background); False when there was nothing to delete."""
        try:
            self.request("DELETE", path(kind, namespace, name), {"propagationPolicy": "Background"})
            return True
        except ApiError as error:
            if error.status == 404:
                return False
            raise

    # ------------------------------------------------------------ streams

    def exec_url(
        self, namespace: str, pod: str, container: str, argv: builtins.list[str], stdin: bool, tty: bool
    ) -> str:
        query: builtins.list[tuple[str, str]] = [("container", container)]
        query += [("command", arg) for arg in argv]
        query += [("stdin", str(stdin).lower()), ("stdout", "true"), ("stderr", str(not tty).lower())]
        query += [("tty", str(tty).lower())]
        base = self.access.server.replace("https://", "wss://", 1)
        return f"{base}{path('Pod', namespace, pod, 'exec')}?{urlencode(query)}"

    def log_lines(self, namespace: str, pod: str, container: str, query: Mapping[str, Any]) -> Iterator[str]:
        """The pod's log, a line at a time (each with its time stamp when query has timestamps=true)."""
        url = self.access.server + path("Pod", namespace, pod, "log") + "?" + urlencode(query)
        request = urllib.request.Request(url, headers=self.access.headers())
        follow = str(query.get("follow")) == "true"
        try:
            answer = urllib.request.urlopen(request, timeout=None if follow else self.timeout, context=self._ssl)
        except urllib.error.HTTPError as error:
            raise _api_error(error) from None
        except (OSError, ValueError) as error:
            raise ApiError(f"The Kubernetes API could not be reached: {error}") from None
        with contextlib.closing(answer):
            for raw in answer:
                yield raw.decode(errors="replace").rstrip("\n")


def _api_error(error: urllib.error.HTTPError) -> ApiError:
    try:
        status = json.loads(error.read() or b"{}")
    except ValueError:
        status = {}
    message = str(status.get("message") or f"HTTP {error.code}")
    return ApiError(message, error.code, str(status.get("reason") or ""))
