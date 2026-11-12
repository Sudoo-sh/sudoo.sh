"""Panel accounts, password hashing and sign-in throttling.

Passwords are never stored, only checked, so they are hashed (one-way) rather than encrypted:

    stored hash = scrypt( HMAC-SHA256(pepper, password), random salt )

scrypt is slow and memory-hard on purpose, so guessing passwords from a stolen users.json is expensive. The pepper
is derived from the vault key (vault.py), which is kept outside users.json: without it, the hashes cannot even be
attacked. Hashes made before the pepper existed are replaced the next time their owner signs in.

Accounts are stored in the panel's storage (storage/, collection "users"). Each account has a session_version:
changing or resetting its password, or deleting it, invalidates all of its sessions.

Roles and their permissions: roles.py. There are no default accounts: the first account, a super admin, is
created on the panel's first-run page with the setup code that the installer prints (complete_setup).
Accounts created by someone else get a generated password and must choose their own at their first sign-in.
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
from sudoo.roles import LABELS
from sudoo.storage import Document, storage

ROLES = LABELS
PROFILE_FIELDS = ("first_name", "last_name", "email", "phone")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
PHONE_RE = re.compile(r"^\+?[0-9 ().-]{6,24}$")
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


def profile_problem(values: dict[str, Any]) -> str | None:
    """What is wrong with profile fields (first and last name, e-mail, phone), or None."""
    for key in ("first_name", "last_name"):
        value = values.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 60 or re.search(r"[\x00-\x1f<>]", value)):
            return f"The {key.replace('_', ' ')} must be text of at most 60 characters."
    email = values.get("email")
    if email and not EMAIL_RE.match(str(email)):
        return "Enter a valid e-mail address."
    phone = values.get("phone")
    if phone and not PHONE_RE.match(str(phone)):
        return "Enter a phone number with digits, spaces and an optional leading +."
    return None


def display_name(username: str, account: dict[str, Any]) -> str:
    full = " ".join(part for part in (account.get("first_name"), account.get("last_name")) if part)
    return full or username


def create(username: str, role: str, password: str | None = None, **fields: Any) -> str:
    """Create an account and return its password: the one given, or a generated one (shown to the user once, who
    must replace it at the first sign-in). Accounts of an external sign-in (auth plugin) are created with
    password="" and have no password here."""
    if not USERNAME_RE.match(username):
        raise ValueError(
            "User names are 3 to 32 characters: lower-case letters, digits, dots, dashes and "
            "underscores, starting with a letter."
        )
    if role not in ROLES:
        raise ValueError("Unknown role.")
    problem = profile_problem(fields)
    if problem:
        raise ValueError(problem)
    generated = password is None
    password = generate_password() if password is None else password

    def add(existing: Document | None) -> Document:
        if existing is not None:
            raise ValueError("An account with this name already exists.")
        account: Document = {
            "role": role,
            "session_version": 1,
            "created": time.time(),
            "password_changed": time.time(),
            **{key: value for key, value in fields.items() if value not in (None, "")},
        }
        if generated:
            account["must_change_password"] = True
        if password:
            account["hash"] = hash_password(password)
        return account

    storage().change(USERS, username, add)
    return password


# --------------------------------------------------------------------------- first run


def _setup_file() -> Any:
    return settings.manager_dir / "setup_code"


def setup_needed() -> bool:
    return not storage().all(USERS)


def setup_code() -> str | None:
    """The one-time code that allows creating the first account; made when there is no account yet. The installer
    prints it, and `sudoo.sh setup-code` shows it again. None once an account exists."""
    if not setup_needed():
        _setup_file().unlink(missing_ok=True)
        return None
    if not _setup_file().exists():
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to mistype
        code = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
        vault.write_private(_setup_file(), code)
    return _setup_file().read_text().strip()


def complete_setup(code: str, username: str, password: str, profile: dict[str, Any]) -> None:
    """Create the first account, a super admin, when `code` is the setup code. Raises ValueError saying why not."""
    with _LOCK:
        expected = setup_code()
        if expected is None:
            raise ValueError("The panel is already set up. Sign in instead.")
        if not hmac.compare_digest(code.strip().upper().replace(" ", ""), expected.replace(" ", "")):
            raise PermissionError("The setup code is not correct. Show it again with: sudoo.sh setup-code")
        problem = password_problem(password, username)
        if problem:
            raise ValueError(problem)
        create(username, "super_admin", password, **profile)
        _setup_file().unlink(missing_ok=True)


def delete(username: str) -> None:
    """Delete an account; the last administrator cannot be deleted."""
    with _LOCK:
        users = all_users()
        if username not in users:
            raise ValueError("No such account.")
        supers = [name for name, user in users.items() if user["role"] == "super_admin"]
        if users[username]["role"] == "super_admin" and len(supers) == 1:
            raise ValueError("The last super admin cannot be deleted.")
        storage().delete(USERS, username)


def set_password(username: str, password: str, must_change: bool = False) -> int:
    """Store a new password and return the new session version (ending all other sessions). With must_change
    (a password generated for someone else), its owner must replace it at the next sign-in."""
    new_hash = hash_password(password)  # slow on purpose: outside the transaction

    def apply(user: Document | None) -> Document:
        if user is None:
            raise ValueError("No such account.")
        changed = {**user, "hash": new_hash, "session_version": user["session_version"] + 1}
        changed["password_changed"] = time.time()
        changed.pop("must_change_password", None)
        if must_change:
            changed["must_change_password"] = True
        return changed

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
