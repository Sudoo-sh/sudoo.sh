"""Pages: the panel (one page app), the sign-in page, the icon and a health check."""
from flask import Blueprint, current_app, redirect, send_from_directory

from .security import current_user

blueprint = Blueprint("views", __name__)


@blueprint.get("/")
@blueprint.get("/i/<name>")
def panel(name=None):
    return send_from_directory(current_app.static_folder, "index.html")


@blueprint.get("/login")
def login_page():
    if current_user():
        return redirect("/")
    return send_from_directory(current_app.static_folder, "login.html")


@blueprint.get("/favicon.ico")
def favicon():
    return send_from_directory(current_app.static_folder, "favicon.ico", mimetype="image/x-icon", max_age=86400)


@blueprint.get("/healthz")
def health():
    return "ok"
