"""The backend's web side: the API that the web app (web/, a separate service) and other systems use.

security.py     who is calling (session or API token), permissions, rate limits, headers
signin.py       signing in: password, second factor, restricted sessions
validation.py   checks every value received from a browser or API client
docs.py         the documentation (README.md, docs/): rendered for the web app's Help pages, and its images
v1/             the API (/api/v1) and the live event stream
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from http import HTTPStatus
from typing import cast

from flask import Flask, Response, jsonify, request
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import HTTPException

from sudoo import accounts, background, exposure, link_deadline, triggers
from sudoo.config import settings
from sudoo.core import event_bridge, resilience, shared, slots

from . import admission, docs, security, v1


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


def _https() -> bool:
    return bool(exposure.provider().https)


def health() -> ResponseReturnValue:
    """GET /healthz, for the container's health check and the web app: 200 "ok", 200 "degraded" while something
    is wrong that the panel works around (a plugin, a setting, a background job: GET /api/v1/health says what),
    503 when the panel's own storage cannot be read, so it can serve nothing useful."""
    try:
        from sudoo.storage import storage

        storage().get("plugin_failures", "health-check")
    except Exception:  # noqa: BLE001 - that is the answer
        return Response("unavailable", status=503, mimetype="text/plain")
    return Response("degraded" if resilience.problems() else "ok", mimetype="text/plain")


def create_app() -> Flask:
    """Build the panel and start the background work (monitor, gateway, connectors, scheduled jobs).

    It always returns a panel that answers /healthz, the sign-in and the API: a step that fails (a plugin, a
    dependency that is down, a setting with a wrong value) is listed for the administrators instead
    (core/resilience.py), and background work that fails is started again."""
    # a new ODOO_ROOT: the panel's own folder holds its keys and data, readable by root only
    resilience.attempt("panel folder", settings.manager_dir.mkdir, 0o700, True, True)
    app = Flask(__name__, static_folder=None)
    app.config.update(
        # a key for this run when the kept one cannot be read or written: sign-ins then last until a restart
        SECRET_KEY=resilience.attempt("session key", accounts.session_secret, default=secrets.token_hex(64)),
        SESSION_COOKIE_NAME="sudoo_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        # https only, when the panel is reached over https (also when that cannot be told: never sent in clear)
        SESSION_COOKIE_SECURE=resilience.attempt("exposure", _https, default=True),
        # the cookie's own lifetime; sessions end sooner, as SESSION_MAX_HOURS says now (security.user_of_session)
        PERMANENT_SESSION_LIFETIME=timedelta(days=31),
        MAX_CONTENT_LENGTH=64 * 1024,  # every request is a small JSON object
        SEND_FILE_MAX_AGE_DEFAULT=0,
    )
    if warning := default_password_warning():
        app.logger.warning(warning)
    app.json.sort_keys = False  # type: ignore[attr-defined]  # answers keep the order they were built in: no sort per answer
    admission.install(app)  # first: a request waits for its turn before anything is done for it
    security.install(app)
    from sudoo.premium import servers

    resilience.attempt("several servers", servers.install, app)  # requests about another server's instances
    app.register_blueprint(docs.blueprint)
    v1.register(app)
    app.add_url_rule("/healthz", "health", health)
    for webhook in triggers.blueprints():  # endpoints of the enabled update triggers, under /hooks/
        resilience.attempt("update trigger webhooks", app.register_blueprint, webhook)

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> ResponseReturnValue:
        # Flask hands an HTTPException without a code straight back, so `code` is set here
        code = cast(int, error.code)
        if request.path.startswith(security.API_PREFIX):
            return v1.problem(code, error.description or HTTPStatus(code).phrase)
        return jsonify(error=error.description), code  # the documentation's images, /healthz

    @app.errorhandler(accounts.SessionEnded)
    def session_ended(error: accounts.SessionEnded) -> ResponseReturnValue:
        """The session asking was ended meanwhile (an administrator, a reset, a sign-out everywhere): it stays
        ended, and its cookie is cleared."""
        security.end_current_session()
        if request.path.startswith(security.API_PREFIX):
            return v1.problem(401, str(error))
        return jsonify(error=str(error)), 401

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
        """Anything a view (or a plugin it called) raised: a clean answer with the request's id, which the
        server's log carries next to the error; the worker goes on serving."""
        from .v1.responses import request_id

        app.logger.exception("unexpected error (request %s)", request_id())
        if request.path.startswith(security.API_PREFIX):
            return v1.problem(500, "Something unexpected went wrong on the server. Quote the request_id.")
        return jsonify(error="Something unexpected went wrong on the server.", request_id=request_id()), 500

    @app.teardown_request
    def give_back_sign_in_tries(error: BaseException | None) -> None:  # noqa: ARG001 - every request, any outcome
        accounts.THROTTLE.end_request()

    @app.after_request
    def api_request_id(response: Response) -> Response:
        """Every API answer carries X-Request-ID, also errors raised before a view ran."""
        return v1.add_request_id(response) if request.path.startswith(security.API_PREFIX) else response

    if resilience.attempt("several servers", servers.is_agent, default=False):  # a server's agent: no web page,
        resilience.attempt("background work", background.start)  # no event stream; the panel passes requests on
        return app
    resilience.attempt("link deadline", link_deadline.first_run)  # recorded once, the first time the panel runs
    resilience.attempt("background work of every process", background.start_every_process)

    def lead() -> None:
        """In one worker process at a time (core/shared.py leadership): the background work, the gateway, the
        live events (the others hand theirs over, core/event_bridge.py) and the support channel."""
        event_bridge.run_as_leader()
        resilience.attempt("background work", background.start)
        resilience.attempt("live events", v1.stream.start, app)
        resilience.attempt("support channel", _start_support_channel, app)

    resilience.attempt("leadership", shared.when_leader, lead)
    return app


def _start_support_channel(app: Flask) -> None:
    """Sudoo.sh support's requests, while a grant is active: with the premium support package only."""
    from sudoo.premium import stubs

    found = stubs.support()
    if found is not None:
        found.start_channel(app)
