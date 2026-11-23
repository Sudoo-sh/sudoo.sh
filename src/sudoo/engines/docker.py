"""The Docker engine: one Docker Compose project per instance (odoo-<name>), driven through the Docker socket.

Every instance created before engines existed runs here, and it stays the default. The commands are exactly those the
panel ran before the engine layer: `docker compose` with the instance's hardened compose file (store.compose,
instances/integrity.py), `docker exec` and `docker cp` in its containers, `docker logs` of its Odoo container.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sudoo.core.shell import capture

from .base import Engine, Observation, TerminalStream

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sudoo.core.jobs import Job

LOCAL = "127.0.0.1"  # Odoo is published on the server's 127.0.0.1 only (instances/odoo.py compose_spec)


def _compose(name: str, *args: str) -> list[str]:
    from sudoo.instances import store

    return store.compose(name, *args)


def _container(name: str, service: str) -> str:
    from sudoo.instances import store

    return store.container(name, service)


class DockerTerminal:
    """The web shell's processes through the Docker Engine API (webshell/docker_exec.py)."""

    async def check(self, plan: Any) -> None:
        from sudoo.webshell import docker_exec, policy

        policy.check_container(await docker_exec.inspect_container(plan.container), plan)

    async def create(
        self, plan: Any, argv: list[str], *, env: dict[str, str], workdir: str | None, cols: int, rows: int
    ) -> str:
        from sudoo.webshell import docker_exec

        return await docker_exec.create(
            plan.container, argv, user=plan.container_user, env=env, workdir=workdir, cols=cols, rows=rows
        )

    async def start(self, process_id: str) -> TerminalStream:
        from sudoo.webshell import docker_exec

        return await docker_exec.start(process_id)

    async def resize(self, process_id: str, cols: int, rows: int) -> None:
        from sudoo.webshell import docker_exec

        await docker_exec.resize(process_id, cols, rows)

    async def hang_up(self, target: str, session_id: str, user: str) -> None:
        from sudoo.webshell import docker_exec

        await docker_exec.hang_up(target, session_id, user)


class DockerEngine(Engine):
    kind = "docker"

    # ------------------------------------------------------------ definition

    def prepare_images(self, job: Job, version: str) -> None:
        from sudoo.instances import operations

        operations._download_images(job, version)

    def apply(self, job: Job, name: str, pull: bool = False) -> None:
        from sudoo.instances import integrity

        integrity.apply(job, name, pull=pull) if pull else integrity.apply(job, name)

    def remove_leftovers(self, job: Job, name: str) -> None:
        label = f"label=com.docker.compose.project=odoo-{name}"
        containers = capture(["docker", "ps", "-aq", "--filter", label]).split()
        volumes = capture(["docker", "volume", "ls", "-q", "--filter", label]).split()
        if containers:
            job.run(["docker", "rm", "-f", *containers])
        if volumes:
            job.run(["docker", "volume", "rm", "-f", *volumes])

    # ------------------------------------------------------------ lifecycle

    def up(
        self, job: Job, name: str, *services: str, timeout: float = 600, orphans: bool = False, recreate: bool = True
    ) -> None:
        flags = ["-d", *(() if recreate else ("--no-recreate",)), *(("--remove-orphans",) if orphans else ())]
        job.run(_compose(name, "up", *flags, *services), timeout=timeout)

    def stop(self, job: Job, name: str, *services: str, timeout: float = 300, checked: bool = True) -> None:
        command = _compose(name, "stop", *services)
        if checked:
            job.run(command, timeout=timeout)
        else:
            job.run_unchecked(command, timeout=timeout)

    def restart(self, job: Job, name: str, service: str, timeout: float = 300) -> None:
        job.run(_compose(name, "restart", service), timeout=timeout)

    def remove(self, job: Job, name: str, timeout: float = 600, checked: bool = True) -> None:
        command = _compose(name, "down", "-v", "--remove-orphans")
        if checked:
            job.run(command, timeout=timeout)
        else:
            job.run_unchecked(command, timeout=timeout)

    # ------------------------------------------------------------ commands

    def exec_cmd(self, name: str, service: str, *args: str, stdin: bool = False) -> list[str]:
        return ["docker", "exec", *(("-i",) if stdin else ()), _container(name, service), *args]

    def odoo_cmd(self, name: str, *args: str) -> list[str]:
        return _compose(name, "run", "--rm", "--no-deps", "-T", "odoo", "odoo", *args)

    def copy_out_cmd(self, name: str, service: str, path: str) -> list[str]:
        # docker cp also works while the container is stopped; "-" writes the folder as a tar stream
        return ["docker", "cp", f"{_container(name, service)}:{path}", "-"]

    def filestore_cmd(self, name: str, script: str, *args: str) -> list[str]:
        from sudoo.instances import odoo

        # root in a short-lived container, with only what replacing files owned by Odoo's user needs (the
        # instance's containers drop every capability otherwise)
        capabilities = [arg for cap in odoo.FILESTORE_CAPABILITIES for arg in ("--cap-add", cap)]
        command = _compose(
            name, "run", "--rm", "--no-deps", "-T", "--user", "root", *capabilities, "--entrypoint", "sh"
        )
        return [*command, "odoo", "-c", script, "sh", *args]

    def logs_cmd(
        self, name: str, tail: int, follow: bool = False, since: str | None = None, until: str | None = None
    ) -> list[str]:
        cmd = ["docker", "logs", "--timestamps"]
        if since:
            cmd += ["--since", since]
        else:
            cmd += ["--tail", str(tail)]
        if until:
            cmd += ["--until", until]
        if follow:
            cmd.append("--follow")
        return [*cmd, _container(name, "odoo")]

    def terminal(self) -> DockerTerminal:
        return DockerTerminal()

    # ------------------------------------------------------------ facts

    def odoo_host(self, env: Mapping[str, str]) -> str:
        return LOCAL

    def observe(self, names: list[str]) -> dict[str, Observation]:
        """{}: the monitor measures Docker instances itself, from one continuous `docker stats` stream that also
        gives the panel's own containers (instances/monitor.py)."""
        return {}
