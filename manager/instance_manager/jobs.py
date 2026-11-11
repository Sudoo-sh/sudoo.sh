"""Background actions ("jobs") such as creating, restarting or updating an instance.

Every action runs in its own thread. Actions on the same instance run one after another (a lock per instance);
actions on different instances run in parallel. A job records user-facing steps, notes and the full technical
log, which the panel shows while it runs and afterwards (the last 300 jobs are kept in memory).
"""
import subprocess
import threading
import time
import uuid
from collections import OrderedDict, defaultdict

from .hints import friendly_hint

JOBS = OrderedDict()
_JOBS_LOCK = threading.Lock()
INSTANCE_LOCKS = defaultdict(threading.Lock)
ACTIVE_JOB = {}           # instance name -> the job currently running on it
_KEEP = 300


class JobError(Exception):
    """A step failed; the message is shown to the user."""


class Job:
    def __init__(self, instance, action, kind, user):
        self.id = uuid.uuid4().hex[:12]
        self.instance = instance
        self.action = action        # title shown to the user, e.g. "Restart"
        self.kind = kind            # machine name, e.g. "restart"
        self.user = user            # account name, or "visitor" / "system" for automatic jobs
        self.status = "queued"
        self.steps = []
        self.notes = []
        self.log = []
        self.result = {}
        self.hint = None
        self.started = time.time()
        self.finished = None

    def write(self, text):
        self.log.append(text)

    def step(self, title):
        """Start a new user-facing step; the previous one is marked done."""
        for step in self.steps:
            if step["status"] == "running":
                step["status"] = "done"
        self.steps.append({"title": title, "status": "running"})
        self.write(f"\n== {title}\n")

    def note(self, text, level="info"):
        """A message for the user that does not stop the job ("info" or "warning")."""
        self.notes.append({"text": text, "level": level})
        self.write(f"[{level}] {text}\n")

    def run(self, cmd, timeout=None, input=None):
        """Run a command, stream its output into the log, raise JobError when it fails."""
        if self.run_unchecked(cmd, timeout, input) != 0:
            raise JobError(f"command failed: {' '.join(cmd[:4])} ...")

    def run_unchecked(self, cmd, timeout=None, input=None):
        """Run a command, stream its output into the log, return its exit code."""
        self.write(f"$ {' '.join(cmd)}\n")
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if input else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        if input:
            proc.stdin.write(input)
            proc.stdin.close()
        deadline = time.time() + timeout if timeout else None
        for line in proc.stdout:
            self.write(line)
            if deadline and time.time() > deadline:
                proc.kill()
                raise JobError("this step took too long and was stopped")
        return proc.wait()

    def summary(self):
        return {"id": self.id, "instance": self.instance, "action": self.action, "kind": self.kind,
                "user": self.user, "status": self.status, "started": self.started, "finished": self.finished,
                "steps": self.steps}


def start(instance, action, kind, fn, *args, user="system"):
    """Run fn(job, *args) in the background for `instance` and return the Job."""
    job = Job(instance, action, kind, user)
    with _JOBS_LOCK:
        JOBS[job.id] = job
        while len(JOBS) > _KEEP:
            JOBS.popitem(last=False)
    threading.Thread(target=_run, args=(job, fn, args), daemon=True).start()
    return job


def _run(job, fn, args):
    with INSTANCE_LOCKS[job.instance]:
        ACTIVE_JOB[job.instance] = job
        job.status = "running"
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
            job.hint = friendly_hint("".join(job.log))
        finally:
            job.finished = time.time()
            ACTIVE_JOB.pop(job.instance, None)


def busy(instance):
    return INSTANCE_LOCKS[instance].locked()


def recent(instance=None, limit=30):
    """Summaries of the most recent jobs, optionally for one instance only."""
    with _JOBS_LOCK:
        jobs = [j for j in reversed(JOBS.values()) if instance is None or j.instance == instance]
    return [j.summary() for j in jobs[:limit]]
