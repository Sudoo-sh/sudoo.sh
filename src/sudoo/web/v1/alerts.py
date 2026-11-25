"""Alerts (alerts/): the list behind the web app's bell, and test alerts.

    GET  /api/v1/alerts          the newest alerts first (permission audit.view); ?limit=1-200 (default 50).
                                 While ODOO_ADMIN_PASSWORD is the well-known "admin", the answer starts with a
                                 standing alert about it (admins only; default_password_alert below)
    POST /api/v1/alerts/test     send a test alert with the saved settings (permission settings.manage):
                                 {"channel": "email"} to every alert recipient, or {"channel": "webhook",
                                 "webhook": 1-3} to one webhook -> 200, 400 (nothing to send to), 502 (not delivered)

New and changed alerts also arrive as the live event "alert" (only for callers that may read the audit log).
A webhook's address and secret never appear in answers or in the audit log: it is named by its number.
"""

from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, Response, abort, g, request

from sudoo import alerts, roles
from sudoo.config import settings
from sudoo.instances import geoip

from .. import validation as check
from ..security import can, log, require
from .responses import ok, page

blueprint = Blueprint("alerts", __name__)

DEFAULT_PASSWORD_TYPE = "default_admin_password"
"""The kind of the standing alert while ODOO_ADMIN_PASSWORD is the well-known \"admin\" (web/__init__.py warns in
the log at startup; the installer sets a random password, so this is for setups made by hand)."""


def default_password_alert(role: str, manage_settings: bool) -> dict[str, Any] | None:
    """The standing alert while ODOO_ADMIN_PASSWORD is still \"admin\": for admins only, with a link to the setting
    for those who may change it. Only that the password is the default is sent, never the password itself. The
    setting is read live, so the alert is gone from the next answer once the password is changed."""
    if role not in roles.ADMINISTRATORS or settings.odoo_admin_password != "admin":
        return None
    now = time.time()
    return {
        "id": "default-admin-password",
        "key": DEFAULT_PASSWORD_TYPE,
        "type": DEFAULT_PASSWORD_TYPE,
        "severity": "problem",
        "title": 'The Odoo admin password is still "admin"',
        "text": "New instances get the well-known Odoo login admin / admin: anyone with an instance's link can "
        "sign in as its administrator. Set another ODOO_ADMIN_PASSWORD in the panel's Settings page or in .env.",
        "hint": None,
        "details": [],
        "instance": None,
        "path": "/settings?tab=instances&focus=odoo_admin_password" if manage_settings else None,
        "facts": {},
        "at": now,
        "last_at": now,
        "count": 1,
        "resolved_at": None,
        "delivery": [],
    }


GEO_TYPE = "country_database"


def country_database_alert(role: str, manage_settings: bool) -> dict[str, Any] | None:
    """The standing alert while the access rules are on and the country database is missing or older than
    ACCESS_RULES_GEO_MAX_AGE_DAYS (45 by default): the country part of a rule then lets everyone in (or nobody, with
    ACCESS_RULES_GEO_MISSING=closed) or decides by old data. For admins only; read live, so it goes with the problem."""
    if role not in roles.ADMINISTRATORS or not settings.access_rules_beta:
        return None
    if (why := geoip.problem()) is None:
        return None
    closed = settings.access_rules_geo_missing == "closed"
    now = time.time()
    return {
        "id": "country-database",
        "key": GEO_TYPE,
        "type": GEO_TYPE,
        "severity": "problem",
        "title": "The country database is missing" if why == "missing" else "The country database is out of date",
        "text": (
            "Country rules for production instances cannot tell where a visitor is. "
            + (
                "Visitors who match a country rule are turned away."
                if closed
                else "Everyone passes the country part of a rule."
            )
            if why == "missing"
            else f"It is more than {settings.access_rules_geo_max_age_days} days old, so country rules may decide by old data."
        )
        + " The panel downloads a new one each month when ACCESS_RULES_GEO_DOWNLOAD is on and the address in "
        "ACCESS_RULES_GEO_URL can be reached.",
        "hint": None,
        "details": [],
        "instance": None,
        "path": "/settings?tab=security&focus=access_rules_geo_download" if manage_settings else None,
        "facts": {},
        "at": now,
        "last_at": now,
        "count": 1,
        "resolved_at": None,
        "delivery": [],
    }


@blueprint.get("/alerts")
@require("audit.view")
def list_alerts() -> Response:
    limit = check.whole_number(request.args.get("limit", "50"), "limit", minimum=1, maximum=alerts.KEEP)
    items = alerts.recent(alerts.KEEP)
    if warning := country_database_alert(g.user["role"], can("settings.manage")):
        items.insert(0, warning)
    if warning := default_password_alert(g.user["role"], can("settings.manage")):
        items.insert(0, warning)
    return page(items, limit, 0, alerts.resource)


@blueprint.post("/alerts/test")
@require("settings.manage")
def test_alert() -> Response:
    data = check.body()
    channel = check.one_of(
        check.text(data, "channel"), ("email", "webhook"), 'Choose the channel "email" or "webhook".'
    )
    number = None
    if channel == "webhook":
        given = data.get("webhook")
        if not isinstance(given, int) or isinstance(given, bool) or given not in alerts.webhooks.NUMBERS:
            abort(400, "Choose the webhook to test: 1, 2 or 3.")
        number = given
    what = "e-mail" if number is None else f"webhook {number}"
    try:
        result = alerts.send_test(channel, number, user=g.user["name"])
    except alerts.AlertError as error:
        log("test alert not sent", detail=what, ok=False)
        abort(400 if isinstance(error, alerts.NotConfigured) else 502, str(error))
    log("test alert sent", detail=what if number else f"e-mail to {len(result['sent_to'])} addresses")
    return ok(result)
