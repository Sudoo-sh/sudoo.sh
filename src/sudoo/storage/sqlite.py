"""Storage plugin "sqlite": one SQLite file, <ODOO_ROOT>/manager/sudoo.db (mode 600).

SQLite is part of Python, needs no server and no memory of its own, and gives transactions: two requests changing
the same account cannot overwrite each other's change. Write-ahead logging lets readers continue while a write
happens. Each thread gets its own connection.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from sudoo.config import Settings

from .base import Document

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    collection TEXT NOT NULL,
    key        TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (collection, key)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY,
    ts       INTEGER NOT NULL,
    user     TEXT NOT NULL,
    ip       TEXT,
    event    TEXT NOT NULL,
    instance TEXT,
    ok       INTEGER NOT NULL,
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS audit_user ON audit (user, id);
CREATE INDEX IF NOT EXISTS audit_instance ON audit (instance, id);
"""
_AUDIT_KEEP = 200_000  # entries; older ones are removed now and then


class SqliteStorage:
    def __init__(self, settings: Settings) -> None:
        settings.manager_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path: Path = settings.manager_dir / "sudoo.db"
        self._local = threading.local()
        if not self.path.exists():
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600))
        with self._connection() as db:
            db.executescript(_SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db: sqlite3.Connection | None = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=True)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA busy_timeout=10000")
            self._local.db = db
        return db

    @contextmanager
    def _connection(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = self._db()
        if not write:
            yield db
            return
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")

    # ------------------------------------------------------------ documents

    def get(self, collection: str, key: str) -> Document | None:
        row = (
            self._db()
            .execute("SELECT data FROM documents WHERE collection = ? AND key = ?", (collection, key))
            .fetchone()
        )
        return json.loads(row[0]) if row else None

    def all(self, collection: str) -> dict[str, Document]:
        rows = self._db().execute("SELECT key, data FROM documents WHERE collection = ?", (collection,))
        return {key: json.loads(data) for key, data in rows}

    def put(self, collection: str, key: str, document: Document) -> None:
        with self._connection(write=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO documents (collection, key, data) VALUES (?, ?, ?)",
                (collection, key, json.dumps(document)),
            )

    def delete(self, collection: str, key: str) -> bool:
        with self._connection(write=True) as db:
            return db.execute("DELETE FROM documents WHERE collection = ? AND key = ?", (collection, key)).rowcount > 0

    def change(self, collection: str, key: str, fn: Callable[[Document | None], Document | None]) -> Document | None:
        with self._connection(write=True) as db:
            row = db.execute(
                "SELECT data FROM documents WHERE collection = ? AND key = ?", (collection, key)
            ).fetchone()
            result = fn(json.loads(row[0]) if row else None)
            if result is None:
                db.execute("DELETE FROM documents WHERE collection = ? AND key = ?", (collection, key))
            else:
                db.execute(
                    "INSERT OR REPLACE INTO documents (collection, key, data) VALUES (?, ?, ?)",
                    (collection, key, json.dumps(result)),
                )
            return result

    # ------------------------------------------------------------ audit log

    def log(self, entry: Document) -> None:
        with self._connection(write=True) as db:
            cursor = db.execute(
                "INSERT INTO audit (ts, user, ip, event, instance, ok, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["ts"],
                    entry["user"],
                    entry.get("ip"),
                    entry["event"],
                    entry.get("instance"),
                    int(entry.get("ok", True)),
                    entry.get("detail"),
                ),
            )
            if cursor.lastrowid and cursor.lastrowid % 1000 == 0:
                db.execute("DELETE FROM audit WHERE id <= ?", (cursor.lastrowid - _AUDIT_KEEP,))

    def logs(
        self, limit: int = 200, offset: int = 0, user: str | None = None, instance: str | None = None
    ) -> list[Document]:
        where, values = [], []
        if user is not None:
            where.append("user = ?")
            values.append(user)
        if instance is not None:
            where.append("instance = ?")
            values.append(instance)
        rows = self._db().execute(
            "SELECT ts, user, ip, event, instance, ok, detail FROM audit"
            + (f" WHERE {' AND '.join(where)}" if where else "")
            + " ORDER BY id DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        )
        entries = []
        for ts, who, ip, event, inst, ok, detail in rows:
            entry: Document = {"ts": ts, "user": who, "ip": ip, "event": event, "ok": bool(ok)}
            if inst:
                entry["instance"] = inst
            if detail:
                entry["detail"] = detail
            entries.append(entry)
        return entries
