# Load tests

A repeatable load test of the panel's hot paths, used for the figures in
[docs/PERFORMANCE.md](../docs/PERFORMANCE.md). It runs on one Linux machine with Docker and the development setup of
[CONTRIBUTING.md](../CONTRIBUTING.md) (`uv sync`), and touches nothing else on it: the panel runs as a child
process with its own throwaway data folder, and every container it starts is named `pperf-*` and removed at the end.

```bash
perf/run.sh /tmp/perf-run                    # seed, start, measure, stop: about 15 minutes
uv run python perf/report.py /tmp/perf-run   # the tables again
```

To compare with another version, measure its `src/` with the same scripts and the web image built from its `web/`:

```bash
mkdir /tmp/old && git archive origin/main src web | tar -x -C /tmp/old
docker build --tag pperf-web:old /tmp/old/web
WEB_IMAGE=pperf-web:old perf/run.sh /tmp/perf-old /tmp/old/src
WEB_IMAGE=pperf-web:new perf/run.sh /tmp/perf-new                  # after: docker build --tag pperf-web:new web
uv run python perf/report.py /tmp/perf-old /tmp/perf-new           # before and after, side by side
docker image rm pperf-web:old pperf-web:new
```

`ONLY=panel,gateway,events,cold` runs some parts only; `SECONDS_PER_SCENARIO` (20) sets how long each scenario runs.

## What runs

| File | What it does |
| :--- | :--- |
| `seed.py` | A data folder as a busy server has it: 40 instances (folders with `.env`, a git repository with 12 commits, three backups each; no containers), 21 accounts, 10 API tokens, 200,000 audit-log entries. Settings: `host-ports` exposure on `127.0.0.1` (no Cloudflare connector is ever started), no second factor, rate limits off (they would answer `429` long before capacity is reached; they are tested in `tests/`). |
| `gunicorn_perf.py` | Gunicorn hooks, used only here (through `GUNICORN_CMD_ARGS`): one hour of metrics history for the server and every instance, and 300 finished jobs with their logs, which live in memory only. |
| `stub_odoo.py` | A stand-in for Odoo behind the first instance's gateway (4 processes): pages, a 400 KB script, JSON-RPC, long polling (2 s), WebSocket echo, uploads and downloads of any size. It answers at once, so what is measured is the gateway. |
| `k6/panel.js` | The panel through the web service (Caddy) on port 19100, one scenario after another, 16 virtual users each (8 for sign-in): sign-in, session, instance list and detail, metrics, metrics history, server, jobs, audit log, the API with signed token requests, and signed GitHub webhook deliveries. |
| `k6/gateway.js` | Run twice: straight against the stub Odoo, then through the instance's gateway; the difference is what the gateway adds. Page loads, a static asset, RPC calls (32 users), long polling (200 users), WebSocket round trips (50 users), 50 MB uploads and 100 MB downloads (4 users). |
| `sse_bench.py` | 300 browsers on the live event stream; a setting is changed 20 times and the time until each browser has the event is measured (k6 has no Server-Sent Events client). |
| `cold_start.py` | Time from `sudoo.sh serve` to the first answer of `/healthz`, five times, and the backend's memory once started. |
| `lighthouse.py` | The sign-in page and the dashboard in Lighthouse 12 (mobile), median of three runs (part `web`). |
| `gateway_ab.py` | The gateway of two versions side by side in rounds that alternate, so a busy machine slows both alike: `uv run python perf/gateway_ab.py <run folder> <old src> src`. |
| `report.py` | The tables of one run, or of two side by side. |

Every run folder keeps the raw results (`panel.json`, `gateway-direct.json`, `gateway.json`, `sse.json`,
`cold_start.json`), the peak memory of the backend during each part (`peak_rss_*.txt`) and the logs.

## Reading the results

- **req/s** is what the panel served with that many users asking as fast as they can: its capacity on this machine,
  not a traffic forecast. **p50/p95/p99** are the times within which half, 95 % and 99 % of the answers arrived.
- k6, the stub Odoo and the panel share the machine's processors, so absolute figures depend on the machine and on
  what else runs on it. Compare runs made one after the other on the same machine, and repeat a run when a figure
  looks odd.
- The panel is one process (by design: no message queue, no database server), and Python runs one thread at a time,
  so the API's figures are those of one processor core. Sign-in is slow on purpose (scrypt, about 0.1 s per
  password): that is what makes stolen password hashes expensive to guess.
