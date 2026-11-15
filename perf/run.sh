#!/usr/bin/env bash
# Run the whole load test against one copy of the code and write the results into a run folder (perf/README.md).
#
#   perf/run.sh <run folder> [source folder]
#
# <source folder> is the src/ folder of the code to measure (default: this repository's), so the same scripts can
# measure an older version: `git archive <ref> src | tar -x -C /tmp/old` then `perf/run.sh /tmp/run-old /tmp/old/src`.
# The web service is the image in WEB_IMAGE (default pperf-web:local, built from web/ by this script when missing).
#
# ONLY=panel,gateway,events,web,cold runs some of the parts (default: all of them). "web" (Lighthouse) needs
# Node.js and Chrome or Chromium (CHROME_PATH, or Playwright's).
#
# Everything it starts is named pperf-* or runs as a child process, and is stopped at the end.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
run="$(mkdir -p "$1" && cd "$1" && pwd)"
source_dir="$(cd "${2:-$repo/src}" && pwd)"
if [ ! -f "$source_dir/sudoo/__init__.py" ]; then
  # without this check Python would quietly import the installed copy instead, and measure the wrong code
  echo "$source_dir does not contain the sudoo package" >&2
  exit 1
fi
web_image="${WEB_IMAGE:-pperf-web:local}"
k6_image="${K6_IMAGE:-grafana/k6:2.4.0}"
seconds="${SECONDS_PER_SCENARIO:-20}"
only=",${ONLY:-panel,gateway,events,web,cold},"
export PYTHONPATH="$source_dir"
python="$repo/.venv/bin/python"
export PATH="$repo/.venv/bin:$PATH"

for port in 19100 19101 19102 19200 19300; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    echo "Port $port is in use: stop whatever listens there (an earlier run?) first." >&2
    exit 1
  fi
done

pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  docker rm -f pperf-web pperf-k6 >/dev/null 2>&1 || true
  wait 2>/dev/null || true
}
trap cleanup EXIT

[ -d "$run/odoo" ] || "$python" "$here/seed.py" "$run"
set -a
# shellcheck disable=SC1091
. "$run/panel.env"
set +a

"$python" "$here/stub_odoo.py" --port 19300 >"$run/stub.log" 2>&1 &
pids+=("$!")

GUNICORN_CMD_ARGS="--config $here/gunicorn_perf.py" sudoo.sh serve >"$run/panel.log" 2>&1 &
panel_pid="$!"
pids+=("$panel_pid")

if ! docker image inspect "$web_image" >/dev/null 2>&1; then
  docker build --quiet --tag "$web_image" "$repo/web" >/dev/null
fi
docker run -d --name pperf-web --network host -e EXPOSURE=cloudflare-quick \
  -e PANEL_PORT="$PANEL_PORT" -e API_PORT="$API_PORT" -e EVENTS_PORT="$EVENTS_PORT" "$web_image" >/dev/null

for _ in $(seq 1 300); do
  if curl -fsS "http://127.0.0.1:$PANEL_PORT/healthz" >/dev/null 2>&1 \
    && curl -fsS "http://127.0.0.1:19200/web/health" >/dev/null 2>&1; then break; fi
  sleep 0.2
done
grep -q "perf seed" "$run/panel.log" || sleep 2

# peak memory of the backend (gunicorn's worker) while the tests run; each part starts again from 0
worker="$(pgrep -P "$panel_pid" | head -1)"
echo 0 >"$run/peak_rss_kb"
(
  while kill -0 "$worker" 2>/dev/null; do
    rss="$(awk '/VmRSS/ {print $2}' "/proc/$worker/status" 2>/dev/null || echo 0)"
    peak="$(cat "$run/peak_rss_kb" 2>/dev/null || echo 0)"
    [ "${rss:-0}" -gt "${peak:-0}" ] && echo "$rss" >"$run/peak_rss_kb"
    sleep 0.5
  done
) &
pids+=("$!")

k6() {
  docker run --rm --name pperf-k6 --network host --user "$(id -u)" -v "$here/k6:/scripts:ro" -v "$run:/out" \
    -e SECONDS="$seconds" "$k6_image" run --quiet --no-usage-report "$@"
}

cp "$run/secrets.json" "$run/k6-secrets.json"
# the gateway first: the panel part leaves the process larger (Python keeps memory it used), which would hide what
# uploads and downloads cost
if [[ $only == *,gateway,* ]]; then
  echo 0 >"$run/peak_rss_kb"
  k6 -e BASE="http://127.0.0.1:19300" -e OUT=/out/gateway-direct.json /scripts/gateway.js >"$run/k6-direct.log" 2>&1
  k6 -e BASE="http://127.0.0.1:19200" -e OUT=/out/gateway.json /scripts/gateway.js >"$run/k6-gateway.log" 2>&1
  echo "$(cat "$run/peak_rss_kb") KB" >"$run/peak_rss_gateway.txt"
fi
if [[ $only == *,panel,* ]]; then
  echo 0 >"$run/peak_rss_kb"
  k6 -e BASE="http://127.0.0.1:$PANEL_PORT" -e SECRETS=/out/k6-secrets.json -e OUT=/out/panel.json \
    /scripts/panel.js >"$run/k6-panel.log" 2>&1
  echo "$(cat "$run/peak_rss_kb") KB" >"$run/peak_rss_panel.txt"
fi
if [[ $only == *,events,* ]]; then
  "$python" "$here/sse_bench.py" "$run" --base "http://127.0.0.1:$PANEL_PORT" >"$run/sse.log" 2>&1
fi
if [[ $only == *,web,* ]]; then
  "$python" "$here/lighthouse.py" "$run" --base "http://127.0.0.1:$PANEL_PORT" >"$run/lighthouse.log" 2>&1 \
    || echo "Lighthouse did not run: see $run/lighthouse.log"
fi

cleanup
pids=()
if [[ $only == *,cold,* ]]; then
  "$python" "$here/cold_start.py" "$run" >"$run/cold_start.log" 2>&1
fi
"$python" "$here/report.py" "$run"
