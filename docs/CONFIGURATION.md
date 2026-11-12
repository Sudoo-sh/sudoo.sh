# Configuration

Every setting of the panel, with its type, default and meaning. Settings are environment variables, kept in
`/opt/odoo/manager/.env` on the server ([.env.example](../.env.example) is the template). Apply a change with
`cd /opt/odoo/manager && docker compose up -d`.

* Names are not case sensitive. A wrong value (for example `IDLE_HOURS=soon`) stops the panel at start-up with a
  message naming the setting.
* See what the panel actually uses: `docker exec sudoo-panel sudoo.sh config`.
* The settings are defined in `src/sudoo/config.py`; a test fails when one of them is missing here.
* Settings marked **live** can also be changed by an administrator on the panel's **Settings** page (or with
  `PATCH /api/v1/settings`, see [Panel API](PANEL_API.md#7-settings)). The change takes effect at once, without a
  restart, and is kept in `/opt/odoo/manager/settings.json` on top of `.env`; **Reset** brings back the `.env` value.
  All other settings are read when the panel starts.

---

## Contact

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `ADMIN_CONTACT_NAME` | str | `the administrator` | **live** Who users are told to contact. |
| `ADMIN_CONTACT_PHONE` | str | empty | **live** Phone number added to that message. |
| `ADMIN_CONTACT_EMAIL` | str | empty | **live** E-mail address added to that message. |

---

## Odoo

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `ODOO_VERSIONS` | str | `auto` | **live** Versions offered: "auto" (every published version of the official image from odoo_min_version) or a comma-separated list such as 17.0,18.0,saas-18.2. |
| `ODOO_MIN_VERSION` | str | `16.0` | **live** Oldest version offered when odoo_versions is "auto". |
| `ODOO_IMAGE` | str | `odoo` | Docker image of Odoo; the tag is the Odoo version. |
| `POSTGRES_IMAGE` | str | `postgres:16` | Docker image of each instance's database. |
| `ODOO_ADMIN_PASSWORD` | str | `admin` | Password of the Odoo user "admin" in new instances. |

---

## Enterprise add-ons (plugin)

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `ENTERPRISE_SOURCE` | str | `folder` | Where Enterprise add-ons come from: "folder", "git" or "none". |
| `ENTERPRISE_PATH` | str | `/opt/odoo/enterprise/{version}` | folder source: where the add-ons of a version are; {version} (19.0) and {major} (19) are replaced. |
| `ENTERPRISE_GIT_URL` | str | empty | git source: repository holding one branch per Odoo version. |
| `ENTERPRISE_GIT_BRANCH` | str | `{version}` | git source: branch name of a version, e.g. {version}. |
| `ENTERPRISE_MARKER` | str | `web_enterprise` | Module whose presence proves the add-ons are there. |

---

## Exposure (plugin)

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `EXPOSURE` | str | `cloudflare-quick` | How instances are reached: "cloudflare-quick" or "host-ports". |
| `PUBLIC_HOST` | str | empty | host-ports: the server's public name or address. |
| `PUBLIC_SCHEME` | `http` or `https` | `http` | host-ports: scheme of the public addresses. |
| `CLOUDFLARED_IMAGE` | str | `cloudflare/cloudflared:latest` | cloudflare-quick: connector image. |
| `CONNECTOR_PREFIX` | str | `sudoo-connector-` | cloudflare-quick: container name prefix. |
| `CONNECTOR_STATUS_PORT_START` | int | `20300` | cloudflare-quick: first status port of a connector. |
| `PANEL_CONNECTOR_STATUS_PORT` | int | `20242` | cloudflare-quick: status port of the panel connector. |

---

## Code updates (plugins)

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `UPDATE_TRIGGERS` | str | `polling` | What starts code updates, comma-separated: "polling", "github-webhook" or both. See [Plugins](PLUGINS.md#5-update-triggers-what-starts-a-code-update). |
| `GITHUB_WEBHOOK_SECRET` | str | empty | github-webhook: the secret entered on GitHub (20 or more random characters). |

---

## Storage, sign-in and e-mail (plugins)

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `STORAGE` | str | `sqlite` | Where the panel keeps its own data (accounts, tokens, audit log, instance state): `sqlite`, the file `/opt/odoo/manager/sudoo.db`. |
| `AUTH` | str | `local` | How user names and passwords are checked: `local`, the panel's own accounts. |
| `SECOND_FACTORS` | str | `email,totp` | Second factors offered, comma-separated: `email` (a code by e-mail; only when e-mail is set up) and `totp` (an authenticator app). |
| `REQUIRE_SECOND_FACTOR` | str | `admins` | **live** Who must use a second factor: `none`, `admins` or `everyone`. Such accounts without one can only set one up after signing in. |
| `MAILER` | str | `smtp` | How the panel sends e-mail: `smtp` or `none`. |
| `MAIL_FROM` | str | empty | Sender, for example `Sudoo.sh <no-reply@your-site.com>`; empty: `SMTP_USERNAME`. |
| `SMTP_HOST` | str | empty | The mail server, for example `mail.your-site.com`. Empty: no e-mail, and no e-mail codes. |
| `SMTP_PORT` | int | `587` | `587` with `starttls`, `465` with `ssl`. |
| `SMTP_SECURITY` | str | `starttls` | `starttls`, `ssl` or `none` (only for a mail server on the same machine). |
| `SMTP_USERNAME` | str | empty | Usually the full address of the mailbox. |
| `SMTP_PASSWORD` | str | empty | Its password (often an "app password"). Never shown in the panel. |
| `MAIL_TEMPLATES_DIR` | str | empty | A folder with e-mail templates (`layout.html`, `code.html`, `code.txt`, ...) that replace the built-in ones of the same name (`src/sudoo/mail/templates/`). |
| `BRAND_COLOR` | str | `#714B67` | **live** Accent colour of e-mails (title mark, buttons), as `#RRGGBB`. |

E-mails are HTML in the style of Odoo's own (a white card, the title on the left, the product name on the right,
a button and a footer), with a plain-text version for mail programs that show text only. Sign-in codes also say
when, from which address and which browser they were asked for.

### Gmail

1. On the Google account that will send the e-mails, turn on **2-Step Verification**
   (myaccount.google.com, Security).
2. Create an **app password**: myaccount.google.com/apppasswords, name it `Sudoo.sh`. Google shows 16 letters once.
3. In `/opt/odoo/manager/.env`:

   ```
   MAILER=smtp
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_SECURITY=starttls
   SMTP_USERNAME=your.address@gmail.com
   SMTP_PASSWORD=the16letterapppassword
   MAIL_FROM=Sudoo.sh <your.address@gmail.com>
   ```

4. Apply and test:

   ```
   cd /opt/odoo/manager && docker compose up -d
   docker exec sudoo-panel sudoo.sh test-email you@example.com
   ```

Gmail sends from the signed-in address (or an alias added under Gmail's **Send mail as**), at most about 500
messages a day for a personal account. With Google Workspace on your own domain, use the domain's address.

### Your website's mailbox

```
MAIL_FROM=Sudoo.sh <no-reply@your-site.com>
SMTP_HOST=mail.your-site.com
SMTP_USERNAME=no-reply@your-site.com
SMTP_PASSWORD=...
```

## Limits

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `RATE_LIMITER` | str | `memory` | Where request counts are kept: `memory`. |
| `RATE_LIMIT_READS_PER_MINUTE` | int | `600` | **live** Requests that read, per minute, per signed-in user or API token (per address before signing in); 0: no limit. |
| `RATE_LIMIT_CHANGES_PER_MINUTE` | int | `60` | **live** Requests that change something, per minute, counted the same way; 0: no limit. |
| `MAX_INSTANCES` | int | `0` | **live** Instances on this server at most; 0: no limit. |
| `MAX_INSTANCES_PER_USER` | int | `0` | **live** Instances one person or API token may have created and not yet deleted; 0: no limit. |
| `CREATE_COOLDOWN_MINUTES` | float | `0` | **live** Minutes one person or token must wait between two creations, for example `60`; 0: no wait. |

Answers over a request limit are `429 Too Many Requests` with `Retry-After`; every limited answer carries
`RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset`.

## Ports

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `PANEL_PORT` | int | `9000` | Port of the panel. |
| `EVENTS_PORT` | int | `9001` | Port of the live event stream (`/api/v1/events`), on 127.0.0.1 only. |
| `GATEWAY_PORT_START` | int | `8069` | First gateway port (one per instance). |
| `ODOO_PORT_START` | int | `28069` | First Odoo port (one per instance, 127.0.0.1 only). |

---

## Behaviour

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `IDLE_HOURS` | float | `6` | **live** Pause an instance after this many hours without visits; 0: never. |
| `AUTO_PAUSE` | bool | `true` | **live** New instances pause when idle (each instance can change it). |
| `AUTO_UPDATE` | bool | `true` | **live** New instances get new code from their branch automatically. |
| `AUTO_UPGRADE` | bool | `true` | **live** New instances upgrade changed modules after an update. |
| `DELETE_AFTER_DAYS` | int | `30` | **live** Lifetime offered for new instances, in days; 0: keep. |
| `POLL_MINUTES` | float | `2` | **live** polling: check GitHub for new commits this often. |
| `SESSION_IDLE_MINUTES` | float | `60` | **live** Panel sign-out after this long without activity. |
| `SESSION_MAX_HOURS` | float | `12` | **live** Longest panel session. |

---

## Locations

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `ODOO_ROOT` | Path | `/opt/odoo` | Folder that holds instances, backups and panel data. |
| `DOCS_DIR` | Path | `/app/documentation` | README.md and docs/ shown in the panel. |
| `MEMORY_DIR` | Path | `/dev/shm` | Memory-backed folder for secrets needed briefly as files. |
