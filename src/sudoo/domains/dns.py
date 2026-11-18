"""DNS: what record a custom domain needs, and whether it points at this server.

The target is DOMAIN_DNS_TARGET, or PUBLIC_HOST when that is empty: a name gets a CNAME record (erp.client.com CNAME
server.example.net), an address an A record (IPv4) or AAAA record (IPv6). A bare domain (client.com) cannot have a
CNAME: it gets A/AAAA records with the target's addresses.

The check resolves the domain from the server, with DOMAIN_DNS_TIMEOUT_SECONDS for each lookup, through the
server's own resolvers (dnspython, which also follows CNAME chains). It passes when the domain has at least one
address and every one of its addresses is one of the target's: an address that points elsewhere (an old server, a
proxy) would make visitors, and the ACME authority, reach another machine.

Lookups go through `lookup`, which tests replace: tests never ask real DNS.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Protocol

import dns.exception
import dns.resolver

from sudoo.config import settings

from .names import is_apex


class Lookup(Protocol):
    def __call__(self, name: str, kind: str, timeout: float) -> tuple[list[str], str]:
        """The records of one kind ("A", "AAAA") of `name` and its canonical name (after CNAMEs); ([], name) when
        it has none. Raises LookupFailed when DNS did not answer."""
        ...


class LookupFailed(Exception):
    """DNS did not answer in time, or answered with an error."""


def resolve(name: str, kind: str, timeout: float) -> tuple[list[str], str]:
    """The real lookup (dnspython)."""
    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    resolver.timeout = timeout
    try:
        answer = resolver.resolve(name, kind, raise_on_no_answer=False)
    except dns.resolver.NXDOMAIN:
        return [], name
    except (dns.exception.Timeout, dns.resolver.NoNameservers) as error:
        raise LookupFailed(f"DNS did not answer for {name}: {error.__class__.__name__}") from error
    except dns.exception.DNSException as error:
        raise LookupFailed(f"DNS lookup of {name} failed: {error.__class__.__name__}") from error
    canonical = str(answer.canonical_name).rstrip(".").lower()
    records = [record.to_text() for record in answer] if answer.rrset is not None else []
    return records, canonical


lookup: Lookup = resolve


@dataclass(frozen=True, slots=True)
class Target:
    """What custom domains point at."""

    host: str  # a name or an address
    is_address: bool

    def record_type(self) -> str:
        if not self.is_address:
            return "CNAME"
        return "AAAA" if ipaddress.ip_address(self.host).version == 6 else "A"


def target() -> Target | None:
    """DOMAIN_DNS_TARGET, else PUBLIC_HOST; None when neither is set."""
    host = (settings.domain_dns_target or settings.public_host or "").strip().lower().rstrip(".")
    if not host:
        return None
    try:
        return Target(str(ipaddress.ip_address(host)), True)
    except ValueError:
        return Target(host, False)


def addresses(name: str) -> tuple[set[str], list[str]]:
    """Every IPv4 and IPv6 address of `name`, and the names on the way (CNAME chain end)."""
    found: set[str] = set()
    canonical: list[str] = []
    for kind in ("A", "AAAA"):
        records, end = lookup(name, kind, settings.domain_dns_timeout_seconds)
        found |= {str(ipaddress.ip_address(record)) for record in records}
        if end and end != name and end not in canonical:
            canonical.append(end)
    return found, canonical


def target_addresses(goal: Target) -> set[str]:
    if goal.is_address:
        return {goal.host}
    return addresses(goal.host)[0]


_SHOWN: dict[str, tuple[float, list[str]]] = {}  # target -> (when, its addresses) for the instructions
SHOWN_SECONDS = 300


def _shown_addresses(goal: Target) -> list[str]:
    """The target's addresses for the instructions, looked up at most every 5 minutes."""
    import time

    cached = _SHOWN.get(goal.host)
    if cached and time.time() - cached[0] < SHOWN_SECONDS:
        return cached[1]
    try:
        found = sorted(target_addresses(goal), key=lambda a: (":" in a, a))
    except LookupFailed:
        found = []
    _SHOWN[goal.host] = (time.time(), found)
    return found


def instructions(domain: str) -> list[dict[str, str]]:
    """The DNS records to create for `domain`: [{"type", "name", "value"}]; empty when there is no target."""
    goal = target()
    if goal is None:
        return []
    if goal.is_address:
        return [{"type": goal.record_type(), "name": domain, "value": goal.host}]
    if not is_apex(domain):
        return [{"type": "CNAME", "name": domain, "value": goal.host}]
    found = _shown_addresses(goal)
    if not found:
        return [{"type": "CNAME", "name": domain, "value": goal.host}]
    return [{"type": "AAAA" if ":" in address else "A", "name": domain, "value": address} for address in found]


@dataclass(frozen=True, slots=True)
class Check:
    ok: bool
    reason: str
    addresses: tuple[str, ...] = ()


def check(domain: str) -> Check:
    """Whether `domain` points at this server (see the module's description)."""
    goal = target()
    if goal is None:
        return Check(False, "The server's public name is not set (DOMAIN_DNS_TARGET): ask an administrator.")
    try:
        found, _ = addresses(domain)
        expected = target_addresses(goal)
    except LookupFailed as error:
        return Check(False, f"{error}. Trying again shortly.")
    shown = tuple(sorted(found))
    if not found:
        return Check(False, f"{domain} has no DNS record yet. Create the record below; it can take a while to appear.")
    if not expected:
        return Check(False, f"The server's public name {goal.host} has no address in DNS: ask an administrator.")
    stray = found - expected
    if stray:
        return Check(
            False,
            f"{domain} points at {', '.join(sorted(stray))}, which is not this server. Change its DNS record as shown "
            "below (behind a proxy such as Cloudflare, switch the proxy off for this record: DNS only).",
            shown,
        )
    return Check(True, "", shown)
