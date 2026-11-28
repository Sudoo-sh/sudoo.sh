"""Small in-process caches for what the panel reads again and again: files, the output of slow commands, answers.

Each worker process has its own (core/shared.py keeps what must be the same in every process); none decides
correctness, so they need no server. Each says exactly when its value stops being valid:

    FileMemo       a value read from a file, kept while the file is the same one, with the same size and the same
                   modification time; a file changed in the last second is read every time ("racy": the file
                   system's clock is coarse, so two changes within one tick could otherwise look alike, the same
                   rule git uses for its index)
    VersionedMemo  a value computed for a key, kept while the key's version (anything comparable: a counter, the
                   time of the latest measurement) is unchanged, and at most `seconds` long

Values that callers may change are copied by the caller (store.read_env returns a new dict every time).
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from pathlib import Path

RACY_SECONDS = 1.0

Signature = tuple[int, int, int]


def signature(path: str | os.PathLike[str]) -> Signature | None:
    """(inode, size, modification time in ns) of a file, or None when it does not exist."""
    try:
        found = os.stat(path)
    except OSError:
        return None
    return found.st_ino, found.st_size, found.st_mtime_ns


def settled(sig: Signature | None) -> bool:
    """True when the file was last changed long enough ago for its signature to identify its content."""
    return sig is not None and time.time() - sig[2] / 1e9 > RACY_SECONDS


class FileMemo[P: str | Path, T]:
    """read(path), remembered per path while the file's signature stays the same."""

    def __init__(self, read: Callable[[P], T], size: int = 4096) -> None:
        self._read: Callable[[P], T] = read
        self._size = size
        self._lock = threading.Lock()
        self._entries: OrderedDict[P, tuple[Signature, T]] = OrderedDict()

    def get(self, path: P) -> T:
        sig = signature(path)  # before reading: a change after this point gives another signature next time
        with self._lock:
            found = self._entries.get(path)
        if found is not None and found[0] == sig:
            return found[1]
        value = self._read(path)
        with self._lock:
            if sig is not None and settled(sig):
                self._entries[path] = (sig, value)
                self._entries.move_to_end(path)
                while len(self._entries) > self._size:
                    self._entries.popitem(last=False)
            else:
                self._entries.pop(path, None)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class VersionedMemo[T]:
    """build(), remembered per key while its version is the same and for at most `seconds`. When the value must be
    built again, one caller builds it and the others asking for the same key wait for that result instead of all
    building it at once."""

    def __init__(self, seconds: float, size: int = 1024) -> None:
        self._seconds = seconds
        self._size = size
        self._lock = threading.Lock()
        self._entries: OrderedDict[Hashable, tuple[Hashable, float, T]] = OrderedDict()
        self._building: dict[Hashable, threading.Lock] = {}

    def _fresh(self, key: Hashable, version: Hashable) -> tuple[Hashable, float, T] | None:
        """The entry of `key` when it is of `version` and young enough, else None."""
        with self._lock:
            found = self._entries.get(key)
        if found is not None and found[0] == version and time.monotonic() - found[1] < self._seconds:
            return found
        return None

    def get(self, key: Hashable, version: Hashable, build: Callable[[], T]) -> T:
        found = self._fresh(key, version)
        if found is not None:
            return found[2]
        with self._lock:
            building = self._building.setdefault(key, threading.Lock())
        with building:
            found = self._fresh(key, version)  # built by another caller meanwhile
            if found is not None:
                return found[2]
            built = build()
            with self._lock:
                self._entries[key] = (version, time.monotonic(), built)
                self._entries.move_to_end(key)
                while len(self._entries) > self._size:
                    self._entries.popitem(last=False)
                if len(self._building) > 2 * self._size:  # one small lock per key asked for: bounded too
                    self._building = {key: building}
            return built

    def forget(self, key: Hashable) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
