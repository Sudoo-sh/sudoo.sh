"""Engines: where an instance's Odoo and PostgreSQL run (engines/base.py says what an engine does).

    of(name)        the engine of an instance (the ENGINE key of its .env; absent: docker)
    named(kind)     the engine of that kind ("docker", ...)
    kind_of(env)    the kind an instance's .env names

Docker is the default and every existing instance's engine; Kubernetes (beta, engines/kubernetes/) is offered for new
instances while KUBERNETES_ENGINE_BETA is on. Design of the engine layer and of the Kubernetes engine:
docs/KUBERNETES_ENGINE.md.
"""

from __future__ import annotations

from collections.abc import Mapping

from .base import SERVICES, Engine, Observation
from .docker import DockerEngine

DOCKER = "docker"
KUBERNETES = "kubernetes"
ENV_KEY = "ENGINE"  # in an instance's .env

_ENGINES: dict[str, Engine] = {DOCKER: DockerEngine()}

__all__ = [
    "DOCKER",
    "ENV_KEY",
    "KUBERNETES",
    "SERVICES",
    "Engine",
    "Observation",
    "kind_of",
    "kinds",
    "named",
    "of",
    "offered",
]


class UnknownEngine(LookupError):
    """An instance names an engine this panel does not have."""


def kinds() -> list[str]:
    """The engines this panel has, the default first."""
    return [DOCKER, KUBERNETES]


def offered() -> list[str]:
    """The engines a new instance may use now: Docker, and Kubernetes while KUBERNETES_ENGINE_BETA is on."""
    from sudoo.config import settings

    return [DOCKER, KUBERNETES] if settings.kubernetes_engine_beta else [DOCKER]


def kind_of(env: Mapping[str, str]) -> str:
    return env.get(ENV_KEY) or DOCKER


def named(kind: str) -> Engine:
    if kind == KUBERNETES and kind not in _ENGINES:  # loaded on first use: Docker-only panels never import it
        from sudoo.topology import engines as topology

        from .kubernetes import KubernetesEngine
        from .kubernetes.topology import KubernetesTopology

        _ENGINES[kind] = KubernetesEngine()
        topology.contribute(KUBERNETES, KubernetesTopology())  # nodes, namespaces and replicas (topology/engines.py)
    try:
        return _ENGINES[kind]
    except KeyError:
        raise UnknownEngine(f"unknown engine {kind[:40]!r}") from None


def of(name: str) -> Engine:
    """The engine instance `name` runs on."""
    from sudoo.instances import store

    return named(kind_of(store.read_env(name)))
