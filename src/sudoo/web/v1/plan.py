"""The panel's Sudoo.sh plan (entitlements.py): what it allows, what the panel uses, and where to manage it.

    GET    /api/v1/plan       the plan, its source ("hub", "grace", "expired", "unlinked", "default"), whether the
                              panel is linked to an account, the entitlements (production branches, servers,
                              projects, teams, features, support level, add-ons; period and renewal when the hub
                              says them), production_withheld, banned, usage (servers, projects, production branches
                              and their names, instances by stage), why one more production branch would be refused,
                              and the links to the pricing page and the account site; link_deadline: whether
                              the panel is linked, its first run, the deadline to link it, the days left and
                              whether managing is limited because the deadline passed (link_deadline.py); for
                              super admins and administrators only (null for everyone else, who see no banner)

Everyone signed in may read it (instances.view): the web app uses it to say what a plan allows before someone tries.
Nothing secret is in it. Changing the plan happens on the account site, never in the panel.
"""

from __future__ import annotations

from flask import Blueprint, Response, g

from sudoo import entitlements, link_deadline, roles

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
    found["link_deadline"] = None
    if g.user["role"] in roles.ADMINISTRATORS:  # only super admins and administrators see the banner
        deadline = link_deadline.status()
        found["link_deadline"] = {
            **deadline,
            "first_run": iso(deadline["first_run"]),
            "deadline": iso(deadline["deadline"]),
        }
    return ok(found)
