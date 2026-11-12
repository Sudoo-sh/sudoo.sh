# Changelog

All notable changes to this project are recorded in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Renamed

- The project is now **Sudoo** (`sudo` + `Odoo`), the self-hosted counterpart of Odoo.sh: repository
  `sudoo.sh`, Python package and command `sudoo`, containers `sudoo-panel` and `sudoo-connector-<name>`,
  API tokens `sudoo_...`, signature headers `X-Sudoo-*`.
- New sudoo.sh logo (light and dark) and social preview in `docs/images/brand/`, shown at the top of the
  README; the remaining `odoo-sudoo` folder names in the README, CONTRIBUTING and the Administrator guide now say
  `sudoo.sh`.

### Added

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

- Dependencies are installed with uv from `uv.lock` instead of Alpine packages; the image is based on
  `python:3.12-alpine`.
- Settings are typed and checked at start-up (pydantic-settings): a wrong value stops the panel with a clear message.
- Code moved to a `src/` layout with subpackages (`core`, `instances`, `repos`, `exposure`, `addons`, `web`), type
  hints on every function, formatted with black.
- `ADMIN_CONTACT_NAME` defaults to "the administrator".

### Upgrading

Run `install.sh` as usual. It replaces the old `manager/` files, adds the new settings to `.env` with their
defaults and keeps your values. `ODOO_VERSIONS` keeps its current value; set it to `auto` to offer every version.
Use `sudoo` instead of `python3 manage.py` in commands.

## [0.1.0] - 2026-11-11

### Added

- Web panel that runs many independent Odoo 18, 19 and 20 instances (Community and Enterprise) on one Docker server.
- Instance creation from a GitHub repository and branch, with generated names, databases, ports and passwords.
- One public https link per instance through Cloudflare quick tunnels, with no open incoming port.
- GitHub sync every 2 minutes, upgrading only changed modules; submodule update, add and remove.
- Read-only deploy keys per private repository, stored encrypted with AES-256-GCM.
- Auto-pause after 6 hours without visits, and wake-up on the next visit.
- Instance lifetime with automatic Odoo-format backups before any deletion.
- Administrator and sync operator roles, sign-in throttling and an audit log.
- Server and per-instance resource monitoring.
- Documentation in `docs/`, also shown inside the panel.
- Contributor files: contributing guide, security policy, code of conduct, issue and pull request templates, continuous integration and Dependabot.

[Unreleased]: https://github.com/Yosef-AlSabbah/sudoo.sh/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Yosef-AlSabbah/sudoo.sh/releases/tag/v0.1.0
