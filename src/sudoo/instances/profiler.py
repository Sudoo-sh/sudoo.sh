"""The code profiler: what an instance's Odoo workers spend their time on, recorded for up to a few minutes.

Like the Tools tab of Odoo.sh, it samples every Odoo process of the instance (the main one, its HTTP and cron
workers, the gevent process) without changing or restarting anything, then shows the result as a flame graph.

How a recording is made (one per instance at a time):

    1. The profiler image is ready: PROFILER_IMAGE, or, when it is empty, sudoo-profiler:py-spy-<version> built on
       this server the first time from python:3.12-slim and the pinned py-spy wheel, checked against its sha256.
    2. A one-off sidecar container joins the PID namespace of the instance's Odoo container
       (--pid=container:odoo-<name>-odoo-1) with only the SYS_PTRACE capability, no network, no new privileges and
       a small memory limit. It finds the Odoo main process and runs
       `py-spy record --format speedscope --pid <pid> --subprocesses --duration <seconds> --rate <PROFILER_RATE>`.
       Odoo is only read, never paused for longer than a sample takes.
    3. Stopping early sends SIGINT to py-spy, which then writes what it has.
    4. The speedscope file is copied out of the sidecar (docker cp), the sidecar removed, the command lines of the
       processes (they may contain passwords) are removed from it, and a compact call tree for the flame graph is
       computed once. PROFILER_KEEP recordings are kept per instance in <ODOO_ROOT>/manager/profiles/<instance>/.

Every start, stop, download and deletion is in the audit log. Before a sidecar is started, the Odoo container is
checked to be exactly the one the panel made for the instance (its name and labels, running).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sudoo.config import settings
from sudoo.core import audit
from sudoo.instances import odoo, store

PY_SPY_VERSION = "0.4.3"
PY_SPY_HASHES = (  # manylinux x86_64 and aarch64 wheels of py-spy 0.4.3 on PyPI
    "8a4277225f470d394a85cca0cdd944bef87f0097b6f20150a6dd3ac2e92806b8",
    "bbcec46930d464b3bfc142d6d02ade4dd8ad5627823263b3eff0280e80c7c132",
)
BASE_IMAGE = "python:3.12-slim"
BUILT_IMAGE = f"sudoo-profiler:py-spy-{PY_SPY_VERSION}"
LABEL = "sh.sudoo.profiler"  # the sidecar's label: the instance it records
ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
MAX_NODES = 20_000  # the flame graph's call tree keeps at most this many nodes (the smallest go first)
MIN_SHARE = 1 / 5000  # and leaves out calls that took less than this share of all samples
TOP_FUNCTIONS = 40
SAVE_SECONDS = 120  # what copying and summarising may take after the recording itself

DOCKERFILE = f"""\
FROM {BASE_IMAGE}
RUN printf '%s\\n' 'py-spy=={PY_SPY_VERSION} {" ".join(f"--hash=sha256:{h}" for h in PY_SPY_HASHES)}' \\
        > /tmp/requirements.txt \\
 && pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r /tmp/requirements.txt \\
 && rm /tmp/requirements.txt && py-spy --version
LABEL org.opencontainers.image.title="Sudoo.sh profiler" org.opencontainers.image.description="py-spy {PY_SPY_VERSION}"
"""

# Runs in the sidecar: finds the Odoo main process (the lowest process whose command line runs odoo with Python),
# then becomes py-spy. $1 seconds, $2 samples per second; both checked by the panel.
RECORD_SCRIPT = """\
set -eu
pid=""
for d in /proc/[0-9]*; do
    p=${d#/proc/}
    [ "$p" = "$$" ] && continue
    cmd=$(tr '\\000' ' ' < "$d/cmdline" 2>/dev/null || true)
    case "$cmd" in
        *python*odoo*|*odoo-bin*) if [ -z "$pid" ] || [ "$p" -lt "$pid" ]; then pid=$p; fi ;;
    esac
done
[ -n "$pid" ] || { echo "No Odoo process was found in the container." >&2; exit 3; }
mkdir -p /out
exec py-spy record --format speedscope --output /out/profile.json --pid "$pid" --subprocesses \\
    --duration "$1" --rate "$2"
"""


class ProfilerError(Exception):
    """A recording cannot start or be read: `status` is the HTTP status, `code` a word the web app can show its own
    text for, the message a sentence for people."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class Run:
    """A recording being made."""

    id: str
    instance: str
    user: str
    ip: str | None
    minutes: int
    production: bool
    started: float = field(default_factory=time.time)
    recording_since: float | None = None
    phase: str = "preparing"  # preparing (image, checks) -> recording -> saving
    stop_requested: bool = False
    container: str = ""

    def resource(self) -> dict[str, Any]:
        ends = (self.recording_since or self.started) + self.minutes * 60
        return {
            "id": self.id,
            "phase": self.phase,
            "minutes": self.minutes,
            "user": self.user,
            "started_at": round(self.started),
            "recording_since": round(self.recording_since) if self.recording_since else None,
            "ends_at": round(ends),
            "stopping": self.stop_requested,
            "production": self.production,
        }


RUNS: dict[str, Run] = {}
LAST_ERROR: dict[str, dict[str, Any]] = {}  # instance -> {"message", "at"} of its last failed recording
_lock = threading.Lock()
_image_lock = threading.Lock()


# --------------------------------------------------------------------------- docker


def docker(args: list[str], timeout: float = 60, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the docker command line (replaced by the tests)."""
    return subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True, timeout=timeout)


def image() -> str:
    """The image recordings run in: PROFILER_IMAGE, or the one built here."""
    return settings.profiler_image or BUILT_IMAGE


def ensure_image() -> str:
    """Make sure the profiler image is on this server: pulled (PROFILER_IMAGE) or built (the default). Returns it."""
    wanted = image()
    with _image_lock:
        if docker(["image", "inspect", "--format", "{{.Id}}", wanted]).returncode == 0:
            return wanted
        if settings.profiler_image:
            result = docker(["pull", "--", wanted], timeout=600)
            what = f"The profiler image {wanted} could not be pulled"
        else:
            result = docker(["build", "--quiet", "--tag", wanted, "-"], timeout=900, stdin=DOCKERFILE)
            what = "The profiler image could not be built (it needs python:3.12-slim and py-spy from PyPI)"
        if result.returncode != 0:
            raise ProfilerError(502, "image", f"{what}: {_last_line(result.stderr)}")
    return wanted


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "no message")[:300]


def sidecar_name(name: str) -> str:
    return f"sudoo-profiler-{name}"


def check_container(name: str) -> str:
    """The instance's Odoo container, when it is running and exactly the one the panel made; else ProfilerError."""
    target = store.container(name, "odoo")
    result = docker(["container", "inspect", "--", target])
    if result.returncode != 0:
        raise ProfilerError(409, "not-running", "The instance is not running: start it, then record a profile.")
    try:
        found = json.loads(result.stdout)[0]
    except (ValueError, IndexError, KeyError):
        raise ProfilerError(502, "docker", "Docker did not describe the instance's container.") from None
    labels = (found.get("Config") or {}).get("Labels") or {}
    host = found.get("HostConfig") or {}
    ours = (
        found.get("Name") == f"/{target}"
        and labels.get(odoo.LABEL_MANAGED) == "true"
        and labels.get(odoo.LABEL_INSTANCE) == name
        and labels.get(odoo.LABEL_SERVICE) == "odoo"
    )
    if not ours:
        raise ProfilerError(409, "not-ours", "This container is not one the panel made for this instance.")
    if not (found.get("State") or {}).get("Running"):
        raise ProfilerError(409, "not-running", "The instance is not running: start it, then record a profile.")
    if host.get("Privileged") or host.get("PidMode") == "host":
        raise ProfilerError(409, "exposed", "This container was changed to reach the server's processes: restore its "
                            "configuration first.")  # fmt: skip
    return target


def record_command(name: str, target: str, seconds: int) -> list[str]:
    """The `docker run` of the sidecar that records `target` (the instance's Odoo container) for `seconds`."""
    return [
        "run", "--detach",
        "--name", sidecar_name(name),
        "--label", f"{LABEL}={name}",
        f"--pid=container:{target}",
        "--cap-drop", "ALL", "--cap-add", "SYS_PTRACE",
        "--security-opt", "no-new-privileges",
        "--network", "none",
        "--memory", "256m", "--pids-limit", "64", "--cpus", "1",
        "--entrypoint", "sh",
        image(), "-c", RECORD_SCRIPT, "sudoo-profiler", str(seconds), str(int(settings.profiler_rate)),
    ]  # fmt: skip


# --------------------------------------------------------------------------- stored recordings


def folder(name: str) -> Path:
    return settings.manager_dir / "profiles" / name


def _paths(name: str, profile_id: str) -> tuple[Path, Path, Path]:
    if not ID_RE.match(profile_id or ""):
        raise ProfilerError(404, "missing", "This recording does not exist.")
    base = folder(name)
    return base / f"{profile_id}.meta.json", base / f"{profile_id}.speedscope.json", base / f"{profile_id}.tree.json"


def recordings(name: str) -> list[dict[str, Any]]:
    """The instance's recordings, newest first."""
    found = []
    for path in folder(name).glob("*.meta.json") if folder(name).is_dir() else ():
        try:
            found.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda m: (float(m.get("created") or 0), str(m.get("id", ""))), reverse=True)


def recording(name: str, profile_id: str) -> dict[str, Any]:
    meta, _, _ = _paths(name, profile_id)
    try:
        return dict(json.loads(meta.read_text()))
    except (OSError, ValueError):
        raise ProfilerError(404, "missing", "This recording does not exist.") from None


def tree(name: str, profile_id: str) -> dict[str, Any]:
    recording(name, profile_id)
    _, _, tree_file = _paths(name, profile_id)
    try:
        return dict(json.loads(tree_file.read_text()))
    except (OSError, ValueError):
        raise ProfilerError(404, "missing", "This recording has no flame graph.") from None


def raw_file(name: str, profile_id: str) -> Path:
    recording(name, profile_id)
    _, raw, _ = _paths(name, profile_id)
    if not raw.is_file():
        raise ProfilerError(404, "missing", "This recording's file is gone.")
    return raw


def delete(name: str, profile_id: str) -> None:
    recording(name, profile_id)
    for path in _paths(name, profile_id):
        path.unlink(missing_ok=True)


def forget_instance(name: str) -> None:
    """Remove every recording of an instance (it was deleted)."""
    if store.NAME_RE.match(name):
        shutil.rmtree(folder(name), ignore_errors=True)


def _prune(name: str) -> None:
    for old in recordings(name)[max(1, int(settings.profiler_keep)) :]:
        for path in _paths(name, str(old["id"])):
            path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- the flame graph


_PROCESS_RE = re.compile(r"^process (\d+):")
_LIBRARY_RE = re.compile(r"^/usr/(?:local/)?lib/python3(?:\.\d+)?/(?:(?:site|dist)-packages/)?")  # odoo/http.py


def _frame_label(frame: dict[str, Any]) -> tuple[str, str]:
    """(name, where) of a speedscope frame; a process frame keeps only its number and whether it is gevent."""
    name = str(frame.get("name", "?"))
    found = _PROCESS_RE.match(name)
    if found:
        role = " gevent" if " gevent" in name else ""
        return f"process {found.group(1)} (odoo{role})", ""
    where = str(frame.get("file") or "")
    where = _LIBRARY_RE.sub("", where)
    line = frame.get("line")
    return name, f"{where}:{line}" if where and line else where


def sanitize(data: dict[str, Any]) -> dict[str, Any]:
    """The speedscope document without the processes' command lines (they may hold passwords) and named after the
    instance, not the command."""
    for frame in (data.get("shared") or {}).get("frames") or []:
        if isinstance(frame, dict) and _PROCESS_RE.match(str(frame.get("name", ""))):
            frame["name"] = _frame_label(frame)[0]
    data["name"] = "Odoo workers"
    return data


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    """The call tree the web app draws: {"total", "seconds", "threads": [{"name", "samples"}], "root": node,
    "top": [{"name", "where", "self", "total"}]}; a node is {"n" name, "w" where, "v" samples, "c" children}. Calls
    of every thread are merged under one root; the first level below it is the thread."""
    frames = [_frame_label(f) if isinstance(f, dict) else ("?", "") for f in (data.get("shared") or {}).get("frames")
              or []]  # fmt: skip
    root: dict[str, Any] = {"n": "all", "w": "", "v": 0, "c": {}}
    threads: list[dict[str, Any]] = []
    self_time: dict[tuple[str, str], int] = {}
    total_time: dict[tuple[str, str], int] = {}
    seconds = 0.0
    for profile in data.get("profiles") or []:
        samples = profile.get("samples") or []
        if not samples:
            continue
        thread = _thread_name(str(profile.get("name", "")))
        threads.append({"name": thread, "samples": len(samples)})
        seconds = max(seconds, float(profile.get("endValue") or 0) - float(profile.get("startValue") or 0))
        top = root["c"].setdefault(("thread", thread), {"n": thread, "w": "", "v": 0, "c": {}})
        for stack in samples:
            root["v"] += 1
            top["v"] += 1
            node = top
            seen: set[tuple[str, str]] = set()
            labels = [frames[i] for i in stack if isinstance(i, int) and 0 <= i < len(frames)]
            for label in labels:
                if label[0].startswith("process "):
                    continue  # the process is already in the thread's name
                node = node["c"].setdefault(label, {"n": label[0], "w": label[1], "v": 0, "c": {}})
                node["v"] += 1
                if label not in seen:
                    total_time[label] = total_time.get(label, 0) + 1
                    seen.add(label)
            if labels and not labels[-1][0].startswith("process "):
                self_time[labels[-1]] = self_time.get(labels[-1], 0) + 1
    total = root["v"]
    floor = max(1, int(total * MIN_SHARE))
    if total:  # raise the floor until the tree fits in MAX_NODES
        while _count(root, floor) > MAX_NODES:
            floor *= 2
    top_functions = sorted(self_time.items(), key=lambda item: item[1], reverse=True)[:TOP_FUNCTIONS]
    return {
        "total": total,
        "seconds": round(seconds, 2),
        "threads": sorted(threads, key=lambda t: t["samples"], reverse=True),
        "root": _pruned(root, floor),
        "top": [{"name": k[0], "where": k[1], "self": v, "total": total_time.get(k, v)} for k, v in top_functions],
    }


def _thread_name(name: str) -> str:
    """'Process 17 Thread 19 "Worker WorkerHTTP (17) workthread"' -> 'Worker WorkerHTTP (17) workthread';
    'Process 18 Thread 18 "MainThread"' -> 'MainThread (process 18)' (every process has one)."""
    found = re.match(r'^Process (\d+) Thread (\d+) "(.*)"$', name)
    if not found:
        return name[:80] or "thread"
    process, thread, label = found.groups()
    label = label or f"Thread {thread}"
    return (label if f"({process})" in label else f"{label} (process {process})")[:80]


def _count(node: dict[str, Any], floor: int) -> int:
    return 1 + sum(_count(child, floor) for child in node["c"].values() if child["v"] >= floor)


def _pruned(node: dict[str, Any], floor: int) -> dict[str, Any]:
    children = sorted((c for c in node["c"].values() if c["v"] >= floor), key=lambda c: c["v"], reverse=True)
    out: dict[str, Any] = {"n": node["n"], "v": node["v"]}
    if node["w"]:
        out["w"] = node["w"]
    if children:
        out["c"] = [_pruned(child, floor) for child in children]
    return out


# --------------------------------------------------------------------------- recording


def current(name: str) -> Run | None:
    return RUNS.get(name)


def start(
    name: str,
    user: str,
    ip: str | None,
    minutes: int,
    production: bool,
    run_async: Callable[[Callable[[], None]], None] | None = None,
) -> Run:
    """Start recording instance `name` for `minutes`; the work happens in a thread. ProfilerError when it cannot."""
    if not settings.profiler_enabled:
        raise ProfilerError(403, "disabled", "The profiler is switched off on this panel.")
    if not 1 <= minutes <= int(settings.profiler_max_minutes):
        raise ProfilerError(400, "minutes", f"Choose from 1 to {settings.profiler_max_minutes} minutes.")
    with _lock:
        if name in RUNS:
            raise ProfilerError(
                409, "running", "A profile of this instance is being recorded. Wait for it, or stop it."
            )
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        run = Run(f"{stamp}-{secrets.token_hex(3)}", name, user, ip, minutes, production)
        RUNS[name] = run
    LAST_ERROR.pop(name, None)
    (run_async or _thread)(lambda: _work(run))
    return run


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="profiler", daemon=True).start()


def stop(name: str) -> Run | None:
    """Ask the recording of `name` to stop now; py-spy then writes what it has. None when nothing is recorded."""
    run = RUNS.get(name)
    if run is None:
        return None
    run.stop_requested = True
    if run.phase == "recording":
        docker(["kill", "--signal", "INT", sidecar_name(name)], timeout=20)
    return run


def _work(run: Run) -> None:
    name = run.instance
    try:
        ensure_image()
        if run.stop_requested:
            return
        target = check_container(name)
        docker(["rm", "--force", sidecar_name(name)], timeout=30)  # a sidecar left by a panel restart
        started = docker(record_command(name, target, run.minutes * 60), timeout=60)
        if started.returncode != 0:
            raise ProfilerError(502, "docker", f"The profiler could not start: {_last_line(started.stderr)}")
        run.container = sidecar_name(name)
        run.recording_since = time.time()
        run.phase = "recording"
        if run.stop_requested:  # asked while it was starting
            docker(["kill", "--signal", "INT", run.container], timeout=20)
        waited = docker(["wait", run.container], timeout=run.minutes * 60 + SAVE_SECONDS)
        run.phase = "saving"
        code = (waited.stdout or "").strip()
        output = docker(["logs", "--tail", "20", run.container], timeout=20)
        if waited.returncode != 0 or code not in ("0", "130"):  # 130: ended by SIGINT before writing
            message = _last_line(output.stderr or output.stdout)
            if "Permission denied" in message or "Operation not permitted" in message:
                message = ("The server does not allow reading the Odoo processes (ptrace). Check that Docker grants "
                           f"SYS_PTRACE to containers. ({message})")  # fmt: skip
            raise ProfilerError(502, "failed", f"py-spy stopped with an error: {message}")
        _save(run)
    except ProfilerError as error:
        LAST_ERROR[name] = {"message": str(error), "code": error.code, "at": round(time.time())}
        audit.record(run.user, run.ip, "profile failed", instance=name, ok=False, detail=str(error))
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        LAST_ERROR[name] = {"message": f"The recording failed ({error.__class__.__name__}).", "code": "failed",
                            "at": round(time.time())}  # fmt: skip
        audit.record(run.user, run.ip, "profile failed", instance=name, ok=False, detail=repr(error))
    finally:
        if run.container:
            docker(["rm", "--force", run.container], timeout=30)
        with _lock:
            RUNS.pop(name, None)


def _save(run: Run) -> None:
    name = run.instance
    base = folder(name)
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    meta_file, raw_path, tree_file = _paths(name, run.id)
    partial = base / f"{run.id}.part"
    try:
        copied = docker(["cp", f"{run.container}:/out/profile.json", str(partial)], timeout=SAVE_SECONDS)
        if copied.returncode != 0 or not partial.is_file():
            raise ProfilerError(502, "empty", "py-spy wrote no profile: the Odoo processes may have ended.")
        data = sanitize(json.loads(partial.read_text()))
    finally:
        partial.unlink(missing_ok=True)
    summary = summarize(data)
    _write(raw_path, json.dumps(data, separators=(",", ":")))
    _write(tree_file, json.dumps(summary, separators=(",", ":")))
    ended = time.time()
    meta = {
        "id": run.id,
        "instance": name,
        "user": run.user,
        "started_at": round(run.recording_since or run.started),
        "ended_at": round(ended),
        "minutes": run.minutes,
        "seconds": round(ended - (run.recording_since or run.started), 1),  # how long it recorded (wall clock)
        "samples": summary["total"],
        "threads": len(summary["threads"]),
        "rate": int(settings.profiler_rate),
        "stopped_early": run.stop_requested,
        "production": run.production,
        "created": time.time(),
        "size": raw_path.stat().st_size,
    }
    _write(meta_file, json.dumps(meta))
    _prune(name)
    audit.record(run.user, run.ip, "profile recorded", instance=name,
                 detail=f"{run.id}: {meta['seconds']} s, {meta['samples']} samples")  # fmt: skip


def _write(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(text)
    temporary.replace(path)
