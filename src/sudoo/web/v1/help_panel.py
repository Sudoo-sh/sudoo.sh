"""What the web app's help panel (the "?" button) needs from the server.

    GET /api/v1/help    {"version", "docs_url", "support": {"kind", "url"}}

support.kind:
    hub     the panel is linked to a Sudoo.sh account (Settings, Marketplace): the hub's help desk (HUB_SUPPORT_URL)
    link    SUPPORT_URL, an https:// address set by an administrator
    email   SUPPORT_URL as mailto:, or else ADMIN_CONTACT_EMAIL
    none    nothing set up: the panel shows who to contact (ADMIN_CONTACT_NAME) instead

Every signed-in person may read it (instances.view, which every role has). Nothing in it is secret.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response

from sudoo.config import settings
from sudoo.hub import marketplace

from ..security import require
from .responses import ok
from .server import _version

blueprint = Blueprint("help_panel", __name__)


def support() -> dict[str, Any]:
    linked = False
    if settings.marketplace:
        try:
            linked = bool(marketplace.state().get("linked"))
        except (OSError, ValueError):
            linked = False
    if linked:
        return {"kind": "hub", "url": settings.hub_support_url}
    if settings.support_url.lower().startswith("mailto:"):
        return {"kind": "email", "url": settings.support_url}
    if settings.support_url:
        return {"kind": "link", "url": settings.support_url}
    if settings.admin_contact_email:
        return {"kind": "email", "url": f"mailto:{settings.admin_contact_email}"}
    return {"kind": "none", "url": None, "name": settings.admin_contact_name}


@blueprint.get("/help")
@require("instances.view")
def help_info() -> Response:
    return ok({"version": _version(), "docs_url": settings.docs_url, "support": support()})
