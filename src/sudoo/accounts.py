"""Panel accounts, password hashing and sign-in throttling.

Passwords are never stored, only checked, so they are hashed (one-way) rather than encrypted:

    stored hash = scrypt( HMAC-SHA256(pepper, password), random salt )

scrypt is slow and memory-hard on purpose, so guessing passwords from a stolen users.json is expensive. The pepper
is derived from the vault key (vault.py), which is kept outside users.json: without it, the hashes cannot even be
attacked. Hashes made before the pepper existed are replaced the next time their owner signs in.

Accounts are stored in the panel's storage (storage/, collection "users"). Each account has a session_version:
changing or resetting its password, or deleting it, invalidates all of its sessions.

Roles:
    admin     everything
    operator  the sync operator: code updates, submodules, restart and start instances
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Any

from sudoo.config import settings
from sudoo.core import vault
from sudoo.storage import Document, storage

ROLES = {"admin": "Administrator", "operator": "Sync operator"}
DEFAULT_ACCOUNTS = (("admin", "admin"), ("sync", "operator"))
USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{2,31}$")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
COMMON_PASSWORDS = {  # lower case; a password containing one of these is refused
    "password",
    "passw0rd",
    "123456",
    "qwerty",
    "azerty",
    "letmein",
    "welcome",
    "admin",
    "administrator",
    "iloveyou",
    "monkey",
    "dragon",
    "football",
    "baseball",
    "sunshine",
    "princess",
    "trustno1",
    "abc123",
    "odoo",
    "changeme",
    "secret",
    "master",
    "login",
}

_SCRYPT = {"n": 2**15, "r": 8, "p": 1}  # 32 MB of memory and about 0.1 s per hash
_MAXMEM = 64 * 1024 * 1024
_ALGORITHM = "scrypt-peppered"
_LOCK = threading.Lock()


def session_secret() -> str:
    """Key that signs session cookies, created once."""
    if not settings.secret_file.exists():
        vault.write_private(settings.secret_file, secrets.token_hex(64))
    return settings.secret_file.read_text().strip()


# --------------------------------------------------------------------------- passwords


def _pepper(password: str) -> bytes:
    pepper = hmac.new(vault.key(), b"password-pepper", hashlib.sha256).digest()
    return hmac.new(pepper, password.encode(), hashlib.sha256).digest()


def _scrypt(data: bytes, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(data, salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(_pepper(password), salt, **_SCRYPT)
    return f"{_ALGORITHM}${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """True when `password` matches the stored hash (compared in constant time)."""
    algorithm, n, r, p, salt, digest = stored.split("$")
    data = _pepper(password) if algorithm == _ALGORITHM else password.encode()  # "scrypt": made before the pepper
    candidate = _scrypt(data, bytes.fromhex(salt), int(n), int(r), int(p))
    return hmac.compare_digest(candidate.hex(), digest)


def _needs_rehash(stored: str) -> bool:
    algorithm, n, r, p, *_ = stored.split("$")
    return algorithm != _ALGORITHM or (int(n), int(r), int(p)) != tuple(_SCRYPT.values())


_DUMMY_HASH = hash_password(secrets.token_hex(16))  # unknown users take as long to reject as known ones


def generate_password() -> str:
    """24 random characters (144 bits), shown once to the person who receives it."""
    return secrets.token_urlsafe(18)


def password_problem(password: str, username: str) -> str | None:
    """Why `password` is not acceptable for `username`, or None."""
    lowered = password.lower()
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"The password must be at least {MIN_PASSWORD_LENGTH} characters long."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"The password must be at most {MAX_PASSWORD_LENGTH} characters long."
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        return "The password must not contain control characters."
    if username.lower() in lowered:
        return "The password must not contain the user name."
    if any(word in lowered for word in COMMON_PASSWORDS):
        return "The password contains a common word or sequence that is guessed first. Choose another one."
    if len(set(password)) < 6:
        return "The password is too simple: use at least 6 different characters."
    return None


# --------------------------------------------------------------------------- accounts


USERS = "users"


def all_users() -> dict[str, dict[str, Any]]:
    return storage().all(USERS)


def get(username: str) -> dict[str, Any] | None:
    return storage().get(USERS, username)


def _change(username: str, **values: Any) -> Document:
    """Change fields of an existing account atomically; returns the account."""

    def apply(user: Document | None) -> Document:
        if user is None:
            raise ValueError("No such account.")
        return {**user, **values}

    return storage().change(USERS, username, apply) or {}


def update(username: str, **values: Any) -> Document:
    """Change fields of an account (email, second factors, ...); None removes a field."""

    def apply(user: Document | None) -> Document:
        if user is None:
            raise ValueError("No such account.")
        merged = {**user, **values}
        return {key: value for key, value in merged.items() if value is not None}

    return storage().change(USERS, username, apply) or {}


def check_password(username: str, password: str) -> dict[str, Any] | None:
    """The account if the password is right, else None (the "local" auth plugin uses this)."""
    user = get(username)
    if not user or "hash" not in user:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user["hash"]):
        return None
    if _needs_rehash(user["hash"]):
        _change(username, hash=hash_password(password))
    return user


def create(username: str, role: str, password: str | None = None, **fields: Any) -> str:
    """Create an account and return its password: the one given, or a generated one (shown to the user once).
    Accounts of an external sign-in (auth plugin) are created with password="" and have no password here."""
    if not USERNAME_RE.match(username):
        raise ValueError(
            "User names are 3 to 32 characters: lower-case letters, digits, dots, dashes and "
            "underscores, starting with a letter."
        )
    if role not in ROLES:
        raise ValueError("Unknown role.")
    password = generate_password() if password is None else password

    def add(existing: Document | None) -> Document:
        if existing is not None:
            raise ValueError("An account with this name already exists.")
        account: Document = {
            "role": role,
            "session_version": 1,
            "created": time.time(),
            "password_changed": time.time(),
            **fields,
        }
        if password:
            account["hash"] = hash_password(password)
        return account

    storage().change(USERS, username, add)
    return password


def create_defaults() -> dict[str, str]:
    """Create the admin and sync accounts if they do not exist; return {name: password} of those created."""
    created: dict[str, str] = {}
    for username, role in DEFAULT_ACCOUNTS:
        if not get(username):
            created[username] = create(username, role)
    return created


def delete(username: str) -> None:
    """Delete an account; the last administrator cannot be deleted."""
    with _LOCK:
        users = all_users()
        if username not in users:
            raise ValueError("No such account.")
        admins = [name for name, user in users.items() if user["role"] == "admin"]
        if users[username]["role"] == "admin" and len(admins) == 1:
            raise ValueError("The last administrator account cannot be deleted.")
        storage().delete(USERS, username)


def set_password(username: str, password: str) -> int:
    """Store a new password and return the new session version (ending all other sessions)."""
    new_hash = hash_password(password)  # slow on purpose: outside the transaction

    def apply(user: Document | None) -> Document:
        if user is None:
            raise ValueError("No such account.")
        return {
            **user,
            "hash": new_hash,
            "session_version": user["session_version"] + 1,
            "password_changed": time.time(),
        }

    return int((storage().change(USERS, username, apply) or {})["session_version"])


def end_sessions(username: str) -> int:
    """End every session of the account (e.g. after its second factors changed); returns the new version."""

    def bump(user: Document | None) -> Document:
        if user is None:
            raise ValueError("No such account.")
        return {**user, "session_version": user["session_version"] + 1}

    return int((storage().change(USERS, username, bump) or {})["session_version"])


def record_sign_in(username: str) -> None:
    _change(username, last_login=time.time())


# --------------------------------------------------------------------------- sign-in throttling


class SignInThrottle:
    """Refuses sign-ins after too many failures: per account and address, and per address."""

    WINDOW_SECONDS = 15 * 60
    MAX_PER_ACCOUNT_AND_ADDRESS = 5
    MAX_PER_ADDRESS = 20

    def __init__(self) -> None:
        self._failures: defaultdict[tuple[str, str | None], deque[float]] = defaultdict(deque)

    def _recent(self, key: tuple[str, str | None], now: float) -> deque[float]:
        failures = self._failures[key]
        while failures and now - failures[0] > self.WINDOW_SECONDS:
            failures.popleft()
        return failures

    def seconds_to_wait(self, ip: str, username: str) -> int:
        now = time.time()
        for key, limit in (((ip, username), self.MAX_PER_ACCOUNT_AND_ADDRESS), ((ip, None), self.MAX_PER_ADDRESS)):
            failures = self._recent(key, now)
            if len(failures) >= limit:
                return int(self.WINDOW_SECONDS - (now - failures[0])) + 1
        return 0

    def failed(self, ip: str, username: str) -> None:
        now = time.time()
        self._failures[(ip, username)].append(now)
        self._failures[(ip, None)].append(now)

    def succeeded(self, ip: str, username: str) -> None:
        self._failures.pop((ip, username), None)


THROTTLE = SignInThrottle()
