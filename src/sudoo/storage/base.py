"""The interface of a storage plugin: where the panel keeps its own data.

The panel's data is small and document-shaped (an account, a token, the state of an instance), plus an append-only
audit log. So a storage plugin offers documents in named collections, an atomic read-modify-write, and the log.
Built in: "sqlite" (sqlite.py). A plugin for another database implements this Protocol and is registered under the
entry-point group "sudoo.storage" (docs/PLUGINS.md).

Collections used by the panel: users, api_tokens, instance_state, creations.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

Document = dict[str, Any]


class Storage(Protocol):
    def get(self, collection: str, key: str) -> Document | None:
        """One document, or None."""
        ...

    def all(self, collection: str) -> dict[str, Document]:
        """Every document of a collection, by key."""
        ...

    def put(self, collection: str, key: str, document: Document) -> None:
        """Create or replace a document."""
        ...

    def delete(self, collection: str, key: str) -> bool:
        """Delete a document; True when it existed."""
        ...

    def change(self, collection: str, key: str, fn: Callable[[Document | None], Document | None]) -> Document | None:
        """Atomically: read the document (None when missing), call fn, store what it returns (None deletes).
        fn may raise to change nothing. Returns the stored document."""
        ...

    def log(self, entry: Document) -> None:
        """Append an audit-log entry: {"ts", "user", "ip", "event", "ok", "instance"?, "detail"?}."""
        ...

    def logs(
        self, limit: int = 200, offset: int = 0, user: str | None = None, instance: str | None = None
    ) -> list[Document]:
        """Audit-log entries, newest first, optionally of one user or instance."""
        ...
