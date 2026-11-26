"""Panel accounts, password hashing and sign-in throttling.

Passwords are never stored, only checked, so they are hashed (one-way) rather than encrypted:

    stored hash = scrypt( HMAC-SHA256(pepper, password), random salt )

scrypt is slow and memory-hard on purpose, so guessing passwords from a stolen users.json is expensive. The pepper
is derived from the vault key (vault.py), which is kept outside users.json: without it, the hashes cannot even be
attacked. Hashes made before the pepper existed are replaced the next time their owner signs in.

Accounts are stored in the panel's storage (storage/, collection "users"). Each account has a session_version:
changing or resetting its password, or deleting it, invalidates all of its sessions. The version is a random number,
chosen when the account is created and again whenever it changes, so an account created later under the same user
name never accepts a session of the deleted one.

Roles and their permissions: roles.py. There are no default accounts: the first account, a super admin, is
created on the panel's first-run page with the setup code that the installer prints (complete_setup).

People sign in with their e-mail address (or the user name, which is derived from the address when an account is
created and stays the account's key). An account created by someone else gets a password typed by that person or a
generated one; either way its owner must choose their own at the first sign-in, unless the creator unticks that.

Password rules (password_problem) follow NIST SP 800-63B: length rather than forced character classes (12 to 128
characters, any characters, spaces and pasting allowed), and refusal of what is guessed first: common passwords,
keyboard and alphabet sequences, repeated characters, and the person's own name or address.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Mapping
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
    "sudoo",
    "starwars",
    "whatever",
    "superman",
    "batman",
    "shadow",
    "michael",
    "jordan",
    "hello",
    "freedom",
    "summer",
    "winter",
    "spring",
    "autumn",
    "computer",
    "internet",
    "default",
    "temp",
    "test",
}
SEQUENCES = (  # a password containing five characters in a row of one of these (either way) is refused
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "1qaz2wsx3edc",
    "qazwsxedc",
)

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


_DUMMY_HASH: list[str] = []  # unknown users take as long to reject as known ones
_DUMMY_LOCK = threading.Lock()


def dummy_hash() -> str:
    """A hash of a random password, checked when the account does not exist. Made once, in the background when the
    panel starts (background.py) rather than while it imports: hashing is slow on purpose."""
    with _DUMMY_LOCK:
        if not _DUMMY_HASH:
            _DUMMY_HASH.append(hash_password(secrets.token_hex(16)))
        return _DUMMY_HASH[0]


def generate_password() -> str:
    """24 random characters (144 bits), shown once to the person who receives it."""
    return secrets.token_urlsafe(18)


def _has_sequence(lowered: str) -> bool:
    for sequence in SEQUENCES:
        for text in (sequence, sequence[::-1]):
            if any(text[i : i + 5] in lowered for i in range(len(text) - 4)):
                return True
    return False


def password_problem(password: str, username: str, email: str | None = None, names: tuple[str, ...] = ()) -> str | None:
    """Why `password` is not acceptable for this person, or None."""
    lowered = password.lower()
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"The password must be at least {MIN_PASSWORD_LENGTH} characters long."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"The password must be at most {MAX_PASSWORD_LENGTH} characters long."
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        return "The password must not contain control characters."
    personal = [username, (email or "").split("@")[0], *names]
    if any(len(part) >= 3 and part.lower() in lowered for part in personal):
        return "The password must not contain your name, user name or e-mail address."
    if any(word in lowered for word in COMMON_PASSWORDS):
        return "The password contains a common word that is guessed first. Choose another one."
    if _has_sequence(lowered):
        return "The password contains a sequence such as 12345 or qwerty. Choose another one."
    if re.search(r"(.)\1\1\1", password):
        return "The password repeats a character four times in a row. Choose another one."
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
        verify_password(password, dummy_hash())
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


def find(login: str) -> str | None:
    """The user name of the account that signs in as `login`: its e-mail address or its user name."""
    login = login.strip().lower()
    if "@" in login:
        return next((name for name, user in all_users().items() if str(user.get("email") or "").lower() == login), None)
    return login if USERNAME_RE.match(login) and get(login) else None


def email_taken(email: str | None, other_than: str | None = None) -> bool:
    """Whether another account already uses this e-mail address."""
    if not email:
        return False
    wanted = email.strip().lower()
    return any(
        str(user.get("email") or "").lower() == wanted for name, user in all_users().items() if name != other_than
    )


def username_for(email: str) -> str:
    """A free user name made from an e-mail address: jane.doe@example.com -> jane.doe (-2, -3, ...)."""
    local = re.sub(r"[^a-z0-9._-]", "", email.split("@")[0].lower()).strip("._-")
    if not local or not local[0].isalpha():
        local = "user" + (f"-{local}" if local else "")
    base = (local + "xx")[: max(3, len(local))][:28]
    users = all_users()
    candidate, number = base, 1
    while candidate in users:
        number += 1
        candidate = f"{base}-{number}"
    return candidate


def new_session_version(current: int | None = None) -> int:
    """A random session version (63 bits), different from `current`. Random rather than counted from 1, so a
    session of a deleted account never matches an account created later under the same user name."""
    while True:
        version = secrets.randbits(63)
        if version != current:
            return version


LANGUAGES = ("en", "ar")
LANGUAGE_FIELD = "language"  # the account's own choice of the web app's language, or absent (the server's default)


def own_language(account: dict[str, Any]) -> str | None:
    choice = account.get(LANGUAGE_FIELD)
    return choice if choice in LANGUAGES else None


def language(account: dict[str, Any]) -> str:
    """The web app's language for this account: its own choice, otherwise the server's default (DEFAULT_LANGUAGE)."""
    return own_language(account) or settings.default_language


TOURS_FIELD = "tours"  # the account's guided-tour state: {"seen": [tour ids], "tips": bool}, or absent (nothing seen)
TOUR_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
TOURS_SEEN_MAX = 50


def tours(account: dict[str, Any]) -> dict[str, Any]:
    """What the account has seen of the guided tours and whether it wants tips (on unless it turned them off)."""
    raw = account.get(TOURS_FIELD)
    raw = raw if isinstance(raw, dict) else {}
    seen = raw.get("seen")
    seen = [item for item in seen if isinstance(item, str) and TOUR_ID.match(item)] if isinstance(seen, list) else []
    return {"seen": seen[:TOURS_SEEN_MAX], "tips": raw.get("tips") is not False}


def tours_problem(value: Any) -> str | None:
    """Why a tour change cannot be kept, or None. Keys (any of them): "mark" (tour names seen: added to the list),
    "seen" (only [] : forget every tour seen) and "tips" (true or false)."""
    if not isinstance(value, dict) or not value or set(value) - {"mark", "seen", "tips"}:
        return 'tours must be an object with "mark", "seen" and/or "tips".'
    if "tips" in value and not isinstance(value["tips"], bool):
        return "tips must be true or false."
    if "seen" in value and value["seen"] != []:
        return 'seen can only be [] (show every tour again); use "mark" to add tours seen.'
    if "mark" in value:
        mark = value["mark"]
        if not isinstance(mark, list) or len(mark) > TOURS_SEEN_MAX:
            return f"mark must be a list of at most {TOURS_SEEN_MAX} tour names."
        if not all(isinstance(i, str) and TOUR_ID.match(i) for i in mark):
            return "tour names are lower case letters, digits and hyphens, up to 40."
    return None


def update_tours(username: str, change: dict[str, Any]) -> Document:
    """Apply a checked tour change in one stored change (read and write together), touching nothing else of the
    account. "mark" adds to the tours seen as stored now, so two browsers marking different tours at once keep both;
    "seen": [] forgets them all (applied first, then any "mark"); "tips" sets the choice."""

    def apply(user: Document | None) -> Document:
        if user is None:
            raise ValueError("No such account.")
        now = tours(user)
        seen = [] if "seen" in change else list(now["seen"])
        seen += [name for name in change.get("mark", []) if name not in seen]
        return {**user, TOURS_FIELD: {"seen": seen[:TOURS_SEEN_MAX], "tips": change.get("tips", now["tips"])}}

    return storage().change(USERS, username, apply) or {}


def display_name(username: str, account: dict[str, Any]) -> str:
    full = " ".join(part for part in (account.get("first_name"), account.get("last_name")) if part)
    return full or username


def create(
    username: str, role: str, password: str | None = None, must_change: bool | None = None, **fields: Any
) -> str:
    """Create an account and return its password: the one given, or a generated one (shown to the user once).
    With must_change (the default for a generated password) its owner must replace it at the first sign-in.
    Accounts of an external sign-in (auth plugin) are created with password="" and have no password here."""
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
    if email_taken(fields.get("email")):
        raise ValueError("Another account already uses this e-mail address.")
    generated = password is None
    password = generate_password() if password is None else password
    if must_change is None:
        must_change = generated

    def add(existing: Document | None) -> Document:
        if existing is not None:
            raise ValueError("An account with this name already exists.")
        account: Document = {
            "role": role,
            "session_version": new_session_version(),
            "created": time.time(),
            "password_changed": time.time(),
            **{key: value for key, value in fields.items() if value not in (None, "")},
        }
        if must_change and password:
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
        names = tuple(str(profile.get(key) or "") for key in ("first_name", "last_name"))
        problem = password_problem(password, username, profile.get("email"), names)
        if problem:
            raise ValueError(problem)
        create(username, "super_admin", password, must_change=False, **profile)
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
        changed = {**user, "hash": new_hash, "session_version": new_session_version(user["session_version"])}
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
        return {**user, "session_version": new_session_version(user["session_version"])}

    return int((storage().change(USERS, username, bump) or {})["session_version"])


def is_disabled(account: Mapping[str, Any] | None) -> bool:
    return bool(account and account.get("disabled"))


def disable(username: str, by: str, reason: str) -> bool:
    """Disable an account: it cannot sign in, its sessions end now, and the API tokens it owns stop working. Its
    data, its role and its project access stay, for when it is enabled again. False when it was disabled already.
    The last active super admin cannot be disabled (the same guard as deleting it). The reason is required and kept."""
    reason = reason.strip()
    if not reason:
        raise ValueError("Say why: the reason is kept for the other administrators.")
    with _LOCK:
        users = all_users()
        account = users.get(username)
        if account is None:
            raise ValueError("No such account.")
        if is_disabled(account):
            return False
        active = [n for n, u in users.items() if u["role"] == "super_admin" and not is_disabled(u)]
        if account["role"] == "super_admin" and active == [username]:
            raise ValueError("The last active super admin cannot be disabled.")
        update(username, disabled=True, disabled_at=time.time(), disabled_by=by, disabled_reason=reason[:300])
        end_sessions(username)  # a new session version: every cookie of the account stops working
        return True


def enable(username: str, reason: str) -> str | None:
    """Enable a disabled account again (it signs in as before; its old sessions stay ended). Returns the reason it was
    disabled for ("" when none was recorded), read under the same lock as the change, or None when it was not disabled.
    A reason is required (the route writes it in the audit log, next to the reason it was disabled)."""
    if not reason.strip():
        raise ValueError("Say why this account is enabled again: the reason is written in the audit log.")
    with _LOCK:
        account = get(username)
        if account is None:
            raise ValueError("No such account.")
        if not is_disabled(account):
            return None
        was = str(account.get("disabled_reason") or "")
        update(username, disabled=None, disabled_at=None, disabled_by=None, disabled_reason=None)
        return was


def record_sign_in(username: str) -> None:
    _change(username, last_login=time.time())


# --------------------------------------------------------------------------- ended sessions

ENDED_SESSIONS = "ended_sessions"


def end_session(sid: str, started: float) -> None:
    """Remember that the session `sid` (started at `started`) was signed out, so a copy of its cookie no longer
    works. Kept until the session would have ended anyway (SESSION_MAX_HOURS); older records are removed here."""
    store = storage()
    store.put(ENDED_SESSIONS, sid, {"started": started, "ended": time.time()})
    oldest = time.time() - settings.session_max_hours * 3600
    for key, record in store.all(ENDED_SESSIONS).items():
        if record.get("started", 0) < oldest:
            store.delete(ENDED_SESSIONS, key)


def session_ended(sid: str) -> bool:
    """Whether the session `sid` was signed out."""
    return storage().get(ENDED_SESSIONS, sid) is not None


# --------------------------------------------------------------------------- sign-in throttling


def rate_key(ip: str) -> str:
    """The address a rate limit counts: an IPv4 address as it is, an IPv6 address as its /64 (one subscriber usually
    holds a whole /64, so the full address would let one person count as billions); an IPv4-mapped IPv6 address as
    its IPv4 address. Anything that is not an address is kept as it is."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network((address, 64), strict=False))
    return str(address)


class SignInThrottle:
    """Refuses sign-ins after too many failures: per account and address, and per address (an IPv6 address counts as its
    /64, rate_key).

    Only failures are kept, and only for WINDOW_SECONDS: asking about an address that never failed stores nothing,
    so a flood of sign-ins under made-up names or from many addresses cannot fill the panel's memory."""

    WINDOW_SECONDS = 15 * 60
    MAX_PER_ACCOUNT_AND_ADDRESS = 5
    MAX_PER_ADDRESS = 20

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[tuple[str, str | None], deque[float]] = {}
        self._swept = 0.0

    def _recent(self, key: tuple[str, str | None], now: float) -> int:
        failures = self._failures.get(key)
        if failures is None:
            return 0
        while failures and now - failures[0] > self.WINDOW_SECONDS:
            failures.popleft()
        if not failures:
            del self._failures[key]
            return 0
        return len(failures)

    def _sweep(self, now: float) -> None:
        """Forget every key without a recent failure (at most once a minute)."""
        if now - self._swept < 60:
            return
        self._swept = now
        for key in list(self._failures):
            self._recent(key, now)

    def seconds_to_wait(self, ip: str, username: str) -> int:
        ip = rate_key(ip)
        now = time.time()
        with self._lock:
            self._sweep(now)
            for key, limit in (((ip, username), self.MAX_PER_ACCOUNT_AND_ADDRESS), ((ip, None), self.MAX_PER_ADDRESS)):
                if self._recent(key, now) >= limit:
                    return int(self.WINDOW_SECONDS - (now - self._failures[key][0])) + 1
        return 0

    def failed(self, ip: str, username: str) -> None:
        ip = rate_key(ip)
        now = time.time()
        with self._lock:
            for key in ((ip, username), (ip, None)):
                self._failures.setdefault(key, deque()).append(now)

    def succeeded(self, ip: str, username: str) -> None:
        ip = rate_key(ip)
        with self._lock:
            self._failures.pop((ip, username), None)


THROTTLE = SignInThrottle()
