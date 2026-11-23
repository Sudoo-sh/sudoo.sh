"""Signing in with GitHub ("Continue with GitHub"), through the panel's own GitHub App (repos/github_app.py).

A GitHub App can identify the people who use it ("Request user authorization (OAuth)"): the panel uses the web flow
with `state` and PKCE (RFC 7636, S256), and GitHub sends the browser back to the panel's own address,
<panel>/login/github, which must be one of the App's callback URLs (the manifest of a new App lists it; for an
App created before, it is added once on GitHub: docs/ADMIN_GUIDE.md).

    1. start(): a random `state` (kept in the browser's session too) and a PKCE verifier (kept here, in memory, for
       10 minutes, never sent to the browser); the browser goes to GitHub's authorize page with the state and the
       verifier's SHA-256 (code_challenge).
    2. GitHub asks the person to authorize the App (once), then sends the browser to /login/github with a one-time
       code and the state. The web app posts both to the panel.
    3. finish(): the state must be the one of this browser's session and is used once; the code is exchanged with
       the App's client secret and the verifier (a code stolen on the way is useless without it). With the user
       token, the panel reads who the person is (GET /user), their verified primary e-mail address
       (GET /user/emails, when the App may read e-mail addresses) and, for GITHUB_SIGN_IN_ORGS, whether they are a
       member (GET /user/memberships/orgs/<org>). Then it revokes the token at once: no user token is ever stored.
       What is kept on the account: the GitHub user id, login and verified primary e-mail address.

Who may sign in (account_for):
    (a) an account linked before (by the GitHub user id, which never changes; logins can be renamed);
    (b) an account or a pending invitation that names the GitHub login (typed by an administrator), or whose e-mail
        address is the person's verified primary GitHub address: it is linked at this first sign-in. An address
        GitHub has not verified never links anything;
    (c) a member of one of GITHUB_SIGN_IN_ORGS: an account is created with GITHUB_SIGN_IN_ROLE;
    (d) nobody else.
The account must allow the method "github" (methods.py). Project access can then follow the person's permissions on
the projects' repositories (github_access.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from sudoo import accounts, invitations
from sudoo.config import settings
from sudoo.repos import github_app

from . import methods

OAUTH = "https://github.com"  # changed by the tests, which run a fake GitHub
CALLBACK_PATH = "/login/github"
FLOW_SECONDS = 600
MAX_FLOWS = 500
FIELD = "github"  # on the account: {"id", "login", "email", "linked"}
LOGIN_FIELD = "github_login"  # on an account or invitation: the GitHub login an administrator expects
PURPOSES = ("sign-in", "link")


class GitHubSignInError(Exception):
    """Signing in with GitHub was refused or failed; the message is for people (never a secret)."""

    def __init__(self, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class Flow:
    verifier: str
    redirect_uri: str
    purpose: str
    user: str | None
    expires: float


@dataclass(frozen=True, slots=True)
class GitHubUser:
    """Who GitHub says signed in. `email` is the verified primary address (None when unknown or not verified)."""

    id: int
    login: str
    name: str = ""
    email: str | None = None
    two_factor: bool = False
    orgs: tuple[str, ...] = field(default=())


_FLOWS: dict[str, Flow] = {}
_LOCK = threading.Lock()
_LINK_LOCK = threading.Lock()


# ---------------------------------------------------------------- configuration


def _app() -> dict[str, Any] | None:
    return github_app._read()


def configured() -> bool:
    """The panel's GitHub App is connected and has a client id and secret (every App made from the manifest has)."""
    data = _app()
    return bool(data and data.get("client_id") and data.get("client_secret"))


def enabled() -> bool:
    """GITHUB_SIGN_IN is on and the App can identify people."""
    return bool(settings.github_sign_in) and configured()


def allowed_orgs() -> list[str]:
    return [part for part in (p.strip() for p in settings.github_sign_in_orgs.split(",")) if part]


def callback_url(panel_url: str) -> str:
    return panel_url.rstrip("/") + CALLBACK_PATH


# ---------------------------------------------------------------- talking to GitHub


def _call(method: str, url: str, *, headers: dict[str, str], body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": "Sudoo.sh", **headers}
    if data is not None:
        headers["Content-Type"] = "application/json"
    call = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(call, timeout=20) as answer:  # noqa: S310 - https addresses of GitHub
            raw = answer.read()
    except urllib.error.HTTPError as error:
        raise github_app.GitHubError(f"GitHub answered {error.code}.", error.code) from None
    except (OSError, ValueError) as error:
        raise github_app.GitHubError(f"GitHub cannot be reached ({error.__class__.__name__}).") from None
    return json.loads(raw) if raw else None


def _api(path: str, token: str) -> Any:
    return _call(
        "GET",
        github_app.API + path,
        headers={"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": github_app.API_VERSION},
    )


def _client() -> tuple[str, str]:
    data = _app()
    if not data or not data.get("client_id") or not data.get("client_secret"):
        raise GitHubSignInError("Sign-in with GitHub is not set up on this panel.", 409)
    return str(data["client_id"]), github_app._secret(data, "client_secret")


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


# ---------------------------------------------------------------- the flow


def start(purpose: str, panel_url: str, user: str | None = None) -> tuple[str, str]:
    """A new flow: (state, the address of GitHub's authorize page). Raises GitHubSignInError when not set up."""
    if purpose not in PURPOSES:
        raise ValueError(purpose)
    if not enabled():
        raise GitHubSignInError("Sign-in with GitHub is not turned on for this panel.", 409)
    client_id, _ = _client()
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    redirect_uri = callback_url(panel_url)
    now = time.time()
    with _LOCK:
        for key in [k for k, flow in _FLOWS.items() if flow.expires < now]:
            del _FLOWS[key]
        while len(_FLOWS) >= MAX_FLOWS:  # a flood of starts: the oldest go first
            del _FLOWS[min(_FLOWS, key=lambda k: _FLOWS[k].expires)]
        _FLOWS[state] = Flow(verifier, redirect_uri, purpose, user, now + FLOW_SECONDS)
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
            "allow_signup": "false",
        }
    )
    return state, f"{OAUTH}/login/oauth/authorize?{query}"


def _take(state: str, expected: str) -> Flow:
    """The flow of `state`, used up; refused unless it is the one this browser started and it has not expired."""
    if not state or not expected or not hmac.compare_digest(state, expected):
        raise GitHubSignInError("This sign-in did not start in this browser. Start again.", 400)
    with _LOCK:
        flow = _FLOWS.pop(state, None)
    if flow is None or flow.expires < time.time():
        raise GitHubSignInError("Signing in with GitHub took too long. Start again.", 400)
    return flow


def _exchange(flow: Flow, code: str) -> str:
    client_id, client_secret = _client()
    answer = _call(
        "POST",
        f"{OAUTH}/login/oauth/access_token",
        headers={},
        body={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": flow.redirect_uri,
            "code_verifier": flow.verifier,
        },
    )
    if not isinstance(answer, dict) or not answer.get("access_token"):
        reason = str((answer or {}).get("error_description") or (answer or {}).get("error") or "no token")
        raise GitHubSignInError(f"GitHub refused the sign-in ({reason[:120]}). Start again.", 400)
    return str(answer["access_token"])


def _revoke(token: str) -> None:
    """Delete the user token on GitHub right away (best effort: it also expires by itself)."""
    try:
        client_id, client_secret = _client()
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        _call(
            "DELETE",
            f"{github_app.API}/applications/{client_id}/token",
            headers={"Authorization": f"Basic {basic}", "X-GitHub-Api-Version": github_app.API_VERSION},
            body={"access_token": token},
        )
    except (github_app.GitHubError, GitHubSignInError):
        pass


def _primary_email(token: str) -> str | None:
    try:
        found = _api("/user/emails", token)
    except github_app.GitHubError:  # the App may not read e-mail addresses: then nothing links by address
        return None
    for entry in found if isinstance(found, list) else []:
        if isinstance(entry, dict) and entry.get("primary") and entry.get("verified") is True:
            address = str(entry.get("email") or "")
            return address if accounts.EMAIL_RE.match(address) else None
    return None


def _member_of(token: str) -> tuple[str, ...]:
    found = []
    for org in allowed_orgs():
        try:
            membership = _api(f"/user/memberships/orgs/{org}", token)
        except github_app.GitHubError:
            continue  # not a member, or the App cannot see the organisation's members
        if isinstance(membership, dict) and membership.get("state") == "active":
            found.append(org)
    return tuple(found)


def finish(state: str, code: str, expected_state: str) -> tuple[Flow, GitHubUser]:
    """Steps 2 and 3: who signed in on GitHub, and what the flow was for. Raises GitHubSignInError."""
    flow = _take(state, expected_state)
    if not github_app.CODE_RE.match(code or ""):
        raise GitHubSignInError("The code GitHub sent back is not valid. Start again.", 400)
    try:
        token = _exchange(flow, code)
    except github_app.GitHubError as error:
        raise GitHubSignInError(f"{error} Start again.", 502) from None
    try:
        found = _api("/user", token)
        if not isinstance(found, dict) or not isinstance(found.get("id"), int) or not found.get("login"):
            raise GitHubSignInError("GitHub's answer did not say who signed in.", 502)
        person = GitHubUser(
            id=int(found["id"]),
            login=str(found["login"])[:39],
            name=str(found.get("name") or "")[:120],
            email=_primary_email(token),
            two_factor=found.get("two_factor_authentication") is True,
            orgs=_member_of(token),
        )
    except github_app.GitHubError as error:
        raise GitHubSignInError(f"{error} Start again.", 502) from None
    finally:
        _revoke(token)
    return flow, person


# ---------------------------------------------------------------- accounts


def link_of(account: dict[str, Any]) -> dict[str, Any] | None:
    found = account.get(FIELD)
    return found if isinstance(found, dict) and isinstance(found.get("id"), int) else None


def linked_account(github_id: int) -> str | None:
    """The account linked to this GitHub user id, or None."""
    for name, account in accounts.all_users().items():
        link = link_of(account)
        if link and link["id"] == github_id:
            return name
    return None


def _record(person: GitHubUser) -> dict[str, Any]:
    return {"id": person.id, "login": person.login, "email": person.email, "linked": round(time.time())}


def link(name: str, person: GitHubUser) -> None:
    """Link the account `name` to this GitHub account. Raises GitHubSignInError when another account has it."""
    with _LINK_LOCK:
        other = linked_account(person.id)
        if other is not None and other != name:
            raise GitHubSignInError("This GitHub account is linked to another account of this panel.", 409)
        accounts.update(name, **{FIELD: _record(person), LOGIN_FIELD: None})


def unlink(name: str) -> None:
    accounts.update(name, **{FIELD: None})


def _expects(account: dict[str, Any], person: GitHubUser) -> bool:
    """An administrator typed this GitHub login for the account (or invitation)."""
    return str(account.get(LOGIN_FIELD) or "").lower() == person.login.lower()


def _same_address(account: dict[str, Any], person: GitHubUser) -> bool:
    return bool(person.email) and str(account.get("email") or "").lower() == str(person.email).lower()


def account_for(person: GitHubUser) -> tuple[str, str]:
    """(account name, how it was found: "linked", "matched", "invitation" or "organisation"); linking or creating it
    when needed. Raises GitHubSignInError when this GitHub account may not sign in."""
    with _LINK_LOCK:
        users = accounts.all_users()
        found = linked_account(person.id)
        how = "linked"
        if found is None:
            unlinked = {n: u for n, u in users.items() if link_of(u) is None}
            found = next((n for n, u in unlinked.items() if _expects(u, person)), None)
            found = found or next((n for n, u in unlinked.items() if _same_address(u, person)), None)
            how = "matched"
        if found is None:
            found = _from_invitation(person)
            how = "invitation"
        if found is None and person.orgs:
            found = _from_organisation(person)
            how = "organisation"
        if found is None:
            raise GitHubSignInError(
                "This GitHub account has no access to this panel. Ask an administrator to invite you.", 403
            )
        account = accounts.get(found) or {}
        if not methods.allows(account, "github"):
            raise GitHubSignInError("Sign-in with GitHub is turned off for this account. Use another way.", 403)
        accounts.update(found, **{FIELD: {**_record(person), "linked": (link_of(account) or {}).get("linked")
                                          or round(time.time())}, LOGIN_FIELD: None})  # fmt: skip
    return found, how


def _profile(person: GitHubUser) -> dict[str, str]:
    first, _, last = person.name.partition(" ")
    profile = {"first_name": first.strip()[:60], "last_name": last.strip()[:60]}
    return {k: v for k, v in profile.items() if v and not accounts.profile_problem({k: v})}


def _from_invitation(person: GitHubUser) -> str | None:
    """Accept a pending invitation that names this GitHub login or the verified primary address."""
    now = time.time()
    for invitation_id, invitation in invitations.all_invitations().items():
        if invitation.get("expires", 0) <= now:
            continue
        if not (_expects(invitation, person) or _same_address(invitation, person)):
            continue
        taken = invitations.take(invitation_id)
        if taken is None:
            continue
        email = str(taken.get("email") or "") or person.email
        role = str(taken.get("role") or "viewer")
        if not methods.allows({"role": role}, "github"):
            raise GitHubSignInError("Sign-in with GitHub is not offered for new accounts. Accept the e-mail.", 403)
        name = accounts.username_for(email or f"{person.login.lower()}@github")
        names = {k: str(taken[k]) for k in ("first_name", "last_name") if taken.get(k)} or _profile(person)
        profile: dict[str, Any] = {**names, "email": email if not accounts.email_taken(email) else None}
        accounts.create(name, role, "", None, **profile)
        accounts.update(name, invited_by=taken.get("invited_by"))
        return name
    return None


def _from_organisation(person: GitHubUser) -> str:
    """A new account for a member of one of GITHUB_SIGN_IN_ORGS."""
    email = person.email if person.email and not accounts.email_taken(person.email) else None
    name = accounts.username_for(email or f"{person.login.lower()}@github")
    profile: dict[str, Any] = {**_profile(person), "email": email}
    accounts.create(name, settings.github_sign_in_role, "", None, **profile)
    accounts.update(name, **{methods.FIELD: ["github"], "github_orgs": list(person.orgs)})
    return name
