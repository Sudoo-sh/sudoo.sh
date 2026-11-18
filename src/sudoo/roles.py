"""Roles and what each may do.

Every check in the panel asks for a permission, never for a role (security.require, security.can), so a role is
only a named set of permissions:

    super_admin   Super admin     everything, including the panel's settings, the e-mail texts, where off-site copies
                                  go, and the other super admins
    admin         Administrator   everything except the panel's settings, the e-mail texts and where off-site copies
                                  go (sees plugins, cannot install them); adds and removes instances' custom domains;
                                  restores from and deletes off-site copies; manages teams, project access and
                                  locked branches
    maintainer    Maintainer      creates projects and instances and does everything on them except deleting
                                  (restoring backups too); GitHub access
    developer     Developer       follows instances: gets new code, restarts, starts, adds submodules; reads the
                                  e-mails caught from staging and development instances (deleting and releasing
                                  them is for administrators)
    viewer        Viewer          sees instances, their activity and logs

Shells (webshell/): super admins and administrators open terminals in instances' containers (instances.shell,
instances.database_shell); the SHELL_ROLES setting adds maintainers, or maintainers and developers, for the Odoo
container only (permissions() adds it); viewers never. Only super admins supervise shells (shells.supervise).
SHELL_ENABLED off takes the shell permissions from everyone.

Inside a project, a role's permissions are limited by the level the person has there (teams and grants, locked
branches): projects/access.py. A role is the most anyone can do; a grant never adds to it.

Accounts of earlier versions are renamed once when the panel starts (storage/migrate.py): "admin" becomes a super
admin, "operator" (the sync operator) a developer. API tokens act as an administrator (write scope), a viewer
(read scope), a connected app (instances scope) or a developer token (developer scope).

TOKEN_ROLES are roles only an API token can have, never an account (users cannot be given them, and an account's
stored role is checked against ROLES alone):

    connected_app Connected app   lists, creates, starts, pauses, restarts, backs up, extends and deletes
                                  instances; nothing else (no passwords, logs, restores, backups, GitHub, accounts,
                                  tokens or settings). The API narrows it further: security.CONNECTED_APP_ENDPOINTS
                                  and web/v1/instances.CONNECTED_APP_ACTIONS.
    developer_token Developer (API) lists and reads projects, branches and instances, their logs, jobs and caught
                                  e-mails; starts, pauses, restarts, gets new code, updates submodules; creates
                                  and deletes development instances. Never settings, accounts, teams, access,
                                  stages, production, restores, domains, off-site copies or shells. The API
                                  narrows it: security.DEVELOPER_ENDPOINTS and web/v1/instances.DEVELOPER_ACTIONS.
"""

from __future__ import annotations

from dataclasses import dataclass

from sudoo.config import settings

PERMISSIONS: dict[str, str] = {
    "instances.view": "See instances, their figures and activity",
    "instances.logs": "Read Odoo's log and jobs' technical logs",
    "instances.update": "Get new code, restart, start, add submodules",
    "instances.operate": "Pause, switch off, upgrade, switch branch, back up, change automation",
    "instances.create": "Create instances",
    "projects.manage": "Create and link projects (one GitHub repository each), change their settings and the stages "
    "of their branches",
    "projects.access": "Choose who may see and work in a project and its branches (grants, restricted projects and "
    "branches) and lock or unlock branches",
    "teams.manage": "Create, change and delete teams (named groups of accounts that project access is given to)",
    "instances.restore": "Restore an instance from a backup (replaces its database and files)",
    "instances.delete": "Delete instances",
    "instances.credentials": "See Odoo passwords and ports",
    "instances.resources": "Set an instance's resources: Odoo workers, time and memory limits, container limits",
    "caught_mail.view": "Read the e-mails caught from staging and development instances (the Mail tab)",
    "caught_mail.manage": "Delete caught e-mails and release them (send a copy to an address)",
    "backups.manage": "Download and delete backups; see, check and copy again off-site copies",
    "offsite.configure": "Set up where off-site copies go, and test it",
    "offsite.restore": "Restore an instance from an off-site copy (it is downloaded and verified first)",
    "offsite.delete": "Delete off-site copies",
    "domains.manage": "Add and remove instances' custom domains (seeing them needs instances.view)",
    "github.manage": "Manage deploy keys; see the GitHub App's state",
    "accounts.manage": "Create, change and delete accounts",
    "tokens.manage": "Create and revoke API tokens",
    "audit.view": "Read the audit log",
    "plugins.view": "See installed plugins, the marketplace and purchases",
    "settings.manage": "Change the panel's settings, install and buy plugins, and restart it",
    "mail.manage": "Change the texts of the e-mails the panel sends",
    "instances.shell": "Open a terminal in instances' Odoo containers (odoo shell too)",
    "instances.database_shell": "Open psql in instances' database containers",
    "shells.supervise": "See open and past shells, end them and play their recordings",
}


@dataclass(frozen=True, slots=True)
class Role:
    label: str
    description: str
    permissions: frozenset[str]


_VIEW = frozenset({"instances.view", "instances.logs"})
_DEVELOP = _VIEW | {"instances.update", "caught_mail.view"}
_MAINTAIN = _DEVELOP | {
    "instances.operate",
    "instances.restore",
    "instances.create",
    "instances.credentials",
    "github.manage",
    "projects.manage",
}
_ADMIN = frozenset(PERMISSIONS) - {"settings.manage", "mail.manage", "offsite.configure", "shells.supervise"}

ROLES: dict[str, Role] = {
    "super_admin": Role("Super admin", "Everything, including the panel's settings.", frozenset(PERMISSIONS)),
    "admin": Role("Administrator", "Everything except the panel's settings and e-mail texts.", _ADMIN),
    "maintainer": Role("Maintainer", "Creates instances and runs every action on them except deleting.", _MAINTAIN),
    "developer": Role("Developer", "Gets new code, restarts and starts instances, adds submodules.", _DEVELOP),
    "viewer": Role("Viewer", "Sees instances, their activity and logs.", _VIEW),
}
CONNECTED_APP = "connected_app"
_CONNECTED_APP = frozenset(
    {"instances.view", "instances.create", "instances.update", "instances.operate", "instances.delete"}
)
DEVELOPER_TOKEN = "developer_token"
_DEVELOPER_TOKEN = frozenset(
    {
        "instances.view",
        "instances.logs",
        "instances.update",
        "instances.operate",  # only for pause (web/v1/instances.DEVELOPER_ACTIONS)
        "instances.create",  # only development instances
        "instances.delete",  # only development instances
        "caught_mail.view",
    }
)
TOKEN_ROLES: dict[str, Role] = {
    CONNECTED_APP: Role(
        "Connected app",
        "Lists, creates, starts, pauses, restarts, backs up, extends and deletes instances; nothing else.",
        _CONNECTED_APP,
    ),
    DEVELOPER_TOKEN: Role(
        "Developer (API)",
        "Reads projects, branches, instances, logs, jobs and caught e-mails; starts, pauses, restarts and updates "
        "instances; creates and deletes development instances. No settings, access, production or restores.",
        _DEVELOPER_TOKEN,
    ),
}
LABELS = {name: role.label for name, role in ROLES.items()}
ADMINISTRATORS = frozenset({"super_admin", "admin"})  # "admins" in REQUIRE_SECOND_FACTOR


def normalize(role: str) -> str:
    """A known role, or the role with the fewest permissions."""
    return role if role in ROLES else "viewer"


SHELL_PERMISSIONS = frozenset({"instances.shell", "instances.database_shell"})
SHELL_ROLES = {"admins": (), "maintainers": ("maintainer",), "developers": ("maintainer", "developer")}
"""Who else may open a shell in an Odoo container, by the SHELL_ROLES setting (super admins and administrators
always may; viewers never)."""


def permissions(role: str) -> frozenset[str]:
    if role in TOKEN_ROLES:
        return TOKEN_ROLES[role].permissions
    role = normalize(role)
    found = ROLES[role].permissions
    if not settings.shell_enabled:  # shells switched off: nobody has them (the Shell tab disappears)
        return found - SHELL_PERMISSIONS
    if role in SHELL_ROLES.get(settings.shell_roles, ()):
        return found | {"instances.shell"}
    return found


def label(role: str) -> str:
    """The role's name as people read it (also for a token's role)."""
    return TOKEN_ROLES[role].label if role in TOKEN_ROLES else ROLES[normalize(role)].label
