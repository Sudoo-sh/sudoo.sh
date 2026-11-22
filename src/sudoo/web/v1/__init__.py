"""The API: /api/v1, used by the panel's web app and by other systems alike.

Who may call (security.py): the web app with its session cookie (and X-CSRF-Token for changes), or another system
with an API token and a signature on every request (api_tokens.py, docs/PANEL_API.md). Either way the caller is an
role (roles.py), and the same permission checks apply.

    alerts.py      the alerts of the bell, test alerts
    session.py     sign in (password, then a second factor when needed) and out, own password, contact
    account.py     own account and second factors
    instances.py   instances, jobs, versions
    logs.py        an instance's logs: which there are, downloading one (log_stream.py follows one live)
    connect.py     Connect as: open an instance's Odoo signed in as one of its users
    imports.py     Import database: upload an Odoo dump in pieces and put it into an instance
    projects.py    projects (one GitHub repository each), their branches and stages, access and locked branches
    project_resources.py  a project's and a branch's resources: workers, timeouts, memory per stage; storage
    promotion.py   the pre-promotion checklist, comparing branches, the scheduled staging refresh, PR previews
    upgrades.py    database upgrades of instances to a newer Odoo version: pre-check, test, production, rollback
    teams.py       teams: named groups of accounts that project access is given to (reading, removing; the rest
                   comes with the premium teams package)
    domains.py     instances' custom domains as the core serves them (reading, removing)
    premium.py     premium features: what the plan includes and what is installed, their web files, and 402 stubs
                   for the API of absent packages (each loaded package adds its own blueprint)
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
    github.py      deploy keys and repository checks
    docs.py        the documentation, for the web app's Help pages
    plan.py        the panel's Sudoo.sh plan: entitlements, usage, where to manage it
    plugins.py     installed plugins and the marketplace (catalogue, purchases, linking)
    shell.py       the web shell: tickets, open and past shells, recordings
    events.py      the live event stream (Server-Sent Events), served on the asyncio loop
    shell_socket.py  the web shell's WebSocket, served on the same loop
    log_stream.py  following a log (Server-Sent Events), served on the same loop
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
    connect,
    docs,
    domains,
    github,
    help_panel,
    imports,
    instances,
    invitations,
    licence,
    logs,
    mail_templates,
    plan,
    plugins,
    premium,
    project_resources,
    projects,
    promotion,
    server,
    session,
    settings,
    shell,
    teams,
    tokens,
    upgrades,
    users,
)
from . import events as stream
from .resources import instance_resource, job_resource
from .responses import PREFIX, add_request_id, problem


def _project_event(project_id: str) -> dict[str, object]:
    """A project's live event: its list resource, or {"id", "deleted": true} once it is gone."""
    from sudoo import projects as model

    found = model.get(project_id)
    return projects.project_resource(found) if found else {"id": project_id, "deleted": True}


__all__ = ["PREFIX", "add_request_id", "problem", "register", "stream"]


def register(app: Flask) -> None:
    """Add the API to the Flask app, and tell the live events how to show instances and jobs."""
    api = Blueprint("api_v1", __name__, url_prefix=PREFIX)
    for module in (
        session,
        account,
        instances,
        logs,
        connect,
        imports,
        projects,
        project_resources,
        promotion,
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
        plan,
        github,
        docs,
        help_panel,
        plugins,
        shell,
        teams,
        upgrades,
        premium,
    ):
        api.register_blueprint(module.blueprint)
    premium.register_packages(api, app)
    app.register_blueprint(api)
    app.register_blueprint(premium.files)
    bus.present("instance", instance_resource)
    bus.present("job", lambda job_id: job_resource(jobs.JOBS[job_id]))
    bus.present("domains", domains.domains_resource)
    bus.present("project", _project_event)
    bus.present("build", projects.build_event)
    live.on_change(settings.publish_changes)
