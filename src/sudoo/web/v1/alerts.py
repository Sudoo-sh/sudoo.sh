"""Alerts (alerts/): the list behind the web app's bell, and test alerts.

    GET  /api/v1/alerts          the newest alerts first (permission audit.view); ?limit=1-200 (default 50)
    POST /api/v1/alerts/test     send a test alert with the saved settings (permission settings.manage):
                                 {"channel": "email"} to every alert recipient, or {"channel": "webhook",
                                 "webhook": 1-3} to one webhook -> 200, 400 (nothing to send to), 502 (not delivered)

New and changed alerts also arrive as the live event "alert" (only for callers that may read the audit log).
A webhook's address and secret never appear in answers or in the audit log: it is named by its number.
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, g, request

from sudoo import alerts

from .. import validation as check
from ..security import log, require
from .responses import ok, page

blueprint = Blueprint("alerts", __name__)


@blueprint.get("/alerts")
@require("audit.view")
def list_alerts() -> Response:
    limit = check.whole_number(request.args.get("limit", "50"), "limit", minimum=1, maximum=alerts.KEEP)
    return page(alerts.recent(alerts.KEEP), limit, 0, alerts.resource)


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
