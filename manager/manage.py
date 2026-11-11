"""Command line for the server administrator, run inside the panel container:

    docker exec oim-panel python3 manage.py init-accounts           create admin and sync, print passwords
    docker exec oim-panel python3 manage.py create-user NAME ROLE   ROLE is admin or operator
    docker exec oim-panel python3 manage.py reset-password NAME     print a new password
    docker exec oim-panel python3 manage.py list-users
    docker exec oim-panel python3 manage.py urls                    current public addresses
"""
import sys
from datetime import datetime

from instance_manager import accounts, audit, instances, tunnels


def init_accounts():
    created = accounts.create_defaults()
    for name, password in created.items():
        print(f"{name:<6} ({accounts.ROLES[accounts.get(name)['role']]}): {password}")
    if not created:
        print("Accounts already exist. Use reset-password to set a new password.")
    audit.record("command line", None, "default accounts created", detail=",".join(created) or "none")


def create_user(name, role):
    password = accounts.create(name.lower(), role)
    audit.record("command line", None, "account created", detail=name)
    print(f"Account {name.lower()} created. Password (shown once): {password}")


def reset_password(name):
    if not accounts.get(name):
        sys.exit(f"No account called {name!r}.")
    password = accounts.generate_password()
    accounts.set_password(name, password)
    audit.record("command line", None, "password reset", detail=name)
    print(f"New password for {name}: {password}\nAll existing sessions of this account were signed out.")


def list_users():
    for name, user in sorted(accounts.all_users().items()):
        last = user.get("last_login")
        when = datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M") if last else "never"
        print(f"{name:<16} {accounts.ROLES[user['role']]:<16} last sign-in: {when}")


def urls():
    print(f"Panel: {tunnels.address(tunnels.PANEL) or 'not ready yet (connector is starting)'}")
    for name in instances.names():
        print(f"{name}: {tunnels.address(name) or 'not ready yet'}")


COMMANDS = {"init-accounts": init_accounts, "create-user": create_user, "reset-password": reset_password,
            "list-users": list_users, "urls": urls}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    try:
        COMMANDS[sys.argv[1]](*sys.argv[2:])
    except (TypeError, ValueError) as error:
        sys.exit(f"{error}\n{__doc__}")
