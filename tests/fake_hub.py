"""A small stand-in for hub.sudoo.sh, served on 127.0.0.1 for the tests.

It speaks the hub's panel contract (sudoo-hub README, "Panels"): it checks every signed request the way the hub does
(Ed25519 over method, path, installation, timestamp, nonce and body hash; 5-minute window; nonces once), registers
installations, answers heartbeats with EdDSA leases, links panels with one-time codes, lists a catalogue, serves
signed packages (optionally through a redirect, as with S3), and sells paid plugins either by charging a saved card
or through a Checkout link. It is written apart from the panel's code, so the tests check both sides agree.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


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

    @property
    def latest(self) -> str:
        return sorted(self.versions, key=lambda v: tuple(int(p) for p in v.split(".")))[-1]


class FakeHub:
    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.key_id = hashlib.sha256(raw_public(self.key.public_key())).hexdigest()[:16]
        self.installations: dict[str, Ed25519PublicKey] = {}
        self.nonces: set[str] = set()
        self.listings: dict[str, Listing] = {}
        self.licences: dict[str, dict[str, Any]] = {}  # plugin id -> {state, expires_at, grace_until}
        self.purchases: dict[str, dict[str, Any]] = {}
        self.link_codes = {"LINKCODE42": "Ada's company"}
        self.linked: str | None = None
        self.charge_saved_card = True
        self.heartbeat_refusal: str | None = None  # e.g. "integrity_violation"
        self.tamper_downloads = False
        self.redirect_downloads = False
        self.now: float | None = None  # the hub's clock for leases (None: real time)
        self.heartbeats: list[dict[str, Any]] = []
        self.requests: list[str] = []
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
        self.listings[listing.id] = listing
        return listing

    # ------------------------------------------------------------ the hub's rules

    def _clock(self) -> float:
        return self.now if self.now is not None else time.time()

    def lease(self, installation_id: str, plugins: list[dict[str, str]]) -> str:
        now = int(self._clock())
        standing: dict[str, str] = {}
        licences: dict[str, Any] = {}
        for item in plugins:
            listing = self.listings.get(item["id"])
            if listing is None:
                standing[item["id"]] = "unknown"
            elif listing.tier == "free":
                standing[item["id"]] = "free"
            else:
                licence = self.licences.get(item["id"])
                if licence is None:
                    standing[item["id"]] = "unlicensed"
                else:
                    licences[item["id"]] = licence
                    end = licence.get("grace_until") or licence.get("expires_at") or now + 1
                    in_grace = licence.get("expires_at") and licence["expires_at"] <= now < end
                    standing[item["id"]] = "grace" if in_grace else "licensed" if now < end else "expired"
        header = {"alg": "EdDSA", "kid": self.key_id, "typ": "sudoo-lease+jwt"}
        claims = {
            "iss": self.url,
            "aud": f"sudoo-panel:{installation_id}",
            "sub": installation_id,
            "iat": now,
            "nbf": now,
            "exp": now + 7 * 86400,
            "jti": uuid.uuid4().hex,
            "status": "active",
            "plugins": standing,
            "licenses": licences,
            "next_heartbeat": now + 6 * 3600,
        }
        signing_input = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(claims).encode())}"
        return f"{signing_input}.{b64url(self.key.sign(signing_input.encode()))}"

    def sign_package(self, plugin_id: str, version: str, package: bytes) -> tuple[str, str]:
        sha = hashlib.sha256(package).hexdigest()
        message = b"\n".join((b"sudoo-plugin-v1", plugin_id.encode(), version.encode(), sha.encode()))
        return sha, b64url(self.key.sign(message))

    def complete_checkout(self, purchase_id: str) -> None:
        purchase = self.purchases[purchase_id]
        purchase["status"] = "paid"
        self._grant(purchase["plugin_id"])

    def _grant(self, plugin_id: str) -> None:
        now = int(self._clock())
        self.licences[plugin_id] = {"state": "active", "expires_at": now + 30 * 86400, "grace_until": now + 37 * 86400}

    # ------------------------------------------------------------ HTTP

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, data: Any = None, headers: dict[str, str] | None = None, raw: bytes | None = None) -> None:  # fmt: skip
                body = raw if raw is not None else json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/zip" if raw is not None else "application/json")
                self.send_header("Content-Length", str(len(body)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def _problem(self, status: int, detail: str, code: str | None = None) -> None:
                self._send(status, {"type": "about:blank", "status": status, "detail": detail, "code": code})

            def _body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def _signed(self, body: bytes, key: Ed25519PublicKey | None = None) -> str | None:
                """The installation id when the request is signed as the hub requires; None after a 401."""
                installation = self.headers.get("X-Sudoo-Installation", "")
                timestamp = self.headers.get("X-Sudoo-Timestamp", "")
                nonce = self.headers.get("X-Sudoo-Nonce", "")
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
                hub.nonces.add((installation, nonce))  # type: ignore[arg-type]
                return installation

            def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
                hub.requests.append(f"GET {self.path}")
                path = urlsplit(self.path).path
                parts = path.strip("/").split("/")
                if path == "/api/v1/keys":
                    key = b64url(raw_public(hub.key.public_key()))
                    return self._send(
                        200, {"data": [{"algorithm": "Ed25519", "key_id": hub.key_id, "public_key": key}]}
                    )
                if path == "/api/v1/catalog":
                    query = parse_qs(urlsplit(self.path).query)
                    items = [self._plugin_out(listing) for listing in hub.listings.values()]
                    if "q" in query:
                        items = [i for i in items if query["q"][0].lower() in i["name"].lower()]
                    meta = {"total": len(items), "count": len(items), "limit": 48, "offset": 0}
                    return self._send(200, {"data": items, "meta": meta})
                if path == "/api/v1/categories":
                    return self._send(200, {"data": [{"type": "mailer", "count": len(hub.listings)}]})
                if parts[:3] == ["api", "v1", "catalog"] and len(parts) == 4:
                    listing = hub.listings.get(parts[3])
                    if listing is None:
                        return self._problem(404, "No such plugin.")
                    detail = {**self._plugin_out(listing), "plans": listing.plans, "permissions": [], "requires": [],
                              "latest_version": {"version": listing.latest, "min_sudoo_version": "0.1.0", "changelog": None}}  # fmt: skip
                    return self._send(200, {"data": detail})
                if parts[:3] == ["api", "v1", "catalog"] and len(parts) == 6 and parts[4] == "download":
                    return self._download(parts[3], parts[5])
                if parts[:2] == ["objects", "pkg"]:
                    listing = hub.listings[parts[2]]
                    return self._send(200, raw=self._bytes(listing, parts[3]))
                if path == "/api/v1/purchases":
                    if self._signed(b"") is None:
                        return None
                    return self._send(200, {"data": list(hub.purchases.values())})
                if parts[:3] == ["api", "v1", "purchases"] and len(parts) == 4:
                    if self._signed(b"") is None:
                        return None
                    found = hub.purchases.get(parts[3])
                    return self._send(200, {"data": found}) if found else self._problem(404, "No such purchase.")
                return self._problem(404, "Not found.")

            def _plugin_out(self, listing: Listing) -> dict[str, Any]:
                price = listing.plans[0]["price_cents"] if listing.plans else None
                return {"id": listing.id, "name": listing.name, "description": "A plugin for tests.",
                        "type": listing.type, "tier": listing.tier, "tags": [], "price_cents": price,
                        "currency": "usd", "publisher": listing.publisher, "avg_rating": 4.5, "rating_count": 2,
                        "install_count": 3}  # fmt: skip

            def _bytes(self, listing: Listing, version: str) -> bytes:
                package = listing.versions[version]
                return package + b"tampered" if hub.tamper_downloads else package

            def _download(self, plugin_id: str, version: str) -> None:
                if self._signed(b"") is None:
                    return
                listing = hub.listings.get(plugin_id)
                if listing is None or version not in listing.versions:
                    return self._problem(404, "No such version.")
                if listing.tier == "paid":
                    if hub.linked is None:
                        return self._problem(403, "Link first.", "link_required")
                    if plugin_id not in hub.licences:
                        return self._problem(403, "No licence.", "license_required")
                sha, signature = hub.sign_package(plugin_id, version, listing.versions[version])
                headers = {"X-Sudoo-Plugin-Id": plugin_id, "X-Sudoo-Plugin-Version": version,
                           "X-Sudoo-Package-SHA256": sha, "X-Sudoo-Signature": signature,
                           "X-Sudoo-Signing-Key": hub.key_id}  # fmt: skip
                if hub.redirect_downloads:
                    return self._send(307, {}, {**headers, "Location": f"{hub.url}/objects/pkg/{plugin_id}/{version}"})
                return self._send(200, raw=self._bytes(listing, version), headers=headers)

            def do_POST(self) -> None:  # noqa: N802
                hub.requests.append(f"POST {self.path}")
                body = self._body()
                data = json.loads(body or b"{}")
                if self.path == "/api/v1/register":
                    key = Ed25519PublicKey.from_public_bytes(unb64url(data["public_key"]))
                    if self._signed(body, key) is None:
                        return None
                    hub.installations[data["installation_id"]] = key
                    out = {"installation_id": data["installation_id"], "status": "active",
                           "hub_key": {"key_id": hub.key_id}, "heartbeat_interval_seconds": 21600}  # fmt: skip
                    return self._send(201, {"data": out})
                installation = self._signed(body)
                if installation is None:
                    return None
                if self.path == "/api/v1/heartbeat":
                    hub.heartbeats.append(data)
                    if hub.heartbeat_refusal:
                        return self._problem(403, "Refused.", hub.heartbeat_refusal)
                    lease = hub.lease(installation, data.get("plugins", []))
                    return self._send(200, {"data": {"lease": lease, "integrity": "ok", "status": "active"}})
                if self.path == "/api/v1/link":
                    account = hub.link_codes.pop(data.get("code", ""), None)
                    if account is None:
                        return self._problem(404, "This code is not valid or has expired.", "bad_link_code")
                    hub.linked = account
                    return self._send(200, {"data": {"installation_id": installation, "account_name": account,
                                                     "linked_at": "2026-11-14T10:00:00Z"}})  # fmt: skip
                if self.path == "/api/v1/purchases":
                    if hub.linked is None:
                        return self._problem(403, "Link this panel first.", "link_required")
                    purchase_id = uuid.uuid4().hex
                    purchase = {"id": purchase_id, "plugin_id": data["plugin_id"], "plugin_name": "Paid plugin",
                                "plan_kind": "monthly", "amount_cents": 500, "currency": "usd", "kind": "purchase",
                                "created_at": "2026-11-14T10:00:00Z",
                                "receipt_url": "https://pay.stripe.com/receipts/x", "status": "pending"}  # fmt: skip
                    if hub.charge_saved_card:
                        purchase["status"] = "paid"
                        hub._grant(data["plugin_id"])
                    else:
                        purchase["checkout_url"] = f"https://checkout.stripe.com/c/pay/{purchase_id}"
                    hub.purchases[purchase_id] = purchase
                    return self._send(201, {"data": purchase})
                return self._problem(404, "Not found.")

        return Handler
