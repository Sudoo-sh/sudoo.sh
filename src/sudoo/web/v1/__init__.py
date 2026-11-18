"""The API: /api/v1, used by the panel's web app and by other systems alike.

Who may call (security.py): the web app with its session cookie (and X-CSRF-Token for changes), or another system
with an API token and a signature on every request (api_tokens.py, docs/PANEL_API.md). Either way the caller is an
role (roles.py), and the same permission checks apply.

    alerts.py      the alerts of the bell, test alerts
    session.py     sign in (password, then a second factor when needed) and out, own password, contact
    account.py     own account and second factors
    instances.py   instances, jobs, versions
    domains.py     instances' custom domains
    caught_mail.py the e-mails of staging and development instances, caught by the mail catcher
    help_panel.py  what the help panel needs: version, documentation and support addresses
    settings.py    the panel's settings, changed while it runs
    licence.py     the Sudoo.sh Licence Agreement: its text, acceptances
    mail_templates.py  the texts of the e-mails the panel sends
    server.py      the server's resource use, the panel's address and plugins
    users.py       roles, accounts and the audit log
    invitations.py invitations of new people by e-mail, and accepting one
    tokens.py      API tokens
    backups.py     backups
    offsite.py     off-site copies of backups: where they go, the signing key, checking and restoring copies
    github.py      deploy keys and repository checks
    docs.py        the documentation, for the web app's Help pages
    plugins.py     installed plugins and the marketplace (catalogue, purchases, linking)
    shell.py       the web shell: tickets, open and past shells, recordings
    events.py      the live event stream (Server-Sent Events), served on the asyncio loop
    shell_socket.py  the web shell's WebSocket, served on the same loop
Answers follow the conventions in responses.py.
"""

from __future__ import annotations

from flask import Blueprint, Flask

from sudoo.config import live
from sudoo.core import events as bus
from sudoo.core import jobs

from . import (
    account,
    alerts,
    backups,
    caught_mail,
    docs,
    domains,
    github,
    help_panel,
    instances,
    invitations,
    licence,
    mail_templates,
    offsite,
    plugins,
    server,
    session,
    settings,
    shell,
    tokens,
    users,
)
from . import events as stream
from .resources import instance_resource, job_resource
from .responses import PREFIX, add_request_id, problem

__all__ = ["PREFIX", "add_request_id", "problem", "register", "stream"]


def register(app: Flask) -> None:
    """Add the API to the Flask app, and tell the live events how to show instances and jobs."""
    api = Blueprint("api_v1", __name__, url_prefix=PREFIX)
    for module in (
        session,
        account,
        instances,
        domains,
        caught_mail,
        settings,
        licence,
        mail_templates,
        alerts,
        server,
        users,
        invitations,
        tokens,
        backups,
        offsite,
        github,
        docs,
        help_panel,
        plugins,
        shell,
    ):
        api.register_blueprint(module.blueprint)
    app.register_blueprint(api)
    bus.present("instance", instance_resource)
    bus.present("job", lambda job_id: job_resource(jobs.JOBS[job_id]))
    bus.present("domains", domains.domains_resource)
    live.on_change(settings.publish_changes)
