# Changelog

All notable changes to this project are recorded in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.2.0] - 2026-11-22

### Added

- **Pre-promotion checklist**: moving a branch into production (drag and drop or **Move to**), and **Promotion
  check** in a staging branch's menu, list green, amber and red items with the reason: the branch's latest build and
  tests, production's newest backup (**Back up now** when older than `PROJECT_PROMOTION_BACKUP_HOURS`), the modules
  added, removed and with a new version between production and the branch (read from each `__manifest__.py` at both
  commits), the migration scripts those versions run, the Odoo version, locks and capacity. Red items stop the move
  (`PROJECT_PROMOTION_BLOCKS`) unless someone who manages the project ticks **Promote anyway** (audit-logged). API:
  `GET /api/v1/projects/<id>/branches/<branch>/promotion-check`; `PUT .../stage` takes `"promote_anyway"`.
- **Compare branches**: **Compare** in a branch's menu shows commits ahead and behind, the commits, changed files,
  module version changes and pending migrations between any two branches, with a link to GitHub's compare page
  (`GET /api/v1/projects/<id>/compare?base=&head=`). The panel keeps its own bare, contents-on-demand copy of each
  repository for it (`<ODOO_ROOT>/manager/mirrors/`); GitHub's compare API answers when git cannot.
- **Scheduled staging refresh**: a project's Settings can rebuild its staging branches from a new copy of production
  every day or every week at a chosen time (all staging branches or chosen ones), skipping locked or busy instances,
  with a **refresh** entry in each branch's history and **Refresh now**
  (`GET/PUT /api/v1/projects/<id>/staging-refresh`, `POST .../staging-refresh/run`).
- **Pull-request previews** (off by default, `PROJECT_PR_PREVIEWS`): an open pull request gets a development
  instance of its branch, one comment on the pull request with its link (updated after each build), and the instance
  is deleted without a backup when the pull request is closed or merged. At most `PROJECT_PR_PREVIEW_LIMIT` per project
  (3); pull requests from forks only with `PROJECT_PR_PREVIEWS_FORKS`. Heard from the GitHub App's webhook, with a look
  at open pull requests every `POLL_MINUTES` as a fallback (`GET /api/v1/projects/<id>/previews`). New GitHub Apps ask
  for **Pull requests: write**, **Issues: write** and the **Pull request** event; apps connected earlier keep working
  without previews' comments until they are added (Admin Guide, The GitHub App).
- **Project status page**, as Odoo.sh's: a **Status** tab on every project with a banner (all systems operational,
  degraded, down), the uptime of the past day, week and month and a bar per day for 30 days for each production
  instance (checked through its gateway every `STATUS_CHECK_INTERVAL_SECONDS`), the server (its heartbeat) and the
  backups (late against the daily schedule; off-site copies when they are on), the response time per day, and the
  incidents with their start, end and duration. Results are kept per minute for 48 hours and per hour for 90 days
  (`STATUS_*`). An optional public, read-only status page per project (off by default; a link with a secret token
  that can be replaced). API: `GET /api/v1/projects/<id>/status`, `PUT .../status/public`,
  `GET /api/v1/public-status/<token>`.
- **Notifications** per project: Slack, Discord, Telegram, Microsoft Teams, a signed webhook (HMAC-SHA256, its secret
  shown once) and e-mail, each with the events it wants (build failed, with warnings, working again, stage change,
  promotion to production, production down and up, backup failed, backup restored, storage over its quota).
  Addresses and tokens are encrypted and never shown again (only their last 4 characters); requests reach public
  addresses only (`NOTIFICATION_ALLOWED_HOSTS` for your own network), are retried with growing waits, and each
  channel shows its last delivery and has **Send test** (`NOTIFICATION_*`). API: `/api/v1/projects/<id>/notifications`.
- **Command palette**: Ctrl+K (⌘K on a Mac) opens a search over projects, branches, instances, their tabs and the
  panel's pages, and runs Get latest code and Restart with the same confirmations; recent items first, keyboard only,
  only what your role may open. `/` focuses the branch filter of a project.
- **Code profiler (Tools tab), as on Odoo.sh:** record what an instance's Odoo workers spend their time on for 1 to
  `PROFILER_MAX_MINUTES` minutes (py-spy in a one-off sidecar that joins the Odoo container's processes with only
  `SYS_PTRACE`, no network), stop it early, then read an interactive flame graph (zoom, search, threads, the
  functions with the most time) or download it as a speedscope file. The last `PROFILER_KEEP` recordings are kept
  per instance; production asks for a confirmation every time; everything is in the audit log. New permission
  `instances.profile` (super admins, administrators and, by `PROFILER_ROLES`, maintainers); new settings
  `PROFILER_ENABLED`, `PROFILER_ROLES`, `PROFILER_PRODUCTION`, `PROFILER_MAX_MINUTES`, `PROFILER_KEEP`,
  `PROFILER_RATE`, `PROFILER_IMAGE`. API: `/api/v1/instances/<name>/profiler` and `/profiles/<id>`.
- **Audit log per project, with exports:** entries now name their project and branch; the audit log filters by
  project, branch, instance, user, action, result and dates, sorts by any column and pages on the server, and exports
  what it shows as CSV or XLSX (a year and `AUDIT_EXPORT_MAX_ROWS` entries at most; each export is audit-logged).
  A project's page links to its own entries. API: `GET /api/v1/audit` (new filters, `sort`, `offset`, `meta.total`),
  `GET /api/v1/audit/facets`, `GET /api/v1/audit/export`.
- **GitHub connection check**, like Verify Deploy Key and Verify Webhook on Odoo.sh: a project's Settings tab checks
  that the repository can be read, the GitHub App covers it with the permissions and events it needs, its webhook
  reaches the panel and its latest deliveries arrived, and every submodule can be read, with what to fix where. API:
  `POST /api/v1/projects/<id>/github/check`.
- **Project resources**, as Odoo.sh's database workers, workers settings and production database size: a
  **Resources** section on a project's Settings tab (one card per stage with steppers for workers and cron workers,
  the HTTP and scheduled actions timeouts, more limits, the computed Odoo container memory and cores, and the line
  "This server: X of Y GB reserved after this change"), a branch's own values under **Override for this branch** on
  its Branch settings tab, and new permission `projects.resources` (administrators; a project's admin level).
  Values stay within administrator maxima (`RESOURCES_MAX_*`, a column of Settings, Resources). Applying restarts the
  instances it changes through a job; production is backed up first and gets its previous values back if Odoo does
  not answer. A change the server cannot hold is refused (`RESOURCES_RESERVATION_CHECK`, `CAPACITY_OVERCOMMIT`).
  New knobs for every layer: `limit_time_real_cron` (`<STAGE>_ODOO_LIMIT_TIME_REAL_CRON`) and the database's
  `max_connections` with Odoo's `db_maxconn` shared out (`<STAGE>_DB_MAX_CONNECTIONS`); with workers set by a project or
  a branch, the Odoo container's limits are computed from them unless set (`RESOURCES_COMPUTED_LIMITS`, `RESOURCES_BASE_MEMORY_MB`).
  Every default stays off, so existing instances do not change. API: `GET/PUT /api/v1/projects/<id>/resources` and
  `/branches/<branch>/resources`.
- **Storage of each instance**: its database and filestore measured every hour (`STORAGE_MEASURE_MINUTES`) and
  kept while it is paused, shown on its Settings tab and per project, against a soft quota per stage
  (`STORAGE_QUOTA_GB_*`, `STORAGE_WARNING_PERCENT`) with alerts at the warning and over the quota (`ALERT_STORAGE`;
  production every day). Nothing is refused.
- With workers, the gateway also sends the long-polling bus of Odoo 15 and earlier to Odoo's gevent server.

- **Branch settings and build options in the web app**, as on Odoo.sh: a **Branch settings** tab on each branch
  (Behaviour upon new commits; New build options with **Use the project's defaults**, module installation, test suite
  with a link to Odoo's test tags, demo data), each value saying whether it comes from the branch, the project or the
  panel; a **Development branches defaults** card on the project's Settings (with tests on staging and commit
  statuses, which explains when the GitHub App may not write them); the eight `PROJECT_*` build settings on the
  Settings page. The branch history shows commits a branch did not build (**Commit received**, Do nothing) and each
  build's tests ("Tests: 132 passed", or the failing module); the history API gives each build's `tests`.
- **Connect as** is next to **Open Odoo** at the top of an instance's page, as on Odoo.sh (it was on the Access tab).
  `LOG_DOWNLOAD_MAX_LINES` and `IMPORT_MAX_GB` are on the Settings page.
- **Build options and tests**, as on Odoo.sh: each project, and each branch for itself, chooses what a new commit does
  (update in place, a new build from scratch, or nothing but a line in the history), which modules a new development
  database gets (the repository's, all, or a list) and which tests run (off, the repository's modules, or custom
  `--test-tags`). Tests run before the instance is served (staging on a throw-away copy of its database) and their
  result feeds the build: "At least one test failed when loading the modules (module_x)". API:
  `GET/PUT /api/v1/projects/<id>/branches/<branch>/options`; new settings `PROJECT_DEVELOPMENT_ON_NEW_COMMIT`,
  `PROJECT_MODULE_INSTALL`, `PROJECT_MODULES`, `PROJECT_TEST_SUITE`, `PROJECT_TEST_TAGS`, `PROJECT_STAGING_TESTS`,
  `PROJECT_TEST_TIMEOUT_MINUTES`. Defaults keep today's behaviour (updates in place, no tests).
- **Commit statuses:** builds are reported on GitHub as commit statuses (`sudoo/<stage>`: pending, success,
  failure, error), linked to the branch's history in the panel, when the GitHub App has **Commit statuses: Read and
  write** (new apps ask for it; add it to an existing app on GitHub) and `PROJECT_COMMIT_STATUSES` is on. GitHub
  refusing never fails a build.
- **Build history per branch:** every branch of a project keeps a history, as on Odoo.sh: one build per commit an
  instance was built from (created, updated by a push or by hand, staging rebuilt), with its commit, who started it,
  times and duration, the instance and job, and a status (building, success, warning when Odoo's log has warnings or
  errors while loading the modules, failed, dropped once the instance moved on), plus the branch's stage changes and
  restores. Read it with `GET /api/v1/projects/<id>/builds`, `GET /api/v1/projects/<id>/branches/<branch>/history`
  and `GET /api/v1/projects/<id>/builds/<build_id>`; the `build` live event carries every change.
  `PROJECT_BUILDS_KEPT` (200) sets how many builds each branch keeps.
- **Live logs, as on Odoo.sh:** the instance's **Logs** tab follows `odoo.log` as Odoo writes it, and also shows the
  logs of its latest creation, update and module upgrade (`install.log`, `update.log`, `upgrade.log`). Pause keeps new
  lines aside and counts them, the search filters on the server (plain text, any case) and highlights its matches,
  levels are coloured (werkzeug's request lines dimmed), long lines wrap or scroll, and **Download** saves the log as
  text (`LOG_DOWNLOAD_MAX_LINES`, 100,000). `GET /api/v1/instances/<name>/logs/files`, `.../logs/stream` (Server-Sent
  Events, resumed with Last-Event-ID) and `.../logs/download`.
- **Connect as:** open an instance's Odoo signed in as any active internal user, without their password, from the
  **Access** tab. The panel gives a single-use link valid for 60 seconds; the instance's gateway has the panel write a
  session for that user through Odoo's own session API and sets the session cookie. No password is read or changed.
  Needs `instances.credentials`; production asks for "production" to be typed; giving and using a link are both
  audit-logged. `GET /api/v1/instances/<name>/odoo-users`, `POST /api/v1/instances/<name>/connect`.
- **Import database:** upload a dump from Odoo.sh, another server or Odoo's database manager (`.zip` with the
  filestore, `.dump`, `.sql`, `.sql.gz`) into an instance from its **Backups** card. The upload goes in resumable
  pieces up to `IMPORT_MAX_GB` (20); a zip is checked for paths that would leave it, and a dump of another major Odoo
  version is refused with what to do instead. The import backs up the current state first (Rollback is offered),
  neutralises the data unless unticked (the default outside production) and keeps users' passwords. Production asks
  for "production" to be typed. `POST /api/v1/instances/<name>/imports` and what follows it.
- **Backups show their revision and a comment:** every backup records the commit the instance's code was at, and
  **Back up now** takes an optional comment; both show in the instance's list of backups and in `GET /api/v1/backups`.
- **History and Builds in the web app:** a branch opens on its **History**, a timeline of its builds grouped by day
  (author, commit with a hash to copy and a link to GitHub, status, **Connect** on the live build, the reason and
  Odoo's messages for a failed or warned build, **View log**), stage changes and restores, with **Show older**. A
  project's new **Builds** tab shows the last four builds of every branch side by side, by stage, with **GitHub**,
  **Rebuild** and **Connect**. **Builds kept per branch** is in **Settings**. The demo shows both.
- **Branch commands, as on Odoo.sh:** the branch pane's toolbar has Clone, Fork, Merge, SSH, SQL, Submodule and
  Delete, each with its command for the branch (and **Open the web shell**, **Add submodule**, **Delete instance**
  where you may). `GET /api/v1/instances/<name>` names the instance's containers and database user (`containers`) for
  those who may open a shell or see its credentials; never a password.

### Changed

- **A project is one workspace, as on Odoo.sh:** its branches in a sidebar grouped by Production, Staging and
  Development (with a filter, the Odoo version and a status dot), and the chosen branch beside them with commands to
  copy (clone, merge) and its instance's tabs, at `/projects/<project>/branches/<branch>/<tab>`. Dragging a branch onto
  another stage (mouse, long press, or the keyboard) asks for the same confirmation as **Move to**. The **+** of
  Staging and Development shows how to push a new branch. Instance pages at `/i/<name>` are unchanged.

### Fixed

- **Demo data on Odoo 17 and 18:** a new development database with demo data failed there (those versions have no
  `--with-demo`), and one without demo data still got it. Each version now gets the flag it knows.
- **Build tests, checked against real Odoo 17, 18 and 19:** a failing test's line keeps its exception, the failing
  module is also read from Odoo's per-module summary, repeated warnings count once, theme modules are left out of
  "every module", and the staging test copy first ends other sessions on the source database.
- **Workers of a new instance:** an instance created in a project stage with workers (production with 2, say)
  started in threaded mode until its first restart, while the gateway already sent WebSockets to Odoo's gevent port,
  so live updates in Odoo did not connect. Its `odoo.conf` now gets the stage's workers and limits when it is created.
- **Branch resources after a stage change:** a branch's **Resources of this branch** still named its former stage
  after it was moved (drag and drop or **Move to**) until the page was reloaded.
- **Audit log of a new instance:** the **Create instance** entry of a project's branch had no project or branch (they
  were read from the instance's files, which do not exist yet), so the CSV and XLSX exports left them empty. They come
  from the creation's plan now.
- **Scheduled staging refresh:** saving the schedule showed "Last refresh just now" although nothing was refreshed.
  The last refresh is now the last run that started a rebuild (on schedule or with **Refresh now**), and saving the
  schedule again keeps it.
- **Pre-promotion checklist:** a production backup taken minutes ago read "backed up 0 hours ago"; it now reads "less
  than an hour ago" (and "1 hour ago").
- **Deleting an instance:** the web app no longer reads the deleted instance again when its deletion ends (requests
  that could only answer 404).
- **Panel on a new ODOO_ROOT:** the panel did not start when `<ODOO_ROOT>/manager` did not exist yet. It now creates
  that folder (readable by root only) when it starts.
- **Gateway under a long ODOO_ROOT:** the gateway's control sockets did not open when their path was longer than a Unix
  socket allows ("AF_UNIX path too long"). They then move to a short private folder in `/tmp` named after the
  gateway folder, which the panel, the gateway service and its health check all find.
- **Stopping the backend:** it no longer reports "Unclosed client session": the gateway's connections to Odoo are
  closed when the process ends, and when the gateway service fails to start.
- **Activity filters:** the labels of the filter fields were not linked to their fields (screen readers did not read
  them, a click on a label did not focus its field). Every field label now names the input or list inside it.
- **New project:** an error message no longer covers the **Create project** button: the page leaves room under it.

## [3.1.1] - 2026-11-21

### Fixed

- **Managed subdomains follow a new company label:** when Sudoo.sh changes the account's company label, the next check
  asks for each name again under the new label (the hub has it ready), gets its certificate and releases the old name.
  Before, the instances kept the old names until those were removed.

## [3.1.0] - 2026-11-21

### Added

- **Three ways to reach a server, Direct by default:** `EXPOSURE` takes `direct`, `proxy` or `tunnel`. **Direct**, the
  default of new installations, has the gateway answer ports 80 and 443 of the server itself, with certificates from
  the ACME authority (Let's Encrypt): the panel at its own name, each instance at its primary custom domain or managed
  name, else `https://<server address>:<gateway port>`. **Proxy** fits behind your own reverse proxy or load balancer:
  the panel and the gateways listen on `PROXY_LISTEN` (`127.0.0.1`) and trust the visitor's address in
  `PROXY_CLIENT_IP_HEADER` (`X-Real-IP`). **Tunnel** is the earlier quick-tunnel behaviour, for servers that cannot
  open ports. The earlier names `cloudflare-quick` (= `tunnel`) and `host-ports` keep working.
- **The panel's own address with Direct:** `PANEL_DOMAIN` (else `PUBLIC_HOST` when it is a name) is served on port 443
  with a certificate the panel gets and renews itself; until then, or without a name, the panel answers on the
  server's own address with a certificate it makes itself. The gateway forwards the panel's visits to the web
  service, which listens on `127.0.0.1` only, with the visitor's address in `X-Sudoo-Client-IP`.
- **The panel's own managed name with Direct:** a panel linked to a Sudoo.sh account that offers managed subdomains
  gets `panel.<company>.<base>` (for example `panel.acme.sudoo.cloud`) for itself. Sudoo.sh keeps its DNS records,
  the gateway serves it on port 443 and the panel gets and renews its certificate (ACME), like its other names. It is
  the panel's address when no name of your own is set (`PANEL_DOMAIN`, else `PUBLIC_HOST` when it is a name); it is
  released, and the panel goes back to the previous address, when you set a name, turn off `PANEL_MANAGED_NAME`
  (new, on by default) or `MANAGED_SUBDOMAINS`, log out of the account, or Sudoo.sh stops offering it. Checked with
  the managed subdomains. Settings, Addresses and domains shows the name and its state (`public_address.source` and
  `public_address.managed_name` in `GET /api/v1/server`). Needs a hub with panel names; earlier hubs are not asked.
- **Settings, Addresses and domains, How people reach this server:** shows the mode and the panel's address, and
  explains what a change does (ports, addresses, keeping the page open) before it is saved; the change applies when
  the panel restarts. A change that would move the web service (from or to `host-ports`, or proxy on another
  address) is refused there and is made in `.env`. `GET /api/v1/server` returns `public_address`.
- New settings `PANEL_DOMAIN`, `PROXY_LISTEN` and `PROXY_CLIENT_IP_HEADER`.

### Changed

- **Existing installations keep how they are reached:** a `.env` without `EXPOSURE` keeps `tunnel`, the behaviour of
  earlier releases, and an update keeps the value in `.env`; only new installations (and `.env.example`) use
  `direct`.
- `CUSTOM_DOMAINS` and `PUBLIC_SCHEME` left empty follow the mode: `acme` with direct, `external` and `https` with
  proxy, `off` and `http` otherwise. Values set in `.env` are kept.
- With Direct, an instance's gateway port speaks https with the panel's own certificate, and creating an instance no
  longer waits for a tunnel address.
- The documentation treats Direct as the normal path; the concept page "Public links" is now
  [Public addresses](docs/concepts/PUBLIC_ADDRESSES.md), with the tunnel as the option for servers without open ports.

### Fixed

## [3.0.1] - 2026-11-21

### Fixed

- **Linking a new panel to hub.sudoo.sh:** this release ships the hub's public key (`8a23a52cbd0d2e51`) as its root key,
  so a new panel trusts the hub from the start instead of relying on the first key list it sees.
- **Dashboard, "By instance":** each value is written inside its bar when it fits there, otherwise just past the bar's
  end with a gap, so it no longer overlaps the bar; the text inside takes the colour that reads best on the bar (light
  and dark themes). On Arabic pages the chart is mirrored (names on the right, bars growing to the left) and values
  such as "1.7 GB" keep their order. Long instance names are shortened with an ellipsis to fit instead of being cut
  off at the edge.

## [3.0.0] - 2026-11-20

### Added

- **The demo shows every plan:** a **Plan** menu in the demo's banner switches the sample panel between Free, Pro and
  Business (**Show Business features** goes straight to Business); the choice is kept in the browser. The in-browser
  backend then answers as a panel with that plan: what the plan allows (**Settings, Plan**), which premium features run,
  and `402` with the plans that include a feature for the calls of those that do not, so each page shows its plan card.
  With Business the demo has three servers (Frankfurt, Dubai, Virginia) with instances on each, the server of each
  instance, adding and approving a server, choosing the server of a new instance and moving an instance; teams with
  grants and a locked branch, custom domains in several certificate states, off-site copies and a support access grant.
  Each premium page and section is marked with the plans that include it. The premium features' compiled web files are
  added to a demo build from a folder outside the repository (`SUDOO_DEMO_PREMIUM`, or the git-ignored
  `web/demo-premium/`); without them, as in this repository's own builds, every premium feature shows its plan card.
  Tests of the demo's backend run with `npm test` in `web/`.
- **Custom domains follow a moved instance (several servers):** the core keeps what a move needs: a certificate a
  domain was served with can be installed on its new server (`custom_domains.install_certificate`, for any domain,
  not only managed names), and a domain that moved there is marked (`moved`, `dns_pending`) until its DNS points at
  the new server. The domain worker checks such a domain first (the DNS check with the domains package, else the http
  probe), keeps serving the certificate it came with, and says where its DNS points now and where to point it
  ("still points at the old server ... Update its DNS to ..."). `GET /instances/<name>/domains` shows `moved_from`
  and `dns_pending` for each domain, and the **Domains** tab shows a note for each moved domain.
- **Several servers, the panel's side:** the extension points the premium package for several servers (Business
  plans) uses: a server's agent (`sudoo.sh agent [join <code>|status|leave]`, which fetches the panel's premium packages
  with the join code and runs them only when they are signed with the premium key and bound to that panel), the
  instances of every server in lists, counts and access checks, the choice of server for a new instance (`"server"`;
  a project's `default_server`), automatic updates reaching other servers, and the agents' channels. Every instance
  shows its server (`server` in the API; `SERVER_NAME`, `SERVER_REGION`). Without the package, another server answers
  `402` with the plans that include it. New settings `SERVER_NAME`, `SERVER_REGION`, `SERVERS_PANEL_URL`,
  `SERVERS_JOIN_CODE_MINUTES`, `SERVERS_HEARTBEAT_SECONDS`, `SERVERS_OFFLINE_SECONDS`, `SERVERS_REQUEST_SECONDS`,
  `SERVERS_WAIT_MINUTES`, `MOVE_MAX_BACKUP_GB`, `MOVE_CHUNK_MB`, `MOVE_AT_ONCE`, `MOVE_HEALTH_SECONDS`; new permissions
  `servers.manage` and `instances.move`. The web app shares more of its design system with premium pages (lists to
  choose from, text fields, tables, check boxes).
- **Managed subdomains (every plan):** while the panel is linked to a Sudoo.sh account that offers them, each instance
  gets a name under Sudoo.sh's domain, `<branch>.<stage>.<company>.<base>` (for example
  `main.production.acme.sudoo.cloud`). Sudoo.sh keeps the DNS records from the server's public addresses; the gateway
  answers the name on ports 80 and 443 and the panel gets and renews its certificate itself, whatever
  `CUSTOM_DOMAINS` says. Once served it is the instance's address (a primary custom domain still comes first) and the
  instance's **Overview** shows it; the name follows a branch or stage change and is released when the instance is
  deleted (a release Sudoo.sh could not do at once is tried again). **Settings, Custom domains, Managed subdomains**
  shows whether Sudoo.sh offers them, the end of the names, the company label, use, limit and the addresses sent, with
  **Check now**. New settings `MANAGED_SUBDOMAINS` (on), `MANAGED_SUBDOMAIN_COMPANY`,
  `MANAGED_SUBDOMAINS_CHECK_MINUTES`; API `GET /api/v1/server/managed-subdomains`,
  `POST /api/v1/server/managed-subdomains/sync`, and `managed_name` on every instance. Requests for premium packages
  and managed names no longer wait for the account's daily API quota.
- **Managed subdomains for instances on other servers:** with several servers, instances on the other servers get
  their managed name too. Only the main panel asks Sudoo.sh for names, for every server: for an instance of another
  server it sends that server's public addresses (`ipv4`, `ipv6` in the request), which its agent reports from its own
  `PUBLIC_IPV4` / `PUBLIC_IPV6` (no longer taken from the main panel), and Sudoo.sh points the name there. Agents
  never talk to the hub and no server holds a DNS token. The panel tells each agent the names its server serves;
  that server's gateway answers them on ports 80 and 443 and its agent gets and renews their certificates (ACME,
  HTTP-01). A name follows its instance: a move points it at the new server with its certificate (served there at
  once) and the old server stops serving it, a branch or stage change gives the new name, a deletion releases it,
  and a change of a server's addresses moves its names. A name is never released while its instance is being moved.
  `GET /api/v1/server/managed-subdomains` has `other_servers`.

- **A project's backups:** a project's page has a **Backups** tab with the backups of all its branches, grouped by
  branch with its stage, newest first, each with **Download**, **Restore** (into the instance it was taken from) and
  **Delete**, for those who manage backups (the project's access and branch locks apply). On the **Branches** tab an
  instance with backups shows how many, linking to the instance's new **Backups** tab. API: `GET /api/v1/backups`
  takes `?instance=<name>` and `?project=<id>`.

- **Premium features install automatically:** teams and access per project and branch, custom domains, off-site
  copies of backups, support access and several servers are premium features that install automatically from
  Sudoo.sh when your plan includes them (after each signed lease; **Check now** in Settings › Plan › Premium features,
  which also shows what is installed, waiting, failed or switched off). Each package is checked with a key used only
  for premium packages, tied to your server (installation and hardware fingerprint, renewed before it ends) and
  checked again every few minutes; a copy on another machine does not load. When a plan no longer includes a
  feature, the panel backs up its data, switches it off and keeps everything; restrictions and locks stay enforced and
  can always be removed, and custom domains set up before keep serving and renewing their certificates (the panel
  does that itself; adding new domains comes with the package). Without the package, its pages show the
  plans that include it and its API answers `402`. New settings `PREMIUM_AUTO_INSTALL`, `PREMIUM_CHECK_HOURS`,
  `PREMIUM_RETRY_MINUTES`, `PREMIUM_WATCH_MINUTES`, `PREMIUM_BINDING_RENEW_DAYS`, `PREMIUM_MAX_PACKAGE_MB`,
  `PREMIUM_PUBLIC_KEYS` (`.env` only), `PUBLIC_IPV4` and `PUBLIC_IPV6`; API `GET /api/v1/premium`,
  `POST /api/v1/premium/sync`. The heartbeat reports the installed premium features and the server's public
  addresses. See [Premium features](docs/PREMIUM.md).

- **Support access:** super admins and administrators let Sudoo.sh support into the panel for a while (Settings ›
  Support access, or Support access in the side bar; permission `support_access.manage`): view, panel admin, or panel
  admin with shells; 1 hour, 1 day or 3 days (`SUPPORT_ACCESS_MAX_HOURS`); all projects and instances or chosen ones
  (restricted projects and branches stay closed unless chosen; locked branches always stay locked); an optional ticket.
  Support reaches the panel only through its own signed connection to the Sudoo.sh hub (no inbound port): while a
  grant is active the panel asks the hub for work and runs each request against its own API as
  "Sudoo.sh support (staff name)" with exactly the grant's level and scope, never accounts, tokens, teams, access,
  settings, plugins or support access itself. Every page shows "Sudoo.sh support is connected" with **End now**;
  withdrawing takes effect at once. Everything support does is in the activity log, marked **Support**; support shells
  are always recorded. Settings `SUPPORT_ACCESS` and `SUPPORT_ACCESS_MAX_HOURS`. Needs a hub with support access
  (sudoo-hub migration 0019).
- **Instances by stage in the heartbeat:** the `usage` the panel reports to Sudoo.sh now carries
  `instances_by_stage` (production, staging and development counts, as Settings › Plan shows them). Needs a hub that
  takes it (sudoo-hub with migration 0018): update the hub first.
- **Database upgrades to a newer Odoo version (Odoo.sh-like):** a new **Upgrade** tab on instances (permission
  `instances.upgrade`, administrators and super admins) offers **Upgrade to Odoo <next>**. The pre-check lists the
  installed modules and flags the repository's modules whose manifest version is not of the target series, or that
  are missing from the upgrade branch (named after the target, such as `19.0`). **Test the upgrade** backs up the
  instance and loads the upgraded copy into a new, neutralised staging instance on the target version (the upgrade
  branch's code, or plain Odoo with standard modules when there is none); **Run the test again** repeats it from a
  fresh backup, also on each push to the upgrade branch (project option `upgrade_test_on_push`, OpenUpgrade). **Upgrade
  production** (confirmed) puts Odoo into maintenance (the gateway shows **Upgrading**), takes a `before-upgrade`
  backup, upgrades the latest data, switches the instance to the target version and branch and starts Odoo; any
  failure puts everything back. For `UPGRADE_ROLLBACK_MINUTES` the upgrade can be cancelled, and is rolled back by
  itself if Odoo stops answering. Enterprise databases use Odoo S.A.'s upgrade service through Odoo's own upgrade
  script, downloaded when the upgrade runs, with the customer's subscription code (never stored, masked in logs) and
  Odoo's terms accepted each time; Community databases use OCA OpenUpgrade (AGPL-3.0), downloaded unmodified at a
  pinned commit by a temporary container. Nothing third-party is bundled. New settings `UPGRADE_METHODS`,
  `UPGRADE_SERVICE_URL` (`.env` only), `OPENUPGRADE_REPOSITORY`, `OPENUPGRADE_REFS`, `OPENUPGRADE_LIB`,
  `UPGRADE_ROLLBACK_MINUTES`, `UPGRADE_MAX_HOURS`, `PROJECT_UPGRADE_TEST_ON_PUSH`; API under
  `/api/v1/instances/<name>/upgrade`; the demo simulates the whole flow.
- **Developer API tokens:** a new `developer` scope for developers' tools and CI. Such a token lists and reads the
  projects, branches and instances it may see, their logs, jobs and caught e-mails; starts, pauses, restarts, gets the
  latest code and updates submodules; creates development instances (also for a project's development branch) and
  deletes them. It never changes settings, accounts, teams, access, locks or stages, never touches production,
  restores, backups, domains, off-site copies or shells; every other call is refused with 403 and audit-logged. As
  every token, its rights are the scope's ∩ its creator's role, and the creator's teams, project access and locked
  branches apply. New **Developer** choice on the API tokens page; English and Arabic.
- **Link the panel within 7 days:** a panel is linked to a Sudoo.sh account within `LINK_DEADLINE_DAYS` (default 7,
  **Settings, About**) of its first run, recorded once and never reset. Until then super admins and administrators
  see a banner on every page that cannot be closed (**Link this panel to your Sudoo.sh account**, with the days left
  and, for super admins, the button to the link flow); other roles see nothing. After the deadline, while unlinked,
  new instances, stage changes into production and updates or deploys (also automatic updates on push and instances
  for new branches) are refused until it is linked; instances keep serving and backups keep running. Linking lifts
  it at once. `GET /plan` has `link_deadline`; the demo shows the banner. English and Arabic.
- **The plan from the hub's signed licence:** the panel reads the entitlements of the lease each heartbeat brings
  (production branches with extra branches and privileges, servers, projects, teams, `per_project_access`,
  `custom_domains`, `offsite_backups`, support level, withheld production, ban) and enforces them: one more production
  branch beyond the plan (counted on the whole panel, with plain production instances), while production is withheld
  or once the licence expired is refused with how to get more; teams, access per project, adding custom domains and
  turning off-site backups on need a plan that has them (what was set up keeps working; locks are on every plan).
  When the hub cannot be reached the last entitlements apply until their `valid_until`, then only new production
  waits; nothing that runs ever stops. Unlinked panels have the Free plan of the licence (one production branch),
  with a banner saying how to link. After a downgrade, the licence's `production_keep` is applied: the other
  production branches become development branches (never deleted), with an alert (kind `plan`) and an audit entry.
  The heartbeat reports `usage` as the hub expects it (`servers`, `projects`, `production_branches`,
  `production_branch_names`). The hub's 429 `api_quota_exceeded` is respected: counted calls wait until its
  `Retry-After` (heartbeats and licence calls never count), with a clear message. New **Settings, Plan** tab (plan,
  entitlements, usage, withheld state, branches leaving production, **Manage the plan**, **Compare plans**) and
  upsell notes on the Teams page, the Access tab and the Domains tab. New settings `PLAN_PRICING_URL` and
  `PLAN_ACCOUNT_URL`; API `GET /plan`. English and Arabic; the demo has a Pro plan.
- **Teams, per-project and per-branch access, locked branches:** administrators make **Teams** (named groups of
  accounts; new page in the sidebar) and give a team or a person a level in a project or one of its branches on the
  project's new **Access** tab: no access, view, developer, maintainer or admin. What someone may do in a project is
  their role ∩ their level there (a grant never adds to a role); their own grant comes first, else their teams'
  highest. A project is open to everyone with their role (every existing project: nobody is locked out by the
  upgrade) or **restricted** to those granted; a branch can have its own levels or be restricted (production for the
  leads). A branch can be **locked**: nobody but the people and teams the lock allows may open (credentials, logs,
  caught e-mails, the shell), change, update, deploy, back up, restore or delete its instances, change its stage or
  copy its production data into staging; super admins must unlock first (always allowed). Automatic updates,
  staging rebuilds, expiry, deleted-branch handling and new-branch instances skip locked branches, and a visit does
  not start a paused one; a locked production branch keeps serving and keeps its daily backups. Locks show as a
  **Locked** tag on branches and instances and a banner on the instance's page. Everything is enforced on the server
  (`projects/access.py`) for the API, jobs, backups, off-site copies, shell tickets and WebSockets, the gateway and
  the live events, with hidden projects, branches and instances left out of every list (404 when asked for), and
  audited. New permissions `teams.manage` and `projects.access` (administrators and super admins). API:
  `GET`/`POST /teams`, `GET`/`PATCH`/`DELETE /teams/<id>`, `GET`/`PUT /projects/<id>/access`,
  `PUT`/`DELETE /projects/<id>/branches/<branch>/lock`; `lock` on instances and branches, `permissions` on branches
  and in `meta` of `GET /instances/<name>` and `GET /projects/<id>`. English and Arabic; the demo has teams, an
  access setup and a locked production branch. Docs: User manual (Teams, Project access and locked branches), Admin
  guide, Security, Panel API.

- **Projects in the web app:** **Projects** is the first entry of the sidebar (the Instances page is now **All
  instances**). The Projects page shows one card per project (repository, Odoo version and edition, branches per
  stage, instances running, paused or working, a needs-attention mark) and the plain Odoo instances **Without a
  project**. A project's page lists every branch of its repository grouped by stage, with its latest commit,
  instance and status, and per branch: **Create instance**, **Open**, **Details**, **Logs**, **Move to** another
  stage (with the production confirmations), **Rebuild from production** (staging) and **Delete instance**; a warning
  when the server runs as many instances as it takes comfortably; and a **Settings** tab (name, Odoo version and
  edition, what happens on new, pushed and deleted branches, lifetimes, delete). **New project** links a repository
  or creates a new, empty one, and says what to grant on GitHub when the app may not. **Connect GitHub** can ask
  for repository creation; the Settings page has a **Projects** group; instances link to their project; every text
  in English and Arabic; the demo has projects too. Docs: User manual section 2 (Projects); the sections after it are
  renumbered.
- **New projects with a new, empty repository:** creating a project can make a new, totally empty repository (no
  README, no branch; private by default) in an organisation the GitHub App is installed on, instead of linking an
  existing one. It needs the app's **Administration: write** permission: **Connect GitHub** can ask for it ("Also
  allow creating repositories"), and for an app connected earlier the panel detects that it is missing and says how
  to grant it on GitHub (and that an organisation owner must accept it). The permission is used only through a
  separate one-hour token for that one call; git's tokens stay read-only. API: `GET /projects/repository-owners`,
  `POST /projects` with `create`, `POST /github/app/manifest` with `create_repositories`; installations record their
  permissions.
- **Projects (the Odoo.sh model):** a project is one GitHub repository, and each of its branches has a stage:
  production, staging or development. Every instance of a repository now belongs to its project; existing instances
  join theirs when the panel starts (one project per repository; only `PROJECT=<id>` is added to their `.env`, so
  names, stages, addresses and backups stay and nothing restarts); plain Odoo instances stay standalone. A branch's
  stage decides how its instances are made: development from a fresh database (with demo data by default), staging
  from a **neutralised copy of the project's production instance** (new backup kind `staging-copy`; mail to the mail
  catcher, scheduled actions, payment providers and IAP off, new database identity, its own address), production
  with the production rules. A push to a staging branch rebuilds it from a new copy of production, then the branch's
  code (or only takes the code). A new branch pushed to GitHub gets a development instance by itself, up to a limit
  per project; a branch deleted on GitHub archives (backup, switched off) or deletes its development instance;
  production is never touched. Changing a branch's stage moves its instances, with confirmations for production and
  one production branch per project by default. New settings (**Settings, Instances, Projects**):
  `PROJECT_PRODUCTION_BRANCHES` (1), `PROJECT_AUTO_DEVELOPMENT` (on), `PROJECT_MAX_AUTO_DEVELOPMENT` (3),
  `PROJECT_DEVELOPMENT_DEMO_DATA` (on), `PROJECT_STAGING_ON_PUSH` (`rebuild`), `PROJECT_BRANCH_DELETED`
  (`archive`); each project can change all but the first, and its own lifetimes. New permission `projects.manage`
  (maintainers and up). API: `GET`/`POST /projects`, `GET`/`PATCH`/`DELETE /projects/<id>`,
  `PUT /projects/<id>/branches/<branch>/stage`, `POST /projects/<id>/branches/<branch>/instances` and `/rebuild`;
  instances gained `project`; live event `project`. The SQL neutralisation used before Odoo 16 now also switches off
  payment providers.
- **Mail catcher for staging and development:** like Odoo.sh, staging and development instances never send real
  e-mail. A new service in `compose.yaml`, `sudoo-mail` (`sudoo.sh mailcatcher`: the panel's own image, a small
  built-in SMTP server; no Docker socket, no `.env`, no published port, read-only, no capabilities, its own internal
  network), catches it. The panel connects it to each staging and development instance's Odoo network (never the
  database network) and disconnects it from production; writes `smtp_server = sudoo-mail`, port 2525 and a password
  of the instance's own into their `odoo.conf` (production keeps none); and switches off the instances' own outgoing
  mail servers (`ir.mail_server`) when they start, leave production, and every 5 minutes. Each e-mail is filed in
  the mailbox of the instance that authenticated, so no instance can read or fill another's. New **Mail** tab on
  staging and development instances: list with search, viewer with the HTML (served on its own address with a
  strict Content-Security-Policy and shown in a sandboxed frame: no scripts, forms or remote images; inline `cid:`
  images shown), the text, every header and the attachments, **Download .eml**, **Delete**, **Delete all** and
  **Release** (a copy, with its attachments, from the panel's own address to one typed address; audit-logged).
  New permissions `caught_mail.view` (developers and up) and `caught_mail.manage` (administrators and super
  admins). Settings (**Settings, E-mail, Mail catcher**): `MAIL_CATCHER_RETENTION_DAYS` (7),
  `MAIL_CATCHER_MAX_MESSAGES` (500 per instance), `MAIL_CATCHER_MAX_MB` (200 per instance),
  `MAIL_CATCHER_MAX_MESSAGE_MB` (25) and `MAIL_CATCHER_RELEASE` (on). API: `GET /instances/<name>/mail` and
  `/mail/<id>` (`/html`, `/raw`, `/attachments/<n>`), `DELETE` and `POST /mail/<id>/release`. The mail plugin
  `smtp` gained `send_message` for an already composed message.
- **Help button:** a **?** button in the top bar of every page (or the **?** key, not while typing; **Esc**
  closes) opens a help panel at the side: a short text about the page shown (every page and instance tab, English
  and Arabic), links to its documentation in the panel and on docs.sudoo.sh, the keyboard shortcuts, **Contact
  support** (the Sudoo.sh help desk when the panel is linked to an account, otherwise `SUPPORT_URL` or an e-mail to
  `ADMIN_CONTACT_EMAIL`), the panel's version and **Copy diagnostics** (version, browser, page, time and the
  request id of the last error; nothing secret). New settings `DOCS_URL`, `SUPPORT_URL` and `HUB_SUPPORT_URL`
  (**Settings, General, Help**) and `GET /api/v1/help`. Side panels now take the focus when they open and give it
  back when they close.
- **Licence acceptance:** the panel asks for the Sudoo.sh Licence Agreement to be accepted. First-run setup starts
  with a **Licence agreement** step: the agreement's summary (in Arabic, its Annex A), the full text of the copy this
  release bundles (identical to `LICENSE.md`, a test checks it), a link to the published version, the declared use
  (**Personal projects**, **My company**, **Service provider**) and, for a company, "I accept it on behalf of my
  company, and I am authorised to bind it". On a server set up before 3.0, the first super admin to sign in sees
  **Accept the licence agreement** before anything else; other people, API tokens and the instances carry on, and
  instances keep running and serving. A new setting `LICENCE_REQUIRED_VERSION` (default `1.0`, never newer than the
  bundled version) asks again when a new version is required. Each acceptance is recorded append-only (version,
  SHA-256 of the text, time, address, browser, user, declared use, statements, language) and audit-logged. **Settings,
  About** shows the bundled and required versions, who accepted and when, every acceptance (the installer's from
  `/opt/odoo/manager/licence-acceptance.log` too) and the full text, with **Review and accept again**. A panel linked
  to a Sudoo.sh account sends its acceptances to the hub, and keeps them for later while the hub does not take them
  yet. API: `GET /licence/text` (public), `GET /licence`, `POST /licence/acceptances`; `POST /setup` takes
  `licence`. The demo shows the setup step at `/setup` and the step for super admins.
- **Resources per instance, production first under load:** instances have no processor or memory limit by
  default. Each instance's Odoo and database containers carry its stage's weights, which only act when the server is
  busy: processor weight (`cpu_shares` 4096 production, 2048 staging, 1024 development), a memory reservation for
  production (1 GB Odoo, 512 MB database: the kernel takes memory back from the others first) and an out-of-memory
  score (0, 300, 600: development is stopped first, production last; `-1000` is refused). All are settings
  (`PRIORITY_*`, **Settings, Resources**). Optional limits per stage and per instance, all off by default: Odoo
  workers and cron workers, `limit_memory_soft`/`_hard`, `limit_time_cpu`/`_real`, and the memory and processors of
  the Odoo and database containers (`<STAGE>_ODOO_WORKERS`...). The instance's **Settings** tab has a
  **Resources** section: its priority, usage against limits, the limits with the stage's defaults, warnings (memory
  reserved beyond the server's, workers too many for the processor or the memory limit; never refused), and
  **Apply**, a job that rewrites `odoo.conf` and the compose file and restarts Odoo (production: confirmation, a
  backup first, the previous limits put back if Odoo does not answer). With workers, Odoo's WebSocket port is
  published on `127.0.0.1` and the gateway sends WebSockets there. New permission `instances.resources`
  (administrators and super admins). API: `GET`/`PUT /instances/<name>/resources`. The integrity check compares
  the weights too.
- **Off-site copies of backups:** backups can be copied to S3-compatible storage (Cloudflare R2, AWS S3, Backblaze
  B2, MinIO), set up by a super admin under **Settings, Backups** (address, region, bucket, folder, access key; the
  secret stored encrypted and never shown again) with **Test connection**. Every backup of a production instance is
  copied (manual, daily and deletion by default, `OFFSITE_KINDS`), the others with `OFFSITE_OTHER_STAGES`. Each copy
  has a `manifest.json` (instance, stage, kind, time, each file's size and SHA-256) and an Ed25519 signature made
  with the panel's own key (made when the feature is first switched on, encrypted with the vault key); the public
  key and its fingerprint are shown with a download button, to keep a copy off the server. The **Backups** page
  lists local backups and off-site copies per instance with a location badge and the copy's status (uploading,
  stored, verifying, verified, failed with the reason), with **Verify now**, **Copy again**, **Restore** (download,
  check signature and checksums, fail closed, then the usual restore with Rollback; a public key kept off the server
  can be given for the check) and **Delete**. Retention per instance: the newest copy of each of the last 30 days
  and 12 months for production, 7 days for the others (all settings), never the newest verified copy; every
  removal is audit-logged. New alerts and e-mails "Off-site copy failed" and "No recent off-site copy" (a production
  instance without a copy for `ALERT_OFFSITE_MISSING_HOURS`, 26). New permissions `offsite.configure` (super
  admins), `offsite.restore` and `offsite.delete` (administrators and super admins). Requests are signed with AWS
  Signature Version 4 by the panel itself (no new dependency). API: `/offsite`, `/offsite/test`, `/offsite/scan`,
  `/offsite/public-key`, `/offsite/copies` with `verify`, `retry`, `restore` and `DELETE`.

- **Web shell (3.0.0):** a **Shell** tab on an instance's page opens a terminal in the browser (xterm.js, served by
  the panel, no CDN) inside the instance's own containers: bash in the Odoo container as its user `odoo`, an
  **odoo shell** on the instance's database, or **psql** in its database container. Copy and paste
  (Ctrl+Shift+C/V, ⌘C/V), clear, full screen, reconnect, light and dark terminal colours, the panel's monospace font,
  always left to right; a notice on phones. Security first: new permissions `instances.shell` and
  `instances.database_shell` (super admins and administrators; `SHELL_ROLES` can add maintainers or developers for
  the Odoo container, never viewers) and `shells.supervise` (super admins); production shells confirmed every time
  and by default for super admins only (`SHELL_PRODUCTION`); root only for super admins when `SHELL_ROOT` is on
  (off by default). The WebSocket opens only with a one-time ticket (`POST /instances/<name>/shell/tickets`, an
  authenticated change with the CSRF token), valid `SHELL_TICKET_SECONDS` (15), bound to the person, the panel
  session and the instance, offered as a subprotocol, and only from the panel's own Origin; rules are checked again
  when it opens and every 15 seconds. Shells reach only the instance's labelled containers (never the panel or the
  server; containers changed to be privileged or to reach the host are refused). Idle and longest time
  (`SHELL_IDLE_MINUTES` 15, `SHELL_MAX_MINUTES` 60), shells per person and per instance (`SHELL_MAX_PER_USER` 2,
  `SHELL_MAX_PER_INSTANCE` 3); programs run within the container's limits at a lower priority, and **every process
  of a shell is ended when it closes**, background ones included (also after a panel crash). Audit entries `shell
  opened`, `shell closed` (duration, reason), `shell refused`; recordings of what is shown and typed
  (`SHELL_RECORDING`: production instances and root shells by default), encrypted with the vault key, capped
  (`SHELL_RECORDING_MAX_MB`, `SHELL_RECORDINGS_TOTAL_MB`) and kept `SHELL_RECORDING_DAYS`; super admins see the
  shells of an instance, end open ones and play or download recordings (asciicast). API: `GET /instances/<name>/shell`,
  `POST /instances/<name>/shell/tickets`, the socket `/instances/<name>/shell/socket`, `GET /shells`,
  `DELETE /shells/<id>`, `GET /shells/<id>/recording`. The demo has a simulated shell. Fonts are no longer inlined
  into the stylesheet as `data:` addresses, which the Content-Security-Policy refused.

- **Invitations:** new people are invited by e-mail instead of being given a password. **Accounts, Invite** takes
  the e-mail address and role (name optional) and e-mails a link (new e-mail "Invitation" on the E-mail templates
  page) that works once and for `INVITATION_HOURS` (72 by default); only a hash of it is stored. The invitee chooses
  a password and name on `/invite` (English and Arabic), is signed in and offered a second factor (required first
  when `REQUIRE_SECOND_FACTOR` applies). The list shows **Invited, expires in ...** with **Send again** (the old link
  stops working) and **Revoke**; everything is in the audit log. New settings: `INVITATION_HOURS`,
  `INVITATION_PASSWORD_FALLBACK` (when a password may still be set directly: `no_mail` by default, only while e-mail
  is not set up; `always`, `never`) and `INVITATIONS_PER_HOUR` (20). Wrong links are throttled like wrong passwords.
  API: `GET/POST /invitations`, `POST /invitations/<id>/resend`, `DELETE /invitations/<id>`, `POST /invitation`,
  `POST /invitation/accept`; `POST /users` follows `INVITATION_PASSWORD_FALLBACK`.

- **Log viewer:** the Odoo log, job logs (job drawer, instance creation) colour each line by level (errors red,
  warnings amber, steps done and other success green, debug muted), read from the standard Odoo log format and the
  jobs' markers; tracebacks keep their error's colour. A level filter with counts, search with highlighted matches and
  next/previous (Enter, Shift+Enter), follow new lines (pauses when you scroll up), copy, and a full-screen view (Esc
  leaves it, phones too). Logs stay left to right in Arabic; only the last 2,000 lines are drawn at first.

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
- **Limits on what each server's agent reports (several servers):** new settings `SERVERS_INSTANCES_KEPT` (instances
  the panel keeps of one agent's report, default `2000`), `SERVERS_REPORT_KB` (largest description of one instance
  or job, default `64` KB) and `SERVERS_LOG_ENTRIES_PER_DAY` (log entries taken from one agent per day, default `5000`;
  beyond that the log says once that some were left out). All three are on the Settings page, **Servers**.

### Changed

- **Docs: install commands from PyPI:** the plugin pages show `pip install sudoo-plugin-sdk`, and the Panel API page
  and the documentation index show `pipx install sudoo-cli`, with links to both packages on PyPI and to the command
  line guide.

- **The web app's controls are shadcn/ui components** (Radix, Tailwind CSS), with the panel's look kept: buttons,
  fields, switches, tags, notes, tables, menus, dialogs and side panels. Every list to choose from opens a designed
  list (rounded, with a check mark, right to left in Arabic) instead of the browser's own; on a phone a dialog comes
  up from the bottom edge and the sidebar is a sheet. Keyboard use is kept. The design token `--accent` is now
  `--brand` (shadcn uses `--accent` for hover fills), and `BRAND_COLOR` now colours the web app as documented.

- The code of teams, adding custom domains, off-site copies and support access is no longer part of this
  repository: it comes with the premium packages. Serving and renewing the custom domains that exist stays in the
  panel. Their data stays where it was (nothing is migrated or lost), and the panel itself
  keeps serving existing custom domains, enforcing existing restrictions and locks, and removing them.

- **Panels not linked to a Sudoo.sh account have the Free plan:** one production branch on the whole panel (on top
  of `PROJECT_PRODUCTION_BRANCHES` and `MAX_PRODUCTION_INSTANCES`), and no new teams, access rules, custom domains or
  off-site backups. Nothing that exists stops: production branches above the limit keep serving, domains keep
  renewing, off-site copies already made stay. Link the panel (Settings, Marketplace) to get its plan.
- **API tokens act for their owner:** a token's permissions are now its scope's role ∩ the role of the account that
  created it, and that account's teams, project access and locked branches apply to it. Tokens created with
  `sudoo.sh create-token`, or whose creator was deleted, keep their scope's role but are kept out of restricted
  projects and branches and cannot act on locked branches. Nothing changes while no project has access rules.
- **Licence:** the panel is now licensed under the Sudoo.sh Licence Agreement 1.0 (LICENSE.md, also at
  https://sudoo.sh/legal/license) instead of the PolyForm Noncommercial License 1.0.0. Use is limited to the plans at
  https://sudoo.sh/pricing; contributions are licensed to the author as described in CONTRIBUTING.md.
- **No container limits by default:** `ODOO_MEMORY_LIMIT`, `ODOO_CPU_LIMIT`, `DB_MEMORY_LIMIT` and `DB_CPU_LIMIT`
  (2 GB, 2 cores, 1 GB, 2 cores for every instance) are replaced by the per-stage defaults, all off. A value changed
  on the Settings page becomes the default of every stage; a value in `.env` is no longer read. Instances get the
  change at their next start or restart.
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
- **Answers of another server's agent are never pages of the panel:** a response the servers package marks as
  passed on from another server (`X-Sudoo-Forwarded`, removed before it is sent) gets a sandboxed
  `Content-Security-Policy` with no script (`default-src 'none'; ...; sandbox`), whatever its content type.
- **Codes are typed in boxes built on shadcn's `input-otp`** (the second factor at sign-in, and confirming the
  authenticator app or the e-mail code in Account). Six boxes grouped 3 + 3, now one field underneath with one label
  (`autocomplete="one-time-code"`, numeric keyboard), so code suggestions, password managers and pasting the whole
  code work as with a plain field; digits run left to right in Arabic. A typed digit pops in, a wrong code empties and
  shakes the boxes (now in Account too), no motion when less is asked for.

### Fixed

- **Restoring into a new instance right after its database starts:** on a new database volume, PostgreSQL's image
  first runs a temporary server that answers on its socket and then stops, so loading a backup into a new instance
  (an instance made from a backup, a move to another server) could fail with "No such file or directory" at random.
  The panel now waits until the database answers over TCP inside its container (the real server).
- **Several servers:** a move that fails and is rolled back says so in the job's error ("runs on <server> again")
  instead of the general hint; `GET /api/v1/backups?instance=<name>` lists the backups of an instance of another
  server from that server.
- **Web app dependencies:** `minimatch`, used only by the shadcn command line tool, is held at 11.0.1 or later
  (GHSA-g3hv-75wg-m249, a denial of service through crafted patterns).

- **Panel polish:** recent commits show when they were made in the reader's language ("3 hours ago", in Arabic too),
  with the full date and time on hover, the short hash linked to the commit (or copied) and the author
  (`recent_commits[].when` is now an ISO time in UTC, with `full_sha`). Switching e-mail templates no longer flashes
  "Unknown placeholders" from the previous one. One thin scrollbar everywhere, as on the websites. Moving between
  Settings tabs keeps the title and the tabs still, with a "Settings › tab" breadcrumb. A back-to-top button on long
  pages, and a "Back to website" link in the demo's banner.

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
- **No border around the Instances ring on the dashboard.** Its box was named `ring`, which is also a Tailwind
  utility (a 1px shadow in the text colour), so a dark frame was drawn around the chart, in light and dark mode and in
  the demo. The box is now `donut`.

### Security

- **Several servers:** a backup of an instance on another server is deleted, downloaded or restored (also into
  another instance) only by those who may manage backups in that instance's project and branch, as on one server;
  the panel checks it before passing the request on, and the other server refuses a backup of a project's instance
  the panel did not check. An instance moved to another server keeps the identity the panel has on record for it
  (its `INSTANCE_ID` and project), never what a backup's manifest or the other server says; the panel names it when
  it plans the instance. Another server gets GitHub App tokens only for the repositories of the instances the panel
  itself placed or moved there. A server's agent loads only premium packages bound to the panel it joined, as that
  panel states over its signed channel, and checks bindings against its clock but never earlier than the latest time
  the panel signed.
- **Project and branch access:** creating or rebuilding a staging instance from production needs the right to
  restore production's instance, so a restricted production branch keeps its data from people it does not name; a
  push rebuilds staging from production only when everyone who may open the staging instance may also do so in
  production (otherwise it only brings the new code). Backups record their instance's project and branch, which keep
  applying once the instance is deleted (list, download, delete, restore); such a backup taken by an earlier version
  is only for those from whom no project is hidden.
- **Database upgrades:** an upgrade's test instance is the very instance its test made (its creation time and
  project are recorded); deleting it forgets it, so a later instance given the same name is never treated as the
  test instance and never receives a copy of production on a re-run.
- **Stages:** moving an instance or a branch out of production needs `instances.delete`, and a lifetime sent with
  the stage change is weighed with the new stage. Switching a production instance to another branch (the new branch takes
  the earlier one's place in production unless another production instance stays on it), promoting an upgrade onto
  its upgrade branch and connecting a repository to a plain production instance keep to the project's and the
  plan's production-branch limits (409).
- **Instance files:** `.env` lines end only at line feeds, and values with C1 control characters or the Unicode line
  and paragraph separators are refused, so an Odoo administrator login read from a database cannot add keys.
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
- **Folders shared with the gateway and the mail catcher:** the panel no longer follows a link placed in
  `GATEWAY_DIR` or `MAIL_CATCHER_DIR`. Files there (routes, certificates, challenges, the catcher's settings) are
  written to a temporary file with a random name, created only when nothing is at that name, and moved into place;
  modes are set on the folder or socket itself; caught e-mails are read, pruned and removed only inside their own
  mailbox, and only plain files are read. Private files elsewhere are written the same way.
- **Releasing a caught e-mail** sends it only to the address typed: `Resent-*` headers written by the instance are
  removed, and the SMTP envelope is set by the panel (its own address to the typed one) instead of being taken from
  the message's headers.
- **Managed subdomains:** a name Sudoo.sh answers with must end in its announced base domain, and it never replaces
  a domain the server already has (an instance's custom domain); such an answer is shown as a problem instead.
- **Premium keys:** a revoked premium key stays revoked when the keys a release ships change. Bindings and packages
  are signed with separate keys: a package is accepted only with a premium signing key's signature, a binding only
  with a binding key's (one the release ships, `PREMIUM_BINDING_PUBLIC_KEYS` on a test panel, or one a premium
  signing key endorsed in Sudoo.sh's list with `"role": "binding"`), so a binding key cannot sign a package.
  A server's agent that joins a panel learns the same way: the panel's list of packages for agents carries Sudoo.sh's
  premium keys (`"keys"`), which the agent applies before installing, so a binding key Sudoo.sh rotated is trusted
  when a premium signing key the agent already trusts endorsed it, and never on the panel's word alone.
- **Teams:** adding members to a team needs, except for super admins, the right to change the access of every
  project and branch whose grants name the team, and to be allowed by every branch lock that lets it through. A
  team shows only the projects the person reading it may see.
- **Alert webhooks:** in Slack, Discord and Teams the alert's words (which can come from a failed job's output) are
  shown as written: they no longer mention a channel (`<!channel>`, `@everyone`) or form a link with another label.
- **Sudoo.sh support stays within its scope:** a support grant limited to chosen projects or instances no longer opens
  instances outside every project (credentials, logs, actions, shells), and does not create projects or instances
  outside one; a grant for everything, or one that names the instance, still does.
- **Restricted branches stay restricted:** changing the access of a project (`PUT /projects/<id>/access`) refuses
  (403) to add, change or remove the rule of a branch the caller does not manage (`projects.access` in that branch).
- **New instances in a branch need access there in every way:** connecting a repository to a plain Odoo instance
  needs `instances.create` in the branch of the project it joins, and a database upgrade test needs it in the upgrade
  branch; running an upgrade test again needs `instances.restore` on its test instance, so a locked branch stays
  locked.
- **Live job logs:** `job.output` events carry the job's instance, and a job event whose instance the panel cannot
  tell (another server's) goes only to callers from whom nothing is hidden.
- **Automatic copies of a restricted production** into staging also count API tokens (those whose owner is gone too)
  and active support grants (the support package's `active_users` hook) as people who could open the copy.
- **Second factors:** adding a second factor next to one already set up needs the password, as replacing one does.
- **API tokens created by a token** belong to the same account (they follow its role and access); a token whose
  account was deleted cannot create tokens.
- **Premium and hub keys:** a key list never trusts an expired key again, never makes a retiring key active again or
  extends its end, and a key endorses another only while it is usable itself.
- **Plugins:** the module-name check also covers `src/<name>.py` and folders without `__init__.py`, and premium
  packages go on the module path before plugins.
- **Shared folders:** certificates in the gateway's folder (also those of managed names that go with a move) and the
  mail catcher's `catcher.json` are read, written and removed without following a link, and are read only as regular
  files of bounded size (a FIFO planted there no longer blocks instance starts).
- **Premium keys shipped:** the panel now trusts Sudoo.sh's premium release key (`4f4f3c18cd19ac11`) and the hub's
  binding key (`20aded7fa2a24bd4`) out of the box (`SHIPPED_PREMIUM_KEYS`, `SHIPPED_PREMIUM_BINDING_KEYS`), so premium
  packages install on a new panel without any key setting.

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

[Unreleased]: https://github.com/Sudoo-sh/sudoo.sh/compare/v3.2.0...HEAD
[3.2.0]: https://github.com/Sudoo-sh/sudoo.sh/compare/v3.1.1...v3.2.0
[3.1.1]: https://github.com/Sudoo-sh/sudoo.sh/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/Sudoo-sh/sudoo.sh/compare/v3.0.1...v3.1.0
[3.0.1]: https://github.com/Sudoo-sh/sudoo.sh/compare/v3.0.0...v3.0.1
[3.0.0]: https://github.com/Sudoo-sh/sudoo.sh/compare/v1.0.0...v3.0.0
[1.0.0]: https://github.com/Sudoo-sh/sudoo.sh/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/Sudoo-sh/sudoo.sh/releases/tag/v0.1.0
