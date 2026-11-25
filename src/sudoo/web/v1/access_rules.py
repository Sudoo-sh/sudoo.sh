"""Production access rules (BETA): who may open a production instance (instances/access_rules.py).

    GET  /api/v1/instances/<name>/access-rules           the rules, whether they apply, the country database,
                                                         the caller's own address and country, whether the caller
                                                         may change them and the code channels they can use
    POST /api/v1/instances/<name>/access-rules/preview   {"rules", "test_address"?} -> what the rules would do,
                                                         before anything is saved ("test before applying")
    POST /api/v1/instances/<name>/access-rules/code      {"method": "email" | "app"} -> sends the one-time code
    PUT  /api/v1/instances/<name>/access-rules           {"rules", "confirm_name", "method", "code"} -> saves them
    POST /api/v1/instances/<name>/open-link              {"target"?: "/odoo"} -> {"url", "pass"}: the address
                                                         that opens the instance, through a pass when its rules
                                                         apply (Open Odoo in the web app)

Reading needs instances.view. Changing needs a person (never an API token) who may change them
(access_rules.may_change), the instance's name typed, and a one-time code. Everything is audit-logged.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo import accounts, exposure
from sudoo.config import settings
from sudoo.instances import access_confirm, access_gate, access_rules, geoip
from sudoo.mail import MailError

from .. import validation as check
from ..security import client_ip, log, require
from ..signin import request_info
from .instances import existing
from .responses import ok

blueprint = Blueprint("access_rules", __name__)

MAX_WRONG_CODES = 5
WRONG_CODES_SECONDS = 600
_WRONG: dict[tuple[str, str], list[float]] = {}
_WRONG_LOCK = threading.Lock()


def _iso(at: Any) -> str | None:
    return datetime.fromtimestamp(float(at), UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if at else None


def _available() -> None:
    if not access_rules.visible():
        abort(404, "Production access rules are not switched on on this panel (ACCESS_RULES_BETA).")


def _account() -> dict[str, Any]:
    return accounts.get(str(g.user["name"])) or {}


def _rules_resource(name: str) -> dict[str, Any]:
    rules = access_rules.get(name)
    return {**rules, "changed_at": _iso(rules.get("changed_at"))}


def _resource(name: str) -> dict[str, Any]:
    you = client_ip()
    may_change = access_rules.may_change(g.user, name)
    return {
        "available": access_rules.visible(),
        "production": access_rules.is_production(name),
        "active": access_rules.active(name),
        "rules": _rules_resource(name),
        "can_change": may_change,
        "channels": access_confirm.available(str(g.user["name"]), _account()) if may_change else [],
        "geo": geoip.status(),
        "geo_missing": settings.access_rules_geo_missing,
        "you": {"address": you, "country": geoip.lookup(you)},
        "presets": {key: list(paths) for key, paths in access_gate.PRESETS.items()},
        "back_office": list(access_gate.BACK_OFFICE),
        "public_under_web": list(access_gate.PUBLIC_UNDER_WEB),
    }


@blueprint.get("/instances/<name>/access-rules")
@require("instances.view")
def read(name: str) -> Response:
    existing(name)
    if not access_rules.visible():
        return ok({"available": False})
    return ok(_resource(name))


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    try:
        return access_rules.clean(data.get("rules"))
    except access_rules.RuleError as error:
        abort(400, str(error))


@blueprint.post("/instances/<name>/access-rules/preview")
@require("instances.view")
def preview(name: str) -> Response:
    existing(name)
    _available()
    data = check.body()
    rules = _clean(data)
    test = check.text(data, "test_address", max_length=64)
    if test and access_gate.parse_ip(test) is None:
        abort(400, f'"{test}" is not an address. Use an address such as 203.0.113.7 or 2001:db8::7.')
    return ok(access_rules.preview(rules, client_ip(), test or None))


def _refuse_change(name: str) -> None:
    if not access_rules.may_change(g.user, name):
        log("access rules: change refused (not allowed)", instance=name, ok=False)
        abort(
            403,
            "Only the project's administrators, super admins and the project's client can change production access "
            "rules, in the panel (not with an API token).",
        )
    if not access_rules.is_production(name):
        abort(409, "Access rules are for production instances. Move this instance to production first.")


@blueprint.post("/instances/<name>/access-rules/code")
@require("instances.view")
def send_code(name: str) -> Response:
    existing(name)
    _available()
    _refuse_change(name)
    data = check.body()
    method = check.text(data, "method", max_length=20)
    username, account = str(g.user["name"]), _account()
    if method not in access_confirm.available(username, account):
        abort(400, "Choose a way to get the code that your account can use: e-mail, or your authenticator app.")
    try:
        told = access_confirm.CHANNELS[method].send(username, account, name, request_info())
    except ValueError as error:
        abort(429, str(error))
    except MailError as error:
        abort(502, f"The code could not be e-mailed: {error}")
    log("access rules: confirmation code asked", instance=name, detail=method)
    return ok({"method": method, **told})


def _too_many_wrong(key: tuple[str, str], now: float) -> bool:
    with _WRONG_LOCK:
        recent = [at for at in _WRONG.get(key, []) if now - at < WRONG_CODES_SECONDS]
        _WRONG[key] = recent
        return len(recent) >= MAX_WRONG_CODES


def _wrong(key: tuple[str, str], now: float) -> None:
    with _WRONG_LOCK:
        _WRONG.setdefault(key, []).append(now)


@blueprint.put("/instances/<name>/access-rules")
@require("instances.view")
def change(name: str) -> Response:
    existing(name)
    _available()
    _refuse_change(name)
    data = check.body()
    rules = _clean(data)
    if check.text(data, "confirm_name", max_length=100) != name:
        log("access rules: change refused (name not typed)", instance=name, ok=False)
        abort(400, f'Type the instance\'s name, "{name}", to confirm.')
    username, account = str(g.user["name"]), _account()
    method = check.text(data, "method", max_length=20)
    code = check.text(data, "code", max_length=12)
    key = (username, name)
    now = time.time()
    if _too_many_wrong(key, now):
        log("access rules: change refused (too many wrong codes)", instance=name, ok=False)
        abort(429, "Too many wrong codes. Wait ten minutes, then ask for a new code.")
    channel = access_confirm.CHANNELS.get(method)
    if channel is None or method not in access_confirm.available(username, account):
        abort(400, "Choose how you confirm: a code by e-mail, or your authenticator app.")
    if not code or not channel.verify(username, account, name, code):
        _wrong(key, now)
        log("access rules: change refused (wrong code)", instance=name, ok=False, detail=method)
        abort(400, "The code is not right, or it has expired. Ask for a new one.")
    access_rules.save(name, rules, username, client_ip())
    return ok(_resource(name))


@blueprint.post("/instances/<name>/open-link")
@require("instances.view")
def open_link(name: str) -> Response:
    existing(name)
    if access_rules.is_api_token(g.user):
        abort(403, "An API token cannot open an instance through a pass. Use a signed-in session.")
    data = check.body()
    target = check.text(data, "target", max_length=200, default="/odoo") or "/odoo"
    if not target.startswith("/") or target.startswith("//"):
        abort(400, "The target must be a path on the instance, such as /odoo.")
    address = exposure.address(name)
    if not address:
        abort(409, "This instance has no public address yet.")
    url, given = access_rules.pass_url(g.user, name, address, target)
    return ok({"url": url, "pass": given})
