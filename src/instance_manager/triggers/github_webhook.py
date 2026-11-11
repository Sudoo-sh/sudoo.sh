"""Update trigger "github-webhook": GitHub calls the panel the moment code is pushed, so updates start in seconds.

Add a webhook to each repository on GitHub (repository Settings, Webhooks, Add webhook):

    Payload URL    <panel address>/hooks/github
    Content type   application/json
    Secret         the value of GITHUB_WEBHOOK_SECRET
    Events         Just the push event

Every call is checked with GitHub's signature (X-Hub-Signature-256: HMAC-SHA256 of the body with the secret), so
only GitHub, which knows the secret, can trigger updates. A push updates every instance that follows the pushed
repository and branch and has "Update automatically" on.

The panel needs an address that does not change (EXPOSURE=host-ports, or a domain): with cloudflare-quick the
address changes when the server restarts and the webhook would have to be edited each time. Keep "polling" enabled
next to it as a safety net if you like: UPDATE_TRIGGERS=github-webhook,polling.
"""

from __future__ import annotations

import hashlib
import hmac

from flask import Blueprint, Response, abort, jsonify, request

from instance_manager.config import Settings
from instance_manager.instances import store
from instance_manager.repos import github
from instance_manager.repos.gitrepo import origin_url
from instance_manager.triggers.base import RequestUpdate


class GitHubWebhook:
    """Receives GitHub push events at /hooks/github."""

    def __init__(self, settings: Settings) -> None:
        if len(settings.github_webhook_secret) < 20:
            raise ValueError(
                "UPDATE_TRIGGERS=github-webhook needs GITHUB_WEBHOOK_SECRET: 20 or more random characters."
            )
        self.secret = settings.github_webhook_secret.encode()
        self._request_update: RequestUpdate | None = None

    def start(self, request_update: RequestUpdate) -> None:
        self._request_update = request_update

    def blueprint(self) -> Blueprint | None:
        hooks = Blueprint("github_webhook", __name__)
        hooks.add_url_rule("/hooks/github", "receive", self.receive, methods=["POST"])
        return hooks

    def signature_valid(self, body: bytes, header: str) -> bool:
        expected = "sha256=" + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, header)

    def receive(self) -> Response:
        if not self.signature_valid(request.get_data(), request.headers.get("X-Hub-Signature-256", "")):
            abort(401, "Invalid signature: check that the webhook's secret matches GITHUB_WEBHOOK_SECRET.")
        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return jsonify(ok=True, message="Webhook received. Push events will update the matching instances.")
        if event != "push":
            return jsonify(ok=True, message=f"Ignored the {event or 'unknown'} event; only push events are used.")
        payload = request.get_json(silent=True) or {}
        repository = str((payload.get("repository") or {}).get("full_name", "")).lower()
        branch = str(payload.get("ref", "")).removeprefix("refs/heads/")
        updated = self.instances_following(repository, branch)
        assert self._request_update is not None
        for name in updated:
            self._request_update(name, f"push to {repository} {branch}")
        return jsonify(ok=True, instances=updated)

    @staticmethod
    def instances_following(repository: str, branch: str) -> list[str]:
        """Instances whose code comes from `repository` (owner/name, lower case) and `branch`."""
        found = []
        for name in store.names():
            env = store.read_env(name)
            if env["GIT_BRANCH"] == branch and github.repo_id(origin_url(store.addons_dir(name))) == repository:
                found.append(name)
        return found
