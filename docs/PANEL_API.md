# Panel API

**Audience:** developers connecting other systems to the panel: CI pipelines, chat bots, scripts, dashboards.
**Covers:** API tokens, how to sign a request, every endpoint, settings, live events, and a ready-to-use client.

This is the API of the **panel** (create, change, restart, delete instances). To talk to the **Odoo** inside an
instance, see [API and webhooks](API.md).

It is the panel's only API: the panel's own web app uses it too. Two kinds of callers are accepted, with the same
roles and checks:

| Caller | Authentication |
| :--- | :--- |
| Another system (CI, bot, script) | An API token and a signature on every request ([1](#1-getting-a-token), [2](#2-signing-a-request)) |
| The panel's web app in a browser | The session cookie set by `POST /api/v1/session`, plus `X-CSRF-Token` on every change |

---

## Contents

1. [Getting a token](#1-getting-a-token)
2. [Signing a request](#2-signing-a-request)
3. [Endpoints](#3-endpoints)
4. [Examples](#4-examples)
5. [Errors](#5-errors)
6. [Security notes](#6-security-notes)
7. [Settings](#7-settings)
8. [Live events](#8-live-events)
9. [Metrics](#9-metrics)
10. [Signing in from a web app](#10-signing-in-from-a-web-app)
11. [Limits](#11-limits)

---

## 1. Getting a token

An administrator creates tokens in the panel: **API tokens** in the top bar, then **Create token**, with a name
saying what will use it and a scope:

| Scope | Allows |
| :--- | :--- |
| `read` | List and read instances, jobs and versions |
| `write` | Everything `read` allows, plus create, change, run actions on and delete instances |

The panel shows two values **once**: the **token** (`sudoo_...`) and the **signing secret**. Store both where the other
system keeps secrets. A token can be revoked at any time on the same page; it stops working at once.

On the server, a token can also be created from the command line:

```bash
docker exec sudoo-panel sudoo create-token "CI pipeline" write
```

---

## 2. Signing a request

Every request carries four headers:

| Header | Value |
| :--- | :--- |
| `Authorization` | `Bearer <token>` |
| `X-Sudoo-Timestamp` | the current time, Unix seconds, for example `1794430123` |
| `X-Sudoo-Nonce` | 16 to 64 random letters, digits, `-` or `_`; new for every request |
| `X-Sudoo-Signature` | `sha256=` and the hex HMAC-SHA256 of the text below, with the signing secret as the key |

The signed text is five lines joined with `\n` (no newline at the end):

```
<X-Sudoo-Timestamp>
<X-Sudoo-Nonce>
<METHOD in capitals>
<path, with ?query if there is one>
<hex SHA-256 of the exact request body; of an empty body when there is none>
```

For example, `GET /api/v1/instances` at `1794430123` with nonce `p4Xq9mW2...`:

```
1794430123
p4Xq9mW2...
GET
/api/v1/instances
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

Requests with a body send it as JSON (`Content-Type: application/json`) and sign the exact bytes sent.

What each part protects against:

| Part | Stops |
| :--- | :--- |
| Token | Unknown callers. Revoking it ends access at once. |
| Signature over method, path and body | A request being changed on the way, and anyone who only has the token (from a log, a proxy, a screenshot) |
| Timestamp (within 5 minutes) | Old requests being sent again later |
| Nonce (each one accepted once) | The same request being sent twice within those 5 minutes |

---

## 3. Endpoints

All paths start with `/api/v1`, and every answer follows the same conventions:

| Convention | Detail |
| :--- | :--- |
| Success | `{"data": <resource or list>}`, and `"meta"` for lists |
| Background work | **202 Accepted**, the job in `data`, and a `Location` header pointing to it (`/api/v1/jobs/<id>`) |
| Errors | [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem details, `Content-Type: application/problem+json` (see [Errors](#5-errors)) |
| Times | ISO 8601 in UTC: `2026-11-12T08:30:00Z` |
| Request id | Every answer has `X-Request-ID`; send your own (8 to 64 letters, digits, `-`, `_`, `.`) to find a call in both logs |
| Links | Each resource has `links.self`, the path to read it again |
| Secrets | Never returned: no Odoo passwords, no master password, no ports |

| Method and path | Scope | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /instances?limit=100&offset=0` | read | | 200, a list of instances; `meta`: `total`, `count`, `limit` (1 to 500), `offset` |
| `GET /instances/<name>` | read | | 200, the instance with `disk`, `recent_commits`, `submodules` and `recent_jobs` |
| `GET /versions` | read | | 200, `[{"version": "19.0", "enterprise": true}, ...]` |
| `GET /jobs/<id>` | read | | 200, the job; add `?include=log` for its technical log |
| `POST /instances` | write | as below | **202**, the job; `Content-Location` names the new instance |
| `PATCH /instances/<name>` | write | any of `auto_update`, `auto_upgrade`, `auto_pause` (true or false), `delete_after_days` (0 keeps it) | 200, the instance |
| `POST /instances/<name>/actions/<action>` | write | `{}`, or the action's fields | **202**, the job |
| `DELETE /instances/<name>` | write | `{"confirm": "<name>"}` | **202**, the job; a backup is taken first |
| `GET /instances/<name>/credentials` | write | | 200, Odoo login, admin and master passwords, ports (administrators only) |
| `GET /instances/<name>/logs?lines=300` | read | | 200, `{"text"}`: the end of Odoo's log |
| `GET /instance-plan?repo=&branch=&name=` | write | | 200, the name and database a new instance would get, and the defaults |
| `GET /instance-quota` | read | | 200, how many instances the caller may still create, and when ([Limits](#11-limits)) |
| `GET /jobs?instance=&limit=30&offset=0` | read | | 200, recent jobs, newest first |
| `GET /jobs/<id>/log?offset=0` | read | | 200, `{"text"}` from that line on; `meta.next_offset` is where to continue |
| `GET /server` | read | | 200, the server's CPU, memory and disk, the panel's address, version and plugins |
| `GET /metrics` | read | | 200, the latest figures of the server and every instance ([Metrics](#9-metrics)) |
| `GET /metrics/history?minutes=60` | read | | 200, up to one hour of figures, in columns |
| `GET /instances/<name>/metrics?minutes=60` | read | | 200, one instance's latest figures and history |
| `GET /settings` | read | | 200, every setting ([Settings](#7-settings)) |
| `PATCH /settings` | write | `{"idle_hours": 4}` | 200, the settings; administrators only |
| `DELETE /settings/<key>` | write | | **204**, back to the `.env` value |
| `GET /events` | read | | the live event stream ([Live events](#8-live-events)) |

Accounts, tokens, backups and GitHub (administrators only, except the repository checks):

| Method and path | Body | Answer |
| :--- | :--- | :--- |
| `GET /users` | | 200, accounts |
| `POST /users` | `{"name", "role"}` (`admin` or `operator`) | **201**, the account with its `password`, shown once |
| `POST /users/<name>/password` | `{}` | 200, `{"password"}`: a new password, shown once |
| `DELETE /users/<name>` | | **204** |
| `DELETE /users/<name>/second-factors` | | **204**: removes the account's second factors (a lost phone) and signs it out |
| `GET /audit?limit=200` | | 200, the latest audit-log entries |
| `GET /tokens` | | 200, API tokens (never their secrets) |
| `POST /tokens` | `{"name", "scope"}` | **201**, with `token` and `signing_secret`, shown once |
| `DELETE /tokens/<id>` | | **204**, revoked |
| `GET /backups` | | 200, backups, newest first |
| `GET /backups/<file>` | | the zip |
| `DELETE /backups/<file>` | | **204** |
| `GET /deploy-keys` | | 200, deploy keys |
| `POST /deploy-keys` | `{"repo"}` | **201**, the public key to add on GitHub |
| `DELETE /deploy-keys/<owner>/<repo>` | | **204** |
| `GET /repositories/branches?url=` | | 200, `{"branches", "default"}` |
| `GET /repositories/access?url=&branch=` | | 200, whether the server can read the repository and each submodule |

The web app's session (no token):

| Method and path | Body | Answer |
| :--- | :--- | :--- |
| `GET /contact` | | 200, who to contact (no sign-in needed) |
| `POST /session` | `{"username", "password"}` | **201**, the account and its `csrf_token`; sets the session cookie. Or **200** with `second_factor` when a code is needed ([10](#10-signing-in-from-a-web-app)) |
| `POST /session/second-factor` | `{"method", "code"}` | **201**, signed in |
| `POST /session/second-factor/<method>` | `{}` | 200, a new code was sent (`email`) |
| `GET /account` | | 200, own account: e-mail, second factors, whether one is required |
| `POST /account/second-factors/<name>` | `email`: `{"email"}`; `totp`: `{}` | 200: a code was sent, or `{"secret", "otpauth_uri"}` for the app |
| `PUT /account/second-factors/<name>` | `{"code"}` | 200, it is set up |
| `DELETE /account/second-factors/<name>` | `{"password"}` | **204**; the account's other sessions end |
| `GET /session` | | 200, the signed-in account and its `csrf_token` |
| `DELETE /session` | | **204**, signed out |
| `PUT /session/password` | `{"current", "new"}` | **204**; other sessions of the account end |

An instance:

```json
{
  "data": {
    "name": "sales-main",
    "status": "online",
    "url": "https://river-stone-apple.trycloudflare.com",
    "odoo": {"version": "19.0", "edition": "enterprise", "database": "sales_main"},
    "git": {
      "repository": "https://github.com/acme-co/sales",
      "branch": "main",
      "branch_deleted_on_github": false,
      "commit": {"sha": "4f2a9c1", "subject": "Add delivery report", "author": "Sam", "age": "2 hours ago"}
    },
    "settings": {"auto_update": true, "auto_upgrade": true, "auto_pause": true, "delete_after_days": 30},
    "usage": {
      "cpu_percent": 3.2, "memory_bytes": 905969664, "memory_limit_bytes": 3011510272, "memory_percent": 30.1,
      "requests_per_minute": 42.0, "error_rate_percent": 0.0, "response_ms_avg": 38.5, "response_ms_p95": 120.4,
      "uptime_seconds": 7260, "disk_bytes": 412090368
    },
    "created_at": "2026-11-11T10:16:47Z",
    "last_visit_at": "2026-11-12T08:01:10Z",
    "pauses_at": "2026-11-12T14:01:10Z",
    "expires_at": "2026-12-11T10:16:47Z",
    "current_job": null,
    "links": {"self": "/api/v1/instances/sales-main"}
  }
}
```

`status` is one of `online`, `starting`, `busy` (a job is running), `paused`, `switched_off`, `stopped`, `unknown`.

A job (the answer to every 202, and to `GET /jobs/<id>`):

```json
{
  "data": {
    "id": "a12fff66a05d",
    "instance": "sales-main",
    "action": "Restart",
    "status": "running",
    "progress_percent": 50,
    "requested_by": "api:CI pipeline",
    "started_at": "2026-11-12T08:31:02Z",
    "finished_at": null,
    "steps": [{"title": "Restarting Odoo", "status": "running"}],
    "notes": [],
    "error": null,
    "links": {"self": "/api/v1/jobs/a12fff66a05d", "instance": "/api/v1/instances/sales-main"}
  }
}
```

`status` goes from `queued` to `running` to `success` or `failed`; when it failed, `error` explains why in plain words.
`progress_percent` is the share of steps done (below 100 until the job has finished), or `null` for jobs whose
number of steps is not known in advance.

Creating an instance takes the same fields as the **New instance** form:

```json
{
  "repo": "https://github.com/acme-co/sales",
  "branch": "main",
  "version": "19.0",
  "edition": "community",
  "name": "",
  "install_modules": true,
  "demo": false,
  "delete_after_days": 30
}
```

Actions:

| Action | Body |
| :--- | :--- |
| `update`, `update-submodules`, `restart`, `start`, `pause`, `switch-off`, `backup` | `{}` |
| `upgrade` | `{"modules": "sale_custom,stock_custom"}` or `{"modules": "all"}` |
| `switch-branch` | `{"branch": "main"}` |
| `add-submodule` | `{"url": "https://github.com/OCA/web", "path": "oca/web", "branch": "19.0"}` |
| `remove-submodule` | `{"path": "oca/web"}` |

---

## 4. Examples

**Python**: [`examples/panel_api_client.py`](../examples/panel_api_client.py) is a complete client using only the
standard library. Copy it into your project:

```python
from panel_api_client import PanelAPI

api = PanelAPI("https://panel.example.com", token="sudoo_...", signing_secret="...")
for instance in api.instances():                    # follows the pages of the list
    print(instance["name"], instance["status"], instance["url"])

job = api.action("sales-main", "update")           # waits until the update is finished
print(job["status"], job["error"])                  # success None, or failed and the reason

job = api.create(repo="https://github.com/acme-co/sales", branch="feature-x", version="19.0",
                 edition="community", install_modules=True, delete_after_days=7)
print(job["status"], job["instance"])
```

**Shell** (bash with openssl):

```bash
URL=https://panel.example.com TOKEN=sudoo_... SECRET=...
call() {  # call METHOD PATH [JSON]
  local body="${3:-}" ts nonce sig
  ts=$(date +%s); nonce=$(openssl rand -hex 16)
  sig=$(printf '%s\n%s\n%s\n%s\n%s' "$ts" "$nonce" "$1" "$2" "$(printf '%s' "$body" | sha256sum | cut -d' ' -f1)" \
        | openssl dgst -sha256 -hmac "$SECRET" -hex | awk '{print $NF}')
  curl -s -X "$1" "$URL$2" -H "Authorization: Bearer $TOKEN" -H "X-Sudoo-Timestamp: $ts" \
       -H "X-Sudoo-Nonce: $nonce" -H "X-Sudoo-Signature: sha256=$sig" \
       ${body:+-H "Content-Type: application/json" --data "$body"}
}
call GET /api/v1/instances
call POST /api/v1/instances/sales-main/actions/restart '{}'
```

**A CI pipeline** that gives every pull request its own instance: create an instance from the pull request's branch
with `delete_after_days: 7`, post its `url` as a comment, and delete it when the pull request is closed.

---

## 5. Errors

Errors follow [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) ("problem details"), with
`Content-Type: application/problem+json`:

```json
{
  "type": "about:blank",
  "title": "Bad Request",
  "status": 400,
  "detail": "Choose an Odoo version from the list.",
  "instance": "/api/v1/instances",
  "request_id": "5f0c9a3e2b8d4e7a9c1d0b6f3e2a1c4d"
}
```

`detail` says what is wrong and what to do; quote `request_id` when asking for help.

| Status | Meaning |
| :--- | :--- |
| 400 | A field is missing or not valid; `detail` says which and what to send |
| 401 | Token, timestamp, nonce or signature not accepted; `detail` says which |
| 403 | The token's scope does not allow this (a `read` token sending a change) |
| 404 | No such instance, job or action |
| 405 | The method is not allowed on this path |
| 415 | The body is not sent as JSON |
| 403 | Also: an instance limit is reached (`MAX_INSTANCES`, `MAX_INSTANCES_PER_USER`); `detail` says which |
| 429 | Too many requests per minute, too many refused calls from this address (5 in 15 minutes), or a creation within `CREATE_COOLDOWN_MINUTES`; `Retry-After` says how many seconds to wait |
| 500 | Something unexpected on the server; quote the `request_id` |

---

## 6. Security notes

* The token is stored only as a SHA-256 hash, and the signing secret encrypted with the vault key. Neither is ever
  shown again; a lost one means creating a new token.
* A `write` token can do everything an administrator can do with instances, including deleting them. Give each system
  its own token, with the `read` scope where reading is enough, and revoke tokens that are no longer used.
* Every call is in the audit log as `api:<token name>`, and every refused call with its reason and address.
* The API never returns passwords (Odoo admin, master password) or the server's ports.
* API tokens do not open the panel's pages. The browser's session opens the API only from the panel's own
  address, and every change also needs the session's CSRF token, so another website cannot use a visitor's session.

---

## 7. Settings

The settings in `.env` are the defaults. An administrator can change some of them while the panel runs, on the
**Settings** page or through the API; the change takes effect at once, without a restart, and is kept on top of
`.env`. Which ones: those marked **live** in [Configuration](CONFIGURATION.md), such as `idle_hours`,
`auto_pause` (the default for new instances), `delete_after_days` and the session limits. Ports, plugins and
images are read when the panel starts and stay in `.env`.

```json
GET /api/v1/settings
{
  "data": [
    {
      "key": "idle_hours", "env": "IDLE_HOURS",
      "value": 4, "env_value": 6, "changed_in_panel": true,
      "editable": true, "secret": false, "type": "number", "minimum": 0,
      "description": "Pause an instance after this many hours without visits; 0: never."
    },
    ...
  ]
}
```

| Request | Effect |
| :--- | :--- |
| `PATCH /api/v1/settings` `{"idle_hours": 4, "auto_pause": false}` | Change several at once; all are checked before any is kept |
| `PATCH /api/v1/settings` `{"idle_hours": null}` | Back to the `.env` value |
| `DELETE /api/v1/settings/idle_hours` | The same |

A wrong value is refused with 400 and the setting's name (`IDLE_HOURS: Input should be greater than or equal to
0.`); a setting that is not live is refused with 400. Secrets (`odoo_admin_password`, `github_webhook_secret`)
are never shown: `value` only says whether they are set.

---

## 8. Live events

`GET /api/v1/events` keeps the connection open and sends every change the moment it happens, as
[Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html), a standard every browser
reads with `EventSource`. Nothing needs to ask "anything new?" again and again: a job's progress, an instance going
online, new resource figures arrive by themselves.

```
id: 4182
event: job
data: {"key": "a12fff66a05d", "data": {"id": "a12fff66a05d", "status": "running", "steps": [...], ...}}

id: 4183
event: metrics
data: {"key": "", "data": {"server": {"cpu": 12.5, ...}, "instances": {"sales-main": {"cpu_percent": 3.1, ...}}}}
```

| Event | When | `data` |
| :--- | :--- | :--- |
| `instance` | An instance changed: status, code, address, settings, a job started or ended | The instance, as `GET /instances/<name>` |
| `instance.deleted` | An instance was deleted | `{"name"}` |
| `job` | A job started, moved to another step, added a note or ended | The job, as `GET /jobs/<id>` |
| `job.output` | New log text of a running job (at most twice a second) | `{"job", "offset", "text"}` |
| `metrics` | Every 15 seconds | The new figures, as `GET /metrics` ([Metrics](#9-metrics)) |
| `settings` | A setting was changed | `{"changed": ["idle_hours", ...]}` |
| `reset` | After a reconnection, when more was missed than the panel keeps | `{}`: reload everything |

* `?topics=job,instance` sends only those events.
* A line starting with `:` is sent every 20 seconds to keep the connection open through proxies; ignore it.
* When the connection drops, `EventSource` reconnects by itself and sends the last `id` it received
  (`Last-Event-ID`); the stream starts with what was missed, only the latest state of each instance and job.
  Other clients can send `?last_event_id=` instead.

In a browser (the session cookie is sent by itself):

```js
const events = new EventSource("/api/v1/events?topics=job,instance");
events.addEventListener("job", (message) => {
  const { data: job } = JSON.parse(message.data);
  console.log(job.action, job.status);
});
```

From another system, sign `GET /api/v1/events` like any other request; the signature's 5-minute window applies to
opening the stream, which then stays open. With the [Python client](../examples/panel_api_client.py):

```python
for event in api.events(topics=["job"]):
    print(event["event"], event["data"]["status"])
```

---

## 9. Metrics

The panel measures the server and every instance every 15 seconds and keeps one hour of figures in memory (they
start again empty when the panel restarts).

| Figure | Meaning |
| :--- | :--- |
| `cpu_percent` | Share of the whole server's processor (all cores); for an instance, Odoo and its database together |
| `memory_percent`, `memory_bytes`, `memory_limit_bytes` | Memory in use, of the containers' limit (the server's memory when they have none) |
| `requests_per_minute` | Requests the gateway forwarded to Odoo: pages, RPC and API calls (the background traffic of open tabs is left out) |
| `error_rate_percent` | Share of those requests answered with a 5xx error; `null` without requests |
| `response_ms_avg`, `response_ms_p95` | Time until Odoo started answering: average, and the time 95 % of requests stayed under |
| `uptime_seconds` | Since Odoo's container started; `null` when it is not running |
| `disk_bytes` | Database and files, measured every 5 minutes while running |
| Server only: `memory_*_bytes`, `disk_percent`, `disk_*_bytes`, `load_1m`, `cores`, `uptime_seconds`, `instances_running`, `instances_total` | |

`GET /api/v1/metrics` returns the latest sample:

```json
{
  "data": {
    "at": 1794430123,
    "interval_seconds": 15,
    "server": {"cpu_percent": 12.5, "memory_percent": 61.2, "disk_percent": 48.0, "load_1m": 0.42, ...},
    "instances": {"sales-main": {"running": true, "cpu_percent": 3.2, "memory_percent": 30.1, ...}}
  }
}
```

`GET /api/v1/metrics/history?minutes=60` returns the history in **columns**, one list per figure, the shape chart
libraries draw from directly; `t` holds the times (Unix seconds):

```json
{
  "data": {
    "server": {"t": [1794426523, ...], "cpu_percent": [11.0, ...], "memory_percent": [...], "disk_percent": [...], "load_1m": [...]},
    "instances": {"sales-main": {"t": [...], "cpu_percent": [...], "memory_percent": [...], "requests_per_minute": [...], ...}}
  },
  "meta": {"minutes": 60, "interval_seconds": 15}
}
```

A dashboard loads the history once, then appends the sample of every `metrics` [live event](#8-live-events).

---

## 10. Signing in from a web app

The panel's own web app signs in with a session cookie. Without a second factor it is one call; with one, two:

```
POST /api/v1/session            {"username": "sam", "password": "..."}
  -> 201 {"data": {"user": "sam", "csrf_token": "...", ...}}                 signed in
  -> 200 {"data": {"second_factor": {
            "methods": [{"name": "totp", "label": "Authenticator app"}, {"name": "email", "label": "E-mail code"}],
            "sent_to": "s***@example.com"}}}                                   a code is needed

POST /api/v1/session/second-factor/email          (only to send a new e-mail code)
POST /api/v1/session/second-factor  {"method": "totp", "code": "123456"}
  -> 201 {"data": {"user": "sam", "csrf_token": "...", ...}}
```

Between the two calls the session opens nothing, for 5 minutes at most. When `REQUIRE_SECOND_FACTOR` applies to an
account that has none, the answer has `"must_set_up_second_factor": true` and only `/api/v1/session`,
`/api/v1/account` and `/api/v1/contact` answer until one is set up:

```
POST /api/v1/account/second-factors/totp   {}      -> {"secret": "JBSW...", "otpauth_uri": "otpauth://totp/..."}
                                                       (show the URI as a QR code)
PUT  /api/v1/account/second-factors/totp   {"code": "123456"}
```

API tokens are not affected: a token already is something only its system has.

---

## 11. Limits

**Requests per minute.** Every caller (signed-in user, API token, or address before signing in) may send
`RATE_LIMIT_READS_PER_MINUTE` reading requests (600) and `RATE_LIMIT_CHANGES_PER_MINUTE` changing ones (60). Answers
carry the standard fields:

```
RateLimit-Limit: 60
RateLimit-Remaining: 57
RateLimit-Reset: 41          seconds until the window has moved on
```

Over the limit: `429 Too Many Requests` and `Retry-After`. Well-behaved clients wait that long.

**Instances.** `MAX_INSTANCES` (server), `MAX_INSTANCES_PER_USER` and `CREATE_COOLDOWN_MINUTES` limit creation; all
are off (0) unless set, and can be changed on the Settings page. Before offering **New instance**, ask:

```json
GET /api/v1/instance-quota
{"data": {"instances": 4, "max_instances": 10, "own_instances": 2, "max_instances_per_user": 3,
          "cooldown_minutes": 60, "next_creation_at": 1794433723}}
```

`next_creation_at` (Unix time) is set while the caller has to wait. A creation that breaks a limit is refused with
403 (a number is reached) or 429 with `Retry-After` (wait).
