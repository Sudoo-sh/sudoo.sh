"""The trust between the main panel and the agents of its other servers: keys, fingerprints, join codes, the signed
connection request and the signed frames of the channel.

Keys. The panel has one Ed25519 key pair for its servers (`panel_key()`, created on first use, kept encrypted with
the vault key); each agent creates its own on the server it runs on when it joins. Each side pins the other's public
key: the panel learns the agent's key when the agent joins, and the agent learns the panel's key from the join
answer, which it accepts only when its SHA-256 matches the one written in the join code (`JoinCode.panel_key_sha256`).
A fingerprint is the first 16 hex digits of the SHA-256 of a raw public key, in groups of four (`ABCD-EF01-2345-6789`).

Join codes. `JoinCode.encode()` is what the admin copies to the new server: "sudoo-join-v1." followed by base64url
JSON with the panel's address, the server's id, a 32-byte secret (the panel keeps only its SHA-256) and the SHA-256
of the panel's public key. A code works once and expires (SERVERS_JOIN_CODE_MINUTES).

Connecting. The agent opens the channel with four headers: X-Sudoo-Server, X-Sudoo-Timestamp, X-Sudoo-Nonce and
X-Sudoo-Signature, its signature of "sudoo-agent-connect-v1\\n<server>\\n<timestamp>\\n<nonce>" (`connect_message`).
The panel answers with a welcome signed by its own key over the agent's nonce and a nonce of its own
(`welcome_message`): both sides then know they talk to the pinned key, and the two nonces name the session.

Frames. Every message after the welcome is a `Seal`ed frame: {"n": sequence, "p": JSON text, "g": signature} where
the signature covers "sudoo-channel-v1\\n<session>\\n<sender>\\n<n>\\n<p>". The receiver accepts only the next
sequence number of that session, signed by the other side's pinned key: a changed, unsigned, replayed, reordered or
reflected frame is refused (ChannelError) and the connection is closed. TLS (the panel's https address) protects the
traffic as well; the signatures hold even where TLS ends before the panel (a tunnel, a proxy).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from sudoo.hub.crypto import b64url, from_b64url, private_key_bytes, public_key_b64url, public_key_from_b64url, verify

JOIN_PREFIX = "sudoo-join-v1."
CONNECT_DOMAIN = "sudoo-agent-connect-v1"
WELCOME_DOMAIN = "sudoo-agent-welcome-v1"
JOIN_DOMAIN = "sudoo-agent-join-v1"
FRAME_DOMAIN = "sudoo-channel-v1"
CLOCK_SKEW_SECONDS = 120  # a connection request whose time is further from the panel's clock is refused
SERVER_ID_RE = re.compile(r"^srv-[a-z0-9]{10}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
FINGERPRINT_RE = re.compile(r"^[0-9A-F]{4}(-[0-9A-F]{4}){3}$")
PANEL = "panel"
AGENT = "agent"


class ChannelError(Exception):
    """A frame or a connection request that must not be accepted; the connection is closed."""


# ---------------------------------------------------------------- keys


def new_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def key_from_bytes(raw: bytes) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(raw)


def key_bytes(key: Ed25519PrivateKey) -> bytes:
    return private_key_bytes(key)


def public_text(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    """The public key as base64url of its 32 raw bytes."""
    public = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return public_key_b64url(public)


def public_from_text(text: str) -> Ed25519PublicKey:
    """Raises ValueError when `text` is not a base64url Ed25519 public key."""
    return public_key_from_b64url(text)


def key_sha256(public: str) -> str:
    """The SHA-256 (hex) of a public key's raw bytes (given as base64url)."""
    return hashlib.sha256(from_b64url(public)).hexdigest()


def fingerprint(public: str) -> str:
    """ABCD-EF01-2345-6789: what people compare on both sides before a server is approved."""
    digest = key_sha256(public)[:16].upper()
    return "-".join(digest[i : i + 4] for i in range(0, 16, 4))


def normalize_fingerprint(text: str) -> str:
    """What someone typed, as a fingerprint (spaces and case ignored, dashes optional); "" when it cannot be one."""
    letters = re.sub(r"[\s-]", "", text or "").upper()
    if not re.fullmatch(r"[0-9A-F]{16}", letters):
        return ""
    return "-".join(letters[i : i + 4] for i in range(0, 16, 4))


def load_or_create_key(path: Path, encrypt: Any = None, decrypt: Any = None) -> Ed25519PrivateKey:
    """The key kept in `path` (created, mode 600, when missing). `encrypt`/`decrypt` wrap the stored text (the panel
    keeps its key encrypted with the vault; an agent keeps its key readable by root only)."""
    from sudoo.core import vault

    if path.exists():
        text = path.read_text().strip()
        raw = from_b64url(decrypt(text) if decrypt else text)
        return key_from_bytes(raw)
    key = new_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = b64url(key_bytes(key))
    vault.write_private(path, encrypt(text) if encrypt else text)
    return key


def sign(key: Ed25519PrivateKey, message: bytes) -> str:
    return b64url(key.sign(message))


def check(public: Ed25519PublicKey, signature: str, message: bytes) -> bool:
    try:
        raw = from_b64url(signature)
    except ValueError:
        return False
    return verify(public, raw, message)


# ---------------------------------------------------------------- join codes


@dataclass(frozen=True)
class JoinCode:
    panel_url: str
    server_id: str
    secret: str
    panel_key_sha256: str

    def encode(self) -> str:
        body = {"v": 1, "u": self.panel_url, "i": self.server_id, "s": self.secret, "k": self.panel_key_sha256}
        return JOIN_PREFIX + b64url(json.dumps(body, separators=(",", ":")).encode())

    @staticmethod
    def decode(text: str) -> JoinCode:
        """Raises ValueError (with a message for people) when `text` is not a join code."""
        text = (text or "").strip()
        if not text.startswith(JOIN_PREFIX):
            raise ValueError("This is not a Sudoo.sh join code: it starts with sudoo-join-v1.")
        try:
            body = json.loads(from_b64url(text[len(JOIN_PREFIX) :]))
        except ValueError as error:
            raise ValueError("The join code is damaged: copy it again from the panel.") from error
        if not isinstance(body, dict) or body.get("v") != 1:
            raise ValueError("The join code is from another version of Sudoo.sh: create a new one in the panel.")
        url, server, secret, key = (body.get(k) for k in ("u", "i", "s", "k"))
        valid = (
            isinstance(url, str)
            and re.fullmatch(r"https?://[^\s/?#]+(/[^\s?#]*)?", url) is not None
            and isinstance(server, str)
            and SERVER_ID_RE.match(server) is not None
            and isinstance(secret, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{43}", secret) is not None
            and isinstance(key, str)
            and re.fullmatch(r"[0-9a-f]{64}", key) is not None
        )
        if not valid:
            raise ValueError("The join code is damaged: copy it again from the panel.")
        return JoinCode(str(url).rstrip("/"), str(server), str(secret), str(key))


def new_secret() -> str:
    return b64url(secrets.token_bytes(32))


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def join_message(server_id: str, secret: str, public: str) -> bytes:
    """What a joining agent signs with its new key: proof that it holds the key it gives."""
    return f"{JOIN_DOMAIN}\n{server_id}\n{secret_hash(secret)}\n{public}".encode()


# ---------------------------------------------------------------- connecting


def new_nonce() -> str:
    return b64url(secrets.token_bytes(18))


def connect_message(server_id: str, timestamp: int, nonce: str) -> bytes:
    return f"{CONNECT_DOMAIN}\n{server_id}\n{timestamp}\n{nonce}".encode()


def connect_headers(key: Ed25519PrivateKey, server_id: str, now: float | None = None) -> tuple[dict[str, str], str]:
    """The headers an agent opens the channel with, and the nonce it must find in the panel's welcome."""
    timestamp = int(time.time() if now is None else now)
    nonce = new_nonce()
    headers = {
        "X-Sudoo-Server": server_id,
        "X-Sudoo-Timestamp": str(timestamp),
        "X-Sudoo-Nonce": nonce,
        "X-Sudoo-Signature": sign(key, connect_message(server_id, timestamp, nonce)),
    }
    return headers, nonce


class NonceCache:
    """Nonces of connection requests seen in the last CLOCK_SKEW_SECONDS * 2: a request is accepted once."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def fresh(self, nonce: str, now: float) -> bool:
        for old in [n for n, at in self._seen.items() if now - at > CLOCK_SKEW_SECONDS * 2]:
            del self._seen[old]
        if nonce in self._seen:
            return False
        self._seen[nonce] = now
        return True


def check_connect(
    headers: Any, public: Ed25519PublicKey, nonces: NonceCache, now: float | None = None
) -> tuple[str, str]:
    """(server id, nonce) of a valid connection request; raises ChannelError otherwise."""
    now = time.time() if now is None else now
    server = headers.get("X-Sudoo-Server", "")
    stamp = headers.get("X-Sudoo-Timestamp", "")
    nonce = headers.get("X-Sudoo-Nonce", "")
    signature = headers.get("X-Sudoo-Signature", "")
    if not SERVER_ID_RE.match(server) or not stamp.isdigit() or not NONCE_RE.match(nonce):
        raise ChannelError("the connection request is incomplete")
    if abs(now - int(stamp)) > CLOCK_SKEW_SECONDS:
        raise ChannelError("the server's clock is wrong (more than two minutes away from the panel's)")
    if not check(public, signature, connect_message(server, int(stamp), nonce)):
        raise ChannelError("the connection request is not signed with the server's key")
    if not nonces.fresh(nonce, now):
        raise ChannelError("the connection request was sent before (replayed)")
    return server, nonce


def welcome_message(server_id: str, agent_nonce: str, panel_nonce: str, payload: str) -> bytes:
    return f"{WELCOME_DOMAIN}\n{server_id}\n{agent_nonce}\n{panel_nonce}\n{payload}".encode()


def welcome(key: Ed25519PrivateKey, server_id: str, agent_nonce: str, body: dict[str, Any]) -> tuple[str, str]:
    """The panel's first message (text), and the session it opens."""
    panel_nonce = new_nonce()
    payload = json.dumps(body, separators=(",", ":"))
    signature = sign(key, welcome_message(server_id, agent_nonce, panel_nonce, payload))
    text = json.dumps({"welcome": payload, "nonce": panel_nonce, "g": signature}, separators=(",", ":"))
    return text, f"{agent_nonce}.{panel_nonce}"


def check_welcome(text: str, public: Ed25519PublicKey, server_id: str, agent_nonce: str) -> tuple[dict[str, Any], str]:
    """(welcome body, session) of the panel's first message; raises ChannelError when it is not signed by the pinned
    panel key for this connection."""
    try:
        envelope = json.loads(text)
        payload, panel_nonce, signature = envelope["welcome"], envelope["nonce"], envelope["g"]
    except (ValueError, KeyError, TypeError) as error:
        raise ChannelError("the panel's welcome is not readable") from error
    if not (isinstance(payload, str) and isinstance(panel_nonce, str) and NONCE_RE.match(panel_nonce)):
        raise ChannelError("the panel's welcome is not readable")
    if not check(public, str(signature), welcome_message(server_id, agent_nonce, panel_nonce, payload)):
        raise ChannelError("the panel's welcome is not signed with the panel's pinned key")
    body = json.loads(payload)
    if not isinstance(body, dict):
        raise ChannelError("the panel's welcome is not readable")
    return body, f"{agent_nonce}.{panel_nonce}"


# ---------------------------------------------------------------- frames


class Seal:
    """Signs the frames one side sends and checks those it receives, in one session."""

    def __init__(self, own: Ed25519PrivateKey, peer: Ed25519PublicKey, session: str, side: str) -> None:
        if side not in (PANEL, AGENT):
            raise ValueError("side is panel or agent")
        self.own = own
        self.peer = peer
        self.session = session
        self.side = side
        self.other = AGENT if side == PANEL else PANEL
        self.sent = 0
        self.received = 0

    @staticmethod
    def _message(session: str, sender: str, number: int, payload: str) -> bytes:
        return f"{FRAME_DOMAIN}\n{session}\n{sender}\n{number}\n{payload}".encode()

    def seal(self, body: dict[str, Any]) -> str:
        self.sent += 1
        payload = json.dumps(body, separators=(",", ":"), default=str)
        signature = sign(self.own, self._message(self.session, self.side, self.sent, payload))
        return json.dumps({"n": self.sent, "p": payload, "g": signature}, separators=(",", ":"))

    def open(self, text: str) -> dict[str, Any]:
        """The body of the next frame; raises ChannelError for anything else."""
        try:
            frame = json.loads(text)
            number, payload, signature = frame["n"], frame["p"], frame["g"]
        except (ValueError, KeyError, TypeError) as error:
            raise ChannelError("a frame is not readable") from error
        if not isinstance(number, int) or not isinstance(payload, str) or not isinstance(signature, str):
            raise ChannelError("a frame is not readable")
        if number != self.received + 1:
            raise ChannelError("a frame arrived out of order or twice")
        if not check(self.peer, signature, self._message(self.session, self.other, number, payload)):
            raise ChannelError("a frame is not signed with the pinned key")
        self.received = number
        body = json.loads(payload)
        if not isinstance(body, dict):
            raise ChannelError("a frame is not readable")
        return body
