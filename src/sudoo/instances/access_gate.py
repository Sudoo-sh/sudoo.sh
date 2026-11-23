"""Production access rules, as the gateway applies them to every visit (BETA; the panel's side is access_rules.py).

A production instance may say who can open it: visitors from some countries, from some addresses or ranges (an
office, a VPN), or either. The rules apply to the back office only (the default: a public website and shop keep
working for everyone) or to the whole site. Exceptions (paths such as payment callbacks, and source addresses) are
always let through. Someone who opens the instance from the panel gets a short-lived pass that works from anywhere.

Order of the checks for a request to an instance with active rules:
    1. /__sudoo/pass?token=...&next=/path: the panel's pass (below). A valid one sets the pass cookie and sends the
       visitor on; it is never passed to Odoo.
    2. Back office only, and the path is not back office (classify()): let through.
    3. An exception path or source address: let through.
    4. A valid pass cookie for this instance and this host name: let through.
    5. The rule (decide()): countries, addresses, or either. Otherwise the visitor gets the blocked page (HTTP 403).

The visitor's address is the one the gateway trusts already for X-Forwarded-For (visitor_ip()): the connection's
address (EXPOSURE=direct, host-ports, custom domains on ports 80/443), or the header the exposure in front reports
(EXPOSURE=proxy: PROXY_CLIENT_IP_HEADER, its last address; EXPOSURE=tunnel: Cloudflare's CF-Connecting-IP). A header
the visitor sent is never believed otherwise.

The pass
    The panel signs it with HMAC-SHA256 under a key derived from the vault key (access_rules.pass_key()); the gateway
    service, which has no vault, gets that key in routes.json (a root-only file in a root-only folder). A pass
    names the instance, the host name it is for, when it expires, a random nonce and the version of the rules.
    The token in the link expires after PASS_TOKEN_SECONDS and works once (nonces are remembered until then); it
    becomes a cookie (PASS_COOKIE: HttpOnly, SameSite=Lax, Secure over https, for this host only) valid for
    ACCESS_RULES_PASS_HOURS. Saving new rules changes their version, which ends every pass given before. The cookie
    is removed from the requests passed to Odoo, and Odoo cannot set one with its name.

Countries come from geoip.py. When its database is missing, ACCESS_RULES_GEO_MISSING decides: "open" (the default)
treats the country part of a rule as passed, "closed" as failed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from aiohttp import web

from sudoo.instances import geoip

FORMAT = 1
PASS_PATH = "/__sudoo/pass"
STATUS_PATH = "/__sudoo/status"
CONNECT_PATH = "/__sudoo/connect"
PASS_COOKIE = "sudoo_access"
PASS_TOKEN_SECONDS = 120
MODES = ("countries", "ips", "either")
SCOPES = ("back_office", "site")
MAX_NETWORKS = 200
MAX_COUNTRIES = 250
MAX_PATHS = 50

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# --------------------------------------------------------------------------- what is the back office

BACK_OFFICE = (
    "/web",  # the web client, sign-in (/web/login, /web/signup, /web/reset_password), sessions, the RPC routes
    # (/web/dataset/*, /web/action/*), downloads of reports, and the database manager (/web/database/*)
    "/odoo",  # the web client's addresses since Odoo 17.2 (/odoo/action-..., /odoo/<model>/<id>)
    "/json",  # the JSON-2 API (/json/2/<model>/<method>, Odoo 19 and later)
    "/xmlrpc",  # the XML-RPC API (/xmlrpc/2/common, /xmlrpc/2/object, /xmlrpc/db)
    "/jsonrpc",  # the JSON-RPC API
    "/websocket",  # the bus: live notifications of signed-in users (Odoo 16 and later)
    "/longpolling",  # the bus of Odoo 15 and earlier
    "/bus",
    "/report",  # PDF and HTML reports printed from the back office
    "/doc",  # the API documentation (Odoo 19)
    CONNECT_PATH,  # the panel's Connect as links
)
"""Paths (and everything below them) that make up the back office. The rest is the public website."""

PUBLIC_UNDER_WEB = (
    "/web/assets/",  # the CSS and JavaScript bundles of the website
    "/web/bundle/",  # bundles the website loads later
    "/web/static/",  # static files of the web module
    "/web/image",  # images on website pages (/web/image/<model>/<id>/<field>)
    "/web/content",  # files linked from website pages
    "/web/binary/company_logo",  # the company's logo, on the website and in e-mails
    "/web/webclient/translations",  # translations the website's scripts load
    "/web/webclient/locale",
    "/web/health",  # Odoo's health check, for uptime monitors
)
"""Paths under /web that the public website uses too: never back office."""

PRESETS: dict[str, tuple[str, ...]] = {
    "payments": ("/payment/",),  # payment providers' notifications and webhooks (/payment/<provider>/...)
    "webhooks": ("/web/hook/",),  # webhooks of automation rules (/web/hook/<uuid>, Odoo 17 and later)
    "portal": ("/web/login", "/web/signup", "/web/reset_password", "/my"),  # customers' portal sign-in
    "livechat": ("/websocket", "/longpolling/", "/bus/", "/im_livechat/"),  # live chat with website visitors
    "mail_links": ("/mail/", "/mailing/"),  # links in e-mails: view, unsubscribe, tracking
}
"""Exception paths offered as one choice each."""


def normalize(raw_path: str) -> str:
    """The path as Odoo would route it, for comparing: without query, percent-decoded (twice-encoded too), with
    "." and ".." segments resolved, repeated slashes merged, backslashes as slashes, lower case."""
    path = raw_path.split("?", 1)[0].split("#", 1)[0]
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    path = path.replace("\\", "/").replace("\x00", "")
    segments: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    result = "/" + "/".join(segments)
    if path.endswith("/") and segments:
        result += "/"
    return result.lower()


def matches(path: str, entry: str) -> bool:
    """`path` (normalized) is `entry` or below it. An entry ending with "/" matches below it only."""
    entry = entry.lower()
    if entry.endswith("/"):
        return path.startswith(entry)
    return path == entry or path.startswith(entry + "/")


def classify(raw_path: str) -> str:
    """ "back_office" or "public" (see BACK_OFFICE and PUBLIC_UNDER_WEB)."""
    path = normalize(raw_path)
    if path.startswith("/web") and any(matches(path, entry) for entry in PUBLIC_UNDER_WEB):
        return "public"
    return "back_office" if any(matches(path, entry) for entry in BACK_OFFICE) else "public"


# --------------------------------------------------------------------------- addresses


def parse_ip(value: str | None) -> IpAddress | None:
    """An address (IPv4-mapped IPv6 as IPv4), or None when `value` is not one."""
    if not value:
        return None
    text = value.strip()
    if text.startswith("[") and "]" in text:
        text = text[1 : text.index("]")]
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def parse_network(value: str) -> IpNetwork:
    """An address or a range (CIDR) as a network; ValueError when it is neither. Host bits must be zero."""
    text = value.strip()
    if not text or len(text) > 64:
        raise ValueError("empty")
    network = ipaddress.ip_network(text, strict=True)
    if isinstance(network, ipaddress.IPv6Network) and network.network_address.ipv4_mapped and network.prefixlen >= 96:
        mapped = network.network_address.ipv4_mapped
        return ipaddress.ip_network(f"{mapped}/{network.prefixlen - 96}", strict=False)
    return network


def visitor_ip(request: web.Request, trusted_header: str | None) -> str:
    """The visitor's address as the gateway trusts it (the same rule as X-Forwarded-For, gateway._forward_headers):
    the last address of the exposure's own header when there is one, else the connection's address."""
    visitor = ""
    if trusted_header:
        visitor = request.headers.get(trusted_header, "").split(",")[-1].strip()
    return visitor or request.remote or ""


def in_any(address: IpAddress | None, networks: Iterable[IpNetwork]) -> bool:
    return address is not None and any(address in network for network in networks)


# --------------------------------------------------------------------------- policies


@dataclass(frozen=True)
class Policy:
    mode: str
    scope: str
    countries: frozenset[str]
    networks: tuple[IpNetwork, ...]
    exception_paths: tuple[str, ...]
    exception_networks: tuple[IpNetwork, ...]
    version: int = 1
    no_passes: bool = False  # the client closed access: passes from the panel do not count


@dataclass(frozen=True)
class Config:
    policies: Mapping[str, Policy] = field(default_factory=dict)
    pass_key: bytes = b""
    geo_missing: str = "open"
    show_ip: bool = False
    contact: str = ""
    pass_hours: float = 8.0


def parse_policy(raw: Mapping[str, Any]) -> Policy:
    """A policy from its document (routes.json, or access_rules.gateway_policy); ValueError when not valid."""
    mode, scope = raw.get("mode"), raw.get("scope")
    if mode not in MODES or scope not in SCOPES:
        raise ValueError("unknown mode or scope")
    countries = raw.get("countries") or []
    networks = raw.get("networks") or []
    paths = raw.get("exception_paths") or []
    exceptions = raw.get("exception_networks") or []
    if not all(isinstance(x, list) for x in (countries, networks, paths, exceptions)):
        raise ValueError("lists expected")
    if (
        len(countries) > MAX_COUNTRIES
        or len(networks) + len(exceptions) > 2 * MAX_NETWORKS
        or len(paths) > 4 * MAX_PATHS
    ):
        raise ValueError("too many entries")
    codes = frozenset(str(c).upper() for c in countries)
    if not all(len(c) == 2 and c.isascii() and c.isalpha() for c in codes):
        raise ValueError("not a country code")
    clean_paths = tuple(str(p).lower() for p in paths)
    if not all(p.startswith("/") and len(p) <= 200 for p in clean_paths):
        raise ValueError("not a path")
    version = raw.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("not a version")
    return Policy(
        mode=str(mode),
        scope=str(scope),
        countries=codes,
        networks=tuple(parse_network(str(n)) for n in networks),
        exception_paths=clean_paths,
        exception_networks=tuple(parse_network(str(n)) for n in exceptions),
        version=version,
        no_passes=raw.get("no_passes") is True,
    )


def parse_section(raw: Any) -> Config:
    """The "access" part of routes.json; ValueError when it is not as expected. None or absent: no rules."""
    if raw is None:
        return Config()
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ValueError("not an access section of a known format")
    key = bytes.fromhex(str(raw.get("pass_key") or ""))
    if key and len(key) < 32:
        raise ValueError("pass key too short")
    instances = raw.get("instances") or {}
    if not isinstance(instances, dict):
        raise ValueError("no instances")
    geo_missing = raw.get("geo_missing", "open")
    if geo_missing not in ("open", "closed"):
        raise ValueError("geo_missing")
    hours = raw.get("pass_hours", 8)
    if not isinstance(hours, int | float) or isinstance(hours, bool) or not 0 < hours <= 24:
        raise ValueError("pass_hours")
    return Config(
        policies={str(name): parse_policy(entry) for name, entry in instances.items() if isinstance(entry, dict)},
        pass_key=key,
        geo_missing=str(geo_missing),
        show_ip=raw.get("show_ip") is True,
        contact=str(raw.get("contact") or "")[:300],
        pass_hours=float(hours),
    )


CONFIG = Config()
_PROBLEM = {"text": ""}


def load(raw: Any) -> bool:
    """Use the access section `raw` from now on; when it is not valid, keep the one used before (and say so once).
    True when it was taken."""
    global CONFIG
    try:
        CONFIG = parse_section(raw)
    except (ValueError, TypeError) as error:
        if str(error) != _PROBLEM["text"]:
            _PROBLEM["text"] = str(error)
            print(f"Access rules not used ({error}); keeping the ones read before", flush=True)
        return False
    _PROBLEM["text"] = ""
    return True


@dataclass(frozen=True)
class Decision:
    allowed: bool
    why: str  # "public", "exception", "pass", "address", "country", "geo_missing", "blocked"
    country: str | None = None


def decide(
    policy: Policy,
    address: IpAddress | None,
    path: str,
    *,
    country: str | None,
    geo_ready: bool,
    geo_missing: str = "open",
    has_pass: bool = False,
) -> Decision:
    """Whether a request to `path` from `address` (country `country`, when the database is `geo_ready`) passes."""
    normalized = normalize(path)
    if policy.scope == "back_office" and classify(normalized) == "public":
        return Decision(True, "public", country)
    if any(matches(normalized, entry) for entry in policy.exception_paths):
        return Decision(True, "exception", country)
    if in_any(address, policy.exception_networks):
        return Decision(True, "exception", country)
    if has_pass:
        return Decision(True, "pass", country)
    if policy.mode in ("ips", "either") and in_any(address, policy.networks):
        return Decision(True, "address", country)
    if policy.mode in ("countries", "either"):
        if not geo_ready:
            if geo_missing == "open":
                return Decision(True, "geo_missing", country)
        elif country is not None and country in policy.countries:
            return Decision(True, "country", country)
    return Decision(False, "blocked", country)


# --------------------------------------------------------------------------- passes


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(key: bytes, body: str) -> bytes:
    return hmac.new(key, b"sudoo-access-pass|" + body.encode(), hashlib.sha256).digest()


def sign(key: bytes, payload: Mapping[str, Any]) -> str:
    body = _b64(json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_b64(_signature(key, body))}"


def host_name(host: str) -> str:
    """The host name of a Host header or address, without port, lower case."""
    text = host.strip().lower()
    if text.startswith("["):
        return text[1 : text.index("]")] if "]" in text else text
    if text.count(":") == 1:
        text = text.split(":", 1)[0]
    return text.rstrip(".")


def issue(
    key: bytes,
    instance: str,
    host: str,
    version: int,
    user: str,
    now: float | None = None,
    seconds: float = PASS_TOKEN_SECONDS,
) -> str:
    """A pass token for the link the panel opens (kind "t": once, for PASS_TOKEN_SECONDS)."""
    now = time.time() if now is None else now
    payload = {
        "k": "t", "i": instance, "h": host_name(host), "e": int(now + seconds), "n": secrets.token_urlsafe(12),
        "p": version, "u": user[:80],
    }  # fmt: skip
    return sign(key, payload)


def verify(
    key: bytes, token: str, *, kind: str, instance: str, host: str, version: int, now: float | None = None
) -> tuple[dict[str, Any] | None, str]:
    """(payload, "") when `token` is a valid pass of `kind` for this instance, host and rules version, else
    (None, why). Never says more than "forged" about a bad signature."""
    if not key or not token or len(token) > 1024 or token.count(".") != 1:
        return None, "malformed"
    body, signature = token.split(".", 1)
    try:
        if not hmac.compare_digest(_unb64(signature), _signature(key, body)):
            return None, "forged"
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None, "malformed"
    if not isinstance(payload, dict) or payload.get("k") != kind:
        return None, "wrong kind"
    if payload.get("i") != instance:
        return None, "other instance"
    if payload.get("h") != host_name(host):
        return None, "wrong host"
    if payload.get("p") != version:
        return None, "rules changed"
    expires = payload.get("e")
    if not isinstance(expires, int) or (time.time() if now is None else now) >= expires:
        return None, "expired"
    return payload, ""


_USED_NONCES: dict[str, float] = {}
_NONCE_LOCK = threading.Lock()


def use_nonce(nonce: str, expires: float, now: float | None = None) -> bool:
    """True the first time `nonce` is used (until it expires); False after."""
    now = time.time() if now is None else now
    with _NONCE_LOCK:
        for old in [n for n, until in _USED_NONCES.items() if until <= now]:
            del _USED_NONCES[old]
        if nonce in _USED_NONCES or len(_USED_NONCES) > 100_000:
            return False
        _USED_NONCES[nonce] = expires
        return True


# passes used at the gateway, for the audit log: the panel's process records them at once (SINK), the gateway
# service keeps them until the panel collects them (gateway_service.ServiceHost.status, gateway_link.sync)
SINK: Callable[[dict[str, Any]], None] | None = None
_USED: deque[dict[str, Any]] = deque(maxlen=500)


def _report(event: dict[str, Any]) -> None:
    if SINK is not None:
        try:
            SINK(event)
            return
        except Exception as error:  # noqa: BLE001 - recording never stops a visit
            print(f"access pass audit: {error!r}", flush=True)
    _USED.append(event)


def take_used() -> list[dict[str, Any]]:
    """The pass uses recorded since the last call."""
    taken = list(_USED)
    _USED.clear()
    return taken


# --------------------------------------------------------------------------- the gateway's entry point


def _safe_next(value: str) -> str:
    """Where a pass link sends the visitor: a path on the same site only (never another site)."""
    if not value.startswith("/") or value.startswith("//") or "\\" in value or len(value) > 1000:
        return "/odoo"
    if any(ord(c) < 32 for c in value):
        return "/odoo"
    return value


def _reference() -> str:
    return secrets.token_hex(4).upper()


_BLOCK_LOG: dict[str, tuple[float, int]] = {}  # instance -> (minute, lines written)


def _log_block(name: str, reference: str, address: str, country: str | None, path: str) -> None:
    """One line in the gateway's log per blocked request, at most 20 per instance and minute (scans are noisy)."""
    minute = time.time() // 60
    seen = _BLOCK_LOG.get(name)
    count = seen[1] + 1 if seen and seen[0] == minute else 1
    _BLOCK_LOG[name] = (minute, count)
    if count <= 20:
        print(f"Access rules: blocked {reference} {name} {address or '-'} {country or '--'} {path[:120]}", flush=True)


def check(request: web.Request, name: str, trusted_header: str | None, https: bool) -> web.StreamResponse | None:
    """The answer when the request may not reach instance `name` (or is a pass link), else None."""
    from sudoo.instances import status_pages

    config = CONFIG
    policy = config.policies.get(name)
    if request.path == PASS_PATH:
        return _pass_link(request, name, policy, config, trusted_header, https)
    if policy is None:
        return None
    address_text = visitor_ip(request, trusted_header)
    address = parse_ip(address_text)
    country = geoip.lookup(address)  # with an address rule, only shown on the blocked page
    has_pass = not policy.no_passes and _cookie_pass(request, name, policy, config)
    decision = decide(
        policy, address, request.raw_path, country=country, geo_ready=geoip.ready(), geo_missing=config.geo_missing,
        has_pass=has_pass,
    )  # fmt: skip
    if decision.allowed:
        return None
    reference = _reference()
    _log_block(name, reference, address_text, country, normalize(request.raw_path))
    return status_pages.render_blocked(
        request, name, country=country, address=address_text if config.show_ip else None, reference=reference,
        contact=config.contact,
    )  # fmt: skip


def _cookie_pass(request: web.Request, name: str, policy: Policy, config: Config) -> bool:
    token = request.cookies.get(PASS_COOKIE, "")
    if not token:
        return False
    payload, _ = verify(
        config.pass_key, token, kind="c", instance=name, host=request.host, version=policy.version
    )  # fmt: skip
    return payload is not None


def _pass_link(
    request: web.Request, name: str, policy: Policy | None, config: Config, trusted_header: str | None, https: bool
) -> web.StreamResponse:
    """/__sudoo/pass?token=...&next=/path: a pass from the panel. A valid token becomes the pass cookie; either way the
    visitor goes on to `next`, where the rules decide as usual (a refused pass simply does not help)."""
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}
    if request.method != "GET":
        return web.Response(status=405, text="Use GET.", headers=headers)
    target = _safe_next(request.query.get("next", "/odoo"))
    answer = web.Response(status=303, headers={**headers, "Location": target})
    if policy is None:
        return answer  # no rules (any more): nothing to pass
    address = visitor_ip(request, trusted_header)
    payload, why = verify(
        config.pass_key, request.query.get("token", ""), kind="t", instance=name, host=request.host,
        version=policy.version,
    )  # fmt: skip
    if payload is not None and policy.no_passes:
        payload, why = None, "closed by the client"
    if payload is not None and not use_nonce(str(payload.get("n")), float(payload["e"]) + 1):
        payload, why = None, "used already"
    event = {"instance": name, "ip": address[:64], "at": int(time.time()), "ok": payload is not None, "why": why}
    if payload is not None:
        event["user"] = str(payload.get("u") or "")[:80]
        expires = int(time.time() + config.pass_hours * 3600)
        cookie = sign(
            config.pass_key,
            {"k": "c", "i": name, "h": host_name(request.host), "e": expires, "n": payload["n"], "p": policy.version},
        )
        answer.set_cookie(
            PASS_COOKIE, cookie, path="/", httponly=True, samesite="Lax", secure=https,
            max_age=int(config.pass_hours * 3600),
        )  # fmt: skip
    _report(event)
    return answer
