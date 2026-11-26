"""Where "Explain with AI" may send an excerpt, and the one call that sends it.

Stricter than notifications/guard.py, which it builds on (https parsing, the pinned connection) but whose exceptions
(NOTIFICATION_ALLOWED_HOSTS) do NOT apply here:

    - the destination must be listed exactly, as host:port, in EXPLAIN_ALLOWED_DESTINATIONS; empty means nothing can
      be sent; no wildcards, no networks, a host that is not listed is refused whatever it resolves to;
    - https only, no user name or password in the address, no redirect is followed;
    - every address the host resolves to is checked, at the time of the call, and the connection goes to a checked
      address (never resolved again): a listed host that resolves to ANY forbidden address is refused;
    - always refused, listed or not: the cloud metadata services and link-local addresses (169.254.0.0/16, fe80::/10,
      100.100.100.200, 192.0.0.192, fd00:ec2::254), unspecified, multicast and reserved addresses, and the panel's own
      public name;
    - only PUBLIC addresses are in scope: a private, loopback, shared (100.64/10) or any other non-public address is
      refused, whatever is listed. A model on the administrator's own network is therefore NOT supported in this
      version: marking an entry as "local" would be trust in a label, not proof that the port is not a control
      service of the panel, the database or another internal API. Proposed as a later, separately reviewed mode;
    - the answer is read up to a size cap and within a total time; errors never contain the address, the key or the
      answer.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sudoo.config import settings
from sudoo.notifications import guard
from sudoo.notifications.guard import IPAddress, Refused

MAX_ANSWER = 64 * 1024
MAX_PAYLOAD = 64 * 1024

_NEVER = [
    ipaddress.ip_network(n)
    for n in (
        "169.254.0.0/16", "fe80::/10", "100.100.100.200/32", "192.0.0.192/32", "fd00:ec2::254/128", "0.0.0.0/8",
        "224.0.0.0/4", "240.0.0.0/4", "ff00::/8", "::/128",
    )
]  # fmt: skip


@dataclass(frozen=True)
class Destination:
    host: str
    port: int
    path: str
    address: str  # the checked address the connection goes to


def _entries() -> set[str]:
    """The exact "host:port" entries of EXPLAIN_ALLOWED_DESTINATIONS."""
    return {part.strip().lower() for part in settings.explain_allowed_destinations.split(",") if part.strip()}


def _inner(ip: IPAddress) -> IPAddress:
    return guard.embedded(ip)


def never(ip: IPAddress) -> bool:
    """Addresses no setting can allow: metadata, link-local, unspecified, multicast, reserved."""
    ip = _inner(ip)
    return any(ip in net for net in _NEVER if net.version == ip.version) or ip.is_multicast or ip.is_unspecified


def _panel_hosts() -> set[str]:
    try:
        from sudoo import exposure

        found = {urlsplit(exposure.address(exposure.PANEL) or "").hostname or ""}
    except Exception:  # noqa: BLE001 - no public address yet: nothing to compare
        found = set()
    return {h.lower() for h in found if h}


def check(url: str) -> Destination:
    """The checked destination of `url`; raises Refused (the text says why, never the address)."""
    host, port, path = guard.parse(url)
    if "?" in path or "#" in url or ";" in path:
        # a credential placed in the address would be stored and shown as plain text: the key has its own field
        raise Refused("The address must not contain a query string, parameters or a fragment.")
    entries = _entries()
    if not entries:
        raise Refused("No destination is allowed yet (an administrator lists them in EXPLAIN_ALLOWED_DESTINATIONS).")
    key = f"{host}:{port}"
    if key not in entries:
        raise Refused("This address is not on the list of destinations allowed for AI explanations.")
    if host in _panel_hosts():
        raise Refused("The panel's own address cannot be a destination.")
    try:
        literal: IPAddress | None = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        found = [literal]
    else:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError, OSError):
            raise Refused("The host name could not be found.") from None
        found = list(dict.fromkeys(ipaddress.ip_address(str(i[4][0]).split("%")[0]) for i in infos))
    if not found:
        raise Refused("The host name could not be found.")
    for ip in found:
        if never(ip):
            raise Refused("The address leads to a cloud metadata, link-local or otherwise forbidden address.")
        if not guard.public(ip):
            raise Refused("The address leads to a private or local network, which AI explanations may not reach.")
    return Destination(host, port, path, str(found[0]))


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float = 30.0) -> bytes:
    """POST `payload` (JSON) to the checked destination and return the answer's body (at most MAX_ANSWER bytes, within
    `timeout` seconds in total). Raises Refused or DeliveryError; messages never contain the address, key or answer.
    The transport is notifications/guard.exchange, the one pinned client the notifications use too."""
    body = json.dumps(payload, ensure_ascii=False).encode()
    if len(body) > MAX_PAYLOAD:
        raise Refused("The request is too large.")
    target = check(url)
    status, data = guard.exchange(
        guard.Target(target.host, target.port, target.path, target.address), "POST", body,
        {**headers, "Content-Type": "application/json"}, timeout, MAX_ANSWER, strict_size=True,
    )  # fmt: skip
    guard.check_status(status)
    return data
