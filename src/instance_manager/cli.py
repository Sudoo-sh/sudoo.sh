"""The `instance-manager` command, run inside the panel container by the server administrator:

docker exec oim-panel instance-manager init-accounts           create admin and sync, print their passwords
docker exec oim-panel instance-manager create-user NAME ROLE   ROLE is admin or operator
docker exec oim-panel instance-manager reset-password NAME     print a new password
docker exec oim-panel instance-manager list-users
docker exec oim-panel instance-manager urls                    current public addresses
docker exec oim-panel instance-manager config                  effective settings and installed plugins
instance-manager serve                                         start the web panel (the container does this)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from instance_manager import accounts, exposure, plugins
from instance_manager.config import settings
from instance_manager.core import audit
from instance_manager.instances import store


def init_accounts(_: argparse.Namespace) -> None:
    created = accounts.create_defaults()
    for name, password in created.items():
        print(f"{name:<6} ({accounts.ROLES[dict(accounts.DEFAULT_ACCOUNTS)[name]]}): {password}")
    if not created:
        print("Accounts already exist. Use reset-password to set a new password.")
    audit.record("command line", None, "default accounts created", detail=",".join(created) or "none")


def create_user(args: argparse.Namespace) -> None:
    password = accounts.create(args.name.lower(), args.role)
    audit.record("command line", None, "account created", detail=args.name)
    print(f"Account {args.name.lower()} created. Password (shown once): {password}")


def reset_password(args: argparse.Namespace) -> None:
    if not accounts.get(args.name):
        sys.exit(f"No account called {args.name!r}.")
    password = accounts.generate_password()
    accounts.set_password(args.name, password)
    audit.record("command line", None, "password reset", detail=args.name)
    print(f"New password for {args.name}: {password}\nAll existing sessions of this account were signed out.")


def list_users(_: argparse.Namespace) -> None:
    for name, user in sorted(accounts.all_users().items()):
        last = user.get("last_login")
        when = datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M") if last else "never"
        print(f"{name:<16} {accounts.ROLES[user['role']]:<16} last sign-in: {when}")


def urls(_: argparse.Namespace) -> None:
    print(f"Panel: {exposure.address(exposure.PANEL) or 'not ready yet'}")
    for name in store.names():
        print(f"{name}: {exposure.address(name) or 'not ready yet'}")


def show_config(_: argparse.Namespace) -> None:
    """Every setting with its effective value (secrets hidden), then the installed plugins."""
    hidden = {"odoo_admin_password"}
    for key, value in settings.model_dump().items():
        print(f"{key.upper():<32} {'(set)' if key in hidden and value else value}")
    print(f"\nExposure plugins:   {', '.join(plugins.available(plugins.EXPOSURE_GROUP))}")
    print(f"Add-on sources:     {', '.join(plugins.available(plugins.ADDON_SOURCE_GROUP))}, none")


def serve(_: argparse.Namespace) -> None:
    """Start gunicorn: one process (jobs, monitor and gateway share its memory), listening where the exposure
    plugin needs the panel (127.0.0.1 behind a tunnel, every interface when visitors connect directly)."""
    bind = f"{exposure.provider().listen_host}:{settings.panel_port}"
    os.execvp("gunicorn", ["gunicorn", "--workers", "1", "--threads", "32", "--timeout", "120",
                           "--bind", bind, "instance_manager.wsgi:app"])  # fmt: skip


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="instance-manager", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-accounts", help="create the admin and sync accounts").set_defaults(run=init_accounts)
    create = commands.add_parser("create-user", help="create an account and print its password")
    create.add_argument("name")
    create.add_argument("role", choices=sorted(accounts.ROLES))
    create.set_defaults(run=create_user)
    reset = commands.add_parser("reset-password", help="set and print a new password")
    reset.add_argument("name")
    reset.set_defaults(run=reset_password)
    commands.add_parser("list-users", help="list accounts").set_defaults(run=list_users)
    commands.add_parser("urls", help="print the current public addresses").set_defaults(run=urls)
    commands.add_parser("config", help="print the effective settings and plugins").set_defaults(run=show_config)
    commands.add_parser("serve", help="start the web panel").set_defaults(run=serve)
    args = parser.parse_args(argv)
    try:
        args.run(args)
    except ValueError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
