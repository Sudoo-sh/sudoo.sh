"""Keeping the panel up when something goes wrong (docs/ARCHITECTURE.md "Staying up").

Nothing that fails inside the panel may take the panel down: a plugin that cannot be loaded, a setting with a wrong
value, a database, Docker, mail server or hub that does not answer at start, a background job that raises. Each of
these is caught where it happens, logged, and listed here as a *problem*; the panel goes on with what still works.

problems()      what is wrong right now, for the health check (/healthz), the administrators' banner and
                GET /api/v1/health. Each problem has a kind ("config", "plugin", "startup", "background"), a key,
                a short title and the error's message, cleaned of secrets (describe). The list is bounded.
attempt()       one step of starting the panel: when it raises, the panel starts without it and the problem is
                listed.
start_thread()  a background job under supervision: when it raises it is started again after a pause that
                doubles each time (BACKGROUND_RESTART_SECONDS up to BACKGROUND_RESTART_MAX_SECONDS); a job that
                ends normally is done. Work that must happen once (exposing the instances at start) is retried the
                same way until it succeeds, so a dependency that is down at start is used once it is back.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from sudoo.config import live, settings
from sudoo.core.shell import redact

log = logging.getLogger("sudoo.resilience")

MAX_PROBLEMS = 200  # problems kept at most: the oldest goes first, so a fault that repeats never grows memory
MAX_DETAIL = 300  # characters of an error message kept

_LOCK = threading.Lock()
_PROBLEMS: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_sleep: Callable[[float], None] = time.sleep  # tests replace it


def describe(error: BaseException) -> str:
    """The error's type and message, cleaned of passwords in addresses and tokens, shortened: safe to show to an
    administrator and to keep in the audit log."""
    text = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
    text = redact(" ".join(text.split()))
    return text if len(text) <= MAX_DETAIL else text[: MAX_DETAIL - 1] + "…"


def report(kind: str, key: str, title: str, detail: str = "", level: str = "danger") -> None:
    """List a problem (or count it again when it is listed already)."""
    now = time.time()
    with _LOCK:
        found = _PROBLEMS.pop((kind, key), None)
        if found is None:
            found = {"kind": kind, "key": key, "since": now, "count": 0}
        found.update(title=title, detail=detail[:MAX_DETAIL], level=level, last=now, count=found["count"] + 1)
        _PROBLEMS[(kind, key)] = found
        while len(_PROBLEMS) > MAX_PROBLEMS:
            _PROBLEMS.popitem(last=False)


def resolve(kind: str, key: str) -> None:
    """The problem is gone (the job ran, the dependency answered)."""
    with _LOCK:
        _PROBLEMS.pop((kind, key), None)


def problems() -> list[dict[str, Any]]:
    """Everything that is wrong now, the settings with wrong values first, then the newest first."""
    found = [
        {"kind": "config", "key": name, "detail": detail, "level": "warning", "since": None, "last": None, "count": 1,
         "title": f"The setting {name.upper()} has a wrong value; its default is used" if "settings" not in name
         else "The panel's saved settings could not be used; the values from .env are used"}
        for name, detail in live.problems().items()
    ]  # fmt: skip
    with _LOCK:
        found += [dict(item) for item in reversed(_PROBLEMS.values())]
    return found


def clear() -> None:
    """Forget every listed problem (tests)."""
    with _LOCK:
        _PROBLEMS.clear()


def attempt[T](name: str, fn: Callable[..., T], *args: Any, default: T | None = None) -> T | None:
    """Run one step of starting the panel; when it raises, log it, list the problem and return `default`."""
    try:
        result = fn(*args)
    except Exception as error:  # noqa: BLE001 - the panel starts without this step
        log.exception("start-up step %s failed", name)
        report("startup", name, f"Part of the panel did not start: {name}", describe(error))
        return default
    resolve("startup", name)
    return result


def backoff(attempt: int) -> float:
    """Seconds to wait before the `attempt`-th restart (0 for the first): doubling, at most the maximum."""
    first = float(settings.background_restart_seconds)
    longest = max(first, float(settings.background_restart_max_seconds))
    return min(longest, first * (2 ** min(attempt, 30)))


def supervised(name: str, fn: Callable[[], object]) -> Callable[[], None]:
    """`fn` under supervision: started again after a growing pause whenever it raises, until it returns. A job that
    ran longer than the longest pause before it failed starts again from the shortest one."""

    def run() -> None:
        attempt = 0
        while True:
            started = time.monotonic()
            try:
                fn()
            except Exception as error:  # noqa: BLE001 - one job never stops the others, nor itself for good
                if time.monotonic() - started > float(settings.background_restart_max_seconds):
                    attempt = 0
                wait = backoff(attempt)
                attempt += 1
                log.exception("background job %s failed; started again in %.0f s", name, wait)
                report(
                    "background", name, f"Background job {name} failed and is started again",
                    f"{describe(error)} (failed {attempt} times in a row; next try in {wait:.0f} s)", "warning",
                )  # fmt: skip
                _sleep(wait)
                continue
            resolve("background", name)
            return

    return run


def start_thread(name: str, fn: Callable[[], object]) -> threading.Thread:
    """Start `fn` in a daemon thread named `name`, under supervision (supervised)."""
    thread = threading.Thread(target=supervised(name, fn), daemon=True, name=name)
    thread.start()
    return thread


def guard(name: str, fn: Callable[..., object]) -> Callable[..., None]:
    """`fn` for a listener or callback: an exception is logged and listed, never passed to the caller."""

    def run(*args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 - a listener never breaks whoever called it
            log.exception("%s failed", name)
            report("background", name, f"Background work {name} failed", describe(error), "warning")

    return run
