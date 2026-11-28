"""What every process of the panel on this host shares: locks, small short-lived records, and which process runs the
background work.

The panel may run several gunicorn workers (PANEL_WORKERS). A thread lock or a dict in memory then guards one worker
only, so whatever decides correctness lives here instead:

    SharedLock    a lock for every process of the host: a thread lock, then an flock on <manager dir>/locks/<name>.lock
                  (the same way core/slots.py ADMISSION works). Re-entrant by default, like the RLocks it replaces.
    SharedMap     short-lived records (one-time codes, tickets, nonces, sign-in flows) in the panel's storage, each
                  change one atomic write (storage().change), with an "expires" time after which they are gone.
    leadership    one process at a time runs the background work (monitor, automatic pause, backups, the gateway link,
                  the live events server): the one that holds <manager dir>/locks/leader.lock. The others try to take
                  it every few seconds, so when that process ends another one takes the work over.

Only one host is covered: an flock is not shared across hosts (as for core/slots.py).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import threading
import time
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sudoo.config import settings

Document = dict[str, Any]


def storage() -> Any:
    from sudoo.storage import storage as panel_storage  # late: the storage itself uses this module's locks

    return panel_storage()


def locks_dir() -> Path:
    path = settings.manager_dir / "locks"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _file_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:120] + ".lock"


_ALL: weakref.WeakSet[SharedLock] = weakref.WeakSet()


class SharedLock:
    """A lock for every process of the host. The thread lock first, then an flock held by the outermost acquire of
    this process (an flock belongs to the open file, so threads of one process need the thread lock to exclude each
    other). `reentrant=False` behaves like threading.Lock (a job's lock on its instance)."""

    def __init__(self, name: str, reentrant: bool = True) -> None:
        self.name = name
        self.reentrant = reentrant
        self._thread: Any = threading.RLock() if reentrant else threading.Lock()
        self._depth = 0
        self._fd: int | None = None
        self._pid = 0
        _ALL.add(self)

    def _file(self) -> int:
        if self._fd is None or self._pid != os.getpid():
            self._fd = os.open(locks_dir() / _file_name(self.name), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
            self._pid = os.getpid()
        return self._fd

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        deadline = None if timeout < 0 else time.monotonic() + timeout
        if not (self._thread.acquire(blocking, timeout) if blocking else self._thread.acquire(False)):
            return False
        try:
            if self._depth == 0:
                fd = self._file()
                if blocking and deadline is None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                else:
                    while True:
                        try:
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if not blocking or (deadline is not None and time.monotonic() >= deadline):
                                self._thread.release()
                                return False
                            time.sleep(0.01)
            self._depth += 1
        except BaseException:
            self._thread.release()
            raise
        return True

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0 and self._fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        self._thread.release()

    def __enter__(self) -> SharedLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def locked(self) -> bool:
        """Held now, by a thread of this process or by another process."""
        if self._depth > 0:
            return True
        try:
            fd = os.open(locks_dir() / _file_name(self.name), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return False
        finally:
            os.close(fd)  # releases the try
        return False

    def _forget(self) -> None:
        if self._fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._fd)
        self._thread = threading.RLock() if self.reentrant else threading.Lock()
        self._depth, self._fd, self._pid = 0, None, 0


def _forget_in_child() -> None:
    """A forked child holds copies of the parent's thread locks and of its lock files (the same open files: closing
    them in the child would release nothing, holding them would look like the parent): it starts without any."""
    for lock in list(_ALL):
        lock._forget()
    _LEADER.fd, _LEADER.pid, _LEADER.started = None, 0, False


os.register_at_fork(after_in_child=_forget_in_child)


class KeyedLocks:
    """One SharedLock per key, made on first use (instance names: a job's lock on its instance)."""

    def __init__(self, prefix: str, reentrant: bool = False) -> None:
        self._prefix = prefix
        self._reentrant = reentrant
        self._locks: dict[str, SharedLock] = {}
        self._lock = threading.Lock()

    def __getitem__(self, key: str) -> SharedLock:
        with self._lock:
            found = self._locks.get(key)
            if found is None:
                found = self._locks[key] = SharedLock(f"{self._prefix}{key}", self._reentrant)
            return found

    def __contains__(self, key: object) -> bool:
        return key in self._locks

    def clear(self) -> None:
        with self._lock:
            self._locks.clear()


# ---------------------------------------------------------------- short-lived records


class SharedMap:
    """Records of `collection` in the panel's storage, for every process: each has an "expires" time (seconds since
    the epoch) after which it counts as gone, and expired ones are removed now and then."""

    PRUNE_EVERY = 60.0

    def __init__(self, collection: str) -> None:
        self.collection = collection
        self._pruned = 0.0
        self.lock = SharedLock(f"map-{collection}")  # for a check over several records (a count, a limit)

    def _live(self, doc: Document | None, now: float) -> Document | None:
        return doc if doc is not None and float(doc.get("expires") or 0) > now else None

    def put(self, key: str, doc: Document) -> None:
        self.prune()
        storage().put(self.collection, key, doc)

    def get(self, key: str) -> Document | None:
        return self._live(storage().get(self.collection, key), time.time())

    def take(self, key: str, now: float | None = None) -> Document | None:
        """The record, removed in the same write (once, for every process); None when missing or expired (at
        `now`, by default the current time)."""
        taken: list[Document] = []

        def apply(doc: Document | None) -> None:
            if doc is not None:
                taken.append(doc)
            return None

        storage().change(self.collection, key, apply)
        return self._live(taken[0], time.time() if now is None else now) if taken else None

    def change(self, key: str, fn: Callable[[Document | None], Document | None]) -> Document | None:
        """fn(the live record or None) -> the record (None removes it), in one write."""
        now = time.time()
        return storage().change(self.collection, key, lambda doc: fn(self._live(doc, now)))

    def items(self) -> dict[str, Document]:
        now = time.time()
        return {key: doc for key, doc in storage().all(self.collection).items() if self._live(doc, now)}

    def delete(self, key: str) -> None:
        storage().delete(self.collection, key)

    def clear(self) -> None:
        for key in list(storage().all(self.collection)):
            storage().delete(self.collection, key)

    def prune(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._pruned < self.PRUNE_EVERY:
            return
        self._pruned = now
        for key, doc in storage().all(self.collection).items():
            if float(doc.get("expires") or 0) <= now:

                def gone(found: Document | None) -> Document | None:
                    return None if found is None or float(found.get("expires") or 0) <= now else found

                storage().change(self.collection, key, gone)


# ---------------------------------------------------------------- which process runs the background work


class _Leader:
    def __init__(self) -> None:
        self.fd: int | None = None
        self.pid = 0
        self.started = False
        self.lock = threading.Lock()
        self.work: list[Callable[[], None]] = []


_LEADER = _Leader()
LEADER_RETRY_SECONDS = 3.0


LEADER_FILE = "leader.lock"


def _try_lead() -> bool:
    if _LEADER.fd is not None and _LEADER.pid == os.getpid():
        return True
    fd = os.open(locks_dir() / LEADER_FILE, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _LEADER.fd, _LEADER.pid = fd, os.getpid()  # kept for the process's whole life
    return True


def is_leader() -> bool:
    """Whether this process runs the background work now."""
    return _LEADER.fd is not None and _LEADER.pid == os.getpid()


def when_leader(work: Callable[[], None]) -> None:
    """Run `work` once this process holds the leadership: now when it can take it, else as soon as the process that
    holds it ends (another worker, after a crash or a restart)."""
    with _LEADER.lock:
        if _try_lead():
            run_now = True
        else:
            run_now = False
            _LEADER.work.append(work)
            if not _LEADER.started:
                _LEADER.started = True
                threading.Thread(target=_wait_to_lead, daemon=True, name="leadership").start()
    if run_now:
        work()


def _wait_to_lead() -> None:
    while True:
        time.sleep(LEADER_RETRY_SECONDS)
        with _LEADER.lock:
            if not _try_lead():
                continue
            waiting, _LEADER.work = _LEADER.work, []
        for work in waiting:
            with contextlib.suppress(Exception):
                work()
        return
