"""The web panel: a Flask app serving the panel's pages and its JSON API.

security.py        sign-in sessions, CSRF protection, security headers, roles
views.py           pages and static files
docs.py            the documentation (README.md, docs/) rendered inside the panel
validation.py      checks every value received from a browser or API client
api_session.py     sign-in, sign-out, own account
api_accounts.py    accounts and audit log (administrators)
api_instances.py   instances, jobs, logs
api_github.py      deploy keys and repository checks
api_backups.py     backups: list, download, delete (administrators)
"""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from typing import cast

from flask import Flask, Response, jsonify, request
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import HTTPException

from sudoo import accounts, background, exposure, triggers
from sudoo.config import settings

from . import (
    api_accounts,
    api_backups,
    api_github,
    api_instances,
    api_session,
    api_token_admin,
    api_v1,
    docs,
    security,
    views,
)


def create_app() -> Flask:
    """Build the panel and start the background work (monitor, gateway, connectors, scheduled jobs)."""
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.update(
        SECRET_KEY=accounts.session_secret(),
        SESSION_COOKIE_NAME="sudoo_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=exposure.provider().https,  # https only, when the panel is reached over https
        PERMANENT_SESSION_LIFETIME=timedelta(hours=settings.session_max_hours),
        MAX_CONTENT_LENGTH=64 * 1024,  # every request is a small JSON object
        SEND_FILE_MAX_AGE_DEFAULT=0,
    )
    security.install(app)
    for module in (
        views,
        docs,
        api_session,
        api_accounts,
        api_instances,
        api_github,
        api_backups,
        api_token_admin,
        api_v1,
    ):
        app.register_blueprint(module.blueprint)
    for webhook in triggers.blueprints():  # endpoints of the enabled update triggers, under /hooks/
        app.register_blueprint(webhook)

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> ResponseReturnValue:
        # Flask hands an HTTPException without a code straight back, so `code` is set here
        code = cast(int, error.code)
        if request.path.startswith(security.API_PREFIX):
            return api_v1.problem(code, error.description or HTTPStatus(code).phrase)
        return jsonify(error=error.description), code

    @app.errorhandler(Exception)
    def unexpected_error(error: Exception) -> ResponseReturnValue:
        app.logger.exception("unexpected error")
        if request.path.startswith(security.API_PREFIX):
            return api_v1.problem(500, "Something unexpected went wrong on the server. Quote the request_id.")
        return jsonify(error=security.message_for_user("Something unexpected went wrong on the server.")), 500

    @app.after_request
    def api_request_id(response: Response) -> Response:
        """Every API answer carries X-Request-ID, also errors raised before a view ran."""
        return api_v1.add_request_id(response) if request.path.startswith(security.API_PREFIX) else response

    background.start()
    return app
