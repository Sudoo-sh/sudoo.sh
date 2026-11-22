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
from typing import Any

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
CREATE INDEX IF NOT EXISTS audit_ts ON audit (ts);
"""
_AUDIT_SCOPE = (("project", "TEXT"), ("branch", "TEXT"))  # added in 3.2: the project and branch an entry is about
_AUDIT_SORT = {"ts": "ts", "user": "user", "event": "event", "instance": "instance", "ip": "ip", "ok": "ok"}
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
            known = {row[1] for row in db.execute("PRAGMA table_info(audit)")}
            for column, kind in _AUDIT_SCOPE:
                if column not in known:
                    db.execute(f"ALTER TABLE audit ADD COLUMN {column} {kind}")
            db.execute("CREATE INDEX IF NOT EXISTS audit_project ON audit (project, id)")

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
                "INSERT INTO audit (ts, user, ip, event, instance, ok, detail, project, branch)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["ts"],
                    entry["user"],
                    entry.get("ip"),
                    entry["event"],
                    entry.get("instance"),
                    int(entry.get("ok", True)),
                    entry.get("detail"),
                    entry.get("project"),
                    entry.get("branch"),
                ),
            )
            if cursor.lastrowid and cursor.lastrowid % 1000 == 0:
                db.execute("DELETE FROM audit WHERE id <= ?", (cursor.lastrowid - _AUDIT_KEEP,))

    def rewrite_audit_details(self, event: str, fn: Callable[[str], str]) -> int:
        """Replace the detail of every entry of `event` by fn(detail) (a schema change, migrate.py); returns how
        many entries changed."""
        changed = 0
        self._db().execute("PRAGMA secure_delete = ON")  # the old text is overwritten in the file, not just freed
        with self._connection(write=True) as db:
            rows = db.execute("SELECT id, detail FROM audit WHERE event = ? AND detail IS NOT NULL", (event,))
            for entry_id, detail in rows.fetchall():
                new = fn(detail)
                if new != detail:
                    db.execute("UPDATE audit SET detail = ? WHERE id = ?", (new, entry_id))
                    changed += 1
        return changed

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
            "SELECT ts, user, ip, event, instance, ok, detail, project, branch FROM audit"
            + (f" WHERE {' AND '.join(where)}" if where else "")
            + " ORDER BY id DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        )
        return [_entry(row) for row in rows]

    def search_logs(
        self, filters: dict[str, Any], sort: str = "ts", descending: bool = True, limit: int = 200, offset: int = 0
    ) -> tuple[list[Document], int]:
        """Audit-log entries matching `filters` (core/audit.search describes them), sorted, one page of them, and
        how many match in all."""
        where, values = _audit_where(filters)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        column = _AUDIT_SORT.get(sort, "ts")
        order = f" ORDER BY {column} {'DESC' if descending else 'ASC'}, id {'DESC' if descending else 'ASC'}"
        db = self._db()
        total = int(db.execute("SELECT COUNT(*) FROM audit" + clause, values).fetchone()[0])
        rows = db.execute(
            "SELECT ts, user, ip, event, instance, ok, detail, project, branch FROM audit" + clause + order
            + " LIMIT ? OFFSET ?",
            (*values, limit, offset),
        )  # fmt: skip
        return [_entry(row) for row in rows], total

    def audit_facets(self) -> dict[str, list[str]]:
        """The users and events found in the audit log, for filters."""
        db = self._db()
        return {
            "users": [r[0] for r in db.execute("SELECT DISTINCT user FROM audit ORDER BY user LIMIT 2000")],
            "events": [r[0] for r in db.execute("SELECT DISTINCT event FROM audit ORDER BY event LIMIT 2000")],
        }


def _entry(row: tuple[Any, ...]) -> Document:
    ts, who, ip, event, inst, ok, detail, *scope = row
    entry: Document = {"ts": ts, "user": who, "ip": ip, "event": event, "ok": bool(ok)}
    if inst:
        entry["instance"] = inst
    if detail:
        entry["detail"] = detail
    if scope and scope[0]:
        entry["project"] = scope[0]
    if len(scope) > 1 and scope[1]:
        entry["branch"] = scope[1]
    return entry


def _in(column: str, names: list[str]) -> tuple[str, list[Any]]:
    if not names:
        return "0", []
    return f"{column} IN ({', '.join('?' for _ in names)})", list(names)


def _audit_where(filters: dict[str, Any]) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    values: list[Any] = []
    for key in ("user", "instance", "event", "ip"):
        if filters.get(key):
            where.append(f"{key} = ?")
            values.append(filters[key])
    if filters.get("ok") is not None:
        where.append("ok = ?")
        values.append(int(bool(filters["ok"])))
    if filters.get("since") is not None:
        where.append("ts >= ?")
        values.append(int(filters["since"]))
    if filters.get("until") is not None:
        where.append("ts < ?")
        values.append(int(filters["until"]))
    for column in ("project", "branch"):
        if filters.get(column):  # recorded with the entry, or (older entries) one of its instances now
            known, known_values = _in("instance", list(filters.get(f"{column}_instances") or []))
            where.append(f"({column} = ? OR ({column} IS NULL AND {known}))")
            values += [filters[column], *known_values]
    if filters.get("text"):
        pattern = "%" + str(filters["text"]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append(
            "(user LIKE ? ESCAPE '\\' OR event LIKE ? ESCAPE '\\' OR instance LIKE ? ESCAPE '\\'"
            " OR ip LIKE ? ESCAPE '\\' OR detail LIKE ? ESCAPE '\\')"
        )
        values += [pattern] * 5
    return where, values
