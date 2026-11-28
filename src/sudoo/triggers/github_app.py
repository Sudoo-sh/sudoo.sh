"""Update trigger "github-app": the panel's GitHub App (repos/github_app.py) tells it about pushes within seconds.

Nothing to set up per repository: the app's webhook (<panel address>/hooks/github-app) is part of the app GitHub
created from the panel's manifest, and receives the events of every repository the app is installed on:

    push                       updates every instance that follows the pushed repository and branch
                               (and has "Update automatically" on), like the github-webhook trigger
    create / delete            a branch was created or deleted: instances following it show or clear the
                               "branch deleted on GitHub" warning, and the repository's project creates a
                               development instance for a new branch or archives the instances of a deleted one
                               (projects/automation.py)
    pull_request               a pull request opened, got new commits or closed: the project's preview instance
                               follows (projects/previews.py, when the project has pull-request previews on)
    installation               the app was installed, removed, suspended or given back on an account: the panel's
                               list of installations follows
    installation_repositories  repositories were added to or removed from an installation

Every delivery is checked with GitHub's signature (X-Hub-Signature-256: HMAC-SHA256 of the body with the app's
webhook secret, created by GitHub with the app and stored encrypted). While no app is connected the endpoint answers
404. When the panel's public address changes (Cloudflare quick tunnels), the panel updates the app's webhook address
on GitHub itself (background.py).
"""

from __future__ import annotations

from typing import Any

from flask import Response, abort, jsonify, request

from sudoo.config import Settings
from sudoo.core.state import STATE
from sudoo.repos import github_app
from sudoo.triggers import deliveries
from sudoo.triggers.github_webhook import GitHubWebhook


class GitHubAppWebhook(GitHubWebhook):
    """Receives the GitHub App's events at /hooks/github-app."""

    path = github_app.HOOK_PATH
    endpoint = "github_app_webhook"

    def __init__(self, settings: Settings) -> None:  # noqa: ARG002 - the app's own secret is used, not a setting
        self._request_update = None

    def signature_valid(self, body: bytes, header: str) -> bool:
        return github_app.signature_valid(body, header)

    def receive(self) -> Response:
        if not github_app.connected():
            abort(404, "No GitHub App is connected to this panel.")
        if not self.signature_valid(request.get_data(), request.headers.get("X-Hub-Signature-256", "")):
            abort(401, "Invalid signature: the delivery was not signed with this panel's GitHub App secret.")
        event = request.headers.get("X-GitHub-Event", "")
        payload: dict[str, Any] = request.get_json(silent=True) or {}
        if event == "ping":
            return jsonify(ok=True, message="Webhook received. Pushes will update the matching instances.")
        return deliveries.once(  # the same delivery (or the same push sent by a webhook too) is handled once
            deliveries.keys_of(request.headers.get("X-GitHub-Delivery"), event, payload),
            lambda: self.handle(event, payload),
            lambda: jsonify(ok=True, duplicate=True, message="Delivered already: it was handled once."),
        )

    def handle(self, event: str, payload: dict[str, Any]) -> Response:
        if event == "push":
            return self.push(payload)
        if event in ("create", "delete") and payload.get("ref_type") == "branch":
            from sudoo.projects import automation, charged  # late: projects/ imports the instances package

            repository = str((payload.get("repository") or {}).get("full_name", "")).lower()
            branch = str(payload.get("ref", ""))
            following = self.instances_following(repository, branch)
            for name in following:
                STATE.update(name, branch_missing=event == "delete")
            if event == "create":
                return jsonify(
                    ok=True,
                    instances=following,
                    created=automation.branch_created(repository, branch, github_id=charged.sender_id(payload)),
                )
            return jsonify(ok=True, instances=following, branch_deleted=automation.branch_deleted(repository, branch))
        if event == "pull_request":
            from sudoo.projects import previews  # late: projects/ imports the instances package

            return jsonify(ok=True, **previews.pull_request(payload))
        if event == "installation":
            return self.installation(payload)
        if event == "installation_repositories":
            github_app.forget_repositories()
            return jsonify(ok=True)
        return jsonify(ok=True, message=f"Ignored the {event or 'unknown'} event.")

    @staticmethod
    def installation(payload: dict[str, Any]) -> Response:
        found = payload.get("installation") or {}
        action = str(payload.get("action", ""))
        if not found.get("id"):
            return jsonify(ok=True)
        if action == "deleted":
            github_app.remove_installation(int(found["id"]))
        else:  # created, suspend, unsuspend, new_permissions_accepted
            github_app.record_installation(found)
        return jsonify(ok=True, action=action)
