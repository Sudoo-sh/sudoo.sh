#!/usr/bin/env bash
# Sudoo.sh - install on a fresh server with one command:
#
#     curl -fsSL https://raw.githubusercontent.com/Yosef-AlSabbah/sudoo.sh/main/get.sh | sudo bash
#
# Installs Docker if it is missing (Docker's own installer), downloads the latest release of Sudoo.sh, and runs its
# install.sh, which prints the panel's address and the setup code for the first account.
# Choose a version with SUDOO_VERSION=v1.0.0, or the newest code with SUDOO_VERSION=main.
# Created by Yousef M. Y. Al Sabbah.
set -euo pipefail

REPO="Yosef-AlSabbah/sudoo.sh"
VERSION="${SUDOO_VERSION:-latest}"
KIT="/opt/sudoo.sh-kit"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Run it as root: curl -fsSL https://raw.githubusercontent.com/$REPO/main/get.sh | sudo bash"
[ "$(uname -s)" = "Linux" ] || fail "Sudoo.sh runs on a Linux server (Ubuntu or Debian recommended)."
command -v curl >/dev/null || fail "curl is needed: apt-get install -y curl"

say "1/4  Docker"
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  echo "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '') is installed."
else
  echo "Installing Docker with Docker's official installer (get.docker.com)..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
docker info >/dev/null 2>&1 || fail "Docker is installed but not running: systemctl start docker"

say "2/4  Checking the server"
memory=$(awk '/MemTotal/ {print int($2/1024/1024 + 0.5)}' /proc/meminfo)
disk=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
echo "Memory: ${memory} GB (2 GB or more; each running Odoo uses about 1 GB)"
echo "Free disk: ${disk} GB (20 GB or more recommended)"
[ "${disk:-0}" -ge 5 ] || fail "Less than 5 GB of free disk: free some space first."

say "3/4  Downloading Sudoo.sh ($VERSION)"
if [ "$VERSION" = "latest" ]; then
  VERSION=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
  [ -n "$VERSION" ] || VERSION=main
fi
case "$VERSION" in
  main) url="https://github.com/$REPO/archive/refs/heads/main.tar.gz" ;;
  *) url="https://github.com/$REPO/archive/refs/tags/$VERSION.tar.gz" ;;
esac
rm -rf "$KIT" && mkdir -p "$KIT"
curl -fsSL "$url" | tar -xz -C "$KIT" --strip-components=1
echo "Version $VERSION in $KIT"

say "4/4  Installing"
cd "$KIT"
./install.sh
