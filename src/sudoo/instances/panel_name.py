"""The panel's own managed name with EXPOSURE=direct: `<label>.<company>.<base>` (panel.acme.sudoo.cloud).

Next to the names of its instances (managed_names.py, `<branch>.<stage>.<company>.<base>`), a panel linked to a
Sudoo.sh account that offers managed subdomains may get a name for itself. Instance names have three labels under
the base domain, the panel's name two, so they never clash. The hub owns the DNS (A and AAAA records from the
heartbeat's public addresses, following them when they change); the panel serves the name like its other names:
the gateway answers it on ports 80 and 443 and the core's ACME client gets and renews its certificate
(panel_address.round_, called by the domain worker's rounds).

    GET    /api/v1/installations/panel-name      {offered, base_domain, company, label, counts, name}
    PUT    /api/v1/installations/panel-name      {company?} -> the name (201 made; 200 held: moved here, renamed)
    DELETE /api/v1/installations/panel-name      release it (204)

It is the panel's address only when nobody gave the panel a name (panel_address.domain(): PANEL_DOMAIN, else
PUBLIC_HOST when it is a name, else this name, else the server's address). `sync()` runs with the managed names'
check (every MANAGED_SUBDOMAINS_CHECK_MINUTES, on Check now, when a setting it depends on changes):

    wanted and offered, not held here        ask for it (PUT): made, or moved to this server, or renamed
    held here but not wanted or not offered  release it (a name was set, a setting turned off, the hub stopped)
    not linked (any more)                    forget it (the hub releases it when a server is unlinked)

While the hub cannot be reached the name held stays in use. The state is a document in the storage (collection
"panel_name", key "state"): offered, hostname (held by this server, else None), label, company, base_domain,
counts, checked_at, error, served (the name the gateway was last told about).
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from sudoo.config import settings
from sudoo.core import audit, events
from sudoo.storage import storage

PATH = "/api/v1/installations/panel-name"
COLLECTION = "panel_name"
KEY = "state"
TOPIC = "managed_names"  # the Settings page listens to the managed names' topic already

_RUNNING = threading.Lock()


def state() -> dict[str, Any]:
    return storage().get(COLLECTION, KEY) or {}


def _save(**values: Any) -> dict[str, Any]:
    return storage().change(COLLECTION, KEY, lambda doc: {**(doc or {}), **values}) or {}


def linked() -> bool:
    """A panel linked to a Sudoo.sh account (the marketplace on, and the link recorded)."""
    if not settings.marketplace:
        return False
    from sudoo.hub import marketplace

    return bool(marketplace.state().get("linked"))


def wanted() -> bool:
    """Whether this panel uses a managed name of its own: direct mode, MANAGED_SUBDOMAINS and PANEL_MANAGED_NAME on, a
    linked main panel (not a server's agent), and no name given by the owner (PANEL_DOMAIN, PUBLIC_HOST)."""
    from sudoo.premium import servers

    from . import panel_address

    return bool(
        panel_address.direct()
        and settings.managed_subdomains
        and settings.panel_managed_name
        and not panel_address.owner_domain()
        and not servers.is_agent()
        and linked()
    )


def hostname() -> str:
    """The managed name the panel is served under now; "" when none (not wanted, not offered, not held here)."""
    found = state()
    if not found.get("offered") or not wanted():
        return ""
    return str(found.get("hostname") or "")


def _hub(method: str, body: Any = None) -> Any:
    from sudoo.hub import marketplace

    return marketplace._for_panel(method, PATH, body)


def _installation_id() -> str | None:
    from sudoo.hub import client

    try:
        return client.identity().installation_id
    except Exception:  # noqa: BLE001 - no identity (yet): nothing is held here
        return None


def _checked(name: Any, base: str) -> str | None:
    """The name's hostname when it is a valid name under the hub's base domain, else None."""
    if not isinstance(name, dict):
        return None
    found = str(name.get("hostname") or "").lower()
    if not re.fullmatch(r"[a-z0-9.-]{3,253}", found) or ".." in found or not base or not found.endswith("." + base):
        return None
    return found


def _release(found: str) -> str | None:
    """Release the account's panel name at the hub; None when done, else why not (tried again at the next sync)."""
    from sudoo.hub import HubRefused, HubUnavailable

    try:
        _hub("DELETE")
    except HubRefused as error:
        return None if error.status == 404 else str(error.detail)[:300]
    except HubUnavailable as error:
        return str(error)[:300]
    audit.record("system", None, "panel name released", detail=found)
    return None


def sync(now: float | None = None) -> dict[str, Any]:
    """Compare what the panel wants with what the hub holds (see the module's description); the state afterwards.
    Never raises for the hub: its error is kept in the state."""
    from sudoo.premium import servers

    if servers.is_agent():
        return state()
    now = time.time() if now is None else now
    with _RUNNING:
        _sync(now)
    return _after()


def _sync(now: float) -> dict[str, Any]:
    from sudoo.hub import HubRefused, HubUnavailable

    if not linked():
        if state().get("hostname") or state().get("offered"):
            return _save(offered=False, hostname=None, checked_at=now, error=None)
        return state()
    try:
        answer = _hub("GET")
    except HubRefused as error:
        if error.code == "link_required" or error.status == 404:  # unlinked, or a hub without panel names
            return _save(offered=False, hostname=None, checked_at=now, error=None)
        return _save(checked_at=now, error=str(error.detail)[:300])
    except HubUnavailable as error:  # the name held stays in use
        return _save(checked_at=now, error=str(error)[:300])
    data = answer.data if isinstance(answer.data, dict) else {}
    offered = bool(data.get("offered"))
    base = str(data.get("base_domain") or "").lower().strip(".")
    company = str(data.get("company") or "") or None
    label = str(data.get("label") or "") or None
    facts = {"offered": offered, "base_domain": base or None, "company": company, "label": label,
             "counts": bool(data.get("counts")), "checked_at": now}  # fmt: skip
    name = data.get("name") if isinstance(data.get("name"), dict) else None
    me = _installation_id()
    mine = name is not None and me is not None and str(name.get("server_id") or "") == me
    held = _checked(name, base) if mine else None
    if offered and wanted():
        expected = f"{label}.{company}.{base}" if label and company and base else None
        if held is None or (expected is not None and held != expected):
            body: dict[str, Any] = {}
            if settings.managed_subdomain_company and not company:
                body["company"] = settings.managed_subdomain_company
            try:
                given = _hub("PUT", body)
            except HubRefused as error:
                return _save(**facts, hostname=None, error=str(error.detail)[:300])
            except HubUnavailable as error:
                return _save(**facts, hostname=held, error=str(error)[:300])
            made = _checked(given.data, base)
            if made is None:
                return _save(**facts, hostname=held, error="Sudoo.sh answered without a valid name.")
            if made != held:
                audit.record("system", None, "panel name added", detail=made)
            held = made
            if isinstance(given.data, dict) and given.data.get("company"):
                facts["company"] = str(given.data["company"])
        return _save(**facts, hostname=held, error=None)
    problem = _release(held) if held else None  # held here but not to be used: released
    return _save(**facts, hostname=None, error=problem)


def forget() -> None:
    """The panel logged out of its account: the hub released its name; stop serving it now."""
    _save(offered=False, hostname=None, error=None)
    _after()


def _after() -> dict[str, Any]:
    """When the name served changed since the last time (`served`): the gateway answers the new one and the domain
    worker gets its certificate. The state afterwards."""
    now_served = hostname() or None
    found = state()
    if found.get("served") == now_served:
        return found
    from . import custom_domains

    found = _save(served=now_served)
    custom_domains.refresh_gateway()
    custom_domains.WAKE.set()
    events.publish(TOPIC, "state")
    return found


def resource() -> dict[str, Any]:
    """What Settings shows about the panel's managed name."""
    found = state()
    return {
        "setting": bool(settings.panel_managed_name),
        "wanted": wanted(),
        "offered": bool(found.get("offered")),
        "hostname": hostname() or None,
        "held": found.get("hostname"),
        "counts": bool(found.get("counts")),
        "checked_at": found.get("checked_at"),
        "error": found.get("error"),
    }
