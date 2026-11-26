"""The backend's web side: the API that the web app (web/, a separate service) and other systems use.

security.py     who is calling (session or API token), permissions, rate limits, headers
signin.py       signing in: password, second factor, restricted sessions
validation.py   checks every value received from a browser or API client
docs.py         the documentation (README.md, docs/): rendered for the web app's Help pages, and its images
v1/             the API (/api/v1) and the live event stream
"""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from typing import cast

from flask import Flask, Response, jsonify, request
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import HTTPException

from sudoo import accounts, background, exposure, link_deadline, triggers
from sudoo.config import settings
from sudoo.core import slots

from . import docs, security, v1


def default_password_warning() -> str | None:
    """A warning for the log when new instances would get the well-known Odoo login admin / admin, else None.

    Only a warning: the setting keeps its default (demos and tests rely on it); the installer already replaces it
    with a random password, so this shows up for manual Compose or source setups."""
    if settings.odoo_admin_password != "admin":
        return None
    return (
        'ODOO_ADMIN_PASSWORD is still the well-known "admin": anyone with an instance\'s link can sign in as its '
        "administrator. Set another ODOO_ADMIN_PASSWORD in the panel's Settings page or in .env."
    )


def create_app() -> Flask:
    """Build the panel and start the background work (monitor, gateway, connectors, scheduled jobs)."""
    # a new ODOO_ROOT: the panel's own folder holds its keys and data, readable by root only
    settings.manager_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=accounts.session_secret(),
        SESSION_COOKIE_NAME="sudoo_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=exposure.provider().https,  # https only, when the panel is reached over https
        # the cookie's own lifetime; sessions end sooner, as SESSION_MAX_HOURS says now (security.user_of_session)
        PERMANENT_SESSION_LIFETIME=timedelta(days=31),
        MAX_CONTENT_LENGTH=64 * 1024,  # every request is a small JSON object
        SEND_FILE_MAX_AGE_DEFAULT=0,
    )
    if warning := default_password_warning():
        app.logger.warning(warning)
    app.json.sort_keys = False  # type: ignore[attr-defined]  # answers keep the order they were built in: no sort per answer
    security.install(app)
    from sudoo.premium import servers

    servers.install(app)  # several servers: requests about another server's instances go to its agent
    app.register_blueprint(docs.blueprint)
    v1.register(app)
    app.add_url_rule("/healthz", "health", lambda: "ok")
    for webhook in triggers.blueprints():  # endpoints of the enabled update triggers, under /hooks/
        app.register_blueprint(webhook)

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> ResponseReturnValue:
        # Flask hands an HTTPException without a code straight back, so `code` is set here
        code = cast(int, error.code)
        if request.path.startswith(security.API_PREFIX):
            return v1.problem(code, error.description or HTTPStatus(code).phrase)
        return jsonify(error=error.description), code  # the documentation's images, /healthz

    @app.errorhandler(slots.Refused)
    def slots_refused(error: slots.Refused) -> ResponseReturnValue:
        """The running limits refused a start inside the write that would have recorded it."""
        if request.path.startswith(security.API_PREFIX):
            return v1.problem(403, str(error))
        return jsonify(error=str(error)), 403

    @app.errorhandler(slots.Unavailable)
    def slots_unavailable(error: slots.Unavailable) -> ResponseReturnValue:
        """A start that could not be checked or recorded in time is refused, never started unrecorded."""
        if request.path.startswith(security.API_PREFIX):
            return v1.problem(503, str(error))
        return jsonify(error=str(error)), 503

    @app.errorhandler(Exception)
    def unexpected_error(error: Exception) -> ResponseReturnValue:
        app.logger.exception("unexpected error")
        if request.path.startswith(security.API_PREFIX):
            return v1.problem(500, "Something unexpected went wrong on the server. Quote the request_id.")
        return jsonify(error="Something unexpected went wrong on the server."), 500

    @app.after_request
    def api_request_id(response: Response) -> Response:
        """Every API answer carries X-Request-ID, also errors raised before a view ran."""
        return v1.add_request_id(response) if request.path.startswith(security.API_PREFIX) else response

    if servers.is_agent():  # a server's agent: no web page, no event stream; the panel passes requests on
        background.start()
        return app
    link_deadline.first_run()  # recorded once, the first time the panel runs (link_deadline.py)
    background.start()
    v1.stream.start(app)
    _start_support_channel(app)
    return app


def _start_support_channel(app: Flask) -> None:
    """Sudoo.sh support's requests, while a grant is active: with the premium support package only."""
    from sudoo.premium import stubs

    found = stubs.support()
    if found is not None:
        found.start_channel(app)
