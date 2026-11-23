"""Engines: where an instance's Odoo and PostgreSQL run (engines/base.py says what an engine does).

    of(name)        the engine of an instance (the ENGINE key of its .env; absent: docker)
    named(kind)     the engine of that kind ("docker", ...)
    kind_of(env)    the kind an instance's .env names

Docker is the default and every existing instance's engine. Design of the engine layer and of the Kubernetes engine:
docs/KUBERNETES_ENGINE.md.
"""

from __future__ import annotations

from collections.abc import Mapping

from .base import SERVICES, Engine, Observation
from .docker import DockerEngine

DOCKER = "docker"
ENV_KEY = "ENGINE"  # in an instance's .env

_ENGINES: dict[str, Engine] = {DOCKER: DockerEngine()}

__all__ = ["DOCKER", "ENV_KEY", "SERVICES", "Engine", "Observation", "kind_of", "kinds", "named", "of"]


class UnknownEngine(LookupError):
    """An instance names an engine this panel does not have."""


def kinds() -> list[str]:
    """The engines this panel has, the default first."""
    return list(_ENGINES)


def kind_of(env: Mapping[str, str]) -> str:
    return env.get(ENV_KEY) or DOCKER


def named(kind: str) -> Engine:
    try:
        return _ENGINES[kind]
    except KeyError:
        raise UnknownEngine(f"unknown engine {kind[:40]!r}") from None


def of(name: str) -> Engine:
    """The engine instance `name` runs on."""
    from sudoo.instances import store

    return named(kind_of(store.read_env(name)))
