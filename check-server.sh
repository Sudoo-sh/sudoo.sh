#!/usr/bin/env bash
# Odoo Instance Manager - server readiness check. Changes nothing on the server.
# Created by Yousef M. Y. Al Sabbah.
#
# Usage:  sudo ./check-server.sh
set -u

PASS=0; WARN=0; FAIL=0
ok()   { printf '  [ OK ]  %s\n' "$1"; PASS=$((PASS + 1)); }
warn() { printf '  [WARN]  %s\n' "$1"; [ -n "${2:-}" ] && printf '          Fix: %s\n' "$2"; WARN=$((WARN + 1)); }
fail() { printf '  [FAIL]  %s\n' "$1"; [ -n "${2:-}" ] && printf '          Fix: %s\n' "$2"; FAIL=$((FAIL + 1)); }
section() { printf '\n%s\n' "$1"; }

MANAGER_PORT="${MANAGER_PORT:-9000}"

echo "Odoo Instance Manager - server check ($(hostname), $(date '+%Y-%m-%d %H:%M'))"

# ---------------------------------------------------------------- system
section "System"
if [ -r /etc/os-release ]; then . /etc/os-release; ok "Operating system: ${PRETTY_NAME:-unknown}"; else warn "Operating system could not be detected"; fi
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|aarch64|arm64) ok "CPU architecture: $ARCH" ;;
  *) fail "CPU architecture $ARCH is not supported by the official Odoo image" "use an x86_64 or arm64 server" ;;
esac
[ "$(id -u)" -eq 0 ] && ok "Running as root (sudo)" || warn "Not running as root; some checks may be incomplete" "run: sudo ./check-server.sh"

CPUS="$(nproc 2>/dev/null || echo 1)"
[ "$CPUS" -ge 2 ] && ok "CPU cores: $CPUS" || warn "Only $CPUS CPU core; Odoo will be slow" "2 or more cores recommended"

MEM_MB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
if   [ "$MEM_MB" -ge 4000 ]; then ok "Memory: ${MEM_MB} MB (room for about $(( (MEM_MB - 1000) / 1200 )) running instances)"
elif [ "$MEM_MB" -ge 2000 ]; then warn "Memory: ${MEM_MB} MB, enough for about 1 running instance" "4 GB or more recommended"
else fail "Memory: ${MEM_MB} MB is too little for Odoo" "at least 2 GB, 4 GB or more recommended"; fi
SWAP_MB=$(( $(awk '/SwapTotal/ {print $2}' /proc/meminfo) / 1024 ))
[ "$SWAP_MB" -gt 0 ] && ok "Swap: ${SWAP_MB} MB" || warn "No swap configured; the server may kill Odoo when memory runs out" "add a swap file (2 to 4 GB)"

DISK_PATH=/opt; [ -d /opt ] || DISK_PATH=/
DISK_GB=$(df -BG --output=avail "$DISK_PATH" | tail -1 | tr -dc '0-9')
if   [ "$DISK_GB" -ge 30 ]; then ok "Free disk space: ${DISK_GB} GB"
elif [ "$DISK_GB" -ge 10 ]; then warn "Free disk space: ${DISK_GB} GB (each instance needs a few GB)" "30 GB or more recommended"
else fail "Free disk space: ${DISK_GB} GB is too little" "free up space or enlarge the disk"; fi

if command -v timedatectl >/dev/null 2>&1; then
  [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ] && ok "System clock is synchronised" \
    || warn "System clock is not synchronised (can break GitHub and HTTPS)" "sudo timedatectl set-ntp true"
fi

# ---------------------------------------------------------------- docker
section "Docker"
if command -v docker >/dev/null 2>&1; then
  ok "Docker is installed: $(docker --version 2>/dev/null)"
  if docker info >/dev/null 2>&1; then
    ok "Docker service is running"
    DOCKER_MAJOR=$(docker version --format '{{.Server.Version}}' 2>/dev/null | cut -d. -f1)
    [ "${DOCKER_MAJOR:-0}" -ge 20 ] && ok "Docker engine version $(docker version --format '{{.Server.Version}}')" \
      || warn "Docker engine is old (version ${DOCKER_MAJOR:-unknown})" "update Docker to version 24 or newer"
    DOCKER_ROOT=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)
    [ -n "$DOCKER_ROOT" ] && ok "Docker data folder: $DOCKER_ROOT ($(df -h --output=avail "$DOCKER_ROOT" | tail -1 | tr -d ' ') free)"
    RUNNING=$(docker ps -q | wc -l)
    ok "Containers currently running: $RUNNING"
    if [ "$RUNNING" -gt 0 ]; then docker ps --format '          {{.Names}}  ({{.Image}})  {{.Ports}}'; fi
  else
    if [ "$(id -u)" -ne 0 ]; then fail "Cannot talk to Docker as this user" "run this check with sudo"
    else fail "Docker is installed but not running" "sudo systemctl enable --now docker"; fi
  fi
  if docker compose version >/dev/null 2>&1; then ok "Docker Compose plugin: $(docker compose version --short 2>/dev/null)"
  else
    # Ubuntu's own docker.io package pairs with docker-compose-v2; Docker's repository uses docker-compose-plugin
    if dpkg -s docker.io >/dev/null 2>&1; then COMPOSE_PKG=docker-compose-v2; else COMPOSE_PKG=docker-compose-plugin; fi
    fail "Docker Compose plugin is missing" "apt update && apt install -y $COMPOSE_PKG"
  fi
  if command -v systemctl >/dev/null 2>&1; then
    [ "$(systemctl is-enabled docker 2>/dev/null)" = "enabled" ] && ok "Docker starts automatically after a reboot" \
      || warn "Docker does not start automatically after a reboot" "sudo systemctl enable docker"
  fi
else
  fail "Docker is not installed" "curl -fsSL https://get.docker.com | sudo sh   (official Docker install script)"
fi

# ---------------------------------------------------------------- ports
# Nothing has to be reachable from outside: the panel and the instances use 127.0.0.1 only and are published
# through outgoing Cloudflare connections. Only the panel's own port must be free.
section "Ports"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx oim-panel; then
  ok "Odoo Instance Manager is already running (port $MANAGER_PORT)"
elif ss -ltnH "( sport = :$MANAGER_PORT )" 2>/dev/null | grep -q .; then
  fail "Port $MANAGER_PORT (panel) is used by another program" "stop that program"
else
  ok "Port $MANAGER_PORT (panel) is free"
fi
ok "No incoming ports need to be opened (public access uses outgoing Cloudflare connections)"

# ---------------------------------------------------------------- internet access
section "Internet access"
check_url() {
  if curl -fsS -o /dev/null --max-time 10 "$2" 2>/dev/null; then ok "$1 is reachable"
  else fail "$1 cannot be reached ($2)" "check the server's internet access, DNS and proxy"; fi
}
if command -v curl >/dev/null 2>&1; then
  check_url "GitHub" "https://github.com"
  check_url "Docker Hub" "https://hub.docker.com/v2/repositories/library/odoo/tags/18.0"
  check_url "Cloudflare" "https://www.cloudflare.com/cdn-cgi/trace"
  if timeout 10 bash -c 'exec 3<>/dev/tcp/github.com/22' 2>/dev/null; then ok "GitHub SSH (port 22) is reachable (needed for private repositories)"
  else warn "GitHub SSH (port 22) is blocked" "allow outgoing port 22, or use HTTPS addresses for public repositories"; fi
else
  warn "curl is missing; internet access was not checked" "sudo apt install curl"
fi

# ---------------------------------------------------------------- existing installation
section "Odoo Instance Manager"
if [ -d /opt/odoo/manager ]; then
  ok "Installed in /opt/odoo ($(ls /opt/odoo/instances 2>/dev/null | wc -l) instances)"
else
  ok "Not installed yet; run: sudo ./install.sh"
fi

# ---------------------------------------------------------------- summary
printf '\nResult: %s passed, %s warnings, %s problems\n' "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Fix the problems marked [FAIL] before installing."
  exit 1
fi
echo "The server is ready. Install with: sudo ./install.sh"
