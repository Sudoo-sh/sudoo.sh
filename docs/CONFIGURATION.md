# Configuration

**Audience:** administrators of the server.
**Covers:** every setting of the panel, with its type, default, meaning, and where and when it can be changed.

The defaults are environment variables, kept in `/opt/odoo/manager/.env` on the server
([.env.example](../.env.example) is the template). **Almost every setting can also be changed on the panel's
Settings page** by a super admin (or with `PATCH /api/v1/settings`, see [Panel API](PANEL_API.md#7-settings)). Such
a change is kept in `/opt/odoo/manager/settings.json` on top of `.env`; **Use .env** on the page brings back the
`.env` value.

The **Applies** column of each table says how a change takes effect:

| Applies | Changed where | Takes effect |
| :--- | :--- | :--- |
| **live** | Settings page or `.env` | On the Settings page: at once, without a restart. |
| **restart** | Settings page or `.env` | These settings choose plugins. On the Settings page: after **Restart the panel** (Settings, Advanced), which stops only the backend for a few seconds and is refused while jobs run. |
| `.env` only | `.env` only | Folders, ports, the exposure and the storage, which the web service and the containers read too. The Settings page lists them, read-only, under **Advanced**, **Server**. |

A change made in `.env` itself (any class) applies when the containers are recreated:
`cd /opt/odoo/manager && docker compose up -d`. A value changed on the Settings page wins over `.env` until it is
reset with **Use .env**.

* Names are not case sensitive. A wrong value (for example `IDLE_HOURS=soon`) stops the panel at start-up with a
  message naming the setting; on the Settings page it is refused with the same message.
* See what the panel actually uses: `docker exec sudoo-panel sudoo.sh config`.
* The settings are defined in `src/sudoo/config.py` (the lists `EDITABLE`, `RESTART` and `SECRET`); a test fails when
  one of them is missing here.
* Secret settings (`SMTP_PASSWORD`, `ODOO_ADMIN_PASSWORD`, `GITHUB_WEBHOOK_SECRET`, the alert webhooks' addresses
  and secrets) are write-only on the Settings
  page: it only shows whether they are set. Changed there, they are stored encrypted with the vault key
  ([Security](SECURITY.md#4-stored-secrets-the-vault)).

---

## Contents

1. [Contact](#contact)
2. [Odoo](#odoo)
3. [Instance containers](#instance-containers)
4. [Enterprise add-ons (plugin)](#enterprise-add-ons-plugin)
5. [Exposure (plugin)](#exposure-plugin)
6. [Code updates (plugins)](#code-updates-plugins)
7. [Storage, sign-in and e-mail (plugins)](#storage-sign-in-and-e-mail-plugins)
8. [Limits](#limits)
9. [Gateway](#gateway)
10. [Custom domains](#custom-domains)
11. [Ports](#ports)
12. [Behaviour](#behaviour)
13. [Database upgrades](#database-upgrades)
14. [Off-site copies](#off-site-copies)
15. [Alerts](#alerts)
16. [Web shell](#web-shell)
17. [Servers](#servers)
18. [Mail catcher](#mail-catcher)
19. [Help panel](#help-panel)
20. [Marketplace](#marketplace)
21. [Premium features](#premium-features)
22. [Support access](#support-access)
23. [Locations](#locations)

---

## Contact

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `ADMIN_CONTACT_NAME` | str | `the administrator` | **live** | Who users are told to contact. |
| `ADMIN_CONTACT_PHONE` | str | empty | **live** | Phone number added to that message. |
| `ADMIN_CONTACT_EMAIL` | str | empty | **live** | E-mail address added to that message. |
| `BRAND_COLOR` | str | `#714B67` | **live** | Accent colour of the web app and of e-mails (title mark, buttons), as `#RRGGBB`. |
| `DEFAULT_LANGUAGE` | str | `en` | **live** | Language of the web app for accounts that have not chosen one: `en` (English) or `ar` (Arabic, right to left). Each person can choose their own on the Account page; before signing in, the browser's language decides. E-mails stay in English. |
| `LICENCE_REQUIRED_VERSION` | str | `1.0` | **live** | Oldest version of the Sudoo.sh Licence Agreement that counts. While no super admin has accepted that version (or a later one), the next super admin to sign in sees the acceptance step before anything else; other people, API tokens and the instances are not affected. It cannot be newer than the version this release bundles (**Settings, About**). |
| `LINK_DEADLINE_DAYS` | int | `7` | **live** | Days after the panel's first run to link it to a Sudoo.sh account (**Settings, Marketplace**). Until then super admins and administrators see a banner with the days left; after that, while the panel is not linked, no new instances, no stage changes into production and no updates or deploys. Instances keep serving and backups keep running; linking lifts the limits at once. The first run is recorded once and never changes (**Settings, About**). |

---

## Odoo

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `ODOO_VERSIONS` | str | `auto` | **live** | Versions offered: "auto" (every published version of the official image from odoo_min_version) or a comma-separated list such as 17.0,18.0,saas-18.2. |
| `ODOO_MIN_VERSION` | str | `16.0` | **live** | Oldest version offered when odoo_versions is "auto". |
| `PREPULL_IMAGES` | bool | `false` | **live** | Download the Odoo image of every offered version, and the PostgreSQL image, in the background (checked every 30 minutes), so the first instance of a version does not wait for the download. Only missing images are downloaded; each Odoo image takes 1-3 GB of disk. |
| `ODOO_IMAGE` | str | `odoo` | **live** | Docker image of Odoo; the tag is the Odoo version. Used for instances created after the change. |
| `POSTGRES_IMAGE` | str | `postgres:16` | **live** | Docker image of each instance's database. Used for instances created after the change. |
| `ODOO_ADMIN_PASSWORD` | str | `admin` | **live** | Password of the Odoo user "admin" in new instances. Secret: write-only on the Settings page. |

An instance keeps the images it was created with, pinned by digest; **Update Odoo and PostgreSQL images** on its
page refreshes them (same names and tags). See [Administrator guide](ADMIN_GUIDE.md#hardened-containers-and-changes-made-by-hand).

---

## Instance containers

Process limit and log rotation of each instance's `odoo` and `db` containers, and what happens when they are changed
outside the panel. Processor and memory limits and the priority under load are under
[Resources and priority](#resources-and-priority). Changed settings reach an instance the next time it starts or
restarts through the panel; running instances are not restarted for them. What the containers are allowed to do: [Security](SECURITY.md#10-instance-containers).

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `CONTAINER_PIDS_LIMIT` | int | `1024` | **live** | Processes and threads one instance container may run at the same time (at least 64). |
| `CONTAINER_LOG_MAX_SIZE` | str | `10m` | **live** | Size of a container's log file before it is rotated (`k`, `m` or `g`). |
| `CONTAINER_LOG_MAX_FILES` | int | `3` | **live** | Rotated log files kept per container (1 to 20). |
| `DRIFT_ACTION` | str | `restore` | **live** | When an instance's containers or files were changed outside the panel: `warn` reports it (audit log, banner, e-mail to super admins); `restore` reports it and then recreates Odoo and its database from the panel's own configuration, backing the database up first when its container is affected. Connectors and `.env` are only ever reported. |

The memory of an in-memory `/tmp` counts towards the container's memory limit; very large uploads to Odoo need room
there.

### Resources and priority

Instances have **no processor or memory limit by default**. When the server is busy, production comes first, then
staging, then development: each instance's two containers get its stage's weights. The weights only act under
contention; on a quiet server every instance gets all the processor and memory it asks for. How it works:
[Administrator guide](ADMIN_GUIDE.md#resources-and-priority).

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `PRIORITY_CPU_WEIGHT_PRODUCTION` | int | `4096` | **live** | Processor weight of production instances (Docker `cpu_shares`, 2 to 262144; Docker's default is 1024). |
| `PRIORITY_CPU_WEIGHT_STAGING` | int | `2048` | **live** | Processor weight of staging instances (Docker `cpu_shares`, 2 to 262144; Docker's default is 1024). |
| `PRIORITY_CPU_WEIGHT_DEVELOPMENT` | int | `1024` | **live** | Processor weight of development instances (Docker `cpu_shares`, 2 to 262144; Docker's default is 1024). |
| `PRIORITY_OOM_SCORE_PRODUCTION` | int | `0` | **live** | Out-of-memory score adjustment of production instances (`oom_score_adj`, -999 to 1000). |
| `PRIORITY_OOM_SCORE_STAGING` | int | `300` | **live** | Out-of-memory score adjustment of staging instances (`oom_score_adj`, -999 to 1000). |
| `PRIORITY_OOM_SCORE_DEVELOPMENT` | int | `600` | **live** | Out-of-memory score adjustment of development instances (`oom_score_adj`, -999 to 1000). |
| `PRIORITY_MEMORY_RESERVATION_PRODUCTION` | str | `1g` | **live** | Memory reservation (soft limit) of the Odoo container of production instances, Docker units; `0`: none. |
| `PRIORITY_DB_MEMORY_RESERVATION_PRODUCTION` | str | `512m` | **live** | The same for their database container. |
| `PRIORITY_MEMORY_RESERVATION_STAGING` | str | `0` | **live** | Memory reservation (soft limit) of the Odoo container of staging instances, Docker units; `0`: none. |
| `PRIORITY_DB_MEMORY_RESERVATION_STAGING` | str | `0` | **live** | The same for their database container. |
| `PRIORITY_MEMORY_RESERVATION_DEVELOPMENT` | str | `0` | **live** | Memory reservation (soft limit) of the Odoo container of development instances, Docker units; `0`: none. |
| `PRIORITY_DB_MEMORY_RESERVATION_DEVELOPMENT` | str | `0` | **live** | The same for their database container. |

Default limits of each stage. `0` means "not set": no container limit, and Odoo's own default in `odoo.conf`. An
admin can set any of them for one instance on its **Settings** tab, which wins over the stage's default. With
workers, Odoo serves WebSockets on its own port; the panel publishes it on `127.0.0.1` and the gateway sends them
there.

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `PRODUCTION_ODOO_WORKERS`, `STAGING_ODOO_WORKERS`, `DEVELOPMENT_ODOO_WORKERS` | int | `0` | **live** | Odoo HTTP workers (`workers`); `0`: threaded mode, Odoo's default. |
| `PRODUCTION_ODOO_CRON_THREADS`, `STAGING_ODOO_CRON_THREADS`, `DEVELOPMENT_ODOO_CRON_THREADS` | int | `0` | **live** | Odoo cron workers (`max_cron_threads`); `0`: Odoo's default (2). |
| `PRODUCTION_ODOO_LIMIT_MEMORY_SOFT_MB`, `STAGING_ODOO_LIMIT_MEMORY_SOFT_MB`, `DEVELOPMENT_ODOO_LIMIT_MEMORY_SOFT_MB` | int | `0` | **live** | Memory per worker before Odoo recycles it, MB (`limit_memory_soft`); `0`: Odoo's default (2048). |
| `PRODUCTION_ODOO_LIMIT_MEMORY_HARD_MB`, `STAGING_ODOO_LIMIT_MEMORY_HARD_MB`, `DEVELOPMENT_ODOO_LIMIT_MEMORY_HARD_MB` | int | `0` | **live** | Memory per worker before Odoo stops the request, MB (`limit_memory_hard`); `0`: Odoo's default (2560). |
| `PRODUCTION_ODOO_LIMIT_TIME_CPU`, `STAGING_ODOO_LIMIT_TIME_CPU`, `DEVELOPMENT_ODOO_LIMIT_TIME_CPU` | int | `0` | **live** | Processor seconds per request (`limit_time_cpu`); `0`: Odoo's default (60). |
| `PRODUCTION_ODOO_LIMIT_TIME_REAL`, `STAGING_ODOO_LIMIT_TIME_REAL`, `DEVELOPMENT_ODOO_LIMIT_TIME_REAL` | int | `0` | **live** | Seconds per request (`limit_time_real`); `0`: Odoo's default (120). |
| `PRODUCTION_ODOO_MEMORY_LIMIT`, `STAGING_ODOO_MEMORY_LIMIT`, `DEVELOPMENT_ODOO_MEMORY_LIMIT` | str | `0` | **live** | Memory of the Odoo container, Docker units (`512m`, `2g`, at least `128m`); `0`: no limit. |
| `PRODUCTION_ODOO_CPU_LIMIT`, `STAGING_ODOO_CPU_LIMIT`, `DEVELOPMENT_ODOO_CPU_LIMIT` | float | `0` | **live** | Processor cores of the Odoo container; `0`: no limit. |
| `PRODUCTION_DB_MEMORY_LIMIT`, `STAGING_DB_MEMORY_LIMIT`, `DEVELOPMENT_DB_MEMORY_LIMIT` | str | `0` | **live** | Memory of the database container, Docker units; `0`: no limit. |
| `PRODUCTION_DB_CPU_LIMIT`, `STAGING_DB_CPU_LIMIT`, `DEVELOPMENT_DB_CPU_LIMIT` | float | `0` | **live** | Processor cores of the database container; `0`: no limit. |

`ODOO_MEMORY_LIMIT`, `ODOO_CPU_LIMIT`, `DB_MEMORY_LIMIT` and `DB_CPU_LIMIT` (one limit for every instance, before
3.0) are no longer read: a value changed in the panel became the default of every stage; a value in `.env` is
ignored.

---

## Enterprise add-ons (plugin)

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `ENTERPRISE_SOURCE` | str | `folder` | **restart** | Where Enterprise add-ons come from: "folder", "git" or "none". |
| `ENTERPRISE_PATH` | str | `/opt/odoo/enterprise/{version}` | **live** | folder source: where the add-ons of a version are; {version} (19.0) and {major} (19) are replaced. |
| `ENTERPRISE_GIT_URL` | str | empty | **live** | git source: repository holding one branch per Odoo version. An address with a user name or password in it (`https://user:token@...`) is refused: use an SSH address such as `git@github.com:my-company/odoo-enterprise.git` with a deploy key under **GitHub access**. |
| `ENTERPRISE_GIT_BRANCH` | str | `{version}` | **live** | git source: branch name of a version, e.g. {version}. |
| `ENTERPRISE_MARKER` | str | `web_enterprise` | **live** | Module whose presence proves the add-ons are there. |

---

## Exposure (plugin)

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `EXPOSURE` | str | `cloudflare-quick` | `.env` only | How instances are reached: "cloudflare-quick" or "host-ports". |
| `PUBLIC_HOST` | str | empty | `.env` only | host-ports: the server's public name or address. |
| `PUBLIC_SCHEME` | `http` or `https` | `http` | `.env` only | host-ports: scheme of the public addresses. |
| `CLOUDFLARED_IMAGE` | str | `cloudflare/cloudflared:latest` | **live** | cloudflare-quick: connector image, used for connectors started after the change. |
| `CONNECTOR_PREFIX` | str | `sudoo-connector-` | `.env` only | cloudflare-quick: container name prefix. |
| `CONNECTOR_STATUS_PORT_START` | int | `20300` | `.env` only | cloudflare-quick: first status port of a connector. |
| `PANEL_CONNECTOR_STATUS_PORT` | int | `20242` | `.env` only | cloudflare-quick: status port of the panel connector. |

---

## Code updates (plugins)

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `UPDATE_TRIGGERS` | str | `polling,github-app` | **restart** | What starts code updates, comma-separated: "polling", "github-app" (the GitHub App's webhook), "github-webhook" (one webhook per repository). See [Plugins](PLUGINS.md#5-update-triggers-what-starts-a-code-update). |
| `GITHUB_WEBHOOK_SECRET` | str | empty | **live** | github-webhook: the secret entered on GitHub (20 or more random characters). Secret: write-only on the Settings page. |
| `POLL_MINUTES` | float | `2` | **live** | polling: check GitHub for new commits this often. |

---

## Storage, sign-in and e-mail (plugins)

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `STORAGE` | str | `sqlite` | `.env` only | Where the panel keeps its own data (accounts, tokens, audit log, instance state): `sqlite`, the file `/opt/odoo/manager/sudoo.db`. |
| `AUTH` | str | `local` | **restart** | How user names and passwords are checked: `local`, the panel's own accounts. |
| `SECOND_FACTORS` | str | `email,totp` | **restart** | Second factors offered, comma-separated: `email` (a code by e-mail; only when e-mail is set up) and `totp` (an authenticator app). |
| `REQUIRE_SECOND_FACTOR` | str | `admins` | **live** | Who must use a second factor: `none`, `admins` (super admins and administrators) or `everyone`. Such accounts without one can only set one up after signing in. |
| `SESSION_IDLE_MINUTES` | float | `60` | **live** | Panel sign-out after this long without activity. |
| `SESSION_MAX_HOURS` | float | `12` | **live** | Longest panel session. |
| `RECOVERY_CODES_COUNT` | int | `10` | **live** | How many recovery codes an account gets when it sets up the authenticator app, or makes new ones (4 to 20). Each works once, in place of the app's code. |
| `INVITATION_HOURS` | int | `72` | **live** | How long an invitation link works (1 to 720 hours). New people are invited by e-mail from **Accounts** and choose their own password; the link works once, and sending it again makes the old one stop working. |
| `INVITATION_PASSWORD_FALLBACK` | `no_mail`, `always`, `never` | `no_mail` | **live** | When administrators may still type or generate a new account's password instead of inviting: only while e-mail is not set up (`no_mail`), `always`, or `never`. |
| `INVITATIONS_PER_HOUR` | int | `20` | **live** | Invitations one administrator may send, new or again, per hour. |
| `SIGN_IN_EMAIL` | bool | `true` | **live** | E-mail people each time their account signs in: when, from which address and device, and how. Sent only when e-mail is set up and the account has an address. |
| `SIGN_IN_EMAIL_OPT_OUT` | bool | `true` | **live** | People may turn the sign-in e-mail off for their own account (Account, Security). Off: everyone gets it while `SIGN_IN_EMAIL` is on. |
| `MAILER` | str | `smtp` | **restart** | How the panel sends e-mail: `smtp` or `none`. |
| `MAIL_FROM` | str | empty | **live** | Sender, for example `Sudoo.sh <no-reply@your-site.com>`; empty: `SMTP_USERNAME`. |
| `SMTP_HOST` | str | empty | **live** | The mail server, for example `mail.your-site.com`. Empty: no e-mail, and no e-mail codes. |
| `SMTP_PORT` | int | `587` | **live** | `587` with `starttls`, `465` with `ssl`. |
| `SMTP_SECURITY` | str | `starttls` | **live** | `starttls`, `ssl` or `none` (only for a mail server on the same machine). |
| `SMTP_USERNAME` | str | empty | **live** | Usually the full address of the mailbox. |
| `SMTP_PASSWORD` | str | empty | **live** | Its password (often an "app password"). Secret: write-only on the Settings page. |
| `MAIL_TEMPLATES_DIR` | str | empty | **live** | A folder with a `layout.html` that replaces the frame every e-mail shares (`src/sudoo/mail/templates/layout.html`). The texts of the e-mails are changed on the **E-mail templates** page instead ([Admin guide](ADMIN_GUIDE.md)). |

E-mails are HTML in the style of Odoo's own (a white card, the title on the left, the product name on the right,
a button and a footer), with a plain-text version for mail programs that show text only. A super admin changes
their subjects and texts on the **E-mail templates** page. Sign-in codes also say
when, from which address and which browser they were asked for. The code can be selected with one tap; Gmail's
apps also offer their own **Copy code** button.

### Gmail

1. On the Google account that will send the e-mails, turn on **2-Step Verification**
   (myaccount.google.com, Security).
2. Create an **app password**: myaccount.google.com/apppasswords, name it `Sudoo.sh`. Google shows 16 letters once.
3. In the panel, open **Settings**, then **E-mail**, and enter:

   | Field | Value |
   | :--- | :--- |
   | Mail server | `smtp.gmail.com` |
   | Port | `587` |
   | Encryption | STARTTLS |
   | User name | your Gmail address |
   | Password | the app password |
   | Sender | `Sudoo.sh <your.address@gmail.com>` |

   Click **Save**, then **Send a test** and enter an address to send the test e-mail to. The changes apply at
   once.

4. Or put the same values in `/opt/odoo/manager/.env`:

   ```
   MAILER=smtp
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_SECURITY=starttls
   SMTP_USERNAME=your.address@gmail.com
   SMTP_PASSWORD=the16letterapppassword
   MAIL_FROM=Sudoo.sh <your.address@gmail.com>
   ```

   and apply and test them on the server:

   ```
   cd /opt/odoo/manager && docker compose up -d
   docker exec sudoo-panel sudoo.sh test-email you@example.com
   ```

Gmail sends from the signed-in address (or an alias added under Gmail's **Send mail as**), at most about 500
messages a day for a personal account. With Google Workspace on your own domain, use the domain's address. When
the test fails, the message shows the mail server's answer; for Gmail the password must be an app password, not
the account's password.

### Your website's mailbox

The same fields on the Settings page, or in `.env`:

```
MAIL_FROM=Sudoo.sh <no-reply@your-site.com>
SMTP_HOST=mail.your-site.com
SMTP_USERNAME=no-reply@your-site.com
SMTP_PASSWORD=...
```

---

## Limits

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `RATE_LIMITER` | str | `memory` | **restart** | Where request counts are kept: `memory`. |
| `RATE_LIMIT_READS_PER_MINUTE` | int | `600` | **live** | Requests that read, per minute, per signed-in user or API token (per address before signing in); 0: no limit. |
| `RATE_LIMIT_CHANGES_PER_MINUTE` | int | `60` | **live** | Requests that change something, per minute, counted the same way; 0: no limit. |
| `MAX_INSTANCES` | int | `0` | **live** | Instances on this server at most, those being created included; 0: no limit. |
| `MAX_INSTANCES_PER_USER` | int | `0` | **live** | Instances one person or API token may have created (or be creating) and not yet deleted; 0: no limit. |
| `CREATE_COOLDOWN_MINUTES` | float | `0` | **live** | Minutes one person or token must wait between two creations, for example `60`; 0: no wait. |

Answers over a request limit are `429 Too Many Requests` with `Retry-After`; every limited answer carries
`RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset`.

### Capacity

How many instances the server runs comfortably at the same time, and when the panel warns. The panel never refuses
an instance because of these figures unless `MAX_RUNNING_INSTANCES` is set; how the estimate is made:
[How many instances can my server run?](ADMIN_GUIDE.md#how-many-instances-can-my-server-run).

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `CAPACITY_RESERVE_MB` | int | `1024` | **live** | Memory kept free for the operating system, Docker and the file cache, in MB. The larger of this and `CAPACITY_RESERVE_PERCENT` is used. |
| `CAPACITY_RESERVE_PERCENT` | float | `10` | **live** | The same reserve as a share of the server's memory, 0 to 90. |
| `CAPACITY_INSTANCE_MEMORY_MB` | int | `700` | **live** | Memory one running instance is counted with until the panel has measured one (a few measurements of a running instance in the last hour), in MB. |
| `CAPACITY_WARNING_PERCENT` | float | `85` | **live** | Server memory use, in %, from which the dashboard warns that instances may slow down; also the share of the processor the estimate lets instances use. |
| `MAX_RUNNING_INSTANCES` | int | `0` | **live** | Instances running at once at most, those being created or started included: creating or starting one more (in the panel or the API) is refused with 403. Visitors opening a paused instance still start it. 0: never refused, only warned. |

---

## Gateway

Where the gateway in front of every instance runs, and what it accepts from visitors ([Gateway](concepts/GATEWAY.md)).
Request bodies are passed on to Odoo piece by piece as they arrive, so an upload never sits whole in memory; these
limits keep one visitor from tying up the gateway that every instance shares.

With `GATEWAY_MODE=service` the gateway runs in its own container, `sudoo-gateway`, which has no `.env`: the panel
hands it the values below (also those changed in the panel) through `<ODOO_ROOT>/gateway/routes.json` within two
seconds. Set them in the panel's `.env` or in the panel, as always.

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `GATEWAY_MODE` | text | `service` | `.env` only | Where the gateways run. `service`: in their own container (`sudoo-gateway`, in `compose.yaml`), so the instances keep answering while the panel restarts or is down; while that container is not running, the panel serves the instances itself. `embedded`: inside the panel process, as before version 3.0 (the instances stop answering while the panel restarts; a `sudoo-gateway` container that runs gives the ports back and stays idle). Switching either way needs no connector restart: see [Administrator Guide, section 8](ADMIN_GUIDE.md#8-containers-files-and-folders-on-the-server). |
| `GATEWAY_TAKEOVER_SECONDS` | float | `10` | `.env` only | `service` only: seconds the gateway service may not answer the panel (2 to 600) before the panel serves the instances itself. When the service stopped normally, the panel takes over at once. |
| `GATEWAY_MAX_UPLOAD_MB` | int | `2048` | **live** | Largest request body a visitor may send to an instance (file uploads, imports), in MB. Larger ones get `413 Content Too Large` before Odoo sees them. |
| `GATEWAY_IDLE_TIMEOUT_SECONDS` | float | `120` | **live** | Seconds a visitor may send nothing while sending a request body (5 to 3600); then the gateway answers `408 Request Timeout` and frees the connection. |
| `GATEWAY_WEBSOCKET_MAX_MESSAGE_MB` | int | `16` | **live** | Largest single WebSocket message passed to or from Odoo (Odoo's own live updates are far smaller); a larger one closes that connection (code 1009). |
| `GATEWAY_HEADER_TIMEOUT_SECONDS` | float | `30` | `.env` only | Seconds a visitor's connection has to send a request's line and headers (1 to 3600), counted from when it opens and again after every answer; then the gateway closes it. Requests being answered are not affected: long polling, WebSockets and slow uploads (see `GATEWAY_IDLE_TIMEOUT_SECONDS`) keep going. |
| `GATEWAY_MAX_CONNECTIONS` | int | `1000` | `.env` only | Visitors' connections open at once to one instance's gateway. A new one beyond it is answered `503 Service Unavailable` (`Retry-After: 5`) and closed. A browser tab of Odoo uses up to about 7. |
| `GATEWAY_MAX_CONNECTIONS_TOTAL` | int | `4000` | `.env` only | Visitors' connections open at once to all gateways together; beyond it, the same `503`. Each one can use two of the panel's open files (the visitor's and the one to Odoo): keep this well below the open-files limit of the process that runs the gateways (`ulimit -n` in the `sudoo-gateway` container, or `sudoo-panel` with `GATEWAY_MODE=embedded`). |

---

## Custom domains

Instances answering on your own names, such as `erp.client.com`, over https
([Administrator Guide, Custom domains](ADMIN_GUIDE.md#19-custom-domains-and-certificates)). Off by default. Every
value here can be changed on the Settings page except the two ports.

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `CUSTOM_DOMAINS` | text | `off` | **live** | `off`: no custom domains (recorded ones are kept, not served). `acme`: the gateway answers custom domains on ports 80 and 443 of the server, gets a certificate for each from the ACME authority and renews it; needs a server that accepts connections on those ports. `external`: something in front (your own proxy, or a Cloudflare named tunnel on your own account) handles https and sends the visits to the instance's gateway port; the panel only records the domains, checks that they reach the instance, and uses the primary one as its address. Choosing `acme` means agreeing to the ACME authority's terms of service (Let's Encrypt's subscriber agreement). |
| `ACME_DIRECTORY` | text | `production` | **live** | The ACME authority: `production` (Let's Encrypt), `staging` (Let's Encrypt's test authority: certificates browsers do not trust, with far higher limits, for trying the setup out) or the `https://` address of another authority's ACME directory. |
| `ACME_EMAIL` | text | empty | **live** | Contact e-mail given to the ACME authority when the panel creates its account there; empty: `ADMIN_CONTACT_EMAIL`, or none. |
| `CERTIFICATE_RENEW_DAYS` | int | `30` | **live** | Renew a certificate this many days before it expires (1 to 90); a short-lived certificate is renewed a third of its lifetime before it expires at the latest. |
| `CERTIFICATE_RETRY_MAX_HOURS` | float | `24` | **live** | After a failed issuance or renewal the panel tries again after 5 minutes, then 10, 20 and so on, at most this many hours apart (1 to 168). |
| `DOMAINS_PER_INSTANCE` | int | `5` | **live** | Custom domains one instance may have (0 to 100); `0`: none. |
| `DOMAIN_DNS_TARGET` | text | empty | **live** | The server's public name (or address) custom domains point to; empty: `PUBLIC_HOST`. A name is shown to users as a CNAME record, an address as an A or AAAA record. Without either, no domain can be checked. |
| `DOMAIN_DNS_TIMEOUT_SECONDS` | float | `5` | **live** | Seconds one DNS lookup of a custom domain may take (1 to 60). |
| `DOMAIN_HTTP_PROBE` | bool | `true` | **live** | Before asking for a certificate, fetch a check answer through the domain over http (port 80), as the ACME authority will, so a closed port is reported clearly instead of costing a failed attempt. Turn it off when the server cannot reach its own public address (some networks do not allow it). |
| `DOMAIN_HTTP_PORT` | int | `80` | `.env` only | Port the gateway answers custom domains on over http: ACME checks and the redirect to https. The ACME authority always connects to port 80: change this only when something forwards port 80 to it. |
| `DOMAIN_HTTPS_PORT` | int | `443` | `.env` only | Port the gateway answers custom domains on over https. |
| `MANAGED_SUBDOMAINS` | bool | `true` | **live** | Give each instance a name under Sudoo.sh's domain, `<branch>.<stage>.<company>.<base>`, when the linked Sudoo.sh account offers them (every plan; [Administrator Guide, Managed subdomains](ADMIN_GUIDE.md#managed-subdomains)). Sudoo.sh keeps the DNS records; the gateway answers the name on `DOMAIN_HTTP_PORT` / `DOMAIN_HTTPS_PORT` and the panel gets and renews its certificate itself, whatever `CUSTOM_DOMAINS` says (ports 80 and 443 must reach the server). `false`: no name is asked for and none is served; names held are released when their instance is deleted. |
| `MANAGED_SUBDOMAIN_COMPANY` | text | empty | **live** | The company part of the names (a DNS label: `a`-`z`, `0`-`9`, `-`), asked for with the account's first name; empty: Sudoo.sh makes it from the company's name. It cannot change once the account has a name. |
| `MANAGED_SUBDOMAINS_CHECK_MINUTES` | int | `15` | **live** | Minutes between two comparisons of the instances with their names (1 to 1440); new and deleted instances and branch or stage changes are handled at once. |

---

## Ports

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `PANEL_PORT` | int | `9000` | `.env` only | Port of the panel: the web app (container `sudoo-web`) listens here. |
| `API_PORT` | int | `9002` | `.env` only | Port of the backend (container `sudoo-panel`), on 127.0.0.1 only; the web app forwards `/api`, `/hooks` and `/docs` to it. |
| `EVENTS_PORT` | int | `9001` | `.env` only | Port of the live event stream (`/api/v1/events`), on 127.0.0.1 only. |
| `GATEWAY_PORT_START` | int | `8069` | `.env` only | First gateway port (one per instance). |
| `ODOO_PORT_START` | int | `28069` | `.env` only | First Odoo port (one per instance, 127.0.0.1 only). |

---

## Behaviour

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `IDLE_HOURS` | float | `6` | **live** | Pause an instance after this many hours without visits; 0: never. |
| `AUTO_PAUSE` | bool | `true` | **live** | New instances pause when idle (each instance can change it). |
| `AUTO_UPDATE` | bool | `true` | **live** | New instances get new code from their branch automatically. |
| `AUTO_UPGRADE` | bool | `true` | **live** | New instances upgrade changed modules after an update. |
| `DELETE_AFTER_DAYS` | int | `30` | **live** | Lifetime offered for new instances, in days; 0: keep. |
| `METRICS_INTERVAL_SECONDS` | float | `5` | **live** | Seconds between two measurements of the server and the instances (2 to 60). One hour of figures is kept. |
| `DAILY_BACKUPS` | bool | `true` | **live** | Once a day, back up every instance that was used since its last daily backup. Each instance keeps only its latest daily backup; they are removed when the instance is deleted. |
| `DAILY_BACKUP_TIME` | text | `00:00` | **live** | When the daily backups run, `HH:MM` on a 24-hour clock, in `TIMEZONE`. |
| `TIMEZONE` | text | `UTC` | **live** | Time zone of `DAILY_BACKUP_TIME` and of the times in backup names shown in the panel, for example `Africa/Cairo`. |
| `ROLLBACK_HOURS` | float | `24` | **live** | After a restore, the state the instance had before it is kept this many hours (up to 720), and the instance page offers **Rollback** meanwhile. Then that backup is removed. |
| `SAFE_UPDATES` | bool | `true` | **live** | Before an update that changes an instance's code (a push to its branch, **Get latest code**, **Update submodules**), back up its database and files (kind `before-update`); if the update then fails, put the previous code back, restore that backup when the database may have changed, and start Odoo again. `false`: updates run as before, without the backup and the automatic rollback. See [Keeping the code up to date](USER_MANUAL.md#6-keeping-the-code-up-to-date). |

### Stages

What the stage of an instance changes (production, staging, development; see
[Stages](USER_MANUAL.md#stages-production-staging-and-development)). A development instance follows the settings
above exactly. A production instance is never paused or deleted automatically, is backed up every day whatever
`DAILY_BACKUPS` says (even when nobody used it), and is always updated with `SAFE_UPDATES` behaviour.

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `MAX_PRODUCTION_INSTANCES` | int | `1` | **live** | Instances that may be in production at the same time, those being created as production included. Creating one more, or moving one more to production, is refused with 409. 0: no limit. |
| `PRODUCTION_AUTO_UPDATE` | bool | `false` | **live** | New production instances get new code from their branch automatically. Off: a production instance gets new code only when someone asks (**Get latest code**), or once **Get new code automatically** is switched on for it. |
| `STAGING_DELETE_AFTER_DAYS` | int | `90` | **live** | Lifetime offered for new staging instances, in days; 0: keep. |
| `STAGING_DAILY_BACKUPS` | bool | `true` | **live** | Staging instances get the daily backup too (while `DAILY_BACKUPS` is on). `false`: only their manual and safety backups. |

### Projects

A project is one GitHub repository; each of its branches can run as an instance, in the branch's stage (see
[Projects and branches](ADMIN_GUIDE.md#24-projects-and-branches)). The settings
below apply to every project; all but `PROJECT_PRODUCTION_BRANCHES` are defaults that a project can change on its
**Settings** tab (a project's own value wins; reset it to follow the panel again). The lifetimes of new development and
staging instances of a project follow `DELETE_AFTER_DAYS` and `STAGING_DELETE_AFTER_DAYS` unless the project sets
its own.

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `PROJECT_PRODUCTION_BRANCHES` | int | `1` | **live** | Production branches one project may have. Making one more branch production (or creating one more production instance of the project) is refused with 409. 0: no limit; `MAX_PRODUCTION_INSTANCES` still limits the whole server. |
| `PROJECT_AUTO_DEVELOPMENT` | bool | `true` | **live** | A new branch pushed to a project's repository gets a development instance by itself (through the GitHub App's webhook, the `github-webhook` trigger, or the branch check every `POLL_MINUTES`). |
| `PROJECT_MAX_AUTO_DEVELOPMENT` | int | `3` | **live** | Development instances a project creates by itself for new branches, at most at the same time; one more new branch then waits until one of them is deleted (the project page offers **Create instance**). 0: no limit. Instances created by people do not count. |
| `PROJECT_DEVELOPMENT_DEMO_DATA` | bool | `true` | **live** | Development instances of projects start with Odoo's demo data. Staging and production never get demo data. |
| `PROJECT_STAGING_ON_PUSH` | text | `rebuild` | **live** | A push to a staging branch: `rebuild` replaces the staging instance's data with a new neutralised copy of production, then takes the new code; `keep_data` only takes the new code (like a development instance). |
| `PROJECT_BRANCH_DELETED` | text | `archive` | **live** | A branch deleted on GitHub: its development instance is kept (`keep`), backed up and switched off (`archive`), or deleted after a backup (`delete`). A staging instance is archived at most, a production instance only shows that its branch is gone. |
| `PROJECT_UPGRADE_TEST_ON_PUSH` | bool | `true` | **live** | A push to a project's upgrade branch (named after the next Odoo version, such as `19.0`) runs its database upgrade test again on the test instance: a fresh backup of the instance being upgraded, OpenUpgrade, neutralised. With Odoo's upgrade service (Enterprise) the push only gives the test instance the new code, because the subscription code is never kept. See [Database upgrades](ADMIN_GUIDE.md#25-database-upgrades). |

## Database upgrades

How databases move to a newer Odoo version ([User Manual](USER_MANUAL.md#upgrading-to-a-newer-odoo-version),
[Admin Guide](ADMIN_GUIDE.md#25-database-upgrades)). Only administrators and super admins upgrade (permission
`instances.upgrade`).

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `UPGRADE_METHODS` | text | `enterprise,openupgrade` | **live** | The methods allowed, comma-separated: `enterprise` (Enterprise databases: Odoo S.A.'s upgrade service, with the customer's own Enterprise subscription code, under Odoo's terms) and `openupgrade` (Community databases: OCA OpenUpgrade). Empty: no upgrades. |
| `UPGRADE_SERVICE_URL` | text | `https://upgrade.odoo.com` | `.env` only | Odoo's upgrade service. Its upgrade script is downloaded from `<address>/upgrade` each time an upgrade runs. Only in `.env`, because the subscription code is sent there. |
| `OPENUPGRADE_REPOSITORY` | text | `https://github.com/OCA/OpenUpgrade` | **live** | The GitHub repository of OpenUpgrade. The temporary container that runs an upgrade downloads its sources from here, at the pinned commit. |
| `OPENUPGRADE_REFS` | text | the 16.0 to 20.0 branches, pinned | **live** | The OpenUpgrade commit used to reach each version: `version=commit` pairs (full 40-character commits), comma-separated, for example `19.0=bdae60ef5cece7c0a5e02ef87bc5834ddb310d35`. A version without one cannot be reached with OpenUpgrade. Move a pin forward after checking OpenUpgrade's changes. |
| `OPENUPGRADE_LIB` | text | `openupgradelib==3.13.7` | **live** | The openupgradelib package installed in the temporary container (a pip requirement). |
| `UPGRADE_ROLLBACK_MINUTES` | int | `60` | **live** | After production was upgraded: how long **Cancel the upgrade** is offered (back to the backup taken just before it), and how long the panel watches Odoo, putting the instance back by itself when Odoo stops answering for two minutes. 0 to 10080; 0: neither. |
| `UPGRADE_MAX_HOURS` | float | `8` | **live** | Longest time one upgrade (Odoo's service, or OpenUpgrade) may take before it is stopped (up to 72). |

---

## Off-site copies

Backups copied to S3-compatible storage off this server, each with a manifest of SHA-256 checksums signed with the
panel's Ed25519 key, and checked before they are used. How it works, what to set up and the trust model:
[Admin guide, Off-site copies](ADMIN_GUIDE.md#20-off-site-copies) and [Security](SECURITY.md#14-off-site-copies). All
of them are on the Settings page, **Backups** tab (with **Test connection** and the signing key); changing them needs
the permission `offsite.configure` (super admins).

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `OFFSITE_BACKUPS` | bool | `false` | **live** | Copy backups off-site. The first time it is switched on, the panel makes its signing key. |
| `OFFSITE_ENDPOINT` | text | empty | **live** | Address of the storage's S3 API, for example `https://<account-id>.r2.cloudflarestorage.com`, `https://s3.eu-central-1.amazonaws.com`, `https://s3.us-west-004.backblazeb2.com`. `https://` only (`http://` only for `localhost`); no path. |
| `OFFSITE_REGION` | text | `auto` | **live** | Region of the bucket: `auto` for Cloudflare R2, the bucket's region otherwise (`eu-central-1`, `us-west-004`, `us-east-1` for MinIO). |
| `OFFSITE_BUCKET` | text | empty | **live** | The bucket. Create it first, private, ideally with object versioning or a lock (see the admin guide). |
| `OFFSITE_PREFIX` | text | `sudoo` | **live** | Folder inside the bucket for this panel's copies; several panels can share a bucket with different folders. |
| `OFFSITE_ADDRESSING` | text | `path` | **live** | `path` (`endpoint/bucket/file`, works with R2, B2, MinIO and S3) or `virtual` (`bucket.endpoint/file`). |
| `OFFSITE_ACCESS_KEY_ID` | text | empty | **live** | Access key ID of a key that may read, write, list and delete in the bucket (that bucket only). |
| `OFFSITE_SECRET_ACCESS_KEY` | text | empty | **live** | Its secret. Secret: write-only on the Settings page, stored encrypted with the vault key. |
| `OFFSITE_OTHER_STAGES` | bool | `false` | **live** | Copy the backups of staging and development instances too. Production instances' backups are always copied while `OFFSITE_BACKUPS` is on. |
| `OFFSITE_KINDS` | text | `manual,daily,deletion` | **live** | Which backups are copied, comma-separated: `manual`, `daily`, `deletion`, `before-update`, `before-restore`, `before-rollback`, `before-repair`, `staging-copy`, `before-upgrade`. |
| `OFFSITE_KEEP_DAILY_PRODUCTION` | int | `30` | **live** | Production: keep the newest copy of each of this many most recent days that have a copy (1 to 3650). |
| `OFFSITE_KEEP_MONTHLY_PRODUCTION` | int | `12` | **live** | Production: also keep the newest copy of each of this many most recent months (0 to 600). |
| `OFFSITE_KEEP_DAILY_OTHER` | int | `7` | **live** | Staging and development: days kept, as above. |
| `OFFSITE_KEEP_MONTHLY_OTHER` | int | `0` | **live** | Staging and development: months kept, as above. |
| `OFFSITE_VERIFY_DAYS` | float | `7` | **live** | When an instance's newest verified copy is older than this, download its newest copy and check it (signature and every checksum). `0`: only when someone chooses **Verify now**. |
| `OFFSITE_TIMEOUT_SECONDS` | float | `60` | **live** | Seconds one request to the storage may wait for an answer (5 to 600). |

The newest verified copy of an instance (before any check: its newest stored copy) is never removed by the retention
rules, whatever they say.

---

## Alerts

Who is told when something needs attention, about what, and how often. How alerts work, their e-mails, webhooks
and the bell: [Admin guide, Alerts](ADMIN_GUIDE.md#16-alerts). All of them are on the Settings page, **Alerts** tab,
with **Send a test alert** for the e-mail and each webhook.

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `ALERTS` | bool | `true` | **live** | Send alerts at all. `false`: no alert is sent, listed in the bell or kept. |
| `ALERT_SUPER_ADMINS` | bool | `true` | **live** | E-mail alerts to every super admin who has an e-mail address. |
| `ALERT_EMAILS` | text | empty | **live** | More addresses that receive alert e-mails, comma-separated (an on-call mailbox, say); at most 50. |
| `ALERT_JOB_FAILED` | bool | `true` | **live** | A job failed: its plain hint (the one the panel shows) and a link to its log. |
| `ALERT_UPDATE_FAILED` | bool | `true` | **live** | An update failed and the instance was put back (`SAFE_UPDATES`), or putting it back failed too. Such a job sends this alert instead of `ALERT_JOB_FAILED`. |
| `ALERT_BACKUP_FAILED` | bool | `true` | **live** | A backup job (manual or daily) failed. Sent instead of `ALERT_JOB_FAILED`. |
| `ALERT_INSTANCE_DOWN` | bool | `true` | **live** | An instance that should run (not paused, not switched off, no job on it) is stopped, or runs without Odoo answering, for `ALERT_INSTANCE_DOWN_MINUTES`. |
| `ALERT_INSTANCE_DOWN_MINUTES` | float | `5` | **live** | Minutes it must stay down before the alert (1 to 1440). |
| `ALERT_DISK` | bool | `true` | **live** | The disk holding `ODOO_ROOT` has less than `ALERT_DISK_FREE_PERCENT` free. |
| `ALERT_DISK_FREE_PERCENT` | float | `10` | **live** | Free disk, in %, below which the alert is sent (1 to 90). |
| `ALERT_MEMORY` | bool | `true` | **live** | Server memory stays at or above `CAPACITY_WARNING_PERCENT` for `ALERT_MEMORY_MINUTES`. |
| `ALERT_MEMORY_MINUTES` | float | `10` | **live** | Minutes memory must stay high before the alert (1 to 1440). |
| `ALERT_DRIFT` | bool | `true` | **live** | An instance's containers or files were changed outside the panel (`DRIFT_ACTION`). |
| `ALERT_OFFSITE_FAILED` | bool | `true` | **live** | A backup could not be copied off-site, or an off-site copy failed its check (e-mail "Off-site copy failed"); a notice follows when copies of that instance work again. |
| `ALERT_OFFSITE_MISSING` | bool | `true` | **live** | A production instance has had no successful off-site copy for `ALERT_OFFSITE_MISSING_HOURS` while off-site copies are on (e-mail "No recent off-site copy"). |
| `ALERT_OFFSITE_MISSING_HOURS` | float | `26` | **live** | Hours without a successful off-site copy of a production instance before the alert (1 to 720). |
| `ALERT_PUBLIC_LINK` | bool | `true` | **live** | The panel, or an instance that is online, has had no public link for `ALERT_PUBLIC_LINK_MINUTES` (its connector stopped, say). |
| `ALERT_PUBLIC_LINK_MINUTES` | float | `10` | **live** | Minutes without a public link before the alert (1 to 1440). |
| `ALERT_CERTIFICATE` | bool | `true` | **live** | A custom domain's certificate could not be issued or renewed, or expires within `ALERT_CERTIFICATE_DAYS` and was not renewed yet (e-mails "Certificate not issued" and "Certificate expires soon"). |
| `ALERT_CERTIFICATE_DAYS` | float | `14` | **live** | Days before a certificate expires from which it is shown and alerted as expiring (1 to 60). |
| `ALERT_RECOVERY` | bool | `true` | **live** | Send a notice when a problem that was alerted is over: back online, disk freed, memory normal, link back. |
| `ALERT_QUIET_MINUTES` | float | `60` | **live** | The same alert (same kind, same instance) is sent at most once in this many minutes; a repeat inside it only counts up on the alert in the bell. `0`: every time (0 to 10080). |
| `ALERT_DIGEST` | bool | `false` | **live** | E-mail one summary a day, at `ALERT_DIGEST_TIME`, instead of each alert as it happens. Webhooks and the bell still get each alert at once. |
| `ALERT_DIGEST_TIME` | text | `08:00` | **live** | When the daily summary is sent, `HH:MM` on a 24-hour clock, in `TIMEZONE`. |
| `ALERT_WEBHOOK_TIMEOUT_SECONDS` | float | `10` | **live** | Seconds a webhook may take to answer before the delivery counts as failed (1 to 60). |
| `ALERT_WEBHOOK_1_URL` | text | empty | **live** | Webhook 1: the address alerts are posted to. `https://` only (`http://` only for `localhost`); no user name or password in it. Secret: write-only on the Settings page. |
| `ALERT_WEBHOOK_1_SECRET` | text | empty | **live** | Webhook 1: key of the `X-Sudoo-Signature` header (HMAC-SHA256 of the body). Empty: requests are not signed. Secret. |
| `ALERT_WEBHOOK_1_FORMAT` | text | `json` | **live** | Webhook 1: `json` (the panel's own signed JSON), `slack`, `discord` or `teams` (their incoming webhooks). |
| `ALERT_WEBHOOK_2_URL` | text | empty | **live** | Webhook 2: as webhook 1. Secret. |
| `ALERT_WEBHOOK_2_SECRET` | text | empty | **live** | Webhook 2: as webhook 1. Secret. |
| `ALERT_WEBHOOK_2_FORMAT` | text | `json` | **live** | Webhook 2: as webhook 1. |
| `ALERT_WEBHOOK_3_URL` | text | empty | **live** | Webhook 3: as webhook 1. Secret. |
| `ALERT_WEBHOOK_3_SECRET` | text | empty | **live** | Webhook 3: as webhook 1. Secret. |
| `ALERT_WEBHOOK_3_FORMAT` | text | `json` | **live** | Webhook 3: as webhook 1. |

---

## Web shell

A terminal in the browser inside an instance's containers (the Shell tab). Settings page, **Sign-in and security**,
groups **Web shell** and **Shell recordings**. How it works and is protected: [Admin guide, Web shell](ADMIN_GUIDE.md#21-web-shell),
[Security, Web shell](SECURITY.md#15-web-shell).

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `SHELL_ENABLED` | bool | `true` | **live** | Shells can be opened (by the roles allowed). `false`: nobody can, the Shell tab disappears, and open shells close within 15 seconds. |
| `SHELL_ROLES` | text | `admins` | **live** | Who may open the Odoo container's terminal and `odoo shell`: `admins` (super admins and administrators), `maintainers` (and maintainers), `developers` (and maintainers and developers). Viewers never can; `psql` stays with super admins and administrators. |
| `SHELL_PRODUCTION` | text | `super_admins` | **live** | Shells in production instances: `off` (nobody), `super_admins`, or `allowed` (whoever may open shells in other instances). Every production shell is confirmed when it is opened. |
| `SHELL_ROOT` | bool | `false` | **live** | Super admins may open the Odoo container's terminal as root (the container's capabilities stay dropped). |
| `SHELL_IDLE_MINUTES` | float | `15` | **live** | A shell nobody types in for this long closes (1 to 1440). |
| `SHELL_MAX_MINUTES` | float | `60` | **live** | Longest a shell stays open (1 to 1440). |
| `SHELL_MAX_PER_USER` | int | `2` | **live** | Shells one person may have open at once (1 to 20). |
| `SHELL_MAX_PER_INSTANCE` | int | `3` | **live** | Shells open in one instance at once (1 to 50). |
| `SHELL_TICKET_SECONDS` | int | `15` | **live** | Seconds the one-time ticket that connects a new shell stays valid (5 to 60). |
| `SHELL_RECORDING` | text | `production` | **live** | Record what is typed and shown: `off`, `production` (production instances and root shells) or `all`. Encrypted; only super admins play recordings. |
| `SHELL_RECORDING_DAYS` | int | `30` | **live** | Days recordings and the list of past shells are kept (1 to 3650). |
| `SHELL_RECORDING_MAX_MB` | float | `5` | **live** | Largest recording of one shell, in MB (0.1 to 50); beyond it only typing is recorded, and the shell closes when typing alone reaches the size again. |
| `SHELL_RECORDINGS_TOTAL_MB` | int | `500` | **live** | Space all recordings may take together, in MB (10 to 100000); the oldest are deleted first. |

---

## Servers

Several servers managed from this panel (a premium feature of Business plans, [Premium features](PREMIUM.md)): the
main server, where the panel runs, and other servers, each running the agent (`sudoo-install agent --join <code>`).
Settings page, **Servers**. How it works: [Admin guide, Servers](ADMIN_GUIDE.md#27-servers),
[Security, Servers](SECURITY.md#18-servers). Without several servers, only `SERVER_NAME` and `SERVER_REGION` are
used: instances show them as their server.

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `SERVER_NAME` | text | `Main` | **live** | Name of this server, the main one, on its instances and the Servers page (1 to 40 characters). |
| `SERVER_REGION` | text | empty | **live** | Region of this server (for example `Frankfurt` or `eu-central`). |
| `SERVERS_PANEL_URL` | text | empty | **live** | Address the other servers' agents reach this panel at (`https://...`). Empty: the panel's own public address. Set it when the agents reach the panel by another address (a private network, a fixed domain in front of a quick tunnel). It is written into every new join code. |
| `SERVERS_JOIN_CODE_MINUTES` | int | `30` | **live** | Minutes a join code stays valid (5 to 1440); a code works once. |
| `SERVERS_HEARTBEAT_SECONDS` | int | `15` | **live** | Seconds between two reports of each agent: its instances, jobs, figures and room (5 to 300). |
| `SERVERS_OFFLINE_SECONDS` | int | `90` | **live** | A server whose agent has not reported for this long is shown offline (15 to 3600). |
| `SERVERS_REQUEST_SECONDS` | int | `30` | **live** | Seconds the panel waits for an agent to answer a request (5 to 300). |
| `SERVERS_WAIT_MINUTES` | int | `60` | **live** | Minutes a job on another server waits for the main panel when it needs it (a GitHub token to read private code), before it fails (1 to 10080). |
| `MOVE_MAX_BACKUP_GB` | float | `100` | **live** | Largest backup an instance may have to be moved to another server, in GB (1 to 10000). |
| `MOVE_CHUNK_MB` | int | `8` | **live** | Size of each piece of a backup copied between servers, in MB (1 to 64). |
| `MOVE_AT_ONCE` | int | `1` | **live** | Moves between servers that may run at the same time (1 to 10). |
| `MOVE_HEALTH_SECONDS` | int | `300` | **live** | Seconds a moved instance has to answer on its new server before the move is rolled back (30 to 3600). |

Each agent takes the panel's instance settings (pausing, backups, time zone, stages, resources, images, capacity,
gateway, domains, shells, mail catcher) from the panel; what belongs to its own server stays in its own `.env`
(folders, ports, exposure, Enterprise add-ons). Secrets never leave the panel.

---

## Mail catcher

Staging and development instances never send real e-mail: their e-mail goes to the mail catcher and is shown on
their **Mail** tab. Settings page, **E-mail**, group **Mail catcher**. How it works: [Admin guide, Mail
catcher](ADMIN_GUIDE.md#23-mail-catcher), [Security, Mail catcher](SECURITY.md#16-mail-catcher).

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `MAIL_CATCHER_RETENTION_DAYS` | int | `7` | **live** | Days caught e-mails are kept (1 to 365); older ones are deleted every 5 minutes. |
| `MAIL_CATCHER_MAX_MESSAGES` | int | `500` | **live** | E-mails kept per instance (10 to 100000); beyond it the oldest are deleted. |
| `MAIL_CATCHER_MAX_MB` | int | `200` | **live** | Space the caught e-mails of one instance may take, in MB (10 to 100000); beyond it the oldest are deleted. |
| `MAIL_CATCHER_MAX_MESSAGE_MB` | int | `25` | **live** | Largest e-mail the catcher accepts, in MB (1 to 100); a larger one is refused, and Odoo shows it as not sent. |
| `MAIL_CATCHER_RELEASE` | bool | `true` | **live** | Administrators may **Release** a caught e-mail: send a copy, from the panel's own address (`MAIL_FROM`), to one address they type. Every release is audit-logged. `false`: nobody can. |

---

## Help panel

The **?** button at the top of every page opens the help panel. Settings page, **General**, group **Help**.

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `DOCS_URL` | text | `https://docs.sudoo.sh` | **live** | Where the panel's links to the documentation site go (`https://`; `http://` only for `localhost`). |
| `SUPPORT_URL` | text | empty | **live** | Where **Contact support** goes while the panel is not linked to a Sudoo.sh account: an `https://` address (your own help desk) or `mailto:` and one address. Empty: an e-mail to `ADMIN_CONTACT_EMAIL`; when that is empty too, the panel names `ADMIN_CONTACT_NAME`. |
| `HUB_SUPPORT_URL` | text | `https://app.sudoo.sh/support` | **live** | The help desk **Contact support** opens while the panel is linked to a Sudoo.sh account (Settings, Marketplace). |
| `PLAN_PRICING_URL` | text | `https://sudoo.sh/pricing` | **live** | Where **Settings, Plan** and every message about a plan's limit send people to compare the Sudoo.sh plans (https). |
| `PLAN_ACCOUNT_URL` | text | `https://app.sudoo.sh` | **live** | The account site where the linked account's plan is managed and, after a downgrade, the production branches to keep are chosen (`<address>/plan`) (https). |

---

## Marketplace

How the panel talks to the Sudoo.sh hub, which lists plugins, sells paid ones and signs every package. See
[Marketplace](MARKETPLACE.md).

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `MARKETPLACE` | bool | `off` | **live** | Off by default until the marketplace opens. `on`: register with the hub, send a signed heartbeat every 15 minutes (version, core hash, fingerprint, installed plugins and coarse server figures) and install plugins from the Plugins page. `off`: the panel never contacts the hub; installed free plugins keep working, paid ones stop at their `run_until` (the last lease's end plus 96 hours, about 7 days). |
| `HUB_URL` | text | `https://hub.sudoo.sh` | `.env` only | The hub's address: `https://` (`http://` only for `localhost` while testing). The panel registers with each hub it is pointed at. |
| `PUBLIC_IPV4` | text | empty | **live** | The server's public IPv4 address, sent with the heartbeat so Sudoo.sh can point managed subdomains at it. Empty: the address the server sends from, when it is a public one (behind NAT, set it). On another server of a panel with several servers, set in that server's own `.env`: its agent reports it to the main panel, which asks Sudoo.sh to point the names of that server's instances there (the main panel's value is never passed to agents). |
| `PUBLIC_IPV6` | text | empty | **live** | The same for IPv6. |
| `HUB_PUBLIC_KEYS` | text | empty | `.env` only | The hub's root public keys (Ed25519, base64url, comma-separated). Every answer, lease, command and package must be signed by one of them, or by a newer key one of them endorsed. Empty: the keys this release ships for `hub.sudoo.sh`; for another hub (while testing), the keys it lists the first time the panel reaches it. |

---

## Premium features

Premium features (teams and access per project, custom domains, off-site backups, support access, several servers)
install automatically from Sudoo.sh when your plan includes them. See [Premium features](PREMIUM.md).

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `PREMIUM_AUTO_INSTALL` | bool | `on` | **live** | Install and update the premium features your plan includes after each signed lease. `off`: they install only when you press **Check now** in Settings, Premium features. |
| `PREMIUM_CHECK_HOURS` | number | `6` | **live** | Hours between checks for newer versions of the installed premium features (0.25 to 168). |
| `PREMIUM_RETRY_MINUTES` | number | `15` | **live** | Minutes the panel waits before asking Sudoo.sh again after a check, when the check failed or a feature was not ready yet (1 to 1440). |
| `PREMIUM_WATCH_MINUTES` | number | `10` | **live** | Minutes between integrity passes over the installed premium features: their files, their signature and their binding to this server (1 to 1440). |
| `PREMIUM_BINDING_RENEW_DAYS` | number | `7` | **live** | Days before a premium feature's binding to this server ends that the panel asks Sudoo.sh for a new one (1 to 60). |
| `PREMIUM_MAX_PACKAGE_MB` | number | `60` | **live** | The largest premium package the panel downloads, in MB (5 to 500). |
| `PREMIUM_PUBLIC_KEYS` | text | empty | `.env` only | The premium signing keys the panel trusts (Ed25519, base64url, comma-separated). Empty: the keys this release ships. Set it only on a test panel. |
| `PREMIUM_BINDING_PUBLIC_KEYS` | text | empty | `.env` only | The premium binding keys the panel trusts (Ed25519, base64url, comma-separated): they sign a download's binding to a server, never a package. Empty: the keys this release ships, and those a premium signing key endorsed. Set it only on a test panel. |

---

## Support access

Letting Sudoo.sh support into the panel for a while, through the panel's own connection to the hub (needs
`MARKETPLACE` on). Settings page, **Support access**; how it works: [Admin guide, Support access](ADMIN_GUIDE.md#26-support-access),
[Security, Support access](SECURITY.md#17-support-access).

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `SUPPORT_ACCESS` | bool | `true` | **live** | Super admins and administrators may grant support access. `false`: none can be granted, and every grant ends at once. |
| `SUPPORT_ACCESS_MAX_HOURS` | int | `72` | **live** | The longest a grant may last, in hours (1 to 72): the choices 1 hour, 1 day and 3 days are offered up to it. |

---

## Locations

| Setting | Type | Default | Applies | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `ODOO_ROOT` | Path | `/opt/odoo` | `.env` only | Folder that holds instances, backups and panel data. |
| `DOCS_DIR` | Path | `/app/documentation` | `.env` only | README.md and docs/ shown in the panel. |
| `MEMORY_DIR` | Path | `/dev/shm` | `.env` only | Memory-backed folder for secrets needed briefly as files. |
