"""The panel's settings: read by everyone signed in, changed by administrators.

    GET    /api/v1/settings          every setting: value, .env value, whether it was changed here, editable or not
    PATCH  /api/v1/settings          change editable settings: {"idle_hours": 4, "auto_pause": false}; null resets
    DELETE /api/v1/settings/<key>    bring back the .env value -> 204

Changes take effect at once and are kept on top of .env (config.py, LiveSettings). Settings needed when the panel
starts (ports, plugins, images) are shown with "editable": false. Secrets are never shown, only whether they are
set.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort
from pydantic import ValidationError

from sudoo import config
from sudoo.config import Settings, live, settings
from sudoo.core import events

from .. import validation as check
from ..security import admin_only, log
from .responses import no_content, ok

blueprint = Blueprint("settings", __name__)

_FIXED = {
    "monitor_interval_seconds",
    "pause_check_interval_seconds",
    "expiry_check_interval_seconds",
    "history_samples",
}
_HIDDEN = {"docs_dir", "memory_dir"}


def _type(annotation: Any) -> str:
    return {bool: "boolean", int: "integer", float: "number"}.get(annotation, "string")


def _shown(key: str, value: Any) -> Any:
    if key in config.SECRET:
        return bool(value)  # set or not
    return str(value) if not isinstance(value, bool | int | float | str) else value


def resource() -> list[dict[str, Any]]:
    overrides = live.overrides()
    result = []
    for key, field in Settings.model_fields.items():
        if key in _FIXED or key in _HIDDEN:
            continue
        minimum = next((getattr(m, "ge", None) for m in field.metadata if getattr(m, "ge", None) is not None), None)
        result.append(
            {
                "key": key,
                "env": key.upper(),
                "value": _shown(key, getattr(settings, key)),
                "env_value": _shown(key, getattr(live.env, key)),
                "changed_in_panel": key in overrides,
                "editable": key in config.EDITABLE,
                "secret": key in config.SECRET,
                "type": _type(field.annotation),
                "minimum": minimum,
                "description": field.description,
            }
        )
    return result


@blueprint.get("/settings")
def read() -> Response:
    return ok(resource())


def _change(values: dict[str, Any]) -> None:
    try:
        live.change(values)
    except ValidationError as error:
        first = error.errors()[0]
        abort(400, f"{str(first['loc'][0]).upper()}: {first['msg']}.")
    except ValueError as error:
        abort(400, str(error))
    log("settings changed", detail=", ".join(f"{k.upper()}={'(env)' if v is None else v}" for k, v in values.items()))


@blueprint.patch("/settings")
@admin_only
def change() -> Response:
    values = check.body()
    if not values:
        abort(400, 'Send the settings to change, for example {"idle_hours": 4}.')
    _change(values)
    return ok(resource())


@blueprint.delete("/settings/<key>")
@admin_only
def reset(key: str) -> Response:
    if key not in config.EDITABLE:
        abort(404, "No setting with this name can be changed in the panel.")
    _change({key: None})
    return no_content()


def publish_changes(changed: dict[str, Any]) -> None:
    """Live event "settings" after every change (registered in web/v1/__init__.py)."""
    events.publish("settings", ",".join(sorted(changed)), {"changed": sorted(changed)})
