"""Production access rules (BETA), the panel's side: who may open a production instance's Odoo.

The gateway applies them (access_gate.py has the checks, the order and the pass format); this module keeps them,
checks changes, previews them, hands them to the gateway, gives passes and records everything.

Shown and enforced only while ACCESS_RULES_BETA is on, and only for instances in the production stage: an instance
that leaves production, or a panel that turns the beta off, keeps its rules but they no longer apply.

A rule set (stored per instance, collection "access_rules")
    enabled      whether the rules apply
    mode         "countries" (visitors from the chosen countries), "ips" (from the chosen addresses and ranges) or
                 "either" (from a chosen country or a chosen address)
    scope        "back_office" (the default: sign-in, the back office, the APIs, the database manager; the public
                 website and shop stay open to everyone) or "site" (everything)
    countries    two-letter codes (ISO 3166-1 alpha-2)
    networks     [{"value": "203.0.113.0/24", "label": "Office"}]: IPv4 or IPv6 addresses and ranges
    exceptions   {"presets": ["payments", "webhooks", ...] (access_gate.PRESETS), "paths": ["/custom/hook/"],
                 "networks": [{"value", "label"}]}: always let through
    version      raised by every change; a pass carries it, so new rules end the passes given before

Who may change them (may_change): super admins and the project's administrators (projects.access in the instance),
and the project's clients (client portal). Never an API token: a change needs a person who types
the instance's name and a one-time code (access_confirm.py). Every change, refusal, code and pass is audit-logged;
the people on the other side are told by e-mail (notify()).

Passes (pass_url): opening a production instance from the panel (Open Odoo, Connect as) goes through a signed pass
when its rules are active, so the person gets in from anywhere. Given only when the person may open production
(may_open_production: instances.connect in the instance's stage) and never while the client has closed access
(client_closed); while it is closed, the gateway also refuses every pass given before ("no_passes").
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import threading
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit

from sudoo.config import settings
from sudoo.core import audit
from sudoo.storage import Document, storage

from . import access_gate, geoip, stages, store

COLLECTION = "access_rules"
MAX_LABEL = 60

DEFAULT_RULES: dict[str, Any] = {
    "enabled": False,
    "mode": "countries",
    "scope": "back_office",
    "countries": [],
    "networks": [],
    "exceptions": {"presets": ["payments", "webhooks"], "paths": [], "networks": []},
}

SAMPLE_PATHS = (
    "/odoo",
    "/web/login",
    "/web/database/manager",
    "/json/2/res.partner/search_read",
    "/xmlrpc/2/object",
    "/websocket",
    "/",
    "/shop",
    "/my",
    "/payment/stripe/webhook",
    "/web/hook/2f6c1d0e",
    "/web/image/product.template/1/image_512",
)
"""Paths the preview tries, so people see what a rule set blocks and what stays open."""

_LOCK = threading.Lock()


class RuleError(ValueError):
    """A rule set that is not valid; the message is meant for people."""


# --------------------------------------------------------------------------- reading


def visible() -> bool:
    """Whether the feature is shown at all (ACCESS_RULES_BETA)."""
    return bool(settings.access_rules_beta)


def get(name: str) -> dict[str, Any]:
    """The instance's rules (the defaults when it has none yet)."""
    found = storage().get(COLLECTION, name) or {}
    rules = {**DEFAULT_RULES, **{k: v for k, v in found.items() if k in DEFAULT_RULES}}
    rules["exceptions"] = {**DEFAULT_RULES["exceptions"], **(found.get("exceptions") or {})}
    rules["version"] = int(found.get("version") or 0)
    rules["changed_by"] = found.get("changed_by")
    rules["changed_at"] = found.get("changed_at")
    return rules


def is_production(name: str) -> bool:
    try:
        return store.exists(name) and stages.is_production(store.read_env(name))
    except (OSError, KeyError):
        return False


def active(name: str, rules: Mapping[str, Any] | None = None) -> bool:
    """Whether the instance's rules apply now: beta on, rules on, instance in production."""
    rules = rules if rules is not None else get(name)
    return visible() and bool(rules.get("enabled")) and is_production(name)


# --------------------------------------------------------------------------- checking a rule set


def _networks(raw: Any, what: str) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > access_gate.MAX_NETWORKS:
        raise RuleError(f"Give at most {access_gate.MAX_NETWORKS} {what}.")
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw:
        value, label = (entry.get("value"), entry.get("label", "")) if isinstance(entry, dict) else (entry, "")
        if not isinstance(value, str) or not isinstance(label, str):
            raise RuleError(f"Each of the {what} needs an address or a range.")
        try:
            network = access_gate.parse_network(value)
        except ValueError:
            raise RuleError(
                f'"{value.strip()[:64]}" is not an address or a range. Use an address such as 203.0.113.7 or '
                "2001:db8::7, or a range such as 203.0.113.0/24 (the address part must be the start of the range)."
            ) from None
        if network.prefixlen == 0:
            raise RuleError(f'"{value.strip()}" is every address: it would allow everyone. Remove it.')
        text = str(network)
        if text in seen:
            continue
        seen.add(text)
        clean_label = " ".join(label.split())[:MAX_LABEL]
        if any(ord(c) < 32 for c in clean_label):
            raise RuleError("A label cannot contain control characters.")
        found.append({"value": text, "label": clean_label})
    return found


def _paths(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > access_gate.MAX_PATHS:
        raise RuleError(f"Give at most {access_gate.MAX_PATHS} exception paths.")
    found: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            raise RuleError("Each exception path must be text.")
        text = value.strip()
        if not text.startswith("/") or len(text) > 200 or any(ord(c) < 33 for c in text) or "?" in text:
            raise RuleError(
                f'"{text[:60]}" is not a path. A path starts with "/", for example /payment/ or /web/hook/.'
            )
        normalized = access_gate.normalize(text)
        if normalized == "/":
            raise RuleError('The path "/" is the whole site: it would let everyone in. Remove it.')
        if normalized not in found:
            found.append(normalized)
    return found


def clean(raw: Any) -> dict[str, Any]:
    """A rule set as stored, from what a person sent; raises RuleError saying what is wrong."""
    if not isinstance(raw, dict):
        raise RuleError("Send the rules as an object.")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise RuleError('"enabled" must be true or false.')
    mode, scope = raw.get("mode", "countries"), raw.get("scope", "back_office")
    if mode not in access_gate.MODES:
        raise RuleError("Choose who may open it: countries, addresses, or either.")
    if scope not in access_gate.SCOPES:
        raise RuleError("Choose what the rules protect: the back office, or the whole site.")
    countries_raw = raw.get("countries") or []
    if not isinstance(countries_raw, list) or len(countries_raw) > access_gate.MAX_COUNTRIES:
        raise RuleError("Give the countries as a list of two-letter codes.")
    countries: list[str] = []
    for code in countries_raw:
        if not isinstance(code, str) or len(code) != 2 or not code.isascii() or not code.isalpha():
            raise RuleError(f'"{str(code)[:10]}" is not a two-letter country code.')
        if code.upper() not in countries:
            countries.append(code.upper())
    networks = _networks(raw.get("networks"), "addresses and ranges")
    exceptions = raw.get("exceptions") or {}
    if not isinstance(exceptions, dict):
        raise RuleError("Send the exceptions as an object.")
    presets = exceptions.get("presets") or []
    if not isinstance(presets, list) or any(p not in access_gate.PRESETS for p in presets):
        raise RuleError(f"Unknown exception. Choose from: {', '.join(access_gate.PRESETS)}.")
    rules = {
        "enabled": enabled,
        "mode": mode,
        "scope": scope,
        "countries": sorted(countries),
        "networks": networks,
        "exceptions": {
            "presets": [p for p in access_gate.PRESETS if p in presets],
            "paths": _paths(exceptions.get("paths")),
            "networks": _networks(exceptions.get("networks"), "exception addresses"),
        },
    }
    if enabled:
        if mode in ("countries", "either") and not countries and not (mode == "either" and networks):
            raise RuleError("Choose at least one country, or switch the rules off.")
        if mode == "ips" and not networks:
            raise RuleError("Add at least one address or range, or switch the rules off.")
    return rules


def gateway_policy(rules: Mapping[str, Any]) -> dict[str, Any]:
    """The rule set as the gateway takes it (access_gate.parse_policy): presets turned into their paths."""
    exceptions = rules.get("exceptions") or {}
    paths = [path for preset in exceptions.get("presets") or () for path in access_gate.PRESETS.get(preset, ())]
    paths += [p for p in exceptions.get("paths") or () if p not in paths]
    return {
        "mode": rules["mode"],
        "scope": rules["scope"],
        "countries": list(rules.get("countries") or ()),
        "networks": [n["value"] for n in rules.get("networks") or ()],
        "exception_paths": paths,
        "exception_networks": [n["value"] for n in exceptions.get("networks") or ()],
        "version": int(rules.get("version") or 0),
    }


# --------------------------------------------------------------------------- the gateway's copy


def pass_key() -> bytes:
    """The key passes are signed with: derived from the vault key, so it survives restarts and never sits in a
    file of its own (the gateway service gets it in routes.json)."""
    from sudoo.core import vault

    return hmac.new(vault.key(), b"sudoo access passes v1", hashlib.sha256).digest()


def routes_section() -> dict[str, Any]:
    """The "access" part of routes.json: the active rules of every production instance, the pass key and the
    settings the gateway needs for them."""
    instances: dict[str, Any] = {}
    if visible():
        for name, found in storage().all(COLLECTION).items():
            if found.get("enabled") and store.NAME_RE.match(name) and is_production(name):
                instances[name] = gateway_policy(get(name))
                if client_closed(name):  # the client closed access: no pass works, earlier ones neither
                    instances[name]["no_passes"] = True
    return {
        "format": access_gate.FORMAT,
        "pass_key": pass_key().hex(),
        "geo_missing": settings.access_rules_geo_missing,
        "show_ip": bool(settings.access_rules_show_ip),
        "contact": settings.access_rules_contact,
        "pass_hours": float(settings.access_rules_pass_hours),
        "instances": instances,
    }


def load_locally() -> dict[str, Any]:
    """Give this process's gateway (the panel's, when it serves the instances itself) the current rules; returns the
    section for routes.json. Pass uses here are recorded at once."""
    section = routes_section()
    access_gate.SINK = record_pass_use
    access_gate.load(section)
    return section


def apply() -> None:
    """The rules changed (or the stage, or the beta setting): hand them to the gateway now."""
    load_locally()
    if settings.gateway_mode == "service":
        from sudoo.instances import gateway_link

        if gateway_link.LINK.written is not None:
            gateway_link.sync()


def record_pass_use(event: Mapping[str, Any]) -> None:
    """A pass was used (or refused) at the gateway: the audit log has it."""
    instance = str(event.get("instance") or "")
    ok = bool(event.get("ok"))
    who = str(event.get("user") or "visitor") if ok else "visitor"
    detail = "pass accepted" if ok else f"pass refused: {str(event.get('why') or '')[:60]}"
    audit.record(
        who, str(event.get("ip") or "")[:64] or None, f"access rules: {detail}", instance=instance or None, ok=ok
    )


# --------------------------------------------------------------------------- who may do what (hooks)


def _project_of(name: str) -> tuple[Mapping[str, Any] | None, str | None]:
    from sudoo.projects import access

    return access.scope_of_instance(name)


def client_of(user: Mapping[str, Any], name: str) -> bool:
    """Whether `user` is a client of the instance's project (client portal, projects/clients.py)."""
    from sudoo.projects import access, clients

    if not clients.is_client(user):
        return False
    project, _ = _project_of(name)
    return bool(project and clients.entry(project, access.person(user)))


def clients_of(name: str) -> list[str]:
    """The client accounts of the instance's project, for the e-mails."""
    from sudoo.projects import clients

    project, _ = _project_of(name)
    return [str(e["id"]) for e in clients.entries(project)] if project else []


def client_closed(name: str) -> bool:
    """Whether the client has closed access to the instance's branch now: its switch (closed, or a timed opening
    that ended) or a client lock, for the project or the branch. Strict: nobody gets a pass then, whoever the
    closure would otherwise let through."""
    from sudoo.projects import clients

    project, branch = _project_of(name)
    if project is None:
        return False
    try:
        return clients.closed(project, branch) or clients.client_lock(project, branch) is not None
    except Exception:  # noqa: BLE001 - a broken project document closes rather than opens
        return True


def may_change(user: Mapping[str, Any], name: str) -> bool:
    """Super admins, the project's administrators (projects.access in this instance: the Admin project role) and
    the project's clients. Never an API token."""
    from sudoo.projects import access

    if str(user.get("name", "")).startswith("api:") or user.get("token_id") or user.get("support"):
        return False
    if access.is_super_admin(user):
        return True
    if client_of(user, name):
        return True
    return access.allowed_instance(user, "projects.access", name)


OPEN_PERMISSION = "instances.connect"
"""What the person's project role must give in the instance's stage to get a pass: opening Odoo signed in
(project roles: Maintainer and Admin in production by default; the project's matrix can change it)."""


def may_open_production(user: Mapping[str, Any], name: str) -> tuple[bool, str]:
    """(True, "") when the person may open this production instance from the panel through a pass, else (False,
    why): their project role allows opening it (OPEN_PERMISSION), and the client has not closed access to it."""
    from sudoo.projects import access

    if client_closed(name):
        return False, "The client has closed access to this production instance."
    if not access.allowed_instance(user, OPEN_PERMISSION, name):
        return False, "Your role in this project does not let you open its production instance."
    return True, ""


def pass_url(user: Mapping[str, Any], name: str, address: str, target: str = "/odoo") -> tuple[str, bool]:
    """The address that opens `target` on the instance for this person: through a signed pass when the instance's
    rules apply and the person may open it, else `address` + `target` unchanged. (url, whether a pass was given).
    Audit-logged when a pass is given or refused."""
    from flask import has_request_context

    rules = get(name)
    plain = address.rstrip("/") + (target if target != "/odoo" else "")
    if not active(name, rules):
        return plain or address, False
    allowed, why = may_open_production(user, name)
    ip = _request_ip() if has_request_context() else None
    if not allowed:
        audit.record(str(user.get("name")), ip, "access rules: pass refused", instance=name, ok=False, detail=why)
        return plain or address, False
    host = urlsplit(address).netloc
    token = access_gate.issue(pass_key(), name, host, int(rules["version"]), str(user.get("name")))
    audit.record(str(user.get("name")), ip, "access rules: pass given", instance=name, detail=f"to {target[:120]}")
    url = f"{address.rstrip('/')}{access_gate.PASS_PATH}?token={quote(token)}&next={quote(target, safe='/')}"
    return url, True


def _request_ip() -> str | None:
    from sudoo.web.security import client_ip

    try:
        return client_ip()
    except RuntimeError:
        return None


# --------------------------------------------------------------------------- preview ("test before applying")


def preview(rules: Mapping[str, Any], address: str | None, test_address: str | None = None) -> dict[str, Any]:
    """What a rule set does, before it is saved: whether `address` (the person's own) and `test_address` would get
    into the back office, which sample paths are closed to everyone else, and warnings."""
    policy = access_gate.parse_policy(gateway_policy({**rules, "version": 0}))
    geo_ready = geoip.ready()

    def outcome(text: str | None) -> dict[str, Any] | None:
        if not text:
            return None
        parsed = access_gate.parse_ip(text)
        country = geoip.lookup(parsed)
        decision = access_gate.decide(
            policy, parsed, "/odoo", country=country, geo_ready=geo_ready,
            geo_missing=settings.access_rules_geo_missing,
        )  # fmt: skip
        return {"address": text, "country": country, "allowed": decision.allowed, "why": decision.why}

    nowhere = ipaddress.ip_address("192.0.2.1")  # TEST-NET-1: in no country and, unless listed, in no rule
    samples = []
    for path in SAMPLE_PATHS:
        decision = access_gate.decide(
            policy, nowhere, path, country=None, geo_ready=True, geo_missing="closed"
        )  # fmt: skip
        samples.append({"path": path, "open_to_everyone": decision.allowed, "why": decision.why})
    you = outcome(address)
    warnings: list[str] = []
    if rules.get("enabled") and policy.mode != "ips" and not geo_ready:
        warnings.append("geo_missing_open" if settings.access_rules_geo_missing == "open" else "geo_missing_closed")
    if rules.get("enabled") and you is not None and not you["allowed"]:
        warnings.append("you_are_outside")
    if rules.get("enabled") and policy.mode != "ips":
        warnings.append("vpn")
    if not rules.get("enabled"):
        warnings.append("disabled")
    return {
        "you": you,
        "test": outcome(test_address),
        "samples": samples,
        "warnings": warnings,
        "geo": {"ready": geo_ready, "missing": settings.access_rules_geo_missing},
    }


# --------------------------------------------------------------------------- saving


def summary(rules: Mapping[str, Any]) -> list[str]:
    """The rule set in a few plain lines (English), for the audit log and the e-mails."""
    if not rules.get("enabled"):
        return ["Access rules: off (everyone can open it)."]
    who = {
        "countries": f"from {', '.join(rules['countries'])}",
        "ips": f"from {len(rules['networks'])} address(es) or range(s)",
        "either": f"from {', '.join(rules['countries']) or 'no country'} or {len(rules['networks'])} address(es)",
    }[rules["mode"]]
    scope = "the back office" if rules["scope"] == "back_office" else "the whole site"
    exceptions = rules.get("exceptions") or {}
    lines = [f"Only visitors {who} may open {scope}."]
    kept = [*exceptions.get("presets", []), *exceptions.get("paths", [])]
    if kept:
        lines.append("Always open: " + ", ".join(kept) + ".")
    if exceptions.get("networks"):
        lines.append(f"Always allowed: {len(exceptions['networks'])} address(es).")
    return lines


def save(name: str, rules: Mapping[str, Any], by: str, ip: str | None) -> dict[str, Any]:
    """Store a checked rule set (clean()) as a new version, record it, tell the gateway and the people concerned."""
    with _LOCK:
        previous = get(name)
        document: Document = {
            **{k: rules[k] for k in DEFAULT_RULES},
            "version": int(previous.get("version") or 0) + 1,
            "changed_by": by,
            "changed_at": time.time(),
        }
        storage().put(COLLECTION, name, document)
    lines = summary(document)
    audit.record(by, ip, "access rules changed", instance=name, detail=" ".join(lines))
    apply()
    threading.Thread(target=notify, args=(name, by, lines), daemon=True, name="access-rules-mail").start()
    return get(name)


def forget(name: str) -> None:
    """The instance was deleted: its rules go too."""
    if storage().delete(COLLECTION, name):
        apply()


def recipients(name: str, by: str) -> list[str]:
    """Who is told about a change: the other people who may change the rules (super admins, the project's
    administrators) and the project's clients, except the person who made it."""
    from sudoo import accounts, roles

    found: list[str] = []
    for username, account in accounts.all_users().items():
        if username == by or not account.get("email"):
            continue
        user = {"name": username, "role": roles.normalize(str(account.get("role")))}
        try:
            if may_change(user, name):
                found.append(username)
        except Exception:  # noqa: BLE001 - one broken account never stops the others' e-mails
            continue
    for client in clients_of(name):
        if client != by and client not in found:
            found.append(client)
    return found


def notify(name: str, by: str, lines: list[str]) -> None:
    """E-mail the people on the other side of a change (English or Arabic, as each account prefers)."""
    from sudoo import accounts
    from sudoo.mail import MailError, mailer, send
    from sudoo.mail.render import panel_url

    if not mailer().ready:
        return
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    base = panel_url() or ""
    link = f"{base.rstrip('/')}/i/{name}/settings" if base else ""
    for username in recipients(name, by):
        account = accounts.get(username) or {}
        arabic = accounts.language(account) == "ar"
        try:
            send(
                str(account["email"]),
                "access_rules_changed_ar" if arabic else "access_rules_changed",
                name=accounts.display_name(username, account),
                instance=name,
                by=by,
                time=when,
                rules=" ".join(lines),
                link=link,
            )
        except (MailError, KeyError, ValueError) as error:
            print(f"Access rules e-mail to {username}: {error}", flush=True)
