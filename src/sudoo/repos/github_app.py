"""The panel's GitHub App: read-only access to the code of every repository of the GitHub accounts it is installed on,
commit statuses (a build's result shown on its commit, when the installation has Commit statuses: write), and,
when it was given Administration: write, the creation of empty repositories for new projects in organisations
(create_repository; GitHub lets an app create repositories only in organisations).

One GitHub App per panel, registered from a manifest (GitHub's "App Manifest" flow) so nothing is typed by hand:

    1. A super admin presses Connect GitHub. The panel builds a manifest (name, addresses, read-only access to
       code, Commit statuses: write, push/create/delete events) and the browser posts it to GitHub, with a random
       `state` the panel remembers.
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
CREATE_PERMISSIONS = {"administration": "write"}  # creating repositories in an organisation (projects/)
STATUS_PERMISSIONS = {"statuses": "write"}  # reporting builds as commit statuses (projects/commit_status.py)
STATUS_STATES = ("pending", "success", "failure", "error")
PULL_PERMISSIONS = {"pull_requests": "write", "issues": "write"}  # pull-request previews: events and one comment each
GUARD_PERMISSIONS = {
    "administration": "write", "contents": "write", "pull_requests": "write", "workflows": "write", "checks": "read",
}  # fmt: skip
"""The repository guard (repo_guard/): rulesets and branch protection, the pull request with the check files (a
workflow among them), reading the checks' results. Asked for only when the manifest is made with repository_guard."""
SIGN_IN_PERMISSIONS = {"email_addresses": "read", "members": "read"}
"""Sign-in with GitHub (auth/github.py): the person's verified e-mail address, and their membership of the
organisations of GITHUB_SIGN_IN_ORGS. Both are read with the person's own token, which is revoked at once."""
SIGN_IN_CALLBACK = "/login/github"
REPOSITORY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
EVENTS = ["push", "create", "delete", "pull_request"]
HOOK_PATH = "/hooks/github-app"
SECRET_FIELDS = ("pem", "webhook_secret", "client_secret")
NAME_LIMIT = 34  # GitHub refuses longer app names
ORG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
TOKEN_MARGIN = 300  # a cached token is used until five minutes before it expires
REPOSITORY_CACHE_SECONDS = 60
SYNC_SECONDS = 60  # installations are read from GitHub again at most once a minute (sync_if_stale)

_lock = threading.Lock()
_repositories: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_synced = 0.0  # when the installations were last read from GitHub


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


def manifest(
    panel_url: str, hook_base: str, host: str, create_repositories: bool = False, repository_guard: bool = False
) -> dict[str, Any]:
    """The GitHub App manifest: private, read-only access to code, Commit statuses: write (builds reported on their
    commits, STATUS_PERMISSIONS), Pull requests and Issues: write (pull-request previews: their events and one comment
    on each pull request with the preview's link, PULL_PERMISSIONS), e-mail addresses and organisation members: read
    (sign-in with GitHub, SIGN_IN_PERMISSIONS) with the panel's /login/github as the callback URL,
    push/create/delete/pull_request events to the panel's webhook. With `create_repositories`, also
    Administration: write, so the panel can create empty repositories for new projects in the organisations it is
    installed on (CREATE_PERMISSIONS). With `repository_guard`, also what the repository guard needs
    (GUARD_PERMISSIONS: Administration, Contents, Pull requests and Workflows: write, Checks: read)."""
    panel_url, hook_base = panel_url.rstrip("/"), hook_base.rstrip("/")
    permissions = {
        **PERMISSIONS, **STATUS_PERMISSIONS, **PULL_PERMISSIONS, **SIGN_IN_PERMISSIONS,
        **(CREATE_PERMISSIONS if create_repositories else {}),
        **(GUARD_PERMISSIONS if repository_guard else {}),
    }  # fmt: skip
    return {
        "name": app_name(host),
        "url": panel_url,
        "description": "Access for the Sudoo.sh panel: it downloads branches to run them as Odoo instances, reports "
        "builds on their commits"
        + (" and creates empty repositories for new projects." if create_repositories else " (read-only code).")
        + (" It may also propose CI checks and set branch rules (repository guard)." if repository_guard else ""),
        "hook_attributes": {"url": hook_base + HOOK_PATH, "active": True},
        "redirect_url": f"{panel_url}/github/app/created",
        "callback_urls": [panel_url + SIGN_IN_CALLBACK],  # sign-in with GitHub comes back here (auth/github.py)
        "setup_url": f"{panel_url}/github/app/installed",
        "setup_on_update": True,
        "public": False,
        "request_oauth_on_install": False,
        "default_permissions": permissions,
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
    global _synced
    data = describe()
    _file().unlink(missing_ok=True)
    _token_file().unlink(missing_ok=True)
    _repositories.clear()
    _synced = 0.0
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
        "permissions": {str(k): str(v) for k, v in (found.get("permissions") or {}).items()},
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
    """Replace the recorded installations with the ones GitHub lists (installed or removed on GitHub itself, or
    installed while GitHub's redirect never reached the panel)."""
    global _synced
    _synced = time.time()
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
        known = data.get("installations", {})
        fresh = {str(i["id"]): _installation_record(i) for i in found}
        for key, record in fresh.items():  # keep when the panel first saw an installation
            record["added"] = known.get(key, {}).get("added", record["added"])
        data["installations"] = fresh
        _write(data)
    _repositories.clear()
    return sorted(data["installations"].values(), key=lambda i: str(i["account"]).lower())


def sync_if_stale(max_age: float = SYNC_SECONDS) -> bool:
    """Read the installations from GitHub again when the last reading is older than `max_age` seconds (cheap: one
    call a minute at most, also when GitHub fails). Called when the GitHub access page loads and before a repository
    check, so installations made directly on GitHub are found without pressing Refresh. True when it read them."""
    global _synced
    if not connected() or time.time() - _synced < max_age:
        return False
    _synced = time.time()
    try:
        sync_installations()
    except GitHubError:
        return False
    return True


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


def installation_repositories(installation: dict[str, Any]) -> list[dict[str, Any]]:
    """The repositories one installation may read (cached for a minute)."""
    now = time.time()
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
    return cached[1]


def repositories(query: str = "") -> list[dict[str, Any]]:
    """Repositories readable through the installations, newest push first, filtered by `query` (in the name)."""
    found: list[dict[str, Any]] = []
    for installation in installations():
        if not installation.get("suspended"):
            found += installation_repositories(installation)
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


def explain(full_name: str) -> dict[str, str] | None:
    """Why the app cannot read the repository `full_name` ("owner/repo"), saying what to do about it:
    {"reason", "message", "url", "label"} ("url" is a GitHub page, or a panel path starting with "/"; it and "label"
    are empty when there is nothing to open). None when no app is connected (deploy keys are the way then).

    reason: "app_owned_elsewhere" (a private app can be installed only on the account that owns it), "not_installed",
    "suspended", "not_selected" (installed on the owner, but the repository is not among the selected ones) or
    "not_found" (the app can read the owner's repositories, so the address or the branch is wrong)."""
    data = _read()
    if data is None:
        return None
    owner = full_name.split("/")[0]
    app_owner = str((data.get("owner") or {}).get("login", ""))
    found = next((i for i in installations() if str(i["account"]).lower() == owner.lower()), None)
    if found is None:
        if app_owner.lower() != owner.lower():
            return {
                "reason": "app_owned_elsewhere",
                "message": f"The panel's GitHub App belongs to {app_owner}. A private GitHub App can only be installed "
                f"on the account that owns it, so it can never read the repositories of {owner}. To use them, "
                f"disconnect it on the GitHub access page and connect GitHub again for {owner} (an owner of "
                f"{owner} on GitHub has to create the app).",
                "url": "/github",
                "label": "Open GitHub access",
            }
        return {
            "reason": "not_installed",
            "message": f"The GitHub App is not installed on {owner}. Install it there and give it this repository.",
            "url": install_url(data),
            "label": f"Install on {owner}",
        }
    if found.get("suspended"):
        return {
            "reason": "suspended",
            "message": f"The GitHub App's installation on {owner} is suspended. Unsuspend it on GitHub.",
            "url": str(found.get("settings_url", "")),
            "label": "Open the installation",
        }
    if found.get("repository_selection") != "all":
        try:
            names = {str(r["full_name"]).lower() for r in installation_repositories(found)}
        except GitHubError:
            names = None
        if names is not None and full_name.lower() not in names:
            return {
                "reason": "not_selected",
                "message": f"The GitHub App is installed on {owner}, but {full_name} is not among the repositories it "
                "may read. Add it to the installation's selected repositories.",
                "url": str(found.get("settings_url", "")),
                "label": "Choose repositories",
            }
    return {
        "reason": "not_found",
        "message": f"The GitHub App can read the repositories of {owner}, but GitHub does not know {full_name}. "
        "Check the address.",
        "url": "",
        "label": "",
    }


# --------------------------------------------------------------------------- webhook signatures


def signature_valid(body: bytes, header: str, secret: str | None = None) -> bool:
    """X-Hub-Signature-256 is HMAC-SHA256 of the body with the app's webhook secret."""
    secret = secret if secret is not None else webhook_secret()
    if not secret or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


# --------------------------------------------------------------------------- creating repositories (projects/)


def repository_creation(owner: str) -> dict[str, Any]:
    """Whether the panel can create a repository in the GitHub account `owner`, and if not, what to do:
    {"possible", "reason", "message", "url", "label"}. reason: "ok", "no_app", "not_installed", "suspended",
    "personal_account" (a GitHub App can create repositories only in organisations), "permission_missing" (the
    app lacks Administration: write, or the organisation has not accepted it yet)."""
    data = _read()
    if data is None:
        return {
            "possible": False,
            "reason": "no_app",
            "message": "Connect the GitHub App first (GitHub access), on the organisation the repository goes to.",
            "url": "/github",
            "label": "Open GitHub access",
        }
    found = next((i for i in installations() if str(i["account"]).lower() == owner.lower()), None)
    if found is None:
        return {
            "possible": False,
            "reason": "not_installed",
            "message": f"The GitHub App is not installed on {owner}. Install it there first.",
            "url": install_url(data),
            "label": f"Install on {owner}",
        }
    if found.get("suspended"):
        return {
            "possible": False,
            "reason": "suspended",
            "message": f"The GitHub App's installation on {owner} is suspended. Unsuspend it on GitHub.",
            "url": str(found.get("settings_url", "")),
            "label": "Open the installation",
        }
    if found.get("account_type") != "Organization":
        return {
            "possible": False,
            "reason": "personal_account",
            "message": f"{owner} is a personal account: a GitHub App can create repositories only in organisations. "
            "Create an empty repository on GitHub (without a README), then link it here.",
            "url": "https://github.com/new",
            "label": "New repository on GitHub",
        }
    if (found.get("permissions") or {}).get("administration") != "write":
        return {
            "possible": False,
            "reason": "permission_missing",
            "message": "The panel's GitHub App may not create repositories yet. On GitHub, open the app's settings, "
            "Permissions and events, Repository permissions, set Administration to Read and write and save. Then an "
            f"owner of {owner} accepts the new permission on the installation's page (GitHub sends them an e-mail "
            "too). Come back here and press Check again.",
            "url": settings_url(data) + "/permissions",
            "label": "Open the app's permissions",
            "accept_url": str(found.get("settings_url", "")),
        }
    return {"possible": True, "reason": "ok", "message": "", "url": "", "label": ""}


def create_repository(owner: str, name: str, private: bool = True, description: str = "") -> dict[str, Any]:
    """Create a new, empty repository (no README, no branch) in the organisation `owner`; returns
    {"full_name", "url", "private", "default_branch"}. Raises GitHubError with a sentence for people."""
    if not ORG_RE.match(owner) or not REPOSITORY_NAME_RE.match(name) or name in (".", ".."):
        raise GitHubError("This is not a repository name GitHub accepts (letters, digits, '.', '-', '_').", 400)
    check = repository_creation(owner)
    if not check["possible"]:
        raise GitHubError(str(check["message"]), 409)
    installation = installation_for(owner)
    assert installation is not None
    # a token of its own, with only what creating needs (never cached: the read-only tokens stay read-only)
    answer = request(
        "POST",
        f"/app/installations/{installation}/access_tokens",
        bearer=app_jwt(),
        body={"permissions": {**CREATE_PERMISSIONS, "metadata": "read"}},
    )
    token = str(answer["token"])
    body = {"name": name, "private": private, "description": description[:350], "auto_init": False}
    try:
        created = request("POST", f"/orgs/{owner}/repos", bearer=token, body=body)
    except GitHubError as error:
        if error.status == 422:
            raise GitHubError(f"{owner} has a repository named {name} already. Choose another name.", 409) from None
        if error.status in (401, 403, 404):
            raise GitHubError(
                f"GitHub refused to create the repository in {owner} ({error}). Check that the app has "
                "Administration: Read and write and that an owner of the organisation accepted it.",
                409,
            ) from None
        raise
    _repositories.clear()
    full_name = str(created.get("full_name") or f"{owner}/{name}")
    return {
        "full_name": full_name,
        "url": f"{WEB}/{full_name}",
        "private": bool(created.get("private", private)),
        "default_branch": str(created.get("default_branch") or "main"),
    }


def repository_owners() -> list[dict[str, Any]]:
    """Every account the app is installed on, with whether a repository can be created there (repository_creation)."""
    return [
        {"account": i["account"], "account_type": i.get("account_type", "User"), **repository_creation(i["account"])}
        for i in sorted(installations(), key=lambda i: str(i["account"]).lower())
    ]


# --------------------------------------------------------------------------- commit statuses (projects/)


def can_post_statuses(owner: str) -> bool:
    """Whether the app's installation on `owner` may write commit statuses (Commit statuses: Read and write, accepted
    on the installation). Apps made before the panel asked for it lack it until it is added on GitHub."""
    if not connected():
        return False
    found = next((i for i in installations() if str(i["account"]).lower() == owner.lower()), None)
    return bool(found and not found.get("suspended") and (found.get("permissions") or {}).get("statuses") == "write")


_status_tokens: dict[int, tuple[float, str]] = {}  # installation id -> (expires, token): in memory only


def _status_token(installation_id: int, now: float | None = None) -> str:
    """A token with only Commit statuses: write (never the read-only token's cache: that one stays read-only)."""
    now = now if now is not None else time.time()
    with _lock:
        cached = _status_tokens.get(installation_id)
        if cached and cached[0] - TOKEN_MARGIN > now:
            return cached[1]
    answer = request(
        "POST",
        f"/app/installations/{installation_id}/access_tokens",
        bearer=app_jwt(),
        body={"permissions": {**STATUS_PERMISSIONS, "metadata": "read"}},
    )
    token = str(answer["token"])
    with _lock:
        _status_tokens[installation_id] = (_parse_time(str(answer.get("expires_at", ""))) or now + 3600, token)
    return token


SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def post_status(full_name: str, sha: str, state: str, context: str, description: str, target_url: str | None) -> None:
    """Set the commit status `context` of commit `sha` in repository `full_name` ("owner/repo"). Raises GitHubError
    (callers never let it fail a build)."""
    owner, _, repository = full_name.partition("/")
    if not ORG_RE.match(owner) or not REPOSITORY_NAME_RE.match(repository) or not SHA_RE.match(sha):
        raise GitHubError("Not a repository and commit GitHub knows.", 400)
    if state not in STATUS_STATES:
        raise GitHubError(f"Not a commit status: {state[:20]!r}.", 400)
    installation = installation_for(owner)
    if installation is None:
        raise GitHubError(f"The GitHub App is not installed on {owner}.", 409)
    body: dict[str, Any] = {"state": state, "context": context[:100], "description": description[:140]}
    if target_url:
        body["target_url"] = target_url
    try:
        request("POST", f"/repos/{owner}/{repository}/statuses/{sha}", bearer=_status_token(installation), body=body)
    except GitHubError as error:
        if error.status == 401:
            with _lock:
                _status_tokens.pop(installation, None)  # asked for again next time
        raise


# --------------------------------------------------------------------------- pull requests (projects/previews.py)


def can_comment(owner: str) -> bool:
    """Whether the app's installation on `owner` may comment on pull requests (Pull requests or Issues: Read and write,
    accepted on the installation). Apps made before the panel asked for it lack it until it is added on GitHub."""
    if not connected():
        return False
    found = next((i for i in installations() if str(i["account"]).lower() == owner.lower()), None)
    granted = (found or {}).get("permissions") or {}
    return bool(
        found and not found.get("suspended") and "write" in (granted.get("pull_requests"), granted.get("issues"))
    )


_comment_tokens: dict[int, tuple[float, str]] = {}  # installation id -> (expires, token): in memory only


def _comment_token(installation_id: int, now: float | None = None) -> str:
    """A token with only the installation's Pull requests and Issues access (reading pull requests, writing the
    panel's comment); never the read-only token's cache."""
    now = now if now is not None else time.time()
    with _lock:
        cached = _comment_tokens.get(installation_id)
        if cached and cached[0] - TOKEN_MARGIN > now:
            return cached[1]
    found = next((i for i in installations() if int(i["id"]) == installation_id), {})
    granted = found.get("permissions") or {}
    wanted = {key: str(granted[key]) for key in PULL_PERMISSIONS if granted.get(key) in ("read", "write")}
    answer = request(
        "POST",
        f"/app/installations/{installation_id}/access_tokens",
        bearer=app_jwt(),
        body={"permissions": {**wanted, "metadata": "read"}},
    )
    token = str(answer["token"])
    with _lock:
        _comment_tokens[installation_id] = (_parse_time(str(answer.get("expires_at", ""))) or now + 3600, token)
    return token


def _repository_parts(full_name: str) -> tuple[str, str]:
    owner, _, repository = full_name.partition("/")
    if not ORG_RE.match(owner) or not REPOSITORY_NAME_RE.match(repository):
        raise GitHubError("Not a repository GitHub knows.", 400)
    return owner, repository


def comment(full_name: str, number: int, body: str, comment_id: int | None = None) -> int:
    """Write the panel's comment on pull request `number` of `full_name` ("owner/repo"), or change the one it wrote
    (`comment_id`); returns the comment's id. Raises GitHubError (callers never let it fail anything)."""
    owner, repository = _repository_parts(full_name)
    installation = installation_for(owner)
    if installation is None:
        raise GitHubError(f"The GitHub App is not installed on {owner}.", 409)
    if not can_comment(owner):
        raise GitHubError("The GitHub App may not comment on pull requests (Pull requests: Read and write).", 403)
    token = _comment_token(installation)
    text = body[:60000]
    try:
        if comment_id:
            try:
                request("PATCH", f"/repos/{owner}/{repository}/issues/comments/{int(comment_id)}", bearer=token,
                        body={"body": text})  # fmt: skip
                return int(comment_id)
            except GitHubError as error:
                if error.status != 404:  # someone deleted the comment: a new one
                    raise
        answer = request("POST", f"/repos/{owner}/{repository}/issues/{int(number)}/comments", bearer=token,
                         body={"body": text})  # fmt: skip
    except GitHubError as error:
        if error.status == 401:
            with _lock:
                _comment_tokens.pop(installation, None)  # asked for again next time
        raise
    return int(answer["id"])


def open_pulls(full_name: str) -> list[dict[str, Any]]:
    """The open pull requests of `full_name` (at most 100), as GitHub's webhook describes them (number, head, base,
    title, html_url): the fallback when webhooks do not arrive. Raises GitHubError."""
    owner, repository = _repository_parts(full_name)
    installation = installation_for(owner)
    if installation is None:
        raise GitHubError(f"The GitHub App is not installed on {owner}.", 409)
    found = next((i for i in installations() if int(i["id"]) == installation), {})
    if (found.get("permissions") or {}).get("pull_requests") not in ("read", "write"):
        raise GitHubError("The GitHub App may not read pull requests (Pull requests: Read and write).", 403)
    answer = request("GET", f"/repos/{owner}/{repository}/pulls?state=open&per_page=100",
                     bearer=_comment_token(installation))  # fmt: skip
    return [found for found in answer or [] if isinstance(found, dict)]
