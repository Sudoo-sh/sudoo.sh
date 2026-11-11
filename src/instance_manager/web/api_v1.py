"""The API for other systems: /api/v1, authenticated by an API token and a signature on every request.

How to call it, with examples: docs/PANEL_API.md. Authentication happens in security.py (api_tokens.verify) before
any view runs; a request then acts as the user "api:<token name>": an administrator for write tokens, a read-only
caller for read tokens. The views reuse the panel's own endpoints (api_instances.py), so every input check, role
check and audit-log entry is exactly the same as for a person using the panel.

    GET    /api/v1/instances                         list
    GET    /api/v1/instances/<name>                  details (no passwords)
    POST   /api/v1/instances                         create                      -> 202 {"job", "name"}
    PATCH  /api/v1/instances/<name>                  change automation settings and lifetime
    POST   /api/v1/instances/<name>/actions/<action> restart, update, pause, ... -> 202 {"job"}
    DELETE /api/v1/instances/<name>                  delete after a backup; body {"confirm": "<name>"} -> 202 {"job"}
    GET    /api/v1/jobs/<id>                         progress of a job
    GET    /api/v1/versions                          Odoo versions and Enterprise availability
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, jsonify

from instance_manager import addons
from instance_manager.instances import odoo, store

from . import api_instances
from . import validation as check
from .security import log

blueprint = Blueprint("api_v1", __name__, url_prefix="/api/v1")

_HIDDEN = ("admin_password", "master_password", "ports", "history")  # never sent through the API


@blueprint.get("/instances")
def list_instances() -> Response:
    return jsonify(instances=[api_instances.summary(name) for name in store.names()])


@blueprint.get("/instances/<name>")
def instance(name: str) -> Response:
    details: dict[str, Any] = api_instances.details(name).get_json()
    return jsonify({key: value for key, value in details.items() if key not in _HIDDEN})


@blueprint.post("/instances")
def create() -> tuple[Response, int]:
    return api_instances.create(), 202


@blueprint.patch("/instances/<name>")
def change(name: str) -> Response:
    """Change only the settings that are sent; the others keep their value."""
    api_instances.existing(name)
    data = check.body()
    switches = {"auto_update": "AUTO_UPDATE", "auto_upgrade": "AUTO_UPGRADE", "auto_pause": "AUTO_PAUSE"}
    values: dict[str, object] = {
        key: str(check.flag(data, field)).lower() for field, key in switches.items() if field in data
    }
    if "delete_after_days" in data:
        values["DELETE_AFTER_DAYS"] = check.days(data, "delete_after_days")
    if not values:
        abort(400, "Send at least one of: auto_update, auto_upgrade, auto_pause, delete_after_days.")
    store.update_env(name, **values)
    log("settings changed through the API", instance=name, detail=", ".join(sorted(values)))
    return instance(name)


@blueprint.post("/instances/<name>/actions/<action>")
def action(name: str, action: str) -> tuple[Response, int]:
    return api_instances.run_action(name, action), 202


@blueprint.delete("/instances/<name>")
def delete(name: str) -> tuple[Response, int]:
    return api_instances.run_action(name, "delete"), 202


@blueprint.get("/jobs/<job_id>")
def job(job_id: str) -> Response:
    return api_instances.job_progress(job_id)


@blueprint.get("/versions")
def versions() -> Response:
    return jsonify(versions=[{"version": v, "enterprise": addons.available(v)} for v in odoo.versions()])
