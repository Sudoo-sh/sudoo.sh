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
from datetime import timedelta

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from .. import accounts, background, settings
from . import api_accounts, api_backups, api_github, api_instances, api_session, docs, security, views


def create_app():
    """Build the panel and start the background work (monitor, gateway, connectors, scheduled jobs)."""
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.update(
        SECRET_KEY=accounts.session_secret(),
        SESSION_COOKIE_NAME="oim_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=True,        # the panel is used through its https link
        PERMANENT_SESSION_LIFETIME=timedelta(hours=settings.SESSION_MAX_HOURS),
        MAX_CONTENT_LENGTH=64 * 1024,            # every request is a small JSON object
        SEND_FILE_MAX_AGE_DEFAULT=0,
    )
    security.install(app)
    for module in (views, docs, api_session, api_accounts, api_instances, api_github, api_backups):
        app.register_blueprint(module.blueprint)

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception("unexpected error")
        return jsonify(error=security.message_for_user("Something unexpected went wrong on the server.")), 500

    background.start()
    return app
