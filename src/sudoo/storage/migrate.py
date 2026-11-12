"""Moving the data of earlier versions, kept in JSON files, into the storage plugin (once).

Each file is read, its content stored, and the file renamed to <name>.migrated, so nothing is lost and a second
start does not import it again.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import Storage

_COLLECTIONS = {"users.json": "users", "api_tokens.json": "api_tokens", "state.json": "instance_state"}


SCHEMA = 2  # 2: the five roles of roles.py


def migrate(storage: Storage, folder: Path) -> None:
    """Bring the stored data up to date: JSON files of earlier versions, then the data's own schema."""
    migrate_json_files(storage, folder)
    meta = storage.get("meta", "schema") or {"version": 1}
    if meta["version"] < 2:
        renamed = {"admin": "super_admin", "operator": "developer"}
        for name, user in storage.all("users").items():
            if user.get("role") in renamed:
                storage.put("users", name, {**user, "role": renamed[user["role"]]})
    if meta["version"] != SCHEMA:
        storage.put("meta", "schema", {"version": SCHEMA})


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
