"""Moving the data of earlier versions, kept in JSON files, into the storage plugin (once).

Each file is read, its content stored, and the file renamed to <name>.migrated, so nothing is lost and a second
start does not import it again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .base import Storage

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
