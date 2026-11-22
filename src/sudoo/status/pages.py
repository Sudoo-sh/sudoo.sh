"""Public status pages: a read-only link per project, off until someone who manages the project turns it on.

The link carries a random token (32 bytes, URL-safe): /status/<token> in the web app, which reads
GET /api/v1/public-status/<token> without a session. Only a SHA-256 hash of the token is looked up; the token itself
is kept encrypted (core/vault.py, label "status-page:<project>") so the link can be shown again to the project's
managers. A new token makes the old link stop working. STATUS_PUBLIC_PAGES off answers 404 for every page.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from typing import Any

from sudoo.config import settings
from sudoo.core import vault
from sudoo.storage import Document, storage

from . import PAGES

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,64}$")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _label(project_id: str) -> str:
    return f"status-page:{project_id}"


def get(project_id: str) -> Document | None:
    return storage().get(PAGES, project_id)


def view(project_id: str) -> dict[str, Any]:
    """{"enabled", "allowed", "path"}: the page's link for the project's managers (path None while it is off)."""
    found = get(project_id) or {}
    path = None
    if found.get("enabled") and found.get("token"):
        try:
            path = f"/status/{vault.decrypt(found['token'], _label(project_id))}"
        except vault.VaultError:
            path = None
    return {"enabled": bool(found.get("enabled")), "allowed": bool(settings.status_public_pages), "path": path}


def change(project_id: str, enabled: bool, new_token: bool, user: str) -> dict[str, Any]:
    """Turn the page on or off; a new token on request (and the first time it is turned on)."""

    def apply(found: Document | None) -> Document:
        doc = dict(found or {"project": project_id})
        if enabled and (new_token or not doc.get("token")):
            token = secrets.token_urlsafe(32)
            doc.update(token=vault.encrypt(token, _label(project_id)), hash=_hash(token))
        doc.update(enabled=enabled, changed_by=user, changed_at=round(time.time()))
        return doc

    storage().change(PAGES, project_id, apply)
    return view(project_id)


def project_of(token: str) -> str | None:
    """The project whose page this token opens, or None (unknown token, page off, or public pages off)."""
    if not settings.status_public_pages or not TOKEN_RE.match(token or ""):
        return None
    wanted = _hash(token)
    for project_id, doc in storage().all(PAGES).items():
        if doc.get("enabled") and hmac.compare_digest(str(doc.get("hash", "")), wanted):
            return project_id
    return None


def remove(project_id: str) -> None:
    storage().delete(PAGES, project_id)
