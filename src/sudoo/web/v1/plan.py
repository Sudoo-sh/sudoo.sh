"""The panel's Sudoo.sh plan (entitlements.py): what it allows, what the panel uses, and where to manage it.

    GET    /api/v1/plan       the plan, its source ("hub", "grace", "expired", "unlinked", "default"), whether the
                              panel is linked to an account, the entitlements (production branches, servers,
                              projects, teams, features, support level, add-ons; period and renewal when the hub
                              says them), production_withheld, banned, usage (servers, projects, production branches
                              and their names, instances by stage), why one more production branch would be refused,
                              and the links to the pricing page and the account site

Everyone signed in may read it (instances.view): the web app uses it to say what a plan allows before someone tries.
Nothing secret is in it. Changing the plan happens on the account site, never in the panel.
"""

from __future__ import annotations

from flask import Blueprint, Response

from sudoo import entitlements

from ..security import require
from .responses import iso, ok

blueprint = Blueprint("plan", __name__)


@blueprint.get("/plan")
@require("instances.view")
def plan() -> Response:
    found = entitlements.status()
    found["received_at"] = iso(found["received_at"])
    found["valid_until"] = iso(found["valid_until"])
    found["checked_at"] = iso(found["checked_at"])
    return ok(found)
