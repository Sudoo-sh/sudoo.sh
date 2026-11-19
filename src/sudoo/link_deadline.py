"""Linking the panel to a Sudoo.sh account within LINK_DEADLINE_DAYS of its first run.

The first run
    The first time the panel runs (or the first time this version runs, for a panel installed before), the time is
    recorded once in the panel's storage (collection "installation", document "first_run"). Nothing changes it
    afterwards: no setting, no restart, no new version.

Linked
    The panel is linked when the marketplace is on (MARKETPLACE) and the hub linked it to an account
    (hub/marketplace.py, Settings, Marketplace). Linking takes effect at once; logging out of the account (or turning
    the marketplace off) makes the panel unlinked again.

Before the deadline
    Super admins and administrators see a banner on every page that cannot be closed: "Link this panel to your
    Sudoo.sh account", with the days left (the web app, from GET /api/v1/plan, "link_deadline"). Nothing is limited.

After the deadline (first run + LINK_DEADLINE_DAYS, 7 by default), while unlinked
    Managing is limited (`refusal`, 403): no new instances (by hand, by API or for a new branch), no stage change into
    production, no updates or deploys (get latest code, submodules, switch branch, connect a repository, upgrade
    modules, new images, rebuild staging; pushes do not update instances either). Everything that runs keeps
    running: instances keep serving, start, pause, restart, backups (daily and by hand), restores, monitoring and
    certificates go on. Linking lifts the limits at once.

The time is the marketplace's trusted time (never earlier than what the panel already saw), so turning the clock
back does not give more days.
"""

from __future__ import annotations

import math
import time
from typing import Any

from sudoo.config import settings
from sudoo.storage import storage

COLLECTION = "installation"
FIRST_RUN = "first_run"
DAY = 86400

DEPLOY_ACTIONS = frozenset(
    {
        "update",
        "update-submodules",
        "add-submodule",
        "remove-submodule",
        "switch-branch",
        "connect-repository",
        "upgrade",
        "update-images",
    }
)
"""The instance actions (web/v1/instances.ACTIONS) that wait for the link once the deadline has passed."""


def first_run() -> float:
    """When the panel first ran: recorded the first time this is asked, never changed afterwards."""
    found = storage().get(COLLECTION, FIRST_RUN)
    if found and isinstance(found.get("at"), int | float):
        return float(found["at"])
    now = time.time()

    def keep(doc: dict[str, Any] | None) -> dict[str, Any]:
        return doc if doc and isinstance(doc.get("at"), int | float) else {"at": now}

    stored = storage().change(COLLECTION, FIRST_RUN, keep) or {"at": now}
    return float(stored["at"])


def linked() -> bool:
    """Whether the panel is linked to a Sudoo.sh account now."""
    if not settings.marketplace:
        return False
    from sudoo.hub import marketplace  # late: the hub imports much of the panel

    return bool(marketplace.state().get("linked"))


def _now() -> float:
    try:
        from sudoo.hub import marketplace

        return max(time.time(), marketplace.trusted_now())
    except Exception:  # noqa: BLE001 - the plain clock when the marketplace's state cannot be read
        return time.time()


def status(now: float | None = None) -> dict[str, Any]:
    """{"linked", "first_run", "deadline_days", "deadline", "days_left", "restricted"} (times as Unix seconds).
    `days_left` counts started days (1 on the last day), 0 once the deadline has passed or the panel is linked."""
    from sudoo.premium import servers

    now = _now() if now is None else now
    if servers.is_agent():  # a server's agent: the main panel decides (premium/servers.py)
        restricted = bool(servers.PANEL.get("link_restricted"))
        return {"linked": not restricted, "first_run": now, "deadline_days": int(settings.link_deadline_days),
                "deadline": now, "days_left": 0, "restricted": restricted}  # fmt: skip
    first = first_run()
    days = int(settings.link_deadline_days)
    deadline = first + days * DAY
    is_linked = linked()
    passed = now >= deadline
    return {
        "linked": is_linked,
        "first_run": first,
        "deadline_days": days,
        "deadline": deadline,
        "days_left": 0 if is_linked or passed else max(1, math.ceil((deadline - now) / DAY)),
        "restricted": not is_linked and passed,
    }


def restricted(now: float | None = None) -> bool:
    """Whether the deadline has passed and the panel is still not linked."""
    return bool(status(now)["restricted"])


def refusal(what: str, now: float | None = None) -> str | None:
    """Why `what` ("creating instances", ...) is refused now, or None when it is not limited."""
    found = status(now)
    if not found["restricted"]:
        return None
    return (
        f"This panel is not linked to a Sudoo.sh account, and the {found['deadline_days']} days to link it have "
        f"passed: {what} waits until a super admin links it (Settings, Marketplace). Instances keep serving and "
        "backups keep running."
    )
