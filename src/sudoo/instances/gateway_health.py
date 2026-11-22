"""Health check of the gateway service: `python -m sudoo.instances.gateway_health` exits 0 when it answers on its
control socket (gateway_service.py). Only the standard library, so the check every few seconds costs little."""

from __future__ import annotations

import contextlib
import hashlib
import os
import socket
import stat
import sys

MAX_SOCKET_PATH = 107  # bytes: a Unix socket address holds 108, the last one a NUL
SHORT_BASE = "/tmp"


def short_dir(gateway_dir: str) -> str:
    """The folder for the sockets when GATEWAY_DIR is too deep for them: named after GATEWAY_DIR, so the panel and
    the gateway service find the same one. It is only shared when both run on the same host (a container has its
    own /tmp): keep ODOO_ROOT short where they run in separate containers."""
    digest = hashlib.sha256(os.fsencode(os.path.abspath(gateway_dir))).hexdigest()[:16]
    return os.path.join(SHORT_BASE, f"sudoo-{digest}")


def socket_file(gateway_dir: str, name: str) -> str:
    """Where the socket `name` lives: in GATEWAY_DIR, or in short_dir() when that path is too long for a socket."""
    default = os.path.join(os.path.abspath(gateway_dir), name)
    if len(os.fsencode(default)) <= MAX_SOCKET_PATH:
        return default
    return os.path.join(short_dir(gateway_dir), name)


def prepare_short_dir(gateway_dir: str) -> None:
    """Create short_dir() (mode 700) when the sockets need it; refuse (OSError) a folder another user made, or a link."""
    if socket_file(gateway_dir, "gateway.sock").startswith(os.path.abspath(gateway_dir) + os.sep):
        return
    folder = short_dir(gateway_dir)
    with contextlib.suppress(FileExistsError):
        os.mkdir(folder, 0o700)
    info = os.lstat(folder)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise OSError(f"{folder} is not a folder of this user: remove it, or use a shorter ODOO_ROOT")
    os.chmod(folder, 0o700)


def socket_path() -> str:
    return socket_file(os.path.join(os.environ.get("ODOO_ROOT", "/opt/odoo"), "gateway"), "gateway.sock")


def answers(path: str) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(5)
            conn.connect(path)
            conn.sendall(b"GET /healthz HTTP/1.0\r\nHost: gateway\r\n\r\n")
            status_line = conn.recv(64).split(b"\r\n", 1)[0].split()
            return len(status_line) > 1 and status_line[0].startswith(b"HTTP/") and status_line[1] == b"200"
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(0 if answers(socket_path()) else 1)
