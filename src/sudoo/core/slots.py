"""The running slots that jobs hold, kept in the panel's storage so every process and every restart sees them.

A job that starts or stops an instance holds its running slot (core/jobs.py `starting`) while it runs and until the
monitor has observed the instance after the job ended. That was memory of one process. Here the same facts are one
document (collection "slots", key "main"):

    {"rev": n, "seq": n,
     "held": {job id: {"instance", "owner", "user", "end_seq": n | null}},
     "observed": {instance: {"seq": n, "running": bool}}}

ORDER is a counter in the document (`seq`), never a clock. A job takes `end_seq` when it ends. A monitor pass takes a
`seq` BEFORE it reads and publishes what it read under that number. A hold is released only by an observation whose
seq is larger than its end_seq: the read began after the job ended. An observation is accepted only when its seq is
newer than the one stored for that instance, so a slow pass can never overwrite a newer one. An instance that did not
answer is not published, so it releases nothing: unknown is not stopped.

OWNERS. Each process makes a random owner id when it first records a hold, opens `owners/<id>.lock` (O_CLOEXEC, after
any fork) and keeps an exclusive flock on it for its whole life. Another process finds an owner dead only by taking
that lock without waiting, and keeps it while it records that (the hold gets an end_seq). A lock file that is missing,
or cannot be tried, proves nothing: the hold stays (fail closed) and `Snapshot.unknown_owners` says so. Only one host
can be proved this way: a database shared across hosts is not covered.

ADMISSION. `ADMISSION` is the lock the callers of the running-limit check hold until the job is registered
(limits/quotas.py LOCK): a thread lock plus an flock on `admission.lock`, so the check and the hold are one step for
all processes of the host. Every change of the document is one atomic read-modify-write of the storage with a pure
callback; `rev` grows with each. Waits are bounded (SLOT_STORE_WAIT_SECONDS, a setting) and a failure refuses the
start: `Unavailable`.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sudoo.config import settings
from sudoo.storage import storage

log = logging.getLogger(__name__)

COLLECTION = "slots"
KEY = "main"
RECLAIMED = "reclaimed"  # the owner of a hold whose process died


class Unavailable(Exception):
    """The slots could not be read or recorded in time: a start is refused (never started unrecorded)."""


class Busy(Unavailable):
    """Another start is being admitted and did not finish within the wait."""


@dataclass(frozen=True)
class Admit:
    """What the running limits need to judge one start, read BEFORE the write so the judging callback does no I/O.
    `existing`: the instances that exist; `production`: those of them in production; `owned`: the non-production ones
    `user` created (the person whose own limit applies, or empty). `exempt`: the start is of a production instance
    (the limits never refuse those). `fresh`: this process's last observation was published (see `PUBLISH`)."""

    existing: frozenset[str]
    production: frozenset[str]
    owned: frozenset[str]
    user: str | None
    exempt: bool
    max_running: int
    user_limit: int
    own_voice: bool
    fresh: bool = True


@dataclass(frozen=True)
class Decision:
    kind: str  # "total", "own" or "stale"
    limit: int
    count: int


class Refused(Unavailable):
    """The running limits refuse this start, judged on the document inside the write that would have recorded it."""

    def __init__(self, decision: Decision, admit: Admit) -> None:
        self.decision, self.admit = decision, admit
        super().__init__(describe(decision, admit) if describe else f"Refused ({decision.kind}).")


describe: Callable[[Decision, Admit], str] | None = None  # set by instances/capacity.py: the words of a refusal


class _Publish:
    """Whether this process's last monitor publication worked: a refusal switch only, never a count."""

    failed = False


PUBLISH = _Publish()


def counted_in(held: dict[str, dict[str, Any]], observed: dict[str, dict[str, Any]], existing: Any) -> set[str]:
    """The instances that count as running or starting according to the document alone: those observed running that
    exist, and those a hold names until it is released (an observation that began after the job ended, or the instance
    gone). A hold that has not ended counts always (a creation does not exist yet)."""
    names = {name for name, seen in observed.items() if seen.get("running")} & set(existing)
    for hold in held.values():
        end = hold.get("end_seq")
        instance = hold["instance"]
        if end is not None and (instance not in existing or int(observed.get(instance, {}).get("seq", 0)) > int(end)):
            continue
        names.add(instance)
    return names


def decide(names: set[str], admit: Admit, instance: str | None) -> Decision | None:
    """Pure: why the running limits refuse one more start of `instance`, or None. Production is exempt from the
    limits only (a storage or record failure refuses it like any other start). An instance already counted holds its
    slot and is not asked again."""
    if admit.exempt or (instance is not None and instance in names):
        return None
    if not admit.fresh:
        return Decision("stale", 0, 0)
    if admit.max_running and len(names) >= admit.max_running:
        return Decision("total", admit.max_running, len(names))
    if admit.user and admit.user_limit:
        own = len(names & admit.owned)
        if own >= admit.user_limit:
            return Decision("own", admit.user_limit, own)
    return None


_ARMED = threading.local()


def arm(admit: Admit, instance: str | None) -> None:
    """The check that just passed (instances/capacity.py refusal), for the next start of this thread to be judged
    again inside its write. Only while this thread holds the admission lock (a check made outside it is not followed
    by a start the lock covers); cleared when the lock is released and by the start that consumes it."""
    if ADMISSION.held_by_me():
        _ARMED.value = (admit, instance)


def _consume(instance: str) -> Admit | None:
    found = getattr(_ARMED, "value", None)
    _ARMED.value = None
    if found is None or found[1] not in (None, instance):
        return None
    return found[0]


def _blank() -> dict[str, Any]:
    return {"rev": 0, "seq": 0, "held": {}, "observed": {}}


def _wait() -> float:
    return float(settings.slot_store_wait_seconds)


def _dir() -> Path:
    path = settings.manager_dir / "owners"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


# ------------------------------------------------------------------ this process's owner


class _Owner:
    def __init__(self) -> None:
        self.id: str | None = None
        self.fd: int | None = None
        self.pid = 0
        self.lock = threading.Lock()


_OWNER = _Owner()


def _forget_in_child() -> None:
    """After a fork the child holds a copy of the parent's lock: closing it must not look like the parent died (the
    lock belongs to the open file, which the parent keeps), and the child must not look alive for the parent."""
    if _OWNER.fd is not None:
        with contextlib.suppress(OSError):
            os.close(_OWNER.fd)
    _OWNER.id, _OWNER.fd, _OWNER.pid = None, None, 0
    _OWNER.lock = threading.Lock()


os.register_at_fork(after_in_child=_forget_in_child)


def owner() -> str:
    """This process's owner id; its lock file is locked BEFORE the id is first returned (so before any hold names it)."""
    with _OWNER.lock:
        if _OWNER.id is not None and _OWNER.pid == os.getpid():
            return _OWNER.id
        owner_id = uuid.uuid4().hex  # never reused
        try:
            fd = os.open(_dir() / f"{owner_id}.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Unavailable("The panel could not record this start (its owner lock failed).") from exc
        _OWNER.id, _OWNER.fd, _OWNER.pid = owner_id, fd, os.getpid()
        return owner_id


@contextlib.contextmanager
def _dead(owner_id: str) -> Iterator[bool | None]:
    """True while the lock of `owner_id` is held by this process because nobody else holds it (the owner is dead);
    False: it is alive; None: unknown (no lock file, or it cannot be opened)."""
    path = _dir() / f"{owner_id}.lock"
    try:
        fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    except OSError:
        yield None
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        except OSError:
            yield None
            return
        yield True
        with contextlib.suppress(OSError):
            path.unlink()
    finally:
        os.close(fd)


# ------------------------------------------------------------------ the document


def _read() -> dict[str, Any]:
    try:
        found = storage().get(COLLECTION, KEY)
    except Exception as exc:  # noqa: BLE001 - any storage failure
        raise Unavailable("The panel could not read which instances are starting.") from exc
    if found is None:
        return _blank()
    if not isinstance(found.get("held"), dict) or not isinstance(found.get("observed"), dict):
        raise Unavailable("The record of starting instances is unreadable.")
    return found


def _change(fn: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """One atomic read-modify-write of the document; `fn` changes it in place and only works on the document."""

    def apply(found: dict[str, Any] | None) -> dict[str, Any]:
        doc = _blank() if found is None else found
        fn(doc)
        doc["rev"] = int(doc.get("rev", 0)) + 1
        return doc

    store = storage()
    bounded = getattr(store, "bounded_change", None)
    if bounded is None:  # no bounded, atomic write to rely on: refuse, never fall back to something process-local
        raise Unavailable("The configured storage cannot record which instances are starting (no bounded change).")
    try:
        result = bounded(COLLECTION, KEY, apply, _wait())
    except Exception as exc:  # noqa: BLE001 - locked, full, unreadable
        raise Unavailable("The panel could not record this start in time.") from exc
    assert result is not None
    return result


def next_seq() -> int:
    """The next number of the order (one write). Unavailable when it cannot be taken."""
    return int(_change(lambda doc: doc.__setitem__("seq", int(doc.get("seq", 0)) + 1))["seq"])


def try_seq() -> int | None:
    """next_seq, or None: a monitor pass without a number publishes nothing and so releases nothing."""
    try:
        return next_seq()
    except Unavailable as exc:
        PUBLISH.failed = True
        log.warning("monitor: no observation number (%s)", exc)
        return None


def record_hold(job_id: str, instance: str, user: str) -> None:
    """Record, before the job's thread exists, that `job_id` holds the slot of `instance`. Unavailable: not started.
    When a limit check was armed by this thread (arm), the same write judges it again against the document: Refused
    and nothing recorded when the running limits would be passed."""
    owner_id = owner()
    admit = _consume(instance)
    refusal: list[Decision] = []

    def add(doc: dict[str, Any]) -> None:
        refusal.clear()
        if admit is not None:
            found = decide(counted_in(doc["held"], doc["observed"], admit.existing), admit, instance)
            if found is not None:
                refusal.append(found)
                return
        doc["held"][job_id] = {"instance": instance, "owner": owner_id, "user": user, "end_seq": None}

    _change(add)
    if refusal and admit is not None:
        raise Refused(refusal[0], admit)


def record_end(job_id: str) -> int:
    """The job ended: take its end_seq, in the same write that stores it. Unavailable: the hold stays as it is."""
    taken: list[int] = []

    def end(doc: dict[str, Any]) -> None:
        doc["seq"] = int(doc.get("seq", 0)) + 1
        taken[:] = [doc["seq"]]
        if job_id in doc["held"]:
            doc["held"][job_id]["end_seq"] = doc["seq"]

    _change(end)
    return taken[0]


def drop(job_ids: list[str]) -> bool:
    """Forget holds; True when they are gone. Failing to forget a hold that an observation released is harmless
    (the same rule releases it again), so it is only logged and returns False."""
    if not job_ids:
        return True
    try:

        def forget(doc: dict[str, Any]) -> None:
            for job_id in job_ids:
                doc["held"].pop(job_id, None)

        _change(forget)
    except Unavailable as exc:
        log.warning("slots: could not forget %d holds (%s)", len(job_ids), exc)
        return False
    return True


def publish_observed(seq: int, running: dict[str, bool], present: set[str]) -> None:
    """What a monitor pass that began at `seq` saw: instance -> running. An instance already stored with a newer
    seq keeps it. Entries of instances that no longer exist are removed. Failure is only logged: nothing is released."""

    def put(doc: dict[str, Any]) -> None:
        observed = doc["observed"]
        for name, is_running in running.items():
            if int(observed.get(name, {}).get("seq", 0)) < seq:
                observed[name] = {"seq": seq, "running": bool(is_running)}
        for name in [n for n in observed if n not in present]:
            del observed[name]

    try:
        _change(put)
    except Unavailable as exc:
        PUBLISH.failed = True  # new non-production starts are refused until a publication works (decide: "stale")
        log.warning("monitor: could not publish the observation (%s)", exc)
    else:
        PUBLISH.failed = False


@dataclass
class Snapshot:
    """The document as read once, with the holds of dead owners already ended."""

    held: dict[str, dict[str, Any]] = field(default_factory=dict)  # every hold (this process's too), as read
    others: dict[str, dict[str, Any]] = field(default_factory=dict)  # holds of other owners: job id -> hold
    observed: dict[str, dict[str, Any]] = field(default_factory=dict)
    unknown_owners: set[str] = field(default_factory=set)

    def observed_seq(self, instance: str) -> int:
        return int(self.observed.get(instance, {}).get("seq", 0))

    def running(self) -> set[str]:
        return {name for name, seen in self.observed.items() if seen.get("running")}

    def released(self, hold: dict[str, Any]) -> bool:
        """Whether an observation that began after the hold's job ended exists."""
        end = hold.get("end_seq")
        return end is not None and self.observed_seq(hold["instance"]) > int(end)


def snapshot() -> Snapshot | None:
    """Read the slots once. Holds of other owners whose process is dead get their end_seq now (the probe's lock is
    kept until that is written). None when they cannot be read: callers then fail closed where it matters (a start
    is refused by record_hold) and show what this process knows."""
    try:
        doc = _read()
    except Unavailable as exc:
        log.warning("slots: %s", exc)
        return None
    mine = owner() if _OWNER.id is not None else None
    result = Snapshot(observed=dict(doc["observed"]))
    for job_id, hold in doc["held"].items():
        owner_id = hold.get("owner")
        if owner_id == mine:
            result.held[job_id] = hold
            continue
        if hold.get("end_seq") is None and owner_id != RECLAIMED:
            with _dead(str(owner_id)) as gone:
                if gone is None:
                    result.unknown_owners.add(str(owner_id))
                elif gone:
                    try:
                        taken = record_reclaimed(job_id)
                    except Unavailable as exc:  # still held, counted: a dead owner's hold is ended on a later read
                        log.warning("slots: could not end the hold of a dead owner (%s)", exc)
                    else:
                        if taken is None:  # the hold was removed meanwhile: nothing to count
                            continue
                        hold = {**hold, "end_seq": taken, "owner": RECLAIMED}
        result.others[job_id] = hold
        result.held[job_id] = hold
    return result


def record_reclaimed(job_id: str) -> int | None:
    """End the hold of a dead owner (its job died with its process): the same end_seq rule as a finished job. None
    when the hold is gone already (nothing to end)."""
    taken: list[int] = []

    def end(doc: dict[str, Any]) -> None:
        held = doc["held"].get(job_id)
        taken.clear()
        if held is None:
            return
        if held.get("end_seq") is not None:
            taken[:] = [int(held["end_seq"])]
            return
        doc["seq"] = int(doc.get("seq", 0)) + 1
        held["end_seq"], held["owner"] = doc["seq"], RECLAIMED
        taken[:] = [doc["seq"]]

    _change(end)
    return taken[0] if taken else None


# ------------------------------------------------------------------ admission


class AdmissionLock:
    """Re-entrant, cross-process: the thread lock first, then an flock on admission.lock for the outermost hold."""

    def __init__(self) -> None:
        self._thread = threading.RLock()
        self._depth = 0
        self._fd: int | None = None
        self._pid = 0

    def _file(self) -> int:
        if self._fd is None or self._pid != os.getpid():
            path = settings.manager_dir / "admission.lock"
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
            self._pid = os.getpid()
        return self._fd

    def held_by_me(self) -> bool:
        return self._depth > 0 and self._thread._is_owned()  # type: ignore[attr-defined]

    def __enter__(self) -> AdmissionLock:
        wait = _wait()
        if not self._thread.acquire(timeout=wait):
            raise Busy("Another start is being checked. Try again in a moment.")
        try:
            if self._depth == 0:
                fd = self._file()
                deadline = time.monotonic() + wait
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise Busy("Another start is being checked. Try again in a moment.") from None
                        time.sleep(0.01)
                    except OSError as exc:
                        raise Unavailable("The panel could not check this start.") from exc
            self._depth += 1
        except BaseException:
            self._thread.release()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self._depth -= 1
        if self._depth == 0:
            _ARMED.value = None
        if self._depth == 0 and self._fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        self._thread.release()


ADMISSION = AdmissionLock()


def _forget_admission_in_child() -> None:
    """A forked child holds copies of the parent's thread lock (maybe owned by a thread that does not exist in the
    child) and of its flock file (the same open file: the child would look like the holder). It starts without either.
    """
    if ADMISSION._fd is not None:
        with contextlib.suppress(OSError):
            os.close(ADMISSION._fd)
    ADMISSION._thread = threading.RLock()
    ADMISSION._depth, ADMISSION._fd, ADMISSION._pid = 0, None, 0


os.register_at_fork(after_in_child=_forget_admission_in_child)
