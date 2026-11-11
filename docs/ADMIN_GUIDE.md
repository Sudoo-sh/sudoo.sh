# Administrator Guide

**Audience:** the person responsible for the server and the panel.
**Covers:** installing and updating, settings, links, files, Enterprise add-ons, backups, checks and
troubleshooting.
**You need:** SSH access to the server as root (or with `sudo`).

---

## Contents

1. [The server](#1-the-server)
2. [Installing and updating](#2-installing-and-updating)
3. [Settings](#3-settings)
4. [Public links](#4-public-links)
5. [Files and Docker objects on the server](#5-files-and-docker-objects-on-the-server)
6. [Odoo Enterprise add-ons](#6-odoo-enterprise-add-ons)
7. [Backups](#7-backups)
8. [Accounts from the command line](#8-accounts-from-the-command-line)
9. [Checking that everything works](#9-checking-that-everything-works)
10. [Troubleshooting](#10-troubleshooting)
11. [Moving to permanent links](#11-moving-to-permanent-links)

---

## 1. The server

| Need | Why |
| :--- | :--- |
| Linux with Docker Engine and the Docker Compose plugin | Every part runs in containers ([Docker](concepts/DOCKER.md)). |
| 2 GB of memory or more; about 1 to 1.5 GB per running instance | Odoo and PostgreSQL. Paused instances use none. |
| 10 GB of free disk or more | Odoo images (about 1.5 GB per version), databases, attachments. |
| Outgoing access to GitHub (443 and 22), Docker Hub and Cloudflare (443, 7844) | Code, images, public links. |
| **No incoming port** | Public access goes through connections the server opens itself ([Tunneling](concepts/TUNNELING.md)). Only SSH is needed, for administration. |

`check-server.sh` checks all of this and changes nothing:

```bash
cd ~/odoo-instance-manager
sudo ./check-server.sh
```

It prints one line per check, marked `[ OK ]`, `[WARN]` or `[FAIL]`, with a `Fix:` line saying what to do. If Docker Compose is missing on Ubuntu with
the `docker.io` package, install `docker-compose-v2`.

---

## 2. Installing and updating

Copy the kit to the server and run the installer:

```bash
scp -r odoo-instance-manager <user>@<server>:~
ssh <user>@<server>
cd ~/odoo-instance-manager
sudo ./install.sh
```

What `install.sh` does, in order:

1. Creates `/opt/odoo/{manager,instances,enterprise}` (mode 700, root only).
2. Replaces the panel's code in `/opt/odoo/manager` and copies the documentation next to it.
3. Writes `/opt/odoo/manager/.env` from `.env.example`, **keeping every value already set** on this server.
4. Builds the panel image and starts the container `oim-panel`, which starts the panel's connector.
5. Waits for the panel's public link and prints it.
6. The first time only: creates the `admin` and `sync` accounts and prints their passwords **once**. Store them in a
   password manager.

**Updating** is the same: copy the new kit and run `sudo ./install.sh` again. Instances, accounts, deploy keys, the vault
key and settings are kept. The panel restarts (a few seconds); running instances are not restarted. The panel's link may
change; instance links stay the same.

---

## 3. Settings

File: `/opt/odoo/manager/.env` (mode 600). Apply a change with:

```bash
cd /opt/odoo/manager && docker compose up -d
```

Every setting, with its type, default and meaning, is in [Configuration](CONFIGURATION.md); the template with
comments is `.env.example`. To see what the panel is actually using, and which plugins are installed:

```bash
docker exec oim-panel instance-manager config
```

A wrong value (for example `IDLE_HOURS=soon`) or an unknown plugin name stops the panel at start-up, and
`docker logs oim-panel` names the setting.

The two choices that shape a server are plugins ([Plugins](PLUGINS.md)):

| Setting | Options |
| :--- | :--- |
| `EXPOSURE` | `cloudflare-quick` (free tunnel per instance, no open port; default) or `host-ports` (open ports, fixed addresses) |
| `ENTERPRISE_SOURCE` | `folder` (default), `git` (a branch per version) or `none` |

Per instance, administrators change **Automation** on the instance's page (stored as `AUTO_UPDATE`,
`AUTO_UPGRADE`, `AUTO_PAUSE` in the instance's `.env`).

---

## 4. Public links

The panel and every instance have their own link, `https://<words>.trycloudflare.com`.

**The links change when the server restarts, or when a connector restarts.** The panel always shows the current
ones, and tells every Odoo its new address within about 15 seconds (so links inside Odoo, such as those in e-mails,
follow). Links you shared earlier, and webhook addresses registered at other services, must be updated by hand.

To see the current links on the server:

```bash
docker exec oim-panel instance-manager urls
```

Administrators can also reach the panel without Cloudflare, through SSH:

```bash
ssh -N -L 9000:localhost:9000 <user>@<server>
```

then open `http://localhost:9000`. Why the server needs tunnels at all is explained in
[Tunneling](concepts/TUNNELING.md).

---

## 5. Files and Docker objects on the server

```
/opt/odoo/                                   mode 700, root only
├── manager/
│   ├── .env                                 settings (mode 600)
│   ├── users.json                           accounts: user name, role, password HASH (mode 600)
│   ├── secret_key                           signs session cookies (mode 600)
│   ├── vault.key                            encrypts stored secrets, peppers password hashes (mode 600)
│   ├── audit.log                            audit log, one JSON line per event
│   ├── state.json                           last visit, paused, switched off, per instance
│   ├── keys/known_hosts                     GitHub's SSH host key, recorded on first use
│   ├── keys/deploy/<owner>__<repo>.key      deploy key, ENCRYPTED (mode 600)
│   ├── keys/deploy/<owner>__<repo>.pub      its public part (what is added on GitHub)
│   ├── keys/deploy/<owner>__<repo>.name     the repository name as written on GitHub
│   ├── documentation/                       README.md and docs/, shown in the panel
│   └── src/, pyproject.toml, uv.lock, Dockerfile, compose.yaml, .env.example   (the panel's code, from the kit)
├── enterprise/<version>/                    Enterprise add-ons (folder source, default ENTERPRISE_PATH)
├── enterprise-git/<version>/                Enterprise add-ons downloaded by the git source
├── backups/                                 Odoo-format backup zips (mode 600)
└── instances/<name>/
    ├── .env                                 version, edition, ports, database, automation (mode 600);
    │                                        MASTER_PASSWORD in it is ENCRYPTED
    ├── docker-compose.yml                   the instance's two containers
    ├── config/odoo.conf                     Odoo configuration; master password as a HASH only
    └── addons/                              the repository, with its submodules
```

| Docker object | What it is |
| :--- | :--- |
| `oim-panel` | The panel, the gateway and the background work |
| `oim-connector-panel` | The panel's Cloudflare connector |
| `oim-connector-<name>` | One connector per instance |
| `odoo-<name>-odoo-1`, `odoo-<name>-db-1` | The instance's Odoo and PostgreSQL |
| volumes `odoo-<name>_db-data`, `odoo-<name>_odoo-data` | The instance's database and its attachments |

Useful commands:

| Task | Command |
| :--- | :--- |
| Everything running | `docker ps` |
| Panel log | `docker logs -f oim-panel` |
| Odoo log of an instance | `cd /opt/odoo/instances/<name> && docker compose logs -f odoo` |
| Memory | `free -m` |
| Disk per container and volume | `docker system df -v` |

Do not run `docker compose up` or `down` for an instance by hand: use the panel, which also keeps its gateway,
connector and state in step.

---

## 6. Odoo Enterprise add-ons

**The Enterprise add-ons are not part of this project** and never go into its repository: they are Odoo's
proprietary code, available to Odoo partners and subscribers. Each server needs its own copy. Where the panel finds
it is chosen by the add-on source plugin (`ENTERPRISE_SOURCE`, see [Plugins](PLUGINS.md#4-add-on-sources-where-enterprise-comes-from)).

The **Edition** field offers Enterprise for each version the source can provide. The add-ons are mounted read-only
into every Enterprise instance of that version, after the instance's own modules (so a custom module can replace an
Enterprise one), and `web_enterprise` is installed with the database.

**Folder source** (default): a folder per version, wherever you keep them. `ENTERPRISE_PATH` names it, with
`{version}` (19.0) and `{major}` (19) replaced:

| `ENTERPRISE_PATH` | Folder for Odoo 19 |
| :--- | :--- |
| `/opt/odoo/enterprise/{version}` (default) | `/opt/odoo/enterprise/19.0` |
| `/data/odoo-enterprise-{major}` | `/data/odoo-enterprise-19` |

Each folder holds the modules directly:

```
/opt/odoo/enterprise/19.0/
├── web_enterprise/         required: Enterprise is offered only when this module (ENTERPRISE_MARKER) is there
├── account_accountant/
└── ...                     every Enterprise module directly inside, not in a sub-folder
```

| Rule | Right | Wrong |
| :--- | :--- | :--- |
| Modules sit directly inside the version's folder | `19.0/web_enterprise/` | `19.0/enterprise/web_enterprise/` |
| The add-ons match the version | Odoo 19 add-ons for 19.0 | Odoo 18 add-ons for 19.0 |
| Readable by the Odoo containers | `chmod -R a+rX <folder>` | files readable by root only |

**Git source**: set `ENTERPRISE_SOURCE=git`, `ENTERPRISE_GIT_URL` (a private repository you control, with one branch
per version) and `ENTERPRISE_GIT_BRANCH` (for example `{version}`). Give the server a deploy key for that repository
under **GitHub access**. The branch is downloaded when an Enterprise instance of that version is created, and
refreshed the next time.

Adding or updating a version, from a computer that has the add-ons folder (the folder that directly contains
`web_enterprise`, `account_accountant` and so on):

```bash
tar czf - -C "<folder with the add-ons>" . | ssh <user>@<server> \
  'mkdir -p /opt/odoo/enterprise/21.0 && tar xzf - -C /opt/odoo/enterprise/21.0 && chmod -R a+rX /opt/odoo/enterprise/21.0'
```

(for the default `ENTERPRISE_PATH`; use your own path otherwise.) The new version is offered as soon as the Odoo
image of that version is published (`ODOO_VERSIONS=auto`) or listed in `ODOO_VERSIONS`. After updating a version, restart its Enterprise instances and
upgrade modules where needed. Enterprise databases must be registered with an Odoo subscription or partner code, otherwise
Odoo shows its expiry notice after the trial period.

---

## 7. Backups

**Instances are backed up by the panel.** Before every deletion (automatic or by hand), and when an administrator
clicks **Back up now**, the panel writes one zip per database to `/opt/odoo/backups/`:

```
/opt/odoo/backups/<instance>__<database>__<YYYYMMDD-HHMMSS>.zip      mode 600
├── dump.sql          the database (pg_dump --no-owner)
├── filestore/        attachments and images
└── manifest.json     database name, Odoo version
```

This is Odoo's own backup format: restore it with **Restore Database** in any Odoo's database manager, or by hand
(`createdb`, `psql -f dump.sql`, copy `filestore/` into the new Odoo's `filestore/<database>`). The files are listed,
downloaded and deleted under **Backups** in the panel. They are kept until someone deletes them.

**Lifetime.** Each instance has `DELETE_AFTER_DAYS` in its `.env` (0: kept). Every 10 minutes the panel deletes
the instances whose lifetime is over, backup first. New instances get the value of `DELETE_AFTER_DAYS` in the panel's
`.env` (30) unless "Keep" is chosen; it can be changed on the instance's page.

**By hand**, for an instance (database and attachments):

```bash
NAME=sales-main; DB=sales_main
docker exec odoo-$NAME-db-1 pg_dump -U odoo -Fc $DB > $NAME.dump
docker run --rm -v odoo-${NAME}_odoo-data:/data -v "$PWD":/backup alpine tar czf /backup/$NAME-files.tgz -C /data .
```

**The panel**: copy `/opt/odoo/manager` except `instance_manager/` and `documentation/` (they come with the kit).

**The vault key** (`/opt/odoo/manager/vault.key`) deserves its own place:

* Store it **separately** from the other backups (for example in the team's password manager, as a file
  attachment). A backup that contains both the encrypted secrets and their key protects nothing.
* Without it, encrypted deploy keys and master passwords cannot be read: deploy keys must be created again and
  added on GitHub again, and the panel's account passwords must be reset (the key also peppers their hashes).

See [Security](SECURITY.md) for why it works this way.

---

## 8. Accounts from the command line

For when nobody can sign in, for example after losing the admin password:

```bash
docker exec oim-panel instance-manager list-users
docker exec oim-panel instance-manager create-user <name> admin|operator
docker exec oim-panel instance-manager reset-password <name>
```

New and reset passwords are printed once. Every other account task is done in the panel ([User manual](USER_MANUAL.md#8-accounts-and-passwords)).

---

## 9. Checking that everything works

After installing or updating:

1. `docker ps` shows `oim-panel`, `oim-connector-panel`, and for each instance its connector, Odoo and
   database (paused instances show only their connector).
2. `docker exec oim-panel instance-manager urls` prints the panel's link and every instance's link.
3. Open the panel's link and sign in.
4. **Documentation** in the top bar opens these pages.
5. Create a small test instance (no repository, name `check`, Odoo 20, Community). It should be ready in about 2
   minutes; open its link and sign in with `admin` / `admin`.
6. On its page: **Restart**, then **Pause** (Actions tab), then open its link: "Starting up" appears and Odoo opens
   by itself.
7. Delete it (Actions menu, **Delete instance**). `docker ps` no longer lists it.
8. The panel log shows no errors: `docker logs oim-panel 2>&1 | grep -i error`.

---

## 10. Troubleshooting

| Symptom | Likely cause | What to do |
| :--- | :--- | :--- |
| The panel's link does not open | The link changed after a restart | `docker exec oim-panel instance-manager urls` |
| No link at all | `oim-panel` or the connector is not running | `docker ps -a`, `docker logs oim-panel`, then `cd /opt/odoo/manager && docker compose up -d` |
| "Starting up" for several minutes | Odoo fails to start | **View logs** on the instance's page |
| "No access" under Repository access | No deploy key, or not added on GitHub | Create the key, add it on GitHub, **Check again** ([User manual](USER_MANUAL.md#7-github-access-private-repositories)) |
| Update fails: "branch ... no longer exists on GitHub" (or "no such ref was fetched"); the instance page warns about it | The branch was deleted on GitHub, usually after its pull request was merged | Actions, **Switch branch**, choose the branch to follow now (for example `main`) |
| Update fails: "went in different directions" | Something was changed directly in the instance's `addons` folder | A developer resets that folder to the GitHub branch |
| Module changes not visible | The module was not upgraded | **Upgrade modules**, or turn on **Upgrade changed modules** |
| "Too many failed attempts" | Sign-in lockout (15 minutes) | Wait, or reset the password |
| Instance "Stopped" | Odoo crashed, often out of memory | **Restart**; pause other instances; `free -m` |
| "Cannot decrypt the value" in a log | `vault.key` was replaced or lost | Restore `vault.key` from its backup; otherwise recreate deploy keys and reset passwords |
| Deletion failed: "the database did not start, so no backup could be taken" | The instance's database container is broken | Nothing was deleted. Check `docker logs odoo-<name>-db-1`; to delete anyway, fix or remove the database by hand |
| JSON-2 API: 401 with a valid key | Odoo 20 API key without an expiry date | Create the key with an expiry date ([API](API.md)) |
| Requests over 100 MB, or over 100 seconds, fail | Cloudflare quick tunnel limits | Split uploads; run long operations in the background |

---

## 11. Moving to permanent links

Quick tunnels are free and need no account, but their addresses change. For addresses that never change:

| Option | How |
| :--- | :--- |
| The server accepts incoming connections | `EXPOSURE=host-ports`, `PUBLIC_HOST=<name or address>`, open the ports. Built in. |
| A domain behind Cloudflare | Buy a domain (about 10 USD a year), create a Cloudflare named tunnel, and write a small exposure plugin that routes `<instance>.<domain>` to each gateway port ([Plugins](PLUGINS.md#5-writing-a-plugin)). |
| A reverse proxy you already run | An exposure plugin like the example in [Plugins](PLUGINS.md#5-writing-a-plugin). |

The gateway, the instances and the panel stay as they are; only the exposure plugin changes. The comparison with
Tailscale and other options is in [Tunneling](concepts/TUNNELING.md#7-other-ways-and-why-we-chose-this-one).
