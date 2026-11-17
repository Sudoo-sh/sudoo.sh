"""Health check of the gateway service: `python -m sudoo.instances.gateway_health` exits 0 when it answers on its
control socket (gateway_service.py). Only the standard library, so the check every few seconds costs little."""

from __future__ import annotations

import os
import socket
import sys


def socket_path() -> str:
    return os.path.join(os.environ.get("ODOO_ROOT", "/opt/odoo"), "gateway", "gateway.sock")


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
