"""Which names can be an instance's custom domain.

A custom domain is a full host name such as erp.client.com: lower-case letters, digits and hyphens in dot-separated
labels (internationalised names in their xn-- form), at least two labels, a top-level label that is not all digits.
Refused: IP addresses, wildcards (*.client.com: an HTTP-01 certificate cannot cover them), local names (localhost,
*.local, *.internal, ...), names reserved for documentation and tests (example.com, *.test, *.invalid), the
Sudoo.sh names (sudoo.sh and everything under it), and the panel's own names (the DNS target, PUBLIC_HOST, the
panel's address).
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from sudoo.config import settings

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_RESERVED_SUFFIXES = (
    "localhost",
    "local",
    "localdomain",
    "internal",
    "intranet",
    "lan",
    "home.arpa",
    "arpa",
    "test",
    "invalid",
    "example",
    "onion",
    "example.com",
    "example.net",
    "example.org",
    "sudoo.sh",
    "trycloudflare.com",
)
MAX_LENGTH = 253


class InvalidDomain(ValueError):
    """The name cannot be a custom domain; the message says why."""


def _under(name: str, suffix: str) -> bool:
    return name == suffix or name.endswith("." + suffix)


def own_names() -> set[str]:
    """The panel's own host names, which no instance may take."""
    found = {settings.domain_dns_target, settings.public_host}
    try:
        from sudoo import exposure

        panel = exposure.address(exposure.PANEL) or ""
        found.add(urlsplit(panel).hostname or "")
    except Exception:  # noqa: BLE001 - the panel's address is a nice-to-have here
        pass
    return {name.lower().rstrip(".") for name in found if name}


def normalize(value: str) -> str:
    """The domain as stored (lower case, no trailing dot, internationalised names as xn--), or InvalidDomain."""
    name = str(value or "").strip().lower().rstrip(".")
    if "://" in name or "/" in name or ":" in name or "@" in name:
        raise InvalidDomain("Enter only the domain, such as erp.example.com: no https://, path or port.")
    if name.startswith("*"):
        raise InvalidDomain("Wildcard domains are not supported: add each domain on its own (erp.example.com).")
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        raise InvalidDomain("Enter a domain name, not an IP address.")
    try:
        name = name.encode("idna").decode("ascii") if not name.isascii() else name
    except UnicodeError:
        raise InvalidDomain("This is not a valid domain name.") from None
    labels = name.split(".")
    if not name or len(name) > MAX_LENGTH or len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        raise InvalidDomain("This is not a valid domain name: use letters, digits, hyphens and dots (erp.example.com).")
    if labels[-1].isdigit():
        raise InvalidDomain("Enter a domain name, not an IP address.")
    for suffix in _RESERVED_SUFFIXES:
        if _under(name, suffix):
            raise InvalidDomain(f"Domains under {suffix} cannot be used here.")
    if name in own_names():
        raise InvalidDomain("This name belongs to this server or its panel, not to an instance.")
    return name


def is_apex(name: str) -> bool:
    """Whether the name looks like a bare domain (example.com, example.co.uk), which cannot have a CNAME record.
    A guess without the public-suffix list: two labels, or three with a short second-level label."""
    labels = name.split(".")
    return len(labels) == 2 or (len(labels) == 3 and len(labels[1]) <= 3 and len(labels[2]) == 2)
