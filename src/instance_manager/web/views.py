"""Pages: the panel (one page app), the sign-in page, the icon and a health check."""

from __future__ import annotations

from typing import cast

from flask import Blueprint, Response, current_app, redirect, send_from_directory
from flask.typing import ResponseReturnValue

from .security import current_user

blueprint = Blueprint("views", __name__)


@blueprint.get("/")
@blueprint.get("/i/<name>")
def panel(name: str | None = None) -> Response:
    return send_from_directory(cast(str, current_app.static_folder), "index.html")


@blueprint.get("/login")
def login_page() -> ResponseReturnValue:
    if current_user():
        return redirect("/")
    return send_from_directory(cast(str, current_app.static_folder), "login.html")


@blueprint.get("/favicon.ico")
def favicon() -> Response:
    return send_from_directory(
        cast(str, current_app.static_folder), "favicon.ico", mimetype="image/x-icon", max_age=86400
    )


@blueprint.get("/healthz")
def health() -> str:
    return "ok"
