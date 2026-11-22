"""Requests to addresses people typed in (chat services, webhooks), kept from reaching the panel's own network.

Rules, checked before every request (not only when the address is saved):

    - https:// only, no user name or password in the address, the default port or one in 1-65535;
    - the host name is resolved here, and every address it resolves to must be public: no private (10/8,
      172.16/12, 192.168/16, fc00::/7), loopback, link-local (169.254/16, the cloud metadata service, fe80::/10),
      shared (100.64/10), multicast, reserved or unspecified address, IPv4 inside IPv6 included;
    - except a host name or network an administrator listed in NOTIFICATION_ALLOWED_HOSTS;
    - the connection goes to the address that was checked (never resolved a second time, so a name that changes
      its answer in between cannot lead elsewhere), with the certificate checked for the host name;
    - redirects are never followed (a 3xx counts as failed); at most 64 KB of the answer is read.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from sudoo.config import settings

MAX_ANSWER = 64 * 1024
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class Refused(ValueError):
    """The address may not be used; the text says why (never the address itself)."""


class DeliveryError(RuntimeError):
    """The request was made and failed. `retry`: worth trying again later (a timeout, a 5xx, 429)."""

    def __init__(self, message: str, retry: bool = True, status: int | None = None) -> None:
        super().__init__(message)
        self.retry = retry
        self.status = status


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    path: str
    address: str  # the checked IP address the connection goes to


def _allowed() -> tuple[set[str], list[ipaddress.IPv4Network | ipaddress.IPv6Network]]:
    hosts: set[str] = set()
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in settings.notification_allowed_hosts.split(","):
        part = part.strip().lower()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            hosts.add(part)
    return hosts, networks


def public(ip: IPAddress) -> bool:
    """Whether `ip` is an address on the public internet (an IPv4 address inside IPv6 is judged as IPv4)."""
    if isinstance(ip, ipaddress.IPv6Address):
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is not None:
            return public(inner)
    return bool(ip.is_global) and not ip.is_multicast and not ip.is_reserved and not ip.is_unspecified


def parse(url: str) -> tuple[str, int, str]:
    """(host, port, path with query) of an https address; raises Refused."""
    try:
        parts = urlsplit(url.strip())
        host, port = parts.hostname, parts.port or 443
    except ValueError:
        raise Refused("The address is not valid.") from None
    if parts.scheme != "https" or not host:
        raise Refused("The address must start with https://.")
    if parts.username or parts.password:
        raise Refused("The address must not contain a user name or password.")
    if len(url) > 2048:
        raise Refused("The address is too long.")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return host.lower().rstrip("."), port, path


def resolve(url: str) -> Target:
    """Check `url` and resolve its host; the address to connect to. Raises Refused."""
    host, port, path = parse(url)
    hosts, networks = _allowed()
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
        found = list(dict.fromkeys(ipaddress.ip_address(str(info[4][0]).split("%")[0]) for info in infos))
    if not found:
        raise Refused("The host name could not be found.")
    named = host in hosts
    for ip in found:
        if not (named or public(ip) or any(ip in network for network in networks)):
            raise Refused(
                "The address leads to a private or local network, which notifications may not reach (an "
                "administrator can allow it: NOTIFICATION_ALLOWED_HOSTS)."
            )
    return Target(host, port, path, str(found[0]))


class _PinnedHTTPS(http.client.HTTPSConnection):
    """An https connection to an address checked beforehand, with the certificate checked for the host name."""

    def __init__(self, target: Target, timeout: float, context: ssl.SSLContext) -> None:
        super().__init__(target.host, target.port, timeout=timeout, context=context)
        self._address = target.address
        self._ssl = context

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._ssl.wrap_socket(raw, server_hostname=self.host)


_CONTEXT = ssl.create_default_context()


def post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
    """POST `body` to `url` under the rules above; the HTTP status (2xx). Raises Refused or DeliveryError, never
    with the address in the message."""
    target = resolve(url)
    connection = _PinnedHTTPS(target, timeout, _CONTEXT)
    try:
        connection.request("POST", target.path, body=body, headers={**headers, "Content-Length": str(len(body))})
        answer = connection.getresponse()
        answer.read(MAX_ANSWER)
        status = int(answer.status)
    except ssl.SSLError:
        raise DeliveryError("its certificate could not be checked", retry=False) from None
    except TimeoutError:
        raise DeliveryError(f"no answer within {timeout:g} seconds") from None
    except (OSError, http.client.HTTPException) as error:
        raise DeliveryError(f"could not be reached ({type(error).__name__})") from None
    finally:
        connection.close()
    if 300 <= status < 400:
        raise DeliveryError(f"answered with a redirect (HTTP {status}), which is not followed", False, status)
    if status == 429 or status >= 500:
        raise DeliveryError(f"answered HTTP {status}", True, status)
    if not 200 <= status < 300:
        raise DeliveryError(f"answered HTTP {status}", False, status)
    return status
