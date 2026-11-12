#!/usr/bin/env bash
# Sudoo.sh - install or update. Run on the server as root: sudo ./install.sh
# Created by Yousef M. Y. Al Sabbah.
#
# Copies the panel to /opt/odoo/manager, writes its settings (keeping existing values), builds and starts the backend
# (sudoo-panel) and the web app (sudoo-web), and prints the panel's public address and, on a new server, the setup
# code for the first account. One-command install on a fresh server: get.sh.
set -euo pipefail

ROOT=/opt/odoo
KIT="$(cd "$(dirname "$0")" && pwd)"
DEST="$ROOT/manager"

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo ./install.sh"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "The Docker Compose plugin is missing (run ./check-server.sh)."; exit 1; }

umask 077
mkdir -p "$ROOT/instances" "$ROOT/enterprise" "$DEST/keys/deploy"
chmod 700 "$DEST" "$DEST/keys" "$DEST/keys/deploy"

# the panel's code and build files: replace them completely (older layouts included)
rm -rf "$DEST/src" "$DEST/sudoo" "$DEST"/*.py "$DEST/docker-compose.yml" "$DEST/documentation"
cp -r "$KIT/src" "$KIT/pyproject.toml" "$KIT/uv.lock" "$KIT/README.md" "$KIT/LICENSE.md" \
      "$KIT/Dockerfile" "$KIT/compose.yaml" "$KIT/.env.example" "$DEST/"
# the web app (built inside its image; local build output and packages are not copied)
rm -rf "$DEST/web" && mkdir -p "$DEST/web"
tar -C "$KIT/web" --exclude=node_modules --exclude=dist -cf - . | tar -C "$DEST/web" -xf -
# the documentation shown under Documentation in the panel
mkdir -p "$DEST/documentation"
cp -r "$KIT/README.md" "$KIT/docs" "$DEST/documentation/"

# settings: the template's keys, with the values already set on this server
python3 - "$KIT/.env.example" "$DEST/.env" <<'PY'
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
docker image prune -f >/dev/null 2>&1 || true  # earlier builds of the images
for _ in $(seq 1 60); do
  docker exec sudoo-panel sudoo.sh urls 2>/dev/null | grep -q "^Panel: http" && break
  sleep 2
done

echo
echo "Sudoo.sh, created by Yousef M. Y. Al Sabbah"
docker exec sudoo-panel sudoo.sh urls
echo
code=$(docker exec sudoo-panel sudoo.sh setup-code 2>/dev/null | sed -n 's/^Setup code: //p')
if [ -n "$code" ]; then
  echo "Open the panel's address above and create your account with this setup code:"
  echo
  echo "    $code"
  echo
  echo "Show it again later with:  docker exec sudoo-panel sudoo.sh setup-code"
else
  echo "Accounts already exist and were not changed."
  echo "Lost password:  docker exec sudoo-panel sudoo.sh reset-password <user name>"
fi
