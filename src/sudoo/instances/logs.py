"""An instance's logs, as the Logs tab lists, follows, filters and downloads them.

    odoo.log      what Odoo's container writes (docker logs: standard output and error, merged)
    install.log   the technical log of the instance's latest creation job (cloning, requirements, installing)
    update.log    the technical log of its latest update job (new code, pip requirements, module updates)
    upgrade.log   the technical log of its latest "Upgrade modules" job

The panel keeps no separate pip log: requirements are installed inside the creation and update jobs, so their output
is in install.log and update.log. Job logs live in the panel's memory (core/jobs.py) and are listed while kept.

A filter is a plain piece of text, compared without regard to case: never a regular expression, so a search cannot
be made to take long.
"""

from __future__ import annotations

import collections
import subprocess
from collections.abc import Callable, Iterator

from sudoo.core import jobs
from sudoo.instances import store

JOB_LOGS = {"install.log": ("create",), "update.log": ("update",), "upgrade.log": ("upgrade",)}
"""Log name -> the kinds of job whose latest log it is."""
ODOO_LOG = "odoo.log"
NAMES = (ODOO_LOG, *JOB_LOGS)
MAX_QUERY = 200
"""Longest filter text accepted."""


def latest_job(name: str, log: str) -> jobs.Job | None:
    """The newest job of instance `name` whose log is `log` (install.log, update.log, upgrade.log)."""
    kinds = JOB_LOGS.get(log, ())
    with jobs._JOBS_LOCK:
        found = [j for j in jobs.JOBS.values() if j.instance == name and j.kind in kinds]
    return found[-1] if found else None


def available(name: str) -> list[dict[str, object]]:
    """The logs of instance `name` that can be read now: [{"name", "job", "status", "started_at"}]; odoo.log first
    and always (its container may be stopped: it is then what Docker kept of the last run)."""
    found: list[dict[str, object]] = [{"name": ODOO_LOG, "job": None, "status": None, "started_at": None}]
    for log in JOB_LOGS:
        job = latest_job(name, log)
        if job is not None:
            found.append({"name": log, "job": job.id, "status": job.status, "started_at": job.started})
    return found


def matcher(query: str) -> Callable[[str], bool]:
    """Whether a line contains `query`, ignoring case (everything matches an empty query)."""
    wanted = query.strip().casefold()
    if not wanted:
        return lambda _line: True
    return lambda line: wanted in line.casefold()


def odoo_log_command(name: str, tail: int, follow: bool = False, since: str | None = None) -> list[str]:
    """docker logs of the instance's Odoo container: the last `tail` lines (all when `since` is given: those after
    that time), each with its time stamp (RFC 3339; streams resume from it), then new ones when `follow`."""
    cmd = ["docker", "logs", "--timestamps"]
    if since:
        cmd += ["--since", since]
    else:
        cmd += ["--tail", str(tail)]
    if follow:
        cmd.append("--follow")
    return [*cmd, store.container(name, "odoo")]


def split_stamp(line: str) -> tuple[str, str]:
    """(time stamp, text) of a line of `docker logs --timestamps`; ("", line) when it has none."""
    stamp, _, text = line.partition(" ")
    if len(stamp) >= 20 and stamp[:4].isdigit() and stamp[4] == "-" and "T" in stamp:
        return stamp, text
    return "", line


def tail(name: str, log: str, lines: int, query: str = "") -> Iterator[str]:
    """The lines among the last `lines` of `log` that contain `query`, each ending in a newline. odoo.log is read as
    it comes from Docker, so a large download never holds more than `lines` lines in the panel's memory."""
    keep = matcher(query)
    if log == ODOO_LOG:
        found: collections.deque[str] = collections.deque(maxlen=lines)
        try:
            with subprocess.Popen(
                odoo_log_command(name, lines),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            ) as proc:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    text = split_stamp(raw.rstrip("\n"))[1]
                    if keep(text):
                        found.append(text + "\n")
        except OSError:
            return iter(())
        return iter(found)
    job = latest_job(name, log)
    if job is None:
        return iter(())
    text = "".join(job.log[:])
    chosen = [line + "\n" for line in text.splitlines() if keep(line)]
    return iter(chosen[-lines:])
