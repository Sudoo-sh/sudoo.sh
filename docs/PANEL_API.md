# Panel API

**Audience:** developers connecting other systems to the panel: CI pipelines, chat bots, scripts, dashboards.
**Covers:** API tokens, how to sign a request, every endpoint and the permission it needs, settings, live events,
and a ready-to-use client.

This is the API of the **panel** (create, change, restart, delete instances). To talk to the **Odoo** inside an
instance, see [API and webhooks](API.md).

**Command line:** the `sudoo` command uses this API from a terminal or a CI job. Install it from PyPI with
`pipx install sudoo-cli` (or `pip install sudoo-cli`; [sudoo-cli on PyPI](https://pypi.org/project/sudoo-cli/)), and see
the [command line guide](https://docs.sudoo.sh/cli).

It is the panel's only API: the panel's own web app uses it too (the internal API of earlier versions is removed).
Two kinds of callers are accepted, with the same checks:

| Caller | Authentication |
| :--- | :--- |
| Another system (CI, bot, script) | An API token and a signature on every request ([1](#1-getting-a-token), [2](#2-signing-a-request)) |
| The panel's web app in a browser | The session cookie set by `POST /api/v1/session`, plus `X-CSRF-Token` on every change |

Every endpoint needs a **permission** (for example `instances.create`). A person's role gives a set of permissions
([Security: roles](SECURITY.md#9-roles-and-the-audit-log)); an API token acts as a role: a `write` token as an
**Administrator**, a `read` token as a **Viewer**, an `instances` token as a **Connected app**
([the instances scope](#the-instances-scope)).

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
12. [Web shell](#12-web-shell)
13. [Code profiler](#13-code-profiler)
14. [Client portal](#14-client-portal)
15. [Maintenance windows](#15-maintenance-windows)

---

## 1. Getting a token

Someone with the permission `tokens.manage` (super admins and administrators) creates tokens in the panel:
**API tokens** in the sidebar, then **Create token**, with a name saying what will use it and a scope:

| Scope | Acts as | Allows |
| :--- | :--- | :--- |
| `read` | Viewer | Read instances, their figures, activity and logs, jobs and versions. Every change is refused with 403. |
| `instances` | Connected app | For connected apps such as the Odoo module: list, create, start, pause, restart, back up, extend and delete instances; nothing else ([below](#the-instances-scope)). |
| `developer` | Developer (API) | For developers' tools and CI: list and read projects, branches and instances, their logs, jobs and caught e-mails; start, pause, restart, get latest code, update submodules; create and delete development instances. Never settings, accounts, teams, access, stages, production, restores, domains, off-site copies or shells ([below](#the-developer-scope)). |
| `write` | Administrator | Everything an Administrator may do: create, change, run actions on and delete instances, read Odoo passwords, manage accounts, tokens, backups and deploy keys. Not the panel's settings. |

The panel shows two values **once**: the **token** (`sudoo_...`) and the **signing secret**. Store both where the other
system keeps secrets. A token can be revoked at any time on the same page; it stops working at once.

A token belongs to the account that created it: it never does more than that account's role allows, and that
account's teams, project access and locked branches apply to it ([Projects](#projects)). A token made on the command
line (below) belongs to no account: it keeps its scope's role but does not see restricted projects or branches and
cannot act on locked branches.

On the server, a token can also be created from the command line:

```bash
docker exec sudoo-panel sudoo.sh create-token "CI pipeline" write
```

### The instances scope

An `instances` token is the least a connected app (such as the [Sudoo.sh apps for Odoo](https://apps.odoo.com/apps/modules/browse?author=Sudoo.sh),
source at [Sudoo-sh/sudoo-odoo](https://github.com/Sudoo-sh/sudoo-odoo)) needs. It acts as the role
**Connected app**, which only a token can have, with the permissions `instances.view`, `instances.create`,
`instances.update`, `instances.operate` and `instances.delete`, and it may call these endpoints and nothing else:

| Call | What it may do |
| :--- | :--- |
| `GET /versions` | The Odoo versions |
| `GET /instances`, `GET /instances/<name>` | List and read instances |
| `GET /instance-quota` | How many instances it may still create, and the running figures: `running` (in all, production included), `running_production`, `max_running` (`MAX_RUNNING_INSTANCES`), `running_own` (the non-production instances the caller created that run or start) and `max_running_own` (`null` when off) |
| `POST /instances` | Create an instance; `from_backup` is refused with 403 |
| `GET /capacity-queue` | `instances.view` | | 200, the waiting requests (`id`, `kind` create or start, `instance`, `user`, `position`, `status`): your own, all with `capacity.manage` |
| `GET /capacity-queue/<id>` | `instances.view` | | 200, one waiting request; 404 once it started or was cancelled (or is not yours) |
| `DELETE /capacity-queue/<id>` | `instances.view` | | 204; your own request, or any with `capacity.manage`; 404 otherwise |
| `POST /capacity-queue/<id>/front` | `capacity.manage` | | 200, the request first in line |
| `POST /instances/<name>/actions/<action>` | Only `start`, `pause`, `restart` and `backup`; every other action (`update`, `upgrade`, `switch-branch`, `restore`, `rollback`, `repair`, ...) is refused with 403 |
| `PATCH /instances/<name>` | Only `delete_after_days`, and only to make the lifetime end later (not `0`, "keep", unless the instance is kept already); anything else is refused with 403. It is also limited like a person without `instances.extend_unlimited` (see below) |
| `DELETE /instances/<name>` | Delete an instance after a backup; `without_backup` is refused with 403 |
| `GET /jobs/<id>` | Follow a job it started |

Every other call is refused with 403 and written to the audit log, among them Odoo passwords
(`/instances/<name>/credentials`), logs, restores, backups, GitHub, accounts, tokens, settings, plugins, metrics and
the [live event stream](#8-live-events) (follow jobs with `GET /jobs/<id>` instead).

### The developer scope

A `developer` token is for developers' own tools and CI. It acts as the role **Developer (API)**, which only a token
can have, with the permissions `instances.view`, `instances.logs`, `instances.update`, `instances.operate` (for
pausing only), `instances.create` and `instances.delete` (development instances only) and `caught_mail.view`. As every
token, it never does more than the account that created it: its rights are the scope's ∩ that account's role, and
that account's teams, project access and locked branches apply (a project or branch hidden from the account is
hidden from the token, 404). It may call these endpoints and nothing else:

| Call | What it may do |
| :--- | :--- |
| `GET /versions` | The Odoo versions |
| `GET /projects`, `GET /projects/<id>` | List and read the projects it may see, with their branches |
| `GET /instances`, `GET /instances/<name>`, `GET /instances/<name>/resources` | List and read instances |
| `GET /instances/<name>/logs` | Odoo's log |
| `GET /instances/<name>/logs/files`, `.../logs/entries`, `.../logs/stream`, `.../logs/download` | Odoo's logs: which there are, reading them in pages, following one, downloading one |
| `GET /instances/<name>/mail...` | Caught e-mails (reading only) |
| `GET /jobs`, `GET /jobs/<id>` | Jobs: their steps, progress and why they failed |
| `GET /instance-quota`, `GET /instance-plan` | How many it may still create, and the name a new one would get |
| `GET /repositories/modules`, `GET /odoo-apps` | The modules a new instance may get ([Choosing the modules](#choosing-the-modules)) |
| `GET /events` | The [live event stream](#8-live-events) |
| `POST /instances` | Create a **development** instance; another `stage` or `from_backup` is refused with 403 |
| `POST /projects/<id>/branches/<branch>/instances` | Create an instance of a **development** branch; staging and production branches are refused with 403 |
| `POST /instances/<name>/actions/<action>` | Only `start`, `pause`, `restart`, `update` and `update-submodules`; every other action (`backup`, `upgrade`, `switch-branch`, `restore`, `rollback`, `repair`, `update-images`, submodules added or removed, ...) is refused with 403 |
| `DELETE /instances/<name>` | Delete a **development** instance after a backup; staging and production are refused with 403 |

Every other call is refused with 403 and written to the audit log: changing an instance (`PATCH`, its settings,
lifetime or stage), resources, Odoo passwords, settings, accounts, tokens, teams, project settings, access, locks and
branch stages, backups, restores, custom domains, off-site copies, shells, the audit log and plugins.

---

## 2. Signing a request

Every request carries four headers, and a fifth when it is made on someone's behalf:

| Header | Value |
| :--- | :--- |
| `Authorization` | `Bearer <token>` |
| `X-Sudoo-Timestamp` | the current time, Unix seconds, for example `1794430123` |
| `X-Sudoo-Nonce` | 16 to 64 random letters, digits, `-` or `_`; new for every request |
| `X-Sudoo-Signature` | `sha256=` and the hex HMAC-SHA256 of the text below, with the signing secret as the key |
| `X-Sudoo-Acting-For` | optional: the person in the calling system on whose behalf the call is made, 1 to 120 letters, digits, `.`, `_`, `@`, `+` or `-` ([below](#acting-for-someone)) |

The signed text is five lines joined with `\n` (no newline at the end), and six with `X-Sudoo-Acting-For`:

```
<X-Sudoo-Timestamp>
<X-Sudoo-Nonce>
<METHOD in capitals>
<path, with ?query if there is one>
<hex SHA-256 of the exact request body; of an empty body when there is none>
<X-Sudoo-Acting-For, exactly as sent; only when the header is sent>
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

### Acting for someone

A connected app used by many people (the Odoo module, a chat bot) can say who asked for a call with
`X-Sudoo-Acting-For`, for example `X-Sudoo-Acting-For: sara@example.com`. The value is signed as the sixth line, so
it cannot be added, removed or changed on the way: a request whose signature does not cover it exactly is refused
with 401. A value that is empty, longer than 120 characters or has other characters is refused with 400.

The call is then made as `api:<token name> for <person>` instead of `api:<token name>`:

* in the audit log;
* as `created_by` of the instances it creates;
* in the [instance limits](#11-limits): `MAX_INSTANCES_PER_USER` and `CREATE_COOLDOWN_MINUTES` count per person,
  `MAX_INSTANCES` for the whole server still applies. Requests per minute are still counted per token.

The panel trusts the token holder to name the person truthfully: the header records who asked, it gives no rights
of its own. Without the header, nothing changes: the five-line text, and the caller is `api:<token name>`.

```
POST /api/v1/instances  (token "Odoo module", X-Sudoo-Acting-For: sara@example.com)
1794430123
p4Xq9mW2...
POST
/api/v1/instances
<hex SHA-256 of the body>
sara@example.com
  -> 202; the instance's created_by is "api:Odoo module for sara@example.com"
```

What each part protects against:

| Part | Stops |
| :--- | :--- |
| Token | Unknown callers. Revoking it ends access at once. |
| Signature over method, path and body | A request being changed on the way, and anyone who only has the token (from a log, a proxy, a screenshot) |
| Timestamp (within 5 minutes) | Old requests being sent again later |
| Nonce (each one accepted once) | The same request being sent twice within those 5 minutes |
| Acting-for line (when sent) | Someone adding, removing or changing the person a call is made for |

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
| Secrets | Never in a resource: no Odoo passwords, no master password, no ports. They are only in `GET /instances/<name>/credentials` (permission `instances.credentials`) |

The **Permission** column names what the caller needs. Without it the answer is 403, and `detail` says what the
role is missing. "public" means no sign-in; "signed in" means any account or token.

### Instances, jobs and versions

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /instances?limit=100&offset=0` | `instances.view` | | 200, a list of instances (those being created too, status `creating`); `meta`: `total`, `count`, `limit` (1 to 500), `offset` |
| `GET /deleted-instances` | `instances.view` | | 200, the instances deleted in the last `DELETED_HISTORY_DAYS` days, latest first: `name`, `project` (`id`, `name`, or `null`), `branch`, `stage`, `created_at`, `deleted_at`, `deleted_by` (`null` when its lifetime ended) and `expired`. Those of a project the caller may not see are left out |
| `GET /instances/<name>` | `instances.view` | | 200, the instance with `disk`, `recent_commits`, `submodules`, `backups`, `restorable_backup`, `newer_odoo_image` (a newer copy of its Odoo image is on this server: `update-images` moves it there) and `recent_jobs` ([below](#instances-jobs-and-actions-in-detail)); also while it is being created (status `creating`). For callers with `instances.shell` or `instances.credentials` in it, `containers`: `odoo` and `db` (container names), `db_user` and `host` (PUBLIC_HOST, or `null`), for the SSH and psql commands of a project's branch; never a password |
| `GET /instances/<name>/credentials` | `instances.credentials` | | 200, Odoo `login`, `admin_password`, `master_password`, `ports`; written to the audit log |
| `GET /instances/<name>/logs?lines=300` | `instances.logs` | | 200, `{"text"}`: the end of Odoo's log (1 to 5000 lines) |
| `GET /instances/<name>/logs/files` | `instances.logs` | | 200, the logs that can be read, as `{"name", "job", "status", "started_at"}`: `odoo.log` (Odoo's container), then `install.log` and `update.log` when the panel keeps such a run of Odoo (`started_at`: the latest; `job` and `status` always `null`) ([Reading logs](#reading-logs)). Jobs' technical logs are server logs: they are never listed, streamed or downloaded |
| `GET /instances/<name>/logs/entries?log=odoo.log&limit=500&before=\|after=\|around=&level=&logger=&q=&regex=&since=&until=&tracebacks=` | `instances.logs` | | 200, a page of entries, oldest first: `[{"id", "time", "level", "pid", "database", "logger", "traceback", "text"}]` (`text`: the line and the lines that belong to it, such as its traceback). Without a cursor the last `limit` (1 to 2000); `before` and `after` page from an entry's `id`, `around` gives the entry and about `limit` entries around it. `meta`: `log`, `has_before`, `has_after`, `first`, `last`. Filters below; 400 for a bad value or a pattern that could take very long, **422** when a pattern takes too long on this log ([Reading logs](#reading-logs)) |
| `GET /instances/<name>/logs/stream?log=odoo.log&tail=300&<filters>` | `instances.logs` | | Server-Sent Events: the last `tail` lines (0 to 5000), then new ones as they are written; the filters below keep whole entries. Events `lines` (`{"lines": [...], "ids": [...]}`, `ids` one per line) and `end` (`{"reason": "max_duration"}` after 5 minutes, `"stopped"` when Odoo's container stops, `"slow_pattern"` when the pattern took too long); every event's `id` is where to continue: send it back as `Last-Event-ID` or `?after=`. Only `odoo.log` is followed (400 for another). A caller follows at most 4 logs at once (429) ([Following a log](#following-a-log)) |
| `GET /instances/<name>/logs/download?log=odoo.log&<filters>` | `instances.logs` | | 200, the log as a text file (`text/plain`, attachment): its last `LOG_DOWNLOAD_MAX_LINES` lines, only the entries the filters keep; audit-logged with the filters |
| `GET /instances/<name>/odoo-users` | `instances.credentials` | | 200, the active internal Odoo users: `[{"id", "login", "name"}]` (read again after 30 seconds); 409 when the database is not running |
| `POST /instances/<name>/connect` | `instances.credentials` | `{"uid": 7}`; a production instance also `"confirm_stage": "production"` | **201**, `{"url", "expires_at", "login"}`: a link that opens Odoo signed in as that user, once, within 60 seconds ([Connect as](#connect-as)); audit-logged, and so is its use. With active access rules the link goes through the panel's pass first |
| `GET /instances/<name>/access-rules` | `instances.view` | | 200, the production access rules (beta): `{"available", "production", "active", "rules", "can_change", "channels", "geo", "geo_missing", "you", "presets", "back_office", "public_under_web"}`; only `{"available": false}` while `ACCESS_RULES_BETA` is off ([Administrator Guide](ADMIN_GUIDE.md#31-production-access-rules-beta)) |
| `POST /instances/<name>/access-rules/preview` | `instances.view` | `{"rules", "test_address"?}` | 200, `{"you", "test", "samples", "warnings", "geo"}`: what the rules would do, nothing saved; 400 for rules that are not valid |
| `POST /instances/<name>/access-rules/code` | `instances.view` and may change the rules (super admins, the project's administrators, its client; never a token) | `{"method": "email" \| "app"}` | 200, `{"method", "sent_to"?}`; 409 for an instance that is not in production, 429 when asked again within a minute |
| `PUT /instances/<name>/access-rules` | the same | `{"rules", "confirm_name", "method", "code"}` | 200, the same as GET; 400 for a wrong name or code, 429 after 5 wrong codes in 10 minutes; audit-logged |
| `POST /instances/<name>/open-link` | `instances.view` | `{"target"?: "/odoo"}` | 200, `{"url", "pass"}`: the address that opens the instance, through a signed pass when its access rules apply and the caller may open it (audit-logged). **403 for an API token**: a pass lets its holder past the country and address rules, so it needs a signed-in session |
| `POST /instances/<name>/imports` | `instances.restore` | `{"file_name": "prod.zip", "size": 734003200}` | **201**, a new upload `{"id", "file_name", "format", "size", "received", "complete", "chunk_size"}`; 400 for a file that is not `.zip`, `.dump`, `.sql` or `.sql.gz`, 413 beyond `IMPORT_MAX_GB` ([Import database](#import-database)) |
| `GET /instances/<name>/imports/<id>` | `instances.restore` | | 200, the upload: how much arrived |
| `PUT /instances/<name>/imports/<id>?offset=0` | `instances.restore` | the next piece as `application/octet-stream` (at most 8 MB) | 200, the upload; **409** with `meta.received` when `offset` is not where the upload stands |
| `DELETE /instances/<name>/imports/<id>` | `instances.restore` | | **204**, the upload removed |
| `POST /instances/<name>/imports/<id>/restore` | `instances.restore` | `{"neutralize": true}` (default `true`, `false` in production); a production instance also `"confirm_production": true` and `"confirm_stage": "production"` | **202**, the job "Import a database"; 400 when the file is not a valid dump, unsafe (paths outside the archive), or of another major Odoo version |
| `GET /instances/<name>/resources` | `instances.view` | | 200, `stage`, `priority` (`rank` 1 production to 3 development, `cpu_weight`, `oom_score_adj`, `memory_reservation`, `db_memory_reservation`), `limits` (what applies), `own` (set for this instance), `stage_defaults`, `odoo_defaults`, `unlimited`, `pending` (the containers run with older values until a restart), `warnings` (each `code`, `text`, `values`; they never refuse anything), `server` (`cores`, `memory_bytes`, `usable_bytes`, `reserved_bytes`), `project` and `branch` (the values its project sets for its stage and its branch sets), `inherited` (what applies without its own), `sources` (each knob's layer: `stage`, `project`, `branch`, `instance`), `computed` (container limits computed from the workers), `maxima`, `storage` (`database_bytes`, `files_bytes`, `total_bytes`, `measured_at`, `quota_bytes`, `percent`, `level`: `none`, `ok`, `warning`, `over`) ([Resources and priority](ADMIN_GUIDE.md#resources-and-priority)) |
| `PUT /instances/<name>/resources` | `instances.resources` | `{"limits": {"odoo_workers": 2, "odoo_memory_limit": "4g"}}`: the instance's own limits, each of `odoo_workers`, `odoo_cron_threads`, `odoo_limit_memory_soft_mb`, `odoo_limit_memory_hard_mb`, `odoo_limit_time_cpu`, `odoo_limit_time_real`, `odoo_limit_time_real_cron`, `odoo_memory_limit`, `odoo_cpu_limit`, `db_memory_limit`, `db_cpu_limit`, `db_max_connections` (a missing one or `null` follows the branch, the project and the stage); a production instance also `"confirm_production": true` | **202**, the job "Apply resources"; 400 for a value Docker or Odoo cannot use; **409** when the change raises the memory reserved by all instances above what the server can spare (`RESOURCES_RESERVATION_CHECK`) |
| `GET /projects/<id>/resources` | `instances.view` in the project | | 200, `stages` (for each stage: `own`, `inherited`, `applies`, `computed`, `sources`, `quota_bytes`), `maxima`, `odoo_defaults`, `instances` (each `name`, `branch`, `stage`, `limits`, `pending`, `storage`), `storage` (`instances`, `by_stage`, `total_bytes`), `server`, `changed` |
| `PUT /projects/<id>/resources` | `projects.resources` in the project | `{"stage": "production", "limits": {"odoo_workers": 2, "odoo_limit_time_real": 300}}`: replaces the project's values for that stage (the same knobs as above; left out or `null`: the stage's default); `"preview": true` changes nothing; a change that restarts production instances also `"confirm_production": true` | 200, the resources with `plan` (`before_bytes`, `after_bytes`, `usable_bytes`, `refusal`), `jobs` (one "Apply resources" per instance it changes) and `waiting` (instances busy or locked: they apply at their next start); a preview answers `plan`, `changes` and `view`; 400 above a maximum (`RESOURCES_MAX_*`), **409** when the server cannot hold it |
| `GET /projects/<id>/branches/<branch>/resources` | `instances.view` in the branch | | 200, the branch's `stage`, `own`, `inherited` (the stage and the project), `applies`, `computed`, `sources`, `maxima`, `instances`, `quota_bytes`, `server`, `changed` |
| `PUT /projects/<id>/branches/<branch>/resources` | `projects.resources` in the branch | `{"limits": {...}}`, `"preview"`, `"confirm_production"` as for a project; `{"limits": {}}` follows the project again | as for a project |
| `GET /projects/<id>/status` | `instances.view` in the project | | 200, `state` (`operational`, `degraded`, `down`, `unknown`), `checks`, `interval_seconds`, `slow_ms`, `production` (whether the project has a production instance), `targets` (each `id`, `kind`: `instance`, `backups`, `offsite`, `server`; `name`, `branch`, `state`: `up`, `degraded`, `down` or null before the first check; `since`, `checked_at`, `detail`, `response_ms`, `uptime` (`day`, `week`, `month` in %, null without checks), `days` (30, oldest first: `day` as Unix time, `uptime`, `avg_ms`, `degraded`), `hourly_ms` (24, instances only)), `incidents` (30 days, newest first: `id`, `target`, `kind`, `name`, `start`, `end` (null while open), `duration` seconds, `severity`, `what`), `public` (`enabled`, `allowed`, `path`; null without `projects.manage`) ([Project status and notifications](ADMIN_GUIDE.md#project-status-and-notifications)) |
| `PUT /projects/<id>/status/public` | `projects.manage` in the project | `{"enabled": true, "new_link": false}`: turns the public status page on or off; `new_link` replaces its token (the old link stops working) | 200, `enabled`, `allowed`, `path` (`/status/<token>`) |
| `GET /public-status/<token>` | none (the token) | | 200, `name`, `state`, `targets` (each `kind`, `name`, `state`, `uptime`, `days` with `day` and `uptime`), `at`; **404** for an unknown token, a page that is off, or `STATUS_PUBLIC_PAGES` off |
| `GET /projects/<id>/notifications` | `projects.manage` in the project | | 200, `enabled` (`NOTIFICATIONS`), `channels` (each `id`, `type`, `name`, `events`, `enabled`, `config` (`chat_id`, `addresses`), `secrets` (each secret's last 4 characters, never the value), `last` (the last delivery: `at`, `ok`, `result`, `event`, `attempts`, `retry_at`)), `events`, `default_events`, `types`, `limit` |
| `POST /projects/<id>/notifications` | `projects.manage` in the project | `{"type": "slack", "name": "Ops", "events": ["build_failed"], "secrets": {"url": "https://hooks.slack.com/..."}}`; `type` one of `slack`, `discord`, `teams`, `telegram` (`secrets.bot_token`, `config.chat_id`), `webhook`, `email` (`config.addresses`) | **201**, the channel; a webhook's also `signing_secret`, shown this once. 400 for an address that is not plain `https://` |
| `PATCH /projects/<id>/notifications/<channel>` | `projects.manage` in the project | any of `name`, `events`, `enabled`, `config`, `secrets` (a secret left out stays as it is) | 200, the channel |
| `DELETE /projects/<id>/notifications/<channel>` | `projects.manage` in the project | | 204 |
| `POST /projects/<id>/notifications/<channel>/test` | `projects.manage` in the project | | 200, `channel` and `delivery` (`ok`, `result`); sent at once, not retried |
| `POST /projects/<id>/notifications/<channel>/signing-secret` | `projects.manage` in the project | | 200, the webhook channel with its new `signing_secret`, shown this once |
| `POST /instances` | `instances.create` | as below | **202**, the job; `Content-Location` names the new instance. With `CAPACITY_QUEUE` on and a running limit reached: **202**, the waiting request (`Location: /capacity-queue/<id>`) instead of 403 |
| `PATCH /instances/<name>` | `instances.operate`; also `instances.delete` for a `delete_after_days` that ends the lifetime sooner (403 otherwise) | any of `auto_update`, `auto_upgrade`, `auto_pause` (true or false), `delete_after_days` (0 keeps it), `stage` ([Stages](#stages)) | 200, the instance. A later `delete_after_days` is an extension: without `instances.extend_unlimited` (Admin and super admin roles) it adds at most `EXPIRY_EXTEND_MAX_DAYS` days to the current end date, only `EXPIRY_EXTEND_MAX_COUNT` times (403 otherwise), never `0` (keep), and never to an instance stopped at the end of its lifetime. Extending one starts it again; when the server cannot run one more, it stays stopped and `meta.restart` says why |
| `POST /instances/<name>/actions/<action>` | depends on the action (below) | `{}`, or the action's fields | **202**, the job |
| `DELETE /instances/<name>` | `instances.delete`; with `without_backup` also `backups.manage` | `{"confirm": "<name>"}` (a production instance also `"confirm_stage": "production"`), and `"without_backup": true` to delete even when the backup fails (audit-logged) | **202**, the job; a backup is taken first, and without `without_backup` a failed backup stops the deletion |
| `GET /instance-plan?repo=&branch=&name=&version=&edition=&stage=` | `instances.create` | | 200, the name and database a new instance would get, the defaults of `stage` (development when not given) and of every stage (`stage_defaults`), and `production`: `limit`, `instances`, `refusal` (why one more production instance is refused, or `null`); without `repo` and `name`, a name for plain Odoo with a random part (`odoo-19-0-community-3f9a`), new on each call |
| `GET /instance-quota` | `instances.view` | | 200, how many instances the caller may still create, and when ([Limits](#11-limits)) |
| `GET /repositories/modules?url=&branch=` | `instances.create` (in a project's branch: there) | | 200, the Odoo modules of the branch (for API users; the panel's form does not use it), read without creating anything ([Choosing the modules](#choosing-the-modules)); 404 when GitHub has no such branch, 502 when the repository cannot be read |
| `GET /odoo-apps?version=&edition=` | `instances.create` | | 200, the standard Odoo apps offered for that version and edition: `[{"name", "title", "enterprise"}]`; `meta.max_modules`: how many modules a new instance may get |
| `GET /jobs?instance=&limit=30&offset=0` | `instances.view` | | 200, recent jobs, newest first |
| `GET /jobs/<id>` | `instances.view` | | 200, the job: its steps with their progress, notes, result and `failure`; never its technical log, which stays in the server's log (`docker logs sudoo-panel`) |
| `GET /versions` | `instances.view` | | 200, `[{"version": "19.0", "enterprise": true}, ...]` |
| `GET /instances/<name>/domains` | `instances.view` | | 200, the instance's custom domains and how to set them up: `mode` (`off`, `acme`, `external`), `limit`, `default_url`, `target` (`{"host", "record_type"}` or `null`), `exposure`, `listener_error`, `authority` (`production`, `staging`, `custom`), `domains`: each with `domain`, `primary`, `status` (`waiting_dns`, `issuing`, `active`, `renewing`, `expiring`, `failed`), `reason`, `tls`, `dns_records` (`[{"type", "name", "value"}]`), `addresses`, `added_at`, `added_by`, `checked_at`, `next_check_at`, `renews_at`, `certificate` (`issued_at`, `expires_at`, `issuer`, `staging`), `url`, `moved_from` (the server the domain moved here from with its instance, or `null`) and `dns_pending` (`true` while a moved domain's DNS does not point at this server yet: `reason` says where to point it) ([Administrator Guide](ADMIN_GUIDE.md#19-custom-domains-and-certificates)) |
| `POST /instances/<name>/domains` | `domains.manage` | `{"domain": "erp.client.com"}`; a production instance also `"confirm_production": true` | **201**, the domain, `Location`; 400 for a name that cannot be used, 409 when custom domains are off, the domain is taken or the instance has `DOMAINS_PER_INSTANCE` |
| `PATCH /instances/<name>/domains/<domain>` | `domains.manage` | `{"primary": true}` (production: also `"confirm_production": true`) | 200, the domain: it is the primary one now |
| `POST /instances/<name>/domains/<domain>/check` | `domains.manage` | | 200, the domain: checked (and issued) now instead of at its next attempt |
| `DELETE /instances/<name>/domains/<domain>` | `domains.manage` | production: `{"confirm_production": true}` | **204**; its certificate and key are deleted, the next domain becomes primary |
| `GET /instances/<name>/mail?q=&limit=50&offset=0` | `caught_mail.view` | | 200, the e-mails the mail catcher caught from this staging or development instance, newest first: `id`, `received_at` (seconds), `subject`, `from`, `to`, `cc`, `mail_from`, `rcpt_to`, `date`, `size`, `has_html`, `attachments`, `snippet`; `q` searches subject, addresses and text; `meta`: `total`, `count`, `limit` (1 to 200), `offset`, `catcher` (`running`, `exists`), `usage` (`messages`, `bytes`), `limits`, `release`; **409** for a production instance ([Admin guide](ADMIN_GUIDE.md#23-mail-catcher)) |
| `GET /instances/<name>/mail/<id>` | `caught_mail.view` | | 200, the e-mail with `headers` (`[[name, value]]`), `text` and `attachment_list` (`index`, `filename`, `content_type`, `size`) |
| `GET /instances/<name>/mail/<id>/html` | `caught_mail.view` | | 200, its HTML (`text/html`) with its own Content-Security-Policy (no scripts, forms or remote content; `sandbox`), for a sandboxed frame of the panel; 404 when it has none |
| `GET /instances/<name>/mail/<id>/raw` | `caught_mail.view` | | 200, the message as received, as a download (`.eml`) |
| `GET /instances/<name>/mail/<id>/attachments/<index>` | `caught_mail.view` | | 200, one attachment, as a download (`application/octet-stream`) |
| `DELETE /instances/<name>/mail/<id>` | `caught_mail.manage` | | **204**; audit-logged |
| `DELETE /instances/<name>/mail` | `caught_mail.manage` | | **204**, every caught e-mail of the instance deleted; audit-logged |
| `POST /instances/<name>/mail/<id>/release` | `caught_mail.manage` | `{"to": "qa@example.com"}` | 200, `{"released", "to", "id"}`: a copy was sent from the panel's own address (`MAIL_FROM`), audit-logged; 400 for an address, **403** when `MAIL_CATCHER_RELEASE` is off, 503 when the panel cannot send e-mail |

### Database upgrades

An instance's database moved to a newer Odoo version ([Database upgrades](ADMIN_GUIDE.md#25-database-upgrades)).
Reading needs `instances.view`; everything else `instances.upgrade`. A production instance also needs
`"confirm_production": true` to be upgraded or to cancel its upgrade. For an Enterprise database (Odoo S.A.'s upgrade
service) the test and the upgrade also need `"subscription_code"` (the database's Odoo Enterprise subscription code,
passed to the job only: never stored, never logged) and `"accept_odoo_terms": true` (the service is used under the
customer's subscription and Odoo's terms, `terms_url`).

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /instances/<name>/upgrade` | `instances.view` | | 200: `from_version`, `edition`, `method` (`enterprise` or `openupgrade`), `allowed`, `targets` (the next version first), `skips_versions`, `terms_url`, `refusal`, `state` (`checked`, `testing`, `tested`, `test_failed`, `promoting`, `promoted`, `promotion_failed`, `rolled_back`), `target`, `branch` (the upgrade branch, when the repository has it), `report` (the pre-check), `staging` (the test instance), `tested_at`, `promotion` (`job`, `at`, `by`, `backup`, `rollback_until`, ...), `rollback_open`, `rollback_minutes`, `history` |
| `POST /instances/<name>/upgrade/check` | `instances.upgrade` | `{"target": "19.0"}` (the next version when left out) | 200, the upgrade with its `report`: `installed`, `modules` (each `name`, `version`, `target_version`, `status`: `ready`, `version_mismatch`, `unknown_version`, `not_installable`, `missing`), `branch_exists`, `warnings` and `blocking` (each `code`, `text`) |
| `POST /instances/<name>/upgrade/test` | `instances.upgrade` | `{"target"}` and, for Enterprise, `subscription_code`, `accept_odoo_terms` | **202**, the job: a new staging instance on the target version, or the test run again on the one there is; 409 without a pre-check of that target or with a blocking finding |
| `POST /instances/<name>/upgrade/promote` | `instances.upgrade` | `confirm_production` (production), and for Enterprise `subscription_code`, `accept_odoo_terms` | **202**, the job "Upgrade to Odoo <version>"; 409 before a successful test, or when the repository has no upgrade branch |
| `POST /instances/<name>/upgrade/cancel` | `instances.upgrade` | `confirm_production` (production) | **202**, the job: back to the backup taken before the upgrade; 409 after `UPGRADE_ROLLBACK_MINUTES` |
| `POST /instances/<name>/upgrade/keep` | `instances.upgrade` | | 200: the rollback window ends now |
| `DELETE /instances/<name>/upgrade` | `instances.upgrade` | | **204**: the upgrade is forgotten (its test instance stays); 409 while it runs or can be cancelled |

### Projects

A project is one GitHub repository; every instance of a repository belongs to its project (`project` of an
instance; `null` for plain Odoo). See [Projects and branches](ADMIN_GUIDE.md#24-projects-and-branches).

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /projects` | `instances.view` | | 200, every project: `id`, `name`, `repository` (`full_name`, `url`), `odoo` (`version`, `edition`), `origin` (`linked`, `created`, `adopted`), `created_at`, `created_by`, `branches` (`total` and the count per stage of the branches it knows), `instances` (`total`, `by_status`, `names`), `attention` (instances with a failed update, changes made by hand or a deleted branch), `production_branches`, `options` (what applies), `own_options` (set for the project), `access` (`mode`: `panel_roles` or `restricted`, `grants` (how many), `restricted_branches`, `locked_branches`); `meta.standalone`: the instances in no project (plain Odoo). Only the projects, branches and instances the caller may see are listed |
| `GET /projects/repository-owners?refresh=1` | `projects.manage` | | 200, each account the GitHub App is installed on: `account`, `account_type`, `possible`, `reason` (`ok`, `no_app`, `not_installed`, `suspended`, `personal_account`, `permission_missing`), `message`, `url` and `label` (where to fix it), `accept_url` (where an owner accepts a new permission); `refresh=1` reads the installations from GitHub now |
| `POST /projects` | `projects.manage` | link: `{"repository": "https://github.com/acme/erp", "odoo_version": "19.0", "edition": "community", "name"?}`; or create a new, empty repository (also `github.manage`): `{"create": {"owner": "acme", "name": "erp", "private": true, "description"?}, "odoo_version", "edition", "name"?}` | **201**, the project (`origin` `linked` or `created`) and `default_branch`, `Location`; 400 when the server cannot read the repository (the GitHub App's explanation) or the name is not one GitHub accepts, 409 when it has a project already, the name is taken on GitHub, or the app may not create repositories there (`detail` says what to do) |
| `GET /projects/<id>?refresh=1` | `instances.view` | | 200, the project with `branches_list`: each branch's `name`, `stage`, `default`, `on_github` (`null` when GitHub could not be read), `commit` (`sha`, `full_sha`, `subject`, `when`, `author`), `instances`, `auto_created`, `odoo_version` (the branch's own Odoo version; `null`: the project's), `lock` (`locked_by`, `locked_at`, `reason`, `allow`; `null`), `restricted`, `permissions` (what the caller may do in that branch); production first; branches hidden from the caller are left out. 404 for a project the caller may not see. `meta`: `permissions` (in the project as a whole), `default_branch`, `branches_error`, `branches_read_at` (GitHub is read at most every 30 seconds; `refresh=1` reads it now), `capacity` (`comfortable`, `running`, `headroom`, `warning`, `message`, `overloaded`), `production_limit`, `server_production_limit`, `commit_statuses` (`available`: whether the GitHub App may write commit statuses in the repository) |
| `PATCH /projects/<id>` | `projects.manage` | any of `name`, `odoo_version` and `edition` (for new instances), `options`: `{"auto_development": false, "max_auto_development": 5, "development_demo_data": true, "staging_on_push": "rebuild" or "keep_data", "branch_deleted": "keep", "archive" or "delete", "development_lifetime_days": 7, "staging_lifetime_days": 30, "development_on_new_commit": "update", "new_build" or "nothing", "module_install": "repository", "all" or "list", "modules": "sale_custom,stock_custom", "test_suite": "off", "selection" or "custom", "test_tags": "/sale_custom,-slow", "staging_tests": false, "commit_statuses": true, "email_pusher": true, "pr_previews": false, "pr_preview_limit": 3, "pr_previews_forks": false}`; `null` follows the panel's setting again | 200, the project; 400 for an unknown option or value, a module name or test tag Odoo would not accept, `module_install` `list` without `modules`, or `test_suite` `custom` without `test_tags` |
| `DELETE /projects/<id>` | `projects.manage` | `{"confirm": "<id>"}` | **204**; 409 while it has instances. The repository on GitHub is never touched |
| `PUT /projects/<id>/branches/<branch>/stage` | `projects.manage` | `{"stage": "production", "confirm_production": true}` | 200, the branch; every instance of it moves to the stage (the checks of `PATCH /instances/<name>`). Making a branch production, or moving it out of production, needs `"confirm_production": true` (400), and moving it out also `instances.delete` (403); **409** beyond `PROJECT_PRODUCTION_BRANCHES` in the project or `MAX_PRODUCTION_INSTANCES` on the server, and when the pre-promotion checklist has red items (below) unless the body has `"promote_anyway": true` (audit-logged as "promotion checklist overridden"; never while `PROJECT_PROMOTION_BLOCKS` is off). A branch name may contain `/` |
| `GET /projects/<id>/branches/<branch>/promotion-check?refresh=1&mode=merge` | `instances.view` in the branch | | 200, the [pre-promotion checklist](ADMIN_GUIDE.md#promoting-to-production) of the branch going into production (`mode=merge`: being merged into production's branch on GitHub, without the capacity item): `branch`, `mode` (`move` or `merge`), `production` (`branch`, `instance`; `null` when there is none), `items`: each `key` (`build`, `ci` (only with the [repository guard](#repository-guard)'s checks on: `ci` has `state`, `total`, `failed`, `pending`, `url`), `backup`, `modules`, `migrations`, `odoo`, `lock`, `capacity`), `status` (`ok`, `warning`, `blocked`), `text` (the explanation) and its facts (`build`: `id`, `status`, `at`, `commit`, `tests`; `backup`: `file`, `created`, `instance`, `max_hours`, `busy`; `modules`: `added`, `removed`, `changed`, `known`, with `ahead` and `behind`; `migrations`: `module`, `version`, `scripts`; `branch_series` and `production_series`; `branches` (locked)); `blocked` (the keys of the red items), `blocking` (whether they stop the move), `compare` (`ahead`, `behind`, `files_changed`, `github_url`, `source`; `null` when the branches could not be compared), `checked_at`. `refresh=1` reads GitHub again |
| `GET /projects/<id>/compare?base=<branch>&head=<branch>&refresh=1` | `instances.view` in both branches | | 200, what merging `head` into `base` brings: `ahead`, `behind`, `commits` (of `head` not in `base`, newest first, at most 50: `sha`, `full_sha`, `subject`, `author`, `when`), `files_changed`, `modules` (`added` and `removed`: `name`, `version`; `changed`: `name`, `from`, `to`, `bump` `up`, `down`, `same` or `unknown`; `known`: false when only GitHub's compare API answered, without versions), `migrations` (`module`, `version`, `scripts`), `source` (`git` or `github`), `base` and `head` (`branch`, `sha`), `github_url`, `odoo_series`. 400 for the same branch twice, 404 when GitHub has no such branch, 502 when the repository cannot be read |
| `GET /projects/<id>/staging-refresh` | `instances.view` | | 200, the [scheduled staging refresh](ADMIN_GUIDE.md#scheduled-staging-refresh): `frequency` (`off`, `daily`, `weekly`), `time` (`HH:MM` in the panel's time zone), `weekday` (0 Monday to 6 Sunday), `branches` (empty: every staging branch), `last_run` (the last refresh that started a rebuild, null before the first), `next_run`, `changed_by`; `meta.branches`: the staging branches and their staging instances, `meta.timezone` |
| `PUT /projects/<id>/staging-refresh` | `projects.manage` and `instances.restore` | `{"frequency": "weekly", "time": "03:00", "weekday": 6, "branches": ["staging"]}` | 200, the schedule; a time already past waits for the next one. 400 for a value it does not accept |
| `POST /projects/<id>/staging-refresh/run` | `instances.restore` | | **202** (200 when nothing started), `{"started": [instances], "skipped": [{"instance", "reason"}]}`: each started instance has a job "Scheduled refresh from production"; 403 when production's branch is locked against the caller |
| `GET /projects/<id>/previews` | `instances.view` | | 200, the project's [pull-request previews](ADMIN_GUIDE.md#pull-request-previews), newest first: `number`, `title`, `url`, `branch`, `repository`, `fork`, `head_sha`, `instance` (`null` when there is none or it is hidden from the caller), `instance_exists`, `existing` (the branch's instance from before the pull request: never deleted for it), `status` (`creating`, `building`, `ready`, `failed`, `deleted`, `refused`), `reason`, `commented`, `opened_at`; `meta`: `enabled`, `limit`, `forks`, `app_connected`, `can_comment` (the GitHub App may comment on pull requests), `created` |
| `POST /projects/<id>/branches/<branch>/instances` | `instances.create`; staging also `instances.restore` | `{"name"?, "demo"? (development only; the project's option by default), "install_modules"? (true), "modules"? (more modules, [Choosing the modules](#choosing-the-modules)), "odoo_version"? (one of `GET /versions`)}` | **202**, the creation's job: an instance of the branch in the branch's stage, with the branch's Odoo version (`"odoo_version"` when given, which the branch keeps from then on; else the branch's own; else the project's) and the project's edition and lifetime. Staging starts from a neutralised copy of the project's production instance (**409** when it has none, or another Odoo version or edition; **403** when the caller may not restore production's instance); a staging branch with its own Odoo version of another series than production's starts with an empty database instead. **400** for a version the panel does not offer; **409** when the branch has an instance, or one is being created for it (the panel's own for a new branch included), unless the body has `"another": true`; 404 when GitHub has no such branch |
| `POST /projects/<id>/branches/<branch>/rebuild` | `instances.restore` | `{"instance"?}` (the branch's first instance by default) | **202**, the job "Rebuild from production"; 409 for an instance that is not staging, or a project without a production instance; 403 when production's branch is locked against the caller, or the caller may not restore production's instance |
| `GET /projects/<id>/builds?per_branch=10` | `instances.view` | | 200, per branch the caller may see (production first): `branch`, `stage`, `builds`: its last `per_branch` builds (1 to 200, default 10), newest first. A build: `id`, `type` (`build`), `project`, `branch`, `instance`, `trigger` (`create`, `push`, `manual`, `rebuild`), `status` (`building`, `success`, `warning`, `failed`, `dropped`), `commit` (`sha`, `full_sha`, `subject`, `author`, `when`; `null` until known), `by`, `at`, `queued_at`, `started_at`, `finished_at`, `duration_seconds`, `dropped_at`, `result` (a sentence), `messages` (Odoo's warning and error lines), `tests` (`null` when the build ran none; else `status`, `tests`, `failed`, `errors`, `warnings` and `modules`, the modules with a failing test), `action`, `job`, `failure` (`null` unless it failed; as a job's `failure`), `notified` (a failed build of a push: `{"emailed": true, "at"}`, or `{"emailed": false, "reason"}` with `no_address`, `option_off`, `already` or `mail_failed`; else `null`) and `links` (`self`, `job`, `instance`; a job is kept in memory for the last 300 jobs). `meta`: `per_branch`, `kept_per_branch` (`PROJECT_BUILDS_KEPT`). See [Build history](ADMIN_GUIDE.md#build-history) |
| `GET /projects/<id>/branches/<branch>/options` | `instances.view` in the branch | | 200, the branch's build options ([Build options and tests](ADMIN_GUIDE.md#build-options-and-tests)): `project`, `branch`, `stage`, `options` (what applies: `on_new_commit` `update`, `new_build` or `nothing`; `module_install` `repository`, `all` or `list`; `modules`; `test_suite` `off`, `selection` or `custom`; `test_tags`; `demo_data`), `sources` (where each comes from: `branch`, `project`, or why it is forced: `production`, `module_install`, `empty_list`, `empty_tags`), `own` (the values the branch sets), `project_defaults` (what it gets without them), `changed_by`, `changed_at`, `commit_statuses` (`enabled`: the project's option, `available`: the GitHub App may write statuses, `context`: `sudoo/<stage>`) |
| `PUT /projects/<id>/branches/<branch>/options` | `projects.manage` in the branch | any of `on_new_commit`, `module_install`, `modules` (comma-separated or a list), `test_suite`, `test_tags`, `demo_data`; `null` follows the project again; keys left out are kept | 200, the branch's build options as above; 400 for an unknown key or value, a module name or test tag Odoo would not accept, `list` without modules or `custom` without tags. Production branches keep `update` and no tests whatever they set |
| `GET /projects/<id>/builds/<build_id>` | `instances.view` | | 200, one entry of a branch's history (a build, a stage change or a restore); 404 when the project has none with that id, or its branch is hidden from the caller |
| `GET /projects/<id>/branches/<branch>/history?limit=50&offset=0&commits=0` | `instances.view` in the branch | | 200, the branch's timeline, newest first, `limit` (1 to 200) entries from `offset`: builds as above, stage changes (`type` `stage`: `from`, `to`, `by`, `at`, `explanation` (what the new stage does), `reason` (`null` for a change of the branch's stage; else what made it, such as a database upgrade)) restores (`type` `restore`: `instance`, `status` `success` or `failed`, `action`, `result`, `job`), scheduled staging refreshes (`type` `refresh`: the same fields; `result` lists what was rebuilt and what was skipped) and, with `commits=1`, the commits recorded for a branch that builds nothing on new commits (`type` `commit`: `instance`, `status` `skipped`, `commit`, `result`). A build's `result` and `messages` include its tests: a failed test makes it `failed` with "At least one test failed when loading the modules (module_x)". `meta`: `total`, `limit`, `offset` |
| `GET /projects/<id>/access` | `projects.access` | | 200, `{"mode", "grants": [{"type": "team" or "user", "id", "level", "name"}], "branches": {"<branch>": {"restricted", "grants"}}, "locks": {"<branch>": {"locked_by", "locked_at", "reason", "allow": [{"type", "id", "name"}]}}, "levels"}`; `name` is `null` for a team or account that no longer exists |
| `PUT /projects/<id>/access` | `projects.access` | `{"mode": "panel_roles" or "restricted", "grants": [{"type", "id", "level"}], "branches": {"<branch>": {"restricted": true, "grants": [...]}}}`; `level` is `none`, `view`, `developer`, `maintainer` or `admin` | 200, the access (locks unchanged); 400 for an unknown team or account; **409** when the change would leave the caller without `projects.access` in the project |
| `PUT /projects/<id>/branches/<branch>/lock` | `projects.access` in the branch; to change an existing lock, also allowed by it (super admins always) | `{"reason"?, "allow": [{"type": "team" or "user", "id"}]}` | 200, the lock; the caller is always added to `allow`. 404 for a branch the project does not know |
| `DELETE /projects/<id>/branches/<branch>/lock` | `projects.access`, allowed by the lock (super admins always) | | **204**; 404 when the branch is not locked |
| `POST /projects/<id>/github/check` | `projects.manage` | `{}` | 200, `status` (`ok`, `warning` or `error`), `checked_at` and `checks`: one per part of the link to GitHub, in order, each `{"id", "status", "message", "fix"}` with `status` `ok`, `warning`, `error` or `skipped` and `fix` `{"url", "label"}` or `null`. `id`: `repository` (the server can read it), `installation` (the GitHub App is installed on its owner, not suspended, and covers it), `permissions` (Contents and Metadata: read; Commit statuses: write and the push, create and delete events are recommended), `webhook` (the app's webhook goes to this panel; the latest deliveries for this repository, or the app's, arrived), `submodules` (each submodule of the default branch can be read). Nothing is changed; audit-logged ([Admin guide](ADMIN_GUIDE.md#checking-a-projects-connection)) |

Inside a project every permission is the caller's role ∩ the level the project (or the branch) gives them, and a
locked branch takes every project permission but `instances.view` from whoever its lock does not allow
([Security](SECURITY.md#teams-project-access-and-locked-branches)). This applies to every endpoint about an instance
(`/instances/<name>/...`, its jobs, backups, off-site copies, shell tickets): an instance hidden from the caller
answers 404 like one that does not exist, a refused permission 403 with the reason, and lists leave hidden ones out.
`GET /instances/<name>` has `meta.permissions`, what the caller may do in it, and every instance has `lock`.

### Repository guard

A project's [repository guard](ADMIN_GUIDE.md#repository-guard): CI checks added through a pull request, their
results, and branch rules on GitHub. Changes need `projects.access` in the project (its Admins and super admins; never
Sudoo.sh support) and are audit-logged. GitHub refusing answers 409 (a permission or plan) or 502 (unreachable) with
GitHub's reason.

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /projects/<id>/guard` | `instances.view` | | 200, `checks` (`enabled`, `lint`, `linter` `ruff` or `flake8`, `pylint_odoo`, `manifest`, `xml_csv`, `tests`, `strictness` `relaxed`, `standard` or `strict`), `block_promotion`, `own` (the values the project chose; the rest follows the `PROJECT_GUARD_*` settings), `defaults` (those settings, as `checks`, `rules`, `block_promotion`), `rules` (`enabled`, `method` `auto`, `rulesets` or `branch_protection`, `production` and `staging`: `approvals`, `require_checks`, `linear_history`, `signed_commits`, production also `code_owner_review`, staging also `enabled`; `pushers`: `mode` `anyone`, `roles` or `named`, `teams`, `users`), `pull` (the panel's pull request: `number`, `url`, `state` `open`, `closed` or `merged`, `purpose`, `files`, `by`, `at`; `null`), `applied` (`method`, `rulesets`, `protections`, `by`, `at`; `null`), `versions`, `required_check`, `files`, `may_change`, `github` (`app_connected`, `installed`, `permissions`: each `key`, `level`, `feature`, `why`, `granted`, `ok`; `missing` per feature; `app_permissions_url`, `installation_url`, `install_url`). Nothing is read from GitHub |
| `PATCH /projects/<id>/guard` | `projects.access` | `{"block_promotion": true}` | 200, the guard as above |
| `POST /projects/<id>/guard/checks/preview` | `projects.access` | `{"checks": {...}}` (keys left out keep their value) | 200, `files`: each `path`, `action` (`add`, `update`, `remove`, `unchanged`, `kept`: written by someone else, left alone), `content`, `reason`; `base`, `note`, `changes`. Nothing changes |
| `PUT /projects/<id>/guard/checks` | `projects.access` | `{"checks": {...}, "dry_run"?: true}` | 200, the files as above, `pull`, `result` (`opened`, `updated`, `up_to_date`: nothing to change, an open pull request of the panel is closed; `dry_run`); 400 for an unknown key or value; 409 when the GitHub App lacks Contents, Pull requests or Workflows: write |
| `POST /projects/<id>/guard/pull/refresh` | `projects.manage` | | 200, the pull request as GitHub has it now |
| `GET /projects/<id>/guard/results?sha=<sha>,<sha>` | `instances.view` | | 200, `{"<sha>": {"state", "total", "failed", "pending", "runs", "url"}}` (`state`: `success`, `failure`, `pending`, `none` or `unknown`) for up to 30 full commit hashes, read from GitHub at most once a minute; `{}` while the checks are off or the app lacks Checks: read |
| `POST /projects/<id>/guard/rules/preview` | `projects.access` | `{"rules": {...}, "turn_off"?: true}` | 200, `repository`, `method`, `available`, `limitations` (`reason`: `plan`, `people_in_rulesets`, `personal_repository`, `roles_in_protection`, `method_changed`; `message`), `targets` (each `key`, `kind` `ruleset` or `protection`, `name`, `action` `create`, `update`, `delete`, `unchanged` or `blocked`, `changes`: `field`, `label`, `before`, `after`), `untouched` (rules the panel did not make), `branches`, `affects_production`, `changes`. Nothing changes |
| `PUT /projects/<id>/guard/rules` | `projects.access` | `{"rules": {...}, "confirm": "<owner/repo>", "dry_run"?: true}` | 200, the plan as above with `result` `applied` (or `dry_run`) and `applied`; 400 without the repository's name when production changes; 409 when GitHub's plan does not allow branch rules or the app lacks Administration: write |
| `DELETE /projects/<id>/guard/rules` | `projects.access` | `{"confirm": "<owner/repo>", "dry_run"?: true}` | 200, the plan with `result` `removed`: only the panel's rulesets and branch protection are removed |

### The plan

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /plan` | `instances.view` | | 200, `plan`, `source` (`hub`, `grace`: the hub cannot be reached and the last licence applies, `expired`: no new production branches until it is heard again, `unlinked`, `default`: the Free plan), `linked`, `account_name`, `received_at`, `valid_until`, `entitlements` (`max_production_branches`, `max_servers`, `max_projects` (`null`: unlimited), `teams`, `features` (`per_project_access`, `custom_domains`, `offsite_backups`), `support_level`, `addons`, and `period`, `renews_at`, `trial_plan` when the hub says them), `production_withheld`, `banned`, `over_limit`, `usage` (`servers`, `projects`, `production_branches`, `production_branch_names`, `instances_by_stage`), `production_refusal` (why one more production branch would be refused, or `null`), `production_keep`, `leaving_production`, `links` (`pricing`, `account`, `keep`), `link_deadline` (for super admins and administrators, `null` for everyone else: `linked`, `first_run`, `deadline_days`, `deadline`, `days_left`, `restricted`: the panel was not linked to a Sudoo.sh account within `LINK_DEADLINE_DAYS` of its first run, so new instances, moves into production and updates are refused until it is) |

A request the plan does not allow answers 403 with a sentence naming the plans that have it and the addresses of the
pricing page and the account site (`POST /teams`, `PATCH /teams/<id>` beyond taking members out, `PUT /projects/<id>/access`
beyond clearing it, `POST /instances/<name>/domains`, `PATCH /settings` turning `offsite_backups` on, `POST
/offsite/test`); one more production branch beyond the plan, while production is withheld or once the licence
expired answers 409 with the reason. While the hub answers 429 for the account's daily API quota, the marketplace
endpoints answer **429** with `Retry-After` (seconds to midnight UTC) and say when they work again.

### Support access

Letting Sudoo.sh support into the panel for a while ([Admin guide](ADMIN_GUIDE.md#26-support-access)). Granting,
withdrawing and ending are for people only: an API token is refused with 403.

| Request | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /support-access/status` | signed in | | 200, `connected`, `sessions` (`id`, `grant_id`, `staff_name`, `opened_at`, `expires_at`), `active_grants`, `can_end` |
| `GET /support-access` | `support_access.manage` | | 200, `enabled`, `unavailable` (`off`, `not_connected` or `null`), `max_hours`, `durations`, `levels`, `shell_enabled`, `grants` (active first, then the last 20: `id`, `level`, `scope`, `ticket`, `hours`, `created_by`, `created_at`, `expires_at`, `revoked_at`, `revoked_by`, `state`, `sessions`), `sessions`, `channel` (`last_ok`, `error`) |
| `POST /support-access/grants` | `support_access.manage` | `{"level": "view" \| "admin" \| "admin_shell", "hours": 1, "scope": {"all": true} or {"projects": [ids], "instances": [names]}, "ticket": "T-1042"}` | **201**, the grant; 400 for a choice that is not offered or a project or instance you do not see; 409 when support access is off or the panel is not connected to Sudoo.sh |
| `DELETE /support-access/grants/<id>` | `support_access.manage` | | **204**: withdrawn at once |
| `POST /support-access/end` | `support_access.manage` | | **204**: every grant withdrawn, every support session ended ("End now") |

Audit entries (`GET /audit`) carry `support: true` when Sudoo.sh support made them.

### Servers

Several servers managed from one panel, a premium feature of Business plans ([Admin guide](ADMIN_GUIDE.md#27-servers);
without it these answer **402**). `main` is the main server's id. Every instance has `server` (`id`, `name`,
`region`, `online`); requests about an instance of another server (`/instances/<name>/...`, its jobs) are answered by
that server's agent with the same shapes, and an instance whose server is offline has the status `unreachable`.

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /servers` | `instances.view` | | 200, every server: `id`, `name`, `region`, `main`, `status` (`waiting`, `joined`, `active`, `revoked`), `online`, `last_seen_at`, `version`, `instances`, `capacity` (`max_instances`), `room`, `metrics`; `meta`: `limit` (the plan's servers, the main one included; null: no limit), `counted`, `refusal`, `panel_url` |
| `GET /servers/<id>` | `instances.view` | | 200, the server; with `servers.manage` also `fingerprint`, `facts`, who joined, approved or revoked it and when |
| `POST /servers` | `servers.manage` | `{"name", "region"?, "capacity"?: {"max_instances": n or null}}` | **201**, the server with `join_code` and `install_command` (shown once); 403 beyond the plan's servers |
| `PATCH /servers/<id>` | `servers.manage` | any of `name`, `region`, `capacity` (`main`: name and region) | 200, the server |
| `POST /servers/<id>/join-code` | `servers.manage` | | 200, a new `join_code` for a server that has not joined |
| `POST /servers/<id>/approve` | `servers.manage` | `{"fingerprint"}`: what the server printed | 200; 400 when it is not the fingerprint of the key that joined |
| `POST /servers/<id>/revoke` | `servers.manage` | `{"confirm": "<name>"}` | 200; the agent's channel closes and its key is refused |
| `DELETE /servers/<id>` | `servers.manage` | `{"forget_instances"?: true}` | **204** for a revoked server or one that never joined |
| `POST /instances/<name>/move` | `instances.move` | `{"server", "confirm_production"?: true}` | **202**, the move's job ([Admin guide](ADMIN_GUIDE.md#moving-an-instance)) |

`POST /instances` and `POST /projects/<id>/branches/<branch>/instances` take `"server"` (default: the project's
`default_server`, else `main`; another server without the feature answers 402); `GET /instance-plan` lists the
servers a new instance may go to (`servers`, the main server first); `PATCH /projects/<id>` takes
`"default_server"`. The agents use `POST /servers/join` and `GET /servers/agent-packages[/<id>]` (no session: a join
code or the agent's signature).

### Teams

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /teams` | `teams.manage` or `projects.access` | | 200, every team: `id`, `name`, `description`, `members` (`name`, `display_name`, `role`), `projects` (`id`, `name`: the projects whose access or locks name it), `created_at`, `created_by`, `changed_at`, `changed_by` |
| `GET /teams/<id>` | `teams.manage` or `projects.access` | | 200, the team |
| `POST /teams` | `teams.manage` | `{"name", "description"?, "members"?: ["<account>"]}` | **201**, the team; 409 when another team has the name (case aside), 400 for an unknown account |
| `PATCH /teams/<id>` | `teams.manage` | any of `name`, `description`, `members` | 200, the team |
| `DELETE /teams/<id>` | `teams.manage` | | **204**; the team leaves every grant and lock |

### Server, metrics and settings

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /server` | `instances.view` | | 200, the panel's version and address, the plugins in use, `public_address` (`mode`: `direct`, `proxy`, `tunnel` or the plugin's name; `url`; `domain`: the panel's name or `null`; `status` of its certificate: `waiting`, `active`, `failed` or `null`; `reason`; `server_addresses`), the server's figures, the number of live-event clients |
| `POST /server/restart` | `settings.manage` | `{}` | **202** `{"restarting": true}`: the backend stops and Docker starts it again within seconds; the web app, the connectors and the instances keep running. **409** while jobs run |
| `GET /metrics` | `instances.view` | | 200, the latest figures of the server and every instance, and the capacity estimate ([Metrics](#9-metrics)) |
| `GET /server/managed-subdomains` | `instances.view` | | 200, managed subdomains: `setting` (`MANAGED_SUBDOMAINS`), `linked`, `enabled` (Sudoo.sh offers them), `active`, `base_domain`, `company`, `limit`, `used`, `held` (the names this panel holds, for every server), `other_servers` (of those, the names other servers serve), `checked_at`, `error`, `pending_release`, `addresses` (the public addresses sent) |
| `POST /server/managed-subdomains/sync` | `settings.manage` | | 200, the same after comparing the instances with their names now; 409 when the panel is not linked |
| `GET /server/capacity` | `instances.view` | | 200, how many instances the server can run at once, how many run, the panel's own use, disk headroom ([Capacity](#capacity)); **503** before the first measurement |
| `GET /metrics/history?minutes=60` | `instances.view` | | 200, up to one hour of figures, in columns |
| `GET /instances/<name>/metrics?minutes=60` | `instances.view` | | 200, one instance's latest figures and history |
| `GET /settings` | `settings.manage` | | 200, every setting ([Settings](#7-settings)) |
| `PATCH /settings` | `settings.manage` | `{"idle_hours": 4}` | 200, the settings |
| `DELETE /settings/<key>` | `settings.manage` | | **204**, back to the `.env` value |
| `POST /settings/test-email` | `settings.manage` | `{"to": "you@example.com"}` | 200 `{"sent_to"}`; 400 when e-mail is not set up, 502 with the mail server's answer when sending fails |
| `GET /licence/text` | public | | 200, the licence agreement this release bundles: `version`, `effective`, `sha256`, `url`, `version_url`, `statements`, `summary_html`, `summary_ar_html`, `html` ([Administrator Guide](ADMIN_GUIDE.md#22-the-licence-agreement)) |
| `GET /licence` | signed in | | 200, `bundled_version`, `required_version`, `pending`, `update_needed`, `accepted` (the latest acceptance: version, time, who, declared use); with `settings.manage` also `history`: every acceptance with its address, browser and `synced_at`, and the installer's |
| `POST /licence/acceptances` | a super admin's session | `{"accepted": true, "version", "sha256", "use": "personal"\|"company"\|"service_provider", "on_behalf", "language"}` | **201**, the licence state; 400 when the version or SHA-256 is not the bundled text's, or a company use without `"on_behalf": true`. While acceptance is pending, every other endpoint answers a super admin's session with 403 |
| `GET /alerts?limit=50` | `audit.view` | | 200, the newest alerts first (1 to 200; the bell of the web app), see below |
| `POST /alerts/test` | `settings.manage` | `{"channel": "email"}` or `{"channel": "webhook", "webhook": 1}` | 200 `{"alert", "sent_to"}` or `{"alert", "webhook", "status"}`, sent with the saved settings; **400** when there is nothing to send to (e-mail not set up, no recipient, the webhook has no address), **502** when the delivery failed, with the reason |
| `GET /events` | signed in | | the live event stream ([Live events](#8-live-events)) |
| `GET /mail-templates` | `mail.manage` | | 200, every e-mail the panel sends (below); `meta.mail_ready` says whether e-mail is set up |
| `GET /mail-templates/<key>` | `mail.manage` | | 200, one e-mail |
| `PUT /mail-templates/<key>` | `mail.manage` | `{"subject", "title", "body"}` | 200, the e-mail; **400** naming every problem: an unknown placeholder, one not written like `{{ name }}`, a block placeholder not alone on its line, a link that is not `https://`, `http://`, `mailto:` or a link placeholder, a missing required placeholder |
| `DELETE /mail-templates/<key>` | `mail.manage` | | **204**, back to the built-in text |
| `POST /mail-templates/<key>/preview` | `mail.manage` | `{"subject", "title", "body"}`, each optional (the current text) | 200, `{"subject", "html", "text", "problems"}` with sample values; with problems, the other fields are `null` |
| `POST /mail-templates/<key>/test` | `mail.manage` | as for preview | 200 `{"sent_to"}`: the preview sent to your own address, subject starting `[Test]`; 400 when e-mail is not set up or your account has no address, 502 when sending fails |

An e-mail as `GET /mail-templates` lists it ([Admin guide, E-mail templates](ADMIN_GUIDE.md#17-e-mail-templates)):

```json
{"key": "test", "name": "Test e-mail", "description": "...", "recipients": "...",
 "subject": "Sudoo.sh e-mail works", "title": "It works", "body": "Hello,\n\n...",
 "default": {"subject": "...", "title": "...", "body": "..."}, "customized": false, "changed_at": null,
 "changed_by": null, "placeholders": [{"name": "server", "description": "...", "kind": "text"}], "required": []}
```

`kind` is `text`, `link` (an address, usable as a link target) or `block` (drawn by the panel, alone on its line).
Keys: `sign_in_code`, `email_confirmation`, `sign_in_notice`, `invitation`, `alert`, `alert_digest`, `plugin_notice`, `test`.

An alert, as `GET /alerts` lists it and the `alert` live event carries it ([Admin guide, Alerts](ADMIN_GUIDE.md#16-alerts)):

```json
{"id": "3f9c2a1b7d4e", "key": "instance_down:shop-main", "type": "instance_down", "severity": "problem",
 "title": "shop-main is down", "text": "Instance shop-main is stopped, for 5 minutes now. ...", "hint": null,
 "details": [], "instance": "shop-main", "link": "/i/shop-main", "at": 1763366400.0, "last_at": 1763366400.0,
 "count": 1, "resolved_at": null, "delivery": ["e-mailed to 2 of 2 addresses", "webhook 1: delivered (HTTP 200)"]}
```

`type`: `job_failed`, `update_failed`, `backup_failed`, `instance_down`, `disk`, `memory`, `drift`, `public_link`,
`test`. `severity`: `problem`, `recovered` or `info`. `count` grows when the same alert repeats inside
`ALERT_QUIET_MINUTES` (it is not sent again); `last_at` is its latest time and the list's order. `link` is a path of
the web app. `resolved_at` is set when the problem is over. Webhooks are named by number, never by address.

### Accounts, roles and the audit log

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /roles` | signed in | | 200, `{"roles": [{"name", "label", "description", "permissions"}], "permissions": {"<name>": "<what it allows>"}}` |
| `GET /users` | `accounts.manage` | | 200, accounts (below), each with `second_factor_required` (`REQUIRE_SECOND_FACTOR` applies to it) |
| `POST /users` | `accounts.manage` | Only when `INVITATION_PASSWORD_FALLBACK` allows it (by default while e-mail is not set up; otherwise invite). `{"email", "role"}` required; optional `first_name`, `last_name`, `phone`, `name` (made from the e-mail address when absent), `password` (generated when absent), `must_change_password` (default `true`) | **201**, the account with its `password`, shown once |
| `GET /invitations` | `accounts.manage` | | 200, invitations (`id`, `email`, `first_name`, `last_name`, `role`, `invited_by`, `sent_count`, `expired`, `invited_at`, `sent_at`, `expires_at`); `meta`: `mail_ready`, `password_allowed` (`POST /users` is allowed: `INVITATION_PASSWORD_FALLBACK`), `invitation_hours` |
| `POST /invitations` | `accounts.manage` | `{"email", "role"}` required; optional `first_name`, `last_name` | **201**, the invitation; the link is e-mailed (never returned). 400 when e-mail is not set up, 429 beyond `INVITATIONS_PER_HOUR` |
| `POST /invitations/<id>/resend` | `accounts.manage` | | 200, the invitation, with a new link and expiry (the old link stops working) |
| `DELETE /invitations/<id>` | `accounts.manage` | | **204**: the link stops working |
| `POST /invitation` | public | `{"token"}` (the part of the link after `#`) | 200, `{"email", "first_name", "last_name", "role", "role_label", "invited_by", "expires_at", "second_factor_required"}`; **410** for a used, replaced, revoked or expired link (counted like a wrong password) |
| `POST /invitation/accept` | public | `{"token", "password", "first_name"}`; optional `last_name`, `phone` | **201**, signed in (as `GET /session`; `restricted: "second_factor"` when one is required); the link is used up |
| `PATCH /users/<name>` | `accounts.manage` | any of `role`, `first_name`, `last_name`, `email`, `phone` (`""` clears one), `running_limit` (the most non-production instances this person runs at once: a number 0 to 1000, 0 for no limit, `null` for `MAX_RUNNING_NON_PRODUCTION_PER_USER`; 400 otherwise); also for your own account | 200, the account; a new role ends its sessions |
| `POST /users/<name>/password` | `accounts.manage` | `{}` to generate one, or `{"password", "must_change_password"}` | 200, `{"password"}`, shown once; the account's sessions end |
| `DELETE /users/<name>/second-factors` | `accounts.manage` | | **204**: removes the account's second factors and recovery codes (a lost phone) and signs it out |
| `DELETE /users/<name>` | `accounts.manage` | | **204** |
| `POST /users/<name>/disable` | `accounts.manage` | `{"reason": "..."}` (required, up to 300 characters; 400 without it) | 200, `meta.changed` is false when it was already disabled (nothing changed, no reason kept); the account with `disabled`, `disabled_at`, `disabled_by`, `disabled_reason`: it cannot sign in, its sessions end and the API tokens it owns are refused (403). 400 for your own account and for the last active super admin; only a super admin changes a super admin |
| `POST /users/<name>/enable` | `accounts.manage` | `{"reason": "..."}` (required, up to 300 characters; 400 without it) | 200, the account, enabled again; the audit log keeps both reasons. `meta.changed` is false when it was already enabled (nothing changed, no reason kept) |
| `POST /access-requests` | public | `{"email", "first_name", "last_name"}` | **202**, always `{"received": true}`: the same for an allowed or other domain, a known address, a throttled one, or the feature off (`ACCESS_REQUEST_DOMAINS`). A confirmation link is e-mailed only when a request was made |
| `POST /access-requests/confirm` | public | `{"token"}` | 200 and the request waits for an administrator; 400 for a wrong, used or old link (48 hours), counted against the caller's address like wrong passwords |
| `GET /access-requests` | `accounts.manage` | | 200, confirmed requests newest first (`id`, `email`, `first_name`, `last_name`, `status` pending, approved or rejected, `reason`, `role`, `decided_by`, `requested_at`, `confirmed_at`, `decided_at`); `meta`: `domains`, `enabled`, `awaiting_confirmation` (how many requests have not been confirmed by e-mail yet: a count only, no names and no action) |
| `POST /access-requests/<id>/approve` | `accounts.manage` | `{"role"}` | 200: an invitation with that role is e-mailed (same limits as `POST /invitations`; only a super admin approves `super_admin`). 409 when already decided |
| `POST /access-requests/<id>/reject` | `accounts.manage` | `{"reason"}` | 200; the reason is required and stays in the list; the person is not e-mailed. 409 when already decided |
| `GET /audit?limit=200&offset=0&sort=-ts&...` | `audit.view` | | 200, audit-log entries (`ts`, `user`, `ip`, `event`, `ok`, `instance`?, `detail`?, `project`?, `branch`?, `support`), `limit` (1 to 2000) from `offset`, sorted by `sort` (`ts`, `user`, `event`, `instance`, `ip` or `ok`; a leading `-` for descending, the default `-ts`). Filters: `user`, `event`, `instance`, `ip` (exact), `ok` (`allowed` or `refused`), `since` and `until` (ISO 8601 or Unix time; `until` excluded), `project` and `branch` (a branch needs its project), `q` (text in any field). `meta`: `total`, `limit`, `offset`. An entry names the project and branch of its instance, or of the project it changed; older entries are found by the instances the project and branch have now |
| `GET /audit/facets` | `audit.view` | | 200, `users` and `events` found in the log, `projects` (`id`, `name`), `export_max_rows` (`AUDIT_EXPORT_MAX_ROWS`) |
| `GET /audit/export?format=csv` | `audit.view` | | 200, the entries the same filters and `sort` choose, as a file: `format` `csv` (UTF-8 with a byte order mark; a cell that starts with `=`, `+`, `-` or `@` is prefixed with `'`) or `xlsx`. Columns: time (UTC), user, event, allowed, project, branch, instance, address, detail. At most a year (`since` defaults to a year before `until`; 400 for a longer range) and `AUDIT_EXPORT_MAX_ROWS` entries; headers `X-Total-Count` (matching) and `X-Exported-Count`. Each export is itself audit-logged (`audit log exported`) |

`role` is one of `super_admin`, `admin`, `maintainer`, `developer`, `viewer`. Only a super admin creates, changes,
resets or deletes super admins (403 otherwise).

| Rule | Answer when broken |
| :--- | :--- |
| `email` is required for a new account | 400 "Enter the person's e-mail address: they sign in with it." |
| No two accounts share an e-mail address (compared without case) | 400 "Another account already uses this e-mail address." |
| A typed `password` follows the [password rules](USER_MANUAL.md#password-rules) | 400, `detail` names the rule, for example "The password contains a sequence such as 12345 or qwerty. Choose another one." |
| The last super admin stays a super admin and cannot be deleted | 400 "The last super admin must stay a super admin." or "The last super admin cannot be deleted." |
| Nobody deletes their own account or sets their own password here | 400; use `PUT /session/password` for your own password |

`PATCH /users/<name>` works on your own account too, including your role; a new role ends your sessions as well,
so you sign in again. `name` is the account's user name: 3 to 32 lower-case letters, digits, dots, dashes and
underscores, starting with a letter. When it is left out, it is made from the e-mail address
(`jane.doe@example.com` gives `jane.doe`, then `jane.doe-2` when that is taken).

The password: without `password` one is generated and `must_change_password` is always `true`. With a typed
`password`, `must_change_password: false` lets the owner keep it. While `must_change_password` is `true` the
owner's session is restricted until they choose their own ([10](#10-signing-in-from-a-web-app)).

```
POST /api/v1/users  {"email": "sam@example.com", "role": "maintainer", "first_name": "Sam", "last_name": "Lee"}
  -> 201 {"data": {"name": "sam", "email": "sam@example.com", "must_change_password": true, ...,
                   "password": "generated-and-shown-once"}}

POST /api/v1/users/sam/password  {"password": "green tea at 7 on the balcony", "must_change_password": false}
  -> 200 {"data": {"password": "green tea at 7 on the balcony"}}
```

An account:

```json
{
  "name": "sam", "display_name": "Sam Lee",
  "first_name": "Sam", "last_name": "Lee", "email": "sam@example.com", "phone": null,
  "role": "maintainer", "role_label": "Maintainer",
  "last_sign_in_at": "2026-11-12T08:01:10Z", "password_changed_at": "2026-11-01T09:12:44Z",
  "must_change_password": false, "second_factors": ["totp"],
  "links": {"self": "/api/v1/users/sam"}
}
```

### API tokens, backups and GitHub

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /tokens` | `tokens.manage` | | 200, API tokens (never their secrets) |
| `POST /tokens` | `tokens.manage` | `{"name", "scope"}` (`read`, `instances`, `developer` or `write`) | **201**, with `token` and `signing_secret`, shown once. Created by a token, the new token acts for the same account (403 when that account no longer exists) |
| `DELETE /tokens/<id>` | `tokens.manage` | | **204**, revoked |
| `GET /backups` | `backups.manage` | `?instance=<name>`, `?project=<id>` optional | 200, backups, newest first (below); `instance` keeps those taken from that instance, `project` those of the project's instances (the ones it has now). The project's access applies either way: those of a hidden project or branch are not listed |
| `GET /backups/<file>` | `backups.manage` | | the zip (`dump.sql`, the filestore, `manifest.json`) |
| `DELETE /backups/<file>` | `backups.manage` | | **204** |
| `GET /offsite` | `backups.manage` | | 200, `{"enabled", "configured", "active", "location", "endpoint", "other_stages", "kinds", "public_key", "fingerprint", "last_scan"}` (never the secret) |
| `GET /offsite/public-key` | `backups.manage` | | the public signing key, `sudoo-offsite-signing.pub.pem` |
| `POST /offsite/test` | `offsite.configure` | | 200, `{"location", "steps"}`: a test file written, read back, listed and deleted with the saved settings; 502 with the storage's reason |
| `POST /offsite/scan` | `backups.manage` | | 200, `{"found", "missing"}`: the bucket's list read again |
| `GET /offsite/copies` | `backups.manage` | | 200, off-site copies, newest first: `{"id", "instance", "stage", "kind", "stamp", "created", "status", "reason", "size", "files", "location", "uploaded_at", "verified_at", "verified_with", "job", "found_in_bucket"}`; `status` is `uploading`, `stored`, `verifying`, `verified` or `failed` |
| `POST /offsite/copies/<id>/verify` | `backups.manage` | `{"public_key"}` optional (PEM, a key kept off the server) | **202**, the job that downloads and checks the copy |
| `POST /offsite/copies/<id>/retry` | `backups.manage` | | **202**, the job that copies a failed copy again from the backup on the server; 409 when that backup is gone |
| `POST /offsite/copies/<id>/restore` | `offsite.restore` and `instances.restore` | `{"instance"}` (default: the copy's), `{"public_key"}` optional, `"confirm_production": true` for a production instance | **202**, the job: download, check (fails closed), then the usual restore with Rollback |
| `DELETE /offsite/copies/<id>` | `offsite.delete` | | **204**; 409 for the newest good copy of an existing instance |
| `GET /deploy-keys` | `github.manage` | | 200, deploy keys |
| `POST /deploy-keys` | `github.manage` | `{"repo"}` | **201**, the public key and the GitHub page to add it on |
| `DELETE /deploy-keys/<owner>/<repo>` | `github.manage` | | **204** |
| `GET /repositories/branches?url=` | `instances.view` | | 200, `{"branches", "default"}`; through the GitHub App's API when it covers the repository; 400 with what keeps the server from the repository (below) |
| `GET /repositories/access?url=&branch=` | `instances.view` | | 200, whether the server can read the repository and each submodule, and how (`via`: `deploy-key`, `github-app` or `public`); a line that cannot be read has `problem` and, when the GitHub App explains it, `fix` (below); `meta.can_manage_keys`, `meta.github_app` |
| `GET /github/app` | `github.manage` | | 200, `{"connected", "app", "trigger_running", "expected_hook_url", "deploy_keys"}`; `meta.can_manage` (below). The installations are read from GitHub first when the last reading is more than a minute old |
| `POST /github/app/manifest` | `settings.manage` | `{"organization", "create_repositories"?, "repository_guard"?}` (empty organisation: the personal account; `create_repositories`: also Administration: write; `repository_guard`: also what the [repository guard](#repository-guard) needs) | 200, `{"action", "manifest", "state"}`: post `manifest` as a form field to `action` from the browser; the state is kept in the session for one hour |
| `POST /github/app` | `settings.manage` | `{"code", "state"}` from GitHub's redirect to `/github/app/created` | **201**, the app; 400 when the state does not match the session's or the code expired |
| `DELETE /github/app` | `settings.manage` | | 200, `{"settings_url", "installations"}`: GitHub pages to uninstall and delete the app; the panel forgets its credentials |
| `POST /github/app/installations` | `settings.manage` | `{"installation_id"}` from GitHub's redirect to `/github/app/installed` | **201**, the installation, after GitHub confirms it belongs to the app |
| `POST /github/app/installations/sync` | `settings.manage` | | 200, the installations as GitHub lists them now |
| `DELETE /github/app/installations/<id>` | `settings.manage` | | **204**, forgotten by the panel (uninstall it on GitHub too) |
| `GET /github/repositories?q=` | `instances.create` | | 200, repositories readable through the app, newest push first, filtered by words of `q` (at most 200); `meta.connected`, and `meta.installations` (`[{"account", "settings_url"}]`: where to choose on GitHub which repositories the app may read) |

The GitHub App in `GET /github/app` (`app` is `null` while none is connected; secrets are never returned):

```json
{"id": 123456, "slug": "sudoo-sh-panel-3f9a", "name": "Sudoo.sh panel-3f9a", "client_id": "Iv23li...",
 "owner": {"login": "acme", "type": "Organization"}, "created": 1794441612,
 "hook_url": "https://panel.example.com/hooks/github-app",
 "settings_url": "https://github.com/organizations/acme/settings/apps/sudoo-sh-panel-3f9a",
 "install_url": "https://github.com/apps/sudoo-sh-panel-3f9a/installations/new",
 "installations": [{"id": 777, "account": "acme", "account_type": "Organization", "repository_selection": "all",
                    "settings_url": "https://github.com/organizations/acme/settings/installations/777",
                    "suspended": false, "added": 1794441700}]}
```

Both repository checks also read the app's installations from GitHub when the last reading is more than a minute
old, so installations made on GitHub itself count at once. When a repository cannot be read and an app is connected,
`problem` (and the 400's `detail`) says what applies, and the access report's line carries `fix`:

```json
{"url": "https://github.com/acme/erp", "role": "main", "ok": false,
 "problem": "The GitHub App is not installed on acme. Install it there and give it this repository.",
 "fix": {"reason": "not_installed", "url": "https://github.com/apps/sudoo-sh-panel-3f9a/installations/new",
         "label": "Install on acme"}}
```

`reason` is `app_owned_elsewhere` (the app is private and belongs to another account, so it can never be installed on
the repository's owner; `url` is the panel's `/github`), `not_installed`, `suspended`, `not_selected` (installed, but
the repository is not among the selected ones; `url` is the installation's settings on GitHub) or `not_found`
(`url` and `label` empty). Without an app, `fix` is `null` and `problem` advises a deploy key.

A repository in `GET /github/repositories`:

```json
{"full_name": "acme/sales", "url": "https://github.com/acme/sales", "private": true, "default_branch": "main",
 "description": "Sales customisations", "pushed_at": "2026-11-12T08:30:00Z", "account": "acme"}
```

When GitHub refuses or cannot be reached, these endpoints answer 502 with GitHub's message (never 401, which would
mean the caller's own session ended).

A backup in `GET /backups` (`created` in Unix seconds, `size` in bytes):

```json
{"file": "sales-main__sales_main__20261112-000012__daily.zip", "instance": "sales-main", "db": "sales_main",
 "stamp": "20261112-000012", "kind": "daily", "size": 48213504, "created": 1794441612.4,
 "version": "19.0", "series": "19.0", "edition": "community", "revision": "9f2c4e1...", "comment": null}
```

`revision` is the commit the instance's code was at when the backup was taken, `comment` what was given with **Back
up now** (`null` for backups without them). An instance lists its own backups with both too.

`version` and `edition` come from the backup's `manifest.json` (`null` when it does not say; backups of earlier
versions of Sudoo.sh have no `edition`). `series` is the Odoo release the database belongs to (`"17.1"` for
`saas~17.1`): a backup only goes into an instance of the same series, and an Enterprise backup only into an
Enterprise instance.

`kind` is one of:

| `kind` | Taken | Kept |
| :--- | :--- | :--- |
| `manual` | The `backup` action (**Back up now**); also every file of earlier versions, which have no kind in their name | Until deleted |
| `daily` | Once a day for each instance used since its last daily backup | Only the latest per instance; removed with the instance |
| `deletion` | Before an instance is deleted | Until deleted |
| `before-restore` | At the start of a `restore` action | Only the latest per instance, for `ROLLBACK_HOURS`; removed with the instance |
| `before-rollback` | At the start of a `rollback` action | Only the latest per instance, for `ROLLBACK_HOURS`; removed with the instance |
| `before-repair` | Before the panel recreates a database container that was changed outside the panel (`repair`) | Only the latest per instance; removed with the instance |
| `before-update` | Before an `update` or `update-submodules` job (also an automatic one) changes the code, when `SAFE_UPDATES` is on; for a paused instance only when modules will be upgraded | Only the latest per instance (one kept for a restore by hand when an automatic rollback failed); removed with the instance |

To restore a backup into an instance (its own, or another instance's), use the `restore` action; to start a new
instance from one, `from_backup` of `POST /instances` ([below](#instances-jobs-and-actions-in-detail)).

### Plugins and the marketplace

What the **Plugins** page uses ([Marketplace](MARKETPLACE.md)). The panel talks to the hub itself, with its own
signed requests; callers of these endpoints never need a hub account or key. When the hub cannot be reached, the
`/marketplace/...` endpoints answer **503** with a sentence saying so, and the `/plugins` endpoints keep working. A
hub refusal is passed on as **409** (404 for something that does not exist) with the hub's reason in plain words,
for example that the panel must be linked first. With `MARKETPLACE=off` the `/marketplace/...` endpoints and
installing answer **409**.

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /plugins` | `plugins.view` | | 200, the installed plugins: `id`, `name`, `version`, `tier`, `publisher`, `permissions`, `requires`, `enabled`, `status` (`active`, `disabled`, `modified`, `blocked`, `switched_off`, `unlicensed`), `loaded`, `restart_pending`, `licence` (`state`, `standing`, `expires_at`, `grace_until`, `phase`: `active`, `expiring`, `grace`, `ended`, `no_lease`, `unlicensed`; null for free plugins), `latest_version`, `update_available`; `meta.restart_pending` |
| `GET /plugins/notices` | `plugins.view` | | 200, banners: `[{"plugin", "kind", "level", "text", "date"}]` for licences ending, in grace or ended, changed files, and the hub refusing the panel |
| `POST /plugins` | `settings.manage` | `{"id": "com.sudoo.smtp", "version": "1.2.0"}` (version optional: the newest) | **202**, the job (kind `plugin-install`); the backend restarts when it has finished. **409** while jobs run or when that version is installed |
| `POST /plugins/<id>/update` | `settings.manage` | `{}` | **202**, the job installing the newest version |
| `POST /plugins/<id>/disable` | `settings.manage` | `{}` | 200, the plugin; the backend restarts without it. **409** when another installed plugin needs it |
| `POST /plugins/<id>/enable` | `settings.manage` | `{}` | 200, the plugin; the backend restarts with it. **409** when its files were changed |
| `DELETE /plugins/<id>` | `settings.manage` | `{"confirm": "<id>"}` | **202**, the job: backs up the panel's data, removes the plugin, restarts |
| `GET /marketplace` | `plugins.view` | | 200, the connection: `enabled`, `hub_url`, `installation_id`, `registered`, `hub_keys` (accepted key ids), `reachable`, `error`, `error_code` (`unreachable`, `unverified` for an answer that was not signed by an accepted key, or the hub's code), `hub_status`, `flags` (`fingerprint_mismatch`), `linked` (`account_name`), `key_revoked_at`, `removing_paid_plugins`, `last_heartbeat_at`, `next_heartbeat_at`, `lease_expires_at`, `lease_valid`, `lease_ending_soon` |
| `POST /marketplace/link` | `settings.manage` | `{"code": "<one-time code from app.sudoo.sh>"}` | 200, `{"account_name", "linked_at"}`; 404 for a wrong or used code; 409 when the account already has a server or this server belongs to another account |
| `POST /marketplace/unlink` | `settings.manage` | `{"confirm": true}` | Log out of the account: the hub revokes this server's paid licences and the panel removes its paid plugins after a backup (a job, `plugin-remove-paid`). The panel, free plugins and instances keep running. 200, the connection |
| `POST /marketplace/heartbeat` | `settings.manage` | `{}` | 200, the connection after a heartbeat sent now |
| `GET /marketplace/catalog?q=&type=&tier=&installed=&sort=&limit=50&offset=0` | `plugins.view` | | 200, the hub's catalogue for this panel (`tier`: `free` or `paid`; `installed`: `true` or `false`; `sort`: `popular`, `rating`, `newest`, `name`, `trusted`). Each plugin has `publisher` (`verified`, `official`), `icon_url` (the panel's own address for the icon, or null), `countries`, `languages`, `odoo_versions`, `local`, `plans` (cheapest first: `id`, `kind` `monthly`, `yearly`, `fixed_term` or `one_time`, `price_cents`, `currency`, `period_days`, `interval`, `perpetual`, `monthly_equivalent_cents`, `savings_percent`), `latest_version`, `compatible_version` (the newest that runs on this panel), `installed_version`, `license` and `action`: `install`, `update`, `installed`, `buy`, `renew` (a licence that ended) or `incompatible` |
| `GET /marketplace/catalog/<id>` | `plugins.view` | | 200, the plugin's page: `permissions`, screenshots, plans, `installed_version`. The panel tells the hub the page was shown (a recommendation signal, kept only with the account owner's consent) |
| `GET /marketplace/recommendations?limit=8` | `plugins.view` | | 200, `[{"plugin", "score", "reasons"}]`: plugins the hub suggests for this server, each with up to three reasons ("Because you use Sample mailer") |
| `GET /marketplace/icons/<id>?v=` | `plugins.view` | | 200, the plugin's icon (PNG or SVG), fetched from the hub with a signed request; 404 when it has none |
| `GET /marketplace/categories` | `plugins.view` | | 200, `[{"type", "count"}]` |
| `GET /marketplace/billing` | `plugins.view` | | 200, whether a checkout session can be opened now: `payments_enabled`, `linked`, `account_name`, `can_buy`, `reason` (`payments_disabled`, `link_required`, `fingerprint_mismatch`), `currency`, `manage_url` (app.sudoo.sh, Servers) |
| `POST /marketplace/checkout-sessions` | `settings.manage` | `{"plugin_id", "plan_id", "idempotency_key"}` | Asks to buy for this server; the panel never charges anything. The caller's name and e-mail go with the request (shown on the checkout page and the receipt). `idempotency_key` (16 to 64 of `A-Za-z0-9_-`) is new for each press of Buy and the same when that request is retried: the hub answers the same session. **201** + `Location`, the session: `id`, `status` (`open`, `processing`, `checkout_required`, `succeeded`, `expired`, `cancelled`, `failed`), `url` (`https://app.sudoo.sh/checkout/...`: open it in a new tab, where the account owner pays), `expires_at`, `plugin_id`, `plugin_name`, `plan`, `amount_cents`, `currency`, `method`, `reason` (why the last payment failed), `receipt_number`, `requested_by`, `created_at`, `completed_at`. 409 for `already_licensed`, `purchase_in_progress`, `idempotency_key_reused` ...; 503 when payments are off |
| `GET /marketplace/checkout-sessions/<id>` | `settings.manage` | | 200, the session; poll it every few seconds while `open`, `processing` or `checkout_required` (a declined card leaves it `open` with a `reason`); on `succeeded` install with `POST /plugins`; on `expired`, `cancelled` or `failed` start again with a new key |
| `GET /marketplace/checkout-sessions` | `plugins.view` | | 200, the sessions opened from this server, newest first (the same objects); `?format=csv` answers a CSV file |
| `GET /marketplace/receipts` | `plugins.view` | | 200, receipts and credit notes of this server's plugins: `number`, `kind` (`receipt` or `credit_note`), `issued_at`, `plugin_name`, `plan_kind`, `payment_kind` (`purchase` or `renewal`), `total_cents`, `currency`, `requested_by_name`; `?format=csv` answers a CSV file |

No endpoint returns a plugin's files or its package.

### Documentation

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /docs` | `instances.view` | | 200, `{"index", "sections": [{"title", "pages": [{"path", "title"}]}]}`: the table of contents |
| `GET /docs/<path>` | `instances.view` | | 200, `{"path", "title", "html", "headings": [{"level", "id", "text"}]}`, for example `GET /docs/docs/PANEL_API.md` |
| `GET /help` | `instances.view` | | 200, what the help panel (the **?** button) shows: `version`, `docs_url`, `support` (`kind`: `hub`, `link`, `email` or `none`; `url`; `name` with `none`) |

These are the same Markdown files as in the repository's `docs/`, rendered on the server. Links between pages point
to `/help/<path>`, the web app's Help pages.

### First run, session and own account (the web app)

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /setup` | public | | 200, `{"needed", "contact", "default_language"}`: whether the panel still has no account, and the server's default language (`en` or `ar`) |
| `GET /setup/server-check` | setup code (`X-Setup-Code`) before the first account, then `settings.manage` | `?grow=`, `?separation=`, `?team=` (`yes` or `no`): the answers to the engine questions; `?fresh=1` measures again | 200, the server's figures, `needs`, `recommendation` (`fits`, `recommended`, `default`, `reason`, `reason_code`, `reasons`, `beta`), `figures` (`measured` false while they are placeholders), `engine` (`current`, `wanted`, `available`, `switch_command`). Read only |
| `GET /setup/draft` | setup code | | 200, the setup wizard's choices so far (secrets only as set or not); 409 once the panel is set up |
| `PUT /setup/draft` | setup code | any of `step`, `language`, `engine`, `needs`, `exposure`, `name`, `mail` (with `password`), `github`, `account`, `admin` | 200, the draft; nothing is applied; 400 naming a wrong value |
| `POST /setup/test-email` | setup code | `{"to"}`, optionally `mail` and `password` | 200 `{"sent_to"}`, sent with the draft's mail server; at most 5 every 15 minutes |
| `POST /setup` | public | `{"setup_code", "password", "first_name", "email", "licence"}` required (`licence`: the acceptance of the licence agreement, as `POST /licence/acceptances` takes it); `last_name`, `phone`, `username` optional (made from `email` when absent) | **201**, signed in as the first account, a super admin ([10](#10-signing-in-from-a-web-app)); the setup wizard's draft is checked first and applied after, and `setup` says what was applied (`settings`, `restart_needed`, `linked`, `link_error`, `github`, `engine`) |
| `GET /contact` | public | | 200, who to contact, and `default_language` |
| `POST /session` | public | `{"username", "password"}`; `username` is the e-mail address (up to 254 characters) or the user name | **201**, the session (as `GET /session`); sets the session cookie. Or **200** with `second_factor` when a code is needed. **401** "Wrong e-mail address or password." |
| `POST /session/second-factor` | public | `{"method", "code"}`; `"method": "recovery"` takes a recovery code (`"k7m2p-x9qrt"`, capitals, spaces and the dash optional) | **201**, signed in; each recovery code works once. **401** for a wrong or used code; wrong codes count like wrong passwords |
| `POST /session/second-factor/<method>` | public | `{}` | 200, a new code was sent (`email`) |
| `GET /session` | signed in | | 200, the signed-in account (below) |
| `DELETE /session` | signed in | | **204**, signed out; the session ends on the server too, so a copy of its cookie stops working |
| `PUT /session/password` | signed in | `{"current", "new"}` | **204**; other sessions of the account end, and a `password` restriction is lifted |
| `GET /account` | signed in | | 200, own account: profile, role, second factors, whether one is required, `recovery_codes` (`{"left", "total", "created_at"}` or `null`), `recovery_codes_needed`, `sign_in_email` (`{"enabled", "own_choice", "default", "can_turn_off", "mail_ready"}`), `expiry_email` (`true`, `false` or `null`: the person's own choice for the e-mails about instances that expire), `language` (`{"value", "own_choice", "default"}`: the web app's language, the person's own choice or `null`, and `DEFAULT_LANGUAGE`) and `tours` (`{"seen", "tips", "enabled"}`: the guided tours this person has seen, whether they want tips, and `TOURS_ENABLED`) |
| `PATCH /account` | signed in | any of `first_name`, `last_name`, `email`, `phone`, `sign_in_email` (`true`, `false`, or `null` for the server's default), `expiry_email` (`true`, `false` or `null`), `language` (`"en"`, `"ar"`, or `null` for the server's default), and `tours` (`{"mark": [tour names], "seen": [], "tips": true\|false}`, any key; `mark` adds to the tours seen as stored at that moment, so two browsers cannot drop each other's; `"seen": []` is the only form of `seen` and shows every tour again; 400 for anything else) | 200, the account. `sign_in_email: false` is **403** when `SIGN_IN_EMAIL_OPT_OUT` is off. The e-mail address cannot change while it receives sign-in codes. An address another account uses is refused: 400 "Another account already uses this e-mail address." A new address ends the account's other sessions |
| `POST /account/second-factors/<name>` | signed in | `email`: `{"email"}`; `totp`: `{}`. To replace a factor that is already set up, or to add one when another is set up, add `"password"` | 200: a code was sent, or `{"secret", "otpauth_uri"}` for the app. 400 when the password is missing or wrong, or the e-mail address belongs to another account |
| `PUT /account/second-factors/<name>` | signed in | `{"code"}` | 200, the factors; it is set up. Setting up `totp` on an account without recovery codes also returns them, once, in `meta.recovery_codes`. Replacing a factor, or a new e-mail address, ends the account's other sessions |
| `DELETE /account/second-factors/<name>` | signed in | `{"password"}` | **204**; the account's other sessions end; removing `totp` removes the recovery codes |
| `POST /account/recovery-codes` | signed in | `{"password"}` | **201**, `{"codes", "left", "total", "created_at"}`: new codes, shown once; the earlier ones stop working. 400 without the authenticator app or with a wrong password |
| `DELETE /account/sessions` | signed in | | **204**; every other session of the account ends, this one continues |

`GET /session`:

```json
{
  "data": {
    "user": "sam", "display_name": "Sam Lee",
    "first_name": "Sam", "last_name": "Lee", "email": "sam@example.com", "phone": null,
    "role": "maintainer", "role_label": "Maintainer",
    "permissions": ["github.manage", "instances.create", "instances.credentials", "instances.logs", "..."],
    "csrf_token": "...",
    "restricted": null,
    "must_set_up_second_factor": false,
    "recovery_codes_needed": false,
    "brand_color": "#714B67",
    "language": "en",
    "tours": {"seen": ["panel-welcome"], "tips": true, "enabled": true}
  }
}
```

`permissions` is what the web app uses to show or hide buttons; the server checks again on every call.
`restricted` is `"password"` (choose your own password first), `"second_factor"` (set one up first) or `null`.
While it is set, only `/session`, `/account` and `/contact` answer; every other call is refused with 403.

### Instances, jobs and actions in detail

An instance:

```json
{
  "data": {
    "name": "sales-main",
    "status": "online",
    "stage": "development",
    "project": {"id": "sales", "name": "sales", "links": {"self": "/api/v1/projects/sales"}},
    "url": "https://river-stone-apple.example.com",
    "odoo": {"version": "19.0", "edition": "enterprise", "database": "sales_main"},
    "source": "repository",
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
    "expired_at": null,
    "deletes_at": null,
    "current_job": null,
    "links": {"self": "/api/v1/instances/sales-main"}
  }
}
```

`built_seconds` is how long its creation took (`null` for an instance created before the panel measured it).
`expired_at` is when the instance was stopped at the end of its lifetime and `deletes_at` when it will be deleted
(`EXPIRY_GRACE_DAYS` later); both are `null` otherwise.

`GET /instances/<name>` adds, among others, the instance's backups, newest first, and the one the `restore` action
would use without a `file` (`null` when there is none):

```json
"backups": [
  {"file": "sales-main__sales_main__20261112-000012__daily.zip", "kind": "daily", "database": "sales_main",
   "size_bytes": 48213504, "created_at": "2026-11-12T00:00:12Z"},
  {"file": "sales-main__sales_main__20261110-153001__manual.zip", "kind": "manual", "database": "sales_main",
   "size_bytes": 47120384, "created_at": "2026-11-10T15:30:01Z"}
],
"restorable_backup": "sales-main__sales_main__20261112-000012__daily.zip",
"rollback": {"backups": ["sales-main__sales_main__20261112-101500__before-restore.zip"],
             "taken_at": "2026-11-12T10:15:00Z", "until": "2026-11-13T10:15:00Z"}
```

Every instance also has `managed_name`: `null`, or its name under Sudoo.sh's domain (managed subdomains, every plan;
[Administrator Guide](ADMIN_GUIDE.md#managed-subdomains)). Once `serving`, it is the instance's `url` too, unless
the instance has a primary custom domain:

```json
"managed_name": {"hostname": "main.development.acme.sudoo.cloud", "status": "active", "reason": null,
                 "serving": true, "url": "https://main.development.acme.sudoo.cloud"}
```

`rollback` is `null` unless the instance was restored in the last `ROLLBACK_HOURS` (and not rolled back since):
then it names the `before-restore` backup(s) the `rollback` action puts back, and until when it is offered.

Every instance also has `update_failure`: `null`, or, when its last code update failed (and no update has worked
since), what happened:

```json
"update_failure": {"message": "The update to 9b1e0c4 failed at upgrading modules; the instance was put back to 4f2a9c1. One of the modules has an error in its XML files (views or data). ...",
                   "failed_at": "2026-11-12T10:20:31Z", "rolled_back": true, "job": "/api/v1/jobs/7d0e5f2a9b13"}
```

`rolled_back` is `false` when putting the instance back failed too; `message` then names the `before-update` backup
to restore by hand.

`status` is one of `creating`, `online`, `starting`, `busy` (a job is running), `paused`, `switched_off`, `stopped`,
`unknown`. `stage` is `production`, `staging` or `development` ([Stages](#stages)).

`creating`: the instance's creation is queued or running. The instance answers (and is listed) from the moment
`POST /instances` is accepted, before any of its files exist: then it shows what the creation was asked for (version,
edition, database, repository and branch, settings) and `null` or empty for everything else. `current_job` is the
creation job with its steps so far (`status` `queued` while it waits for another job on the same name, then
`running`); follow it with `GET /jobs/<id>` or the live `job` events. Actions, settings, credentials and logs answer
409 until the creation has finished. When the creation fails, what it made is removed: `GET /instances/<name>`
answers 404 again (a live `instance.deleted` event is sent) and the job, with its error, stays under `/jobs`.

```json
"status": "creating",
"current_job": {"id": "b7e0c2a41f93", "action": "Create instance", "status": "running", "progress_percent": 0,
                "steps": [{"title": "Downloading Odoo 19.0, first time on this server", "status": "running",
                           "started_at": "2026-11-12T08:30:58Z", "progress": "Downloading Odoo 19.0: 2 of 5 parts",
                           "progress_percent": 40}],
                "links": {"self": "/api/v1/jobs/b7e0c2a41f93"}}
```

While another job runs, `current_job` has the same shape.

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
    "steps": [{"title": "Restarting Odoo", "status": "running", "started_at": "2026-11-12T08:31:03Z",
               "progress": null, "progress_percent": null}],
    "notes": [],
    "error": null,
    "failure": null,
    "links": {"self": "/api/v1/jobs/a12fff66a05d", "instance": "/api/v1/instances/sales-main"}
  }
}
```

`status` goes from `queued` to `running` to `success` or `failed`; when it failed, `error` explains why in plain words
and `failure` is "Why it failed": `step` (the step that failed), `reason` (the same sentence as `error`), `kind`
(`repository`, `git`, `requirements`, `tests`, `odoo` or `other`), `details` (only the lines that explain the failure,
at most 60, without server paths, addresses or secrets; empty when nothing explains it) and `repository` (for
`repository`: the repository the panel could not read, `owner/name`). Each step has `started_at` and, while it runs,
`progress` (a plain sentence such as "Downloading Odoo 19.0: 2 of 5 parts", or `null`) and `progress_percent`. A job's
technical log is never part of the answer: it is in the backend's log.
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
  "modules": ["sale_management", "crm"],
  "demo": false,
  "delete_after_days": 30,
  "stage": "development",
  "from_backup": "sales-main__sales_main__20261112-000012__daily.zip"
}
```

**Plain Odoo.** `repo` is optional: without it the instance runs the official Odoo only (Community or Enterprise,
any offered version), with no code from GitHub. Nothing is cloned (its add-ons folder stays empty and is not on
Odoo's add-ons path), `branch` is ignored, `auto_update` and `auto_upgrade` are false and `install_modules` does
nothing. Without `name` either, it is called `odoo-<version>-<edition>-<4 random characters>` (for example
`odoo-18-0-community-3f9a`); the usual name rules apply to a given name. It shows `"source": "plain"` and
`"git": {"repository": null, "branch": null, ...}`. Everything else works as for any instance (pause and start,
backups, restore and rollback, lifetime, links, metrics, the integrity watchdog, `upgrade` of Odoo's own modules).
The actions that need a repository (`update`, `update-submodules`, `switch-branch`, `add-submodule`,
`remove-submodule`) and `PATCH` with `auto_update` or `auto_upgrade` set to true answer **409** with a problem
detail saying it runs plain Odoo. To give it a repository later, run `connect-repository` (below): its database
and files are kept, and from then on it works like an instance created from that repository.

```json
{"type": "about:blank", "title": "Conflict", "status": 409,
 "detail": "odoo-18-0-community-3f9a runs plain Odoo without a GitHub repository: it has no code to update, no branch and no submodules. Connect a repository to it first.",
 "instance": "/api/v1/instances/odoo-18-0-community-3f9a/actions/update", "request_id": "..."}
```

`stage` is optional (`development` by default) and decides the defaults of the fields not sent: `delete_after_days`
(`DELETE_AFTER_DAYS`, `STAGING_DELETE_AFTER_DAYS` for staging, always 0 for production), `auto_pause` (never for
production) and `auto_update` (`PRODUCTION_AUTO_UPDATE` for production). A production instance with
`delete_after_days` other than 0 or `"auto_pause": true` answers 400; one more production instance than
`MAX_PRODUCTION_INSTANCES` allows answers **409** with a problem detail naming those in production.

`from_backup` is optional: the new instance starts with that backup's database and files (loaded under the new
database name) instead of an empty database. It needs `instances.restore` and `backups.manage` besides
`instances.create`; an unknown file answers 404, a backup of another Odoo series (or an Enterprise backup for a
Community instance) 400. `demo` is ignored then, and of the modules asked for (the repository's with
`install_modules`, and `modules`) only those the backup does not have are installed. A backup of another instance is neutralised (no e-mail, no scheduled actions; the job's notes
say so).

Actions (`POST /instances/<name>/actions/<action>`); each needs its own permission:

| Action | Does | Permission | Body |
| :--- | :--- | :--- | :--- |
| `update` | Get latest code | `instances.update` | `{}` |
| `update-submodules` | Update submodules | `instances.update` | `{}` |
| `restart` | Restart | `instances.update` | `{}` |
| `start` | Start a paused or switched-off instance | `instances.update` | `{}` |
| `add-submodule` | Add a submodule | `instances.update` | `{"url": "https://github.com/OCA/web", "path": "oca/web", "branch": "19.0"}` (`path` and `branch` optional) |
| `pause` | Pause | `instances.operate` | `{}` |
| `switch-off` | Switch off (stays off until started) | `instances.operate` | `{}` |
| `upgrade` | Upgrade modules | `instances.operate` | `{"modules": "sale_custom,stock_custom"}` or `{"modules": "all"}` |
| `remove-submodule` | Remove a submodule | `instances.operate` | `{"path": "oca/web"}` |
| `switch-branch` | Switch branch | `instances.operate` | `{"branch": "main"}`; for a production instance the new branch becomes a production branch in place of the earlier one (which stays production only while another production instance follows it); **409** when that is beyond the project's or the plan's limits |
| `connect-repository` | Give an instance of plain Odoo a GitHub repository: clone it, install its modules if asked, restart (data kept); 409 when it has one, or when a production instance would take its repository's project beyond its production branches | `instances.operate` | `{"repo": "https://github.com/acme/odoo-addons", "branch": "main", "install_modules": true}` (`branch` defaults to `main`) |
| `backup` | Back up now | `instances.operate` | `{}`, or `{"comment": "before the price import"}` (up to 200 characters, kept in the backup's `manifest.json`) |
| `update-images` | Download the instance's Odoo and PostgreSQL images again (same names and tags), pin the new digests, recreate the containers | `instances.operate` | `{}` |
| `repair` | Restore the panel's configuration after a change made outside the panel (also started on its own with `DRIFT_ACTION=restore`) | `instances.operate` | `{}` |
| `restore` | Restore a backup: replaces the database and the filestore | `instances.restore` (and `backups.manage` for another instance's backup) | `{}` for the latest, or `{"file": "<backup file name>"}` |
| `rollback` | Go back to the state before the last restore, while it is kept (`ROLLBACK_HOURS`) | `instances.restore` | `{}` |
| `delete` | Delete, after a backup (same as `DELETE /instances/<name>`) | `instances.delete` | `{"confirm": "<name>"}`; production: also `"confirm_stage": "production"` |

By role: a Developer may run the `instances.update` actions, a Maintainer every action except `delete`, a Super
admin or Administrator all of them. On a production instance, `pause`, `switch-off`, `restore` and `rollback` also
need `"confirm_production": true` in the body, and `delete` needs `"confirm_stage": "production"` besides the name;
without them the answer is 400 and nothing starts.

#### Choosing the modules

`install_modules` installs the repository's own modules once the database exists (for a development branch of a
project, what the project's **Modules to install in new instances** says: the repository's modules, every module, or
a list); `false` by default for `POST /instances`, `true` for `POST /projects/<id>/branches/<branch>/instances`.
`modules` is optional: more modules installed in the same run (`odoo -i` with the repository's and these), such as
Odoo's apps (`GET /odoo-apps?version=19.0&edition=enterprise` lists the standard ones, the Enterprise apps only for
`enterprise`) or a module of a submodule; Odoo installs their dependencies itself. It works for plain Odoo too. Each
name is lower-case letters, digits and `_` (at most 64 characters), duplicates count once, and there may be at most
`MAX_INITIAL_MODULES` (200 by default); anything else answers **400** before anything is created. Without the
repository's modules and with an empty (or no) `modules`, the database has Odoo's base only.

For API users, `GET /repositories/modules?url=<address>&branch=<branch>` lists the branch's modules from the
panel's own copy of the repository (the GitHub App's token, a deploy key or anonymously, as for creating), its
submodules included, without creating anything:

```json
{
  "repository": "acme-co/sales",
  "branch": "main",
  "commit": "3f9a0c1e...",
  "modules": [
    {"name": "sales_custom", "title": "Sales custom", "summary": "", "application": true,
     "path": "addons/sales_custom", "submodule": null},
    {"name": "web_responsive", "title": "Web Responsive", "summary": "", "application": false,
     "path": "oca/web/web_responsive", "submodule": "oca/web"}
  ],
  "problems": []
}
```

`problems` lists the submodules that could not be read. A module marked `"installable": False` is left out, as are
those Odoo would not find (inside another module, in hidden or `setup` folders, more than four folders deep).

#### Following a log

`GET /instances/<name>/logs/stream` answers with Server-Sent Events, served next to the live events. A browser's
`EventSource` reconnects by itself and sends the last event's id as `Last-Event-ID`; other clients send it as
`?after=<id>` with `tail=0`. The id of `odoo.log` is the time stamp Docker gave the last line, so a stream opened again
continues exactly after it. The server ends a stream after 5 minutes
(`end` with `"max_duration"`) and when Odoo's container stops (`"stopped"`): open it again from the last id. Lines are
read from Docker only as fast as the client takes them. With filters, an entry (a line and its traceback) is sent
once it is complete: when the next one starts, or after a quarter of a second without a new line.

#### Reading logs

A log is read as **entries**: a line in Odoo's format (`2026-11-22 08:00:00,123 7 INFO shop odoo.http: text`) and
the lines after it that are not, such as a traceback. Each entry has an `id` that orders them: for `odoo.log` the time
stamp Docker gave its first line, with nine decimals (`2026-11-22T08:00:00.123456789Z`), the same as the stream's
`ids`; for `install.log` and `update.log` the start of the job and the line's place in it. A link to a line keeps that
`id` and opens it with `around=`.

* `install.log` and `update.log` are cut out of the jobs' technical logs: only the output of Odoo's own
  `--stop-after-init` runs (`-i` for installs, `-u` for updates), and of it only the lines in Odoo's format and the
  tracebacks that follow them, redacted as the server's log is. Commands, paths, step titles and notes of the panel
  never appear. They exist while the panel keeps the jobs in memory (the last 300 jobs, until it restarts).
* Filters, the same on the pages, the stream and the download: `level` (one or more of `DEBUG`, `INFO`, `WARNING`,
  `ERROR`, `CRITICAL`, separated by commas; `WARN` counts as `WARNING`), `logger` (that logger and those under it:
  `odoo.addons` keeps `odoo.addons.sale.models.sale_order`), `tracebacks=1`, `since` and `until` (ISO 8601 with a
  zone; Odoo writes its time stamps in UTC), and `q`: a text, any case, or with `regex=1` a regular expression (any
  case). Only the first 4,096 characters of an entry are searched.
* A regular expression is compiled by the `regex` package and run with a time limit on each entry (20 ms) and on the
  whole request (2 s). One that already takes too long on short probe texts, such as `(a+)+$`, is refused with 400;
  one that takes too long on the log itself answers 422 (the stream ends with `"slow_pattern"`). A pattern can never
  hold the panel's threads.
* Pages read at most the last `LOG_DOWNLOAD_MAX_LINES` lines of `odoo.log` and at most 64 MB of it.

#### Connect as

`POST /instances/<name>/connect` gives a link `https://<instance address>/__sudoo/connect?token=...`. The token is
random, valid once and for 60 seconds, for that instance and that Odoo user only, and kept by the panel only as its
SHA-256. The instance's gateway answers the link: it asks the panel to use up the token, the panel writes a session for
the user into the instance's own session store through Odoo's session API (in `odoo shell` inside the Odoo container),
and the gateway sets Odoo's `session_id` cookie for the instance's address and redirects to `/web`. No password is
read, reset or changed; the session ends as any Odoo session does (sign out, password change, Odoo's session
lifetime). Giving the link and every use of it are written to the audit log (who, instance, which Odoo user). A used,
expired or unknown link answers 403 with a short page.

#### Import database

An Odoo dump from elsewhere goes into an instance in three steps: `POST .../imports` announces the file, `PUT
.../imports/<id>?offset=` sends it piece by piece (the web app sends 4 MB pieces; after a lost connection, `GET
.../imports/<id>` says how much arrived), and `POST .../imports/<id>/restore` starts the job. Accepted: `.zip`
(Odoo's backup format: `dump.sql`, `filestore/`, `manifest.json`), `.dump` (`pg_dump -Fc`), `.sql` and `.sql.gz`.
Before the job starts, a zip is checked: a valid archive, every entry inside it (no absolute paths, no `..`, no
links), `dump.sql` present, and the Odoo version in `manifest.json` of the instance's major version. A dump without a
manifest is checked by the job, from the version of the `base` module recorded in the dump. The job backs up the
current state first (Rollback is offered afterwards), replaces the database and the filestore (a dump without a
filestore leaves it empty), neutralises the data unless `"neutralize": false`, and starts Odoo. Users keep their
passwords from the dump. An upload belongs to the person who started it and is removed after the import, when
cancelled, or after 24 hours without a new piece.

#### Stages

Every instance has a `stage`: `production`, `staging` or `development`. Instances made before stages existed are
`development` and behave exactly as before.

| | Development | Staging | Production |
| :--- | :--- | :--- | :--- |
| Paused when idle | when `auto_pause` | when `auto_pause` | never (`auto_pause: true` answers 409) |
| Deleted automatically | after `delete_after_days` | after `delete_after_days` (new ones: `STAGING_DELETE_AFTER_DAYS`) | never (`delete_after_days` other than 0 answers 409) |
| Daily backup | when `DAILY_BACKUPS` and used since the last one | the same, and `STAGING_DAILY_BACKUPS` | every day, used or not, whatever `DAILY_BACKUPS` says |
| Safe updates | when `SAFE_UPDATES` | when `SAFE_UPDATES` | always |
| New code automatically | `AUTO_UPDATE` for new ones | `AUTO_UPDATE` for new ones | `PRODUCTION_AUTO_UPDATE` for new ones (off by default) |

Change it with `PATCH /instances/<name>` (`instances.operate`) and `{"stage": "production"}`. Moving an instance into
production answers **409** when `MAX_PRODUCTION_INSTANCES` instances are in production already; otherwise its
lifetime becomes "keep" and its automatic pause is switched off. Moving one out of production needs
`"confirm_production": true` (400 without it) and `instances.delete` (403), as the instance can then expire; its
settings stay as they are. A lifetime sent in the same request is weighed with the new stage. Each change is in the audit log
("instance stage changed", with the stages before and after).

```
PATCH /api/v1/instances/sales-main  {"stage": "staging", "confirm_production": true}
  -> 200 {"data": {"name": "sales-main", "stage": "staging", ...}}
```

`restore` without `file` puts back the newest `manual`, `daily` or `deletion` backup of a database the instance has
(`restorable_backup` in `GET /instances/<name>`); 409 when there is none. With `file`, it may be any backup of the
same Odoo series (`series` in `GET /backups`): one of the instance's own `backups`, or, with `backups.manage` too,
another instance's (403 without it). A backup of an earlier, deleted instance with the same name is another
instance's: it is not in `backups`, not chosen without `file`, needs `backups.manage` and is neutralised. An unknown file answers 404, a backup of another series or an Enterprise backup
for a Community instance 400. The job (`"action": "Restore a backup"`) saves the current state as a `before-restore`
backup, stops Odoo, replaces the database and the filestore, sets the instance's public address again and starts
Odoo. Another instance's backup is loaded under this instance's database name and neutralised first (Odoo's
`neutralize` on Odoo 16 and later: mail servers, scheduled actions and integrations off; a new database identity);
the job's notes say so; its administrator (user 2) then gets this instance's admin password, and
`GET /instances/<name>/credentials` shows that administrator's `login`. When the backup is of a database the
instance does not have, the job fails and `error` says so.

`update` and `update-submodules` with `SAFE_UPDATES` on (the default): when the update changes the code, the job
first saves a `before-update` backup (for a paused instance only when modules will be upgraded), then pulls,
upgrades and restarts. If a later step fails (a pull that cannot fast-forward, a submodule, the module upgrade, or
Odoo not answering after the restart), the job puts the instance back: the previous commit of the repository and of
every submodule, the `before-update` backup when an upgrade ran or started, then Odoo restarted and checked. The job
ends `failed` and its `error` reads "The update to `<sha>` failed at `<step>`; the instance was put back to
`<previous sha>`." followed by the explanation of the failure, when there is one; `update_failure` of the instance
shows the same, and the audit log has an entry. An automatic update does not try a commit whose update was put back
again; `update` does. With `SAFE_UPDATES` off, updates run without the backup and the rollback, except on a
production instance, which is always updated this way.

`rollback` puts back the `before-restore` backup named in `rollback` of `GET /instances/<name>`; 409 when there is
none (also once the instance was rolled back to it; restoring that file with `restore` still puts it back). It
saves the current state as `before-rollback` first (never as `before-restore`, so it cannot replace what it
restores). Restoring the instance's own `before-restore` file with `restore` does the same.

```
POST /api/v1/instances/sales-main/actions/restore  {"file": "sales-main__sales_main__20261110-153001__manual.zip"}
  -> 202 {"data": {"id": "c3d41e0b7a92", "action": "Restore a backup", "status": "queued", ...}}
```

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

A connected app with an [`instances` token](#the-instances-scope), calling for one of its users:

```python
odoo_module = PanelAPI("https://panel.example.com", token="sudoo_...", signing_secret="...",
                       acting_for="sara@example.com")      # signed as the sixth line
job = odoo_module.create(version="19.0", edition="community", delete_after_days=7)
print(job["instance"])                              # created_by: "api:<token name> for sara@example.com"
odoo_module.request("PATCH", f"/api/v1/instances/{job['instance']}", {"delete_after_days": 14})  # extend
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
| 400 | A field is missing or not valid (also `X-Sudoo-Acting-For`); `detail` says which and what to send |
| 401 | Token, timestamp, nonce or signature not accepted, the session has ended, or a wrong e-mail address or password at `POST /session`; `detail` says which |
| 403 | The caller's role lacks the permission; `detail` names what is missing, for example "Your role cannot do this (delete instances)." A `read` token sending a change, an `instances` token calling anything [outside its scope](#the-instances-scope), a `developer` token calling anything [outside its scope](#the-developer-scope), a call refused because the panel was not linked to a Sudoo.sh account within `LINK_DEADLINE_DAYS` (new instances, moves into production, updates), a restricted session, a wrong setup code, and a reached instance limit (`MAX_INSTANCES`, `MAX_INSTANCES_PER_USER`, `MAX_RUNNING_INSTANCES`) are also 403, each with its own `detail` |
| 404 | No such instance, job, account, action or documentation page |
| 405 | The method is not allowed on this path |
| 409 | `POST /server/restart` while jobs are running |
| 415 | The body is not sent as JSON |
| 429 | Too many requests per minute, too many refused calls, wrong passwords or wrong setup codes from this address (5 in 15 minutes), or a creation within `CREATE_COOLDOWN_MINUTES`; `Retry-After` says how many seconds to wait |
| 500 | Something unexpected on the server; quote the `request_id` |
| 502 | `POST /settings/test-email`: the mail server refused the message; `detail` has its answer |
| 503 | An e-mail code could not be sent |

---

## 6. Security notes

* The token is stored only as a SHA-256 hash, and the signing secret encrypted with the vault key. Neither is ever
  shown again; a lost one means creating a new token.
* A `write` token acts as an Administrator: it can do everything an administrator can, including deleting instances,
  reading Odoo passwords and managing accounts (but not the panel's settings). A `read` token acts as a Viewer. An
  `instances` token can only list, create, start, pause, restart, back up, extend and delete instances.
  Give each system its own token, with the `read` scope where reading is enough and the `instances` scope for a
  connected app such as the Odoo module, and revoke tokens that are no
  longer used.
* Every call is in the audit log as `api:<token name>` (`api:<token name> for <person>` with
  [`X-Sudoo-Acting-For`](#acting-for-someone)), and every refused call with its reason and address.
* Resources never contain passwords (Odoo admin, master password) or the server's ports; only
  `GET /instances/<name>/credentials` returns them, to callers with `instances.credentials`, and each read is in the
  audit log.
* API tokens do not open the panel's pages. The browser's session opens the API only from the panel's own
  address, and every change also needs the session's CSRF token, so another website cannot use a visitor's session.

---

## 7. Settings

The settings in `.env` are the defaults. A super admin (permission `settings.manage`) can change almost all of them
while the panel runs, on the **Settings** page or through the API; the change is kept in
`/opt/odoo/manager/settings.json` on top of `.env`. Each setting says when a change takes effect, in `applies`:

| `applies` | When | Which |
| :--- | :--- | :--- |
| `now` | At once, without a restart | Almost every setting: limits, sessions, e-mail, daily backups (`daily_backups`, `daily_backup_time`, `timezone`), Odoo images and versions, Enterprise paths, contact, accent colour (marked **live** in [Configuration](CONFIGURATION.md)) |
| `restart` | After the panel restarts (`POST /api/v1/server/restart`, or **Restart the panel** on the Settings page) | The settings that choose plugins: `mailer`, `second_factors`, `update_triggers`, `enterprise_source`, `auth`, `rate_limiter` |
| `env` | Only in `.env`; the API refuses a change with 400 | Folders, ports, the proxy's address, storage. `EXPOSURE` applies at restart; the API takes `direct`, `proxy` or `tunnel` and answers 409 for a change that would move the web service ([Public addresses](concepts/PUBLIC_ADDRESSES.md#8-changing-the-mode-later)) |

```json
GET /api/v1/settings
{
  "data": [
    {
      "key": "idle_hours", "env": "IDLE_HOURS",
      "value": 4, "env_value": 6, "changed_in_panel": true,
      "editable": true, "applies": "now", "pending_restart": false,
      "secret": false, "type": "number", "minimum": 0,
      "description": "Pause an instance after this many hours without visits; 0: never."
    },
    ...
  ]
}
```

`pending_restart` is `true` for a `restart` setting whose new value is not in use yet. `editable` is `true` for
`now` and `restart` settings.

| Request | Effect |
| :--- | :--- |
| `PATCH /api/v1/settings` `{"idle_hours": 4, "auto_pause": false}` | Change several at once; all are checked before any is kept |
| `PATCH /api/v1/settings` `{"idle_hours": null}` | Back to the `.env` value |
| `DELETE /api/v1/settings/idle_hours` | The same |
| `POST /api/v1/settings/test-email` `{"to": "you@example.com"}` | Send a test e-mail with the current mail settings |
| `POST /api/v1/alerts/test` `{"channel": "webhook", "webhook": 2}` | Send a test alert to webhook 2 (or `{"channel": "email"}`: to every alert recipient) |
| `POST /api/v1/server/restart` | Apply the `restart` settings; refused with 409 while jobs run |

A wrong value is refused with 400 and the setting's name (`IDLE_HOURS: Input should be greater than or equal to
0.`); a setting that can only be set in `.env` is refused with 400. Secrets (`smtp_password`,
`odoo_admin_password`, `github_webhook_secret`, `alert_webhook_<n>_url`, `alert_webhook_<n>_secret`) are write-only: `value` and `env_value` only say whether they are
set, and they are stored encrypted with the vault key. The audit log records their change as `SMTP_PASSWORD=(set)`,
never the value. Every change sends the `settings` [live event](#8-live-events).

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
| `project` | A project changed: created, changed, deleted, a branch's stage changed (key: the project's id) | The project, as `GET /projects` lists it, or `{"id", "deleted": true}` |
| `build` | An entry of a branch's history was added or changed: a build started, ended or was dropped, a stage changed, a backup was restored (key: the entry's id); only for callers who may see the branch | The entry, as `GET /projects/<id>/builds/<build_id>`, or `{"id", "project", "branch", "removed": true}` for an update that found nothing new |
| `domains` | An instance's custom domains changed: added, removed, checked, issued, renewed (key: the instance) | As `GET /instances/<name>/domains` |
| `job` | A job started, moved to another step, reported progress on its running step (at most once a second), added a note or ended. A job's technical log is never sent (the `job.output` event of earlier versions is gone) | The job, as `GET /jobs/<id>` |
| `metrics` | Every `METRICS_INTERVAL_SECONDS` (5 by default) | The new figures, as `GET /metrics` ([Metrics](#9-metrics)) |
| `settings` | A setting was changed | `{"changed": ["idle_hours", ...]}` |
| `alert` | An alert was raised, repeated, resolved or delivered; only for callers with `audit.view` | The alert, as `GET /alerts` |
| `reset` | After a reconnection, when more was missed than the panel keeps | `{}`: reload everything |

* `?topics=job,instance` sends only those events.
* A line starting with `:` is sent every 20 seconds to keep the connection open through proxies; ignore it.
* When the connection drops, `EventSource` reconnects by itself and sends the last `id` it received
  (`Last-Event-ID`); the stream starts with what was missed, only the latest state of each instance and job.
  Other clients can send `?last_event_id=` instead.
* An open stream checks its caller every minute and ends when the session was signed out or ended, the account was
  deleted, or the API token was revoked. Reconnecting then answers **401**.
* One account or API token can hold **8** streams at once; one more is answered **429**. The panel holds 500 in all;
  beyond that the answer is **503**. Both carry `Retry-After`: wait, then open the stream again. The panel's web
  app does this by itself.

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

The panel measures the server and every instance every `METRICS_INTERVAL_SECONDS` (5 seconds by default, 2 to 60,
changeable on the Settings page) and keeps one hour of figures in memory (they start again empty when the panel
restarts). Container figures come from a continuous `docker stats` stream.

| Figure | Meaning |
| :--- | :--- |
| `cpu_percent` | Share of the whole server's processor (all cores); for an instance, Odoo and its database together |
| `memory_percent`, `memory_bytes`, `memory_limit_bytes` | Memory in use, of the containers' limit (the server's memory when they have none) |
| `requests_per_minute` | Requests the gateway forwarded to Odoo: pages, RPC and API calls (the background traffic of open tabs is left out) |
| `error_rate_percent` | Share of those requests answered with a 5xx error; `null` without requests |
| `response_ms_avg`, `response_ms_p95` | Time until Odoo started answering: average, and the time 95 % of requests stayed under |
| `uptime_seconds` | Since Odoo's container started; `null` when it is not running |
| `disk_bytes` | Database and files, measured every 5 minutes while running |
| Server only: `memory_*_bytes`, `disk_percent`, `disk_*_bytes`, `load_1m`, `cores`, `uptime_seconds`, `instances_running`, `instances_total` | `instances_running`: how many instances run now |
| Server only: `panel_cpu_percent`, `panel_memory_bytes`, `panel_cpu_peak_percent`, `panel_memory_peak_bytes` | The panel's own use (its backend, its web service and, with `EXPOSURE=tunnel`, the connectors): now, and the highest over the history kept |

`GET /api/v1/metrics` returns the latest sample:

```json
{
  "data": {
    "at": 1794430123,
    "interval_seconds": 5,
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
    "server": {"t": [1794426523, ...], "cpu_percent": [11.0, ...], "memory_percent": [...], "disk_percent": [...], "load_1m": [...],
               "instances_running": [3, ...]},
    "instances": {"sales-main": {"t": [...], "cpu_percent": [...], "memory_percent": [...], "requests_per_minute": [...], ...}}
  },
  "meta": {"minutes": 60, "interval_seconds": 5}
}
```

A dashboard loads the history once, then appends the sample of every `metrics` [live event](#8-live-events).
`GET /metrics` and the `metrics` event also carry `capacity`, the estimate below (`null` before the first
measurement).

### Capacity

`GET /api/v1/server/capacity` says how many instances the server can run at the same time comfortably, so a tool
can check before it creates or starts one. How the figures are made, in plain language:
[How many instances can my server run?](ADMIN_GUIDE.md#how-many-instances-can-my-server-run).

```json
{
  "data": {
    "comfortable": 9, "running": 9, "headroom": 0, "limited_by": "memory",
    "warning": true,
    "message": "This server can run about 9 instances at once; 9 are running. Starting another may slow every instance.",
    "overloaded": false, "alert": null, "memory_percent": 71.4, "warning_percent": 85,
    "hard_limit": null, "blocked": false,
    "memory": {"total_bytes": 8589934592, "reserve_bytes": 1073741824, "panel_bytes": 314572800,
               "usable_bytes": 7201619968, "per_instance_bytes": 734003200, "per_instance_source": "default",
               "instance_average_bytes": null, "instance_peak_bytes": null, "instance_largest_bytes": null,
               "measured_instances": 0, "instances": 9},
    "processor": {"cores": 4, "panel_percent": 0.6, "per_instance_percent": null, "instances": null},
    "disk": {"free_bytes": 128849018880, "instance_average_bytes": 1610612736, "more_instances": 80},
    "by_version": [],
    "panel": {"cpu_percent": 0.4, "cpu_peak_percent": 3.1, "memory_bytes": 251658240, "memory_peak_bytes": 314572800,
              "parts": {"backend": {"cpu_percent": 0.3, "memory_bytes": 167772160, "containers": 1},
                        "web": {"cpu_percent": 0.0, "memory_bytes": 20971520, "containers": 1},
                        "connectors": {"cpu_percent": 0.1, "memory_bytes": 62914560, "containers": 3}}}
  }
}
```

| Field | Meaning |
| :--- | :--- |
| `comfortable` | Instances that can run at once: `memory.instances`, or `processor.instances` when that is lower (`limited_by`) |
| `running`, `headroom` | Instances running now or being created or started, and `comfortable - running` (negative when over) |
| `warning`, `message` | `true` when starting one more goes beyond `comfortable`, with the sentence the panel shows |
| `overloaded`, `alert` | `true` when the server's memory use reached `warning_percent` (`CAPACITY_WARNING_PERCENT`) or more instances run than `comfortable`, with the dashboard's sentence |
| `hard_limit`, `blocked` | `MAX_RUNNING_INSTANCES` (`null` when off), and whether creating or starting one more is refused now (403) |
| `running_production` | How many of the running instances are production ones: they count in `running` but a limit never refuses them |
| `memory` | `usable_bytes = total_bytes - reserve_bytes - panel_bytes`; `per_instance_bytes` is the measured peak of a running instance, averaged over `measured_instances`, or `CAPACITY_INSTANCE_MEMORY_MB` (`per_instance_source: "default"`) |
| `processor` | The average share of the server's processor one running instance uses; `instances`: how many fit under `warning_percent` (`null` before one is measured) |
| `disk` | Free disk, the average size of an instance (database and files) and how many more of that size fit |
| `by_version` | Per Odoo version, once instances of it were measured: `{"version": "18.0", "instances": 3, "average_bytes", "peak_bytes"}` |
| `panel` | The panel's own use: now, the highest over the history, and by part |
| `reservations` | The memory all instances reserve under load (their stage's `PRIORITY_*MEMORY_RESERVATION_*`): `total_bytes` and `by_stage` ([Resources and priority](ADMIN_GUIDE.md#resources-and-priority)) |

The estimate is never a refusal unless `MAX_RUNNING_INSTANCES` is set: then `POST /instances` and the `start` action
answer 403 with `detail` "This server runs at most 9 instances at once and 9 are running or starting. Pause one
first." while that many run. A production instance is never refused by it (it counts in the total). With
`MAX_RUNNING_NON_PRODUCTION_PER_USER` (or the number on the person's account) the same two routes, and the upgrade
test, answer 403 "You have 2 of your 2 running instances (not counting production) running or starting. Pause one of
yours first." for the person who created the instance; both limits are off by default.

---

## 10. Signing in from a web app

On a new server there is no account yet. `GET /api/v1/setup` answers `{"needed": true}`, and the first account is
created with the setup code the installer printed (`docker exec sudoo-panel sudoo.sh setup-code` shows it again):

```
POST /api/v1/setup  {"setup_code": "K7QH-3MXP-9TAD", "password": "...",
                     "first_name": "Sam", "last_name": "Lee", "email": "sam@example.com", "phone": ""}
  -> 201 {"data": {"user": "sam", "role": "super_admin", "csrf_token": "...", ...}}    signed in
```

`email` and `first_name` are required. Without `username`, the user name is made from the e-mail address (here
`sam`); it is the account's key in `user` and in `/users/<name>`. The password follows the
[password rules](USER_MANUAL.md#password-rules). The code works once. A wrong one is 403; 5 wrong ones from an address in 15 minutes block it (429). Once an account
exists, `POST /api/v1/setup` is refused with 400.

The panel's own web app then signs in with a session cookie. Without a second factor it is one call; with one, two:

```
POST /api/v1/session            {"username": "sam@example.com", "password": "..."}
  -> 201 {"data": {"user": "sam", "csrf_token": "...", ...}}                 signed in
  -> 200 {"data": {"second_factor": {
            "methods": [{"name": "totp", "label": "Authenticator app"}, {"name": "email", "label": "E-mail code"}],
            "sent_to": "s***@example.com"}}}                                   a code is needed

POST /api/v1/session/second-factor/email          (only to send a new e-mail code)
POST /api/v1/session/second-factor  {"method": "totp", "code": "123456"}
  -> 201 {"data": {"user": "sam", "csrf_token": "...", ...}}
```

`username` takes the e-mail address or the user name. A wrong e-mail address and a wrong password give the same
**401** "Wrong e-mail address or password.", in the same time.

Between the two calls the session opens nothing, for 5 minutes at most.

Project roles (`GET /api/v1/projects/<id>/access` has `model`, `roles` and the matrix): `PUT .../access/matrix`
`{"matrix": {"developer": {"staging": ["instances.view", "instances.connect"]}}}` (null: the defaults), `GET
.../access/people` (who can do what), `GET`/`POST .../access/model` (preview and switch a project of the earlier
model), and temporary access: `GET`/`POST /api/v1/projects/<id>/role-requests` (`{"stage", "level", "hours",
"reason"}`), `POST .../role-requests/<rid>/approve|refuse`, `DELETE .../role-requests/<rid>`. A refused right in a
project answers 403 with a sentence that names the role, the right and the stage.

`GET /api/v1/session/options` (public) says which other ways the page offers, `{"password", "email_code", "github"}`
(true or false). Each is a first step like the password; the second factor follows the same way:

```
POST /api/v1/session/email-code         {"email": "sam@example.com"}
  -> 200 {"data": {"sent_to": "s***@example.com"}}       the same answer whether or not an account uses it
POST /api/v1/session/email-code/verify  {"email": "sam@example.com", "code": "123456"}
  -> 201 signed in, or 200 {"second_factor": ...}

POST /api/v1/session/github             {}
  -> 200 {"data": {"url": "https://github.com/login/oauth/authorize?..."}}    the browser goes there
POST /api/v1/session/github/callback    {"code", "state"}      what GitHub sent back to /login/github
  -> 201 signed in, 200 {"second_factor": ...}, or 200 {"linked": "<login>"} after POST /api/v1/account/github
```

The state is bound to the session cookie that started the flow and works once (10 minutes). Wrong e-mail codes
count like wrong passwords; refused GitHub sign-ins are 403 with the reason. An account whose methods do not include
the one used is refused with 403 (`PATCH /api/v1/users/<name>` `{"sign_in_methods": [...]}` chooses them;
`DELETE /api/v1/users/<name>/github` unlinks GitHub; `DELETE /api/v1/account/github` unlinks one's own).

A signed-in session can be **restricted** (`"restricted"` in the answer). Only `/api/v1/session`,
`/api/v1/account` and `/api/v1/contact` answer until the task is done:

| `restricted` | Why | Done with |
| :--- | :--- | :--- |
| `"password"` | The password was set by someone else (a new account, or `POST /users/<name>/password`) with `must_change_password` | `PUT /api/v1/session/password` `{"current", "new"}` |
| `"second_factor"` | `REQUIRE_SECOND_FACTOR` applies and the account has no second factor (`"must_set_up_second_factor": true`) | setting one up, as below |

Setting up an authenticator app:

```
POST /api/v1/account/second-factors/totp   {}      -> {"secret": "JBSW...", "otpauth_uri": "otpauth://totp/..."}
                                                       (show the URI as a QR code)
PUT  /api/v1/account/second-factors/totp   {"code": "123456"}
  -> 200 {"data": [...factors], "meta": {"recovery_codes": ["k7m2p-x9qrt", ...]}}   show them once
```

With the app set up, the sign-in challenge also lists `{"name": "recovery", "label": "Recovery code"}`: a lost phone
signs in with `POST /api/v1/session/second-factor {"method": "recovery", "code": "k7m2p-x9qrt"}`. Every successful
sign-in is e-mailed to the account when `SIGN_IN_EMAIL` applies.

API tokens are not affected: a token already is something only its system has.

`GET /api/v1/session` returns the same document at any time; read `permissions` from it to decide which controls
to show.

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

With [`X-Sudoo-Acting-For`](#acting-for-someone) the caller is `api:<token name> for <person>`: `own_instances`,
`MAX_INSTANCES_PER_USER` and the waiting time then count per person.

`next_creation_at` (Unix time) is set while the caller has to wait. A creation that breaks a limit is refused with
403 (a number is reached) or 429 with `Retry-After` (wait). Creations that are queued or running count as instances
(of whoever started them), in `instances` and `own_instances` too, so creations started together cannot pass a limit.

---

## 12. Web shell

The Shell tab of the web app ([User manual](USER_MANUAL.md#the-shell-tab)) uses these calls. They are for a person
signed in to the panel: **no API token can obtain a ticket**, whatever its scope (403).

| Method and path | Permission | Body | Answer |
| :--- | :--- | :--- | :--- |
| `GET /instances/<name>/shell` | `instances.view` | | What the caller may open here, and why not: `{"enabled", "production", "running", "status", "targets": {"shell" \| "odoo-shell" \| "psql": {"allowed": true, "user", "recorded"} or {"allowed": false, "code", "reason"}}, "root": {...}, "limits": {"idle_minutes", "max_minutes", "per_user", "per_instance"}, "open", "open_mine"}` |
| `POST /instances/<name>/shell/tickets` | `instances.shell` (`psql`: `instances.database_shell`) | `{"target": "shell", "as_root": false, "confirm_production": false}` | **201** `{"ticket", "expires_in", "socket", "protocol": "sudoo.shell.v1", "target", "container", "container_user", "root", "production", "recorded"}` |
| `GET /instances/<name>/shell/socket` | the ticket | WebSocket, see below | 101 Switching Protocols |
| `GET /shells?instance=<name>` | `shells.supervise` | | Open shells, then past ones (newest first): `id`, `instance`, `target`, `container_user`, `root`, `production`, `user`, `ip`, `started_at`, `ended_at`, `duration_seconds`, `reason`, `recorded`, `recording` (can be played), `open` |
| `DELETE /shells/<id>` | `shells.supervise` | | **204**; the shell closes with reason `ended` |
| `GET /shells/<id>/recording` | `shells.supervise` | | `{"session", "header", "events"}`: an [asciicast v2](https://docs.asciinema.org/manual/asciicast/v2/) recording, `events` as `[seconds, "o" \| "i" \| "r" \| "m", text]` (output, input, new size `COLSxROWS`, note). Audit-logged. |

Refusals of a ticket: 400 (unknown target; production not confirmed: `"confirm_production": true` is needed every
time; `as_root` for another target than `shell`), 403 (shells off, no permission, an API token, production or root
rules), 404 (no such instance), 409 (the instance is not running), 429 (the person or the instance has as many
shells as allowed, counting tickets not used yet; or five tickets are waiting).

**The socket.** Open `wss://<panel>/api/v1/instances/<name>/shell/socket?cols=120&rows=32` from the panel's page
(the `Origin` header must be the panel's address) within `expires_in` seconds, offering two subprotocols:
`sudoo.shell.v1` and `ticket.<ticket>`. The panel session cookie must be the one the ticket was issued to. Before
the upgrade, a refusal is a plain HTTP answer: 400 (subprotocols missing), 401 (ticket unknown, used or expired; or
no session), 403 (wrong origin, another session or instance, rules), 409 (container not running or not the
panel's), 429 (limits). A ticket works once, refused or not.

| Direction | Frame | Meaning |
| :--- | :--- | :--- |
| browser to panel | binary | What is typed, as bytes (at most 64 KB per message) |
| browser to panel | text | `{"type": "resize", "cols": 120, "rows": 32}` |
| panel to browser | binary | What the terminal shows |
| panel to browser | text | `{"type": "ready", "session", "instance", "target", "container", "container_user", "user", "root", "production", "recorded", "idle_seconds", "max_seconds", "started"}` |
| panel to browser | text | `{"type": "notice", "code": "idle" \| "ending", "seconds": 60}`: the shell closes soon |
| panel to browser | text | `{"type": "closed", "reason", "detail"}`, then the socket closes (1000 when the process ended or the browser closed, 4000 otherwise) |

Reasons: `exit` (the program ended), `closed` (the browser closed), `idle`, `max-length`, `not-allowed` (session
ended or rules changed; checked every 15 seconds), `ended` (by a super admin), `stopping` (the panel stopped),
`recording-full`, `error`. Whatever the reason, every process of the shell is then ended.

---

## 13. Code profiler

The Tools tab of the web app ([User manual](USER_MANUAL.md#the-tools-tab)) uses these calls. Every one needs
`instances.profile` in the instance (super admins and administrators, plus the roles `PROFILER_ROLES` adds; project
access and locked branches apply). How it works: [Admin guide](ADMIN_GUIDE.md#28-code-profiler).

| Method and path | Body | Answer |
| :--- | :--- | :--- |
| `GET /instances/<name>/profiler` | | 200, `enabled`, `production`, `production_allowed`, `running` (the instance runs), `status`, `max_minutes`, `keep`, `rate`, `current` (the recording being made, or `null`: `id`, `phase` `preparing`, `recording` or `saving`, `minutes`, `user`, `started_at`, `recording_since`, `ends_at`, `stopping`, `production`), `last_error` (`message`, `code`, `at`, or `null`), `recordings` (newest first: `id`, `user`, `minutes`, `seconds`, `samples`, `threads`, `rate`, `size`, `started_at`, `ended_at`, `stopped_early`, `production`, `links`) |
| `POST /instances/<name>/profiler` | `{"minutes": 1}` (1 to `PROFILER_MAX_MINUTES`); a production instance also `"confirm_production": true` | **202**, the recording being made; 400 for minutes out of range or a missing confirmation, 403 when the profiler or production profiles are off, **409** when the instance is not running or a recording of it is being made. Audit-logged |
| `POST /instances/<name>/profiler/stop` | | **202**, the recording, `stopping: true`: py-spy writes what it has; 409 when nothing is recorded. Audit-logged |
| `GET /instances/<name>/profiles/<id>` | | 200, the recording and its `tree`: `total` (samples), `seconds`, `threads` (`name`, `samples`), `root` (the call tree: `n` name, `w` file and line, `v` samples, `c` children; the first level is the threads, calls under 0.02% of the samples are left out) and `top` (the functions with the most samples in their own code: `name`, `where`, `self`, `total`) |
| `GET /instances/<name>/profiles/<id>/download` | | 200, the speedscope file (`application/json`, attachment), for speedscope.app or another viewer; the processes' command lines are removed from it. Audit-logged |
| `DELETE /instances/<name>/profiles/<id>` | | **204**. Audit-logged |

---

## 14. Client portal

How it works: [Admin guide, Client portal](ADMIN_GUIDE.md#30-client-portal). A project's resource has
`client_access` (the project's switch, or `null` without a client), and so has each branch of
`GET /projects/<id>` and each instance: `state` (`open`, `closed`, `timed`), `until`, `scope` (`project`, or `branch`
for a branch's own switch), `changed_by`, `changed_at`, `emergency_allowed`, `clients`, and `lets_you_through` where
the answer is for you. Inside a closure the usual calls answer 403 with "closed by its client".

The panel's people:

| Method and path | Body | Answer |
| :--- | :--- | :--- |
| `GET /projects/<id>/clients` | | 200, `clients`, `invited`, `project` and `branches` (switches), `allow`, `emergency_allowed`, `passes`, `requests`; `meta`: `may_manage`, `may_force`, `emergency_minutes`, `request_hours`, `request_max_hours`, `portal_url` (`instances.view` in the project) |
| `POST /projects/<id>/clients` | `{"email", "first_name"?, "last_name"?, "backups"?, "shell"?}` | **201**: an existing client account is added, a new address invited (`clients.manage` and `projects.access` in the project; 402 without the premium teams package, 403 without the plan's `client_portal`; 409 for a staff address or a closed project) |
| `PATCH /projects/<id>/clients/<name>` | `{"backups"?, "shell"?}` | 200 (as POST) |
| `DELETE /projects/<id>/clients/<name>` | | **204** (`clients.manage`; 409 for the only client of a closed project) |
| `GET /projects/<id>/access-requests` | | 200, the requests: `id`, `branch`, `by`, `reason`, `hours`, `status` (`pending`, `approved`, `refused`, `lapsed`), `asked_at`, `decided_by`, `decided_at`, `people`, `until`, `note` |
| `POST /projects/<id>/access-requests` | `{"branch"?, "reason", "hours"?}` | **201**, the request; 409 when it is open or you asked already. The clients are e-mailed |
| `POST /projects/<id>/emergency-access` | `{"branch"?, "reason", "confirm"}` (`confirm`: the branch's name, or the project's id) | **201**, the access (`id`, `kind`, `branch`, `people`, `until`, `reason`); 403 unless a signed-in super admin, or when the client turned it off. The clients are e-mailed at once |

The client (a client account's session; nothing else is open to it):

| Method and path | Body | Answer |
| :--- | :--- | :--- |
| `GET /client/projects` | | 200, its projects: `id`, `name`, `switch`, `closed_branches`, `pending_requests`, `emergency_active`, `instances` |
| `GET /client/projects/<id>` | | 200, the project with `branches` (`name`, `stage`, `switch`, `instances`: `name`, `status`, `stage`, `url`, `odoo`), `allow`, `people` (whom it may name), `emergency_allowed`, `passes`, `requests`, `notices` (emergency access of the last 7 days), `given`; `meta`: `open_hours`, `open_max_hours`, `request_max_hours`, `emergency_minutes` |
| `PUT /client/projects/<id>/switch` | `{"branch"?, "state", "hours"?, "inherit"?}` | 200, or **202** (below) |
| `PUT /client/projects/<id>/allow` | `{"people": [names]}` | 200, or **202** (below) |
| `PUT /client/projects/<id>/emergency` | `{"allowed": bool}` | 200, or **202** (below) |
| `PUT /client/projects/<id>/lock` | `{"branch"?, "locked": bool, "reason"?}` | 200, or **202** (below): the client's lock of the branch, or of the whole project without `branch` |
| `POST /client/confirmations/<id>` | `{"code"}` or `{"token"}` (the link's secret) | 200 `{"done": true, "project"}`: the change that waited is made; 401 for a wrong code, 403 from another session, 410 used or expired |
| `DELETE /client/confirmations/<id>` | | **204**: the change that waited is dropped |
| `DELETE /client/projects/<id>/passes/<pass>` | | **204** |
| `POST /client/requests/<id>/approve` | `{"hours"?, "people"?, "note"?}` | 200, or **202** (below) |
| `POST /client/requests/<id>/refuse` | `{"note"?}` | 200, the request |
| `GET /client/projects/<id>/backups` | | 200, its instances' backups where it was given them (download: `GET /backups/<file>`) |
| `GET /client/projects/<id>/activity` | `?kind=all\|access&limit&offset` | 200, the project's audit entries (`at`, `user`, `event`, `allowed`, `branch`, `instance`, `detail`, `address` (its own only), `emergency`) |
| `GET /client/projects/<id>/activity/export` | `?kind=` | 200, CSV (the audit export's columns), a year at most |

While `CLIENT_CONFIRMATIONS` is on, the switch, locks, emergency access, the people let in and approvals answer
**202** `{"done": false, "confirmation": {"id", "kind", "summary", "asked_at", "expires_at", "status"}}`: the change
waits until the client confirms it (`POST /client/confirmations/<id>`), and `GET /client/projects/<id>` lists those
waiting in `confirmations` (and the project's lock in `locked`; each switch has `locked` too). With it off they answer
200 `{"done": true}` at once.

`GET /portal` (public) says whether the address is the portal's (`portal`), with `name` (`PORTAL_NAME`) and
`brand_color`; `POST /session` answers 200 `{"redirect": {"address", "url"}}` when the account signs in at the other
address.

---

## 15. Maintenance windows

How it works: [Admin guide, Maintenance windows](ADMIN_GUIDE.md#32-maintenance-windows). Items arrive only from the
hub, signed. Times are ISO 8601 with the offset of `TIMEZONE`; a `start` without an offset is read in `TIMEZONE`.

An item: `id`, `kind` (`panel_update`, `odoo_images`, `restart`, `custom`), `scope` (`panel`, `server`, `instance`,
`project`), `targets`, `version`, `title` and `reason` (`en`, `ar`), `estimated_minutes`, `earliest_start`,
`deadline` (the latest start), `required`, `status` (`proposed`, `scheduled`, `running`, `done`, `failed`,
`cancelled`), `window` (`start`, `end`, `source`: `panel`, `client`, `account` or `default`, `chosen_by`,
`chosen_at`; `null` before a choice), `choose_from` and `choose_until`, `presets` (`id`: `tonight`, `weekend` or
`default`, `start`, `end`), `auto_at` and `default_start` (when and where the default window applies), `instances`
(`name`, `stage`, `project`, `held`: why it would be skipped), `approvals` (projects whose client let it run),
`steps` (`name`, `instance`, `stage`, `status`, `job`, `detail`), `detail`, `received_at`, `started_at`,
`finished_at`.

| Method and path | Body | Answer |
| :--- | :--- | :--- |
| `GET /maintenance` | | 200, the open items and those that ended in the last 30 days; `meta`: `timezone`, `lead_minutes`, `default_window`, `default_days`, `tonight_time`, `weekend_day`, `reminder_hours`, `client_choice`, `may_choose` (`instances.view`) |
| `PUT /maintenance/<id>/window` | `{"preset": "tonight" \| "weekend" \| "default"}` or `{"start": "2026-11-29T02:00"}` | 200, the item (`maintenance.manage`); 409 once it runs or ended; 422 outside its bounds |
| `GET /client/maintenance` | | 200 (a client): the items that work on its projects, with only its instances, `projects` and `held` (its closed or locked ones); `meta` as above with `confirmations` |
| `PUT /client/maintenance/<id>/window` | as above | 200 `{"done": true}`, or **202** `{"confirmation"}` while `CLIENT_CONFIRMATIONS` is on; 403 unless `MAINTENANCE_CLIENT_CHOICE` |
| `POST /client/maintenance/<id>/approve` | `{"project"?}` | the same: lets it run on that closed or locked project |

`GET /public-status/<token>` also has `maintenance`: running or scheduled within 14 days on the project's instances
(`kind`, `title`, `status`, `window_start`, `window_end`; never the reason).

**The hub's side** (signed both ways, [Security](SECURITY.md#22-maintenance-windows)): the heartbeat's answer carries
the command `maintenance` (its payload: `maintenance_id`, `kind`, `scope`, `targets`, `version`, `reason`,
`estimated_minutes`, `earliest_start`, `deadline`, `required`), acknowledged `done` when recorded or `failed` with the
allow-list's reason, and the lease's claim `maintenance` (each item with `status`, `window_start`, `window_end`,
`window_source`, `chosen_by`, `updated_at`). The panel reports with `POST /api/v1/maintenance/<id>/report`
(`status`: `scheduled` with `window_start`, `window_end`, `window_source`, `chosen_by`; `running`, `done` or
`failed` with `steps` and `detail`).
