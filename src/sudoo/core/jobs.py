"""Background actions ("jobs") such as creating, restarting or updating an instance.

Every action runs in its own thread. Actions on the same instance run one after another (a lock per instance);
actions on different instances run in parallel. A job records user-facing steps (each with an optional progress line
such as "Downloading Odoo 19.0: 2 of 5 parts"), notes, its result and, when it fails, a plain-language hint; the
panel shows those while it runs and afterwards (the last 300 jobs are kept in memory).

The technical log of a job (the commands it runs and everything they print) is a server log: it is never sent to
the panel's users, whatever their role. Every line goes to the panel's own process log (`docker logs sudoo-panel`),
prefixed with the job id and the instance, with addresses' passwords and every masked secret replaced. The job also
keeps it in memory for the panel's own use (the failure hint, a build's Odoo warnings, test results).

Every change of a job is published as a live event (core/events.py), "job": when it starts, moves to another step,
reports progress, adds a note or ends.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from typing import Any

from sudoo.config import settings
from sudoo.core import events, failure, slots
from sudoo.core.hints import friendly_hint
from sudoo.core.shell import redact

JOBS: OrderedDict[str, Job] = OrderedDict()
_HELD: dict[str, Job] = {}  # jobs whose instance still counts as running (see starting())
_JOBS_LOCK = threading.Lock()
log = logging.getLogger("sudoo.jobs")
INSTANCE_LOCKS: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
ACTIVE_JOB: dict[str, Job] = {}  # instance name -> the job currently running on it
_GENERATIONS: defaultdict[str, int] = defaultdict(int)  # instance name -> jobs started or ended on it
_KEEP = 300
_END_LISTENERS: list[Callable[[Job], None]] = []
_START_LISTENERS: list[Callable[[Job], None]] = []
_PROGRESS_EVERY_SECONDS = 1.0  # a step's progress line is published at most this often


# Odoo's log while it installs or upgrades a module: "... INFO db odoo.modules.loading: loading sale/views/x.xml"
_ODOO_LOADING = re.compile(r"\bodoo\.modules\.loading: loading ([A-Za-z0-9_]+)/")


def _server_log() -> logging.Logger:
    """The logger of jobs' technical logs: the panel's standard error (docker logs sudoo-panel), one line each."""
    logger = logging.getLogger("sudoo.jobs")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


SERVER_LOG = _server_log()


class JobError(Exception):
    """A step failed; the message is shown to the user."""


class CommandFailed(JobError):
    """A command run by Job.run ended with an error: its message is technical, for the server's log only."""


class Job:
    def __init__(self, instance: str, action: str, kind: str, user: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.instance = instance
        self.action = action  # title shown to the user, e.g. "Restart"
        self.kind = kind  # machine name, e.g. "restart"
        self.user = user  # account name, or "visitor" / "system" for automatic jobs
        self.status = "queued"
        self.steps: list[dict[str, Any]] = []
        self.notes: list[dict[str, str]] = []
        self.log: list[str] = []
        self.result: dict[str, Any] = {}
        self.hint: str | None = None
        self.failure: dict[str, Any] | None = None  # "Why it failed" (core/failure.py): step, reason, error details
        self._failed_at: tuple[str | None, int, int] | None = None  # (step, log from, log to) of the failure
        self.started = time.time()
        self.finished: float | None = None
        self.holds_slot = False  # counted as running while it runs and until its instance is observed after it ended
        self.finished_mono: float | None = None  # time.monotonic() when it ended (in this process only)
        self.end_seq: int | None = None  # core/slots.py number of its end; None until recorded (the hold stays)
        self._progress_at = 0.0  # when the progress of a step was last published
        self.expected_steps: int | None = None  # set by operations that know how many steps they take
        self.details: dict[str, Any] = {}  # what the caller knows in advance (a creation's plan, without secrets)
        self._masked: list[str] = []  # secrets never written to the log (mask)
        self.request_key: str | None = None  # the API request that started it (same_request: sent twice, one job)

    def expect(self, steps: int) -> None:
        """Announce how many steps this job will take, so its progress can be shown as a percentage."""
        self.expected_steps = steps

    def progress_percent(self) -> int | None:
        """Finished steps out of the expected ones (a running job stays below 100); None when unknown."""
        if self.status == "success":
            return 100
        if self.status == "queued":
            return 0
        if not self.expected_steps:
            return None
        done = sum(1 for step in self.steps if step["status"] == "done")
        return min(99, round(100 * done / max(self.expected_steps, len(self.steps))))

    def changed(self) -> None:
        """Tell connected clients that this job changed."""
        events.publish("job", self.id)

    def mask(self, secret: str) -> None:
        """Never write `secret` into the log (a subscription code a command might print): it shows as ********."""
        if secret and secret not in self._masked:
            self._masked.append(secret)

    def write(self, text: str) -> None:
        """Add `text` to the technical log, with the user name and password of any address in it masked, and every
        secret given to mask(); each line also goes to the server's log (never to the panel's users)."""
        for secret in self._masked:
            text = text.replace(secret, "********")
        text = redact(text)
        self.log.append(text)
        for line in text.splitlines():
            if line.strip():
                SERVER_LOG.info("job %s %s: %s", self.id, self.instance, line.rstrip())

    def step(self, title: str) -> None:
        """Start a new user-facing step; the previous one is marked done."""
        for step in self.steps:
            if step["status"] == "running":
                step["status"] = "done"
        self.steps.append({"title": title, "status": "running", "started": time.time(), "log_from": len(self.log)})
        self.write(f"\n== {title}\n")
        self.changed()

    def mark_failure(self) -> None:
        """Remember the step running now and the part of the technical log it wrote as where the job failed (a step
        that puts things back afterwards writes more; it is not the failure). The first call counts."""
        if self._failed_at is not None:
            return
        running = next((step for step in reversed(self.steps) if step["status"] == "running"), None)
        start = int(running.get("log_from", 0)) if running else 0
        self._failed_at = (running["title"] if running else None, start, len(self.log))

    def failure_log(self) -> str:
        """The part of the technical log written by the failed step (all of it before mark_failure)."""
        if self._failed_at is None:
            return "".join(self.log)
        return "".join(self.log[self._failed_at[1] : self._failed_at[2]])

    def failed_step(self) -> str | None:
        if self._failed_at is not None and self._failed_at[0]:
            return self._failed_at[0]
        return next((step["title"] for step in self.steps if step["status"] == "failed"), None)

    def progress(self, text: str, percent: int | None = None) -> None:
        """Show how far the running step is, in plain words ("Downloading Odoo 19.0: 2 of 5 parts") and optionally as
        a percentage. Published as a "job" event when it changed, at most once a second (a change in between shows
        with the next one, or when the step ends)."""
        running = next((step for step in reversed(self.steps) if step["status"] == "running"), None)
        if running is None:
            return
        percent = None if percent is None else max(0, min(100, int(percent)))
        if running.get("progress") == text and running.get("percent") == percent:
            return
        running["progress"] = text
        running["percent"] = percent
        now = time.monotonic()
        if now - self._progress_at >= _PROGRESS_EVERY_SECONDS:
            self._progress_at = now
            self.changed()

    def note(self, text: str, level: str = "info") -> None:
        """A message for the user that does not stop the job ("info" or "warning")."""
        self.notes.append({"text": text, "level": level})
        self.write(f"[{level}] {text}\n")
        self.changed()

    def run(self, cmd: list[str], timeout: float | None = None, input: str | None = None) -> None:
        """Run a command, stream its output into the log, raise JobError when it fails."""
        if self.run_unchecked(cmd, timeout, input) != 0:
            raise CommandFailed(f"command failed: {' '.join(redact(a) for a in cmd[:4])} ...")

    def run_unchecked(self, cmd: list[str], timeout: float | None = None, input: str | None = None) -> int:
        """Run a command, stream its output into the log, return its exit code.

        The timeout holds whether or not the command writes anything: when it passes, the command and everything it
        started are stopped (with the container of a `docker compose run`) and JobError is raised."""
        return self._execute(cmd, timeout, input)

    def run_watched(
        self,
        cmd: list[str],
        timeout: float,
        on_line: Callable[[str], None],
        on_silence: Callable[[], None],
        silence_seconds: float = 10,
    ) -> int:
        """Like run_unchecked, also calling on_line(line) for every line and on_silence() whenever the command has
        written nothing for `silence_seconds` (a download that is still going), so the log keeps moving."""
        return self._execute(cmd, timeout, None, on_line, on_silence, silence_seconds)

    def _execute(
        self,
        cmd: list[str],
        timeout: float | None,
        input: str | None,
        on_line: Callable[[str], None] | None = None,
        on_silence: Callable[[], None] | None = None,
        silence_seconds: float = 10,
    ) -> int:
        """Run `cmd` in its own process group; a thread reads its output, so the deadline is checked on time."""
        cmd, container = _named_compose_run(cmd, self.id)
        self.write(f"$ {' '.join(cmd)}\n")
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if input else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,  # its own process group: a timeout stops what it started too
        )
        lines: queue.Queue[str | None] = queue.Queue()

        def read() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        def feed(text: str) -> None:
            assert proc.stdin is not None
            try:
                proc.stdin.write(text)
                proc.stdin.close()
            except OSError:  # the command ended, or closed its input before reading everything
                pass

        if input:  # written by a thread: a command that never reads its input cannot hold the step past the deadline
            threading.Thread(target=feed, args=(input,), daemon=True, name=f"job-{self.id}-input").start()
        threading.Thread(target=read, daemon=True, name=f"job-{self.id}-output").start()
        if not timeout and settings.job_command_max_hours > 0:  # a command without a limit of its own: never forever
            timeout = float(settings.job_command_max_hours) * 3600
        deadline = time.monotonic() + timeout if timeout else None
        while True:
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                _stop(proc, container)
                raise JobError("this step took too long and was stopped")
            wait = left if on_silence is None else min(silence_seconds, silence_seconds if left is None else left)
            try:
                line = lines.get(timeout=None if wait is None else max(0.05, wait))
            except queue.Empty:
                if on_silence is not None and (deadline is None or time.monotonic() < deadline):
                    on_silence()
                continue
            if line is None:
                break
            self.write(line)
            if on_line is not None:
                on_line(line)
            else:
                self._odoo_progress(line)
        try:  # the output is closed; the command itself may still be running
            return proc.wait(timeout=None if deadline is None else max(0.05, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _stop(proc, container)
            raise JobError("this step took too long and was stopped") from None

    def _odoo_progress(self, line: str) -> None:
        """While Odoo installs or upgrades modules, the module it is working on, as the step's progress."""
        found = _ODOO_LOADING.search(line)
        if found:
            self.progress(f"Setting up the module {found[1]}")

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "instance": self.instance,
            "action": self.action,
            "kind": self.kind,
            "user": self.user,
            "status": self.status,
            "started": self.started,
            "finished": self.finished,
            "steps": self.steps,
        }


# options of `docker compose` itself that take a value, to find the command after them (store.compose)
_COMPOSE_VALUE_OPTIONS = {"--project-directory", "-f", "--file", "-p", "--project-name", "--env-file", "--profile",
                          "--ansi", "--progress", "--parallel"}  # fmt: skip


def _named_compose_run(cmd: list[str], job_id: str) -> tuple[list[str], str | None]:
    """For `docker compose ... run ...` without --name: the command with a container name chosen here, and that name
    (stopping the command does not stop its container; the name lets the job remove it). Others are unchanged."""
    if cmd[:2] != ["docker", "compose"]:
        return cmd, None
    index = 2
    while index < len(cmd) and cmd[index].startswith("-"):
        index += 2 if cmd[index] in _COMPOSE_VALUE_OPTIONS else 1
    if index >= len(cmd) or cmd[index] != "run" or "--name" in cmd[index:]:
        return cmd, None
    name = f"sudoo-run-{job_id}-{uuid.uuid4().hex[:8]}"
    return [*cmd[: index + 1], "--name", name, *cmd[index + 1 :]], name


def _stop(proc: subprocess.Popen[str], container: str | None) -> None:
    """Stop a command that took too long: its whole process group, then the container it started, if any."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)
    if container:  # Docker missing or stuck: there is nothing more the job can do
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(["docker", "rm", "-f", container], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60, check=False)  # fmt: skip


def is_panel_job(instance: str) -> bool:
    """Jobs of the panel itself (plugins) use an "instance" with a colon, which no instance name can have."""
    return ":" in instance


def start(
    instance: str,
    action: str,
    kind: str,
    fn: Callable[..., object],
    *args: Any,
    user: str = "system",
    details: dict[str, Any] | None = None,
    holds_slot: bool | None = None,
    prepare: Callable[[], None] | None = None,
) -> Job:
    """Run fn(job, *args) in the background for `instance` and return the Job.

    `holds_slot`: the job stops or starts the instance, so the instance counts as running (capacity.py) while the job
    runs and until the monitor has observed the instance after the job ended, whatever state it was in when the job
    was asked for. The caller has passed the running-limit check under quotas.LOCK. Default: creations and wakes.

    `prepare` runs once the slot is held and before the job's thread begins (what the job reads and must not see
    before it is certain to run: nothing is saved when the hold is refused). When it raises, nothing starts.

    `details` are facts known before the job runs, shown while it does: a creation's plan is what the instance
    looks like until its files exist (web/v1/resources.py)."""
    job = Job(instance, action, kind, user)
    job.details = dict(details or {})
    job.holds_slot = (kind in ("create", "wake")) if holds_slot is None else holds_slot
    holds = job.holds_slot and not is_panel_job(instance)
    if holds:
        slots.record_hold(job.id, instance, user)  # before the job exists: Unavailable means nothing was started
    try:
        if prepare is not None:
            prepare()
        with _JOBS_LOCK:
            JOBS[job.id] = job
            if holds:
                _HELD[job.id] = job  # outside JOBS: the list's size limit never forgets a slot still held
            while len(JOBS) > _KEEP:
                JOBS.popitem(last=False)
        job.changed()
        if kind == "create" and not is_panel_job(instance):
            events.publish("instance", instance)  # the new instance appears at once, as being created
        threading.Thread(target=_run, args=(job, fn, args), daemon=True).start()
    except BaseException:
        if holds:
            _abandon(job)
        raise
    return job


def _abandon(job: Job) -> None:
    """The job's hold was recorded but its thread never began: nothing ran, so the hold goes. When the record cannot
    be removed the job counts as ended instead, and the usual rule (an observation after its end) releases it."""
    if slots.drop([job.id]):
        with _JOBS_LOCK:
            _HELD.pop(job.id, None)
            JOBS.pop(job.id, None)
        return
    job.status = "failed"
    job.finished = time.time()
    job.finished_mono = time.monotonic()
    _publish_end(job)
    job.changed()  # clients stop showing it as waiting to run


def _run(job: Job, fn: Callable[..., object], args: tuple[Any, ...]) -> None:
    with INSTANCE_LOCKS[job.instance]:
        ACTIVE_JOB[job.instance] = job
        _GENERATIONS[job.instance] += 1
        job.status = "running"
        job.changed()
        if not is_panel_job(job.instance):
            events.publish("instance", job.instance)
        _tell(_START_LISTENERS, job)
        try:
            fn(job, *args)
            for step in job.steps:
                step["status"] = "done"
            job.status = "success"
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the user in the job
            job.mark_failure()
            for step in job.steps:
                if step["status"] == "running":
                    step["status"] = "failed"
            job.status = "failed"
            job.write(f"\nERROR: {exc}\n")
            # a panel job (plugins) refuses in plain words already: show them rather than a general hint (never the
            # technical message of a command that failed)
            plain = (
                isinstance(exc, JobError) and not isinstance(exc, CommandFailed) and is_panel_job(job.instance)
            ) and str(exc)
            job.hint = plain or friendly_hint("".join(job.log))
            try:
                job.failure = failure.describe(job.failed_step(), job.hint, job.failure_log(), job.details.get("tests"))
            except Exception as error:  # noqa: BLE001 - the reason is shown anyway, without details
                job.write(f"Could not pick the error details: {error!r}\n")
                job.failure = {"step": job.failed_step(), "reason": job.hint, "kind": "other", "details": [],
                               "repository": None}  # fmt: skip
        finally:
            job.finished = time.time()
            job.finished_mono = time.monotonic()
            _publish_end(job)
            ACTIVE_JOB.pop(job.instance, None)
            _GENERATIONS[job.instance] += 1  # before the events below: clients that reload see the new state
            job.changed()
            if job.kind == "delete" and job.status == "success":
                events.publish("instance.deleted", job.instance, {"name": job.instance})
            elif job.kind == "create" and job.status == "failed":
                pass  # nothing was left (operations.create announces the removal), or the name was someone else's
            elif not is_panel_job(job.instance):
                events.publish("instance", job.instance)
            _tell(_END_LISTENERS, job)


def _tell(listeners: list[Callable[[Job], None]], job: Job) -> None:
    for listener in list(listeners):
        try:
            listener(job)
        except Exception as exc:  # noqa: BLE001 - a listener (alerts, builds) never changes how the job runs
            print(f"job {job.id} listener: {exc!r}", flush=True)


def on_start(listener: Callable[[Job], None]) -> None:
    """Call `listener(job)` in the job's thread when a job starts running (it holds the instance's lock): the
    build history (projects/builds.py) uses it."""
    if listener not in _START_LISTENERS:
        _START_LISTENERS.append(listener)


def on_end(listener: Callable[[Job], None]) -> None:
    """Call `listener(job)` in the job's thread after every job ends (succeeded or failed): alerts/ uses it."""
    if listener not in _END_LISTENERS:
        _END_LISTENERS.append(listener)


def creating(instance: str) -> Job | None:
    """The job creating `instance` while it is queued or running; None otherwise."""
    with _JOBS_LOCK:
        found = [j for j in JOBS.values() if j.instance == instance and j.kind == "create"]
    return next((j for j in reversed(found) if j.status in ("queued", "running")), None)


def being_created() -> list[str]:
    """Names of the instances whose creation is queued or running."""
    with _JOBS_LOCK:
        return sorted(
            {
                j.instance
                for j in JOBS.values()
                if j.kind == "create" and j.status in ("queued", "running") and not is_panel_job(j.instance)
            }
        )


def pending_creations() -> list[tuple[str, str]]:
    """(instance, user) of each creation that is queued or running."""
    with _JOBS_LOCK:
        return [
            (j.instance, j.user)
            for j in JOBS.values()
            if j.kind == "create" and j.status in ("queued", "running") and not is_panel_job(j.instance)
        ]


def _publish_end(job: Job) -> None:
    """Record the end of a job that holds a slot (core/slots.py). When that fails the hold stays, here and for the
    other processes, and the write is tried again at the next count (`starting`)."""
    if job.id not in _HELD or job.end_seq is not None:
        return
    try:
        job.end_seq = slots.record_end(job.id)
    except slots.Unavailable as exc:
        log.warning("job %s ended but its end could not be recorded yet (%s): its slot stays held", job.id, exc)


def _holding(job: Job, present: set[str], observed: dict[str, int]) -> bool:
    """Whether the instance of this job still counts as running because of it: the job has not ended, or its end is
    not recorded yet, or it ended and no read of the instance that began after the end exists (a read that began
    before the end may show the state from before it). An instance that does not exist any more is not held. An
    instance the monitor cannot see (a cluster that did not answer) stays held: unknown is not stopped.
    `observed`: the numbers this process's monitor took, and those other processes published."""
    if job.finished_mono is None or job.end_seq is None:
        return True
    if job.instance not in present:  # `present`: the instances that exist, read once by the caller
        return False
    return observed.get(job.instance, 0) <= job.end_seq


def _counted(
    held: list[Job], strict: bool = False, snap: slots.Snapshot | None = None
) -> tuple[set[str], set[str], list[str]]:
    """(instances held, those held only for want of a read after the end, ids of released holds), from this process's
    jobs and those the other processes recorded. The instances are read once per call (`snap`: the caller's read).
    `strict`: a limit check is being made, so the shared record must be readable (slots.Unavailable otherwise): a
    count made from this process alone is only for display."""
    from sudoo.instances import metrics, store

    if snap is None:
        snap = slots.snapshot()
    if snap is None and strict:
        raise slots.Unavailable("The panel could not read which instances are running or starting.")
    if not held and (snap is None or not snap.others):
        return set(), set(), []
    present = set(store.names())
    observed = dict(metrics.OBSERVED)
    if snap is not None:
        for name, seen in snap.observed.items():
            observed[name] = max(observed.get(name, 0), int(seen.get("seq", 0)))
    names: set[str] = set()
    waiting: set[str] = set()
    released: list[str] = []
    for job in held:
        if job.finished_mono is not None and job.end_seq is None:
            _publish_end(job)
        if _holding(job, present, observed):
            names.add(job.instance)
            if job.finished_mono is not None:
                waiting.add(job.instance)
        else:
            released.append(job.id)
    if snap is not None:
        for job_id, hold in snap.others.items():
            if hold["instance"] not in present or snap.released(hold):
                if hold.get("end_seq") is not None:
                    released.append(job_id)
                continue
            names.add(hold["instance"])
            if hold.get("end_seq") is not None or hold.get("owner") in snap.unknown_owners:
                waiting.add(hold["instance"])
    return names, waiting, released


def _forget(released: list[str]) -> None:
    if released:
        with _JOBS_LOCK:
            for job_id in released:
                _HELD.pop(job_id, None)
        slots.drop(released)


def starting(strict: bool = False, snap: slots.Snapshot | None = None) -> set[str]:
    """Instances a job holds as running or starting, in this process or another one: a job that holds a slot
    (creations, wakes and every job that stops or starts an instance) while it runs and until the instance is
    observed after it ended, success or failure. `strict`, `snap`: see _counted."""
    with _JOBS_LOCK:
        held = list(_HELD.values())
    names, _, released = _counted(held, strict, snap)
    _forget(released)
    return names


def unsettled() -> list[str]:
    """Instances of jobs that have ended but wait for a read of the instance that began after the end (operators: the
    monitor is behind, stopped or cannot see the instance), or whose owner's lock cannot be tried."""
    with _JOBS_LOCK:
        held = list(_HELD.values())
    _, waiting, _ = _counted(held)
    return sorted(waiting)


def busy(instance: str) -> bool:
    return INSTANCE_LOCKS[instance].locked()


def unfinished(instance: str) -> bool:
    """A job of this panel process for `instance` that is queued or running, or that holds the instance's lock: it
    may read the instance's settings while it runs. Jobs of another process are not seen."""
    with _JOBS_LOCK:
        live = [*JOBS.values(), *_HELD.values()]
    return INSTANCE_LOCKS[instance].locked() or any(
        j.instance == instance and j.status in ("queued", "running") for j in live
    )


def same_request(key: str) -> Job | None:
    """The job of this panel process started by the same request (web/v1/instances.py request_key) that is queued or
    running: a request sent twice (two tabs, a double click, a client that retries) answers with that job."""
    with _JOBS_LOCK:
        live = [*JOBS.values(), *_HELD.values()]
    return next((j for j in live if j.request_key == key and j.status in ("queued", "running")), None)


def generation(instance: str) -> int:
    """Changes whenever a job starts or ends on the instance: what is cached about an instance's code (its commits,
    its submodules) is valid only for one generation (web/v1/resources.py)."""
    return _GENERATIONS[instance]


def recent(instance: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    """Summaries of the most recent jobs, optionally for one instance only."""
    with _JOBS_LOCK:
        jobs = [j for j in reversed(JOBS.values()) if instance is None or j.instance == instance]
    return [j.summary() for j in jobs[:limit]]


def _forget_in_child() -> None:
    """A forked child has none of the parent's threads: the parent's held jobs are not its own, and a lock copied
    while another thread held it would never be released."""
    global _JOBS_LOCK
    _HELD.clear()
    _JOBS_LOCK = threading.Lock()


os.register_at_fork(after_in_child=_forget_in_child)
