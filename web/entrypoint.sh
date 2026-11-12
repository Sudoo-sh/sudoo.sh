#!/bin/sh
# Listen where the exposure needs the panel: 127.0.0.1 behind a tunnel connector, every interface for host-ports.
set -eu
if [ "${EXPOSURE:-cloudflare-quick}" = "host-ports" ]; then WEB_BIND=0.0.0.0; else WEB_BIND=127.0.0.1; fi
export WEB_BIND
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
