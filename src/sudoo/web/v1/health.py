"""What is wrong with the panel right now, for the administrators (core/resilience.py, plugin_guard.py).

    GET  /api/v1/health                                  {"status": "ok" | "degraded", "problems": [...],
                                                          "plugins": [...]}: settings with wrong values, plugins
                                                          that failed or were switched off, parts that did not
                                                          start, background jobs that keep failing
    POST /api/v1/health/plugins/<group>/<name>/enable    switch a plugin that was switched off after failing on
                                                          again: it is loaded again at its next use

Seeing needs "plugins.view" (super admins and administrators), switching on "settings.manage" (super admins).
/healthz, the container's health check, answers without signing in and says only ok, degraded or unavailable.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo import plugin_guard
from sudoo.core import resilience

from ..security import require
from .responses import iso, ok

blueprint = Blueprint("health", __name__)


def _problem(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "since": iso(item.get("since")), "last": iso(item.get("last"))}


def _plugin(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "group": state.get("group"),
        "name": state.get("name"),
        "label": plugin_guard.label(str(state.get("group"))),
        "failures": int(state.get("failures") or 0),
        "stage": state.get("stage"),
        "error": state.get("error") or "",
        "disabled": bool(state.get("disabled")),
        "since": iso(state.get("since")),
        "last": iso(state.get("last")),
    }


@blueprint.get("/health")
@require("plugins.view")
def health() -> Response:
    problems = [_problem(item) for item in resilience.problems()]
    plugins = [_plugin(state) for state in plugin_guard.failures()]
    return ok({"status": "degraded" if problems or plugins else "ok", "problems": problems, "plugins": plugins})


@blueprint.post("/health/plugins/<group>/<name>/enable")
@require("settings.manage")
def enable(group: str, name: str) -> Response:
    if not group.startswith("sudoo.") or len(group) > 100 or len(name) > 100:
        abort(404, "No such plugin.")
    if not plugin_guard.enable(group, name, g.user["name"]):
        abort(404, "This plugin has not failed; there is nothing to switch on.")
    return ok({"group": group, "name": name, "disabled": False})
