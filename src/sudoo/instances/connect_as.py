"""Connect as: open an instance's Odoo signed in as one of its users, without knowing (or changing) a password.

1. Someone with instances.credentials asks the panel for a link (POST /api/v1/instances/<name>/connect, web/v1/
   connect.py), naming an active internal Odoo user. The panel issues a token: random, single-use, valid for
   TOKEN_SECONDS, bound to that instance and that Odoo user, kept only as its SHA-256 in the panel's storage
   (core/shared.py: issued by any worker process, redeemed by the one that serves the gateway).
   The request is audit-logged (who, instance, which Odoo user); production also needs a typed confirmation.
2. The browser opens <instance address>/__sudoo/connect?token=... . The gateway (gateway.py) passes the token to
   connect(): it is used up whatever happens next; when it was valid, a session for the user is written into the
   instance's own session store by Odoo itself (SESSION_SCRIPT runs in `odoo shell` inside the instance's Odoo
   container, through Odoo's own session API, so the session token is the one Odoo computes for that user), and
   the use is audit-logged too. The gateway sets Odoo's session_id cookie for the instance's address and redirects
   to /web.

Passwords are never read, reset or changed. The session is an ordinary Odoo session: it ends when the user signs
out, when their password changes (Odoo checks the session token), or when Odoo's session store removes it.

Verified with Odoo 17, 18, 19 and 20 (odoo.http.root.session_store, or odoo.http.session.session_store() from 19 on);
Odoo 15 and 16 use the same calls (root.session_store.new / save, res.users._compute_session_token).
"""

from __future__ import annotations

import hashlib
import secrets
import subprocess
import time
from dataclasses import asdict, dataclass

from sudoo.core import audit
from sudoo.core.shared import SharedMap
from sudoo.instances import odoo, store

TOKEN_SECONDS = 60
"""How long a Connect as link works (once)."""
USERS_CACHE_SECONDS = 30
"""How long the list of an instance's Odoo users is reused."""
PATH = "/__sudoo/connect"
"""Where the gateway answers a Connect as link, on every instance's address."""
COOKIE = "session_id"
"""Odoo's session cookie."""
SESSION_SECONDS = 7 * 24 * 3600
"""How long the browser keeps the cookie (Odoo's own session lifetime)."""
MAX_TOKENS = 1000
"""Unused tokens kept at most (the oldest go first): a flood of requests cannot fill the memory."""

SESSION_SCRIPT = """
import importlib, os
import odoo.http as _http
try:
    _module = importlib.import_module("odoo.http.session")
except ImportError:
    _module = _http
_store = _module.session_store() if callable(getattr(_module, "session_store", None)) else _http.root.session_store
_users = env["res.users"].sudo().with_context(active_test=False)
_user = _users.browse(int(os.environ["SUDOO_CONNECT_UID"])).exists()
if not _user or not _user.active or _user.share:
    print("SUDOO_CONNECT_ERROR=not an active internal user", flush=True)
else:
    _session = _store.new()
    _default = getattr(_module, "get_default_session", None) or getattr(_http, "get_default_session", None)
    if _default:
        _session.update(_default())
    _env = env(user=_user.id)
    _session.update({
        "db": env.cr.dbname, "login": _user.login, "uid": _user.id,
        "context": dict(_env["res.users"].context_get()),
        "session_token": _env.user._compute_session_token(_session.sid),
    })
    _store.save(_session)
    print("SUDOO_CONNECT_SID=" + _session.sid, flush=True)
"""
"""Run by `odoo shell` (it reads its script from standard input) in the instance's Odoo container; the user's id
comes in the environment, never in the script's text."""

USERS_SQL = (
    "SELECT u.id, u.login, COALESCE(p.name, u.login) FROM res_users u LEFT JOIN res_partner p ON p.id = u.partner_id "
    "WHERE u.active AND NOT COALESCE(u.share, false) ORDER BY u.id LIMIT 500"
)
"""Active internal users (share is false for internal users; portal and public users are shared)."""


class ConnectError(Exception):
    """Connecting failed; the message is for the person connecting."""


@dataclass(frozen=True, slots=True)
class Grant:
    """What a token allows: the panel user `user` signs in to `instance` as Odoo user `uid` (`login`)."""

    instance: str
    uid: int
    login: str
    user: str
    expires: float


_GRANTS = SharedMap("connect_as_grants")  # SHA-256 of the token -> grant
_USERS: dict[str, tuple[float, list[dict[str, object]]]] = {}  # instance -> (read at, users)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(instance: str, uid: int, login: str, user: str, now: float | None = None) -> tuple[str, float]:
    """A new token for `user` to sign in to `instance` as Odoo user `uid`; (token, when it expires)."""
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    grant = Grant(instance, uid, login, user, now + TOKEN_SECONDS)
    with _GRANTS.lock:
        kept = _GRANTS.items()
        for key in sorted(kept, key=lambda k: float(kept[k]["expires"]))[: max(0, len(kept) - MAX_TOKENS + 1)]:
            _GRANTS.delete(key)
        _GRANTS.put(_digest(token), asdict(grant))
    return token, grant.expires


def redeem(instance: str, token: str, now: float | None = None) -> Grant | None:
    """The grant of `token` for `instance`, used up now; None when it is unknown, used, expired or for another
    instance (it is used up in every case: a token is tried once)."""
    if not token or len(token) > 200:
        return None
    found = _GRANTS.take(_digest(token), now)
    if found is None:  # (expired: gone as well)
        return None
    grant = Grant(**{field: found[field] for field in Grant.__dataclass_fields__})
    if grant.instance != instance:
        return None
    return grant if (time.time() if now is None else now) < grant.expires else None


def create_session(name: str, uid: int) -> str:
    """Write an Odoo session for user `uid` into the instance's session store; return its id."""
    databases = odoo.databases(name)
    if not databases:
        raise ConnectError("This instance has no database.")
    cmd = [
        "docker", "exec", "-i", "-e", f"SUDOO_CONNECT_UID={int(uid)}", store.container(name, "odoo"),
        "odoo", "shell", "-d", databases[0], "--no-http", "--log-level=warn",
    ]  # fmt: skip
    try:
        result = subprocess.run(cmd, input=SESSION_SCRIPT, capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as error:
        raise ConnectError("Odoo did not answer in time. Try again in a moment.") from error
    for line in result.stdout.splitlines():
        if line.startswith("SUDOO_CONNECT_SID="):
            sid = line.split("=", 1)[1].strip()
            if sid and all(c.isalnum() or c in "-_" for c in sid):
                return sid
        if line.startswith("SUDOO_CONNECT_ERROR="):
            raise ConnectError("This Odoo user is no longer an active internal user.")
    if "No such container" in result.stderr or "is not running" in result.stderr:
        raise ConnectError("Odoo is not running. Start the instance first.")
    raise ConnectError("Odoo could not open a session for this user.")


def connect(name: str, token: str, ip: str | None) -> str:
    """Use up `token` for instance `name` and return the id of a new Odoo session for its user; raise ConnectError
    (with a message for the person) when the token is not valid or Odoo cannot open the session. Audit-logged."""
    grant = redeem(name, token)
    if grant is None:
        audit.record("visitor", ip, "connect as refused (link used, expired or unknown)", instance=name, ok=False)
        raise ConnectError("This link has expired or was used already. Ask the panel for a new one.")
    try:
        sid = create_session(name, grant.uid)
    except ConnectError as error:
        audit.record(grant.user, ip, "connect as failed", instance=name, ok=False, detail=f"{grant.login}: {error}")
        raise
    audit.record(grant.user, ip, "connected as an Odoo user", instance=name, detail=f"{grant.login} (id {grant.uid})")
    return sid


def odoo_users(name: str, now: float | None = None) -> list[dict[str, object]]:
    """The instance's active internal Odoo users: [{"id", "login", "name"}], read again after
    USERS_CACHE_SECONDS. Raises ConnectError when the database cannot be read."""
    now = time.time() if now is None else now
    cached = _USERS.get(name)
    if cached and now - cached[0] < USERS_CACHE_SECONDS:
        return cached[1]
    databases = odoo.databases(name)
    if not databases:
        raise ConnectError("This instance has no database.")
    cmd = ["docker", "exec", store.container(name, "db"), "psql", "-X", "-U", "odoo", "-d", databases[0], "-tA",
           "-F", "\x1f", "-c", USERS_SQL]  # fmt: skip
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as error:
        raise ConnectError("The database did not answer in time.") from error
    if result.returncode != 0:
        raise ConnectError("The database is not running. Start the instance first.")
    users = parse_users(result.stdout)
    _USERS[name] = (now, users)
    return users


def parse_users(text: str) -> list[dict[str, object]]:
    """Rows of USERS_SQL (fields separated by U+001F) as [{"id", "login", "name"}]."""
    found: list[dict[str, object]] = []
    for line in text.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3 and parts[0].isdigit():
            found.append({"id": int(parts[0]), "login": parts[1], "name": parts[2]})
    return found


def forget(name: str) -> None:
    """Read the instance's users again next time (after a restore or an import)."""
    _USERS.pop(name, None)
