"""An instance's logs, as the Logs tab lists, follows, filters, pages through and downloads them.

    odoo.log      what Odoo's container writes (docker logs: standard output and error, merged)
    install.log   what Odoo wrote while it installed modules (a creation, a rebuild, Install modules)
    update.log    what Odoo wrote while it updated modules (Get latest code, Upgrade modules, a test run)

install.log and update.log are cut out of the jobs' technical logs, which are server logs and are never shown
themselves: only the output of Odoo's own `--stop-after-init` runs is kept, and of it only the lines in Odoo's log
format and the tracebacks that belong to them (every command, path, step title and note the panel writes is left
out; the text goes through the same redaction as the server's log). They exist while the panel keeps those jobs in
memory (the last 300 jobs, until the panel restarts).

A log is read as entries: an entry is a line in Odoo's format and the lines after it that are not (a traceback, a
long message), so a filter keeps or drops a traceback with its line. Filters: levels, a logger (and the loggers under
it), a time range, and a text, plain (any case) or a regular expression. A regular expression is compiled by the
`regex` package and run with a time limit on each entry and on the whole request (SlowPattern), and refused at once
when it already takes too long on a few short probe texts, so a pattern can never hold the panel's threads.

Each entry has an id that orders entries: for odoo.log the time stamp Docker gave its first line (RFC 3339 with
nine decimals), for install.log and update.log the start of the job and the line's position in it. Pages go
backwards from an id (`before`), forwards from one (`after`) or around one (`around`, for a link to one line).
"""

from __future__ import annotations

import collections
import contextlib
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import regex

from sudoo.core.shell import redact
from sudoo.instances import store

ODOO_LOG = "odoo.log"
INSTALL_LOG = "install.log"
UPDATE_LOG = "update.log"
NAMES = (ODOO_LOG, INSTALL_LOG, UPDATE_LOG)
JOB_LOGS = (INSTALL_LOG, UPDATE_LOG)
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
MAX_QUERY = 200
"""Longest filter text (or pattern) accepted."""
MAX_LOGGER = 120
PAGE_DEFAULT = 500
PAGE_MAX = 2000
MATCH_CHARS = 4096
"""Only the first characters of an entry are searched (a megabyte line cannot slow a search down)."""
PATTERN_ENTRY_SECONDS = 0.02
PATTERN_REQUEST_SECONDS = 2.0
MAX_SCAN_BYTES = 64 * 2**20
"""A page reads at most this much of a log (Docker's rotated files are far smaller by default)."""
JOB_LOG_LINES = 20000
"""Lines of install.log or update.log kept (the latest)."""

_ODOO_LINE = re.compile(
    r"^(?P<time>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:,\d+)?) +(?P<pid>\d+) +"
    r"(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|TEST) +(?P<db>\S+) +(?P<logger>[\w.\-]+): ?(?P<message>.*)$"
)
_STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$")
_TIME_ARG = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d{1,9})?)?(?:Z|[+-]\d\d:\d\d)$")
_CURSOR = re.compile(r"^[0-9TZ:.+-]{1,40}$")
_LOGGER = re.compile(r"^[\w.\-]{1,120}$")
# a traceback, as Python prints it: the only lines kept from a job's output that are not in Odoo's format
_TRACE_START = "Traceback (most recent call last):"
_TRACE_END = re.compile(r"^[A-Za-z_][\w.]*(: .*)?$")  # the exception that ends it: "psycopg2.errors.X: text"
_TRACE_CHAINED = ("During handling of the above exception", "The above exception was the direct cause")
_PROBES = ("a" * 2000 + "!", "x" * 2000, " " * 1000 + "!", "aA0_ -.:/=" * 200)


class SlowPattern(Exception):
    """A regular expression took too long on this log."""


@dataclass
class Entry:
    """One entry of a log: its id, the time stamp Docker gave it ("" for job logs) and its lines."""

    id: str
    lines: list[str]
    head: dict[str, str] | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def level(self) -> str | None:
        if self.head is None:
            return None
        level = self.head["level"]
        return {"WARN": "WARNING", "TEST": "INFO"}.get(level, level)

    @property
    def logger(self) -> str | None:
        return self.head["logger"] if self.head else None

    @property
    def traceback(self) -> bool:
        return any(line.startswith("Traceback (most recent call last)") for line in self.lines[1:])

    def time(self) -> datetime | None:
        """When Odoo wrote it (Odoo's own time stamp, UTC in its container), or Docker's time stamp."""
        if self.head:
            with contextlib.suppress(ValueError):
                return datetime.strptime(self.head["time"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        return parse_time(self.id) if _STAMP.match(self.id) else None

    def as_dict(self) -> dict[str, Any]:
        head = self.head or {}
        return {
            "id": self.id,
            "time": head.get("time"),
            "level": self.level,
            "pid": int(head["pid"]) if head.get("pid") else None,
            "database": head.get("db"),
            "logger": head.get("logger"),
            "traceback": self.traceback,
            "text": self.text,
        }


def parse_line(text: str) -> dict[str, str] | None:
    """The parts of a line in Odoo's log format (time, pid, level, db, logger, message), or None."""
    found = _ODOO_LINE.match(text)
    return found.groupdict() if found else None


def normal_stamp(stamp: str) -> str:
    """A time stamp of `docker logs --timestamps` with nine decimals, so that ids compare as text; "" when it is not
    one."""
    found = _STAMP.match(stamp)
    if not found:
        return ""
    whole, fraction, zone = found.groups()
    return f"{whole}.{(fraction or '').ljust(9, '0')}{zone}"


def parse_time(value: str) -> datetime | None:
    """An ISO 8601 / RFC 3339 time with a zone (the filters' since and until), as a UTC datetime."""
    if not _TIME_ARG.match(value):
        return None
    head, _, rest = value.partition(".")
    if rest:  # Python takes six decimals at most
        digits = re.match(r"\d+", rest)
        assert digits is not None
        value = f"{head}.{digits.group()[:6].ljust(6, '0')}{rest[digits.end():]}"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def split_stamp(line: str) -> tuple[str, str]:
    """(time stamp, text) of a line of `docker logs --timestamps`; ("", line) when it has none."""
    stamp, _, text = line.partition(" ")
    if len(stamp) >= 20 and stamp[:4].isdigit() and stamp[4] == "-" and "T" in stamp:
        return stamp, text
    return "", line


def matcher(query: str) -> Callable[[str], bool]:
    """Whether a line contains `query`, ignoring case (everything matches an empty query)."""
    wanted = query.strip().casefold()
    if not wanted:
        return lambda _line: True
    return lambda line: wanted in line.casefold()


def compile_pattern(pattern: str) -> regex.Pattern[str]:
    """`pattern` as a regular expression (ignoring case), or ValueError with what is wrong: too long, not valid, or
    too slow already on short probe texts (nested repetitions such as `(a+)+`)."""
    if len(pattern) > MAX_QUERY:
        raise ValueError(f"The pattern is too long (at most {MAX_QUERY} characters).")
    try:
        compiled = regex.compile(pattern, regex.IGNORECASE | regex.V0)
    except (regex.error, OverflowError, RecursionError) as error:
        raise ValueError(f"This is not a valid regular expression: {str(error)[:120]}.") from None
    for probe in _PROBES:
        try:
            compiled.search(probe, timeout=PATTERN_ENTRY_SECONDS, concurrent=True)
        except TimeoutError:
            raise ValueError(
                "This regular expression could take very long (nested repetitions such as (a+)+). Simplify it."
            ) from None
    return compiled


@dataclass
class Filters:
    """What a page, a stream or a download keeps. Built by from_args, which checks every value."""

    levels: frozenset[str] | None = None
    logger: str = ""
    query: str = ""
    pattern: regex.Pattern[str] | None = None
    since: datetime | None = None
    until: datetime | None = None
    tracebacks: bool = False
    spent: float = 0.0
    _plain: Callable[[str], bool] = field(default=lambda _line: True, repr=False)

    def __post_init__(self) -> None:
        self._plain = matcher(self.query) if self.pattern is None else (lambda _line: True)

    @property
    def empty(self) -> bool:
        return not (
            self.levels or self.logger or self.query or self.pattern or self.since or self.until or self.tracebacks
        )

    def keep(self, entry: Entry) -> bool:
        """Whether `entry` passes every filter; SlowPattern when the pattern has taken too long on this request."""
        if self.levels is not None and (entry.level or "INFO") not in self.levels:
            return False
        if self.logger:
            logger = entry.logger or ""
            if logger != self.logger and not logger.startswith(self.logger + "."):
                return False
        if self.tracebacks and not entry.traceback:
            return False
        if self.since or self.until:
            when = entry.time()
            if when is None or (self.since and when < self.since) or (self.until and when >= self.until):
                return False
        text = entry.text[:MATCH_CHARS]
        if self.pattern is not None:
            started = time.monotonic()
            try:
                found = self.pattern.search(text, timeout=PATTERN_ENTRY_SECONDS, concurrent=True) is not None
            except TimeoutError:
                raise SlowPattern from None
            finally:
                self.spent += time.monotonic() - started
            if self.spent > PATTERN_REQUEST_SECONDS:
                raise SlowPattern
            return found
        return self._plain(text)


def filters_from(args: Mapping[str, str]) -> Filters:
    """The filters a request asks for (`level`, `logger`, `q`, `regex`, `since`, `until`, `tracebacks`), or
    ValueError with what is wrong. Shared by the pages, the stream and the download."""
    levels: frozenset[str] | None = None
    raw_levels = args.get("level", "").strip()
    if raw_levels:
        chosen = {part.strip().upper() for part in raw_levels.split(",") if part.strip()}
        if not chosen <= set(LEVELS):
            raise ValueError(f"level takes one or more of {', '.join(LEVELS)}, separated by commas.")
        levels = frozenset(chosen)
    logger = args.get("logger", "").strip()
    if logger and not _LOGGER.match(logger):
        raise ValueError("logger is the name of a Python logger, such as odoo.http (letters, digits, . _ -).")
    query = args.get("q", "")
    if len(query) > MAX_QUERY:
        raise ValueError(f"The filter is too long (at most {MAX_QUERY} characters).")
    pattern = None
    if args.get("regex", "") in ("1", "true", "on") and query.strip():
        pattern = compile_pattern(query.strip())
    times: dict[str, datetime | None] = {}
    for key in ("since", "until"):
        raw = args.get(key, "").strip()
        times[key] = parse_time(raw) if raw else None
        if raw and times[key] is None:
            raise ValueError(f"{key} is a time in ISO 8601 with its zone, such as 2026-11-22T07:00:00Z.")
    if times["since"] and times["until"] and times["since"] >= times["until"]:
        raise ValueError("since must be before until.")
    return Filters(
        levels=levels,
        logger=logger,
        query=query.strip() if pattern is None else "",
        pattern=pattern,
        since=times["since"],
        until=times["until"],
        tracebacks=args.get("tracebacks", "") in ("1", "true", "on"),
    )


def cursor(value: str, key: str) -> str:
    """A page's `before`, `after` or `around` as an id (ValueError when it cannot be one)."""
    value = value.strip()
    if not _CURSOR.match(value):
        raise ValueError(f"{key} is the id of an entry, as a page or a stream gave it.")
    return normal_stamp(value) or value


# ---------------------------------------------------------------- reading


def odoo_log_command(
    name: str, tail: int, follow: bool = False, since: str | None = None, until: str | None = None
) -> list[str]:
    """docker logs of the instance's Odoo container: the last `tail` lines (all when `since` is given: those after
    that time; with `until`, those before it), each with its time stamp (RFC 3339; streams resume from it), then new
    ones when `follow`."""
    cmd = ["docker", "logs", "--timestamps"]
    if since:
        cmd += ["--since", since]
    else:
        cmd += ["--tail", str(tail)]
    if until:
        cmd += ["--until", until]
    if follow:
        cmd.append("--follow")
    return [*cmd, store.container(name, "odoo")]


def _docker_lines(cmd: list[str]) -> Iterator[tuple[str, str]]:
    """(id, text) of every line `cmd` (docker logs --timestamps) prints, until MAX_SCAN_BYTES were read."""
    read = 0
    try:
        with subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
        ) as proc:
            assert proc.stdout is not None
            try:
                for raw in proc.stdout:
                    read += len(raw)
                    if read > MAX_SCAN_BYTES:
                        break
                    stamp, text = split_stamp(raw.rstrip("\n"))
                    yield normal_stamp(stamp), text
            finally:
                if proc.poll() is None:
                    proc.kill()
    except OSError:
        return


def group(lines: Iterable[tuple[str, str]]) -> Iterator[Entry]:
    """Entries from (id, text) lines: a line in Odoo's format starts one; the lines after it that are not join it."""
    current: Entry | None = None
    for line_id, text in lines:
        head = parse_line(text)
        if head is None and current is not None and current.head is not None:
            current.lines.append(text)
            continue
        if current is not None:
            yield current
        current = Entry(line_id, [text], head)
    if current is not None:
        yield current


def _run_kind(command: str) -> str | None:
    """Which job log a command the panel ran belongs to: a run of Odoo that installs modules (install.log) or only
    updates them (update.log); None for anything else (docker, git, psql...)."""
    words = command.split()
    if "--stop-after-init" not in words:
        return None
    if "-i" in words:
        return INSTALL_LOG
    return UPDATE_LOG if "-u" in words else None


def _job_entries(name: str, log: str) -> list[Entry]:
    """install.log or update.log of instance `name`: Odoo's own lines of its `--stop-after-init` runs, from the jobs
    the panel keeps (oldest first), and their tracebacks; never a command, a path or a step of the panel."""
    from sudoo.core import jobs

    found: collections.deque[tuple[str, str]] = collections.deque(maxlen=JOB_LOG_LINES)
    for job in sorted((j for j in list(jobs.JOBS.values()) if j.instance == name), key=lambda j: j.started):
        inside = in_entry = in_trace = False
        base = f"{int(job.started * 1000):013d}"
        for number, line in enumerate("".join(job.log).splitlines()):
            if line.startswith("$ "):  # a command the panel runs: never shown, it only says whose output follows
                inside, in_entry = _run_kind(line) == log, False
                continue
            if line.startswith("== "):  # the next step
                inside = in_entry = False
                continue
            if not inside or line.startswith(("[info]", "[warning]", "[error]", "[success]")):
                in_entry = False
                continue
            if parse_line(line):
                in_entry, in_trace = True, False
            elif in_entry and line.startswith(_TRACE_START):
                in_trace = True
            elif in_entry and in_trace and (line.startswith("  ") or not line.strip()):
                pass  # a frame of the traceback: File "...", line n, in f / the code
            elif in_entry and in_trace and _TRACE_END.match(line):
                in_trace = False  # the exception: the traceback ends (a chained one may follow)
            elif not (in_entry and line.startswith(_TRACE_CHAINED)):
                in_entry = in_trace = False
                continue
            found.append((f"{base}.{number:07d}", redact(line)))
    return list(group(found))


def job_log_info(name: str, log: str) -> dict[str, Any] | None:
    """When install.log or update.log was last written ({"started_at"}), or None when the panel has no such run."""
    from sudoo.core import jobs

    latest: float | None = None
    for job in list(jobs.JOBS.values()):
        if job.instance == name and any(
            line.startswith("$ ") and _run_kind(line) == log for chunk in job.log for line in chunk.splitlines()
        ):
            latest = max(latest or 0.0, job.started)
    return None if latest is None else {"started_at": latest}


def available(name: str) -> list[dict[str, object]]:
    """The logs of instance `name` that can be read: [{"name", "job", "status", "started_at"}]: odoo.log (its
    container may be stopped: it is then what Docker kept of the last run), then install.log and update.log when the
    panel has such a run of Odoo in memory (started_at: the latest, Unix time). "job" and "status" stay for clients
    written for earlier versions, always null."""
    found: list[dict[str, object]] = [{"name": ODOO_LOG, "job": None, "status": None, "started_at": None}]
    for log in JOB_LOGS:
        info = job_log_info(name, log)
        if info:
            found.append({"name": log, "job": None, "status": None, "started_at": info["started_at"]})
    return found


@dataclass
class Page:
    entries: list[Entry]
    has_before: bool
    has_after: bool
    truncated: bool = False


def _source(name: str, log: str, window: int, before: str, after: str, since: datetime | None) -> Iterator[Entry]:
    if log in JOB_LOGS:
        return iter(_job_entries(name, log))
    docker_since = after or (since.strftime("%Y-%m-%dT%H:%M:%S.000000000Z") if since else None)
    until = before or None
    return group(_docker_lines(odoo_log_command(name, window, since=docker_since, until=until)))


def page(
    name: str,
    log: str,
    filters: Filters,
    *,
    limit: int = PAGE_DEFAULT,
    before: str = "",
    after: str = "",
    around: str = "",
    window: int = 100000,
) -> Page:
    """A page of entries of `log` that pass `filters`, oldest first: the last `limit` (before `before` when given),
    the first `limit` after `after`, or `limit` around `around` (the entry itself, about half before and half after,
    for a link to one line). `window`: how many of the last lines of odoo.log are read at most. SlowPattern when a
    pattern takes too long."""
    if around:
        half = max(1, limit // 2)
        kept: collections.deque[Entry] = collections.deque(maxlen=half)
        later: list[Entry] = []
        found_it = dropped = False
        more = False
        for entry in _source(name, log, window, "", "", filters.since):
            if not found_it:
                if entry.id == around or entry.id > around:
                    found_it = True
                    later.append(entry)  # the entry itself is always shown, filters or not
                elif filters.keep(entry):
                    dropped = dropped or len(kept) == half
                    kept.append(entry)
                continue
            if filters.keep(entry):
                if len(later) >= limit - len(kept):
                    more = True
                    break
                later.append(entry)
        return Page(list(kept) + later, dropped, more)
    if after:
        first: list[Entry] = []
        for entry in _source(name, log, window, "", after, None):
            if entry.id <= after or not filters.keep(entry):
                continue
            if len(first) == limit:
                return Page(first, True, True)
            first.append(entry)
        return Page(first, True, False)
    last: collections.deque[Entry] = collections.deque(maxlen=limit)
    overflow = False
    for entry in _source(name, log, window, before, "", filters.since):
        if before and entry.id >= before:
            break
        if filters.keep(entry):
            overflow = overflow or len(last) == limit
            last.append(entry)
    return Page(list(last), overflow, bool(before))


def tail(name: str, log: str, lines: int, query: str = "", filters: Filters | None = None) -> Iterator[str]:
    """The text of the entries among the last `lines` lines of `log` that contain `query` (or pass `filters`), each
    line ending in a newline. It is read as it comes from Docker, so a large download never holds more than `lines`
    lines in the panel's memory. A pattern that takes too long ends the file early, with a line that says so."""
    keep = filters or Filters(query=query)
    if log not in NAMES:
        return iter(())
    found: collections.deque[str] = collections.deque(maxlen=lines)
    entries = iter(_job_entries(name, log)) if log in JOB_LOGS else group(_docker_lines(odoo_log_command(name, lines)))
    try:
        for entry in entries:
            if keep.keep(entry):
                found.extend(line + "\n" for line in entry.lines)
    except SlowPattern:
        found.append("[the regular expression took too long: the rest of the log was not searched]\n")
    return iter(found)
