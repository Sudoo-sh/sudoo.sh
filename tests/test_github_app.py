"""The GitHub App: manifest flow, JWT, installation tokens and their cache, git's credential helper, the repository
picker and the app's webhook, all against a fake GitHub API served on 127.0.0.1."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from flask import Flask
from flask.testing import FlaskClient

from sudoo.config import Settings, settings
from sudoo.core.state import STATE
from sudoo.repos import git_credential, github, github_app
from sudoo.triggers.github_app import GitHubAppWebhook

Client = tuple[FlaskClient, dict[str, str]]

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
).decode()
WEBHOOK_SECRET = "whsec-" + "s" * 30
CLIENT_ID = "Iv23liFakeClient"
CODE = "good-code-1234"
INSTALLATION = 77


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def jwt_claims(token: str) -> dict[str, Any]:
    """The claims of a JWT after checking its RS256 signature with the app's public key (raises when invalid)."""
    header, claims, signature = token.split(".")
    assert json.loads(_unb64(header)) == {"alg": "RS256", "typ": "JWT"}
    KEY.public_key().verify(_unb64(signature), f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    result: dict[str, Any] = json.loads(_unb64(claims))
    return result


class FakeGitHub(BaseHTTPRequestHandler):
    calls: list[tuple[str, str, Any]] = []
    tokens_issued = 0

    def log_message(self, *args: Any) -> None:  # quiet
        pass

    def _send(self, status: int, body: Any) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _bearer(self) -> str:
        return self.headers.get("Authorization", "").removeprefix("Bearer ")

    def _app_jwt_ok(self) -> bool:
        try:
            claims = jwt_claims(self._bearer())
        except Exception:
            return False
        return claims["iss"] == CLIENT_ID and claims["exp"] > time.time() and claims["exp"] - claims["iat"] <= 600

    def _token_ok(self) -> bool:
        return self._bearer().startswith("ghs_")

    def _handle(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        path = self.path
        FakeGitHub.calls.append((method, path, body))
        installation = {
            "id": INSTALLATION,
            "account": {"login": "Acme", "type": "Organization"},
            "repository_selection": "all",
            "html_url": "https://github.com/organizations/Acme/settings/installations/77",
            "suspended_at": None,
        }
        if method == "POST" and path == f"/app-manifests/{CODE}/conversions":
            return self._send(
                201,
                {
                    "id": 4242,
                    "slug": "sudoo-sh-panel-ab12",
                    "name": "Sudoo.sh panel-ab12",
                    "client_id": CLIENT_ID,
                    "client_secret": "client-secret-value",
                    "webhook_secret": WEBHOOK_SECRET,
                    "pem": PEM,
                    "owner": {"login": "Acme", "type": "Organization"},
                    "html_url": "https://github.com/apps/sudoo-sh-panel-ab12",
                },
            )
        if path.startswith("/app-manifests/"):
            return self._send(404, {"message": "Not Found"})
        if path.startswith("/app/"):
            if not self._app_jwt_ok():
                return self._send(401, {"message": "A JSON web token could not be decoded"})
            if method == "GET" and path == f"/app/installations/{INSTALLATION}":
                return self._send(200, installation)
            if method == "GET" and path.startswith("/app/installations?"):
                return self._send(200, [installation])
            if method == "POST" and path == f"/app/installations/{INSTALLATION}/access_tokens":
                FakeGitHub.tokens_issued += 1
                expires = datetime.fromtimestamp(time.time() + 3600, UTC).isoformat().replace("+00:00", "Z")
                return self._send(201, {"token": f"ghs_fake{FakeGitHub.tokens_issued}", "expires_at": expires})
            if method == "PATCH" and path == "/app/hook/config":
                return self._send(200, {"url": body["url"], "content_type": "json"})
            return self._send(404, {"message": "Not Found"})
        if not self._token_ok():
            return self._send(401, {"message": "Bad credentials"})
        if path.startswith("/installation/repositories"):
            repos = [
                {"full_name": "Acme/shop", "private": True, "default_branch": "main", "pushed_at": "2026-11-10T10:00:00Z"},
                {"full_name": "Acme/hr-addons", "private": True, "default_branch": "17.0", "pushed_at": "2026-11-12T10:00:00Z"},
            ]  # fmt: skip
            return self._send(200, {"total_count": 2, "repositories": repos})
        if path == "/repos/Acme/shop":
            return self._send(200, {"default_branch": "main"})
        if path.startswith("/repos/Acme/shop/branches"):
            return self._send(200, [{"name": "main"}, {"name": "dev"}])
        return self._send(404, {"message": "Not Found"})

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")


@pytest.fixture(scope="module")
def fake_github() -> Iterator[type[FakeGitHub]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    original = github_app.API
    github_app.API = f"http://127.0.0.1:{server.server_address[1]}"
    yield FakeGitHub
    github_app.API = original
    server.shutdown()


@pytest.fixture
def no_app(fake_github: type[FakeGitHub]) -> Iterator[type[FakeGitHub]]:
    github_app.disconnect()
    FakeGitHub.calls.clear()
    yield fake_github
    github_app.disconnect()


@pytest.fixture
def connected(no_app: type[FakeGitHub]) -> type[FakeGitHub]:
    github_app.convert(CODE)
    github_app.add_installation(INSTALLATION)
    FakeGitHub.calls.clear()
    return no_app


# ---------------------------------------------------------------- JWT


def test_jwt_is_rs256_backdated_and_short_lived() -> None:
    now = 1_800_000_000
    claims = jwt_claims(github_app.make_jwt(PEM.encode(), CLIENT_ID, now=now))
    assert claims == {"iat": now - 60, "exp": now - 60 + 600, "iss": CLIENT_ID}
    assert claims["exp"] - now <= 600  # GitHub refuses tokens valid for more than ten minutes


# ---------------------------------------------------------------- manifest flow


def test_manifest_flow_stores_the_app_encrypted(admin: Client, no_app: type[FakeGitHub]) -> None:
    client, headers = admin
    started = client.post("/api/v1/github/app/manifest", json={"organization": "Acme"}, headers=headers)
    assert started.status_code == 200, started.get_json()
    data = started.get_json()["data"]
    assert data["action"] == f"https://github.com/organizations/Acme/settings/apps/new?state={data['state']}"
    manifest = data["manifest"]
    assert manifest["default_permissions"] == {"contents": "read", "metadata": "read"}
    assert manifest["default_events"] == ["push", "create", "delete"] and manifest["public"] is False
    assert manifest["redirect_url"] == "http://localhost/github/app/created"
    assert manifest["hook_attributes"]["url"].endswith("/hooks/github-app")
    assert manifest["name"].startswith("Sudoo.sh ") and len(manifest["name"]) <= 34

    wrong = client.post("/api/v1/github/app", json={"code": CODE, "state": "not-the-state"}, headers=headers)
    assert wrong.status_code == 400 and not github_app.connected()

    # the state is used once: a refused attempt ends it, so start again
    state = client.post("/api/v1/github/app/manifest", json={}, headers=headers).get_json()["data"]["state"]
    done = client.post("/api/v1/github/app", json={"code": CODE, "state": state}, headers=headers)
    assert done.status_code == 201, done.get_json()
    shown = done.get_json()["data"]
    assert shown["slug"] == "sudoo-sh-panel-ab12" and shown["install_url"].endswith("/installations/new")
    assert not {"pem", "webhook_secret", "client_secret"} & set(shown)

    stored = (settings.keys_dir / "github-app.json").read_text()
    assert "BEGIN RSA" not in stored and WEBHOOK_SECRET not in stored and "client-secret-value" not in stored
    assert oct((settings.keys_dir / "github-app.json").stat().st_mode & 0o777) == "0o600"

    again = client.post("/api/v1/github/app", json={"code": CODE, "state": state}, headers=headers)
    assert again.status_code == 400  # the state was used up


def test_an_expired_or_unknown_code_is_refused(admin: Client, no_app: type[FakeGitHub]) -> None:
    client, headers = admin
    state = client.post("/api/v1/github/app/manifest", json={}, headers=headers).get_json()["data"]["state"]
    answer = client.post("/api/v1/github/app", json={"code": "expired-code-99", "state": state}, headers=headers)
    assert answer.status_code == 400 and "404" in answer.get_json()["detail"]


def test_only_super_admins_connect(operator: Client, no_app: type[FakeGitHub]) -> None:
    client, headers = operator
    assert client.post("/api/v1/github/app/manifest", json={}, headers=headers).status_code == 403
    assert client.delete("/api/v1/github/app", headers=headers).status_code == 403


def test_organization_names_are_checked(admin: Client, no_app: type[FakeGitHub]) -> None:
    client, headers = admin
    answer = client.post("/api/v1/github/app/manifest", json={"organization": "../evil"}, headers=headers)
    assert answer.status_code == 400


# ---------------------------------------------------------------- installations


def test_installation_is_checked_with_github(admin: Client, no_app: type[FakeGitHub]) -> None:
    client, headers = admin
    github_app.convert(CODE)
    unknown = client.post("/api/v1/github/app/installations", json={"installation_id": 5}, headers=headers)
    assert unknown.status_code == 400
    added = client.post("/api/v1/github/app/installations", json={"installation_id": "77"}, headers=headers)
    assert added.status_code == 201 and added.get_json()["data"]["account"] == "Acme"
    shown = client.get("/api/v1/github/app").get_json()["data"]
    assert shown["connected"] and [i["id"] for i in shown["app"]["installations"]] == [77]
    assert github_app.installation_for("acme") == 77 and github_app.installation_for("other") is None

    synced = client.post("/api/v1/github/app/installations/sync", headers=headers)
    assert synced.status_code == 200 and [i["account"] for i in synced.get_json()["data"]] == ["Acme"]


# ---------------------------------------------------------------- tokens


def test_tokens_are_cached_encrypted_until_five_minutes_before_expiry(connected: type[FakeGitHub]) -> None:
    first = github_app.installation_token(INSTALLATION)
    assert github_app.installation_token(INSTALLATION) == first
    issued = [c for c in connected.calls if c[1].endswith("/access_tokens")]
    assert len(issued) == 1 and issued[0][2] == {"permissions": {"contents": "read", "metadata": "read"}}
    assert first not in github_app._token_file().read_text()  # encrypted in the cache

    later = time.time() + 3600 - 299  # less than five minutes left: a new token
    assert github_app.installation_token(INSTALLATION, now=later) != first
    assert len([c for c in connected.calls if c[1].endswith("/access_tokens")]) == 2


def test_credential_helper_answers_only_for_installed_owners(connected: type[FakeGitHub]) -> None:
    request = {"protocol": "https", "host": "github.com", "path": "Acme/shop.git"}
    answer = git_credential.answer("get", request)
    assert answer.startswith("username=x-access-token\npassword=ghs_")
    assert git_credential.answer("get", {**request, "path": "someone/else.git"}) == ""
    assert git_credential.answer("get", {**request, "host": "gitlab.com"}) == ""
    assert git_credential.answer("store", request) == ""


def test_git_gets_the_token_from_the_helper_only(connected: type[FakeGitHub]) -> None:
    """Real git, with the panel's configuration: `git credential fill` runs the helper as its own process, which
    reads the token cached by the panel (the fake GitHub is not reachable from there)."""
    token = github_app.installation_token(INSTALLATION)
    saved = dict(os.environ)
    try:
        github.configure_git()
        assert token not in "".join(os.environ.values())
        filled = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\npath=Acme/shop.git\n\n",
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        os.environ.clear()
        os.environ.update(saved)
    assert f"password={token}" in filled.stdout, filled.stderr


# ---------------------------------------------------------------- repository picker and branches


def test_repository_picker(admin: Client, connected: type[FakeGitHub]) -> None:
    client, _ = admin
    listed = client.get("/api/v1/github/repositories").get_json()
    assert [r["full_name"] for r in listed["data"]] == ["Acme/hr-addons", "Acme/shop"]  # newest push first
    assert listed["data"][1]["url"] == "https://github.com/Acme/shop"
    searched = client.get("/api/v1/github/repositories?q=SHOP").get_json()["data"]
    assert [r["full_name"] for r in searched] == ["Acme/shop"]


def test_repository_picker_without_app(admin: Client, no_app: type[FakeGitHub]) -> None:
    client, _ = admin
    answer = client.get("/api/v1/github/repositories").get_json()
    assert answer["data"] == [] and answer["meta"]["connected"] is False


def test_branches_come_from_the_api(connected: type[FakeGitHub]) -> None:
    assert github.branches("https://github.com/Acme/shop") == (["dev", "main"], "main", None)
    assert github.access_via("acme/shop") == "github-app"


# ---------------------------------------------------------------- webhook


@pytest.fixture
def app_webhook(connected: type[FakeGitHub], monkeypatch: pytest.MonkeyPatch) -> tuple[FlaskClient, list[Any]]:
    trigger = GitHubAppWebhook(Settings())
    requested: list[Any] = []
    trigger.start(lambda name, reason: requested.append((name, reason)))
    monkeypatch.setattr(
        trigger, "instances_following", lambda repo, branch: ["shop"] if (repo, branch) == ("acme/shop", "main") else []
    )
    flask_app = Flask(__name__)
    blueprint = trigger.blueprint()
    assert blueprint is not None
    flask_app.register_blueprint(blueprint)
    return flask_app.test_client(), requested


def _deliver(client: FlaskClient, event: str, payload: dict[str, Any], secret: str = WEBHOOK_SECRET) -> Any:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Event": event}
    return client.post("/hooks/github-app", data=body, headers=headers, content_type="application/json")


def test_app_webhook_push(app_webhook: tuple[FlaskClient, list[Any]]) -> None:
    client, requested = app_webhook
    answer = _deliver(client, "push", {"ref": "refs/heads/main", "repository": {"full_name": "Acme/Shop"}})
    assert answer.status_code == 200 and answer.get_json()["instances"] == ["shop"]
    assert requested == [("shop", "push to acme/shop main")]


def test_app_webhook_refuses_other_signatures(app_webhook: tuple[FlaskClient, list[Any]]) -> None:
    client, requested = app_webhook
    payload = {"ref": "refs/heads/main", "repository": {"full_name": "acme/shop"}}
    assert _deliver(client, "push", payload, secret="x" * 32).status_code == 401
    unsigned = client.post("/hooks/github-app", data=b"{}", headers={"X-GitHub-Event": "push"})
    assert unsigned.status_code == 401 and not requested


def test_app_webhook_branch_deleted_and_uninstalled(app_webhook: tuple[FlaskClient, list[Any]]) -> None:
    client, _ = app_webhook
    deleted = {"ref": "main", "ref_type": "branch", "repository": {"full_name": "acme/shop"}}
    assert _deliver(client, "delete", deleted).get_json()["instances"] == ["shop"]
    assert STATE.get("shop")["branch_missing"] is True
    _deliver(client, "create", deleted)
    assert STATE.get("shop")["branch_missing"] is False
    STATE.remove("shop")

    removed = {"action": "deleted", "installation": {"id": INSTALLATION, "account": {"login": "Acme"}}}
    assert _deliver(client, "installation", removed).status_code == 200
    assert github_app.installations() == []


def test_app_webhook_without_app(no_app: type[FakeGitHub]) -> None:
    trigger = GitHubAppWebhook(Settings())
    flask_app = Flask(__name__)
    blueprint = trigger.blueprint()
    assert blueprint is not None
    flask_app.register_blueprint(blueprint)
    assert _deliver(flask_app.test_client(), "ping", {}).status_code == 404


def test_webhook_address_follows_the_panel(connected: type[FakeGitHub]) -> None:
    assert github_app.update_hook_url("https://new-words.trycloudflare.com/")
    patch = [c for c in connected.calls if c[0] == "PATCH"]
    assert patch == [
        (
            "PATCH",
            "/app/hook/config",
            {"url": "https://new-words.trycloudflare.com/hooks/github-app", "content_type": "json"},
        )
    ]
    assert not github_app.update_hook_url("https://new-words.trycloudflare.com")  # unchanged: GitHub is not called


# ---------------------------------------------------------------- disconnect


def test_disconnect_forgets_everything(admin: Client, connected: type[FakeGitHub]) -> None:
    client, headers = admin
    github_app.installation_token(INSTALLATION)
    answer = client.delete("/api/v1/github/app", headers=headers)
    assert answer.status_code == 200
    assert answer.get_json()["data"]["settings_url"] == (
        "https://github.com/organizations/Acme/settings/apps/sudoo-sh-panel-ab12"
    )
    assert not (settings.keys_dir / "github-app.json").exists() and not github_app._token_file().exists()
    assert client.get("/api/v1/github/app").get_json()["data"]["connected"] is False
