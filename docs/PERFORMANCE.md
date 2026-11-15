# Performance

**Audience:** developers changing the panel, and administrators who want to know what one server can carry.
**Covers:** the targets, how the panel is measured, the figures before and after the work that set them, what
keeps the hot paths fast, and the protections on those paths.

---

## Contents

1. [Targets](#1-targets)
2. [How it is measured](#2-how-it-is-measured)
3. [Before and after](#3-before-and-after)
4. [What makes it fast](#4-what-makes-it-fast)
5. [Security on the hot paths](#5-security-on-the-hot-paths)
6. [Rules for changes](#6-rules-for-changes)
7. [Not done yet](#7-not-done-yet)

---

## 1. Targets

Measured with the load test in [`perf/`](../perf/README.md): 40 instances, 200,000 audit-log entries, an hour of
metrics and 300 jobs in memory, 16 users at once on each path (8 for sign-in, 32 visitors on the gateway), through
the web service as browsers reach the panel. The panel is one process (no message queue, no database server), so
these are the figures of one processor core.

| Path | Target (p95, at the load above) | Measured after |
| :--- | :--- | :--- |
| Instance list (40 instances), metrics, server, jobs, session | under 100 ms | 38 to 98 ms |
| An instance's page | under 100 ms | 50 ms (was 381 ms) |
| Dashboard metrics history (an hour, 40 instances) | under 250 ms | 64 ms (was 2.1 s) |
| Signed API calls | under 100 ms | 41 to 78 ms (was up to 276 ms) |
| GitHub webhook delivery | under 250 ms | 203 ms (was 469 ms) |
| Sign-in | about 0.1 to 0.2 s, never less: scrypt is slow on purpose | 197 ms |
| Gateway: time added to a page or RPC call | under 5 ms at the median | 4.3 to 5.3 ms (was 5.5 to 6.7 ms) |
| Gateway: memory while 4 visitors upload 50 MB each | not proportional to the uploads | 84 MB peak (was 310 MB) |
| Live events: one change reaching 300 open tabs | p99 under 100 ms | 47 ms |
| Sign-in page, first load (Lighthouse mobile) | score 90 or more | 95 (was 90) |
| Backend cold start | under 1 s | 0.81 s (was 1.0 s) |

---

## 2. How it is measured

`perf/run.sh <run folder>` seeds a throwaway data folder, starts the panel exactly as the container does
(`sudoo.sh serve`), puts the web service (Caddy, the `sudoo-web` image) in front of it and measures, part by part:

| Part | What | Tool |
| :--- | :--- | :--- |
| Gateway | Page loads, a 400 KB script, JSON-RPC calls, long polling, WebSocket round trips, 50 MB uploads, 100 MB downloads; first straight against a stand-in Odoo, then through the instance's gateway | k6 (Docker) and `perf/stub_odoo.py` |
| Panel | Sign-in, session, instance list and page, metrics and their history, server, jobs, audit log, the API with signed tokens, signed GitHub webhook deliveries | k6 |
| Live events | 300 browsers on `/api/v1/events`; the time until each has an event | `perf/sse_bench.py` |
| First load | The sign-in page and the dashboard in Lighthouse (mobile: slow 4G, slower processor) | Lighthouse 12 |
| Cold start | `sudoo.sh serve` until `/healthz` answers, and memory once started | `perf/cold_start.py` |

Each scenario runs for 20 seconds with its users asking as fast as they are answered, so **req/s** is capacity on
the test machine, not a traffic forecast; p50, p95 and p99 are the times within which half, 95 % and 99 % of the
answers arrived. The machine (16 cores, shared with other work) also runs k6 and the stand-in Odoo, so figures move
from run to run: the tables compare runs made one right after the other, and paths whose code did not change
(session, metrics, audit log, the stand-in Odoo itself) show how much the machine moved between them. How to run
and compare two versions: [perf/README.md](../perf/README.md).

---

## 3. Before and after

Measured on the same machine, one run right after the other, `origin/main` (before) and this version (after).
The machine was busy with other work during the panel runs, which is why paths whose code did not change (session,
metrics, jobs, audit log) are slower here than in quieter runs; compare before and after, not with other tables.

### Panel (16 users per path; sign-in 8)

| Path | req/s before | req/s after | p50 ms before | p50 ms after | p95 ms before | p95 ms after | p99 ms before | p99 ms after |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Sign-in | 50.8 | 47.8 | 158.6 | 164.3 | 189.4 | 196.5 | 205.7 | 204.4 |
| Session | 608 | 594 | 25.7 | 26.3 | 37.3 | 39.1 | 43.4 | 46.3 |
| Instance list (40) | 67 | 224 | 238.8 | 70.2 | 285.3 | 97.9 | 310.5 | 112.8 |
| Instance page | 47.6 | 486 | 338.0 | 31.6 | 380.6 | 49.9 | 409.0 | 61.9 |
| Metrics | 571 | 605 | 27.3 | 25.8 | 40.4 | 38.5 | 46.8 | 44.7 |
| Metrics history (1 h, 40 instances) | 11.3 | 341 | 1402.6 | 42.7 | 2143.4 | 63.9 | 2488.6 | 218.9 |
| Instance metrics | 248 | 586 | 63.1 | 26.1 | 94.1 | 41.1 | 108.4 | 54.2 |
| Server | 353 | 622 | 44.2 | 25.0 | 66.0 | 37.6 | 77.8 | 44.9 |
| Jobs | 427 | 411 | 36.6 | 37.9 | 54.3 | 57.0 | 63.2 | 66.2 |
| Audit log (of 200,000) | 185 | 182 | 85.6 | 86.9 | 100.1 | 100.5 | 110.7 | 112.6 |
| API token: instances | 73.3 | 256 | 215.6 | 61.4 | 276.4 | 78.0 | 326.2 | 89.5 |
| API token: jobs | 385 | 506 | 10.9 | 31.0 | 138.2 | 41.2 | 637.1 | 49.1 |
| GitHub webhook | 38.1 | 112 | 422.2 | 140.8 | 468.8 | 203.3 | 488.3 | 234.9 |

API token calls no longer queue behind each other on the storage's write lock, which is why their slowest answers
fell most (p99 637 ms to 49 ms); the median of the jobs call rose because the calls no longer leave the lock to
some of them. In this run the history answer was larger on the wire (517 KB instead of 397 KB): the backend compressed it once
per measurement with gzip, where Caddy had used zstd per request. The backend now also keeps a zstd copy (Python
3.14 and later, as in the panel's image) and sends it to clients that accept it, so browsers get the smaller one.

### Gateway (`perf/gateway_ab.py`: both versions side by side, median of 3 interleaved rounds of 10 s)

| Scenario | Odoo direct | Before | After |
| :--- | ---: | ---: | ---: |
| Page (25 KB), 32 users: req/s | 26,612 | 4,690 | 5,546 |
| Page: p50 / p95 / p99 ms | 1.1 / 2.0 / 3.0 | 6.5 / 9.2 / 11.5 | 5.4 / 7.8 / 10.1 |
| Script (400 KB), 32 users: req/s | 13,011 | 2,035 | 2,287 |
| Script: p50 / p95 / p99 ms | 1.6 / 5.8 / 9.2 | 15.1 / 20.1 / 23.3 | 13.2 / 18.3 / 21.5 |
| JSON-RPC, 32 users: req/s | 41,693 | 4,145 | 5,209 |
| JSON-RPC: p50 / p95 / p99 ms | 0.6 / 1.3 / 1.8 | 7.3 / 9.5 / 12.3 | 5.9 / 7.1 / 10.0 |
| Long polling (2 s), 200 users: p50 / p99 ms | 2003 / 2007 | 2004 / 2057 | 2004 / 2045 |
| WebSocket round trip, 50 users: p50 / p99 ms | 0 / 1 | 1 / 5 | 1 / 5 |
| Upload 50 MB, 4 users: MB/s | 4,060 | 785 | 1,920 |
| Upload: p50 / p99 ms | 26 / 42 | 239 / 263 | 80 / 100 |
| Download 100 MB, 4 users: MB/s | 13,050 | 2,000 | 2,680 |
| Download: p50 / p99 ms | 31 / 42 | 201 / 245 | 140 / 211 |
| Peak memory of the gateway's process | | 310 MB | 84 MB |

The full runs (`perf/run.sh`, gateway inside the panel process) agree: four 50 MB uploads at once took the panel
to 371 MB before and 150 MB after.

### First load (Lighthouse 12, mobile, median of 3)

| Page | Measure | Before | After |
| :--- | :--- | ---: | ---: |
| Sign-in | Score | 90 | 95 |
| Sign-in | First / Largest Contentful Paint | 2.46 / 2.85 s | 2.10 / 2.25 s |
| Sign-in | Total Blocking Time | 167 ms | 134 ms |
| Sign-in | Transferred (scripts) | 266 KB (200 KB) | 174 KB (111 KB) |
| Dashboard | Score | 59 | 60 |
| Dashboard | First / Largest Contentful Paint | 2.62 / 3.42 s | 2.61 / 3.72 s |
| Dashboard | Total Blocking Time | 2,475 ms | 1,366 ms |
| Dashboard | Transferred (scripts) | 655 KB (200 KB) | 741 KB (178 KB) |

The dashboard transferred more in total because of the history's encoding (above); its scripts are smaller and
its main thread is blocked half as long. Its Largest Contentful Paint did not improve: the page waits for the
instance list and the hour of history of 40 instances, and adds the histories up in the browser (see
[Not done yet](#7-not-done-yet)).

### Web app bundle (`npm run build`)

| File | Before | After |
| :--- | ---: | ---: |
| Loaded by the sign-in page (JavaScript, gzip) | 196 KB (index 141 KB + shared 56 KB) | 120 KB (react 109 KB + index 11 KB) |
| Chart.js | in the first file | `charts` 64 KB gzip, with the first chart |
| All text assets, Brotli | compressed by Caddy per answer | 229 KB, compressed once at build |

### Live events, cold start

| | Before | After |
| :--- | ---: | ---: |
| One change reaching 300 tabs: p50 / p95 / p99 | 17.6 / 26.8 / 41.0 ms | 18.4 / 27.5 / 47.1 ms |
| Opening a stream (300 at once): p50 / p99 | 136 / 173 ms | 170 / 236 ms |
| Backend cold start (median of 5) | 1.01 s | 0.81 s |
| Backend memory once started | 106 MB | 106 MB |

The live events were already fast; serialising each event once matters with more tabs than measured here, and
the small differences are within what the machine's load moves between runs.

---

## 4. What makes it fast

### Backend

| Change | Why |
| :--- | :--- |
| An instance's `.env` is read through a cache that follows the file's signature (inode, size, modification time; `core/cache.py`) | Every visit through the gateway and every instance of every list read and parsed the file. A file changed in the last second is always read again, so two changes within one tick of the file system's clock cannot be mixed up. |
| The backup list follows the backups folder's signature | An instance's page listed and examined every backup file three times. Backups are written under another name and renamed when complete, and removed by unlinking, which both change the folder. |
| Repository addresses follow `.git/config` (and git's address rules); recent commits and submodules are read again after every job on the instance and at least every 30 seconds | A GitHub webhook delivery started git once per instance, and an instance's page started git three or more times. Code only changes through jobs; changes made by hand show within 30 seconds. |
| The metrics history and an instance's figures are serialised and compressed (zstd and gzip) once per measurement, for every caller, after the permission check | The dashboard's history is the largest answer of the API (megabytes of JSON for 40 instances) and was built and compressed again for every viewer. The answer is the same for everyone allowed to read it, and the cache key never names a caller. |
| When a cached value must be rebuilt, one caller builds it and the others wait for it | Otherwise every request arriving after a new measurement builds the same answer at once. |
| Each live event is serialised once for all connected clients | A metrics event was serialised once per open tab. |
| Documentation pages are rendered once; the package version is read once; JSON answers are not sorted key by key | Rendering a long page took 20 to 40 ms; reading the version scanned the installed packages. |
| API tokens: replayed-request checks forget old nonces in arrival order; "last used" is written at most once a minute per token | The check scanned every remembered nonce on every call, and every call waited for the storage's write lock. |
| The dummy password hash (the time an unknown account takes to refuse) is made in the background at start | It took 70 ms of every start. |
| gunicorn keeps its heartbeat file in `MEMORY_DIR` | On a container's disk a slow write can make gunicorn think its worker hangs. |

SQLite already ran with write-ahead logging, `synchronous=NORMAL` and indexes for the audit log's filters; the
lists have no N+1 queries left (each instance's state comes from memory). The worker model stays one process with
32 threads: the jobs, the monitor, the gateway and the live events share their state in memory, and the live
events and the gateway run on their own asyncio loop, so open streams cost no thread.

### Gateway

| Change | Why |
| :--- | :--- |
| Request bodies are passed on to Odoo as they arrive, with a size limit and an idle timeout | They were read whole first: each upload sat in the panel's memory (up to 2 GB per upload) before Odoo saw a byte. |
| Answers are passed on as they arrive (`iter_any`) and sockets are read in 1 MB steps | Fewer, larger writes for files; long-polling answers leave at once. |
| Connections to Odoo are kept open and reused | No new connection per request (they were reused already; now documented and kept). |
| No file is read per visit (the instance's `.env` comes from the cache, paths are plain strings) | Every visit read and parsed the instance's `.env`. |

### Web app

| Change | Why |
| :--- | :--- |
| The sign-in page loads neither the panel's frame nor the dashboard nor Chart.js | They were in the first files every visitor downloaded. The session guard moved to `components/Session.tsx`; the frame and every page load when first opened. |
| React, the router and TanStack Query; Chart.js; the icons: each in a file of its own | They change far less often than the panel's own code, so browsers keep them across releases (`/assets/` files are immutable, named by their content). |
| Every text asset is compressed at build time (Brotli and gzip, `web/scripts/precompress.mjs`) and Caddy serves those files | Caddy compressed every answer again; Brotli at its highest level is also smaller. |
| Charts draw an hour from at most 360 points (sparklines 120, LTTB decimation), give their points fixed colours, draw the first time without animation and do not draw the same data twice | On the dashboard, Chart.js resolved the options of every one of 720 points per series, called the gradient function for each point, and animated the hour's history twice when it arrived. |
| The frame re-renders only when the connection's state changes, not on every live event | Every event redrew the sidebar and the top bar. |

There is no polling: everything after the first load arrives on the live event stream.

---

## 5. Security on the hot paths

Nothing was made faster by checking less: sign-in still costs about 0.1 s of scrypt per password, every API call
is authenticated, CSRF-checked and rate limited as before, and the integrity watchdog and the instances' hardening
are unchanged. While measuring, these were checked and, where needed, fixed (tests in `tests/test_gateway.py` and
`tests/test_performance.py`):

| Check | Result |
| :--- | :--- |
| Secrets compared in constant time | Already so everywhere: CSRF token, API token hash and signature, webhook signatures, setup code, second-factor codes. |
| Request size limits | Backend: 64 KB (unchanged). Web service: bodies over 1 MB for the backend refused before they reach it (new). Gateway: `GATEWAY_MAX_UPLOAD_MB` (new; the earlier 2 GB limit applied to a body read whole into memory). WebSocket messages: `GATEWAY_WEBSOCKET_MAX_MESSAGE_MB` (new; there was no limit). |
| Slow requests | Web service: 10 s for a request's headers, 30 s for its body, idle connections closed after 2 minutes (new). Gateway: a body that sends nothing for `GATEWAY_IDLE_TIMEOUT_SECONDS` is given up with `408` (new); a connection has `GATEWAY_HEADER_TIMEOUT_SECONDS` (30) to send its request headers, and at most `GATEWAY_MAX_CONNECTIONS` (per instance) and `GATEWAY_MAX_CONNECTIONS_TOTAL` connections are open at once, more are answered `503` (new). |
| Request smuggling | The gateway's server and client parse HTTP/1.1 strictly (llhttp): a request with both `Content-Length` and `Transfer-Encoding` is refused. Bodies are passed on with the length they arrived with, or chunked; hop-by-hop headers never pass. |
| Hop-by-hop headers | Removed in both directions, now including any header named in `Connection` and `Proxy-Connection` (fixed). |
| Forged visitor addresses | Odoo believes the last address of `X-Forwarded-For`. The gateway now appends the address it can vouch for and sets `X-Forwarded-Proto` and `X-Forwarded-Host` itself. Before, with `host-ports`, a visitor's own `X-Forwarded-For` reached Odoo unchanged, so Odoo's sign-in limits per address could be dodged (fixed). |
| Panel cookies never reach Odoo | The panel's cookie is removed from every `Cookie` header (a browser on HTTP/2 can send several), and Odoo cannot set a cookie with its name (fixed: only one `Cookie` header was filtered, and the others were dropped). |
| Encodings | The gateway no longer asks Odoo for gzip on behalf of a visitor that did not ask for it (it passes answers on unchanged). |
| Caches that could leak between users | The shared answers (metrics history and figures) are the same for every caller allowed to read them, served only after the permission check, with keys that never contain a caller; the caches of files and of git hold no per-user data. |
| Memory that callers can grow | The sign-in throttle kept an entry for every address and name it was asked about, forever; it now keeps only recent failures (fixed). The nonce store forgets in arrival order. Every new cache has a size bound. |

---

## 6. Rules for changes

- **No Docker or git command on a request path.** The monitor measures every `METRICS_INTERVAL_SECONDS` from one
  `docker stats` stream and one `docker ps`; requests read what it measured. Anything slower than a memory lookup
  that a request needs goes through `core/cache.py` with a version that changes exactly when the value does.
- **No file read per visit.** The gateway runs on the same event loop as every other instance's gateway and the
  live events: a blocking call there delays all of them.
- **Never read a body whole in the gateway.** Pass it on, with a limit.
- **Shared answers only after the permission check, and only when they are the same for every caller.**
- **A chart gets the data it shows, once.** Large series are decimated; animations are for samples that arrive
  later, not for the first draw.
- Run `perf/run.sh` before and after a change to a hot path, and add the figures to the pull request.

---

## 7. Not done yet

| Idea | Why not now |
| :--- | :--- |
| uvloop for the gateway's event loop, orjson for JSON | Both are new dependencies; each is worth an issue first (CONTRIBUTING.md, rule 7). uvloop would roughly halve the gateway's own cost per request. |
| Several backend processes | The jobs, the monitor and the gateway share their state in memory by design; more processes would need a shared store (Redis or a database server), which the panel avoids. Free-threaded Python is the way to use more cores later. |
| A header timeout in the gateway | aiohttp has none. Behind a Cloudflare tunnel (the default) slow clients never reach the gateway; with `host-ports`, put a TLS proxy in front, as [Plugins](PLUGINS.md#host-ports) advises. |
| A summary endpoint for the dashboard | The dashboard adds up 40 instances' histories in the browser; the server could send the sums. A change of the API, so a separate step. |
| GitHub webhook bodies | The backend accepts 64 KB per request; GitHub can send larger push payloads for very large pushes. Raising the limit for `/hooks/` only is worth a look. |
