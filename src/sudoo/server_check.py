"""The server check: what this server has, and the engine recommended for it (the setup page, and Settings).

Measured here, from inside the panel's container, every time it is asked for (at most every few seconds):

- memory: MemTotal of the server's own /proc/meminfo (compose.yaml binds it at /host/proc; instances/monitor.py);
- processors: the processors this process may use;
- free disk: of ODOO_ROOT (instances, databases, backups);
- the system: Docker's own description of the server (`docker info`), else what the installer found;
- ports 80 and 443: listening sockets in /proc/net/tcp and tcp6 (the panel shares the server's network).

The virtualisation (a virtual machine, a container such as LXC or OpenVZ) cannot be seen from inside a container:
it comes from what the installer found (<ODOO_ROOT>/manager/installer/server-check, KEY=value), "unknown" without it.

The engine is chosen by need, as the installer does (sudoo-install.sh, recommend_engine): the figures are only a
minimum check (k3s's 2 cores and 2 GB plus room for Odoo, from engine_figures.json, shown as "to be measured" until
real figures arrive; not a container; Ubuntu 22.04+ or Debian 12+), and three answers decide: more servers later,
stronger separation between clients or teams, one person or a team. Docker is recommended unless the answers point
to Kubernetes (easier to grow to more servers, stronger isolation). Neither is high availability, which needs three
servers and replicated storage (a later phase). While the Kubernetes engine is beta Docker stays pre-selected:
K8S_STABLE (False) is the one line to change when a release marks it stable; ENGINE_K8S_DEFAULT does the same on one
panel.

Nothing here changes anything: changing the engine is the installer's job (`sudo sudoo-install engine kubernetes`),
never the browser's.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from sudoo.config import settings

K8S_STABLE = False
"""False while the Kubernetes engine is beta: Docker is pre-selected everywhere. Change to True (one line, with the
installer's K8S_STABLE) when a release marks it stable."""

FIGURES_FILE = Path(__file__).with_name("engine_figures.json")
NEEDS = ("grow", "separation", "team")
"""The three questions: more servers later, stronger separation between clients or teams, a team runs it."""
ENGINES = ("docker", "kubernetes")
ENGINE_GROUP = "sudoo.engines"
"""Entry points of engines other than Docker: a panel package that brings the Kubernetes engine registers
"kubernetes" here. This release has none, so the instances run on Docker whatever ENGINE says."""

CONTAINERS = {"lxc", "lxc-libvirt", "openvz", "systemd-nspawn", "docker", "podman", "rkt", "wsl", "proot", "pouch"}
CONTAINERS |= {"container-other"}

HOST_PROC = Path("/host/proc")
CACHE_SECONDS = 5.0
_cache: tuple[float, dict[str, Any]] | None = None
_lock = threading.Lock()


def _installer_dir() -> Path:
    return settings.manager_dir / "installer"


def key_values(path: Path) -> dict[str, str]:
    """A KEY=value file the installer wrote (server-check, install-code); {} when it is not there. Values are data."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and re.fullmatch(r"[A-Z][A-Z0-9_]{0,40}", key):
            values[key] = value.strip()[:300]
    return values


def installer_record() -> dict[str, str]:
    return key_values(_installer_dir() / "server-check")


def _proc(name: str) -> Path:
    mounted = HOST_PROC / name
    return mounted if mounted.is_file() else Path("/proc") / name


def memory_mb() -> int:
    try:
        with open(_proc("meminfo")) as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def processors() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def free_disk_gb() -> int:
    path = Path(settings.odoo_root)
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return shutil.disk_usage(path).free // 1024**3
    except OSError:
        return 0


def _docker_system() -> str:
    from sudoo.core.shell import capture

    try:
        return capture(["docker", "info", "--format", "{{json .OperatingSystem}}"], timeout=10).strip().strip('"')
    except Exception:  # noqa: BLE001 - Docker unreachable: the installer's record answers
        return ""


def parse_system(name: str) -> tuple[str, str]:
    """("ubuntu", "24.04") from "Ubuntu 24.04.3 LTS", ("debian", "12") from "Debian GNU/Linux 12 (bookworm)"."""
    match = re.match(r"\s*(Ubuntu)\s+(\d+\.\d+)", name) or re.match(r"\s*(Debian)\D*?(\d+)", name)
    return (match.group(1).lower(), match.group(2)) if match else ("", "")


def system_supported(os_id: str, version: str) -> bool:
    try:
        number = float(version)
    except ValueError:
        return False
    return (os_id == "ubuntu" and number >= 22.04) or (os_id == "debian" and number >= 12)


def listening_ports() -> set[int]:
    """The TCP ports something on the server listens on (state 0A in /proc/net/tcp and tcp6)."""
    ports: set[int] = set()
    for name in ("tcp", "tcp6"):
        try:
            lines = Path("/proc/net", name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 3 and fields[3] == "0A":
                ports.add(int(fields[1].rsplit(":", 1)[1], 16))
    return ports


def engines_available() -> list[str]:
    found = {entry.name for entry in entry_points(group=ENGINE_GROUP)}
    return ["docker", *(name for name in ENGINES[1:] if name in found)]


def memory_gb(mb: int) -> int:
    return (mb + 512) // 1024


def figures() -> dict[str, Any]:
    """The engine figures (engine_figures.json) and the minimum for Kubernetes they make."""
    data = json.loads(FIGURES_FILE.read_text())
    return {
        "min_cpus": int(data["k3s_min_cpus"]),
        "min_memory_mb": int(data["k3s_min_memory_mb"]) + int(data["odoo_room_memory_mb"]),
        "k3s_min_memory_mb": int(data["k3s_min_memory_mb"]),
        "odoo_room_memory_mb": int(data["odoo_room_memory_mb"]),
        "memory_share": float(data["memory_share"]),
        "measured": bool(data["measured"]),
        "source": str(data["k3s_source"]),
    }


def recommend(facts: dict[str, Any], needs: dict[str, bool | None], k8s_default: bool) -> dict[str, Any]:
    """The installer's rule: {"fits", "recommended", "default", "reason", "reasons", "beta"}; reasons are what keeps
    Kubernetes from running well here (the minimum check)."""
    figure = figures()
    note = "" if figure["measured"] else " (to be measured)"
    reasons: list[str] = []
    gb = memory_gb(facts["memory_mb"])
    cpus = int(facts.get("processors", figure["min_cpus"]))
    if cpus < figure["min_cpus"] or facts["memory_mb"] < figure["min_memory_mb"] * figure["memory_share"]:
        reasons.append(
            f"Kubernetes needs at least {figure['min_cpus']} cores and {memory_gb(figure['min_memory_mb'])} GB of "
            f"memory{note}; this server has {cpus} cores and {gb} GB"
        )
    if facts["virtualisation"]["kind"] == "container":
        reasons.append(
            "Kubernetes (k3s) cannot run properly in a container, and this server is one "
            f"({facts['virtualisation']['name']})"
        )
    if not system_supported(facts["system"]["id"], facts["system"]["version"]):
        reasons.append(
            "Kubernetes is prepared on Ubuntu 22.04+ and Debian 12+; this server runs "
            f"{facts['system']['name'] or 'an unknown system'}"
        )
    recommended = "docker"
    if reasons:
        reason = reasons[0]
    elif needs.get("grow"):
        recommended = "kubernetes"
        reason = "you plan to add servers, and Kubernetes makes growing to more servers easier"
    elif needs.get("separation"):
        recommended = "kubernetes"
        reason = "you need stronger separation between clients or teams, and Kubernetes isolates them more"
    elif all(needs.get(key) is None for key in NEEDS):
        reason = "Docker unless you say otherwise: it is simpler to run, and Kubernetes is beta"
    elif needs.get("team"):
        reason = "one server run by a team is well served by Docker, which is simpler to run"
    else:
        reason = "one server run by one person is well served by Docker, which is simpler to run"
    stable = K8S_STABLE or k8s_default
    return {
        "fits": not reasons,
        "recommended": recommended,
        "default": "kubernetes" if recommended == "kubernetes" and stable else "docker",
        "reason": reason,
        "reasons": reasons,
        "beta": not K8S_STABLE,
    }


def facts() -> dict[str, Any]:
    record = installer_record()
    system = _docker_system() or record.get("OS_NAME", "")
    os_id, version = parse_system(system)
    if not os_id and record.get("OS_ID"):
        os_id, version = record.get("OS_ID", ""), record.get("OS_VERSION", "")
    virt = record.get("VIRT", "unknown") or "unknown"
    kind = record.get("VIRT_KIND", "") or ("container" if virt in CONTAINERS else "unknown")
    if kind not in ("none", "vm", "container"):
        kind = "unknown"
    listening = listening_ports()
    from sudoo import config

    ours = config.exposure_mode(settings.exposure) == "direct"  # with direct, Sudoo.sh's gateway answers on 80 and 443
    return {
        "memory_mb": memory_mb(),
        "processors": processors(),
        "disk_free_gb": free_disk_gb(),
        "system": {"name": system, "id": os_id, "version": version},
        "virtualisation": {"name": virt, "kind": kind, "from_installer": bool(record)},
        "ports": {
            str(port): {"free": port not in listening, "sudoo": port in listening and ours} for port in (80, 443)
        },
    }


def check(needs: dict[str, bool | None] | None = None, fresh: bool = False) -> dict[str, Any]:
    """The facts (measured again at most every CACHE_SECONDS), the recommendation for these answers, the figures and
    the engine in use."""
    global _cache
    with _lock:
        if fresh or not _cache or time.monotonic() - _cache[0] >= CACHE_SECONDS:
            _cache = (time.monotonic(), facts())
        found: dict[str, Any] = json.loads(json.dumps(_cache[1]))
    answers = {key: (needs or {}).get(key) for key in NEEDS}
    figure = figures()
    return {
        **found,
        "needs": answers,
        "recommendation": recommend(found, answers, settings.engine_k8s_default),
        "figures": {
            key: figure[key]
            for key in ("min_cpus", "min_memory_mb", "k3s_min_memory_mb", "odoo_room_memory_mb", "measured", "source")
        },
        "engine": {
            "current": settings.engine,
            "wanted": settings.engine_wanted or None,
            "available": engines_available(),
            "switch_command": "sudo sudoo-install engine {engine}",
        },
        "checked_at": time.time(),
    }
