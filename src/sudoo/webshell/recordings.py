"""Recordings of shells, and the list of past shells.

A recording is what was shown and typed, in the asciicast v2 format (one JSON array per line: seconds since the
start, "o" output, "i" input, "r" new size "COLSxROWS", "m" a note), so it can also be played with asciinema.
Each line is encrypted with the vault key (core/vault.py, label "shell-recording:<session id>") before it is
written: what was typed may include passwords. Files are in <ODOO_ROOT>/manager/shell-recordings/ (mode 600).

Size: one recording holds at most SHELL_RECORDING_MAX_MB; beyond it only input is recorded (a note says so), and
the shell closes when input alone reaches the size again, so a shell cannot escape its recording by printing a lot.
All recordings together hold at most SHELL_RECORDINGS_TOTAL_MB (the oldest are deleted first), and recordings and
the list of past shells (storage collection "shell_sessions") are kept SHELL_RECORDING_DAYS.
"""

from __future__ import annotations

import codecs
import json
import os
import time
from pathlib import Path
from typing import Any, TextIO

from sudoo.config import settings
from sudoo.core import vault
from sudoo.storage import storage

COLLECTION = "shell_sessions"
MB = 1024 * 1024


def folder() -> Path:
    path = settings.manager_dir / "shell-recordings"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def _file(session_id: str) -> Path:
    if not session_id.isalnum():
        raise ValueError("not a session id")
    return folder() / f"{session_id}.cast"


def _label(session_id: str) -> str:
    return f"shell-recording:{session_id}"


class Recorder:
    """Writes one shell's recording as it happens."""

    def __init__(self, session_id: str, header: dict[str, Any], limit_bytes: int) -> None:
        self.session_id = session_id
        self.limit = max(1024, limit_bytes)
        self.size = 0
        self.output_full = False
        self.started = time.monotonic()
        self._label = _label(session_id)
        self._output = codecs.getincrementaldecoder("utf-8")("replace")
        self._input = codecs.getincrementaldecoder("utf-8")("replace")
        fd = os.open(_file(session_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._fh: TextIO | None = os.fdopen(fd, "w", buffering=64 * 1024)
        self._write(header)

    def _write(self, item: object) -> None:
        if self._fh is None:
            return
        line = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        self.size += len(line.encode())
        self._fh.write(vault.encrypt(line, self._label) + "\n")

    def _event(self, kind: str, text: str) -> None:
        self._write([round(time.monotonic() - self.started, 3), kind, text])

    def output(self, data: bytes) -> None:
        if self.output_full:
            return
        text = self._output.decode(data)
        if self.size + len(text.encode()) > self.limit:
            self.output_full = True
            self._event("m", "The recording reached its size limit: from here on, only input is recorded.")
            return
        if text:
            self._event("o", text)

    def input(self, data: bytes) -> bool:
        """Record typed input; False when even input no longer fits (the shell must then close)."""
        text = self._input.decode(data)
        if self.size + len(text.encode()) > 2 * self.limit:
            return False
        if text:
            self._event("i", text)
        return True

    def resize(self, cols: int, rows: int) -> None:
        self._event("r", f"{cols}x{rows}")

    def note(self, text: str) -> None:
        self._event("m", text)

    def close(self) -> int:
        """Finish the file; the size of the recording (bytes of its plain text)."""
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        return self.size


def read(session_id: str) -> tuple[dict[str, Any], list[list[Any]]]:
    """(header, events) of a recording. Raises FileNotFoundError, or vault.VaultError when it was changed."""
    lines = _file(session_id).read_text().splitlines()
    label = _label(session_id)
    items = [json.loads(vault.decrypt(line, label)) for line in lines if line]
    if not items or not isinstance(items[0], dict):
        raise FileNotFoundError(session_id)
    return items[0], [item for item in items[1:] if isinstance(item, list)]


def exists(session_id: str) -> bool:
    try:
        return _file(session_id).is_file()
    except ValueError:
        return False


# ---------------------------------------------------------------- past shells


def save(document: dict[str, Any]) -> None:
    storage().put(COLLECTION, str(document["id"]), document)


def past(instance: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Past shells, newest first."""
    found = [d for d in storage().all(COLLECTION).values() if instance is None or d.get("instance") == instance]
    found.sort(key=lambda d: float(d.get("started", 0)), reverse=True)
    return found[:limit]


def get(session_id: str) -> dict[str, Any] | None:
    return storage().get(COLLECTION, session_id)


def prune(now: float | None = None) -> None:
    """Delete what is older than SHELL_RECORDING_DAYS, then the oldest recordings beyond
    SHELL_RECORDINGS_TOTAL_MB."""
    now = time.time() if now is None else now
    oldest = now - settings.shell_recording_days * 86400
    for key, document in storage().all(COLLECTION).items():
        if float(document.get("started", 0)) < oldest and document.get("ended"):
            storage().delete(COLLECTION, key)
            if key.isalnum():
                _file(key).unlink(missing_ok=True)
    files = sorted(folder().glob("*.cast"), key=lambda p: p.stat().st_mtime)
    for path in files:
        if float(path.stat().st_mtime) < oldest:
            path.unlink(missing_ok=True)
    files = [p for p in files if p.exists()]
    total = sum(p.stat().st_size for p in files)
    limit = settings.shell_recordings_total_mb * MB
    for path in files:
        if total <= limit:
            break
        total -= path.stat().st_size
        path.unlink(missing_ok=True)
        session_id = path.stem

        def removed(document: dict[str, Any] | None) -> dict[str, Any] | None:
            return {**document, "recording_removed": True} if document else None

        storage().change(COLLECTION, session_id, removed)
