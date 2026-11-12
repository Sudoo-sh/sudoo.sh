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

from sudoo import accounts, background, exposure, triggers

from . import docs, security, v1


def create_app() -> Flask:
    """Build the panel and start the background work (monitor, gateway, connectors, scheduled jobs)."""
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
    security.install(app)
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

    background.start()
    v1.stream.start(app)
    return app
