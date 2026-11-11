"""Instance folders and their files.

An instance lives in INSTANCES_DIR/<name>/:
    .env                  its settings (version, edition, ports, database, automation switches); mode 600,
                          the Odoo master password in it is encrypted (vault.py)
    docker-compose.yml    its two containers: odoo and db (PostgreSQL)
    config/odoo.conf      Odoo configuration
    addons/               clone of its GitHub repository (with submodules)

Each instance gets three ports on 127.0.0.1, allocated together when it is created:
    GATEWAY_PORT          the gateway listens here; the instance's Cloudflare connector points at it
    ODOO_PORT             Odoo itself, reached only by the gateway
    CONNECTOR_METRICS_PORT  the connector reports its public address here
"""
import re
import socket
import threading

from . import settings

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


def folder(name):
    return settings.INSTANCES_DIR / name


def exists(name):
    return bool(NAME_RE.match(name or "")) and (folder(name) / "docker-compose.yml").exists()


def names():
    """Names of all instances, sorted."""
    if not settings.INSTANCES_DIR.exists():
        return []
    return sorted(p.name for p in settings.INSTANCES_DIR.iterdir() if exists(p.name))


def addons_dir(name):
    return folder(name) / "addons"


def config_file(name):
    return folder(name) / "config" / "odoo.conf"


def read_env(name):
    """The instance's .env file as a dict."""
    env = {}
    path = folder(name) / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                env[key.strip()] = value.strip()
    return env


def master_password_label(name):
    """Vault label of the instance's Odoo master password (stored encrypted in .env, hashed in odoo.conf)."""
    return f"master-password:{name}"


def write_env(name, env):
    """Write the instance's .env file, readable by its owner only (it holds the database password)."""
    path = folder(name) / ".env"
    path.touch(mode=0o600)
    path.write_text("".join(f"{key}={value}\n" for key, value in env.items()))


def update_env(name, **values):
    env = read_env(name)
    env.update({key: str(value) for key, value in values.items()})
    write_env(name, env)


def compose(name, *args):
    """`docker compose` command for the instance's project."""
    path = folder(name)
    return ["docker", "compose", "--project-directory", str(path), "-f", str(path / "docker-compose.yml"), *args]


def container(name, service):
    """Docker container name of a service ("odoo" or "db") of the instance."""
    return f"odoo-{name}-{service}-1"


# --------------------------------------------------------------------------- ports

_PORT_LOCK = threading.Lock()
_RESERVED = set()
_PORT_KEYS = ("GATEWAY_PORT", "ODOO_PORT", "CONNECTOR_METRICS_PORT")


def _free(port):
    """True when nothing listens on 127.0.0.1:<port>."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _used_ports():
    used = {settings.MANAGER_PORT, settings.PANEL_CONNECTOR_METRICS_PORT} | _RESERVED
    for name in names():
        env = read_env(name)
        used |= {int(env[key]) for key in _PORT_KEYS if env.get(key, "").isdigit()}
    return used


def _next_free(start, used):
    port = start
    while port in used or not _free(port):
        port += 1
    return port


def reserve_ports():
    """Reserve and return {GATEWAY_PORT, ODOO_PORT, CONNECTOR_METRICS_PORT} for a new instance."""
    with _PORT_LOCK:
        used = _used_ports()
        ports = {}
        for key, start in zip(_PORT_KEYS, (settings.GATEWAY_PORT_START, settings.ODOO_PORT_START,
                                           settings.CONNECTOR_METRICS_PORT_START)):
            ports[key] = _next_free(start, used)
            used.add(ports[key])
        _RESERVED.update(ports.values())
        return ports


def release_ports(ports):
    """Forget a reservation once the ports are written to the instance's .env (or creation failed)."""
    with _PORT_LOCK:
        _RESERVED.difference_update(ports.values())
