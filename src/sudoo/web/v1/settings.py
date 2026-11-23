"""The panel's settings (permission settings.manage).

    GET    /api/v1/settings              every setting: value, .env value, changed here or not, when it applies
    PATCH  /api/v1/settings              change settings: {"idle_hours": 4, "smtp_password": "..."}; null resets
    DELETE /api/v1/settings/<key>        bring back the .env value -> 204
    POST   /api/v1/settings/test-email   send the test e-mail to {"to"} with the current mail settings

"applies" says when a change takes effect: "now", "restart" (it chooses a plugin; restart the panel with
POST /api/v1/server/restart) or "env" (only in .env: folders, ports, storage). EXPOSURE applies at restart, and
the panel takes only direct, proxy or tunnel, and only while the web service listens on 127.0.0.1 before and after. Changes are kept on top of
.env (config.py, LiveSettings); secrets are stored encrypted and never shown, only whether they are set. The audit
log names every changed setting with its new value, except secrets: "SMTP_PASSWORD=(set)". Changing an off-site
setting (OFFSITE_*) also needs the permission offsite.configure.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort
from pydantic import ValidationError

from sudoo import accounts, config, licence
from sudoo.config import Settings, live, settings
from sudoo.core import events

from .. import validation as check
from ..security import log, refuse_unless, refuse_without_feature, require
from .responses import no_content, ok

blueprint = Blueprint("settings", __name__)

_FIXED = {
    "pause_check_interval_seconds",
    "expiry_check_interval_seconds",
    "integrity_check_interval_seconds",
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
                "editable": key in config.EDITABLE or key in config.RESTART,
                "applies": "now" if key in config.EDITABLE else "restart" if key in config.RESTART else "env",
                "pending_restart": live.pending_restart(key),
                "secret": key in config.SECRET,
                "type": _type(field.annotation),
                "minimum": minimum,
                "description": field.description,
            }
        )
    return result


@blueprint.get("/settings")
@require("settings.manage")
def read() -> Response:
    return ok(resource())


def audit_value(key: str, value: Any) -> str:
    """How a changed setting appears in the audit log: its value, "(env)" when reset, "(set)" for a secret."""
    if value is None:
        return "(env)"
    return "(set)" if key in config.SECRET else str(value)


def web_bind(values: Settings) -> str:
    """Where the web service listens with these settings (web/entrypoint.sh, which reads .env when it starts)."""
    mode = config.exposure_mode(values.exposure)
    return (
        "0.0.0.0" if mode == "host-ports" else str(values.proxy_listen) if mode == "proxy" else "127.0.0.1"
    )  # noqa: S104


def check_exposure(value: Any) -> None:
    """A change of EXPOSURE in the panel: one of the three modes (or the .env value back), and only where the web
    service, which takes its address from .env when it starts, does not have to move: it must listen on 127.0.0.1
    before and after (otherwise the change is made in .env, and the services recreated)."""
    if value is None or value == live.env.exposure:
        return
    if value not in config.EXPOSURE_MODES:
        abort(400, f"EXPOSURE: choose one of {', '.join(config.EXPOSURE_MODES)}.")
    if web_bind(live.env) != "127.0.0.1" or (value == "proxy" and settings.proxy_listen != "127.0.0.1"):
        abort(
            409,
            "EXPOSURE: the web service would have to listen on another address. Change EXPOSURE in "
            "/opt/odoo/manager/.env instead and recreate the services (docs/ADMIN_GUIDE.md).",
        )
    if value == "proxy" and not (settings.panel_domain or settings.public_host):
        abort(400, "EXPOSURE=proxy needs PANEL_DOMAIN or PUBLIC_HOST: the name your proxy serves the panel under.")


def _keep_break_glass(value: Any) -> None:
    """SIGN_IN_METHODS without "password" must leave a super admin that still signs in with one (auth/methods.py):
    one whose own methods, chosen by an administrator, include it."""
    from sudoo.auth import methods

    chosen = live.env.sign_in_methods if value is None else str(value)
    if "password" in [part.strip().lower() for part in chosen.split(",")]:
        return
    users = accounts.all_users()
    if not any(methods.has_break_glass(user) for user in users.values()):
        return  # nothing to keep (or nothing left already)
    keeps = any(
        user.get("role") == "super_admin" and user.get("hash") and "password" in (methods.own_choice(user) or [])
        for user in users.values()
    )
    if not keeps:
        abort(
            400,
            "SIGN_IN_METHODS: at least one super admin must keep signing in with a password. Choose Password for one "
            "on the Accounts page first.",
        )


def _change(values: dict[str, Any]) -> None:
    if "exposure" in values:
        check_exposure(values["exposure"])
    if "sign_in_methods" in values:
        _keep_break_glass(values["sign_in_methods"])
    offsite = sorted(set(values) & set(config.OFFSITE_SETTINGS))
    if offsite:  # where backups go off the server: its own permission, besides settings.manage
        refuse_unless("offsite.configure", f"change {', '.join(k.upper() for k in offsite)}")
    try:
        if "licence_required_version" in values:
            licence.check_required_version(values["licence_required_version"])
        live.change(values)
    except ValidationError as error:
        first = error.errors()[0]
        abort(400, f"{str(first['loc'][0]).upper()}: {first['msg']}.")
    except ValueError as error:
        abort(400, str(error))
    log("settings changed", detail=", ".join(f"{k.upper()}={audit_value(k, v)}" for k, v in values.items()))


@blueprint.patch("/settings")
@require("settings.manage")
def change() -> Response:
    values = check.body()
    if not values:
        abort(400, 'Send the settings to change, for example {"idle_hours": 4}.')
    if values.get("offsite_backups") is True and not settings.offsite_backups:  # turning off-site copies on
        refuse_without_feature("offsite_backups", "turn off-site copies on")
    _change(values)
    return ok(resource())


@blueprint.delete("/settings/<key>")
@require("settings.manage")
def reset(key: str) -> Response:
    if key not in config.EDITABLE and key not in config.RESTART:
        abort(404, "No setting with this name can be changed in the panel.")
    _change({key: None})
    return no_content()


@blueprint.post("/settings/test-email")
@require("settings.manage")
def test_email() -> Response:
    from sudoo.mail import MailError, mailer, send

    to = check.text(check.body(), "to", max_length=254)
    if not accounts.EMAIL_RE.match(to):
        abort(400, "Enter the e-mail address to send the test to.")
    if not mailer().ready:
        abort(400, "E-mail is not set up yet: fill in the mail server (SMTP) settings first.")
    server = f"{settings.smtp_host}:{settings.smtp_port} ({settings.smtp_security})"
    try:
        send(to, "test", server=server)
    except MailError as error:
        abort(502, f"{error} For Gmail, the password must be an app password.")
    log("test e-mail sent", detail=to)
    return ok({"sent_to": to})


def publish_changes(changed: dict[str, Any]) -> None:
    """Live event "settings" after every change (registered in web/v1/__init__.py)."""
    events.publish("settings", ",".join(sorted(changed)), {"changed": sorted(changed)})
