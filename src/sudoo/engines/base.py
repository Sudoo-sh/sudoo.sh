"""What an engine does for the panel: run an instance's two services, Odoo ("odoo") and PostgreSQL ("db").

The panel's operations (instances/operations.py, backups.py, odoo.py, logs.py) describe an instance's life in
steps that do not depend on where it runs: start the database, run `odoo -i base` once, start Odoo, run psql in the
database, stream the filestore out, read Odoo's log. An engine turns each step into what its platform needs:

    lifecycle     up, stop, restart, remove (a job's step: they raise JobError when they fail)
    definition    prepare_images (before a new instance), apply (write or refresh what the platform runs),
                  remove_leftovers (of an earlier instance with the same name)
    commands      exec_cmd, odoo_cmd, copy_out_cmd, filestore_cmd, logs_cmd: argument lists run the way every
                  external command of the panel runs (core/shell.py, Job.run), never through a shell
    shell         terminal(): interactive processes with a terminal, for the web shell (webshell/)
    facts         odoo_host (where the gateway reaches Odoo), observe (running or not, CPU, memory)

Engines: "docker" (engines/docker.py, one Docker Compose project per instance through the Docker socket; the default
and every instance created before engines existed) and "kubernetes" (beta, engines/kubernetes/). An instance's engine
is the ENGINE key of its .env; absent means docker. Design: docs/KUBERNETES_ENGINE.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sudoo.core.jobs import Job

SERVICES = ("odoo", "db")


@dataclass(frozen=True, slots=True)
class Observation:
    """What an engine measured of one instance (instances/monitor.py)."""

    running: bool  # Odoo runs
    db_running: bool
    uptime: str = ""  # a few words for people ("Up 3 hours"), "" when stopped
    cpu: float = 0.0  # share of the server's processor, 0..1 of every core together
    memory: int = 0  # bytes used by both services
    limit: int = 0  # bytes they may use together (0: unknown)
    started: float | None = None  # when Odoo started (Unix time)


class TerminalError(Exception):
    """The platform refused or failed a terminal call; `status` is its HTTP status (0: not reachable, 404: the
    service does not run)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class TerminalStream(Protocol):
    """The terminal of a started process: what it shows and what is typed into it."""

    async def read(self) -> bytes:
        """The next output (b"" when the process ended)."""
        ...

    async def write(self, data: bytes) -> None: ...

    def close(self) -> None: ...


class Terminal(Protocol):
    """Interactive processes with a terminal in an instance's service (the web shell, webshell/sessions.py). `plan`
    is a webshell.policy.Plan: the instance, its service, the container or pod reached and the user."""

    async def check(self, plan: Any) -> None:
        """Raise policy.Refused unless what `plan` reaches is exactly that instance's running service, and nothing
        that would make a shell in it a shell on the server; TerminalError when the platform cannot tell."""

    async def create(
        self, plan: Any, argv: list[str], *, env: dict[str, str], workdir: str | None, cols: int, rows: int
    ) -> str:
        """Prepare the process; an id for start() and resize()."""
        ...

    async def start(self, process_id: str) -> TerminalStream: ...

    async def resize(self, process_id: str, cols: int, rows: int) -> None: ...

    async def hang_up(self, target: str, session_id: str, user: str) -> None:
        """End every process of the session (marked SUDOO_SHELL=<session id>) in `target` (a plan's container).
        Never raises."""


class Engine(ABC):
    """One way of running instances. Instances of every engine share the panel's files and state; only the
    platform underneath differs."""

    kind: ClassVar[str]

    # ------------------------------------------------------------ definition

    @abstractmethod
    def prepare_images(self, job: Job, version: str) -> None:
        """Before a new instance of Odoo `version`: make its images available (a step of the job)."""

    @abstractmethod
    def apply(self, job: Job, name: str, pull: bool = False) -> None:
        """Write or refresh what the platform runs for the instance from its files (.env, odoo.conf, add-ons), and
        put back what was changed by hand. `pull`: take the newest copies of its images."""

    @abstractmethod
    def remove_leftovers(self, job: Job, name: str) -> None:
        """Remove what an earlier instance with the same name left on the platform, so nothing old is reused."""

    # ------------------------------------------------------------ lifecycle

    @abstractmethod
    def up(
        self, job: Job, name: str, *services: str, timeout: float = 600, orphans: bool = False, recreate: bool = True
    ) -> None:
        """Start `services` (all when none). `orphans`: also remove what no longer belongs to the instance;
        `recreate`: replace what runs with an older definition (else keep it running as it is)."""

    @abstractmethod
    def stop(self, job: Job, name: str, *services: str, timeout: float = 300, checked: bool = True) -> None:
        """Stop `services` (all when none), keeping their data. Unchecked: a failure is only logged."""

    @abstractmethod
    def restart(self, job: Job, name: str, service: str, timeout: float = 300) -> None:
        """Restart one running service."""

    @abstractmethod
    def remove(self, job: Job, name: str, timeout: float = 600, checked: bool = True) -> None:
        """Remove everything the instance runs, its data included (its folder stays: the caller removes it)."""

    # ------------------------------------------------------------ commands

    @abstractmethod
    def exec_cmd(self, name: str, service: str, *args: str, stdin: bool = False) -> list[str]:
        """Command that runs `args` inside the running `service`; `stdin`: what the command is given goes in."""

    @abstractmethod
    def odoo_cmd(self, name: str, *args: str) -> list[str]:
        """Command that runs `odoo <args>` once, beside the instance's Odoo, with its configuration, add-ons and
        files (the database must be up); its standard input goes to Odoo."""

    @abstractmethod
    def copy_out_cmd(self, name: str, service: str, path: str) -> list[str]:
        """Command that writes the folder `path` of `service` to its standard output as a tar stream whose
        entries start with the folder's own name; it also works while the service is stopped."""

    @abstractmethod
    def filestore_cmd(self, name: str, script: str, *args: str) -> list[str]:
        """Command that runs the shell `script` (with `args` as $1...) where the instance's filestore can be
        replaced, Odoo stopped; its standard input goes to the script."""

    @abstractmethod
    def logs_cmd(
        self, name: str, tail: int, follow: bool = False, since: str | None = None, until: str | None = None
    ) -> list[str]:
        """Command that prints Odoo's log, each line after its RFC 3339 time stamp: the last `tail` lines (all those
        after `since` when given; before `until`), then new ones when `follow`."""

    @abstractmethod
    def terminal(self) -> Terminal:
        """Interactive processes in the instance's services, for the web shell."""

    # ------------------------------------------------------------ facts

    @abstractmethod
    def odoo_host(self, env: Mapping[str, str]) -> str:
        """The address the gateway reaches the instance's Odoo on (with ODOO_PORT, and ODOO_GEVENT_PORT)."""

    @abstractmethod
    def observe(self, names: list[str]) -> dict[str, Observation]:
        """Figures of these instances of this engine, measured now ({} when the monitor measures them itself)."""
