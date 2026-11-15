"""Build a realistic, throwaway panel data folder for the load tests (perf/README.md).

    uv run python perf/seed.py <run folder> [--instances 40] [--audit 200000] [--backups 3]

Writes <run folder>/odoo (the panel's ODOO_ROOT: instances, backups, accounts, tokens, a long audit log),
<run folder>/panel.env (the settings the panel is started with) and <run folder>/secrets.json (what the load tests
sign in with). No container is created: the instances are folders whose Odoo is not running, except the first one,
whose ODOO_PORT points at the stub Odoo (perf/stub_odoo.py) so that the gateway can be measured.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

PORTS = {
    "PANEL_PORT": 19100,  # Caddy (the web service)
    "EVENTS_PORT": 19101,
    "API_PORT": 19102,
    "GATEWAY_PORT_START": 19200,
    "STUB_PORT": 19300,  # the stub Odoo behind the first instance's gateway
}
WEBHOOK_SECRET = "perf-webhook-secret-0123456789abcdef"


def panel_env(root: Path) -> dict[str, str]:
    return {
        "ODOO_ROOT": str(root),
        "EXPOSURE": "host-ports",
        "PUBLIC_HOST": "127.0.0.1",
        "PANEL_PORT": str(PORTS["PANEL_PORT"]),
        "EVENTS_PORT": str(PORTS["EVENTS_PORT"]),
        "API_PORT": str(PORTS["API_PORT"]),
        "GATEWAY_PORT_START": str(PORTS["GATEWAY_PORT_START"]),
        "ODOO_PORT_START": "19400",
        "CONNECTOR_PREFIX": "pperf-connector-",
        "REQUIRE_SECOND_FACTOR": "none",
        "MAILER": "none",
        "UPDATE_TRIGGERS": "github-webhook",
        "GITHUB_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "ODOO_VERSIONS": "17.0,18.0",
        "DAILY_BACKUPS": "false",
        "IDLE_HOURS": "0",
        "DRIFT_ACTION": "warn",
        "MARKETPLACE": "false",
        # the load tests measure capacity; the limits are measured on their own (perf/README.md)
        "RATE_LIMIT_READS_PER_MINUTE": "0",
        "RATE_LIMIT_CHANGES_PER_MINUTE": "0",
        "DOCS_DIR": str(root.parent / "documentation"),  # README.md and docs/, as the image ships them
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--instances", type=int, default=40)
    parser.add_argument("--audit", type=int, default=200_000)
    parser.add_argument("--backups", type=int, default=3)
    args = parser.parse_args()
    run: Path = args.run.resolve()
    root = run / "odoo"
    if root.exists():
        sys.exit(f"{root} exists already: use a new run folder")
    root.mkdir(parents=True)
    (root / "manager").mkdir(mode=0o700)  # the vault key is made there when the panel's modules load
    repository = Path(__file__).resolve().parent.parent
    (run / "documentation").mkdir()
    shutil.copy(repository / "README.md", run / "documentation")
    shutil.copytree(repository / "docs", run / "documentation" / "docs")
    env = panel_env(root)
    os.environ.update(env)

    # imported only now: the settings are read from the environment when the panel's modules load
    from sudoo import accounts, api_tokens
    from sudoo.core import vault
    from sudoo.core.state import STATE
    from sudoo.instances import store
    from sudoo.storage import storage

    password = "Perf-" + secrets.token_urlsafe(12)
    accounts.create("perfadmin", "super_admin", password, must_change=False, email="perf@example.com")
    for number in range(20):
        accounts.create(f"user{number:02d}", "developer", must_change=False, email=f"user{number}@example.com")
    token, signing_secret = api_tokens.create("perf read", "read", "perf")
    write_token, write_signing_secret = api_tokens.create("perf write", "write", "perf")
    for number in range(8):
        api_tokens.create(f"other token {number}", "read", "perf")

    names = []
    template = run / "repo-template"
    subprocess.run(["git", "init", "-q", "-b", "main", str(template)], check=True)
    for module in ("sale_extra", "stock_extra", "account_extra"):
        (template / module).mkdir()
        (template / module / "__manifest__.py").write_text(f"{{'name': '{module}', 'version': '1.0'}}\n")
    git = ["git", "-C", str(template), "-c", "user.name=perf", "-c", "user.email=perf@example.com"]
    subprocess.run([*git, "add", "-A"], check=True)
    for number in range(12):
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", f"Change number {number}"], check=True)
    subprocess.run([*git, "remote", "add", "origin", "https://github.com/example/odoo-addons.git"], check=True)

    backups_dir = root / "backups"
    backups_dir.mkdir()
    now = int(time.time())
    for index in range(args.instances):
        name = f"pperf-{index:02d}"
        names.append(name)
        folder = store.folder(name)
        (folder / "config").mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", str(template), str(folder / "addons")], check=True)
        subprocess.run(
            ["git", "-C", str(folder / "addons"), "remote", "set-url", "origin",
             f"https://github.com/example/odoo-addons-{index}.git"],
            check=True,
        )  # fmt: skip
        (folder / "docker-compose.yml").write_text("# a folder for the load tests: no container\nservices: {}\n")
        odoo_port = PORTS["STUB_PORT"] if index == 0 else 19400 + index
        store.write_env(
            name,
            {
                "ODOO_VERSION": "18.0" if index % 2 else "17.0",
                "EDITION": "community",
                "GATEWAY_PORT": PORTS["GATEWAY_PORT_START"] + index,
                "ODOO_PORT": odoo_port,
                "DB_NAME": name.replace("-", "_"),
                "DB_PASSWORD": secrets.token_urlsafe(18),
                "GIT_BRANCH": "main",
                "ODOO_ADMIN_PASSWORD": "admin",
                "MASTER_PASSWORD": vault.encrypt(secrets.token_urlsafe(16), store.master_password_label(name)),
                "AUTO_UPDATE": "false",
                "AUTO_UPGRADE": "false",
                "AUTO_PAUSE": "false",
                "DELETE_AFTER_DAYS": 0,
                "CREATED_AT": now - index * 3600,
                "CREATED_BY": "perfadmin",
                "ENTERPRISE_FOLDER": "",
            },
        )
        STATE.update(name, last_visit=now - index * 60, paused=index % 3 == 1, switched_off=False)
        for number in range(args.backups):
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now - number * 86400 - index))
            kind = ("manual", "daily", "deletion")[number % 3]
            path = backups_dir / f"{name}__{name.replace('-', '_')}__{stamp}__{kind}.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"version": "18.0" if index % 2 else "17.0"}))
                archive.writestr("dump.sql", "-- perf\n")

    events = ("signed in", "Restart", "Get latest code", "instance credentials viewed", "Back up", "signed out")
    started = time.perf_counter()
    db = storage()._db()  # type: ignore[attr-defined] - one transaction, not 200,000
    db.execute("BEGIN")
    db.executemany(
        "INSERT INTO audit (ts, user, ip, event, instance, ok, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            (now - (args.audit - i) * 5, f"user{i % 20:02d}", f"10.0.{i % 250}.{i % 200}", events[i % len(events)],
             names[i % len(names)] if i % 3 else None, int(i % 17 != 0), "from the load-test seed" if i % 5 else None)
            for i in range(args.audit)
        ),
    )  # fmt: skip
    db.execute("COMMIT")
    print(f"audit log: {args.audit} entries in {time.perf_counter() - started:.1f} s")

    secrets_file = run / "secrets.json"
    secrets_file.write_text(
        json.dumps(
            {
                "username": "perfadmin",
                "password": password,
                "token": token,
                "signing_secret": signing_secret,
                "write_token": write_token,
                "write_signing_secret": write_signing_secret,
                "webhook_secret": WEBHOOK_SECRET,
                "instances": names,
                "gateway_instance": names[0],
                **{key.lower(): value for key, value in PORTS.items()},
            },
            indent=1,
        )
    )
    os.chmod(secrets_file, 0o600)
    (run / "panel.env").write_text("".join(f"{key}={value}\n" for key, value in env.items()))
    print(f"seeded {len(names)} instances in {root}; settings in {run / 'panel.env'}")


if __name__ == "__main__":
    main()
