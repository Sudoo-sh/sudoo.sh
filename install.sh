#!/usr/bin/env bash
# Odoo Instance Manager - install or update. Run on the server as root: sudo ./install.sh
# Created by Yousef M. Y. Al Sabbah.
#
# Copies the panel to /opt/odoo/manager, writes its settings (keeping existing values), builds and starts it,
# creates the admin and sync accounts on the first run and prints the panel's public address.
set -euo pipefail

ROOT=/opt/odoo
KIT="$(cd "$(dirname "$0")" && pwd)"
SRC="$KIT/manager"
DEST="$ROOT/manager"

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo ./install.sh"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "The Docker Compose plugin is missing (run ./check-server.sh)."; exit 1; }

umask 077
mkdir -p "$ROOT/instances" "$ROOT/enterprise" "$DEST/keys/deploy"
chmod 700 "$DEST" "$DEST/keys" "$DEST/keys/deploy"

# the panel's code: replace it completely
rm -rf "$DEST/instance_manager" "$DEST"/*.py "$DEST/documentation"
cp -r "$SRC/instance_manager" "$SRC/wsgi.py" "$SRC/manage.py" "$SRC/Dockerfile" "$SRC/docker-compose.yml" "$SRC/.env.example" "$DEST/"
# the documentation shown under Documentation in the panel
mkdir -p "$DEST/documentation"
cp -r "$KIT/README.md" "$KIT/docs" "$DEST/documentation/"

# settings: the template's keys, with the values already set on this server
python3 - "$SRC/.env.example" "$DEST/.env" <<'PY'
import sys
from pathlib import Path
template, target = Path(sys.argv[1]), Path(sys.argv[2])
current = {}
if target.exists():
    for line in target.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            current[key.strip()] = value
lines = []
for line in template.read_text().splitlines():
    key, sep, value = line.partition("=")
    lines.append(f"{key}={current.get(key, value)}" if sep and not key.startswith("#") else line)
target.write_text("\n".join(lines) + "\n")
target.chmod(0o600)
PY

cd "$DEST"
docker compose up -d --build --remove-orphans

echo "Waiting for the panel and its public address..."
for _ in $(seq 1 60); do
  docker exec oim-panel python3 manage.py urls 2>/dev/null | grep -q "^Panel: https://" && break
  sleep 2
done

echo
echo "Odoo Instance Manager, created by Yousef M. Y. Al Sabbah"
docker exec oim-panel python3 manage.py urls
echo
if [ -f "$DEST/users.json" ]; then
  echo "Accounts already exist and were not changed."
  echo "Forgotten password:  docker exec oim-panel python3 manage.py reset-password admin"
else
  echo "Accounts (shown only once, store them in a password manager):"
  docker exec oim-panel python3 manage.py init-accounts
fi
