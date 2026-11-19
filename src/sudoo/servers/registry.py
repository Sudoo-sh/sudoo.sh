"""The panel's list of servers (storage collection "servers") and the steps that bring one in or take it out.

    1. An admin adds a server (name, region, room): the panel keeps a record with status "waiting" and shows a join
       code once (trust.JoinCode: the panel's address, the server's id, a secret and the panel key's SHA-256). The
       code works once, for SERVERS_JOIN_CODE_MINUTES; a new code can be made for a server still waiting.
    2. On the new server, `sudoo-install agent --join <code>` makes the agent's key pair and calls POST
       /api/v1/servers/join with the code, its public key and a signature made with that key: the record becomes
       "joined" (the code is used up) and both sides show the agent key's fingerprint.
    3. An admin approves the server by typing the fingerprint the server printed (two-sided approval: the admin made
       the code, and confirms the key that used it). Only then may the agent's channel open: "active".
    4. Revoking ("revoked") closes the channel at once and refuses the key from then on; the server's instances keep
       running there, out of the panel's reach, until the server leaves (`sudoo-install agent leave`). A revoked or
       waiting server can be removed from the list once the panel knows no instance on it.

How many servers there may be comes from the plan (entitlements.py: max_servers, the main server included); adding
or joining one more is refused with where to get more.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from typing import Any

from sudoo import entitlements, servers
from sudoo.config import settings
from sudoo.core import vault
from sudoo.storage import storage

from . import trust

COLLECTION = "servers"
WAITING, JOINED, ACTIVE, REVOKED = "waiting", "joined", "active", "revoked"
STATUSES = (WAITING, JOINED, ACTIVE, REVOKED)
COUNTED = (JOINED, ACTIVE)
"""Servers that count toward the plan's limit (besides the main server)."""
_LOCK = threading.Lock()
_KEY_LOCK = threading.Lock()
_KEY: list[Any] = []


class Refused(Exception):
    """A step that cannot be done: (HTTP status, message for people)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------- the panel's key


def _key_file() -> Any:
    return settings.keys_dir / "servers.key"


def panel_key() -> Any:
    """The panel's Ed25519 key for its servers (created on first use, kept encrypted with the vault key)."""
    if not _KEY:
        with _KEY_LOCK:
            if not _KEY:
                settings.keys_dir.mkdir(parents=True, exist_ok=True)
                _KEY.append(
                    trust.load_or_create_key(
                        _key_file(),
                        encrypt=lambda text: vault.encrypt(text, "servers:panel-key"),
                        decrypt=lambda text: vault.decrypt(text, "servers:panel-key"),
                    )
                )
    return _KEY[0]


def panel_public() -> str:
    return trust.public_text(panel_key())


def panel_url() -> str:
    """The address agents reach the panel at: SERVERS_PANEL_URL, else the panel's public address ("" when neither
    is known yet)."""
    if settings.servers_panel_url:
        return str(settings.servers_panel_url).rstrip("/")
    from sudoo import exposure

    return (exposure.address(exposure.PANEL) or "").rstrip("/")


# ---------------------------------------------------------------- reading


def all_servers() -> list[dict[str, Any]]:
    """Every server record but the main server's, oldest first."""
    found = list(storage().all(COLLECTION).values())
    return sorted(found, key=lambda s: (s.get("created_at") or 0, s["id"]))


def get(server_id: str) -> dict[str, Any] | None:
    if server_id == servers.MAIN or not trust.SERVER_ID_RE.match(server_id or ""):
        return None
    return storage().get(COLLECTION, server_id)


def main_record() -> dict[str, Any]:
    """The main server, as the other records look (it is not stored: its name and region are settings)."""
    return {
        "id": servers.MAIN,
        "name": settings.server_name,
        "region": settings.server_region,
        "status": ACTIVE,
        "main": True,
        "capacity": {},
    }


def counted() -> int:
    """Servers that count toward the plan: the main server and every joined or active one."""
    return 1 + sum(1 for s in all_servers() if s.get("status") in COUNTED)


def active_ids() -> list[str]:
    return [s["id"] for s in all_servers() if s.get("status") == ACTIVE]


def name_of(server_id: str) -> str:
    if server_id == servers.MAIN:
        return str(settings.server_name)
    found = get(server_id)
    return str(found["name"]) if found else server_id


# ---------------------------------------------------------------- limits


def limit_refusal(adding: int = 1) -> str | None:
    """Why `adding` more servers would go beyond the plan (main server included), or None."""
    found = entitlements.current()
    limit = found.limit("servers")
    have = counted()
    if limit is None or have + adding <= limit:
        return None
    plan = found.plan.capitalize()
    linked = "" if found.linked else " This panel is not linked to a Sudoo.sh account, so it has the Free plan."
    count = f"{limit} {'server' if limit == 1 else 'servers'}"
    return entitlements.upsell(
        f"The {plan} plan allows {count}, and this panel has {have} (the main server included).{linked} "
        "Remove a server, or get more servers: several servers are part of Pro and Business."
    )


# ---------------------------------------------------------------- adding


def _new_id() -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    while True:
        server_id = "srv-" + "".join(secrets.choice(alphabet) for _ in range(10))
        if storage().get(COLLECTION, server_id) is None:
            return server_id


def _code(record: dict[str, Any], now: float) -> tuple[dict[str, Any], str]:
    url = panel_url()
    if not url:
        raise Refused(
            409,
            "The panel does not know its own public address yet, so a new server could not reach it. Set Address "
            "for other servers (SERVERS_PANEL_URL) in Settings, Servers, or wait until the panel's address is known.",
        )
    secret = trust.new_secret()
    code = trust.JoinCode(url, record["id"], secret, trust.key_sha256(panel_public()))
    record["code_hash"] = trust.secret_hash(secret)
    record["code_expires_at"] = now + settings.servers_join_code_minutes * 60
    return record, code.encode()


def check_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= 40:
        raise Refused(400, "Give the server a name of 1 to 40 characters.")
    taken = {s["name"].lower() for s in all_servers() if s.get("status") != REVOKED} | {settings.server_name.lower()}
    if name.lower() in taken:
        raise Refused(409, f"A server called {name} exists already. Choose another name.")
    return name


def check_region(region: str) -> str:
    region = " ".join((region or "").split())
    if len(region) > 40:
        raise Refused(400, "The region is at most 40 characters (for example Frankfurt or me-central).")
    return region


def check_capacity(raw: Any) -> dict[str, Any]:
    """{"max_instances": int or None}: how many instances the server takes at most (None: no limit)."""
    if raw is None:
        return {"max_instances": None}
    if not isinstance(raw, dict) or set(raw) - {"max_instances"}:
        raise Refused(400, 'The room of a server is {"max_instances": a number from 1 to 1000, or null}.')
    value = raw.get("max_instances")
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 1000):
        raise Refused(400, "Instances at most is a number from 1 to 1000, or empty for no limit.")
    return {"max_instances": value}


def create(name: str, region: str, capacity: Any, by: str, now: float | None = None) -> tuple[dict[str, Any], str]:
    """A new server waiting for its agent, and its join code (shown once). Raises Refused."""
    now = time.time() if now is None else now
    refused = limit_refusal()
    if refused:
        raise Refused(403, refused)
    with _LOCK:
        record: dict[str, Any] = {
            "id": _new_id(),
            "name": check_name(name),
            "region": check_region(region),
            "capacity": check_capacity(capacity),
            "status": WAITING,
            "created_at": now,
            "created_by": by,
        }
        record, code = _code(record, now)
        storage().put(COLLECTION, record["id"], record)
    return record, code


def new_code(server_id: str, by: str, now: float | None = None) -> tuple[dict[str, Any], str]:
    """A new join code for a server still waiting for its agent (the earlier code stops working)."""
    now = time.time() if now is None else now
    with _LOCK:
        record = get(server_id)
        if record is None:
            raise Refused(404, "This server does not exist.")
        if record["status"] != WAITING:
            raise Refused(409, "This server has joined already; a join code is only for a server that has not.")
        record, code = _code(record, now)
        record["code_by"] = by
        storage().put(COLLECTION, server_id, record)
    return record, code


def join(
    server_id: str, secret: str, public: str, signature: str, facts: dict[str, Any], now: float | None = None
) -> dict[str, Any]:
    """An agent joins with its join code (`server_id`, `secret`) and its new public key, signed with that key. The
    code is used up; the server waits for an admin's approval. Raises Refused (the same 403 for an unknown server, a
    wrong or used code, so a code cannot be guessed piece by piece)."""
    now = time.time() if now is None else now
    wrong = Refused(403, "This join code is not valid: it is wrong, used already or expired. Create a new one.")
    try:
        key = trust.public_from_text(public)
    except ValueError:
        raise Refused(400, "The server's public key is not an Ed25519 key.") from None
    if not trust.check(key, signature, trust.join_message(server_id, secret, public)):
        raise Refused(400, "The join request is not signed with the key it gives.")
    with _LOCK:
        record = get(server_id)
        if record is None or record.get("status") != WAITING or not record.get("code_hash"):
            raise wrong
        if not hmac.compare_digest(record["code_hash"], trust.secret_hash(secret)):
            raise wrong
        if now > float(record.get("code_expires_at") or 0):
            raise wrong
        refused = limit_refusal()
        if refused:
            raise Refused(403, refused)
        record.update(
            status=JOINED,
            public_key=public,
            fingerprint=trust.fingerprint(public),
            joined_at=now,
            facts=_facts(facts),
            version=str(facts.get("version") or "")[:40],
        )
        record.pop("code_hash", None)
        record.pop("code_expires_at", None)
        storage().put(COLLECTION, server_id, record)
    return record


def _facts(raw: dict[str, Any]) -> dict[str, Any]:
    """What an agent says about its server, kept as plain, bounded values."""
    found: dict[str, Any] = {}
    for key in ("hostname", "os", "architecture", "docker", "version"):
        value = raw.get(key)
        if isinstance(value, str):
            found[key] = value[:120]
    for key in ("cpus", "memory_bytes", "disk_bytes", "disk_free_bytes"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**62:
            found[key] = value
    return found


def approve(server_id: str, typed: str, by: str, now: float | None = None) -> dict[str, Any]:
    """Approve a joined server when `typed` is the fingerprint its agent printed. Raises Refused."""
    now = time.time() if now is None else now
    with _LOCK:
        record = get(server_id)
        if record is None:
            raise Refused(404, "This server does not exist.")
        if record["status"] != JOINED:
            raise Refused(409, "Only a server that has joined and waits for approval can be approved.")
        given = trust.normalize_fingerprint(typed)
        if not given or not hmac.compare_digest(given, record["fingerprint"]):
            raise Refused(
                400,
                "This is not the fingerprint of the key that joined. Type the fingerprint the server printed "
                "(sudoo-install agent status). If they differ, someone else may have used the join code: revoke this "
                "server and add it again.",
            )
        record.update(status=ACTIVE, approved_at=now, approved_by=by)
        storage().put(COLLECTION, server_id, record)
    return record


def revoke(server_id: str, by: str, now: float | None = None) -> dict[str, Any]:
    """Refuse the server's key from now on. Its instances keep running on it."""
    now = time.time() if now is None else now
    with _LOCK:
        record = get(server_id)
        if record is None:
            raise Refused(404, "This server does not exist.")
        if record["status"] == REVOKED:
            return record
        record.update(status=REVOKED, revoked_at=now, revoked_by=by)
        record.pop("code_hash", None)
        record.pop("code_expires_at", None)
        storage().put(COLLECTION, server_id, record)
    return record


def remove(server_id: str, has_instances: bool) -> None:
    """Remove a revoked server, or one that never joined, from the list."""
    with _LOCK:
        record = get(server_id)
        if record is None:
            raise Refused(404, "This server does not exist.")
        if record["status"] not in (REVOKED, WAITING):
            raise Refused(409, "Revoke the server first; then it can be removed from the list.")
        if has_instances:
            raise Refused(
                409,
                "The panel still knows instances on this server. Move or delete them first, or forget them with "
                "the server's removal once the server has left (sudoo-install agent leave).",
            )
        storage().delete(COLLECTION, server_id)


def update(server_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Change a server's name, region or room."""
    with _LOCK:
        record = get(server_id)
        if record is None:
            raise Refused(404, "This server does not exist.")
        if "name" in changes and changes["name"] != record["name"]:
            record["name"] = check_name(str(changes["name"]))
        if "region" in changes:
            record["region"] = check_region(str(changes["region"] or ""))
        if "capacity" in changes:
            record["capacity"] = check_capacity(changes["capacity"])
        storage().put(COLLECTION, server_id, record)
    return record


def seen(server_id: str, facts: dict[str, Any] | None = None, now: float | None = None) -> None:
    """Remember that the server's agent reported (and what it said about its server)."""
    now = time.time() if now is None else now

    def change(found: dict[str, Any] | None) -> dict[str, Any] | None:
        if found is None:
            return None
        found["last_seen"] = now
        if facts:
            found["facts"] = _facts(facts)
            found["version"] = str(facts.get("version") or found.get("version") or "")[:40]
        return found

    storage().change(COLLECTION, server_id, change)


def public_key_of(server_id: str, allowed: tuple[str, ...] = (ACTIVE,)) -> Any:
    """The pinned public key of a server whose status is in `allowed`, else None."""
    found = get(server_id)
    if found is None or found.get("status") not in allowed or not found.get("public_key"):
        return None
    return trust.public_from_text(found["public_key"])


def wipe_code(record: dict[str, Any]) -> dict[str, Any]:
    """A record as the API shows it: never the code's hash nor the public key's text."""
    shown = {k: v for k, v in record.items() if k not in ("code_hash", "public_key")}
    if record.get("status") == WAITING and float(record.get("code_expires_at") or 0) < time.time():
        shown["code_expired"] = True
    return shown


def vault_ready() -> None:
    """Create the panel's key now (at start), so the first join code does not wait for it."""
    panel_key()
