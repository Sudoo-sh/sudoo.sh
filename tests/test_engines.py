"""The engine layer (engines/): an instance's engine comes from its .env, and the Docker engine runs exactly the
commands the panel ran before engines existed."""

from __future__ import annotations

from typing import Any

import pytest

from sudoo import engines
from sudoo.engines.docker import DockerEngine
from sudoo.instances import store

NAME = "engdock"


class RecordingJob:
    def __init__(self) -> None:
        self.ran: list[tuple[list[str], float | None, bool]] = []

    def run(self, cmd: list[str], timeout: float | None = None, input: str | None = None) -> None:
        self.ran.append((cmd, timeout, True))

    def run_unchecked(self, cmd: list[str], timeout: float | None = None, input: str | None = None) -> int:
        self.ran.append((cmd, timeout, False))
        return 0


@pytest.fixture
def instance() -> Any:
    folder = store.folder(NAME)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(NAME, {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": NAME})
    yield NAME
    import shutil

    shutil.rmtree(folder, ignore_errors=True)


def test_an_instance_without_an_engine_runs_on_docker(instance: str) -> None:
    assert engines.kind_of({}) == "docker"
    assert isinstance(engines.of(instance), DockerEngine)
    assert engines.kinds()[0] == "docker"
    assert engines.offered() == ["docker"]  # KUBERNETES_ENGINE_BETA is off by default
    with pytest.raises(engines.UnknownEngine):
        engines.named("nomad")


def test_docker_lifecycle_commands_are_the_compose_commands(instance: str) -> None:
    engine, job = engines.of(instance), RecordingJob()
    engine.up(job, instance, "db", timeout=300)  # type: ignore[arg-type]
    engine.up(job, instance, "db", timeout=300, recreate=False)  # type: ignore[arg-type]
    engine.up(job, instance, timeout=900, orphans=True)  # type: ignore[arg-type]
    engine.stop(job, instance, "odoo")  # type: ignore[arg-type]
    engine.stop(job, instance, checked=False)  # type: ignore[arg-type]
    engine.restart(job, instance, "odoo")  # type: ignore[arg-type]
    engine.remove(job, instance)  # type: ignore[arg-type]
    compose = store.compose(instance)
    assert job.ran == [
        ([*compose, "up", "-d", "db"], 300, True),
        ([*compose, "up", "-d", "--no-recreate", "db"], 300, True),
        ([*compose, "up", "-d", "--remove-orphans"], 900, True),
        ([*compose, "stop", "odoo"], 300, True),
        ([*compose, "stop"], 300, False),
        ([*compose, "restart", "odoo"], 300, True),
        ([*compose, "down", "-v", "--remove-orphans"], 600, True),
    ]


def test_docker_commands(instance: str) -> None:
    engine = engines.of(instance)
    db, odoo_container = store.container(instance, "db"), store.container(instance, "odoo")
    assert engine.exec_cmd(instance, "db", "psql", "-c", "SELECT 1") == ["docker", "exec", db, "psql", "-c", "SELECT 1"]
    assert engine.exec_cmd(instance, "db", "psql", stdin=True) == ["docker", "exec", "-i", db, "psql"]
    assert engine.odoo_cmd(instance, "-d", "x") == [*store.compose(instance), "run", "--rm", "--no-deps", "-T", "odoo",
                                                    "odoo", "-d", "x"]  # fmt: skip
    assert engine.copy_out_cmd(instance, "odoo", "/var/lib/odoo/filestore/x") == [
        "docker", "cp", f"{odoo_container}:/var/lib/odoo/filestore/x", "-"
    ]  # fmt: skip
    assert engine.logs_cmd(instance, 50) == ["docker", "logs", "--timestamps", "--tail", "50", odoo_container]
    assert engine.logs_cmd(instance, 50, follow=True, since="2026-01-01T00:00:00Z", until="2026-01-02T00:00:00Z") == [
        "docker", "logs", "--timestamps", "--since", "2026-01-01T00:00:00Z", "--until", "2026-01-02T00:00:00Z",
        "--follow", odoo_container,
    ]  # fmt: skip
    filestore = engine.filestore_cmd(instance, "true", "/var/lib/odoo/filestore/x")
    assert filestore[-5:] == ["odoo", "-c", "true", "sh", "/var/lib/odoo/filestore/x"]
    assert "--user" in filestore and "root" in filestore
    assert engine.odoo_host({}) == "127.0.0.1"
    assert engine.observe([instance]) == {}
