"""Plugins and the marketplace (docs/MARKETPLACE.md).

    GET    /api/v1/plugins                         installed plugins: status, licence, update available
    GET    /api/v1/plugins/notices                 banners: licences ending or ended, modified plugins
    POST   /api/v1/plugins                         install {"id", "version"?} -> 202 job (then the backend restarts)
    POST   /api/v1/plugins/<id>/update             install the newest version -> 202 job
    POST   /api/v1/plugins/<id>/disable|enable     switch off or on (the backend restarts) -> the plugin
    DELETE /api/v1/plugins/<id>                    uninstall {"confirm": "<id>"} after a backup -> 202 job
    GET    /api/v1/marketplace                     the connection to the hub: registered, linked, lease, errors
    POST   /api/v1/marketplace/link                {"code"}: link this panel to an app.sudoo.sh account
    POST   /api/v1/marketplace/heartbeat           send the heartbeat now
    GET    /api/v1/marketplace/catalog             the catalogue (q, type, tier, publisher, sort, limit, offset)
    GET    /api/v1/marketplace/catalog/<id>        one plugin: plans, permissions, requires
    GET    /api/v1/marketplace/categories          plugin types with counts
    GET    /api/v1/marketplace/purchases           purchases of this server (?format=csv for a file)
    POST   /api/v1/marketplace/purchases           buy {"plugin_id", "plan_id"}: installs at once when paid
    GET    /api/v1/marketplace/purchases/<id>      one purchase (the web app follows a Checkout with it)

Seeing needs "plugins.view" (super admins and administrators); installing, buying, switching and linking need
"settings.manage" (super admins). No endpoint returns a plugin's files or package.
"""

from __future__ import annotations

import csv
import io
import threading
import time
from typing import Any, NoReturn
from urllib.parse import urlsplit

from flask import Blueprint, Response, abort, g, request

from sudoo import plugin_manager
from sudoo.core import jobs
from sudoo.hub import HubRefused, HubUnavailable, marketplace

from .. import validation as check
from ..security import log, require
from .resources import job_resource
from .responses import PREFIX, iso, ok

blueprint = Blueprint("plugins", __name__)

UNREACHABLE = "The marketplace cannot be reached right now. Installed plugins keep working; try again later."
_LATEST: dict[str, tuple[float, str | None]] = {}  # plugin id -> (when asked, newest published version)
_LATEST_SECONDS = 600
_LATEST_LOCK = threading.Lock()


def _hub_error(error: Exception) -> NoReturn:
    """Turn a hub failure into an answer the web app shows (never 401, which would sign the person out)."""
    if isinstance(error, HubUnavailable):
        abort(503, UNREACHABLE)
    if isinstance(error, HubRefused):
        status = {404: 404, 422: 400, 429: 429}.get(error.status, 409)
        abort(status, plugin_manager._hub_message(error))
    raise error


def _marketplace_on() -> None:
    if not marketplace.enabled():
        abort(409, "The marketplace is switched off on this panel (Settings, Marketplace).")


# ---------------------------------------------------------------- installed plugins


def _latest(plugin_id: str) -> str | None:
    """The newest published version (asked at most every 10 minutes; None when the hub cannot say)."""
    with _LATEST_LOCK:
        cached = _LATEST.get(plugin_id)
    if cached and time.time() - cached[0] < _LATEST_SECONDS:
        return cached[1]
    newest: str | None = None
    if marketplace.enabled():
        try:
            newest = (marketplace.plugin(plugin_id).get("latest_version") or {}).get("version")
        except (HubUnavailable, HubRefused):
            newest = cached[1] if cached else None
    with _LATEST_LOCK:
        _LATEST[plugin_id] = (time.time(), newest)
    return newest


def plugin_resource(rec: dict[str, Any], now: float, lease_valid: bool, latest: bool = True) -> dict[str, Any]:
    licence = rec.get("licence") or {}
    newest = _latest(rec["id"]) if latest else None
    try:
        update = bool(newest and plugin_manager.semver_key(newest) > plugin_manager.semver_key(rec["version"]))
    except ValueError:
        update = False
    loaded = rec["id"] in (plugin_manager.LOADED or set())
    running = plugin_manager.allowed(rec, now, lease_valid)[0]
    return {
        "id": rec["id"],
        "name": rec.get("name", rec["id"]),
        "version": rec["version"],
        "type": rec.get("type"),
        "tier": rec.get("tier", "free"),
        "description": rec.get("description", ""),
        "publisher": rec.get("publisher"),
        "permissions": rec.get("permissions", []),
        "requires": rec.get("requires", []),
        "enabled": rec.get("enabled", True),
        # active, disabled, modified, blocked, switched_off (licence ended or no lease), unlicensed
        "status": plugin_manager.status_of(rec, now, lease_valid),
        "loaded": loaded,
        "restart_pending": loaded != running,
        "licence": (
            {
                "state": licence.get("state"),
                "standing": rec.get("standing"),
                "expires_at": iso(licence.get("expires_at")),
                "grace_until": iso(licence.get("grace_until")),
                "phase": plugin_manager.phase(rec, now, lease_valid),
            }
            if rec.get("tier") == "paid"
            else None
        ),
        "latest_version": newest,
        "update_available": update,
        "installed_at": iso(rec.get("installed_at")),
        "installed_by": rec.get("installed_by"),
        "updated_at": iso(rec.get("updated_at")),
    }


def _installed(plugin_id: str) -> dict[str, Any]:
    rec = plugin_manager.record(check.plugin_id(plugin_id))
    if rec is None:
        abort(404, "This plugin is not installed.")
    return rec


@blueprint.get("/plugins")
@require("plugins.view")
def installed() -> Response:
    now, lease_valid = marketplace.trusted_now(), marketplace.lease() is not None
    found = sorted(plugin_manager.records().values(), key=lambda r: str(r.get("name", r["id"])).lower())
    return ok(
        [plugin_resource(rec, now, lease_valid) for rec in found],
        meta={"restart_pending": plugin_manager.restart_pending()},
    )


@blueprint.get("/plugins/notices")
@require("plugins.view")
def notices() -> Response:
    return ok([{**notice, "date": iso(notice.get("date"))} for notice in plugin_manager.notices()])


def _start_install(plugin_id: str, version: str | None, title: str) -> jobs.Job:
    if jobs.ACTIVE_JOB:
        running = ", ".join(sorted(j.instance for j in jobs.ACTIVE_JOB.values()))
        abort(409, f"Jobs are running ({running}). Install when they have finished: the panel restarts afterwards.")
    if plugin_manager.restart_pending():
        abort(409, "The panel is about to restart. Try again in a minute.")
    return jobs.start(
        plugin_manager.JOB_INSTANCE, title, "plugin-install", plugin_manager.install, plugin_id, version,
        g.user["name"], user=g.user["name"],
    )  # fmt: skip


def _accepted(job: jobs.Job) -> Response:
    response = ok(job_resource(job), status=202)
    response.headers["Location"] = f"{PREFIX}/jobs/{job.id}"
    return response


@blueprint.post("/plugins")
@require("settings.manage")
def install() -> Response:
    _marketplace_on()
    data = check.body()
    plugin_id = check.plugin_id(check.text(data, "id", max_length=128))
    version = check.text(data, "version", max_length=64) or None
    if version:
        check.plugin_version(version)
    current = plugin_manager.record(plugin_id)
    if current and current["version"] == version and not current.get("tampered"):
        abort(409, f"{plugin_id} {version} is already installed.")
    log("plugin install requested", detail=f"{plugin_id} {version or 'latest'}")
    return _accepted(_start_install(plugin_id, version, f"Install {plugin_id}"))


@blueprint.post("/plugins/<plugin_id>/update")
@require("settings.manage")
def update(plugin_id: str) -> Response:
    _marketplace_on()
    rec = _installed(plugin_id)
    log("plugin update requested", detail=plugin_id)
    return _accepted(_start_install(rec["id"], None, f"Update {rec.get('name', rec['id'])}"))


def _switch(plugin_id: str, value: bool) -> Response:
    rec = _installed(plugin_id)
    try:
        updated = plugin_manager.set_enabled(rec["id"], value, g.user["name"])
    except plugin_manager.PluginRefused as error:
        abort(409, str(error))
    return ok(plugin_resource(updated, marketplace.trusted_now(), marketplace.lease() is not None, latest=False))


@blueprint.post("/plugins/<plugin_id>/disable")
@require("settings.manage")
def disable(plugin_id: str) -> Response:
    return _switch(plugin_id, False)


@blueprint.post("/plugins/<plugin_id>/enable")
@require("settings.manage")
def enable(plugin_id: str) -> Response:
    return _switch(plugin_id, True)


@blueprint.delete("/plugins/<plugin_id>")
@require("settings.manage")
def uninstall(plugin_id: str) -> Response:
    rec = _installed(plugin_id)
    if check.text(check.body(), "confirm", max_length=128) != rec["id"]:
        abort(400, f'To uninstall, send {{"confirm": "{rec["id"]}"}}.')
    if needed_by := plugin_manager.dependents(rec["id"]):
        abort(409, f"Uninstall {', '.join(needed_by)} first: it needs this plugin.")
    if jobs.ACTIVE_JOB:
        abort(409, "Jobs are running. Uninstall when they have finished: the panel restarts afterwards.")
    log("plugin uninstall requested", detail=rec["id"])
    job = jobs.start(
        plugin_manager.JOB_INSTANCE, f"Uninstall {rec.get('name', rec['id'])}", "plugin-uninstall",
        plugin_manager.uninstall, rec["id"], g.user["name"], user=g.user["name"],
    )  # fmt: skip
    return _accepted(job)


# ---------------------------------------------------------------- the marketplace


@blueprint.get("/marketplace")
@require("plugins.view")
def status() -> Response:
    current = marketplace.status()
    for key in ("last_heartbeat_at", "next_heartbeat_at", "lease_expires_at"):
        current[key] = iso(current[key])
    return ok(current)


@blueprint.post("/marketplace/link")
@require("settings.manage")
def link() -> Response:
    _marketplace_on()
    code = check.link_code(check.text(check.body(), "code", max_length=32).replace(" ", ""))
    try:
        linked = marketplace.link(code)
    except (HubUnavailable, HubRefused) as error:
        log("panel link refused", ok=False)
        _hub_error(error)
    log("panel linked to a Sudoo.sh account", detail=linked.get("account_name"))
    return ok(linked)


@blueprint.post("/marketplace/heartbeat")
@require("settings.manage")
def heartbeat() -> Response:
    _marketplace_on()
    try:
        marketplace.heartbeat()
    except (HubUnavailable, HubRefused) as error:
        _hub_error(error)
    return status()


_SORTS = ("popular", "rating", "newest", "name", "trusted")
_TIERS = ("free", "paid")
_PUBLISHERS = ("verified", "official")


def _query() -> dict[str, str]:
    args = request.args
    query: dict[str, str] = {}
    q = check.text(args, "q", max_length=100)
    if q:
        query["q"] = q
    kind = check.text(args, "type", max_length=40)
    if kind:
        if not kind.replace("_", "").isalpha():
            abort(400, "Give a plugin type such as mailer.")
        query["type"] = kind
    for key, allowed in (("tier", _TIERS), ("publisher", _PUBLISHERS), ("sort", _SORTS)):
        value = args.get(key, "")
        if value:
            query[key] = check.one_of(value, allowed, f"{key} must be one of: {', '.join(allowed)}.")
    query["limit"] = str(check.whole_number(args.get("limit", "48"), "limit", minimum=1, maximum=100))
    query["offset"] = str(check.whole_number(args.get("offset", "0"), "offset", maximum=100_000))
    return query


@blueprint.get("/marketplace/catalog")
@require("plugins.view")
def catalog() -> Response:
    _marketplace_on()
    try:
        items, meta = marketplace.catalog(_query())
    except (HubUnavailable, HubRefused) as error:
        _hub_error(error)
    installed_versions = {pid: rec["version"] for pid, rec in plugin_manager.records().items()}
    for item in items:
        if isinstance(item, dict):
            item["installed_version"] = installed_versions.get(str(item.get("id")))
    return ok(items, meta=meta)


@blueprint.get("/marketplace/catalog/<plugin_id>")
@require("plugins.view")
def catalog_plugin(plugin_id: str) -> Response:
    _marketplace_on()
    try:
        detail = marketplace.plugin(check.plugin_id(plugin_id))
    except (HubUnavailable, HubRefused) as error:
        _hub_error(error)
    rec = plugin_manager.record(plugin_id)
    detail["installed_version"] = rec["version"] if rec else None
    return ok(detail)


@blueprint.get("/marketplace/categories")
@require("plugins.view")
def categories() -> Response:
    _marketplace_on()
    try:
        return ok(marketplace.categories())
    except (HubUnavailable, HubRefused) as error:
        _hub_error(error)


def _https(value: Any) -> str | None:
    """Only https links from the hub reach the browser."""
    return value if isinstance(value, str) and urlsplit(value).scheme == "https" else None


def purchase_resource(item: dict[str, Any]) -> dict[str, Any]:
    plan: dict[str, Any] = item["plan"] if isinstance(item.get("plan"), dict) else {}
    return {
        "id": str(item.get("id", "")),
        "plugin_id": item.get("plugin_id"),
        "plugin_name": item.get("plugin_name") or item.get("plugin_id"),
        "plan": item.get("plan_kind") or plan.get("kind"),
        "amount_cents": item.get("amount_cents"),
        "currency": item.get("currency"),
        "status": item.get("status"),  # paid, pending, failed, refunded, cancelled
        "kind": item.get("kind"),  # purchase or renewal
        "created_at": item.get("created_at"),
        "paid_at": item.get("paid_at"),
        "receipt_url": _https(item.get("receipt_url")),
        "checkout_url": _https(item.get("checkout_url")),
    }


@blueprint.get("/marketplace/purchases")
@require("plugins.view")
def purchases() -> Response:
    _marketplace_on()
    try:
        found = [purchase_resource(p) for p in marketplace.purchases() if isinstance(p, dict)]
    except (HubUnavailable, HubRefused) as error:
        _hub_error(error)
    if request.args.get("format") == "csv":
        out = io.StringIO()
        columns = ["created_at", "plugin_name", "plugin_id", "kind", "plan", "amount_cents", "currency", "status",
                   "receipt_url"]  # fmt: skip
        writer = csv.writer(out)
        writer.writerow(columns)
        for item in found:
            writer.writerow([_cell(item.get(c)) for c in columns])
        response = Response(out.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = 'attachment; filename="sudoo-purchases.csv"'
        return response
    return ok(found)


def _cell(value: Any) -> str:
    """A CSV cell that a spreadsheet will not run as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@blueprint.post("/marketplace/purchases")
@require("settings.manage")
def buy() -> Response:
    _marketplace_on()
    data = check.body()
    plugin_id = check.plugin_id(check.text(data, "plugin_id", max_length=128))
    plan_id = check.hub_reference(check.text(data, "plan_id", max_length=64), "plan")
    try:
        bought = purchase_resource(marketplace.buy(plugin_id, plan_id))
    except (HubUnavailable, HubRefused) as error:
        log("plugin purchase refused", ok=False, detail=plugin_id)
        _hub_error(error)
    log("plugin purchase started", detail=f"{plugin_id} {bought['status']}")
    if bought["status"] == "paid":  # the saved card was charged: install at once
        job = _start_install(plugin_id, None, f"Install {plugin_id}")
        return ok({"purchase": bought, "job": job_resource(job)}, status=202)
    return ok({"purchase": bought, "job": None})


@blueprint.get("/marketplace/purchases/<purchase_id>")
@require("settings.manage")
def purchase(purchase_id: str) -> Response:
    _marketplace_on()
    try:
        found = marketplace.purchase(check.hub_reference(purchase_id, "purchase"))
    except (HubUnavailable, HubRefused) as error:
        _hub_error(error)
    return ok(purchase_resource(found))
