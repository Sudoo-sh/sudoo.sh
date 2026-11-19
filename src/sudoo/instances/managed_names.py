"""Managed subdomains: a name under Sudoo.sh's own domain for each instance of this server, on every plan.

The name is `<branch>.<stage>.<company>.<base>` (for example main.production.acme.sudoo.cloud). The hub owns the
DNS: it creates the A and AAAA records from the server's public addresses (the heartbeat's public_ipv4 and
public_ipv6, hub/marketplace.py) and moves them when the addresses change. The panel asks for and releases the
names (signed panel requests, hub contract "Managed subdomains") and serves them like custom domains that exist: the
gateway answers them on DOMAIN_HTTP_PORT / DOMAIN_HTTPS_PORT, and the core's ACME client gets and renews their
certificates (HTTP-01; domain_worker.py, acme.py, certificates.py). No premium package is involved.

    GET    /api/v1/installations/subdomains              {enabled, base_domain, company, limit, used, subdomains}
    POST   /api/v1/installations/subdomains              {branch, stage, company?} -> the name (201, or 200: held)
    DELETE /api/v1/installations/subdomains/<hostname>   release it (204; 404: not held)

Each name is a record of the custom domains' collection (instances/custom_domains.py) with "managed": true and the
labels it was asked for ("label", "stage"); it is never the instance's primary custom domain, and people cannot
remove it: it follows the instance. `sync()` (every MANAGED_SUBDOMAINS_CHECK_MINUTES, after an instance is created,
deleted or changes branch or stage, and on Check now) compares this server's instances with their names:

    an instance without its name        ask for it; the hub's answer is the name (its company label and base)
    a name whose labels no longer fit   ask for the new one, then release the old one (a branch switch, a stage)
    a name whose instance is gone       release it; a release the hub could not do now is tried again later

The label of an instance is its branch (the instance's name for plain Odoo, which has no branch), made a DNS label
the way the hub wants it (`label()`). Two instances with the same branch and stage share one name: the first keeps
it. The instance's address (exposure.address) is its primary custom domain when it has one, else its managed name
once that has a certificate, else the exposure plugin's address.

The hub's switch (`enabled`, its base domain) comes with every list; with it off, or MANAGED_SUBDOMAINS off, no name
is asked for and none is served (names held stay held and are released when their instance goes).
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any
from urllib.parse import quote

from sudoo.config import settings
from sudoo.core import audit, events
from sudoo.storage import storage

PATH = "/api/v1/installations/subdomains"
STATE_COLLECTION = "managed_names"
TOPIC = "managed_names"
STAGES = ("production", "staging", "development")
MAX_LABEL = 63

WAKE = threading.Event()
_RUNNING = threading.Lock()


def label(text: str) -> str:
    """A DNS label from a branch or an instance name, as the hub takes it: lower case, every run of characters outside
    a-z0-9 becomes "-", no "-" at either end, at most 63 characters, never "--" in places 3-4 (no "xn--"); "" when
    nothing is left."""
    found = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:MAX_LABEL].strip("-")
    if len(found) >= 4 and found[2:4] == "--":
        found = (found[:2] + "-" + found[4:].lstrip("-")).strip("-")
    return found


def on() -> bool:
    """Whether managed names are asked for and served: MANAGED_SUBDOMAINS, a linked panel, and the hub offers them."""
    return bool(settings.managed_subdomains and settings.marketplace and state().get("enabled"))


def state() -> dict[str, Any]:
    """What the hub said last: enabled, base_domain, company, limit, used, checked_at, error, pending_release."""
    return storage().get(STATE_COLLECTION, "state") or {}


def _save(**values: Any) -> dict[str, Any]:
    return storage().change(STATE_COLLECTION, "state", lambda doc: {**(doc or {}), **values}) or {}


def records() -> dict[str, dict[str, Any]]:
    """The managed names this server holds, by hostname."""
    from . import custom_domains

    return {d: r for d, r in custom_domains.all_records().items() if r.get("managed")}


def of_instance(name: str) -> dict[str, Any] | None:
    return next((r for r in records().values() if r.get("instance") == name), None)


def wanted(name: str) -> tuple[str, str] | None:
    """(label, stage) the instance's name is asked for with, or None (no usable label, or no such instance)."""
    from . import stages, store

    if not store.exists(name):
        return None
    env = store.read_env(name)
    stage = stages.of(env)
    found = label(env.get("GIT_BRANCH") or name)
    return (found, stage) if found and stage in STAGES else None


def resource(name: str) -> dict[str, Any] | None:
    """The instance's managed name as the API shows it, or None."""
    from . import custom_domains

    record = of_instance(name)
    if record is None:
        return None
    serving = on() and custom_domains.serving(record)
    return {
        "hostname": record["domain"],
        "status": custom_domains.shown_status(record),
        "reason": record.get("reason"),
        "serving": serving,
        "url": f"https://{record['domain']}" if serving else None,
    }


def url(name: str) -> str | None:
    """https://<managed name> when it is served now, else None (exposure.address)."""
    found = resource(name)
    return found["url"] if found else None


# ---------------------------------------------------------------- the hub


def _hub(method: str, path: str, body: Any = None) -> Any:
    from sudoo.hub import marketplace

    return marketplace._for_panel(method, path, body)


def _ask(record_label: str, stage: str) -> dict[str, Any]:
    body: dict[str, Any] = {"branch": record_label, "stage": stage}
    if settings.managed_subdomain_company and not state().get("company"):
        body["company"] = settings.managed_subdomain_company
    answer = _hub("POST", PATH, body)
    data = answer.data if isinstance(answer.data, dict) else {}
    hostname = str(data.get("hostname") or "").lower()
    if not re.fullmatch(r"[a-z0-9.-]{3,253}", hostname) or ".." in hostname:
        from sudoo.hub import HubUnavailable

        raise HubUnavailable("Sudoo.sh answered without a valid name.")
    return data


def _release(hostname: str) -> bool:
    """Release a name at the hub: True once it is gone there (released now, or not held)."""
    from sudoo.hub import HubRefused, HubUnavailable

    try:
        _hub("DELETE", f"{PATH}/{quote(hostname)}")
    except HubRefused as error:
        if error.status == 404:
            return True
        _save(error=str(error.detail)[:300])
        return False
    except HubUnavailable as error:
        _save(error=str(error)[:300])
        return False
    audit.record("system", None, "managed name released", detail=hostname)
    return True


def release_later(hostnames: list[str]) -> None:
    """Names whose instance is gone: released at the next sync (soon)."""
    if not hostnames:
        return
    pending = sorted(set(state().get("pending_release") or []) | set(hostnames))
    _save(pending_release=pending)
    WAKE.set()


def _forget(record: dict[str, Any]) -> None:
    from . import custom_domains

    custom_domains.remove(record["domain"], managed=True)


def _create(name: str, data: dict[str, Any], wanted_label: str, stage: str, now: float) -> None:
    from . import custom_domains

    hostname = str(data["hostname"]).lower()
    storage().put(custom_domains.COLLECTION, hostname, {
        "domain": hostname, "instance": name, "primary": False, "tls": "acme", "managed": True,
        "label": wanted_label, "stage": stage, "status": "waiting_dns", "reason": None, "added_at": now,
        "added_by": "Sudoo.sh", "checked_at": None, "addresses": [], "attempts": 0, "next_attempt_at": now,
    })  # fmt: skip
    custom_domains.forget_cache()
    audit.record("system", None, "managed name added", name, detail=hostname)
    custom_domains.changed(name)
    custom_domains.WAKE.set()  # its certificate: the domain worker


def sync(now: float | None = None) -> dict[str, Any]:
    """Compare this server's instances with their names (see the module's description); the state afterwards.
    Never raises for the hub: its error is kept in the state."""
    from sudoo.hub import HubRefused, HubUnavailable

    from . import store

    if not settings.marketplace:
        return state()
    now = time.time() if now is None else now
    with _RUNNING:
        try:
            answer = _hub("GET", PATH)
        except (HubRefused, HubUnavailable) as error:
            text = str(getattr(error, "detail", error))
            code = getattr(error, "code", None)
            return _save(checked_at=now, error=text[:300], **({"enabled": False} if code == "link_required" else {}))
        data = answer.data if isinstance(answer.data, dict) else {}
        found = _save(
            enabled=bool(data.get("enabled")), base_domain=data.get("base_domain") or None,
            company=data.get("company") or None, limit=data.get("limit"), used=data.get("used"),
            checked_at=now, error=None,
        )  # fmt: skip
        problems: list[str] = []
        pending = list(found.get("pending_release") or [])
        held = records()
        names = set(store.names())
        for hostname, record in held.items():  # names whose instance is gone
            if record.get("instance") not in names:
                pending.append(hostname)
                _forget(record)
        if found.get("enabled") and settings.managed_subdomains:
            for name in sorted(names):
                goal = wanted(name)
                current = of_instance(name)
                if current is not None and goal == (current.get("label"), current.get("stage")):
                    continue
                if goal is None:
                    continue
                taken = next((r for r in records().values() if (r.get("label"), r.get("stage")) == goal), None)
                if taken is not None:
                    continue  # another instance of this server has that branch and stage: the first keeps it
                try:
                    given = _ask(*goal)
                except HubRefused as error:
                    problems.append(f"{name}: {error.detail}")
                    if error.code in ("subdomains_unavailable", "address_unknown", "subdomain_limit"):
                        break  # the same for every instance: not again until the next check
                    continue
                except HubUnavailable as error:
                    problems.append(f"{name}: {error}")
                    break
                if str(given["hostname"]).lower() in records():
                    continue  # the hub gave a name this server holds already (the same name): nothing to add
                if given.get("company") and not state().get("company"):
                    _save(company=str(given["company"]))  # the account's first name fixed its company label
                _create(name, given, goal[0], goal[1], now)
                if current is not None:  # the old name (another branch or stage): released once the new one is there
                    pending.append(current["domain"])
                    _forget(current)
        still = [h for h in dict.fromkeys(pending) if not _release(h)]
        found = _save(pending_release=still, problems=problems[:20], **({"error": problems[0][:300]} if problems
                                                                         else {}))  # fmt: skip
    events.publish(TOPIC, "state")
    return found


def overview() -> dict[str, Any]:
    """What Settings shows: the setting, the hub's switch and base domain, the company label, use and limit."""
    from sudoo.hub import marketplace

    found = state()
    return {
        "setting": bool(settings.managed_subdomains),
        "linked": bool(settings.marketplace),
        "enabled": bool(found.get("enabled")),
        "active": on(),
        "base_domain": found.get("base_domain"),
        "company": found.get("company"),
        "limit": found.get("limit"),
        "used": found.get("used"),
        "held": len(records()),
        "checked_at": found.get("checked_at"),
        "error": found.get("error"),
        "pending_release": list(found.get("pending_release") or []),
        "addresses": marketplace.public_addresses() if settings.marketplace else {},
        "custom_domains": str(settings.custom_domains),
    }


def run_forever() -> None:
    """Sync every MANAGED_SUBDOMAINS_CHECK_MINUTES, and at once when woken (an instance came or went)."""
    WAKE.set()  # once at start
    while True:
        WAKE.wait(max(60.0, float(settings.managed_subdomains_check_minutes) * 60))
        WAKE.clear()
        try:
            sync()
        except Exception as error:  # noqa: BLE001 - the loop must survive anything
            print(f"managed names: {error!r}", flush=True)


def wake() -> None:
    WAKE.set()
