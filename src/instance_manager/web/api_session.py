"""Sign-in, sign-out and the signed-in user's own account."""

from __future__ import annotations

from flask import Blueprint, Response, abort, g, jsonify, session

from instance_manager import accounts
from instance_manager.config import settings
from instance_manager.core import audit

from . import validation as check
from .security import client_ip, log, start_session

blueprint = Blueprint("session", __name__, url_prefix="/api")


@blueprint.get("/contact")
def contact() -> Response:
    """Who to contact; shown on the sign-in page."""
    return jsonify(sentence=settings.contact_sentence())


@blueprint.post("/login")
def login() -> Response:
    data = check.body()
    name = check.text(data, "username", max_length=32).lower()
    password = check.secret(data, "password")
    ip = client_ip()
    wait = accounts.THROTTLE.seconds_to_wait(ip, name)
    if wait:
        audit.record(name, ip, "sign-in refused (too many attempts)", ok=False)
        abort(429, f"Too many failed attempts. Try again in {max(1, wait // 60)} minutes.")
    account = accounts.authenticate(name, password) if accounts.USERNAME_RE.match(name) else None
    if not account:
        accounts.THROTTLE.failed(ip, name)
        audit.record(name, ip, "sign-in failed", ok=False)
        abort(401, "Wrong user name or password.")
    accounts.THROTTLE.succeeded(ip, name)
    accounts.record_sign_in(name)
    start_session(name, account["session_version"])
    audit.record(name, ip, "signed in")
    return jsonify(ok=True)


@blueprint.post("/logout")
def logout() -> Response:
    log("signed out")
    session.clear()
    return jsonify(ok=True)


@blueprint.get("/me")
def me() -> Response:
    return jsonify(
        user=g.user["name"],
        role=g.user["role"],
        role_label=accounts.ROLES[g.user["role"]],
        csrf=session["csrf"],
        contact=settings.admin_contact,
        idle_hours=settings.idle_hours,
        poll_minutes=settings.poll_minutes,
    )


@blueprint.post("/account/password")
def change_password() -> Response:
    data = check.body()
    current, new = check.secret(data, "current"), check.secret(data, "new")
    name = g.user["name"]
    if not accounts.authenticate(name, current):
        log("password change refused (wrong current password)", ok=False)
        abort(400, "The current password is not correct.")
    problem = accounts.password_problem(new, name)
    if problem:
        abort(400, problem)
    session["version"] = accounts.set_password(name, new)  # other sessions of this account end
    log("changed own password")
    return jsonify(ok=True)
