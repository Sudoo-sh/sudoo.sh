"""The API: /api/v1, used by the panel's web app and by other systems alike.

Who may call (security.py): the web app with its session cookie (and X-CSRF-Token for changes), or another system
with an API token and a signature on every request (api_tokens.py, docs/PANEL_API.md). Either way the caller is an
role (roles.py), and the same permission checks apply.

    access_rules.py  production access rules (beta) and the links that open an instance through a pass
    alerts.py      the alerts of the bell, test alerts
    session.py     sign in (password, then a second factor when needed) and out, own password, contact
    setup_wizard.py  the first-run setup wizard's server check, draft and test e-mail
    account.py     own account and second factors
    instances.py   instances, jobs, versions
    logs.py        an instance's logs: which there are, downloading one (log_stream.py follows one live)
    connect.py     Connect as: open an instance's Odoo signed in as one of its users
    imports.py     Import database: upload an Odoo dump in pieces and put it into an instance
    profiler.py    the code profiler of an instance (the Tools tab): record, stop, read, download, delete
    projects.py    projects (one GitHub repository each), their branches and stages, access and locked branches
    project_resources.py  a project's and a branch's resources: workers, timeouts, memory per stage; storage
    promotion.py   the pre-promotion checklist, comparing branches, the scheduled staging refresh, PR previews
    project_status.py  a project's Status tab (uptime, incidents) and its public status page
    repo_guard.py  a project's repository guard: CI checks through a pull request, their results, branch rules
    notifications.py   a project's notification channels (Slack, Discord, Telegram, Teams, webhooks, e-mail)
    upgrades.py    database upgrades of instances to a newer Odoo version: pre-check, test, production, rollback
    clients.py     the client portal: a project's clients, access requests, emergency access (staff), and the
                   client's own side (its projects, the switch, answers, its activity log)
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
    maintenance.py the maintenance Sudoo.sh plans on the panel, and choosing its time (staff and clients)
    server.py      the server's resource use, the panel's address and plugins
    kubernetes.py  the Kubernetes engine (beta): the cluster's nodes, the command that adds one
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
    access_rules,
    account,
    alerts,
    backups,
    capacity_queue,
    caught_mail,
    clients,
    connect,
    docs,
    domains,
    github,
    help_panel,
    imports,
    instances,
    invitations,
    kubernetes,
    licence,
    logs,
    mail_templates,
    maintenance,
    notifications,
    plan,
    plugins,
    premium,
    profiler,
    project_resources,
    project_status,
    projects,
    promotion,
    repo_guard,
    server,
    session,
    settings,
    setup_wizard,
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
        setup_wizard,
        account,
        instances,
        logs,
        connect,
        access_rules,
        imports,
        profiler,
        projects,
        project_resources,
        promotion,
        project_status,
        repo_guard,
        notifications,
        domains,
        caught_mail,
        settings,
        licence,
        mail_templates,
        maintenance,
        alerts,
        server,
        kubernetes,
        users,
        invitations,
        tokens,
        capacity_queue,
        backups,
        plan,
        github,
        docs,
        help_panel,
        plugins,
        shell,
        teams,
        clients,
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
