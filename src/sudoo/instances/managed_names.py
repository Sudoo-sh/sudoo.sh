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

Several servers (premium package "servers", premium/servers.py)
    Only the main panel talks to the hub, for the instances of every server. For an instance of another server it
    sends that server's public addresses with the request (`ipv4`, `ipv6`, as its agent reported them); the hub
    points the name there and leaves it there when the panel's own addresses change. The record says which server
    serves the name ("server", absent: this one) and the addresses sent ("points_to"). The panel tells each agent
    the names it serves (`for_server`, pushed with the panel's decisions, and at once through `servers.serve_names`);
    the agent keeps them as its own records (`adopt`): its gateway answers them on ports 80 and 443 and its domain
    worker gets and renews their certificates with ACME, as here. Agents never call the hub.

    an instance that moved           the name is pointed at its new server (the same name: the hub answers 200), its
                                     certificate goes with it (`follow`, called by the move before the old copy is
                                     deleted; else at the next sync, then the new server gets its own), the old
                                     server forgets it
    a server whose addresses changed its names are asked for again with the new addresses
    an instance being moved          neither released nor pointed elsewhere until its move ended
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
MAIN = "main"  # premium/servers.MAIN: the server the panel runs on
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


class NoAddress(Exception):
    """A server whose agent reported no public address: its instances get no name until it does."""


def on() -> bool:
    """Whether managed names are asked for and served: MANAGED_SUBDOMAINS, a linked panel, and the hub offers them
    (in a server's agent: the panel said so with the names it pushed, `adopt`)."""
    from sudoo.premium import servers

    linked = settings.marketplace or servers.is_agent()
    return bool(settings.managed_subdomains and linked and state().get("enabled"))


def server_of(record: dict[str, Any]) -> str:
    """The server that serves a managed name: this one unless the record names another (several servers)."""
    return str(record.get("server") or MAIN)


def moving(name: str) -> bool:
    """Whether the instance is being moved to another server now (its name waits for the move to end)."""
    from sudoo.core import jobs

    return any(
        j.kind == "move" and j.instance == name and j.status in ("queued", "running") for j in list(jobs.JOBS.values())
    )


def kept_on_delete(record: dict[str, Any]) -> bool:
    """A managed name that stays when this server deletes its instance: the instance lives on another server now
    (the name points there, or its move is running). Never in an agent: its records are copies of the panel's."""
    from sudoo.premium import servers

    if servers.is_agent():
        return False
    return server_of(record) != MAIN or moving(str(record.get("instance")))


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
    from sudoo.premium import servers

    from . import stages

    if not servers.exists(name):
        return None
    env = servers.env(name)
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


def points_to(server_id: str) -> dict[str, str | None] | None:
    """The addresses a name of another server is asked for with (that server's, as its agent reported them); None for
    this server (the hub uses the addresses of the panel's heartbeat). Raises NoAddress when there is none."""
    from sudoo.premium import servers

    if server_id == MAIN:
        return None
    found = servers.addresses(server_id)
    wanted = {"ipv4": found.get("public_ipv4") or None, "ipv6": found.get("public_ipv6") or None}
    if not wanted["ipv4"] and not wanted["ipv6"]:
        raise NoAddress(server_id)
    return wanted


def _ask(record_label: str, stage: str, server_id: str = MAIN) -> dict[str, Any]:
    body: dict[str, Any] = {"branch": record_label, "stage": stage}
    target = points_to(server_id)
    if target is not None:  # another server's instance: the hub points the name at that server
        body.update({key: value for key, value in target.items() if value})
    if settings.managed_subdomain_company and not state().get("company"):
        body["company"] = settings.managed_subdomain_company
    answer = _hub("POST", PATH, body)
    data = answer.data if isinstance(answer.data, dict) else {}
    hostname = str(data.get("hostname") or "").lower()
    if not re.fullmatch(r"[a-z0-9.-]{3,253}", hostname) or ".." in hostname:
        from sudoo.hub import HubUnavailable

        raise HubUnavailable("Sudoo.sh answered without a valid name.")
    base = str(state().get("base_domain") or "").lower().strip(".")
    if not base or not hostname.endswith("." + base):
        from sudoo.hub import HubUnavailable

        raise HubUnavailable("Sudoo.sh answered with a name outside its own domain.")
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
    """Names whose instance is gone: released at the next sync (soon). Not in an agent: the panel releases them."""
    from sudoo.premium import servers

    if not hostnames or servers.is_agent():
        return
    pending = sorted(set(state().get("pending_release") or []) | set(hostnames))
    _save(pending_release=pending)
    WAKE.set()


def _forget(record: dict[str, Any]) -> None:
    from . import custom_domains

    if server_of(record) == MAIN:
        custom_domains.remove(record["domain"], managed=True)
        return
    with custom_domains._LOCK:  # another server's name: no certificate and no gateway here
        storage().delete(custom_domains.COLLECTION, record["domain"])
        custom_domains.forget_cache()


def _new_record(hostname: str, name: str, wanted_label: str, stage: str, now: float) -> dict[str, Any]:
    return {
        "domain": hostname, "instance": name, "primary": False, "tls": "acme", "managed": True,
        "label": wanted_label, "stage": stage, "status": "waiting_dns", "reason": None, "added_at": now,
        "added_by": "Sudoo.sh", "checked_at": None, "addresses": [], "attempts": 0, "next_attempt_at": now,
    }  # fmt: skip


def _create(
    name: str, data: dict[str, Any], wanted_label: str, stage: str, now: float, server_id: str = MAIN,
    target: dict[str, str | None] | None = None,
) -> None:  # fmt: skip
    from . import custom_domains

    hostname = str(data["hostname"]).lower()
    record = _new_record(hostname, name, wanted_label, stage, now)
    if server_id != MAIN:
        record.update(server=server_id, points_to=target)

    def add(existing: dict[str, Any] | None) -> dict[str, Any]:
        if existing is not None:  # a domain this server has already (a custom domain of an instance): never replaced
            raise custom_domains.Refused(f"{hostname} is already a domain of this server.")
        return record

    storage().change(custom_domains.COLLECTION, hostname, add)
    custom_domains.forget_cache()
    audit.record("system", None, "managed name added", name, detail=hostname)
    if server_id != MAIN:
        return  # served by that server (the caller tells it)
    custom_domains.changed(name)
    custom_domains.WAKE.set()  # its certificate: the domain worker


# ---------------------------------------------------------------- certificates that go with a name


def certificate_pem(domain: str) -> str | None:
    """The certificate this server serves a name with (its key and chain, PEM), or None."""
    from . import custom_domains

    return custom_domains.certificate_pem(domain)


def install_certificate(domain: str, pem: str, now: float | None = None) -> bool:
    """Serve a managed name of this server with a certificate it had on another server (a move): kept where the
    gateway reads it, the record active until its renewal. False (nothing changed) for anything but a managed name
    and a key with a certificate for that name that is still valid; the domain worker then gets a new one."""
    from . import custom_domains

    record = custom_domains.get(domain)
    if record is None or not record.get("managed"):
        return False
    return custom_domains.install_certificate(domain, pem, now)


# ---------------------------------------------------------------- names of other servers


def for_server(server_id: str) -> list[dict[str, str]]:
    """The names another server serves, as the panel tells its agent: [{domain, instance, label, stage}]."""
    return [
        {"domain": domain, "instance": str(r.get("instance")), "label": str(r.get("label") or ""),
         "stage": str(r.get("stage") or "")}
        for domain, r in sorted(records().items())
        if server_of(r) == server_id
    ]  # fmt: skip


def _tell(server_ids: set[str], certificates: dict[str, dict[str, str]] | None = None) -> None:
    """Tell other servers' agents their names now (best effort: they also come with the panel's next push)."""
    from sudoo.premium import servers

    for server_id in sorted(server_ids - {MAIN}):
        try:
            servers.serve_names(server_id, (certificates or {}).get(server_id))
        except Exception as error:  # noqa: BLE001 - offline: the push brings them when it is back
            print(f"managed names: {server_id} not told now: {error!r}", flush=True)


def adopt(items: Any, certificates: Any = None, enabled: bool = True, now: float | None = None) -> None:
    """In a server's agent: the managed names the panel says this server serves become this server's records (served
    by its gateway, certificates from its domain worker, or the one that came with a moved name); the others go."""
    from . import custom_domains, store

    now = time.time() if now is None else now
    wanted: dict[str, dict[str, str]] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        domain, instance = str(item.get("domain") or "").lower(), str(item.get("instance") or "")
        if re.fullmatch(r"[a-z0-9.-]{3,253}", domain) and ".." not in domain and store.NAME_RE.match(instance):
            wanted[domain] = {"instance": instance, "label": str(item.get("label") or "")[:63],
                              "stage": str(item.get("stage") or "")[:20]}  # fmt: skip
    pems = certificates if isinstance(certificates, dict) else {}
    if bool(state().get("enabled")) != bool(enabled):
        _save(enabled=bool(enabled))
    touched: set[str] = set()
    for domain, record in records().items():
        if wanted.get(domain, {}).get("instance") != record.get("instance"):
            custom_domains.remove(domain, managed=True)
    for domain, item in wanted.items():
        if domain not in records():
            record = _new_record(domain, item["instance"], item["label"], item["stage"], now)
            storage().put(custom_domains.COLLECTION, domain, {**record, "added_by": "Sudoo.sh (main panel)"})
            custom_domains.forget_cache()
            audit.record("system", None, "managed name served here", item["instance"], detail=domain)
            touched.add(item["instance"])
        if isinstance(pems.get(domain), str) and not custom_domains.certificate_present(domain):
            install_certificate(domain, pems[domain], now)
            touched.add(item["instance"])
    for name in touched:
        custom_domains.changed(name)
    if touched:
        custom_domains.WAKE.set()


def _repoint(record: dict[str, Any], server_id: str, now: float) -> set[str]:
    """Point an instance's name at another server (a move); the servers to tell. Raises for the hub, NoAddress."""
    from sudoo.premium import servers

    from . import custom_domains

    old, domain = server_of(record), record["domain"]
    target = points_to(server_id)
    given = _ask(str(record.get("label")), str(record.get("stage")), server_id)
    if str(given["hostname"]).lower() != domain:  # not expected (the same labels): keep both right anyway
        _create(str(record["instance"]), given, str(record.get("label")), str(record.get("stage")), now, server_id,
                target)  # fmt: skip
        _forget(record)
        release_later([domain])
        return {old, server_id}
    pem = certificate_pem(domain) if old == MAIN else servers.certificate(old, domain)
    reset = {"status": "waiting_dns", "reason": None, "certificate": None, "renew_at": None, "attempts": 0,
             "next_attempt_at": now}  # fmt: skip
    custom_domains.set_fields(domain, server=server_id if server_id != MAIN else None, points_to=target, **reset)
    if old == MAIN and server_id != MAIN:
        custom_domains.cert_file(domain).unlink(missing_ok=True)
        custom_domains.refresh_gateway()
    if old != server_id:
        audit.record("system", None, "managed name moved", str(record["instance"]),
                     detail=f"{domain}: {old} -> {server_id}")  # fmt: skip
    if server_id == MAIN:
        if pem:
            install_certificate(domain, pem, now)
        custom_domains.changed(str(record["instance"]))
        custom_domains.WAKE.set()
    elif pem and old != server_id:
        _CARRIED.setdefault(server_id, {})[domain] = pem
    return {old, server_id}


_CARRIED: dict[str, dict[str, str]] = {}  # certificates that go with names to their new server, in one sync


def follow(name: str, server_id: str, now: float | None = None) -> str | None:
    """Point the instance's name at `server_id` now, with its certificate (the move job, once the instance answers
    there and before its old copy is deleted); None when done or nothing to do, else why not (the next sync tries
    again)."""
    from sudoo.hub import HubRefused, HubUnavailable

    now = time.time() if now is None else now
    if not settings.marketplace:
        return None
    with _RUNNING:
        record = of_instance(name)
        if record is None or server_of(record) == server_id:
            return None
        _CARRIED.clear()
        try:
            told = _repoint(record, server_id, now)
        except NoAddress:
            return "the server has not reported a public address yet"
        except HubRefused as error:
            return str(error.detail)
        except HubUnavailable as error:
            return str(error)
        carried = dict(_CARRIED)
        _CARRIED.clear()
    _tell(told, carried)
    events.publish(TOPIC, "state")
    return None


def sync(now: float | None = None) -> dict[str, Any]:
    """Compare the instances of every server with their names (see the module's description); the state afterwards.
    Never raises for the hub: its error is kept in the state. Nothing in a server's agent (the panel does it)."""
    from sudoo.hub import HubRefused, HubUnavailable
    from sudoo.premium import servers

    from . import custom_domains

    if not settings.marketplace or servers.is_agent():
        return state()
    now = time.time() if now is None else now
    told: set[str] = set()
    with _RUNNING:
        _CARRIED.clear()
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
        names = set(servers.names())
        for hostname, record in held.items():  # names whose instance is gone
            if record.get("instance") not in names and not moving(str(record.get("instance"))):
                pending.append(hostname)
                told.add(server_of(record))
                _forget(record)
        if found.get("enabled") and settings.managed_subdomains:
            for name in sorted(names):
                goal = wanted(name)
                if goal is None or moving(name):
                    continue
                current = of_instance(name)
                server_id = servers.server_of(name)
                try:
                    if current is not None and goal == (current.get("label"), current.get("stage")):
                        if server_of(current) != server_id or (
                            server_id != MAIN and current.get("points_to") != points_to(server_id)
                        ):  # it moved, or its server's addresses changed
                            told |= _repoint(current, server_id, now)
                        continue
                    taken = next((r for r in records().values() if (r.get("label"), r.get("stage")) == goal), None)
                    if taken is not None:
                        continue  # another instance has that branch and stage: the first keeps it
                    target = points_to(server_id)
                    given = _ask(*goal, server_id)
                except NoAddress:
                    problems.append(f"{name}: its server has not reported a public address yet")
                    continue
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
                try:
                    _create(name, given, goal[0], goal[1], now, server_id, target)
                except custom_domains.Refused as error:
                    problems.append(f"{name}: {error}")
                    continue
                told.add(server_id)
                if current is not None:  # the old name (another branch or stage): released once the new one is there
                    pending.append(current["domain"])
                    told.add(server_of(current))
                    _forget(current)
        still = [h for h in dict.fromkeys(pending) if not _release(h)]
        found = _save(pending_release=still, problems=problems[:20], **({"error": problems[0][:300]} if problems
                                                                         else {}))  # fmt: skip
        carried = dict(_CARRIED)
        _CARRIED.clear()
    _tell(told, carried)
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
        "other_servers": sum(1 for r in records().values() if server_of(r) != MAIN),
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
