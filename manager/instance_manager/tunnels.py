"""Cloudflare quick-tunnel connectors: the public https addresses of the panel and of every instance.

The server accepts no incoming connections except SSH, so nothing on it can be opened from the internet directly.
A connector (`cloudflared`) makes an outgoing connection to Cloudflare; Cloudflare gives it a random address
https://<words>.trycloudflare.com and sends every visit to that address back through the connection.

One connector per destination, all managed the same way:
    oim-connector-panel     -> the panel        (127.0.0.1:MANAGER_PORT)
    oim-connector-<name>    -> instance gateway (127.0.0.1:GATEWAY_PORT of the instance)

Each connector reports its current address on its status port (GET /quicktunnel). The address changes when the
connector restarts; the monitor then updates each Odoo's web.base.url.
"""
import json
import time
import urllib.request

from . import instances, settings
from .shell import capture, succeeds

PANEL = "panel"
_ADDRESS_CACHE = {}
_CACHE_SECONDS = 20


def container(target):
    """Container name of the connector for `target` ("panel" or an instance name)."""
    return f"oim-connector-{target}"


def _ports(target):
    """(destination port, status port) of a connector."""
    if target == PANEL:
        return settings.MANAGER_PORT, settings.PANEL_CONNECTOR_METRICS_PORT
    env = instances.read_env(target)
    return int(env["GATEWAY_PORT"]), int(env["CONNECTOR_METRICS_PORT"])


def run_cmd(target):
    """`docker run` command that creates the connector of `target`."""
    destination, status = _ports(target)
    return ["docker", "run", "-d", "--name", container(target), "--restart", "unless-stopped", "--network", "host",
            "--label", "oim=connector", settings.CLOUDFLARED_IMAGE,
            "tunnel", "--no-autoupdate", "--metrics", f"127.0.0.1:{status}", "--url", f"http://127.0.0.1:{destination}"]


def ensure(target):
    """Make sure the connector of `target` exists and runs (create it, or start it if it was stopped)."""
    state = capture(["docker", "inspect", "-f", "{{.State.Status}}", container(target)])
    if state == "running":
        return
    cmd = ["docker", "start", container(target)] if state else run_cmd(target)
    succeeds(cmd, timeout=300)


def remove(target):
    succeeds(["docker", "rm", "-f", container(target)], timeout=120)


def address(target):
    """Current https address of the connector of `target`, or None while it is connecting. Cached 20 s."""
    cached = _ADDRESS_CACHE.get(target)
    if cached and time.time() - cached[1] < _CACHE_SECONDS:
        return cached[0]
    _, status = _ports(target)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{status}/quicktunnel", timeout=2) as response:
            hostname = json.loads(response.read()).get("hostname", "")
    except (OSError, ValueError):
        hostname = ""
    result = f"https://{hostname}" if hostname.endswith(".trycloudflare.com") else None
    _ADDRESS_CACHE[target] = (result, time.time())
    return result


def wait_for_address(target, timeout=90):
    """Wait until the connector of `target` has its address; return it (None if it did not come up in time)."""
    deadline = time.time() + timeout
    _ADDRESS_CACHE.pop(target, None)
    while time.time() < deadline:
        found = address(target)
        if found:
            return found
        _ADDRESS_CACHE.pop(target, None)
        time.sleep(3)
    return None


def ensure_all():
    """Start the panel's connector and the connector of every instance (at panel start)."""
    ensure(PANEL)
    for name in instances.names():
        ensure(name)
