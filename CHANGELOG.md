# Changelog

All notable changes to this project are recorded in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Renamed

- The project is now **Sudoo** (`sudo` + `Odoo`), the self-hosted counterpart of Odoo.sh: repository
  `sudoo.sh`, Python package and command `sudoo`, containers `sudoo-panel` and `sudoo-connector-<name>`,
  API tokens `sudoo_...`, signature headers `X-Sudoo-*`.

### Added

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
