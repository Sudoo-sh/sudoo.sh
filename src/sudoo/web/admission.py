"""How many requests the panel works on at once (MAX_CONCURRENT_REQUESTS), and what the others get.

The panel is one process, and Python runs one thread at a time: 32 requests worked on together each take 32 times
longer, and none finishes sooner. Under a rush (50 people opening the dashboard at once on a server with 1,000
instances) every answer then took longer than the browsers waited; they gave up and asked again, while the panel kept
working on the requests nobody waited for any more, so it stayed unusable for minutes after the rush had ended.

Now at most MAX_CONCURRENT_REQUESTS requests are worked on at once. The next ones wait their turn, first come first
served, for at most REQUEST_QUEUE_SECONDS; a request still waiting then is answered at once, 503 with Retry-After
("busy, try again") and nothing done, so a rush is refused cleanly and the panel is back to normal as soon as it
ends. /healthz and the following of a log (a stream that stays open for minutes) never wait. 0 turns it off.
"""

from __future__ import annotations

import threading
import time

from flask import Flask, Response, abort, g, request

from sudoo.config import settings

BUSY = "The panel is busy. Try again in a few seconds."
RETRY_SECONDS = 5
_TURN = threading.Condition()
_WORKING = [0]  # requests worked on now
_WAITING = [0]  # requests waiting for their turn now


def _exempt() -> bool:
    return request.path == "/healthz" or request.path.endswith("/logs/stream")


def _enter() -> None:
    limit = int(settings.max_concurrent_requests)
    if limit <= 0 or _exempt():
        return
    deadline = time.monotonic() + float(settings.request_queue_seconds)
    with _TURN:
        _WAITING[0] += 1
        try:
            while _WORKING[0] >= limit:
                left = deadline - time.monotonic()
                if left <= 0:
                    g.retry_after = RETRY_SECONDS
                    abort(503, BUSY)
                _TURN.wait(left)
        finally:
            _WAITING[0] -= 1
        _WORKING[0] += 1
    g.admitted = True


def _leave() -> None:
    if g.pop("admitted", False):
        with _TURN:
            _WORKING[0] -= 1
            _TURN.notify()


def _after(response: Response) -> Response:
    _leave()  # a streamed answer is sent after this: sending it does not hold a turn
    if response.status_code == 503 and g.get("retry_after"):
        response.headers["Retry-After"] = str(g.retry_after)
    return response


def _teardown(_error: BaseException | None) -> None:
    _leave()  # when after_request did not run (an error the error handlers did not catch)


def counts() -> dict[str, int]:
    """{"working", "waiting"}: requests worked on and waiting now."""
    with _TURN:
        return {"working": _WORKING[0], "waiting": _WAITING[0]}


def install(app: Flask) -> None:
    """Before every other check of a request, so waiting costs nothing but a thread."""
    app.before_request(_enter)
    app.after_request(_after)
    app.teardown_request(_teardown)
