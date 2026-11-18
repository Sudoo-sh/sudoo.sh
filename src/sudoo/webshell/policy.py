"""Who may open which shell where: every rule of the web shell in one place, checked when a ticket is asked for and
again when its connection opens.

Targets (what the Shell tab offers):

    shell        a terminal in the instance's Odoo container, as its own user (odoo)     instances.shell
    odoo-shell   `odoo shell` on the instance's database, in the same container            instances.shell
    psql         psql on the instance's database, in its database container                instances.database_shell

Rules, in order: shells are switched on (SHELL_ENABLED); the caller is a person in the panel, never an API token;
their role has the target's permission (roles.permissions, which applies SHELL_ROLES); the instance exists; their
access in its project gives that permission and its branch is not locked against them (projects/access.py); a
production instance allows it (SHELL_PRODUCTION) and the caller confirmed it, every time; root only for the Odoo
container's terminal, for super admins, when SHELL_ROOT is on. Sudoo.sh support (support/shells.py) follows the
same rules as an administrator limited by its grant, never as root, and its shells are always recorded.

check_container() then makes sure the container reached is exactly that instance's: its name, the panel's labels,
running, and nothing that would make a shell in it a shell on the server (privileged, the server's processes or
network, the Docker socket). A shell never reaches the panel's own container or the server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sudoo import roles
from sudoo.config import settings
from sudoo.instances import odoo, stages, store
from sudoo.projects import access

TARGETS = ("shell", "odoo-shell", "psql")
SERVICE = {"shell": "odoo", "odoo-shell": "odoo", "psql": "db"}
PERMISSION = {"shell": "instances.shell", "odoo-shell": "instances.shell", "psql": "instances.database_shell"}
CONTAINER_USER = {"odoo": "odoo", "db": "postgres"}  # who a shell runs as, in each container
ODOO_CONFIG = "/etc/odoo/odoo.conf"  # odoo.py mounts the instance's config folder there
LOGIN_SHELL = "if command -v bash >/dev/null 2>&1; then exec bash -l; fi; exec sh -l"
# lower priority than Odoo itself, so a heavy command in a shell does not slow the instance's visitors
NICE = 'if command -v nice >/dev/null 2>&1; then exec nice -n 10 "$@"; fi; exec "$@"'


class Refused(Exception):
    """The shell cannot be opened: `status` is the HTTP status, `code` a word the web app can show its own text for,
    the message a sentence for people."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class Plan:
    """A shell that may be opened: where, as whom, running what."""

    instance: str
    target: str
    service: str
    container: str
    container_user: str
    argv: tuple[str, ...]
    workdir: str | None
    root: bool
    production: bool
    recorded: bool


def recorded(production: bool, root: bool) -> bool:
    """Whether such a shell is recorded (SHELL_RECORDING)."""
    mode = settings.shell_recording
    return mode == "all" or (mode == "production" and (production or root))


def plan(user: dict[str, str], name: str, target: str, *, as_root: bool = False, confirmed: bool = False) -> Plan:
    """The shell `user` may open, or Refused."""
    if not settings.shell_enabled:
        raise Refused(403, "disabled", "Shells are switched off on this panel.")
    if user.get("token_id"):
        raise Refused(403, "token", "A shell is opened by a person in the panel, not with an API token.")
    if target not in TARGETS:
        raise Refused(400, "target", 'Choose "shell", "odoo-shell" or "psql".')
    if PERMISSION[target] not in access.role_permissions(user):
        needed = roles.PERMISSIONS[PERMISSION[target]].lower()
        raise Refused(403, "permission", f"Your role cannot do this ({needed}). {settings.contact_sentence()}")
    if not store.NAME_RE.match(name) or not store.exists(name):
        raise Refused(404, "missing", "This instance does not exist.")
    refused = access.instance_refusal(user, PERMISSION[target], name)  # project access, locked branch
    if refused is not None:
        status, message = refused
        raise Refused(status, "missing" if status == 404 else "locked" if "locked" in message else "access", message)
    env = store.read_env(name)
    production = stages.is_production(env)
    if production:
        if settings.shell_production == "off":
            raise Refused(403, "production-off", "Shells in production instances are switched off on this panel.")
        if settings.shell_production == "super_admins" and user["role"] != "super_admin":
            raise Refused(403, "production-super-admins", "Only super admins open shells in production instances.")
        if not confirmed:
            raise Refused(
                400,
                "confirm-production",
                f"{name} is a production instance: confirm that you want to open a shell in it "
                '("confirm_production": true).',
            )
    if as_root:
        if target != "shell":
            raise Refused(400, "root-target", "Only the Odoo container's terminal can run as root.")
        if not settings.shell_root:
            raise Refused(403, "root-off", "Root shells are switched off on this panel.")
        if user["role"] != "super_admin":
            raise Refused(403, "root-super-admins", "Only super admins open root shells.")
    service = SERVICE[target]
    database = env.get("DB_NAME", "")
    if target == "shell":
        argv: tuple[str, ...] = ("sh", "-c", LOGIN_SHELL)
    elif target == "odoo-shell":
        argv = ("odoo", "shell", "-c", ODOO_CONFIG, "-d", database, "--no-http")
    else:
        argv = ("psql", "-U", "odoo", "-d", database)
    return Plan(
        instance=name,
        target=target,
        service=service,
        container=store.container(name, service),
        container_user="root" if as_root else CONTAINER_USER[service],
        argv=("sh", "-c", NICE, "sudoo-shell", *argv),
        workdir=odoo.CUSTOM_ADDONS_MOUNT if service == "odoo" else None,
        root=as_root,
        production=production,
        recorded=True if user.get("support") else recorded(production, as_root),  # support's shells: always
    )


def check_container(found: dict[str, Any], shell: Plan) -> None:
    """Refused unless `found` (GET /containers/<name>/json) is the running container of `shell`'s instance and
    service, as the panel made it."""
    labels = (found.get("Config") or {}).get("Labels") or {}
    host = found.get("HostConfig") or {}
    mounts = found.get("Mounts") or []
    ours = (
        found.get("Name") == f"/{shell.container}"
        and labels.get(odoo.LABEL_MANAGED) == "true"
        and labels.get(odoo.LABEL_INSTANCE) == shell.instance
        and labels.get(odoo.LABEL_SERVICE) == shell.service
    )
    if not ours:
        raise Refused(409, "not-ours", "This container is not one the panel made for this instance.")
    if not (found.get("State") or {}).get("Running"):
        raise Refused(409, "not-running", "The instance is not running: start it, then open the shell.")
    exposed = (
        host.get("Privileged")
        or host.get("PidMode") == "host"
        or host.get("NetworkMode") == "host"
        or host.get("IpcMode") == "host"
        or any("docker.sock" in str(m.get("Source", "")) for m in mounts if isinstance(m, dict))
    )
    if exposed:
        raise Refused(
            409,
            "exposed",
            "This container was changed to reach the server itself (privileged, host processes or network, or "
            "the Docker socket): no shell is opened in it. Restore its configuration first.",
        )
