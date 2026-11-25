"""Moving the data of earlier versions, kept in JSON files, into the storage plugin (once).

Each file is read, its content stored, and the file renamed to <name>.migrated, so nothing is lost and a second
start does not import it again.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .base import Storage

log = logging.getLogger(__name__)

_COLLECTIONS = {"users.json": "users", "api_tokens.json": "api_tokens", "state.json": "instance_state"}


SCHEMA = 4
"""2: the five roles of roles.py; 3: no values of secret settings in the audit log; 4: projects made before project
roles keep the earlier access model ("legacy": panel role ∩ grant) until an admin switches them, so nobody gains or
loses access by the upgrade (projects/people.convert)."""


def migrate(storage: Storage, folder: Path) -> None:
    """Bring the stored data up to date: JSON files of earlier versions, then the data's own schema."""
    migrate_json_files(storage, folder)
    meta = storage.get("meta", "schema") or {"version": 1}
    if meta["version"] < 2:
        renamed = {"admin": "super_admin", "operator": "developer"}
        for name, user in storage.all("users").items():
            if user.get("role") in renamed:
                storage.put("users", name, {**user, "role": renamed[user["role"]]})
    if meta["version"] < 3:
        rewrite = getattr(storage, "rewrite_audit_details", None)  # optional: the built-in SQLite storage has it
        if rewrite is not None:
            rewrite("settings changed", hide_secret_settings)
    if meta["version"] < 4:
        for key, project in storage.all("projects").items():
            raw = project.get("access")
            current: dict[str, object] = raw if isinstance(raw, dict) else {}
            if "model" not in current:
                storage.put("projects", key, {**project, "access": {**current, "model": "legacy"}})
    if meta["version"] != SCHEMA:
        storage.put("meta", "schema", {"version": SCHEMA})
    separate_access_requests(storage)  # every start: safe to repeat, see the function


def hide_secret_settings(detail: str) -> str:
    """The detail of a "settings changed" entry with the values of secret settings replaced by "(set)"; earlier
    versions wrote them there as typed. "SMTP_PASSWORD=abc, IDLE_HOURS=2" -> "SMTP_PASSWORD=(set), IDLE_HOURS=2"."""
    from sudoo.config import SECRET

    parts = re.split(r", (?=[A-Z][A-Z0-9_]*=)", detail)
    for index, part in enumerate(parts):
        key, _, value = part.partition("=")
        if key.lower() in SECRET and value != "(env)":
            parts[index] = f"{key}=(set)"
    return ", ".join(parts)


def migrate_json_files(storage: Storage, folder: Path) -> None:
    for file_name, collection in _COLLECTIONS.items():
        path = folder / file_name
        if not path.exists():
            continue
        try:
            documents = json.loads(path.read_text())
        except ValueError:
            continue  # damaged: leave it where it is for a person to look at
        for key, document in documents.items():
            if storage.get(collection, key) is None:
                storage.put(collection, key, document)
        path.rename(path.with_name(file_name + ".migrated"))
    log = folder / "audit.log"
    if log.exists():
        for old in (folder / "audit.log.1", log):
            if old.exists():
                for line in old.read_text().splitlines():
                    if line.startswith("{"):
                        storage.log(json.loads(line))
                old.rename(old.with_name(old.name + ".migrated"))


OLD_REQUESTS = "access_requests"
OLD_REQUESTS_BACKUP = "access_requests_backup"
ROLE_REQUESTS = "role_requests"  # temporary access (projects/elevation.py)
CLIENT_REQUESTS = "client_access_requests"  # the client portal's access requests (projects/clients.py)


def _kind_of_request(document: dict[str, object]) -> str | None:
    """Which feature wrote a document of the old shared collection, by its shape: temporary access has "user" and
    "asked" ("stage", "level" and "decided" too); a client portal request has "by" and "at" ("users", "until",
    "decided_at" and "branch" too). None when it fits neither or both."""
    temporary = "user" in document and "asked" in document
    portal = "by" in document and "at" in document
    if temporary and not portal:
        return ROLE_REQUESTS
    if portal and not temporary:
        return CLIENT_REQUESTS
    return None


def separate_access_requests(storage: Storage) -> dict[str, int]:
    """Temporary access and the client portal used to keep their requests in one collection, "access_requests", and
    each one read, lapsed and deleted the other's documents. Each now has its own collection; this moves what is
    left of the old one to the right place by the shape of each document.

    Safe to run any number of times: a document is copied (never over one already there) before it is removed from
    the old collection, a document that fits neither shape stays where it is, and nothing is changed when the old
    collection is empty. A copy of the old collection as it was is kept in "access_requests_backup" until the next
    start-up replaces or drops it. Returns how many documents were moved, left and backed up."""
    storage.all(OLD_REQUESTS_BACKUP)  # (read first so a broken storage fails here, before anything changes)
    for key in storage.all(OLD_REQUESTS_BACKUP):
        storage.delete(OLD_REQUESTS_BACKUP, key)  # the copy of the previous start-up is no longer needed
    old = storage.all(OLD_REQUESTS)
    result = {"moved": 0, "left": 0, "backed_up": 0}
    if not old:
        return result
    for key, document in old.items():
        storage.put(OLD_REQUESTS_BACKUP, key, document)
    result["backed_up"] = len(old)
    for key, document in old.items():
        target = _kind_of_request(document)
        if target is None:
            result["left"] += 1
            log.warning("Access request %s fits neither temporary access nor the client portal: left in %s.",
                        key, OLD_REQUESTS)  # fmt: skip
            continue
        if storage.get(target, key) is None:
            storage.put(target, key, document)
        storage.delete(OLD_REQUESTS, key)
        result["moved"] += 1
        log.info("Moved access request %s from %s to %s.", key, OLD_REQUESTS, target)
    log.info("Access requests separated: %d moved, %d left, copy kept in %s until the next start-up.",
             result["moved"], result["left"], OLD_REQUESTS_BACKUP)  # fmt: skip
    return result
