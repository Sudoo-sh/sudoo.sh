"""The panel's GitHub App: read-only access to every repository of the GitHub accounts it is installed on.

One GitHub App per panel, registered from a manifest (GitHub's "App Manifest" flow) so nothing is typed by hand:

    1. A super admin presses Connect GitHub. The panel builds a manifest (name, addresses, read-only permissions,
       push/create/delete events) and the browser posts it to GitHub, with a random `state` the panel remembers.
    2. GitHub shows the new app, the super admin presses Create, and GitHub sends the browser back with a one-time
       `code` (valid one hour) and the same `state`.
    3. The panel checks the state and exchanges the code (POST /app-manifests/{code}/conversions) for the app's id,
       slug, client id and secret, webhook secret and private key. The secrets are stored encrypted (vault.py).
    4. The super admin installs the app on a personal account or an organisation (all or selected repositories).
       GitHub sends the browser back with the installation id, which the panel checks with GitHub and records.
       Several installations (a personal account and organisations) work side by side.

Access to code: for each installation the panel asks GitHub for an installation access token (one hour, read-only
contents and metadata), signing the request with a JWT made from the app's private key (RS256, issued 60 seconds
in the past, valid 9 minutes). Tokens are cached until five minutes before they expire, encrypted, in a file in
MEMORY_DIR, so git's credential helper (git_credential.py), which runs as a separate process, shares them. Git
gets a token only through that helper: never in a repository address, .git/config or a log.

Stored in <ODOO_ROOT>/manager/keys/github-app.json (mode 600), secrets encrypted with labels "github-app:<field>".
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from sudoo.config import settings
from sudoo.core import vault

API = "https://api.github.com"  # changed by the tests, which run a fake GitHub
WEB = "https://github.com"
API_VERSION = "2022-11-28"
PERMISSIONS = {"contents": "read", "metadata": "read"}
EVENTS = ["push", "create", "delete"]
HOOK_PATH = "/hooks/github-app"
SECRET_FIELDS = ("pem", "webhook_secret", "client_secret")
NAME_LIMIT = 34  # GitHub refuses longer app names
ORG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
TOKEN_MARGIN = 300  # a cached token is used until five minutes before it expires
REPOSITORY_CACHE_SECONDS = 60

_lock = threading.Lock()
_repositories: dict[int, tuple[float, list[dict[str, Any]]]] = {}


class GitHubError(Exception):
    """GitHub refused or could not be reached; the message is meant for people (it never contains a secret)."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- talking to GitHub


def request(
    method: str, path: str, *, bearer: str | None = None, body: dict[str, Any] | None = None, timeout: float = 20
) -> Any:
    """One GitHub REST call; the decoded JSON answer. Raises GitHubError with GitHub's message on failure."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION, "User-Agent": "Sudoo.sh"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    call = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(call, timeout=timeout) as answer:  # noqa: S310 - API is an https address
            raw = answer.read()
    except urllib.error.HTTPError as error:
        try:
            message = str(json.loads(error.read()).get("message", ""))
        except (ValueError, AttributeError):
            message = ""
        raise GitHubError(f"GitHub answered {error.code}{': ' + message if message else ''}.", error.code) from None
    except (OSError, ValueError) as error:
        raise GitHubError(f"GitHub cannot be reached ({error.__class__.__name__}).") from None
    return json.loads(raw) if raw else None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(pem: bytes, issuer: str, now: float | None = None) -> str:
    """A JSON Web Token for the app: RS256, issued 60 s in the past (clock drift), expiring 9 minutes from now."""
    issued = int(now if now is not None else time.time()) - 60
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"iat": issued, "exp": issued + 600, "iss": issuer}, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise GitHubError("The stored private key of the GitHub App is not an RSA key.", 500)
    signature = key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64(signature)}"


# --------------------------------------------------------------------------- the stored app


def _file() -> Path:
    return settings.keys_dir / "github-app.json"


def _read() -> dict[str, Any] | None:
    try:
        data = json.loads(_file().read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id") else None


def _write(data: dict[str, Any]) -> None:
    settings.keys_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    vault.write_private(_file(), json.dumps(data, indent=1))


def _secret(data: dict[str, Any], field: str) -> str:
    return vault.decrypt(data[field], f"github-app:{field}")


def connected() -> bool:
    return _read() is not None


def settings_url(data: dict[str, Any]) -> str:
    """The app's settings page on GitHub (where it is deleted)."""
    owner = data.get("owner") or {}
    if owner.get("type") == "Organization":
        return f"{WEB}/organizations/{owner.get('login')}/settings/apps/{data['slug']}"
    return f"{WEB}/settings/apps/{data['slug']}"


def install_url(data: dict[str, Any]) -> str:
    return f"{WEB}/apps/{data['slug']}/installations/new"


def describe() -> dict[str, Any] | None:
    """The app without its secrets: {"id", "slug", "name", "owner", "html_url", "settings_url", "install_url",
    "hook_url", "created", "installations": [...]}, or None when no app is connected."""
    data = _read()
    if data is None:
        return None
    public = {k: v for k, v in data.items() if k not in SECRET_FIELDS and k != "installations"}
    public.update(
        settings_url=settings_url(data),
        install_url=install_url(data),
        installations=sorted(data.get("installations", {}).values(), key=lambda i: str(i["account"]).lower()),
    )
    return public


def app_jwt() -> str:
    data = _read()
    if data is None:
        raise GitHubError("No GitHub App is connected.", 409)
    return make_jwt(_secret(data, "pem").encode(), str(data.get("client_id") or data["id"]))


def webhook_secret() -> str | None:
    data = _read()
    return _secret(data, "webhook_secret") if data else None


# --------------------------------------------------------------------------- manifest flow


def app_name(host: str) -> str:
    """'Sudoo.sh <first part of the panel's host>-<4 random characters>', at most 34 characters (GitHub's limit).
    App names are unique on all of GitHub, hence the random part; it can be changed on GitHub's page."""
    label = host.split(":")[0].split(".")[0] if host and not host.endswith(".trycloudflare.com") else "panel"
    suffix = secrets.token_hex(2)
    return f"Sudoo.sh {label[: NAME_LIMIT - len('Sudoo.sh ') - 5]}-{suffix}"


def manifest(panel_url: str, hook_base: str, host: str) -> dict[str, Any]:
    """The GitHub App manifest: read-only, private, push/create/delete events to the panel's webhook."""
    panel_url, hook_base = panel_url.rstrip("/"), hook_base.rstrip("/")
    return {
        "name": app_name(host),
        "url": panel_url,
        "description": "Read-only access for the Sudoo.sh panel: it downloads branches to run them as Odoo instances.",
        "hook_attributes": {"url": hook_base + HOOK_PATH, "active": True},
        "redirect_url": f"{panel_url}/github/app/created",
        "setup_url": f"{panel_url}/github/app/installed",
        "setup_on_update": True,
        "public": False,
        "request_oauth_on_install": False,
        "default_permissions": dict(PERMISSIONS),
        "default_events": list(EVENTS),
    }


def new_app_url(organization: str | None, state: str) -> str:
    """Where the browser posts the manifest: the personal account's or an organisation's new-app page."""
    if organization:
        if not ORG_RE.match(organization):
            raise GitHubError("This is not a GitHub organisation name.", 400)
        return f"{WEB}/organizations/{organization}/settings/apps/new?state={state}"
    return f"{WEB}/settings/apps/new?state={state}"


def convert(code: str) -> dict[str, Any]:
    """Exchange the one-time code for the new app's credentials, store them encrypted, return describe()."""
    if not CODE_RE.match(code):
        raise GitHubError("The code GitHub sent back is not valid.", 400)
    if connected():
        raise GitHubError("A GitHub App is already connected. Disconnect it first.", 409)
    answer = request("POST", f"/app-manifests/{code}/conversions")
    if not isinstance(answer, dict) or not answer.get("id") or not answer.get("pem"):
        raise GitHubError("GitHub's answer did not contain the new app.")
    owner = answer.get("owner") or {}
    data: dict[str, Any] = {
        "id": int(answer["id"]),
        "slug": str(answer.get("slug", "")),
        "name": str(answer.get("name", "")),
        "client_id": str(answer.get("client_id", "")),
        "html_url": str(answer.get("html_url", "")),
        "owner": {"login": str(owner.get("login", "")), "type": str(owner.get("type", "User"))},
        "created": round(time.time()),
        "hook_url": None,
        "installations": {},
    }
    for field in SECRET_FIELDS:
        if answer.get(field):
            data[field] = vault.encrypt(str(answer[field]), f"github-app:{field}")
    _write(data)
    return describe() or {}


def set_hook_url(url: str) -> None:
    """Remember the webhook address GitHub has (set by the manifest or by update_hook_url)."""
    data = _read()
    if data is not None:
        data["hook_url"] = url
        _write(data)


def update_hook_url(base: str) -> bool:
    """Point the app's webhook at `base`/hooks/github-app when it changed (Cloudflare quick tunnels change the
    panel's address on restart). True when GitHub was told the new address."""
    data = _read()
    url = base.rstrip("/") + HOOK_PATH
    if data is None or data.get("hook_url") == url:
        return False
    request("PATCH", "/app/hook/config", bearer=app_jwt(), body={"url": url, "content_type": "json"})
    set_hook_url(url)
    return True


def disconnect() -> dict[str, Any] | None:
    """Forget the app: its credentials, installations and cached tokens. Returns what it was (for the links to
    uninstall and delete it on GitHub), or None."""
    data = describe()
    _file().unlink(missing_ok=True)
    _token_file().unlink(missing_ok=True)
    _repositories.clear()
    return data


# --------------------------------------------------------------------------- installations


def _installation_record(found: dict[str, Any]) -> dict[str, Any]:
    account = found.get("account") or {}
    return {
        "id": int(found["id"]),
        "account": str(account.get("login", "")),
        "account_type": str(account.get("type", "User")),
        "repository_selection": str(found.get("repository_selection", "selected")),
        "settings_url": str(found.get("html_url", "")),
        "suspended": bool(found.get("suspended_at")),
        "added": round(time.time()),
    }


def add_installation(installation_id: int) -> dict[str, Any]:
    """Record an installation after GitHub confirms it belongs to this app (an unknown id answers 404)."""
    record = record_installation(request("GET", f"/app/installations/{installation_id}", bearer=app_jwt()))
    if record is None:
        raise GitHubError("No GitHub App is connected.", 409)
    return record


def record_installation(found: dict[str, Any]) -> dict[str, Any] | None:
    """Record or update an installation as described by GitHub (a webhook's "installation" object)."""
    record = _installation_record(found)
    with _lock:
        data = _read()
        if data is None:
            return None
        known = data.setdefault("installations", {})
        record["added"] = known.get(str(record["id"]), {}).get("added", record["added"])
        known[str(record["id"])] = record
        _write(data)
    _repositories.pop(record["id"], None)
    return record


def forget_repositories() -> None:
    """Drop the cached repository lists (repositories were added to or removed from an installation)."""
    _repositories.clear()


def sync_installations() -> list[dict[str, Any]]:
    """Replace the recorded installations with the ones GitHub lists (installed or removed on GitHub itself)."""
    jwt = app_jwt()
    found: list[dict[str, Any]] = []
    for number in range(1, 11):
        page = request("GET", f"/app/installations?per_page=100&page={number}", bearer=jwt)
        found += page
        if len(page) < 100:
            break
    with _lock:
        data = _read()
        if data is None:
            raise GitHubError("No GitHub App is connected.", 409)
        data["installations"] = {str(i["id"]): _installation_record(i) for i in found}
        _write(data)
    _repositories.clear()
    return sorted(data["installations"].values(), key=lambda i: str(i["account"]).lower())


def remove_installation(installation_id: int) -> bool:
    with _lock:
        data = _read()
        if data is None or str(installation_id) not in data.get("installations", {}):
            return False
        del data["installations"][str(installation_id)]
        _write(data)
    _repositories.pop(installation_id, None)
    _forget_token(installation_id)
    return True


def installations() -> list[dict[str, Any]]:
    data = _read()
    return list(data.get("installations", {}).values()) if data else []


def installation_for(owner: str) -> int | None:
    """The installation that covers the GitHub account `owner` (user or organisation), or None."""
    for found in installations():
        if found["account"].lower() == owner.lower() and not found.get("suspended"):
            return int(found["id"])
    return None


# --------------------------------------------------------------------------- installation access tokens


def _token_file() -> Path:
    """Cached tokens, shared with git's credential helper; kept in memory (MEMORY_DIR), encrypted."""
    tag = hashlib.sha256(str(settings.odoo_root).encode()).hexdigest()[:12]
    return settings.memory_dir / f"sudoo-github-tokens-{tag}.json"


def _cached_tokens() -> dict[str, Any]:
    try:
        cached = json.loads(_token_file().read_text())
    except (OSError, ValueError):
        return {}
    return cached if isinstance(cached, dict) else {}


def _store_tokens(cached: dict[str, Any]) -> None:
    # when another process writes at the same moment, the token is simply asked for again next time
    with contextlib.suppress(OSError):
        vault.write_private(_token_file(), json.dumps(cached))


def _forget_token(installation_id: int) -> None:
    with _lock:
        cached = _cached_tokens()
        if cached.pop(str(installation_id), None) is not None:
            _store_tokens(cached)


def installation_token(installation_id: int, now: float | None = None) -> str:
    """A read-only token for the installation's repositories, valid for at least five more minutes."""
    now = now if now is not None else time.time()
    label = f"github-app-token:{installation_id}"
    with _lock:
        cached = _cached_tokens()
        entry = cached.get(str(installation_id))
        if isinstance(entry, dict) and float(entry.get("expires", 0)) - TOKEN_MARGIN > now:
            try:
                return vault.decrypt(str(entry["token"]), label)
            except vault.VaultError:
                pass
        answer = request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            bearer=app_jwt(),
            body={"permissions": PERMISSIONS},
        )
        token = str(answer["token"])
        expires = _parse_time(str(answer.get("expires_at", ""))) or now + 3600
        cached = {k: v for k, v in cached.items() if float(v.get("expires", 0)) > now}  # drop expired ones
        cached[str(installation_id)] = {"token": vault.encrypt(token, label), "expires": expires}
        _store_tokens(cached)
        return token


def _parse_time(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def token_for(owner: str) -> str | None:
    """A token for the repositories of the GitHub account `owner`, or None when no installation covers it."""
    installation = installation_for(owner)
    return installation_token(installation) if installation is not None else None


# --------------------------------------------------------------------------- repositories and branches


def _repository(found: dict[str, Any], installation: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": str(found["full_name"]),
        "url": f"{WEB}/{found['full_name']}",
        "private": bool(found.get("private")),
        "default_branch": str(found.get("default_branch") or "main"),
        "description": str(found.get("description") or ""),
        "pushed_at": found.get("pushed_at"),
        "account": installation["account"],
    }


def repositories(query: str = "") -> list[dict[str, Any]]:
    """Repositories readable through the installations, newest push first, filtered by `query` (in the name)."""
    now = time.time()
    found: list[dict[str, Any]] = []
    for installation in installations():
        if installation.get("suspended"):
            continue
        number = int(installation["id"])
        cached = _repositories.get(number)
        if cached is None or now - cached[0] > REPOSITORY_CACHE_SECONDS:
            token = installation_token(number)
            listed: list[dict[str, Any]] = []
            for page in range(1, 21):
                answer = request("GET", f"/installation/repositories?per_page=100&page={page}", bearer=token)
                batch = answer.get("repositories", [])
                listed += [_repository(r, installation) for r in batch]
                if len(batch) < 100:
                    break
            cached = (now, listed)
            _repositories[number] = cached
        found += cached[1]
    words = query.lower().split()
    chosen = [r for r in found if all(w in r["full_name"].lower() for w in words)]
    return sorted(chosen, key=lambda r: str(r.get("pushed_at") or ""), reverse=True)


def branches(full_name: str) -> tuple[list[str], str] | None:
    """(branches, default branch) through the API, or None when no installation covers the repository's owner."""
    owner = full_name.split("/")[0]
    token = token_for(owner)
    if token is None:
        return None
    default = str(request("GET", f"/repos/{full_name}", bearer=token).get("default_branch") or "main")
    names: list[str] = []
    for page in range(1, 31):
        batch = request("GET", f"/repos/{full_name}/branches?per_page=100&page={page}", bearer=token)
        names += [str(b["name"]) for b in batch]
        if len(batch) < 100:
            break
    return sorted(names), default


# --------------------------------------------------------------------------- webhook signatures


def signature_valid(body: bytes, header: str, secret: str | None = None) -> bool:
    """X-Hub-Signature-256 is HMAC-SHA256 of the body with the app's webhook secret."""
    secret = secret if secret is not None else webhook_secret()
    if not secret or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
