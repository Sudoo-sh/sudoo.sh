"""Sign-in with GitHub (state, PKCE, who may sign in, linking), project access from repository permissions, sign-in
codes by e-mail without a password, and the break-glass rule; against a fake GitHub served on 127.0.0.1."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask.testing import FlaskClient

from sudoo import accounts, invitations, projects
from sudoo.auth import email_sign_in, github, github_access, methods
from sudoo.config import Settings, live, settings
from sudoo.core import vault
from sudoo.projects import access
from sudoo.repos import github_app
from sudoo.storage import storage

ORIGIN = {"Origin": "http://localhost"}
CLIENT_ID = "Iv23liSignIn"
SECRET = "client-secret-for-sign-in"
PEM = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                   serialization.NoEncryption())
    .decode()
)  # fmt: skip


class FakeGitHub(BaseHTTPRequestHandler):
    codes: dict[str, tuple[str, str]] = {}  # code -> (code_challenge, user key)
    users: dict[str, dict[str, Any]] = {}
    emails: dict[str, list[dict[str, Any]]] = {}
    orgs: dict[str, set[str]] = {}
    permissions: dict[str, str] = {}  # login -> role_name on Acme/shop
    revoked: list[str] = []
    issued: list[str] = []

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, status: int, body: Any = None) -> None:
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _user(self) -> str | None:
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not token.startswith("ghu_") or token in self.revoked:
            return None
        return token.removeprefix("ghu_").rsplit(".", 1)[0]

    def _handle(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        path = self.path
        if method == "POST" and path == "/login/oauth/access_token":
            found = self.codes.pop(body.get("code"), None)
            verifier = str(body.get("code_verifier") or "")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if found is None or found[0] != challenge or body.get("client_secret") != SECRET:
                return self._send(200, {"error": "bad_verification_code", "error_description": "The code is wrong."})
            token = f"ghu_{found[1]}.{len(self.issued)}"
            self.issued.append(token)
            return self._send(200, {"access_token": token, "token_type": "bearer"})
        if method == "DELETE" and path == f"/applications/{CLIENT_ID}/token":
            expected = base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
            if self.headers.get("Authorization") != f"Basic {expected}":
                return self._send(401, {"message": "Bad credentials"})
            self.revoked.append(body["access_token"])
            return self._send(204)
        if method == "POST" and path.startswith("/app/installations/"):
            expires = datetime.fromtimestamp(time.time() + 3600, UTC).isoformat().replace("+00:00", "Z")
            return self._send(201, {"token": "ghs_installation", "expires_at": expires})
        if path.startswith("/repos/Acme/") and "/collaborators/" in path:
            if self.headers.get("Authorization") != "Bearer ghs_installation":
                return self._send(401, {"message": "Bad credentials"})
            login = path.split("/")[5]
            role = self.permissions.get(login)
            if role is None:
                return self._send(404, {"message": "Not Found"})
            return self._send(200, {"permission": "write", "role_name": role, "user": {"login": login}})
        user = self._user()
        if user is None:
            return self._send(401, {"message": "Bad credentials"})
        if path == "/user":
            return self._send(200, self.users[user])
        if path == "/user/emails":
            return self._send(200, self.emails.get(user, []))
        if path.startswith("/user/memberships/orgs/"):
            org = path.rsplit("/", 1)[1]
            if user in self.orgs.get(org, set()):
                return self._send(200, {"state": "active", "role": "member"})
            return self._send(404, {"message": "Not Found"})
        return self._send(404, {"message": "Not Found"})

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")


@pytest.fixture(scope="module")
def fake() -> Iterator[type[FakeGitHub]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    original = github_app.API, github.OAUTH
    github_app.API, github.OAUTH = address, address
    yield FakeGitHub
    github_app.API, github.OAUTH = original
    server.shutdown()


@pytest.fixture
def gh(fake: type[FakeGitHub], passwords: dict[str, str]) -> Iterator[type[FakeGitHub]]:
    """A connected GitHub App (installed on Acme) with sign-in on."""
    github_app._write(
        {
            "id": 99,
            "slug": "sudoo-test",
            "client_id": CLIENT_ID,
            "client_secret": vault.encrypt(SECRET, "github-app:client_secret"),
            "pem": vault.encrypt(PEM, "github-app:pem"),
            "owner": {"login": "Acme", "type": "Organization"},
            "installations": {"77": {"id": 77, "account": "Acme", "account_type": "Organization"}},
        }
    )
    live.change({"github_sign_in": True})
    for attribute in ("codes", "users", "emails", "orgs", "permissions"):
        getattr(fake, attribute).clear()
    accounts.THROTTLE._failures.clear()
    email_sign_in.SEND_LIMIT.clear()
    email_sign_in.ACCOUNT_SEND_LIMIT.clear()
    yield fake
    live.change(dict.fromkeys(live.overrides()))
    github_app.disconnect()
    accounts.THROTTLE._failures.clear()


def _person(fake: type[FakeGitHub], key: str, *, user_id: int, email: str | None = None, verified: bool = True,
            two_factor: bool = False) -> None:  # fmt: skip
    fake.users[key] = {"id": user_id, "login": key, "name": "Grace Hopper", "two_factor_authentication": two_factor}
    if email:
        fake.emails[key] = [{"email": email, "primary": True, "verified": verified}]


def _start(
    client: FlaskClient, headers: dict[str, str] | None = None, path: str = "/api/v1/session/github"
) -> dict[str, str]:
    started = client.post(path, json={}, headers=headers or ORIGIN)
    assert started.status_code == 200, started.get_json()
    url = started.get_json()["data"]["url"]
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert query["code_challenge_method"] == "S256" and query["redirect_uri"] == "http://localhost/login/github"
    assert query["client_id"] == CLIENT_ID and "client_secret" not in url
    return query


def _github_sign_in(
    client: FlaskClient, fake: type[FakeGitHub], key: str, headers: dict[str, str] | None = None, link: bool = False
) -> Any:
    query = _start(client, headers, "/api/v1/account/github" if link else "/api/v1/session/github")
    code = f"code-{key}-{time.time_ns()}"
    fake.codes[code] = (query["code_challenge"], key)
    return client.post(
        "/api/v1/session/github/callback", json={"code": code, "state": query["state"]}, headers=headers or ORIGIN
    )


def _account(role: str = "developer", **fields: Any) -> str:
    name = f"g{time.time_ns() % 10**10}"
    accounts.create(name, role, "orchid-pebble-summit-73", must_change=False, **fields)
    return name


# ---------------------------------------------------------------- the flow


def test_sign_in_page_options(app: Flask, gh: type[FakeGitHub]) -> None:
    options = app.test_client().get("/api/v1/session/options").get_json()["data"]
    assert options["github"] is True and options["password"] is True
    live.change({"github_sign_in": False})
    assert app.test_client().get("/api/v1/session/options").get_json()["data"]["github"] is False
    assert app.test_client().post("/api/v1/session/github", json={}, headers=ORIGIN).status_code == 409


def test_linked_by_verified_email_and_token_never_kept(app: Flask, gh: type[FakeGitHub]) -> None:
    name = _account(email="grace@example.com")
    _person(gh, "grace", user_id=1001, email="grace@example.com")
    client = app.test_client()
    answer = _github_sign_in(client, gh, "grace")
    assert answer.status_code == 201, answer.get_json()
    assert answer.get_json()["data"]["user"] == name
    assert accounts.get(name)["github"]["id"] == 1001  # type: ignore[index]
    assert gh.issued[-1] in gh.revoked  # the user token was revoked at once
    stored = json.dumps(storage().all(accounts.USERS))
    assert "ghu_" not in stored and SECRET not in stored
    events = [e["event"] for e in storage().logs(user=name, limit=5)]
    assert "signed in with GitHub" in events and "GitHub account linked (matched)" in events
    # the next time, found by the GitHub user id even after a rename on GitHub
    gh.users["grace"]["login"] = "grace-renamed"
    again = _github_sign_in(app.test_client(), gh, "grace")
    assert again.status_code == 201 and accounts.get(name)["github"]["login"] == "grace-renamed"  # type: ignore[index]


def test_an_unverified_address_links_nothing(app: Flask, gh: type[FakeGitHub]) -> None:
    name = _account(email="mallory-target@example.com")
    _person(gh, "mallory", user_id=1002, email="mallory-target@example.com", verified=False)
    answer = _github_sign_in(app.test_client(), gh, "mallory")
    assert answer.status_code == 403 and "no access" in answer.get_json()["detail"]
    assert "github" not in (accounts.get(name) or {})
    assert any(e["event"] == "GitHub sign-in refused" for e in storage().logs(user="mallory", limit=3))


def test_unknown_people_are_refused(app: Flask, gh: type[FakeGitHub]) -> None:
    _person(gh, "stranger", user_id=1003, email="stranger@example.com")
    answer = _github_sign_in(app.test_client(), gh, "stranger")
    assert answer.status_code == 403
    assert not any(str(a.get("email")) == "stranger@example.com" for a in accounts.all_users().values())


def test_linked_by_github_login_typed_by_an_administrator(app: Flask, gh: type[FakeGitHub], admin: Any) -> None:
    name = _account(email="linus@company.example")
    client, headers = admin
    assert client.patch(f"/api/v1/users/{name}", json={"github_login": "@Linus-T"}, headers=headers).status_code == 200
    _person(gh, "linus-t", user_id=1004)  # no e-mail the app may read
    answer = _github_sign_in(app.test_client(), gh, "linus-t")
    assert answer.status_code == 201 and answer.get_json()["data"]["user"] == name
    assert "github_login" not in (accounts.get(name) or {})  # used once


def test_state_must_be_this_browsers_and_works_once(app: Flask, gh: type[FakeGitHub]) -> None:
    _account(email="ada.l@example.com")
    _person(gh, "ada", user_id=1005, email="ada.l@example.com")
    victim, attacker = app.test_client(), app.test_client()
    query = _start(attacker)
    code = "code-ada-csrf"
    gh.codes[code] = (query["code_challenge"], "ada")
    forged = victim.post(
        "/api/v1/session/github/callback", json={"code": code, "state": query["state"]}, headers=ORIGIN
    )
    assert forged.status_code == 400 and "did not start in this browser" in forged.get_json()["detail"]
    good = attacker.post(
        "/api/v1/session/github/callback", json={"code": code, "state": query["state"]}, headers=ORIGIN
    )
    assert good.status_code == 201
    replay = attacker.post("/api/v1/session/github/callback", json={"code": code, "state": query["state"]},
                           headers=ORIGIN)  # fmt: skip
    assert replay.status_code == 400


def test_pkce_a_code_issued_for_another_challenge_is_refused(app: Flask, gh: type[FakeGitHub]) -> None:
    _account(email="eve.target@example.com")
    _person(gh, "eve", user_id=1006, email="eve.target@example.com")
    client = app.test_client()
    query = _start(client)
    gh.codes["stolen-code"] = ("another-challenge", "eve")
    answer = client.post("/api/v1/session/github/callback", json={"code": "stolen-code", "state": query["state"]},
                         headers=ORIGIN)  # fmt: skip
    assert answer.status_code == 400 and "GitHub refused" in answer.get_json()["detail"]


def test_members_of_listed_organisations_get_an_account(app: Flask, gh: type[FakeGitHub]) -> None:
    _person(gh, "orgdev", user_id=1007, email="orgdev@acme.example")
    assert _github_sign_in(app.test_client(), gh, "orgdev").status_code == 403  # no organisation listed yet
    live.change({"github_sign_in_orgs": "Acme", "github_sign_in_role": "maintainer"})
    assert _github_sign_in(app.test_client(), gh, "orgdev").status_code == 403  # not a member
    gh.orgs["Acme"] = {"orgdev"}
    answer = _github_sign_in(app.test_client(), gh, "orgdev")
    assert answer.status_code == 201
    created = accounts.get(answer.get_json()["data"]["user"]) or {}
    assert created["role"] == "maintainer" and created["email"] == "orgdev@acme.example" and "hash" not in created
    assert created["sign_in_methods"] == ["github"]


def test_a_pending_invitation_is_accepted_with_github(app: Flask, gh: type[FakeGitHub]) -> None:
    invitation_id, _ = invitations.create("fresh.invitee@example.com", "developer", "admin1", first_name="New")
    _person(gh, "freshinvitee", user_id=1008, email="fresh.invitee@example.com")
    answer = _github_sign_in(app.test_client(), gh, "freshinvitee")
    assert answer.status_code == 201
    created = accounts.get(answer.get_json()["data"]["user"]) or {}
    assert created["role"] == "developer" and created["first_name"] == "New" and created["github"]["id"] == 1008
    assert invitations.get(invitation_id) is None


def test_github_turned_off_for_an_account(app: Flask, gh: type[FakeGitHub], admin: Any) -> None:
    name = _account(email="nogh@example.com")
    client, headers = admin
    assert client.patch(f"/api/v1/users/{name}", json={"sign_in_methods": ["password"]},
                        headers=headers).status_code == 200  # fmt: skip
    _person(gh, "nogh", user_id=1009, email="nogh@example.com")
    answer = _github_sign_in(app.test_client(), gh, "nogh")
    assert answer.status_code == 403 and "turned off" in answer.get_json()["detail"]


def test_second_factor_applies_after_github_unless_githubs_2fa_is_accepted(app: Flask, gh: type[FakeGitHub]) -> None:
    from sudoo.auth import totp

    name = _account(email="secure@example.com")
    secret = "JBSWY3DPEHPK3PXP"
    accounts.update(name, second_factors={"totp": {"secret": vault.encrypt(secret, f"totp:{name}")}})
    _person(gh, "secure", user_id=1010, email="secure@example.com", two_factor=True)
    client = app.test_client()
    asked = _github_sign_in(client, gh, "secure")
    assert asked.status_code == 200 and asked.get_json()["data"]["second_factor"]["methods"][0]["name"] == "totp"
    code = totp.code_at(secret, int(time.time() // 30))
    assert client.post("/api/v1/session/second-factor", json={"method": "totp", "code": code},
                       headers=ORIGIN).status_code == 201  # fmt: skip
    live.change({"github_sign_in_skips_second_factor": True})
    assert _github_sign_in(app.test_client(), gh, "secure").status_code == 201
    gh.users["secure"]["two_factor_authentication"] = False  # GitHub's 2FA off: the panel's code again
    assert _github_sign_in(app.test_client(), gh, "secure").status_code == 200


def test_link_and_unlink_from_the_account_page(app: Flask, gh: type[FakeGitHub]) -> None:
    from conftest import sign_in

    name = _account()
    client, headers = sign_in(app, name, "orchid-pebble-summit-73")
    _person(gh, "linker", user_id=1011)
    linked = _github_sign_in(client, gh, "linker", headers, link=True)
    assert linked.status_code == 200 and linked.get_json()["data"] == {"linked": "linker"}
    assert client.get("/api/v1/account").get_json()["data"]["sign_in_methods"]["github"]["login"] == "linker"
    # another account cannot take the same GitHub account
    other = _account()
    other_client, other_headers = sign_in(app, other, "orchid-pebble-summit-73")
    assert _github_sign_in(other_client, gh, "linker", other_headers, link=True).status_code == 409
    wrong = client.delete("/api/v1/account/github", json={"password": "nope"}, headers=headers)
    assert wrong.status_code == 400
    assert client.delete("/api/v1/account/github", json={"password": "orchid-pebble-summit-73"},
                         headers=headers).status_code == 204  # fmt: skip
    assert "github" not in (accounts.get(name) or {})
    events = [e["event"] for e in storage().logs(user=name, limit=10)]
    assert "GitHub account linked" in events and "GitHub account unlinked" in events


# ---------------------------------------------------------------- project access from GitHub


def test_permission_mapping() -> None:
    assert github_access.level_of({"role_name": "admin"}) == ("admin", "admin")
    assert github_access.level_of({"role_name": "maintain"}) == ("maintainer", "maintain")
    assert github_access.level_of({"role_name": "write"}) == ("developer", "write")
    assert github_access.level_of({"role_name": "triage"}) == ("tester", "triage")
    assert github_access.level_of({"role_name": "read"}) == ("tester", "read")
    custom = {"role_name": "release-manager", "user": {"permissions": {"push": True, "pull": True}}}
    assert github_access.level_of(custom) == ("developer", "write")
    assert github_access.level_of({"permission": "none"}) == ("none", "none")


def test_repository_permission_decides_and_losing_it_removes_access(app: Flask, gh: type[FakeGitHub]) -> None:
    project = projects.by_repository("Acme/shop") or projects.create(
        repository="Acme/shop", odoo_version="18.0", edition="community", origin="linked", user="admin1"
    )
    name = _account(role="maintainer", email="dev.shop@example.com")
    user = {"name": name, "role": "maintainer"}
    assert access.level(user, project) == "admin"  # not linked: the panel's own rules
    _person(gh, "devshop", user_id=1012, email="dev.shop@example.com")
    gh.permissions["devshop"] = "write"
    assert _github_sign_in(app.test_client(), gh, "devshop").status_code == 201  # sign-in reads the permissions
    assert access.level(user, project) == "developer"
    assert access.sees_project(user, project) and not access.sees_everything(user)
    gh.permissions.pop("devshop")  # removed from the repository on GitHub
    assert github_access.refresh(name) == [project["id"]]
    assert access.level(user, project) == "none" and not access.sees_project(user, project)
    assert any(e["event"] == "project access from GitHub changed" for e in storage().logs(user=name, limit=5))
    # an individual grant wins over GitHub
    granted = projects.change(project["id"], lambda p: {**p, "access": {"grants": [{"type": "user", "id": name,
                                                                                    "level": "view"}]}})  # fmt: skip
    assert access.level(user, granted) == "view"
    project = projects.change(project["id"], lambda p: {**p, "access": {}})
    # turned off: the panel's own rules again
    live.change({"github_access_from_repositories": False})
    assert access.level(user, project) == "admin"
    shown = github_access.describe(project)
    assert shown["enabled"] is False and any(p["user"] == name for p in shown["people"])


def test_stale_permissions_count_as_none(app: Flask, gh: type[FakeGitHub]) -> None:
    project = projects.by_repository("Acme/shop") or projects.create(
        repository="Acme/shop", odoo_version="18.0", edition="community", origin="linked", user="admin1"
    )
    name = _account(email="stale@example.com")
    github.link(name, github.GitHubUser(id=1013, login="stale"))
    gh.permissions["stale"] = "admin"
    github_access.refresh(name)
    user = {"name": name, "role": "developer"}
    assert access.level(user, project) == "admin"
    record = storage().get(github_access.COLLECTION, name) or {}
    record["repos"]["acme/shop"]["checked"] = time.time() - github_access.max_age() - 1
    storage().put(github_access.COLLECTION, name, record)
    github_access._cache.pop(name, None)
    github_access._asked[name] = time.time()  # no background refresh in this test
    assert access.level(user, project) == "none"


def test_github_read_gives_the_role_the_project_chose(app: Flask, gh: type[FakeGitHub]) -> None:
    """F-04: read gives Viewer in projects made now (GITHUB_READ_ROLE), Tester stays in projects made earlier."""
    newer = projects.create(repository="Acme/newer", odoo_version="18.0", edition="community", origin="linked",
                            user="admin1")  # fmt: skip
    older = projects.create(repository="Acme/older", odoo_version="18.0", edition="community", origin="linked",
                            user="admin1")  # fmt: skip
    older = projects.change(older["id"], lambda p: {**p, "access": {"mode": "panel_roles", "model": "project_roles"}})
    assert access.access_of(newer)["github_read_role"] == "viewer" and github_access.read_role(older) == "tester"
    assert settings.github_read_role == "viewer"
    name = _account(role="developer", email="reader@example.com")
    user = {"name": name, "role": "developer"}
    github.link(name, github.GitHubUser(id=1020, login="reader"))
    gh.permissions["reader"] = "read"
    github_access.refresh(name)
    assert access.level(user, newer) == "view" and access.level(user, older) == "tester"
    shown = {p["user"]: p for p in github_access.describe(newer)["people"]}[name]
    assert shown["level"] == "view" and github_access.describe(newer)["read_role"] == "viewer"
    # a person with triage keeps Tester in both
    gh.permissions["reader"] = "triage"
    github_access.refresh(name)
    assert access.level(user, newer) == "tester" and access.level(user, older) == "tester"
    # an Admin of the project switches each way
    gh.permissions["reader"] = "read"
    github_access.refresh(name)
    switched = projects.change(older["id"], lambda p: {**p, "access": {**access.access_of(p),
                                                                       "github_read_role": "viewer"}})  # fmt: skip
    assert access.level(user, switched) == "view"
    back = projects.change(newer["id"], lambda p: {**p, "access": {**access.access_of(p),
                                                                   "github_read_role": "tester"}})  # fmt: skip
    assert access.level(user, back) == "tester"


def test_github_read_role_can_be_changed_by_a_project_admin(app: Flask, gh: type[FakeGitHub], admin: Any) -> None:
    from conftest import sign_in

    project = projects.create(repository="Acme/readrole", odoo_version="18.0", edition="community", origin="linked",
                              user="admin1")  # fmt: skip
    client, headers = admin  # the suite's super admin: another one would outlive the test
    path = f"/api/v1/projects/{project['id']}/access"
    assert client.get(path, headers=headers).get_json()["data"]["github_read_role"] == "viewer"
    assert client.put(path + "/github-read-role", json={"role": "owner"}, headers=headers).status_code == 400
    done = client.put(path + "/github-read-role", json={"role": "tester"}, headers=headers)
    assert done.status_code == 200 and done.get_json()["data"]["github_read_role"] == "tester"
    assert github_access.read_role(projects.get(project["id"])) == "tester"
    assert any(e["event"] == "project access from GitHub changed" for e in storage().logs(user="admin1", limit=5))
    other = _account(role="developer")
    other_client, other_headers = sign_in(app, other, "orchid-pebble-summit-73")
    refused = other_client.put(path + "/github-read-role", json={"role": "viewer"}, headers=other_headers)
    assert refused.status_code in (403, 404) and github_access.read_role(projects.get(project["id"])) == "tester"


# ---------------------------------------------------------------- e-mail codes


class Mailbox:
    ready = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        self.sent.append((to, subject.split(" ", 1)[0]))


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[Mailbox]:
    import sudoo.mail

    fake = Mailbox()
    from sudoo.auth import email_code

    monkeypatch.setattr(sudoo.mail, "mailer", lambda: fake)
    monkeypatch.setattr(email_code, "mailer", lambda: fake)
    monkeypatch.setattr(email_sign_in, "_deliver", lambda job: job())
    email_sign_in.SEND_LIMIT.clear()
    email_sign_in.ACCOUNT_SEND_LIMIT.clear()
    accounts.THROTTLE._failures.clear()
    yield fake
    live.change(dict.fromkeys(live.overrides()))
    accounts.THROTTLE._failures.clear()


def test_email_code_sign_in(app: Flask, mailbox: Mailbox, admin: Any) -> None:
    name = _account(role="viewer", email="client.one@example.com")
    client = app.test_client()
    path = "/api/v1/session/email-code"
    # not allowed for this account yet: the same answer, nothing sent
    first = client.post(path, json={"email": "client.one@example.com"}, headers=ORIGIN)
    unknown = client.post(path, json={"email": "nobody@example.com"}, headers=ORIGIN)
    assert first.get_json()["data"] == {"sent_to": "c***@example.com"} and unknown.status_code == 200
    assert not [to for to, _ in mailbox.sent if to == "client.one@example.com"]
    admin_client, headers = admin
    assert admin_client.patch(f"/api/v1/users/{name}", json={"sign_in_methods": ["email_code"]},
                              headers=headers).status_code == 200  # fmt: skip
    assert client.post(path, json={"email": "Client.One@example.com"}, headers=ORIGIN).status_code == 200
    to, code = mailbox.sent[-1]
    assert to == "client.one@example.com"
    wrong = client.post(f"{path}/verify", json={"email": "client.one@example.com", "code": "000000"}, headers=ORIGIN)
    assert wrong.status_code == 401
    good = client.post(f"{path}/verify", json={"email": "client.one@example.com", "code": code}, headers=ORIGIN)
    assert good.status_code == 201 and good.get_json()["data"]["user"] == name
    again = client.post(f"{path}/verify", json={"email": "client.one@example.com", "code": code}, headers=ORIGIN)
    assert again.status_code == 401  # each code works once
    # the password no longer signs this account in
    refused = app.test_client().post("/api/v1/session", json={"username": name, "password": "orchid-pebble-summit-73"},
                                     headers=ORIGIN)  # fmt: skip
    assert refused.status_code == 403
    assert any(e["event"] == "signed in with an e-mail code" for e in storage().logs(user=name, limit=5))


def test_a_client_signs_in_with_an_e_mail_code_at_the_portal_only(app: Flask, mailbox: Mailbox) -> None:
    """Clients use CLIENT_SIGN_IN_METHODS (codes by e-mail) through the same e-mail sign-in; at the panel's address,
    once the portal has its own, the code is accepted but nothing is started there: the client is sent to the portal."""
    from sudoo.auth import methods

    name = _account(role="client", email="portal.client@example.com")
    assert methods.of(accounts.get(name) or {}) == ["email_code"]
    live.change({"portal_domain": "portal.example.com"})
    path = "/api/v1/session/email-code"
    panel = app.test_client()
    assert panel.post(path, json={"email": "portal.client@example.com"}, headers=ORIGIN).status_code == 200
    _, code = mailbox.sent[-1]
    sent = panel.post(f"{path}/verify", json={"email": "portal.client@example.com", "code": code}, headers=ORIGIN)
    assert sent.status_code == 200 and sent.get_json()["data"]["redirect"]["address"] == "portal"
    assert panel.get("/api/v1/session").status_code == 401
    portal = app.test_client()
    here = {"Origin": "http://portal.example.com"}
    url = "http://portal.example.com"
    assert portal.post(path, json={"email": "portal.client@example.com"}, headers=here, base_url=url).status_code == 200
    _, code = mailbox.sent[-1]
    good = portal.post(f"{path}/verify", json={"email": "portal.client@example.com", "code": code}, headers=here,
                       base_url=url)  # fmt: skip
    assert good.status_code == 201 and good.get_json()["data"]["address"] == "portal"


def test_email_codes_are_limited_per_address(app: Flask, mailbox: Mailbox) -> None:
    live.change({"email_code_sends_per_hour": 2})
    client = app.test_client()
    for _ in range(2):
        assert (
            client.post("/api/v1/session/email-code", json={"email": "x@example.com"}, headers=ORIGIN).status_code
            == 200
        )
    assert client.post("/api/v1/session/email-code", json={"email": "x@example.com"}, headers=ORIGIN).status_code == 429


def test_email_codes_are_limited_per_account_across_addresses(
    monkeypatch: pytest.MonkeyPatch, mailbox: Mailbox, admin: Any
) -> None:
    """Ten codes an hour per account whatever the address asking (EMAIL_CODE_SENDS_PER_HOUR_PER_ACCOUNT); over it
    nothing is sent and the answer does not change."""
    from sudoo.auth import RequestInfo, codes

    name = _account(role="viewer", email="busy.person@example.com", sign_in_methods=["email_code"])
    monkeypatch.setattr(codes, "RESEND_SECONDS", 0)
    live.change({"email_code_sends_per_hour": 1000, "email_code_sends_per_hour_per_account": 3})
    assert settings.email_code_sends_per_hour_per_account == 3
    answers = []
    for number in range(5):  # five different addresses, one account
        origin = RequestInfo(ip=f"203.0.113.{number + 1}")
        answers.append(email_sign_in.request_code("busy.person@example.com", origin))
    assert all(answer == {"sent_to": "b***@example.com"} for answer in answers)
    assert len([to for to, _ in mailbox.sent if to == "busy.person@example.com"]) == 3
    other = _account(role="viewer", email="calm.person@example.com", sign_in_methods=["email_code"])
    email_sign_in.request_code("calm.person@example.com", RequestInfo(ip="203.0.113.99"))
    assert mailbox.sent[-1][0] == "calm.person@example.com" and name != other  # another account is not affected


def test_the_per_account_limit_keeps_the_last_code_working(
    monkeypatch: pytest.MonkeyPatch, mailbox: Mailbox, admin: Any
) -> None:
    """Asking again over the account's limit sends nothing and does not void the code sent last."""
    from sudoo.auth import RequestInfo, codes

    _account(role="viewer", email="kept.code@example.com", sign_in_methods=["email_code"])
    monkeypatch.setattr(codes, "RESEND_SECONDS", 0)
    live.change({"email_code_sends_per_hour": 1000, "email_code_sends_per_hour_per_account": 1})
    email_sign_in.request_code("kept.code@example.com", RequestInfo(ip="203.0.113.7"))
    sent = [code for to, code in mailbox.sent if to == "kept.code@example.com"]
    assert len(sent) == 1
    for number in range(3):  # a stranger asks again from other addresses
        email_sign_in.request_code("kept.code@example.com", RequestInfo(ip=f"198.51.100.{number + 1}"))
    assert len([to for to, _ in mailbox.sent if to == "kept.code@example.com"]) == 1
    assert email_sign_in.check_code("kept.code@example.com", sent[0]) is not None


def test_the_per_account_limit_defaults_to_ten() -> None:
    assert Settings().email_code_sends_per_hour_per_account == 10


def test_ipv6_limits_count_the_whole_64(mailbox: Mailbox) -> None:
    from sudoo.auth import RequestInfo

    assert accounts.rate_key("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == accounts.rate_key("2001:db8:1:2::1")
    assert accounts.rate_key("2001:db8:1:2::1") != accounts.rate_key("2001:db8:1:3::1")
    assert accounts.rate_key("::ffff:198.51.100.7") == "198.51.100.7" == accounts.rate_key("198.51.100.7")
    assert accounts.rate_key("not an address") == "not an address"
    live.change({"email_code_sends_per_hour": 2})
    for number in range(2):  # two different addresses of one /64 share the limit
        email_sign_in.request_code("x@example.com", RequestInfo(ip=f"2001:db8:5:6::{number + 1}"))
    with pytest.raises(PermissionError):
        email_sign_in.request_code("x@example.com", RequestInfo(ip="2001:db8:5:6:ffff::9"))
    email_sign_in.request_code("x@example.com", RequestInfo(ip="2001:db8:5:7::1"))  # the next /64 has its own count


def test_wrong_tries_of_one_ipv6_64_share_the_throttle() -> None:
    for number in range(accounts.THROTTLE.MAX_PER_ACCOUNT_AND_ADDRESS):
        accounts.THROTTLE.failed(f"2001:db8:9:9::{number + 1}", "someone")
    assert accounts.THROTTLE.seconds_to_wait("2001:db8:9:9:1234::1", "someone") > 0
    assert accounts.THROTTLE.seconds_to_wait("2001:db8:9:a::1", "someone") == 0
    accounts.THROTTLE._failures.clear()


def test_the_email_code_is_not_a_second_factor_after_an_email_code(app: Flask, mailbox: Mailbox) -> None:
    from sudoo.web import signin

    account = {"second_factors": {"email": {"added": 1}}, "email": "a@example.com", "role": "admin"}
    assert signin._second_factors(account, "email_code") == []
    live.change({"require_second_factor": "admins"})
    assert signin.restriction(account, "email_code") == "second_factor"
    assert signin.restriction(account, "password") is None


def test_email_codes_need_mail(app: Flask) -> None:
    assert methods.email_code_offered() is False  # no mail server in the tests
    answer = app.test_client().post("/api/v1/session/email-code", json={"email": "a@example.com"}, headers=ORIGIN)
    assert answer.status_code == 409


# ---------------------------------------------------------------- break glass


def test_the_last_super_admin_with_a_password_keeps_it(app: Flask, admin: Any, passwords: dict[str, str]) -> None:
    client, headers = admin
    others = [n for n, u in accounts.all_users().items() if u["role"] == "super_admin" and n != "admin1"]
    for other in others:  # only admin1 signs in with a password as a super admin
        accounts.update(other, sign_in_methods=["github"])
    refused = client.patch("/api/v1/users/admin1", json={"sign_in_methods": ["github"]}, headers=headers)
    assert refused.status_code == 400 and "super admin" in refused.get_json()["detail"]
    settings_refused = client.patch("/api/v1/settings", json={"sign_in_methods": "github"}, headers=headers)
    assert settings_refused.status_code == 400
    # with Password chosen on admin1's own account, the default may drop it
    assert client.patch("/api/v1/users/admin1", json={"sign_in_methods": ["password", "github"]},
                        headers=headers).status_code == 200  # fmt: skip
    assert client.patch("/api/v1/settings", json={"sign_in_methods": "github"}, headers=headers).status_code == 200
    # a second super admin without a password does not count
    second = _account(role="super_admin")
    accounts.update(second, sign_in_methods=["github"])
    assert (
        client.patch("/api/v1/users/admin1", json={"sign_in_methods": ["github"]}, headers=headers).status_code == 400
    )
    accounts.update(second, sign_in_methods=["password"])
    assert (
        client.patch("/api/v1/users/admin1", json={"sign_in_methods": ["github"]}, headers=headers).status_code == 200
    )
    accounts.update(second, sign_in_methods=["github"])
    assert client.delete(f"/api/v1/users/{second}", headers=headers).status_code == 204  # admin1 lost it already
    client.patch("/api/v1/users/admin1", json={"sign_in_methods": None}, headers=headers)
    live.change(dict.fromkeys(live.overrides()))
    for other in others:
        accounts.update(other, sign_in_methods=None)
    assert methods.has_break_glass(accounts.get("admin1") or {})
