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

| Setting | Default | Meaning |
| :--- | :--- | :--- |
| `ADMIN_CONTACT_NAME` | Yousef M. Y. Al Sabbah | Shown to sync operators and visitors when they must contact the administrator |
| `ADMIN_CONTACT_PHONE`, `ADMIN_CONTACT_EMAIL` | empty | Added to that message when set |
| `ODOO_VERSIONS` | `18.0,19.0,20.0` | Versions offered when creating an instance |
| `ODOO_ADMIN_PASSWORD` | `admin` | Odoo password of the user `admin` in new instances |
| `IDLE_HOURS` | `6` | Pause an instance after this many hours without visits (`0` turns automatic pause off) |
| `DELETE_AFTER_DAYS` | `30` | Lifetime offered for new instances: deleted, after a backup, this many days after creation (`0` offers "keep") |
| `POLL_MINUTES` | `2` | How often GitHub is checked for new commits |
| `SESSION_IDLE_MINUTES` | `60` | Panel sign-out after this long without activity |
| `SESSION_MAX_HOURS` | `12` | Longest panel session |

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
docker exec oim-panel python3 manage.py urls
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
│   └── instance_manager/, wsgi.py, manage.py, Dockerfile, docker-compose.yml, .env.example
├── enterprise/<version>/                    Odoo Enterprise add-ons, one folder per version
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

The **Edition** field offers Enterprise for each version whose add-ons are in `/opt/odoo/enterprise/<version>`.
They are mounted read-only into every Enterprise instance of that version, after the instance's own modules
(so a custom module can replace an Enterprise one). `web_enterprise` is installed with the database.

**The Enterprise add-ons are not part of this project** and never go into its repository: they are Odoo's
proprietary code, available to Odoo partners and subscribers. Each server needs its own copy, in this layout:

```
/opt/odoo/enterprise/
├── 18.0/                       one folder per Odoo version, named exactly like the version tag
│   ├── web_enterprise/         required: the panel offers Enterprise only when
│   │   └── __manifest__.py     <version>/web_enterprise/__manifest__.py exists
│   ├── account_accountant/
│   ├── ...                     every Enterprise module directly inside the version folder
├── 19.0/
└── 20.0/
```

Rules:

| Rule | Right | Wrong |
| :--- | :--- | :--- |
| The folder is named like the Odoo version in `ODOO_VERSIONS` | `19.0` | `19`, `v19`, `odoo-19`, `enterprise-19.0` |
| Modules sit directly inside the version folder | `19.0/web_enterprise/` | `19.0/enterprise/web_enterprise/` |
| The add-ons match the version of the folder | Odoo 19 add-ons in `19.0/` | Odoo 18 add-ons in `19.0/` |
| Readable by the Odoo containers | `chmod -R a+rX /opt/odoo/enterprise/19.0` | files readable by root only |

Adding or updating a version, from a computer that has the add-ons folder (the folder that directly contains
`web_enterprise`, `account_accountant` and so on):

```bash
tar czf - -C "<folder with the add-ons>" . | ssh <user>@<server> \
  'mkdir -p /opt/odoo/enterprise/21.0 && tar xzf - -C /opt/odoo/enterprise/21.0 && chmod -R a+rX /opt/odoo/enterprise/21.0'
```

The option appears in the panel at once. After updating a version, restart its Enterprise instances and upgrade
modules where needed. Enterprise databases must be registered with an Odoo subscription or partner code, otherwise
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
docker exec oim-panel python3 manage.py list-users
docker exec oim-panel python3 manage.py create-user <name> admin|operator
docker exec oim-panel python3 manage.py reset-password <name>
```

New and reset passwords are printed once. Every other account task is done in the panel ([User manual](USER_MANUAL.md#8-accounts-and-passwords)).

---

## 9. Checking that everything works

After installing or updating:

1. `docker ps` shows `oim-panel`, `oim-connector-panel`, and for each instance its connector, Odoo and
   database (paused instances show only their connector).
2. `docker exec oim-panel python3 manage.py urls` prints the panel's link and every instance's link.
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
| The panel's link does not open | The link changed after a restart | `docker exec oim-panel python3 manage.py urls` |
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

Quick tunnels are free and need no account, but their addresses change. For addresses that never change, such as
`sales.<your-domain>`:

1. Buy a domain (about 10 USD a year) and add it to a free Cloudflare account.
2. Create one **named tunnel** in Cloudflare and give the server its token.
3. Route one host name per instance to that instance's gateway port.

Only the way connectors are started (`tunnels.py`) changes; the gateway, the instances and the panel stay as they
are. The comparison with Tailscale and other options is in [Tunneling](concepts/TUNNELING.md#7-other-ways-and-why-we-chose-this-one).
