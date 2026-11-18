"""The support channel: the panel asks the Sudoo.sh hub for support's requests while a grant is active, carries
them out against its own API as support's synthetic user, and sends the answers back. All over the panel's existing
signed link to the hub (hub/client.py): every call signed with the installation key, every answer verified with the
hub's keys; nothing listens on the server for it.

    POST /api/v1/installations/support/channel   {grants, ended_sessions, wait}: every active grant (a grant the
                                                  panel no longer sends is revoked on the hub), the sessions ended
                                                  here; the hub answers within `wait` seconds with the requests
                                                  waiting and the sessions open
    POST /api/v1/installations/support/requests/<id>/response   {status, body}: the answer to one request

The loop asks with wait=20 while a grant is active, sends one last call when the grants changed (so a withdrawn
grant is revoked on the hub within a second or two: revoke() wakes it, and a separate call goes out at once), and
otherwise sleeps until a grant is made. Requests are carried out by a small pool of threads, each checked first: an
active grant of this panel, a session the hub says is open on that grant, not ended here, and not past its time.

Requests (`kind`)
    http    one call of /api/v1 (relay_http): run through the Flask app in this process with the support user,
            never the paths in BLOCKED; the answer must be JSON (at most MAX_ANSWER_BYTES)
    shell   one step of a support shell (shells.py)
    hello   nothing: the sessions changed (the answer's session list is the news)
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from flask import Flask

from sudoo.core import audit

from . import active, get, is_active, note_expired, user_for

log = logging.getLogger("sudoo.support")

CHANNEL = "/api/v1/installations/support/channel"
WAIT_SECONDS = 20
TIMEOUT_SECONDS = WAIT_SECONDS + 20
RETRY_SECONDS = (5, 10, 30, 60)
MAX_ANSWER_BYTES = 900 * 1024  # the hub takes 1 MB bodies
WORKERS = 8
SESSION_SEEN_SECONDS = 90  # a session the hub listed this recently is shown as connected
IP = "sudoo.sh-support"  # the address audit entries of support show
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
PATH_RE = re.compile(r"^/api/v1/[A-Za-z0-9._~/-]{1,400}$")
QUERY_RE = re.compile(r"^[A-Za-z0-9._~=&%+,:-]{0,400}$")
BLOCKED = re.compile(
    r"^/api/v1/(?:session|account|support-access|tokens|users|invitations?|settings|server/restart|setup|licence"
    r"|teams|mail-templates|plugins|marketplace|offsite/(?:settings|key)|instances/[^/]+/shell)(?:/|$)"
)
"""Paths support never reaches, whatever its level: its own access, accounts and sign-in, tokens, teams, the panel's
settings and restart, plugins and purchases, off-site storage settings, and the browser shell (support's shells go
through shells.py, always recorded)."""

APP: Flask | None = None
_WAKE = threading.Event()
_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="support")
_SESSIONS: dict[str, dict[str, Any]] = {}  # session id -> {id, grant_id, staff_name, opened_at, expires_at, seen}
_ENDED: set[str] = set()  # sessions ended here ("End now"), until the hub confirms
_STATE: dict[str, Any] = {"reported": None, "last_ok": None, "error": None}


def start(app: Flask) -> None:
    """Start the loop (once, when the panel starts)."""
    global APP
    APP = app
    threading.Thread(target=run_forever, daemon=True, name="support-channel").start()


def wake() -> None:
    """The grants changed: tell the hub now, and start asking for work if a grant is active."""
    _WAKE.set()
    threading.Thread(target=_sync_quietly, daemon=True, name="support-sync").start()


def _enabled() -> bool:
    from sudoo.hub import marketplace

    return marketplace.enabled()


def _sync_quietly() -> None:
    try:
        if _enabled():
            exchange(0)
    except Exception as error:  # noqa: BLE001 - the loop sends it again
        log.info("support: could not tell the hub at once: %s", error)


def run_forever() -> None:
    failures = 0
    while True:
        wait = 60.0
        try:
            note_expired()
            grants = active()
            changed = {g["id"] for g in grants} != _STATE["reported"]
            if _enabled() and (grants or changed or _ENDED):
                exchange(WAIT_SECONDS if grants and not changed and not _ENDED else 0)
                failures, wait = 0, 0 if grants else 60.0
        except Exception as error:  # noqa: BLE001 - the loop must survive anything; it tries again later
            wait = RETRY_SECONDS[min(failures, len(RETRY_SECONDS) - 1)]
            failures += 1
            with _LOCK:
                _STATE["error"] = str(error)[:300]
            log.info("support channel: %s", error)
        if wait:
            _WAKE.wait(wait)
        _WAKE.clear()


def _grant_out(grant: Mapping[str, Any]) -> dict[str, Any]:
    from sudoo import accounts
    from sudoo.web.v1.responses import iso

    account = accounts.get(str(grant.get("created_by") or "")) or {}
    by = " ".join(p for p in (account.get("first_name"), account.get("last_name")) if p) or grant.get("created_by")
    scope = grant.get("scope") or {}
    return {
        "id": grant["id"],
        "level": grant["level"],
        "scope": {
            "all": bool(scope.get("all")),
            "projects": [
                {"id": str(p["id"]), "name": str(p.get("name") or p["id"])[:200]} for p in scope.get("projects") or ()
            ],
            "instances": list(scope.get("instances") or ()),
        },
        "ticket": grant.get("ticket"),
        "granted_by": "".join(c for c in str(by or "admin") if c not in "<>")[:200] or "admin",
        "granted_at": iso(grant["created_at"]),
        "expires_at": iso(grant["expires_at"]),
    }


def exchange(wait: int) -> list[dict[str, Any]]:
    """One call of the channel: report the grants and ended sessions, take the requests (each handed to the pool)
    and the open sessions. Raises HubUnavailable or HubRefused."""
    from sudoo.hub import client, marketplace

    grants = active()
    with _LOCK:
        ended = sorted(_ENDED)
    body = {"grants": [_grant_out(g) for g in grants], "ended_sessions": ended, "wait": wait if grants else 0}
    marketplace.register()
    answer = client.request("POST", CHANNEL, body, timeout=TIMEOUT_SECONDS if wait else 15).data or {}
    sessions = [s for s in answer.get("sessions") or () if isinstance(s, dict)] if isinstance(answer, dict) else []
    requests = [r for r in answer.get("requests") or () if isinstance(r, dict)] if isinstance(answer, dict) else []
    _remember_sessions(sessions, ended)
    with _LOCK:
        _STATE.update(reported={g["id"] for g in grants}, last_ok=time.time(), error=None)
    for item in requests:
        _POOL.submit(_handle_quietly, item)
    return requests


def _remember_sessions(listed: list[dict[str, Any]], ended_sent: list[str]) -> None:
    """The sessions the hub says are open: new ones and ended ones go to the audit log (as support's user)."""
    from sudoo.core import events

    now = time.time()
    with _LOCK:
        _ENDED.difference_update(ended_sent)  # the hub took them
        before = dict(_SESSIONS)
        current: dict[str, dict[str, Any]] = {}
        for item in listed:
            grant = get(str(item.get("grant_id") or ""))
            session_id = str(item.get("id") or "")
            if not grant or not is_active(grant) or not session_id or session_id in _ENDED:
                continue
            current[session_id] = {
                "id": session_id,
                "grant_id": grant["id"],
                "staff_name": str(item.get("staff_name") or "staff"),
                "opened_at": item.get("opened_at"),
                "expires_at": item.get("expires_at"),
                "seen": now,
            }
        _SESSIONS.clear()
        _SESSIONS.update(current)
    for session_id in current.keys() - before.keys():
        item = current[session_id]
        user = user_for(
            get(item["grant_id"]) or {"id": item["grant_id"], "level": "view"}, item["staff_name"], session_id
        )
        audit.record(
            user["name"], IP, "support session started", detail=f"session {session_id}; grant {item['grant_id']}"
        )
    for session_id in before.keys() - current.keys():
        item = before[session_id]
        user = user_for(
            get(item["grant_id"]) or {"id": item["grant_id"], "level": "view"}, item["staff_name"], session_id
        )
        audit.record(
            user["name"], IP, "support session ended", detail=f"session {session_id}; grant {item['grant_id']}"
        )
        from . import shells

        shells.end_for_session(session_id)
    if current.keys() != before.keys():
        events.publish("support", "status", {"changed": True})


def sessions() -> list[dict[str, Any]]:
    """Support sessions open now (the hub listed them in the last SESSION_SEEN_SECONDS, their grant is active)."""
    now = time.time()
    with _LOCK:
        found = [dict(s) for s in _SESSIONS.values() if now - s["seen"] <= SESSION_SEEN_SECONDS]
    return [s for s in found if is_active(get(s["grant_id"]))]


def session_open(session_id: str, grant_id: str) -> dict[str, Any] | None:
    for item in sessions():
        if item["id"] == session_id and item["grant_id"] == grant_id:
            return item
    return None


def end_sessions_now() -> None:
    """ "End now": every open session ends here at once, and on the hub with the next call."""
    from sudoo.core import events

    from . import shells

    with _LOCK:
        ended = list(_SESSIONS.values())
        _ENDED.update(_SESSIONS)
        _SESSIONS.clear()
    for item in ended:
        user = user_for(get(item["grant_id"]) or {"id": item["grant_id"], "level": "view"}, item["staff_name"], item["id"])
        audit.record(user["name"], IP, "support session ended", detail=f"ended on the panel; session {item['id']}")
        shells.end_for_session(item["id"])
    events.publish("support", "status", {"changed": True})
    wake()


def state() -> dict[str, Any]:
    with _LOCK:
        return {"last_ok": _STATE["last_ok"], "error": _STATE["error"]}


def forget() -> None:
    """Forget sessions and state (tests)."""
    with _LOCK:
        _SESSIONS.clear()
        _ENDED.clear()
        _STATE.update(reported=None, last_ok=None, error=None)


# ---------------------------------------------------------------- carrying out a request


def _handle_quietly(item: dict[str, Any]) -> None:
    try:
        status, body = handle(item)
    except Exception:  # noqa: BLE001 - one failing request must not stop the others
        log.exception("support: a request failed")
        status, body = 500, _problem(500, "The panel could not carry out this request.")
    if item.get("kind") == "hello" or not isinstance(item.get("id"), str):
        return
    respond(str(item["id"]), status, body)


def respond(request_id: str, status: int, body: Any) -> None:
    from sudoo.hub import HubRefused, HubUnavailable, client

    if not re.fullmatch(r"[a-f0-9]{8,64}", request_id):
        return
    try:
        client.request("POST", f"/api/v1/installations/support/requests/{request_id}/response",
                       {"status": status, "body": body})  # fmt: skip
    except (HubUnavailable, HubRefused) as error:
        log.info("support: the answer to %s did not reach the hub: %s", request_id, error)


def _problem(status: int, detail: str) -> dict[str, Any]:
    return {"type": "about:blank", "status": status, "detail": detail}


def handle(item: Mapping[str, Any]) -> tuple[int, Any]:
    """Check one request and carry it out: (status, JSON body)."""
    kind = item.get("kind")
    if kind == "hello":
        return 204, None
    if int(item.get("expires_at") or 0) < time.time():
        return 408, _problem(408, "The request waited too long.")
    grant = get(str(item.get("grant_id") or ""))
    session = session_open(str(item.get("session_id") or ""), str(item.get("grant_id") or ""))
    if not is_active(grant) or session is None or grant is None:
        return 403, _problem(403, "Support access to this panel was withdrawn or has ended.")
    user = user_for(grant, session["staff_name"], session["id"])
    if kind == "http":
        return relay_http(user, item)
    if kind == "shell":
        from . import shells

        payload = item.get("shell")
        return shells.handle(user, payload if isinstance(payload, dict) else {})
    return 400, _problem(400, "This panel does not know this kind of request.")


def relay_http(user: dict[str, Any], item: Mapping[str, Any]) -> tuple[int, Any]:
    """Run one /api/v1 call through the panel's own Flask app as `user` (security.py authenticates it from the
    WSGI environment, which nothing outside this process can set)."""
    from werkzeug.test import EnvironBuilder, run_wsgi_app

    from sudoo.web import security

    method, path, query = str(item.get("method") or "GET"), str(item.get("path") or ""), str(item.get("query") or "")
    if method not in METHODS or not PATH_RE.match(path) or not QUERY_RE.match(query) or ".." in path.split("/"):
        return 400, _problem(400, "This is not a request support can make.")
    if BLOCKED.match(path):
        audit.record(user["name"], IP, "support request refused", ok=False, detail=f"{method} {path}")
        return 403, _problem(403, "Support cannot use this part of the panel.")
    if APP is None:
        return 503, _problem(503, "The panel is starting. Try again in a moment.")
    body = item.get("body")
    builder = EnvironBuilder(
        path=path,
        method=method,
        query_string=query,
        json=body if method != "GET" and body is not None else None,
        environ_base={"REMOTE_ADDR": IP, security.SUPPORT_ENVIRON: user},
    )
    try:
        app_iter, status_line, headers = run_wsgi_app(APP, builder.get_environ(), buffered=True)
        raw = b"".join(app_iter)
    finally:
        builder.close()
    status = int(status_line.split(" ", 1)[0])
    if method != "GET" or status >= 400:
        audit.record(user["name"], IP, "support request", ok=status < 400, detail=f"{method} {path} -> {status}")
    if len(raw) > MAX_ANSWER_BYTES:
        return 413, _problem(413, "The answer is too large to send through support access.")
    if not raw:
        return status, None
    if "json" not in headers.get("Content-Type", ""):
        return 415, _problem(415, "This answer is not JSON (a download): it cannot go through support access.")
    try:
        return status, json.loads(raw)
    except ValueError:
        return 502, _problem(502, "The panel's answer could not be read.")
