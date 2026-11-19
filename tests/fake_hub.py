"""A small stand-in for hub.sudoo.sh, served on 127.0.0.1 for the tests.

It speaks the hub's panel contract v2 (sudoo-hub README: "Signed panel requests", "Signed answers", "Signing keys",
"Leases", "Linking", "Panel contract"): it checks every signed request the way the hub does (Ed25519 over method,
path, installation, timestamp, nonce and body hash; 5-minute window; nonces once; a current lease for
/installations/*) and **signs every answer** to a signed request (refusals and package downloads included) with its
active key. It lists its keys with states and endorsements and can rotate or revoke them; registers installations
with their fingerprint, flags a heartbeat from another machine, answers heartbeats with 72-hour EdDSA leases
(licences with run_until and perpetual, commands, flags), links and unlinks (queueing a signed remove_paid_plugins
command), takes acknowledgements, revokes keys (401 key_revoked), serves the panel catalogue with each plugin's
action, plans with monthly equivalents and savings, icons and recommendations, the billing context, install answers
and signed packages (optionally through a redirect, as with S3), and opens checkout sessions that the test then pays,
declines, cancels or lets expire. It is written apart from the panel's code, so the tests check both sides agree.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

LEASE_HOURS = 72
OFFLINE_GRACE_HOURS = 96


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def iso(timestamp: float | None) -> str | None:
    return None if timestamp is None else datetime.fromtimestamp(timestamp, UTC).isoformat()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def make_package(
    plugin_id: str,
    version: str,
    *,
    module: str = "hello_plugin",
    group: str = "sudoo.mailers",
    entry: str = "hello-mail",
    tier: str = "free",
    extra: dict[str, bytes] | None = None,
    manifest: dict[str, Any] | None = None,
) -> bytes:
    """A .sudoo package (zip): manifest.json, pyproject.toml and a module with one plugin class."""
    data = {
        "id": plugin_id,
        "name": "Hello mailer",
        "version": version,
        "sdk_version": ">=0.1.0",
        "min_sudoo_version": "0.1.0",
        "author": {"name": "Test"},
        "description": "A mailer that only says hello, for tests.",
        "license": "MIT",
        "type": "mailer",
        "entry_points": {group: {entry: f"{module}.plugin:Hello"}},
        "permissions": [],
        "tier": tier,
        **({"price": 500} if tier == "paid" else {}),
        **(manifest or {}),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(data))
        archive.writestr("pyproject.toml", f'[project]\nname = "{module}"\nversion = "{version}"\n')
        archive.writestr(f"{module}/__init__.py", "")
        archive.writestr(
            f"{module}/plugin.py",
            "class Hello:\n"
            "    ready = True\n"
            "    def __init__(self, settings):\n"
            "        self.settings = settings\n"
            f"    version = {version!r}\n",
        )
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def plan(kind: str, price_cents: int, **extra: Any) -> dict[str, Any]:
    """A plan as the hub lists it (with its interval, monthly equivalent; savings are filled in by the listing)."""
    interval = {"monthly": "month", "yearly": "year", "fixed_term": "days", "one_time": None}[kind]
    monthly = {"monthly": price_cents, "yearly": round(price_cents / 12), "one_time": None}.get(kind)
    if kind == "fixed_term":
        monthly = round(price_cents * (365.25 / 12) / extra.get("period_days", 30))
    return {"id": str(uuid.uuid4()), "kind": kind, "price_cents": price_cents, "currency": "usd",
            "period_days": extra.get("period_days"), "interval": interval, "perpetual": kind == "one_time",
            "monthly_equivalent_cents": monthly, "savings_percent": None, **extra}  # fmt: skip


@dataclass
class Listing:
    id: str
    name: str
    tier: str = "free"
    type: str = "mailer"
    versions: dict[str, bytes] = field(default_factory=dict)
    plans: list[dict[str, Any]] = field(default_factory=list)
    publisher: dict[str, Any] = field(
        default_factory=lambda: {"name": "Sudoo.sh", "slug": "sudoo", "verified": True, "official": True}
    )
    icon: bytes | None = None
    countries: list[str] = field(default_factory=list)

    @property
    def latest(self) -> str:
        return sorted(self.versions, key=lambda v: tuple(int(p) for p in v.split(".")))[-1]


@dataclass
class HubKey:
    private: Ed25519PrivateKey
    state: str = "active"
    not_before: int = field(default_factory=lambda: int(time.time()) - 60)
    not_after: int | None = None
    endorsed_by: str | None = None
    endorsement: str | None = None

    @property
    def public(self) -> str:
        return b64url(raw_public(self.private.public_key()))

    @property
    def key_id(self) -> str:
        return hashlib.sha256(raw_public(self.private.public_key())).hexdigest()[:16]

    def sign(self, message: bytes) -> str:
        return b64url(self.private.sign(message))


@dataclass
class PremiumBuild:
    """A premium package the hub offers (PREMIUM_DESIGN "Hub contract")."""

    id: str
    version: str
    package: bytes
    python_tag: str = "py3"
    platform: str = "any"
    features: list[str] = field(default_factory=list)


def make_premium_package(
    package_id: str,
    version: str,
    *,
    python_tag: str = "py3",
    platform: str = "any",
    files: dict[str, bytes] | None = None,
    entry_points: dict[str, dict[str, str]] | None = None,
    ui: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
) -> bytes:
    """A premium package (zip): manifest.json, a module sudoo_premium_<id> with a blueprint, and optional web files."""
    module = f"sudoo_premium_{package_id}"
    sources = (
        files
        if files is not None
        else {
            f"src/{module}/__init__.py": (
                b"from flask import Blueprint, jsonify\n"
                b"blueprint = Blueprint('premium_" + package_id.encode() + b"', __name__)\n"
                b"@blueprint.get('/premium-hello/" + package_id.encode() + b"')\n"
                b"def hello():\n"
                b"    return jsonify(data={'hello': '" + package_id.encode() + b"'})\n"
                b"STARTED = []\n"
                b"def start():\n"
                b"    STARTED.append(1)\n"
            ),
            "ui/page.js": b"export function Page() { return null; }\n",
        }
    )
    data = {
        "id": package_id, "kind": "sudoo-premium", "name": f"Premium {package_id}", "version": version,
        "features": [], "python_tag": python_tag, "platform": platform, "min_sudoo_version": "0.1.0",
        "entry_points": entry_points if entry_points is not None else {
            "sudoo.premium.blueprints": {package_id: f"{module}:blueprint"},
            "sudoo.premium.jobs": {package_id: f"{module}:start"},
        },
    }  # fmt: skip
    web = {name: hashlib.sha256(content).hexdigest() for name, content in sources.items() if name.startswith("ui/")}
    data["ui"] = ui if ui is not None else {
        "routes": [{"path": f"/{package_id}", "module": "ui/page.js", "export": "Page"}] if "ui/page.js" in web else [],
        "files": web,
    }  # fmt: skip
    data.update(manifest or {})
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("manifest.json", json.dumps(data))
        for name, content in sources.items():
            archive.writestr(name, content)
    return out.getvalue()


class FakeHub:
    def __init__(self) -> None:
        self.keys: list[HubKey] = [HubKey(Ed25519PrivateKey.generate())]
        self.installations: dict[str, Ed25519PublicKey] = {}
        self.fingerprints: dict[str, str] = {}
        self.flagged: set[str] = set()
        self.revoked: set[str] = set()
        self.nonces: set[tuple[str, str]] = set()
        self.listings: dict[str, Listing] = {}
        self.licences: dict[str, dict[str, Any]] = {}  # plugin id -> {state, expires_at, grace_until, perpetual}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.receipts: list[dict[str, Any]] = []
        self.commands: dict[str, dict[str, Any]] = {}  # id -> {kind, issued_at, payload, acked}
        self.acks: list[tuple[str, dict[str, Any]]] = []
        self.views: list[str] = []
        # licence acceptances sent by panels; None: this hub does not take them (404), like hubs before they did
        self.licence_acceptances: list[dict[str, Any]] | None = None
        self.reported: dict[str, str] = {}  # plugin id -> version, from the last heartbeat
        self.heartbeat_seen = False
        self.link_codes = {"LINKCODE42": "Ada's company"}
        self.linked: str | None = None
        self.payments_enabled = True
        self.heartbeat_refusal: str | None = None  # e.g. "integrity_violation"
        self.tamper_downloads = False
        self.redirect_downloads = False
        self.unsigned_answers = False  # an answer without Sudoo-Signature, as if from a proxy in between
        self.answer_skew = 0  # seconds added to the signed time of answers
        self.now: float | None = None  # the hub's clock for leases (None: real time)
        self.heartbeats: list[dict[str, Any]] = []
        # support access: what the panel reported on its channel, the requests waiting for it, the open sessions,
        # and the answers it sent
        self.support_reports: list[dict[str, Any]] = []
        self.support_queue: list[dict[str, Any]] = []
        self.support_sessions: list[dict[str, Any]] = []
        self.support_answers: dict[str, dict[str, Any]] = {}
        self.requests: list[str] = []
        # the lease's "entitlements" claim (sudoo-hub docs/PLANS.md, "In the signed licence"); None: none, as before
        self.entitlements: dict[str, Any] | None = None
        # past the account's daily API quota: counted panel calls answer 429 api_quota_exceeded with Retry-After
        self.quota_retry_after: int | None = None
        # premium packages: the premium signing key (separate from the hub keys), the builds offered, the bindings
        self.premium_key = HubKey(Ed25519PrivateKey.generate())
        self.premium: dict[str, PremiumBuild] = {}
        self.premium_binding_days = 30
        self.premium_requests: list[str] = []
        # managed subdomains (hub contract "Managed subdomains")
        self.subdomains_enabled = True
        self.subdomains_base = "sudoo.test"
        self.subdomains_company: str | None = None
        self.subdomains: dict[str, dict[str, Any]] = {}  # hostname -> {branch, stage, company, server_id, ipv4, ipv6}
        self.subdomain_limit: int | None = 5
        self.subdomains_foreign: set[str] = set()  # names another account holds
        self.subdomain_release_failures = 0  # the next releases answer 502 dns_failed
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> FakeHub:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def publish(self, listing: Listing) -> Listing:
        monthly = next((p["price_cents"] for p in listing.plans if p["kind"] == "monthly"), None)
        for item in listing.plans:  # the hub's savings_percent: saved against the monthly plan
            if monthly and item["kind"] != "monthly" and item["monthly_equivalent_cents"]:
                saved = round(100 * (monthly - item["monthly_equivalent_cents"]) / monthly)
                item["savings_percent"] = saved if saved > 0 else None
        self.listings[listing.id] = listing
        return listing

    # ------------------------------------------------------------ keys

    @property
    def key(self) -> HubKey:
        return next(k for k in self.keys if k.state == "active")

    @property
    def key_id(self) -> str:
        return self.key.key_id

    def rotate(self, *, revoke_previous: bool = False, endorse: bool = True) -> HubKey:
        """`sudoo-hub keys rotate`: a new active key endorsed by the previous one, which retires (or is revoked)."""
        previous = self.key
        previous.state = "revoked" if revoke_previous else "retiring"
        previous.not_after = int(time.time()) + 30 * 86400
        new = HubKey(Ed25519PrivateKey.generate(), not_before=int(time.time()), endorsed_by=previous.key_id)
        message = "\n".join(("sudoo-hub-key-v1", new.key_id, new.public, str(new.not_before))).encode()
        new.endorsement = previous.sign(message) if endorse else b64url(b"\0" * 64)
        self.keys.append(new)
        return new

    def keys_out(self) -> list[dict[str, Any]]:
        now = time.time()
        return [{"algorithm": "Ed25519", "key_id": k.key_id, "public_key": k.public,
                 "state": "expired" if k.state == "retiring" and k.not_after and k.not_after <= now else k.state,
                 "not_before": iso(k.not_before), "not_after": iso(k.not_after), "revoked_at": None,
                 "endorsed_by": k.endorsed_by, "endorsement": k.endorsement} for k in self.keys]  # fmt: skip

    # ------------------------------------------------------------ the hub's rules

    def _clock(self) -> float:
        return self.now if self.now is not None else time.time()

    def standing(self, plugin_id: str, installation: str | None = None) -> str:
        listing = self.listings.get(plugin_id)
        if listing is None:
            return "unknown"
        if listing.tier == "free":
            return "free"
        if installation in self.flagged:
            return "blocked"
        licence = self.licences.get(plugin_id)
        if licence is None or licence["state"] == "revoked":
            return "unlicensed"
        if licence.get("perpetual"):
            return "licensed"
        now = self._clock()
        end = licence.get("grace_until") or licence.get("expires_at") or now + 1
        in_grace = licence.get("expires_at") and licence["expires_at"] <= now < end
        return "grace" if in_grace else "licensed" if now < end else "expired"

    def usable(self, plugin_id: str) -> bool:
        return self.standing(plugin_id) in ("licensed", "grace")

    def lease_licences(self, installation: str, expires: int) -> dict[str, dict[str, Any]]:
        if installation in self.flagged:
            return {}
        out = {}
        for plugin_id, licence in self.licences.items():
            if licence["state"] == "revoked":
                continue
            end = licence.get("grace_until") or licence.get("expires_at")
            offline = expires + (OFFLINE_GRACE_HOURS * 3600 if self.usable(plugin_id) else 0)
            out[plugin_id] = {"state": licence["state"], "expires_at": licence.get("expires_at"),
                              "grace_until": licence.get("grace_until"), "perpetual": bool(licence.get("perpetual")),
                              "run_until": offline if end is None else min(offline, int(end))}  # fmt: skip
        return out

    def command_out(self, command_id: str, installation: str) -> dict[str, Any]:
        command = self.commands[command_id]
        digest = hashlib.sha256(canonical(command["payload"])).hexdigest()
        message = "\n".join(("sudoo-hub-command-v1", command_id, installation, command["kind"],
                             str(command["issued_at"]), digest)).encode()  # fmt: skip
        return {"id": command_id, "kind": command["kind"], "issued_at": iso(command["issued_at"]),
                "payload": command["payload"], "key_id": self.key_id, "signature": self.key.sign(message)}  # fmt: skip

    def pending(self, installation: str) -> list[str]:
        return [cid for cid, c in self.commands.items() if c["installation"] == installation and not c["acked"]]

    def lease(self, installation_id: str, plugins: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        now = int(self._clock())
        expires = now + LEASE_HOURS * 3600
        licences = self.lease_licences(installation_id, expires)
        flags = ["fingerprint_mismatch"] if installation_id in self.flagged else []
        commands = [{**self.commands[cid], "id": cid} for cid in self.pending(installation_id)]
        header = {"alg": "EdDSA", "kid": self.key_id, "typ": "sudoo-lease+jwt"}
        claims = {
            "iss": self.url,
            "aud": f"sudoo-panel:{installation_id}",
            "sub": installation_id,
            "iat": now,
            "nbf": now,
            "exp": expires,
            "jti": uuid.uuid4().hex,
            "status": "active",
            "plugins": {item["id"]: self.standing(item["id"], installation_id) for item in plugins},
            "licenses": licences,  # every licence held, installed or not
            "commands": [{"id": c["id"], "kind": c["kind"], "issued_at": c["issued_at"], "payload": c["payload"]}
                         for c in commands],  # fmt: skip
            "flags": flags,
            "next_heartbeat": now + 900,
        }
        if self.entitlements is not None:
            claims["entitlements"] = {"valid_until": min(expires, now + 24 * 3600), **self.entitlements}
        signing_input = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(claims).encode())}"
        return f"{signing_input}.{self.key.sign(signing_input.encode())}", {"licenses": licences, "flags": flags}

    def sign_package(self, plugin_id: str, version: str, package: bytes) -> tuple[str, str]:
        sha = hashlib.sha256(package).hexdigest()
        message = b"\n".join((b"sudoo-plugin-v1", plugin_id.encode(), version.encode(), sha.encode()))
        return sha, self.key.sign(message)

    def subdomains_out(self) -> dict[str, Any]:
        return {"enabled": self.subdomains_enabled, "base_domain": self.subdomains_base if self.subdomains_enabled
                else None, "company": self.subdomains_company, "limit": self.subdomain_limit,
                "used": len(self.subdomains), "subdomains": [self.subdomain_out(h) for h in sorted(self.subdomains)]}  # fmt: skip

    def subdomain_out(self, hostname: str) -> dict[str, Any]:
        found = self.subdomains[hostname]
        return {"hostname": hostname, "branch": found["branch"], "stage": found["stage"], "company": found["company"],
                "ipv4": found.get("ipv4") or (None if found.get("ipv6") else "203.0.113.7"), "ipv6": found.get("ipv6"),
                "proxied": False, "server_id": found["server_id"],
                "created_at": "2026-11-19T10:00:00Z", "updated_at": "2026-11-19T10:00:00Z"}  # fmt: skip

    def premium_included(self) -> list[str]:
        return list((self.entitlements or {}).get("premium") or [])

    def premium_offer(self, build: PremiumBuild) -> dict[str, Any]:
        sha = hashlib.sha256(build.package).hexdigest()
        statement = "\n".join(("sudoo-premium-v1", build.id, build.version, sha, build.python_tag, build.platform))
        query = f"version={build.version}&python_tag={build.python_tag}&platform={build.platform}"
        return {"id": build.id, "name": f"Premium {build.id}", "version": build.version, "features": build.features,
                "python_tag": build.python_tag, "platform": build.platform, "package_sha256": sha,
                "size": len(build.package), "signature": self.premium_key.sign(statement.encode()),
                "signing_key_id": self.premium_key.key_id, "min_sudoo_version": "0.1.0", "max_sudoo_version": None,
                "download_path": f"/api/v1/installations/premium/{build.id}/download?{query}"}  # fmt: skip

    def premium_binding(self, installation: str, fingerprint: str, build: PremiumBuild) -> tuple[str, str]:
        now = int(self._clock())
        binding = {"v": 1, "kind": "sudoo-premium-binding", "server_id": installation,
                   "hardware_fingerprint": fingerprint, "package_id": build.id, "version": build.version,
                   "sha256": hashlib.sha256(build.package).hexdigest(), "iat": now,
                   "exp": now + self.premium_binding_days * 86400}  # fmt: skip
        raw = canonical(binding)
        return b64url(raw), self.premium_key.sign(b"sudoo-premium-binding-v1\n" + raw)

    def install_info(self, listing: Listing, version: str) -> dict[str, Any]:
        sha, signature = self.sign_package(listing.id, version, listing.versions[version])
        return {"plugin_id": listing.id, "version": version, "tier": listing.tier,
                "download_path": f"/api/v1/catalog/{listing.id}/download/{version}", "package_sha256": sha,
                "package_size": len(listing.versions[version]), "signature": signature,
                "signing_key_id": self.key_id, "requires": [], "license": self.licences.get(listing.id)}  # fmt: skip

    def unlink(self, installation: str, reason: str = "unlinked") -> None:
        """What the hub does on POST /unlink or DELETE /account/installations/{id}."""
        paid = sorted(self.licences)
        for licence in self.licences.values():
            licence["state"] = "revoked"
        self.linked = None
        command_id = str(uuid.uuid4())
        self.commands[command_id] = {"kind": "remove_paid_plugins", "issued_at": int(time.time()),
                                     "payload": {"plugins": paid, "reason": reason}, "acked": False,
                                     "installation": installation}  # fmt: skip

    # ------------------------------------------------------------ checkout sessions (paid on app.sudoo.sh)

    def pay(self, session_id: str) -> None:
        """The account owner paid on the checkout page: the licence and the receipt exist."""
        session = self.sessions[session_id]
        now = int(self._clock())
        perpetual = session["plan_kind"] == "one_time"
        licence = {"state": "active", "perpetual": perpetual,
                   "expires_at": None if perpetual else now + 30 * 86400,
                   "grace_until": None if perpetual else now + 37 * 86400}  # fmt: skip
        self.licences[session["plugin_id"]] = licence
        number = f"SR-{len(self.receipts) + 1:06d}"
        listing = self.listings[session["plugin_id"]]
        session.update(status="succeeded", url=None, reason=None, completed_at=iso(now), receipt_number=number,
                       license={k: iso(v) if k.endswith(("_at", "_until")) else v for k, v in licence.items()},
                       install=self.install_info(listing, listing.latest))  # fmt: skip
        self.receipts.append({"id": uuid.uuid4().hex, "kind": "receipt", "number": number, "issued_at": iso(now),
                              "plugin_id": listing.id, "plugin_name": listing.name,
                              "plan_kind": session["plan_kind"], "payment_kind": "purchase",
                              "total_cents": session["amount_cents"], "currency": "usd",
                              "requested_by_name": session["requested_by"]["name"]})  # fmt: skip

    def decline(self, session_id: str, reason: str = "card_declined") -> None:
        """A card was declined: the session is open again with the reason."""
        self.sessions[session_id].update(status="open", reason=reason, method="saved_card")

    def action(self, listing: Listing) -> str:
        installed = self.reported.get(listing.id)
        if installed:
            return "update" if installed != listing.latest else "installed"
        if listing.tier == "free" or self.usable(listing.id):
            return "install"
        return "renew" if listing.id in self.licences else "buy"

    # ------------------------------------------------------------ HTTP

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, data: Any = None, headers: dict[str, str] | None = None, raw: bytes | None = None) -> None:  # fmt: skip
                body = raw if raw is not None else (b"" if status == 204 else json.dumps(data).encode())
                headers = dict(headers or {})
                package_sha = headers.get("X-Sudoo-Package-SHA256") if status == 200 else None
                installation = self.headers.get("X-Sudoo-Installation", "")
                nonce = self.headers.get("X-Sudoo-Nonce", "")
                if installation and nonce and not hub.unsigned_answers:
                    ts = int(time.time()) + hub.answer_skew
                    digest = package_sha or hashlib.sha256(body).hexdigest()
                    message = "\n".join(("sudoo-hub-response-v1", self.command, self.path, installation, nonce,
                                         str(status), str(ts), digest)).encode()  # fmt: skip
                    headers["Sudoo-Signature"] = f"kid={hub.key_id}, ts={ts}, sig={hub.key.sign(message)}"
                self.send_response(status)
                if status != 204:
                    self.send_header("Content-Type", "application/zip" if raw is not None else "application/json")
                self.send_header("Content-Length", str(len(body)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def _problem(self, status: int, detail: str, code: str | None = None, headers: dict[str, str] | None = None) -> None:  # fmt: skip
                self._send(status, {"type": "about:blank", "status": status, "detail": detail, "code": code}, headers)

            def _over_quota(self) -> bool:
                """The hub's api_quota: a counted call past the daily quota answers 429 (PANEL_FREE are not)."""
                free = ("/api/v1/register", "/api/v1/heartbeat", "/api/v1/licenses/validate",
                        "/api/v1/installations/licence-acceptances", "/api/v1/keys")  # fmt: skip
                path = urlsplit(self.path).path
                if hub.quota_retry_after is None or path in free or path.startswith("/api/v1/commands/"):
                    return False
                self._problem(429, "This account has used its 10,000 hub API requests for today (UTC).",
                              "api_quota_exceeded", {"Retry-After": str(hub.quota_retry_after)})  # fmt: skip
                return True

            def _body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def _signed(self, body: bytes, key: Ed25519PublicKey | None = None, lease: bool = False) -> str | None:
                """The installation id when the request is signed as the hub requires (and, with `lease`, the
                panel has a current lease); None after answering with a refusal."""
                installation = self.headers.get("X-Sudoo-Installation", "")
                timestamp = self.headers.get("X-Sudoo-Timestamp", "")
                nonce = self.headers.get("X-Sudoo-Nonce", "")
                if installation in hub.revoked:
                    self._problem(401, "This installation's key was revoked by its owner.", "key_revoked")
                    return None
                key = key or hub.installations.get(installation)
                if key is None:
                    self._problem(401, "This installation is not registered.", "unknown_installation")
                    return None
                if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > 300:
                    self._problem(401, "Clock skew.", "clock_skew")
                    return None
                message = "\n".join(
                    ("sudoo-hub-request-v1", self.command, self.path, installation, timestamp, nonce,
                     hashlib.sha256(body).hexdigest())
                ).encode()  # fmt: skip
                try:
                    key.verify(unb64url(self.headers.get("X-Sudoo-Signature", "")), message)
                except (InvalidSignature, ValueError):
                    self._problem(401, "The request signature is not valid.", "bad_signature")
                    return None
                if (installation, nonce) in hub.nonces:
                    self._problem(401, "Replay.", "replay")
                    return None
                hub.nonces.add((installation, nonce))
                if lease and not hub.heartbeat_seen:
                    self._problem(403, "The panel has no current lease. Send a heartbeat first.", "lease_expired")
                    return None
                return installation

            def _plugin_out(self, listing: Listing) -> dict[str, Any]:
                icon = f"{hub.url}/api/v1/catalog/{listing.id}/icon?v={hashlib.sha256(listing.icon).hexdigest()[:12]}" if listing.icon else None  # fmt: skip
                return {"id": listing.id, "name": listing.name, "description": "A plugin for tests.",
                        "type": listing.type, "tier": listing.tier, "tags": [], "icon_url": icon,
                        "price_cents": listing.plans[0]["price_cents"] if listing.plans else None, "currency": "usd",
                        "publisher": listing.publisher, "requires": [], "avg_rating": 4.5, "rating_count": 2,
                        "install_count": 3, "countries": listing.countries, "languages": [], "odoo_versions": [],
                        "local": ("SA" in listing.countries) if listing.countries else None}  # fmt: skip

            def _panel_item(self, listing: Listing) -> dict[str, Any]:
                latest = {"version": listing.latest, "min_sudoo_version": "0.1.0", "max_sudoo_version": None,
                          "package_sha256": hashlib.sha256(listing.versions[listing.latest]).hexdigest(),
                          "package_size": len(listing.versions[listing.latest]), "published_at": None}  # fmt: skip
                return {**self._plugin_out(listing), "plans": listing.plans, "latest_version": latest,
                        "compatible_version": latest, "installed_version": hub.reported.get(listing.id),
                        "license": hub.licences.get(listing.id), "action": hub.action(listing)}  # fmt: skip

            def _page(self, items: list[Any]) -> None:
                self._send(200, {"data": items, "meta": {"total": len(items), "count": len(items), "limit": 50, "offset": 0}})  # fmt: skip

            def _session_out(self, session: dict[str, Any]) -> dict[str, Any]:
                return {k: v for k, v in session.items() if k != "idempotency_key"}

            def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
                hub.requests.append(f"GET {self.path}")
                if self._over_quota():
                    return None
                path = urlsplit(self.path).path
                query = parse_qs(urlsplit(self.path).query)
                parts = path.strip("/").split("/")
                if path == "/api/v1/keys":
                    return self._send(200, {"data": hub.keys_out()})
                if path == "/api/v1/categories":
                    return self._send(200, {"data": [{"type": "mailer", "count": len(hub.listings)}]})
                if parts[:3] == ["api", "v1", "catalog"] and len(parts) == 5 and parts[4] == "icon":
                    listing = hub.listings.get(parts[3])
                    if listing is None or listing.icon is None:
                        return self._problem(404, "This plugin has no icon.")
                    return self._send(200, raw=listing.icon, headers={"Content-Type": "image/png"})
                if parts[:3] == ["api", "v1", "catalog"] and len(parts) == 4:  # the public page of one plugin
                    listing = hub.listings.get(parts[3])
                    if listing is None:
                        return self._problem(404, "No such plugin.")
                    detail = {**self._plugin_out(listing), "plans": listing.plans, "permissions": ["network.outbound"],
                              "screenshots": [], "developer": {}, "latest_version": {"version": listing.latest}}  # fmt: skip
                    return self._send(200, {"data": detail})
                if parts[:3] == ["api", "v1", "catalog"] and len(parts) == 6 and parts[4] == "download":
                    return self._download(parts[3], parts[5])
                if parts[:2] == ["objects", "pkg"]:
                    return self._send(200, raw=self._bytes(hub.listings[parts[2]], parts[3]))
                if path == "/api/v1/recommendations":
                    if self._signed(b"", lease=True) is None:
                        return None
                    picks = [{"plugin": self._plugin_out(listing), "score": 1.5,
                              "reasons": ["Because you use Hello mailer", "From a verified publisher"]}
                             for listing in hub.listings.values() if listing.id not in hub.reported]  # fmt: skip
                    return self._send(200, {"data": picks[: int(query.get("limit", ["8"])[0])]})
                if path == "/api/v1/premium/keys":
                    key = hub.premium_key
                    return self._send(200, {"data": [{"algorithm": "Ed25519", "key_id": key.key_id,
                                                      "public_key": key.public, "state": key.state,
                                                      "not_before": iso(key.not_before), "not_after": None,
                                                      "revoked_at": None, "endorsed_by": None,
                                                      "endorsement": None}]})  # fmt: skip
                if parts[:3] != ["api", "v1", "installations"]:
                    return self._problem(404, "Not found.")
                installation = self._signed(b"", lease=True)
                if installation is None:
                    return None
                if path == "/api/v1/installations/subdomains":
                    return self._send(200, {"data": hub.subdomains_out()})
                if path == "/api/v1/installations/premium" or path.startswith("/api/v1/installations/premium/"):
                    return self._premium(installation, parts, query)
                if path == "/api/v1/installations/catalog":
                    items = [self._panel_item(listing) for listing in hub.listings.values()]
                    if "q" in query:
                        items = [i for i in items if query["q"][0].lower() in i["name"].lower()]
                    if query.get("installed") == ["true"]:
                        items = [i for i in items if i["installed_version"]]
                    return self._page(items)
                if path == "/api/v1/installations/billing":
                    reason = ("payments_disabled" if not hub.payments_enabled else "link_required"
                              if hub.linked is None else "fingerprint_mismatch" if installation in hub.flagged
                              else None)  # fmt: skip
                    return self._send(200, {"data": {
                        "payments_enabled": hub.payments_enabled, "linked": hub.linked is not None,
                        "account_name": hub.linked, "can_buy": reason is None, "reason": reason, "currency": "usd",
                        "manage_url": "https://app.sudoo.sh/servers"}})  # fmt: skip
                if path == "/api/v1/installations/checkout-sessions":
                    return self._page([self._session_out(s) for s in reversed(hub.sessions.values())])
                if path == "/api/v1/installations/receipts":
                    return self._page(list(reversed(hub.receipts)))
                if len(parts) == 5 and parts[3] == "checkout-sessions":
                    found = hub.sessions.get(parts[4])
                    return self._send(200, {"data": self._session_out(found)}) if found else self._problem(404, "No such session.")  # fmt: skip
                return self._problem(404, "Not found.")

            def _premium(self, installation: str, parts: list[str], query: dict[str, list[str]]) -> None:
                hub.premium_requests.append("/".join(parts[4:]) or "list")
                if installation in hub.flagged:
                    return self._problem(403, "This server reported from another machine.", "fingerprint_mismatch")
                included = hub.premium_included()
                if len(parts) == 4:
                    return self._send(200, {"data": [hub.premium_offer(b) for b in hub.premium.values()
                                                     if b.id in included]})  # fmt: skip
                build = hub.premium.get(parts[4])
                if build is None:
                    return self._problem(404, "No such premium package.", "unknown_package")
                if build.id not in included:
                    return self._problem(403, "The plan does not include this package.", "premium_not_included")
                fingerprint = (query.get("fingerprint") or [""])[0]
                if fingerprint != hub.fingerprints.get(installation):
                    return self._problem(403, "Another machine.", "fingerprint_mismatch")
                binding, signature = hub.premium_binding(installation, fingerprint, build)
                if parts[5] == "binding":
                    return self._send(200, {"data": {"binding": binding, "signature": signature,
                                                     "key_id": hub.premium_key.key_id}})  # fmt: skip
                offer = hub.premium_offer(build)
                headers = {"X-Sudoo-Package-SHA256": offer["package_sha256"], "X-Sudoo-Premium-Id": build.id,
                           "X-Sudoo-Premium-Version": build.version, "X-Sudoo-Signature": offer["signature"],
                           "X-Sudoo-Signing-Key": hub.premium_key.key_id, "X-Sudoo-Premium-Binding": binding,
                           "X-Sudoo-Premium-Binding-Signature": signature,
                           "X-Sudoo-Premium-Binding-Key": hub.premium_key.key_id}  # fmt: skip
                return self._send(200, raw=build.package, headers=headers)

            def _bytes(self, listing: Listing, version: str) -> bytes:
                package = listing.versions[version]
                return package + b"tampered" if hub.tamper_downloads else package

            def _download(self, plugin_id: str, version: str) -> None:
                installation = self._signed(b"", lease=True)
                if installation is None:
                    return
                listing = hub.listings.get(plugin_id)
                if listing is None or version not in listing.versions:
                    return self._problem(404, "No such version.")
                if listing.tier == "paid":
                    if installation in hub.flagged:
                        return self._problem(403, "This server reported from another machine.", "fingerprint_mismatch")  # fmt: skip
                    if not hub.usable(plugin_id):
                        return self._problem(403, "No licence.", "license_required")
                sha, signature = hub.sign_package(plugin_id, version, listing.versions[version])
                headers = {"X-Sudoo-Plugin-Id": plugin_id, "X-Sudoo-Plugin-Version": version,
                           "X-Sudoo-Package-SHA256": sha, "X-Sudoo-Signature": signature,
                           "X-Sudoo-Signing-Key": hub.key_id}  # fmt: skip
                if hub.redirect_downloads:
                    return self._send(307, {}, {**headers, "Location": f"{hub.url}/objects/pkg/{plugin_id}/{version}"})
                return self._send(200, raw=self._bytes(listing, version), headers=headers)

            def _subdomain(self, installation: str, data: dict[str, Any]) -> None:
                label = re.compile(r"^(?!..--)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
                if not hub.subdomains_enabled:
                    return self._problem(503, "Managed subdomains are not offered.", "subdomains_unavailable")
                branch, stage, company = data.get("branch", ""), data.get("stage"), data.get("company")
                if not label.match(branch) or stage not in ("production", "staging", "development"):
                    return self._problem(422, "Not a label.", "invalid_label")
                if set(data) - {"branch", "stage", "company", "ipv4", "ipv6"}:
                    return self._problem(422, "Unknown fields.", "invalid_request")
                pointed = {"ipv4": data.get("ipv4"), "ipv6": data.get("ipv6")}  # another server's addresses
                if not any(pointed.values()) and not any(h.get("public_ipv4") or h.get("public_ipv6")
                                                         for h in hub.heartbeats):  # fmt: skip
                    return self._problem(409, "No public address yet.", "address_unknown")
                if company and hub.subdomains_company and company != hub.subdomains_company:
                    return self._problem(409, "The company label is fixed.", "company_label_fixed")
                hub.subdomains_company = hub.subdomains_company or company or "acme"
                hostname = f"{branch}.{stage}.{hub.subdomains_company}.{hub.subdomains_base}"
                if hostname in hub.subdomains_foreign:
                    return self._problem(409, "Taken.", "hostname_taken")
                if hostname in hub.subdomains:
                    hub.subdomains[hostname].update(server_id=installation, **pointed)
                    return self._send(200, {"data": hub.subdomain_out(hostname)})
                if hub.subdomain_limit is not None and len(hub.subdomains) >= hub.subdomain_limit:
                    return self._problem(403, "The plan's managed names are used up.", "subdomain_limit")
                hub.subdomains[hostname] = {"branch": branch, "stage": stage, "company": hub.subdomains_company,
                                            "server_id": installation, **pointed}  # fmt: skip
                return self._send(201, {"data": hub.subdomain_out(hostname)})

            def do_DELETE(self) -> None:  # noqa: N802
                hub.requests.append(f"DELETE {self.path}")
                installation = self._signed(self._body(), lease=True)
                if installation is None:
                    return None
                prefix = "/api/v1/installations/subdomains/"
                if not self.path.startswith(prefix):
                    return self._problem(404, "Not found.")
                hostname = unquote(self.path[len(prefix) :])
                if hub.subdomain_release_failures:
                    hub.subdomain_release_failures -= 1
                    return self._problem(502, "The DNS records could not be changed.", "dns_failed")
                if hostname not in hub.subdomains:
                    return self._problem(404, "The account holds no such name.", "unknown_subdomain")
                del hub.subdomains[hostname]
                return self._send(204)

            def do_POST(self) -> None:  # noqa: N802
                hub.requests.append(f"POST {self.path}")
                body = self._body()
                if self._over_quota():
                    return None
                data = json.loads(body or b"{}")
                if self.path == "/api/v1/register":
                    key = Ed25519PublicKey.from_public_bytes(unb64url(data["public_key"]))
                    if self._signed(body, key) is None:
                        return None
                    hub.installations[data["installation_id"]] = key
                    hub.fingerprints.setdefault(data["installation_id"], data["hardware_fingerprint"])
                    out = {"installation_id": data["installation_id"], "status": "active",
                           "hub_key": {"key_id": hub.key_id}, "heartbeat_interval_seconds": 900}  # fmt: skip
                    return self._send(201, {"data": out})
                if self.path == "/api/v1/heartbeat":
                    installation = self._signed(body)
                    if installation is None:
                        return None
                    usage = data.get("usage")
                    if usage is not None:  # the hub's UsageIn is strict: only these fields, names as BranchName
                        allowed = {"servers", "projects", "production_branches", "production_branch_names",
                                   "instances_by_stage"}  # fmt: skip
                        stages = usage.get("instances_by_stage") or {}
                        if set(stages) - {"production", "staging", "development"}:
                            return self._problem(422, "usage.instances_by_stage: an unknown stage.")
                        names_ok = all(re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$", n)
                                       for n in usage.get("production_branch_names") or [])  # fmt: skip
                        if set(usage) - allowed or not names_ok:
                            return self._problem(422, "usage: unknown field or a name the hub does not take.")
                    hub.heartbeats.append(data)
                    if data.get("hardware_fingerprint") != hub.fingerprints.get(installation):
                        hub.flagged.add(installation)
                    if hub.heartbeat_refusal:
                        return self._problem(403, "Refused.", hub.heartbeat_refusal)
                    hub.heartbeat_seen = True
                    hub.reported = {item["id"]: item["version"] for item in data.get("plugins", [])}
                    lease, signed = hub.lease(installation, data.get("plugins", []))
                    expires = int(hub._clock()) + LEASE_HOURS * 3600
                    out = {"installation_id": installation, "status": "active", "integrity": "ok", "lease": lease,
                           "lease_expires_at": iso(expires),
                           "plugins": {i["id"]: hub.standing(i["id"], installation) for i in data.get("plugins", [])},
                           "licenses": {pid: {**item, **{k: iso(item[k]) for k in ("expires_at", "grace_until", "run_until")}}
                                        for pid, item in signed["licenses"].items()},
                           "commands": [hub.command_out(cid, installation) for cid in hub.pending(installation)],
                           "flags": signed["flags"], "next_heartbeat_seconds": 900}  # fmt: skip
                    return self._send(200, {"data": out})
                if self.path == "/api/v1/installations/subdomains":
                    installation = self._signed(body, lease=True)
                    if installation is None:
                        return None
                    return self._subdomain(installation, data)
                if self.path == "/api/v1/link":
                    installation = self._signed(body)
                    if installation is None:
                        return None
                    account = hub.link_codes.pop(data.get("code", ""), None)
                    if account is None:
                        return self._problem(404, "This code is not valid or has expired.", "bad_link_code")
                    hub.linked = account
                    return self._send(200, {"data": {"installation_id": installation, "account_name": account,
                                                     "linked_at": "2026-11-14T10:00:00Z"}})  # fmt: skip
                if self.path == "/api/v1/unlink":
                    installation = self._signed(body)
                    if installation is None:
                        return None
                    if hub.linked is not None:
                        hub.unlink(installation)
                    return self._send(204)
                if self.path.startswith("/api/v1/commands/") and self.path.endswith("/ack"):
                    installation = self._signed(body)
                    if installation is None:
                        return None
                    command_id = self.path.split("/")[4]
                    command = hub.commands.get(command_id)
                    if command is None or command["installation"] != installation:
                        return self._problem(404, "There is no such command for this installation.")
                    command["acked"] = True
                    hub.acks.append((command_id, data))
                    return self._send(204)
                if self.path == "/api/v1/installations/support/channel":
                    if self._signed(body) is None:
                        return None
                    hub.support_reports.append(data)
                    reported = {g["id"] for g in data.get("grants", [])}
                    ended = set(data.get("ended_sessions", []))
                    hub.support_sessions = [s for s in hub.support_sessions
                                            if s["grant_id"] in reported and s["id"] not in ended]  # fmt: skip
                    waiting, hub.support_queue = hub.support_queue[:20], hub.support_queue[20:]
                    if not reported:
                        waiting = []
                    return self._send(200, {"data": {"requests": waiting, "sessions": hub.support_sessions}})
                if self.path.startswith("/api/v1/installations/support/requests/"):
                    if self._signed(body) is None:
                        return None
                    hub.support_answers[self.path.split("/")[-2]] = data
                    return self._send(204)
                installation = self._signed(body, lease=True)
                if installation is None:
                    return None
                if self.path == "/api/v1/installations/views":
                    hub.views.append(data.get("plugin_id", ""))
                    return self._send(204)
                if self.path == "/api/v1/installations/licence-acceptances":
                    if hub.licence_acceptances is None:
                        return self._problem(404, "Not found.")
                    hub.licence_acceptances.extend(data.get("acceptances", []))
                    return self._send(204)
                if self.path == "/api/v1/installations/install":
                    listing = hub.listings.get(data.get("plugin_id", ""))
                    if listing is None:
                        return self._problem(404, "No such plugin.")
                    if listing.tier == "paid":
                        if hub.linked is None:
                            return self._problem(403, "Link this panel first.", "link_required")
                        if installation in hub.flagged:
                            return self._problem(403, "Flagged.", "fingerprint_mismatch")
                        if not hub.usable(listing.id):
                            return self._problem(403, "Buy it first.", "license_required")
                    version = data.get("version") or listing.latest
                    if version not in listing.versions:
                        return self._problem(404, "No such version.")
                    return self._send(200, {"data": hub.install_info(listing, version)})
                if self.path == "/api/v1/installations/checkout-sessions":
                    return self._checkout(data, installation)
                return self._problem(404, "Not found.")

            def _checkout(self, data: dict[str, Any], installation: str) -> None:
                if set(data) != {"plugin_id", "plan_id", "idempotency_key", "requested_by"}:
                    return self._problem(422, "The body takes plugin_id, plan_id, idempotency_key, requested_by.")
                if not hub.payments_enabled:
                    return self._problem(503, "Payments are switched off.", "payments_disabled")
                if hub.linked is None:
                    return self._problem(403, "Link this panel first.", "link_required")
                if installation in hub.flagged:
                    return self._problem(403, "Flagged.", "fingerprint_mismatch")
                for existing in hub.sessions.values():
                    if existing["idempotency_key"] == data["idempotency_key"]:
                        if (existing["plugin_id"], existing["plan_id"]) != (data["plugin_id"], data["plan_id"]):
                            return self._problem(409, "Key used for another plan.", "idempotency_key_reused")
                        return self._send(200, {"data": self._session_out(existing)})
                listing = hub.listings.get(data["plugin_id"])
                chosen = next((p for p in (listing.plans if listing else []) if p["id"] == data["plan_id"]), None)
                if listing is None or chosen is None:
                    return self._problem(404, "No such plugin or plan.")
                if hub.usable(listing.id):
                    return self._problem(409, "Already licensed.", "already_licensed")
                for existing in hub.sessions.values():  # the same plan while one is open: that session
                    if existing["plugin_id"] == listing.id and existing["status"] == "open":
                        if existing["plan_id"] == chosen["id"]:
                            return self._send(200, {"data": self._session_out(existing)})
                        existing.update(status="cancelled", url=None)
                session_id = str(uuid.uuid4())
                now = time.time()
                session: dict[str, Any] = {
                    "id": session_id, "installation_id": installation, "status": "open",
                    "url": f"https://app.sudoo.sh/checkout/{b64url(uuid.uuid4().bytes)}",
                    "expires_at": iso(now + 1800), "plugin_id": listing.id, "plugin_name": listing.name,
                    "plan_id": chosen["id"], "plan_kind": chosen["kind"], "plan": chosen,
                    "amount_cents": chosen["price_cents"], "currency": chosen["currency"], "method": None,
                    "reason": None, "requested_by": data["requested_by"], "license": None, "receipt_number": None,
                    "install": None, "created_at": iso(now), "completed_at": None,
                    "idempotency_key": data["idempotency_key"],
                }  # fmt: skip
                hub.sessions[session_id] = session
                return self._send(201, {"data": self._session_out(session)})

        return Handler
