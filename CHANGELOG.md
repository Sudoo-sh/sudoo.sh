# Changelog

All notable changes to this project are recorded in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Arabic in the web app (3.0.0):** the panel can be used in Arabic, right to left, as well as English. Each person
  chooses on **My account** or with the switch at the bottom of the sidebar; the choice is kept on the account
  (`PATCH /api/v1/account` `language`: `en`, `ar` or `null`; `GET /session` and `GET /account` return it), so it
  follows them to every browser. New setting `DEFAULT_LANGUAGE` (`en` by default, **Settings, General**) for accounts
  that have not chosen; `GET /setup` and `GET /contact` return it. Before signing in, the browser's language (or a
  `sudoo_lang` cookie) decides, and the sign-in and setup pages have the switch too. The page's language and
  direction are set before it is drawn (`boot.js`, no inline script), Arabic letters use IBM Plex Sans Arabic served
  by the panel itself, digits stay 0-9, and code, branches, addresses, logs and e-mail addresses stay left to right.
  Messages written by the server and the e-mails stay in English for now. The demo has the switch too.

- **Custom domains with certificates (3.0.0):** an instance can answer on your own domains, such as
  `erp.client.com`, over https (`CUSTOM_DOMAINS`, off by default). With `acme` the gateway answers them on ports 80
  and 443, routes each by its name, redirects http to https and gets a certificate for each from Let's Encrypt
  (`ACME_DIRECTORY`: production, staging or another ACME authority; `ACME_EMAIL`) by HTTP-01, renewed
  `CERTIFICATE_RENEW_DAYS` before it expires; with `external` your own Cloudflare tunnel or proxy handles https and
  the panel records and checks the domains. Before asking for a certificate the panel checks the domain's DNS from
  the server (`DOMAIN_DNS_TARGET`, `DOMAIN_DNS_TIMEOUT_SECONDS`) and, with `DOMAIN_HTTP_PROBE`, that it reaches the
  server over http. Failures are retried with growing waits (`CERTIFICATE_RETRY_MAX_HOURS`) and alerted (new alert
  type certificate, `ALERT_CERTIFICATE`, `ALERT_CERTIFICATE_DAYS`, e-mails "Certificate not issued" and "Certificate
  expires soon" on the E-mail templates page). The primary domain becomes the instance's address and Odoo's
  `web.base.url`. Administrators manage domains (new permission `domains.manage`, `DOMAINS_PER_INSTANCE`), production
  instances ask for a confirmation, and every change is audited. API: `/api/v1/instances/<name>/domains`; live event
  `domains`. The `sudoo-gateway` service gets the `NET_BIND_SERVICE` capability (update `compose.yaml`). In the
  web app, an instance's new **Domains** tab adds a domain, shows the DNS record to create, follows its state live
  (waiting for DNS, getting a certificate, active, renewing, expires soon, failed with the reason), its certificate's
  expiry, **Check now**, **Make primary** and **Remove**; Settings has a **Custom domains** tab. The demo shows sample
  domains in each state.

- **Recovery codes for the authenticator app:** setting up the app now shows 10 single-use recovery codes
  (`RECOVERY_CODES_COUNT`) to copy or download; each signs in once in place of the app's code ("Lost your phone?"
  on the sign-in page). They are stored only as keyed hashes, count like wrong passwords when wrong, are audited,
  and can be made again with the password (the old ones stop working). My account shows how many are left; accounts
  that used the app before are asked to make them. Removing the app, or an administrator removing an account's
  second factors, removes them.
- **Sign-in e-mail:** every successful sign-in e-mails the account when, from which address, browser and system,
  and how it signed in, with a link to change the password or sign out everywhere. Sent in the background, only when
  e-mail is set up. `SIGN_IN_EMAIL` (on) and `SIGN_IN_EMAIL_OPT_OUT` (on) decide who gets it and whether people may
  turn it off on My account, which also has **Sign out others** (`DELETE /api/v1/account/sessions`).
- **E-mail templates page:** super admins (new permission `mail.manage`) change the subject, title and text of every
  e-mail the panel sends (sign-in code, address confirmation, new sign-in, alert, daily summary, plugin notice, test
  e-mail) in a plain format with placeholders, bold, lists, notes and buttons, with a live preview, **Send me a
  test** and **Reset to default**. Unknown placeholders and unsafe links are refused, values are always escaped, and
  no template code is run; changes are audited. API: `/api/v1/mail-templates`.

- **Demo build:** `npm run build:demo` builds the web app as a static, fully client-side demo with sample data (an
  integrator's client projects, live metrics, simulated jobs, backups, accounts, audit log and alerts) and a banner
  to view it as a super admin, admin, developer or viewer; ready for Cloudflare Pages (README, Demo build). The
  normal build contains none of it. Buttons and other pressable controls no longer wait for a double tap on touch
  screens.
- **Instances keep serving without the panel:** a separate `sudoo-gateway` container now owns every instance's
  gateway port, so visitors keep reaching running instances while the panel restarts or is down (a paused instance
  shows "Starting soon" until the panel is back). It has no Docker socket and no `.env`; while it is not running the
  panel serves the instances itself and hands the ports back when it returns, so no connector is restarted.
  `GATEWAY_MODE=embedded` restores the old behaviour.
- **Instance stages:** every instance is Production, Staging or Development (existing ones are Development and
  behave as before). Production never pauses or expires, is backed up every day and always updated safely, and
  pausing, switching off, restoring or deleting it asks for an extra confirmation; `MAX_PRODUCTION_INSTANCES`
  (default 1) limits how many there are. Staging keeps a longer lifetime (`STAGING_DELETE_AFTER_DAYS`).
- **Alerts:** e-mail and webhooks (signed JSON, Slack, Discord, Teams) when a job, an update or a backup fails, an
  instance is down, the disk is nearly full, memory stays high, a public link is missing or an instance was changed
  outside the panel, with recovery notices, a quiet period and an optional daily digest; a bell lists recent alerts.
  Every alert and threshold is a setting (Settings, Alerts), with a "Send a test alert" button.
- **Safe updates:** before an update changes an instance's code (a push, Get latest code, Update submodules), the
  panel takes a `before-update` backup and records the commits; if the pull, the submodules, the module upgrade or
  the restart fails, it puts the code back, restores the backup when an upgrade ran, restarts Odoo and says "The
  update to <sha> failed at <step>; the instance was put back to <sha>." Automatic updates do not retry a commit
  that was rolled back. `SAFE_UPDATES` (on by default) turns it off.
- **Server capacity:** the dashboard shows what the panel itself uses, and how many instances the server can run
  comfortably (from measured memory and processor use); creating or starting one more beyond that shows a warning,
  and the dashboard warns above 85% memory. `GET /api/v1/server/capacity`; settings `CAPACITY_*` and an optional
  hard limit `MAX_RUNNING_INSTANCES` (off by default).
- **Odoo apps on the tokens page:** the API tokens page lists the free Sudoo.sh apps on the Odoo Apps store and has a
  **Token for Odoo** button that opens the new-token dialog with the Instances scope.
- **Sudoo.sh apps for Odoo:** the README and the Panel API link the free apps on the Odoo Apps store (Instance
  Manager, CRM Demos, Project Tests, Helpdesk Tests), which use a token of the instances scope.
- **API tokens for connected apps:** a third token scope, `instances`, for connected apps such as the Sudoo.sh
  module for Odoo: list, create, start, pause, restart, back up, extend and delete instances, and nothing else (no
  passwords, logs, restores, backups, GitHub, accounts, tokens or settings). The API tokens page offers it next to
  Read only and Read and write ([Panel API](docs/PANEL_API.md#the-instances-scope)).
- **Acting for someone:** API calls may send `X-Sudoo-Acting-For` with the person in the calling system who asked
  for them. The value is signed as a sixth line (requests without it keep the five-line signature), and the caller
  becomes `api:<token name> for <person>` in the audit log, as `created_by` of new instances and in the per-user
  instance limits ([Panel API](docs/PANEL_API.md#acting-for-someone)).
- **Connect a repository to plain Odoo:** an instance created without a repository gets one later through
  **⋯ → Connect a repository…** (API action `connect-repository`, `instances.operate`): the panel clones the branch
  into its add-ons folder, installs the repository's modules if asked and restarts it. Its database and files are
  kept, and from then on it works like an instance created from that repository.
- **Repository access from the picker:** under the New instance page's repository list, a link opens the GitHub
  App installation's page on GitHub to choose which repositories the app may read (one link per account it is
  installed on). `GET /api/v1/github/repositories` lists them in `meta.installations`.
- **Plain Odoo instances:** the New instance page asks first **From a repository** or **Plain Odoo (no
  repository)**. A plain instance runs the official Odoo only (Community or Enterprise, any version offered): nothing
  is cloned, it gets no update triggers, and the update, branch and submodule actions, the Code tab and the automatic
  update settings are hidden; the API refuses them with 409. Without a name it is called
  `odoo-<version>-<edition>-<random>`. Pausing, backups, restore, lifetime, links, metrics and the integrity
  watchdog work as for any instance. API: `repo` is optional in `POST /api/v1/instances`, instances show `source`
  (`repository` or `plain`), and `GET /api/v1/instance-plan` takes `version` and `edition`
  ([Panel API](docs/PANEL_API.md)).
- **GitHub App:** "Connect GitHub" on the GitHub access page (super admins) registers one private, read-only GitHub
  App for the panel through GitHub's App Manifest flow, for a personal account or an organisation, and "Install on
  GitHub" gives it all or selected repositories of the account that owns it (the app is private). The panel reads private
  repositories and their submodules with one-hour installation tokens handed to git by a credential helper (never in
  addresses, `.git/config` or logs). The New instance page lists the app's repositories as you type, branches come
  from GitHub's API, and the new update trigger `github-app` (default `UPDATE_TRIGGERS=polling,github-app`) updates
  instances seconds after a push through the app's webhook, whose address the panel updates itself when the panel's
  public link changes. Existing deploy keys keep working and remain the fallback. API: `/api/v1/github/app`,
  `/api/v1/github/app/manifest`, `/api/v1/github/app/installations`, `/api/v1/github/repositories`.
- **Plugin store:** a new **Plugins** page (super admins install and buy; administrators can look) with the
  Sudoo.sh marketplace's catalogue (search, categories, a **Recommended** row with the reason for each suggestion,
  plugin icons, countries and a "Made for your country" badge, Official and Verified publishers, plan prices with
  their monthly equivalent and saving, one-time licences), the installed plugins (versions, updates, licence dates,
  switch off and on, uninstall after a backup) and the receipts and purchases of this server, each with a CSV
  export. A free plugin installs with one click: the package is downloaded with a signed request, its SHA-256 and
  the hub's Ed25519 signature are checked, it is unpacked into a private folder and the backend restarts by itself.
  **The panel never charges anything:** buying opens a checkout session whose page on app.sudoo.sh opens in a new
  tab, where the account owner pays; the Plugins page follows it (waiting, declined card, cancelled, expired) and
  installs the plugin once paid. Licences are followed offline (reminder 7 days before, grace period, `run_until`
  from each lease, backup and switch-off keeping the data, switch-on after a renewal). The panel registers with the
  hub, sends a signed heartbeat every 15 minutes (with its hardware fingerprint and coarse server figures), keeps
  the 72-hour signed lease, and **verifies the hub's signature on every answer** with keys it trusts (root keys,
  rotations followed through endorsements, revoked keys dropped). **Log out of the account** (Settings,
  Marketplace) unlinks the server: the hub's signed command makes the panel back up and remove its paid plugins,
  while the panel, free plugins and instances keep running. A server flagged as a copy shows its paid plugins as
  withheld; a revoked key makes the panel register again with a new identity. Everything keeps working when the hub
  cannot be reached. New settings `MARKETPLACE`, `HUB_URL` and `HUB_PUBLIC_KEYS`, a read-only mount of the host's
  `/etc/machine-id` in `compose.yaml`, permission `plugins.view`, command `sudoo.sh marketplace`, and
  [Marketplace](docs/MARKETPLACE.md).

### Changed

- `MAIL_TEMPLATES_DIR` now replaces only the frame of the e-mails (`layout.html`); the per-e-mail template files
  (`code.html`, `alert.html`, ...) are gone, and their texts are changed on the E-mail templates page instead.
- **Settings page in tabs:** General, Instances, Odoo, Backups, Sign-in and security, E-mail, GitHub and updates,
  Marketplace and Advanced; the tab is kept in the address (`?tab=`), and tabs with unsaved or restart-pending
  changes are marked.
- The sign-in page, the package details and the examples in the documentation name the project, not a person;
  support and conduct reports go to support@sudoo.sh.
- **Moving between pages** is smoother in browsers with the View Transitions API: the sidebar and the top bar stay
  still while the page fades out (120 ms) and the new one then fades in (190 ms, rising 4 px); between an
  instance's tabs only the tab's content does. With reduced motion the fades are shorter and nothing moves, and
  other browsers change the page at once. A new page starts at its top and going back returns
  to where you were. Loading placeholders appear only after 150 ms, so quick pages never flash a skeleton or
  spinner, and a thin bar shows while a page's code is still loading. On a phone, an instance's backup buttons and
  submodules table no longer widen the page.
- **Performance** (figures, method and targets in [Performance](docs/PERFORMANCE.md); a repeatable load test in
  `perf/`):
  - The gateway passes request bodies on to Odoo as they arrive instead of reading them whole first, and answers as
    they come: four 50 MB uploads at once no longer make the panel hold 200 MB, and an upload of several gigabytes
    never sits in memory. It reads an instance's `.env` only when the file changed, reads and writes sockets in
    larger steps, and adds less time to every visit.
  - The API reads instances' `.env` files, the backup list, repository addresses and recent commits only when they
    changed (by file signature, or after a job on the instance), so the instance list, an instance's page and
    GitHub webhook deliveries no longer start git or read every file on every call.
  - The dashboard's metrics history and an instance's figures are built once per measurement for every viewer,
    and each live event is serialised once for all open tabs; documentation pages are rendered once.
  - API tokens: "last used" is written at most once a minute per token (to the minute), and replayed-request
    checks no longer slow down as calls add up.
  - The web app loads less before the sign-in page appears (no charts, no panel frame), keeps libraries in files
    of their own that browsers reuse across releases, and is served precompressed (Brotli, gzip) by Caddy; the
    frame no longer redraws on every live event.
  - The backend starts faster (the dummy password hash is made in the background), and gunicorn keeps its
    heartbeat file in memory.

### Fixed

- **Page transitions:** moving between pages no longer shows both pages' titles and introductions on top of each other
  for a moment (they were cross-faded in place): the old page now fades out completely before the new one fades in,
  and between an instance's tabs only the tab's content fades ([Design](docs/DESIGN.md#6-motion)).
- **A new instance showed "This page does not exist"** after **Create**, for as long as its files did not exist
  (minutes when the Odoo image had to be downloaded first), and the page did not recover. An instance now answers
  from the moment its creation is accepted, with status `creating` (listed too, with the creation job as
  `current_job` and its steps); its page shows the creation live (status, steps, progress and technical log, or
  "Waiting to start" while another job on the name finishes) and becomes the instance's page when it succeeds. A
  failed creation shows its error, the step where it stopped and a way back, and the API answers 404 again while the
  job stays readable ([Panel API](docs/PANEL_API.md#instances-jobs-and-actions-in-detail)). Creating an instance now
  starts by downloading the Odoo and PostgreSQL images, in a step of its own ("Downloading Odoo 19.0 image", or
  "Checking for a newer ..." when the image is already on the server), and the log says how far the download is
  every 10 seconds while Docker writes nothing; when the check fails but the image is on the server, that copy is
  used. The new setting `PREPULL_IMAGES` (off by default) downloads the images of every offered version in the
  background, so the first instance of a version does not wait for it ([Configuration](docs/CONFIGURATION.md)).

- **GitHub App and organisations:** a private GitHub App can be installed only on the account that owns it, so an app
  created on a personal account could never read a company organisation's private repositories, and creating an
  instance from one failed with advice about deploy keys. **Connect GitHub** now asks whose repositories the instances
  use and explains this, recommending an app created under the organisation (by one of its owners). The panel reads
  the app's installations from GitHub by itself (at most once a minute, when the GitHub access page opens and before
  a repository check), so installations made on GitHub are found without **Refresh**. When a repository cannot be
  read, the New instance page and the failed job say what applies, with a button to fix it: the app belongs to
  another account, is not installed on the owner, is suspended, or the repository is not among its selected ones;
  the deploy-key advice is given only when no app is connected. The access report gains `fix`
  ([Panel API](docs/PANEL_API.md)); the recommended setup is in the
  [Administrator guide](docs/ADMIN_GUIDE.md#which-account-should-own-the-app).

### Security

- **Signed installer:** the release workflow now builds `sudoo-install.sh` from a pinned commit of the installer's
  repository and signs it keylessly with Sigstore (`sudoo-install.sh.sigstore.json`), as it already did for
  `compose.yaml`; no maintainer login signs anything. `sudoo-install update` verifies a newer release's installer
  against exactly `https://github.com/Sudoo-sh/sudoo.sh/.github/workflows/release.yml@refs/tags/v<version>` when
  `cosign` is installed and stops on a missing or failing signature. Every GitHub Action in the workflows is pinned
  by commit.
- **No default Odoo admin password after an update:** when `/opt/odoo/manager/.env` still has
  `ODOO_ADMIN_PASSWORD=admin`, the update replaces it with a random value (the old `.env` is in the update's backup).
  Existing instances keep their password until it is changed in the panel.
- **Least privilege for connected apps:** an `instances` token acts as the new role Connected app, which only a
  token can have (`instances.view`, `instances.create`, `instances.update`, `instances.operate`,
  `instances.delete`), and may call only the endpoints a connected app needs; other actions, `from_backup`,
  `without_backup` and a shorter lifetime are refused with 403 and audit-logged. Such a token no longer needs the
  `write` scope, which acts as an Administrator.
- Requests per minute are counted per API token, whoever it acts for, so `X-Sudoo-Acting-For` cannot multiply a
  token's limit.
- **Instance files:** `.env` holds one line per key: values with control characters are refused, and an Odoo
  administrator login read from a database that is not a single plain line is not recorded. The Enterprise mount of
  a hardened instance comes from the panel's record only, and only the instance's own folder and the Enterprise
  source's folders may be mounted. The filestore restore script takes its folder as an argument.
- **Backups belong to an instance, not a name:** new instances get an `INSTANCE_ID`, written into every backup.
  A backup of an earlier instance with the same name counts as another instance's (needs `backups.manage`, is
  neutralised, is not "the latest backup").
- **Lifetime and deletion:** making an instance expire sooner needs `instances.delete`. With `instances.delete` and
  `backups.manage`, an instance can be deleted even when its backup fails; after three failed automatic deletions
  it is switched off instead. Creations in progress count toward the instance limits.
- **Sessions and accounts:** session versions are random, so an account re-created under the same name never
  accepts an old cookie; signing out ends the session on the server; replacing a second factor needs the password
  and ends other sessions, as does changing the sign-in e-mail; an e-mail another account uses is refused.
- **Audit log:** secret settings are recorded as `KEY=(set)`; values written by earlier versions are removed on
  upgrade. Rotate the SMTP password and the GitHub webhook secret if administrators or write tokens could read the
  log.
- **Live events:** at most 8 streams per account or token (429); streams end when their token is revoked or their
  session ends; the web app reconnects with backoff.
- **Jobs:** a step's timeout holds even for a command that prints nothing; the command's process group and its
  `docker compose run` container are stopped.
- **Credentials in logs:** user names and passwords in addresses are masked in job logs; `ENTERPRISE_GIT_URL` with a
  password is refused.
- **Plugins:** packages with Python metadata or `.pth` files are refused, and a plugin cannot provide another
  plugin's entry points.
- **Gateway:** request headers must arrive within `GATEWAY_HEADER_TIMEOUT_SECONDS` (30), with connection caps per
  instance and overall (`GATEWAY_MAX_CONNECTIONS`, `GATEWAY_MAX_CONNECTIONS_TOTAL`). Backups copy filestore files in
  pieces instead of reading them whole.
- Gateway: headers named in `Connection` (and `Proxy-Connection`) are removed both ways, like the other hop-by-hop
  headers; repeated request headers are passed on instead of only the last one; the panel's cookie is removed from
  every `Cookie` header a browser sends (HTTP/2 splits them), not only from one.
- Gateway: `X-Forwarded-For` ends with the address the gateway can vouch for (Cloudflare's `CF-Connecting-IP`, or
  the connecting address with `host-ports`), and `X-Forwarded-Proto` and `X-Forwarded-Host` are always set by it.
  Before, with `host-ports`, a visitor could send their own `X-Forwarded-For` and Odoo (in proxy mode) believed it,
  for example to get around Odoo's sign-in limits per address.
- Gateway: new settings `GATEWAY_MAX_UPLOAD_MB` (2048), `GATEWAY_IDLE_TIMEOUT_SECONDS` (120) and
  `GATEWAY_WEBSOCKET_MAX_MESSAGE_MB` (16), also on the Settings page. WebSocket messages had no size limit, so one
  visitor could make the panel process hold a message of any size; a request body that stops arriving now frees
  the connection; the gateway no longer asks Odoo for gzip on behalf of visitors that did not ask for it.
- Web service: Caddy gives a request 10 s for its headers and 30 s for its body, closes idle connections after 2
  minutes, and refuses bodies over 1 MB for the backend before they reach it.
- Sign-in throttling keeps only failures: a flood of sign-ins or API calls under made-up names or from many
  addresses can no longer fill the panel's memory.

## [1.0.0] - 2026-11-14

### Added

- **Prebuilt, signed images:** every version tag publishes `ghcr.io/sudoo-sh/sudoo-panel` and
  `ghcr.io/sudoo-sh/sudoo-web` for linux/amd64 and linux/arm64 (tags `1.2.3`, `1.2`, `1`, `latest`), with an SBOM and
  provenance attestation, signed with Sigstore (cosign, keyless). The documentation is inside the panel image. The
  release carries `compose.yaml` with the images pinned by digest, `env.example` and `SHA256SUMS`. Every commit on
  `main` publishes `edge` and `sha-<commit>` images.
- **One-file installer:** `curl -fsSL https://github.com/Sudoo-sh/sudoo.sh/releases/latest/download/sudoo-install.sh | sudo bash`.
  It checks the server first (each problem with its fix), installs Docker and other missing programs only after
  asking ([y]es / [n]o / [a]ll, `--yes` for scripts), and runs the release's images. It stays on the server as
  `sudoo-install` with `check`, `update`, `update --rollback`, `update --version edge`, `status` and `uninstall`
  (`--purge`). An update replaces only `sudoo-panel` and `sudoo-web`; instances and connectors are never touched.
- **Manual installation** with the release's `compose.yaml` (docs/ADMIN_GUIDE.md), and **from source** with
  `docker compose build` (README).
- **Hardened instance containers:** each instance's `odoo` and `db` run with `no-new-privileges`, every Linux
  capability dropped except the few PostgreSQL's entry point needs (Odoo gets none), read-only system files (only
  the data volumes and in-memory `/tmp` are writable; `config/` and the add-ons are mounted read-only), an init
  process, rotated logs, process, memory and processor limits (`ODOO_MEMORY_LIMIT`, `ODOO_CPU_LIMIT`,
  `DB_MEMORY_LIMIT`, `DB_CPU_LIMIT`, `CONTAINER_PIDS_LIMIT`, `CONTAINER_LOG_MAX_SIZE`, `CONTAINER_LOG_MAX_FILES`;
  Settings, Instance containers), and the database on an internal network only (no port, no way out, not
  reachable from other instances or, on Docker 28+, from the server). Images are pinned by digest at creation;
  **Update Odoo and PostgreSQL images** (action `update-images`) refreshes them. The compose file is rendered from
  a fixed list of keys, refuses anything else (extra keys, more capabilities, ports off `127.0.0.1`, the Docker
  socket, writable folders from the server), holds its values literally and is run without `.env`. Instance folders
  are mode 700, `.env` and the compose file 600, `odoo.conf` 600 and owned by the Odoo image's user. Existing
  instances get all this the next time they start or restart through the panel; running ones are not restarted for
  it. New Cloudflare connectors are hardened too; existing ones are left as they are (their links would change).
- **Changes made by hand are found and undone:** every 20 seconds the panel compares each instance's files and
  containers (`docker inspect`: image, capabilities, privileges, mounts, networks, ports, environment, labels,
  limits, restart policy) with what it applied, and looks for containers that pretend to belong to an instance. A
  difference goes to the audit log, shows as a **Changed outside the panel** banner and is e-mailed to super admins.
  With `DRIFT_ACTION=restore` (default) the panel then backs up the database (new backup kind `before-repair`) when
  its container is affected, writes its own files back, stops strange containers and recreates the affected ones;
  `warn` only reports, with a **Restore the panel's configuration** button (action `repair`). Connectors and `.env`
  are only ever reported. See [Security](docs/SECURITY.md#10-instance-containers).

- **Daily backups:** once a day (`DAILY_BACKUP_TIME`, default 00:00, in `TIMEZONE`), every instance that was used
  since its last daily backup is backed up. Each instance keeps only its latest daily backup; they go when the
  instance is deleted. Settings: `DAILY_BACKUPS`, `DAILY_BACKUP_TIME`, `TIMEZONE` (Settings, Backups).
- **Restore from the instance's page:** a Backups card with "Back up now", "Restore the latest" (with its date) and
  every backup of the instance. A restore saves the current state first ("before a restore"), then replaces the
  database and the filestore and starts Odoo. New permission `instances.restore` (maintainers and up) and action
  `POST /api/v1/instances/<name>/actions/restore`.
- **Backups anywhere:** "Restore another backup…" on an instance's Backups card puts any instance's backup of the
  same Odoo version into it (picker grouped by instance, other versions greyed out with the reason), loaded under the
  instance's database name and neutralised (Odoo's `neutralize` on 16+, otherwise mail servers and scheduled actions
  off). "Start from a backup" on the New instance page (`from_backup` in `POST /api/v1/instances`) starts a new
  instance with a backup's data, installing only the repository's modules it lacks. In both, the copied
  administrator gets the instance's admin password and the Access tab shows its login. Both need
  `instances.restore` and `backups.manage`. `GET /api/v1/backups` now shows each backup's Odoo version and edition.
- **Rollback after a restore:** the state before a restore is kept for `ROLLBACK_HOURS` (default 24, Settings,
  Backups), with a "Rollback to before the restore" banner on the instance's page and action
  `POST /api/v1/instances/<name>/actions/rollback`. A rollback saves the current state as a new kind,
  `before-rollback`, and never replaces the snapshot it restores; expired snapshots are removed automatically.
- **Dashboard charts:** a one-hour line inside every figure card, and charts for what the cards do not show:
  requests and errors of all instances, instances by state, response time, and the instances using the most
  processor, memory or disk.
- **Passwords typed by an administrator:** new accounts and password resets can generate a password or take one typed
  by the administrator, checked live with a strength meter; optionally without asking the person to change it.
- **A new logo,** the same everywhere: Odoo's round "o" followed by a text cursor.

- **One-command install:** `curl -fsSL https://raw.githubusercontent.com/Sudoo-sh/sudoo.sh/main/get.sh | sudo bash`
  installs Docker if needed, downloads the latest release and runs the installer.
- **Account profiles** (first and last name, e-mail, phone), editable on My account and by administrators.
- **New endpoints:**
  - `GET/POST /api/v1/setup`, `GET /api/v1/roles`, `PATCH /api/v1/users/<name>`, `PATCH /api/v1/account`;
  - `GET /api/v1/docs` and `GET /api/v1/docs/<path>`;
  - `POST /api/v1/server/restart` and `POST /api/v1/settings/test-email`;
  - `sudoo.sh setup-code`.
- **Sign-in codes in e-mails** can be selected with one tap.
- **A design guide** (`docs/DESIGN.md`) explaining the tokens, type, materials and motion, and how to restyle
  Sudoo.sh.

- **A new web app** (`web/`): React, TypeScript and SCSS, built with Vite and served by Caddy as its own container
  (`sudoo-web`) on the panel's port, using only `/api/v1`. Every page is new:
  - sign-in with the second step (six-box code entry, e-mail resend);
  - instances with live status, resource figures, sparklines and job progress;
  - each instance with live charts for the last hour, activity with a live log, code, Odoo log, settings and
    credentials;
  - a guided **New instance** with repository checks, branch list, name preview and quota warnings;
  - **Settings** with save and reset, accounts, API tokens, backups, GitHub access, audit log, and **My account**
    with authenticator-app setup by QR code.

  It has light and dark themes, page transitions, and an accent colour from `BRAND_COLOR`. The backend moves to
  `127.0.0.1:API_PORT` (9002); the panel's link does not change.
- E-mails in HTML, in the style of Odoo's own (white card, title and product name, code box, button, footer), with
  a plain-text version. Sign-in codes say when, from which address and browser they were asked for. Templates can
  be replaced with `MAIL_TEMPLATES_DIR`; `BRAND_COLOR` sets the accent colour (also on the Settings page).
- `sudoo.sh test-email ADDRESS` checks the mail settings; Gmail setup in docs/CONFIGURATION.md.
- The pages, documentation and installer say **Sudoo.sh**; the command is `sudoo.sh` (`sudoo` still works).
- The panel's own data (accounts, API tokens, audit log, instance state) is kept in SQLite
  (`/opt/odoo/manager/sudoo.db`) through a storage plugin (`STORAGE`); the JSON files of earlier versions are moved
  in on the first start and kept as `*.migrated`.
- Second factors at sign-in: authenticator apps (`totp`) and e-mail codes sent from your own mailbox over SMTP
  (`email`, `SMTP_*`, `MAIL_FROM`). `REQUIRE_SECOND_FACTOR` (`none`, `admins` by default, `everyone`, also on the
  Settings page) makes one compulsory. New API: two-step `POST /api/v1/session`, `/api/v1/account` and its second
  factors, and `DELETE /api/v1/users/<name>/second-factors` for a lost phone.
- Plugin interfaces for sign-in (`sudoo.auth`, e.g. LDAP), second factors (`sudoo.second_factors`, e.g. SMS),
  e-mail (`sudoo.mailers`), storage (`sudoo.storage`) and rate limiting (`sudoo.rate_limiters`).
- Request limits per user, token or address (`RATE_LIMIT_READS_PER_MINUTE`, `RATE_LIMIT_CHANGES_PER_MINUTE`) with
  IETF `RateLimit-*` headers and `Retry-After`.
- Instance limits: `MAX_INSTANCES`, `MAX_INSTANCES_PER_USER` and `CREATE_COOLDOWN_MINUTES`, also on the Settings
  page; `GET /api/v1/instance-quota`; instances record who created them (`CREATED_BY`).
- `LICENSE.md` carries the copyright holder's Required Notice; the README explains commercial licences.
- Metrics for the server and every instance, with one hour of history: CPU and memory (as a share of the limit),
  requests per minute, error rate and response times (average and 95th percentile) measured by the gateway,
  uptime and disk use; `GET /api/v1/metrics`, `/metrics/history` and `/instances/<name>/metrics`, and in every
  `metrics` live event. Instances show them under `usage`.
- Jobs show their progress as a percentage (`progress_percent`) when the number of steps is known (creating,
  deleting, restarting and starting instances).
- One API for everything: every function of the panel is now in `/api/v1` (sessions, accounts, API tokens, backups,
  deploy keys, repository checks, job logs, instance credentials, server figures). The panel's web app can use it
  with its session cookie and CSRF token; other systems keep using tokens and signatures.
- Live events: `GET /api/v1/events` pushes every change (job progress and log, instance status, resource figures,
  settings) as Server-Sent Events, and resumes after a lost connection with what was missed.
- Settings page support: administrators change behaviour settings (pause, lifetime, polling, sessions, contact,
  versions) while the panel runs, through `GET/PATCH /api/v1/settings`; changes take effect at once and are kept on
  top of `.env`, and a reset brings back the `.env` value.
- `AUTO_PAUSE`, `AUTO_UPDATE` and `AUTO_UPGRADE` set the automation switches of new instances (all on, as before);
  the create request can set each one.
- `EVENTS_PORT` (9001): local port of the live event stream.
- The example client follows live events (`api.events()`).

- API answers follow common conventions: `{"data", "meta"}` envelopes, RFC 9457 problem details for errors
  (`application/problem+json`), `202 Accepted` with `Location` for background jobs, ISO 8601 times, pagination
  (`limit`, `offset`), `X-Request-ID` on every answer, and `links` on every resource.

- Plugins for what differs between servers, loaded through Python entry points and chosen in `.env`
  ([docs/PLUGINS.md](docs/PLUGINS.md)):
  - exposure: `cloudflare-quick` (as before) or `host-ports` (open ports, fixed addresses), set by `EXPOSURE`;
  - Enterprise add-on source: `folder` with any naming (`ENTERPRISE_PATH=/data/ent-{major}`), `git` with one branch
    per version, or `none`, set by `ENTERPRISE_SOURCE`.
- Any Odoo version: `ODOO_VERSIONS=auto` offers every published version from `ODOO_MIN_VERSION`.
- New settings for the Odoo and PostgreSQL images and all ports; full reference in
  [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- `sudoo` command (replaces `manage.py`), with `config` to show the effective settings and plugins.
- Update triggers as plugins (`UPDATE_TRIGGERS`, several at once): `polling` (as before) and `github-webhook`
  (updates within seconds of a push, verified with GitHub's signature).
- API for other systems at `/api/v1`: list, read, create, change, run actions on and delete instances, follow jobs.
  Each request needs an API token and an HMAC-SHA256 signature (timestamp, nonce, method, path, body); read and
  write scopes; tokens managed under **API tokens** or with `sudoo create-token`
  ([docs/PANEL_API.md](docs/PANEL_API.md)). A reference client is in `examples/`.
- Test suite (pytest), pre-commit hooks (black, ruff, mypy, shellcheck, uv.lock) and CI running them on every pull
  request.

### Changed

- **Servers no longer build the images or keep the source code.** `get.sh`, `install.sh` and `check-server.sh` are
  gone; servers installed with them move to the images with `sudoo-install update` (same containers, folders and
  `.env`; the source files and `/opt/sudoo.sh-kit` are removed afterwards and kept in the backup).
- `compose.yaml` has health checks for both services; settings in `.env` that the template does not know are kept.

- **The logo follows the theme:** the favicon and the status pages' icon switch to the graphite tile in dark
  mode, and the README shows the dark animated mark there.
- **TRADEMARKS.md:** what you may do with the name, logo and badges; the licence covers the code only.
- **The logo is redrawn:** a plum ring with a dot and a text cursor on a white tile (light plum on graphite in dark
  mode), in the app, favicon, status pages, e-mails and README; the README shows it with a blinking cursor. The
  repository moved to the Sudoo-sh organization and every link points there.
- **Sign in with the e-mail address.** Every account has a unique e-mail address; the user name is made from it and
  still works. First-run setup no longer asks for a user name.
- **Super admins can be edited,** including your own account; the last super admin stays one.
- **Stronger password rules** (NIST SP 800-63B): sequences such as 12345 or qwerty, repeated characters and the
  person's own name or address are refused, in the browser and on the server.
- **Menus open above the page,** so a list never cuts them off, and upwards near the bottom of the window.
- **The unsaved-changes bar on the Settings page follows the theme.**
- **The web app builds with Node.js 26, the current LTS release** (image, CI and `engines`). Dependabot proposes
  only LTS (even-numbered) Node majors.
- **A redesigned interface** ([docs/DESIGN.md](docs/DESIGN.md)):
  - the system font with size-specific tracking;
  - graphite neutrals with Odoo's plum as the single accent;
  - a translucent sidebar and top bar;
  - presses that answer at once; menus that grow from their trigger; dialogs that materialize in the centre;
  - a job drawer and toasts that leave the way they came; a sliding segmented control; quick cross-fades
    between pages;
  - themes that fade instead of flashing;
  - support for reduced motion, reduced transparency and higher contrast.
- **The live charts use Chart.js,** updated in place as each sample arrives.
- **Faster measurements:** every 5 seconds (`METRICS_INTERVAL_SECONDS`), from a continuous `docker stats` stream.
  Git details are read at most once a minute and right after a job.
- **No status badge:** a small "Reconnecting" notice appears only when the connection is lost.
- **Five roles** replace administrator and sync operator: super admin, administrator, maintainer, developer and
  viewer, each a set of named permissions checked on every request. Existing accounts are renamed automatically
  (administrator to super admin, sync operator to developer).
- **No default accounts:** the installer prints a one-time setup code, and the first visit creates your own
  account (name, e-mail, phone, password).
- **Generated passwords** (new accounts, resets) must be replaced at the first sign-in.
- **Almost every setting can be changed on the Settings page,** including the e-mail account:
  - secrets are stored encrypted;
  - settings that choose plugins apply after **Restart the panel**;
  - folders, ports, the exposure and the storage stay in `.env`.
- **The documentation is shown on the web app's Help pages,** from the same files as on GitHub.
- **The previous pages and the internal `/api/*` are removed;** everything uses `/api/v1`.

- Dependencies are installed with uv from `uv.lock` instead of Alpine packages; the image is based on
  `python:3.12-alpine`.
- Settings are typed and checked at start-up (pydantic-settings): a wrong value stops the panel with a clear message.
- Code moved to a `src/` layout with subpackages (`core`, `instances`, `repos`, `exposure`, `addons`, `web`), type
  hints on every function, formatted with black.
- `ADMIN_CONTACT_NAME` defaults to "the administrator".

### Renamed

- The project is now **Sudoo** (`sudo` + `Odoo`), the self-hosted counterpart of Odoo.sh: repository
  `sudoo.sh`, Python package and command `sudoo`, containers `sudoo-panel` and `sudoo-connector-<name>`,
  API tokens `sudoo_...`, signature headers `X-Sudoo-*`.
- New sudoo.sh logo (light and dark) and social preview in `docs/images/brand/`, shown at the top of the
  README; the remaining `odoo-sudoo` folder names in the README, CONTRIBUTING and the Administrator guide now say
  `sudoo.sh`.

### Fixed

- **Live container figures work with Docker 29:** the `docker stats` stream ends its lines with a terminal code
  that stopped the reader, so per-instance processor and memory figures were missing. A line it cannot read is now
  skipped instead of restarting the stream.

### Upgrading

Run `install.sh` as usual. It replaces the old `manager/` files, adds the new settings to `.env` with their
defaults and keeps your values. `ODOO_VERSIONS` keeps its current value; set it to `auto` to offer every version.
Use `sudoo` instead of `python3 manage.py` in commands.

## [0.1.0] - 2026-11-11

### Added

- Web panel that runs many independent Odoo 18, 19 and 20 instances (Community and Enterprise) on one Docker server.
- Instance creation from a GitHub repository and branch, with generated names, databases, ports and passwords.
- One public https link per instance, with no open incoming port.
- GitHub sync every 2 minutes, upgrading only changed modules; submodule update, add and remove.
- Read-only deploy keys per private repository, stored encrypted with AES-256-GCM.
- Auto-pause after 6 hours without visits, and wake-up on the next visit.
- Instance lifetime with automatic Odoo-format backups before any deletion.
- Administrator and sync operator roles, sign-in throttling and an audit log.
- Server and per-instance resource monitoring.
- Documentation in `docs/`, also shown inside the panel.
- Contributor files: contributing guide, security policy, code of conduct, issue and pull request templates, continuous integration and Dependabot.

[Unreleased]: https://github.com/Sudoo-sh/sudoo.sh/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Sudoo-sh/sudoo.sh/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/Sudoo-sh/sudoo.sh/releases/tag/v0.1.0
