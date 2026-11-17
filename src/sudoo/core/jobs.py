"""Background actions ("jobs") such as creating, restarting or updating an instance.

Every action runs in its own thread. Actions on the same instance run one after another (a lock per instance);
actions on different instances run in parallel. A job records user-facing steps, notes and the full technical
log, which the panel shows while it runs and afterwards (the last 300 jobs are kept in memory).

Every change of a job is published as a live event (core/events.py): "job" when it starts, moves to another step,
adds a note or ends, and "job.output" with new log text at most twice a second.
"""

from __future__ import annotations

import contextlib
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from typing import Any

from sudoo.core import events
from sudoo.core.hints import friendly_hint
from sudoo.core.shell import redact

JOBS: OrderedDict[str, Job] = OrderedDict()
_JOBS_LOCK = threading.Lock()
INSTANCE_LOCKS: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
ACTIVE_JOB: dict[str, Job] = {}  # instance name -> the job currently running on it
_GENERATIONS: defaultdict[str, int] = defaultdict(int)  # instance name -> jobs started or ended on it
_KEEP = 300
_END_LISTENERS: list[Callable[[Job], None]] = []
_OUTPUT_EVERY_SECONDS = 0.5


class JobError(Exception):
    """A step failed; the message is shown to the user."""


class Job:
    def __init__(self, instance: str, action: str, kind: str, user: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.instance = instance
        self.action = action  # title shown to the user, e.g. "Restart"
        self.kind = kind  # machine name, e.g. "restart"
        self.user = user  # account name, or "visitor" / "system" for automatic jobs
        self.status = "queued"
        self.steps: list[dict[str, str]] = []
        self.notes: list[dict[str, str]] = []
        self.log: list[str] = []
        self.result: dict[str, Any] = {}
        self.hint: str | None = None
        self.started = time.time()
        self.finished: float | None = None
        self._published_lines = 0  # log lines already sent as "job.output" events
        self.expected_steps: int | None = None  # set by operations that know how many steps they take
        self.details: dict[str, Any] = {}  # what the caller knows in advance (a creation's plan, without secrets)

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

    def publish_output(self) -> None:
        """Send the log lines written since the last call as one "job.output" event."""
        end = len(self.log)
        if end > self._published_lines:
            text = "".join(self.log[self._published_lines : end])
            events.publish(
                "job.output",
                self.id,
                {"job": self.id, "offset": self._published_lines, "next_offset": end, "text": text},
            )
            self._published_lines = end

    def write(self, text: str) -> None:
        """Add `text` to the log, with the user name and password of any address in it masked."""
        self.log.append(redact(text))

    def step(self, title: str) -> None:
        """Start a new user-facing step; the previous one is marked done."""
        for step in self.steps:
            if step["status"] == "running":
                step["status"] = "done"
        self.steps.append({"title": title, "status": "running"})
        self.write(f"\n== {title}\n")
        self.changed()

    def note(self, text: str, level: str = "info") -> None:
        """A message for the user that does not stop the job ("info" or "warning")."""
        self.notes.append({"text": text, "level": level})
        self.write(f"[{level}] {text}\n")
        self.changed()

    def run(self, cmd: list[str], timeout: float | None = None, input: str | None = None) -> None:
        """Run a command, stream its output into the log, raise JobError when it fails."""
        if self.run_unchecked(cmd, timeout, input) != 0:
            raise JobError(f"command failed: {' '.join(redact(a) for a in cmd[:4])} ...")

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
        try:  # the output is closed; the command itself may still be running
            return proc.wait(timeout=None if deadline is None else max(0.05, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _stop(proc, container)
            raise JobError("this step took too long and was stopped") from None

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
) -> Job:
    """Run fn(job, *args) in the background for `instance` and return the Job.

    `details` are facts known before the job runs, shown while it does: a creation's plan is what the instance
    looks like until its files exist (web/v1/resources.py)."""
    job = Job(instance, action, kind, user)
    job.details = dict(details or {})
    with _JOBS_LOCK:
        JOBS[job.id] = job
        while len(JOBS) > _KEEP:
            JOBS.popitem(last=False)
    _start_output_publisher()
    job.changed()
    if kind == "create" and not is_panel_job(instance):
        events.publish("instance", instance)  # the new instance appears at once, as being created
    threading.Thread(target=_run, args=(job, fn, args), daemon=True).start()
    return job


def _run(job: Job, fn: Callable[..., object], args: tuple[Any, ...]) -> None:
    with INSTANCE_LOCKS[job.instance]:
        ACTIVE_JOB[job.instance] = job
        _GENERATIONS[job.instance] += 1
        job.status = "running"
        job.changed()
        if not is_panel_job(job.instance):
            events.publish("instance", job.instance)
        try:
            fn(job, *args)
            for step in job.steps:
                step["status"] = "done"
            job.status = "success"
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the user in the job
            for step in job.steps:
                if step["status"] == "running":
                    step["status"] = "failed"
            job.status = "failed"
            job.write(f"\nERROR: {exc}\n")
            # a panel job (plugins) refuses in plain words already: show them rather than a general hint
            plain = isinstance(exc, JobError) and is_panel_job(job.instance) and str(exc)
            job.hint = plain or friendly_hint("".join(job.log))
        finally:
            job.finished = time.time()
            ACTIVE_JOB.pop(job.instance, None)
            _GENERATIONS[job.instance] += 1  # before the events below: clients that reload see the new state
            job.publish_output()
            job.changed()
            if job.kind == "delete" and job.status == "success":
                events.publish("instance.deleted", job.instance, {"name": job.instance})
            elif job.kind == "create" and job.status == "failed":
                pass  # nothing was left (operations.create announces the removal), or the name was someone else's
            elif not is_panel_job(job.instance):
                events.publish("instance", job.instance)
            for listener in list(_END_LISTENERS):
                try:
                    listener(job)
                except Exception as exc:  # noqa: BLE001 - a listener (alerts) never changes how the job ended
                    print(f"job {job.id} listener: {exc!r}", flush=True)


def on_end(listener: Callable[[Job], None]) -> None:
    """Call `listener(job)` in the job's thread after every job ends (succeeded or failed): alerts/ uses it."""
    if listener not in _END_LISTENERS:
        _END_LISTENERS.append(listener)


_publisher_started = threading.Event()


def _start_output_publisher() -> None:
    """Once: a thread that sends the new log text of running jobs as live events."""
    if _publisher_started.is_set():
        return
    _publisher_started.set()

    def publish_forever() -> None:
        while True:
            time.sleep(_OUTPUT_EVERY_SECONDS)
            for job in list(ACTIVE_JOB.values()):
                job.publish_output()

    threading.Thread(target=publish_forever, daemon=True, name="job-output").start()


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


def starting() -> set[str]:
    """Instances a queued or running job is creating or starting (they will run once it ends)."""
    with _JOBS_LOCK:
        return {
            j.instance
            for j in JOBS.values()
            if j.kind in ("create", "wake") and j.status in ("queued", "running") and not is_panel_job(j.instance)
        }


def busy(instance: str) -> bool:
    return INSTANCE_LOCKS[instance].locked()


def generation(instance: str) -> int:
    """Changes whenever a job starts or ends on the instance: what is cached about an instance's code (its commits,
    its submodules) is valid only for one generation (web/v1/resources.py)."""
    return _GENERATIONS[instance]


def recent(instance: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    """Summaries of the most recent jobs, optionally for one instance only."""
    with _JOBS_LOCK:
        jobs = [j for j in reversed(JOBS.values()) if instance is None or j.instance == instance]
    return [j.summary() for j in jobs[:limit]]
