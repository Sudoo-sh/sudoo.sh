#!/bin/sh
# Listen where the exposure needs the panel: every interface for host-ports, PROXY_LISTEN for proxy, else 127.0.0.1
# (direct: the gateway forwards the panel's address here; tunnel: the connector does).
set -eu
case "${EXPOSURE:-tunnel}" in
  host-ports) WEB_BIND=0.0.0.0 ;;
  proxy) WEB_BIND=${PROXY_LISTEN:-127.0.0.1} ;;
  *) WEB_BIND=127.0.0.1 ;;
esac
export WEB_BIND
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
