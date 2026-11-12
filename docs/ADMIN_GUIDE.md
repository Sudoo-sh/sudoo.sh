# Administrator Guide

**Audience:** the person responsible for the server and the panel.
**Covers:** installing and updating, the first account, settings, e-mail, links, files and containers, Enterprise
add-ons, backups, daily backups and restores, the command line, checks and troubleshooting.
**You need:** SSH access to the server as root (or with `sudo`).

---

## Contents

1. [The server](#1-the-server)
2. [Installing](#2-installing)
3. [The first account](#3-the-first-account)
4. [Updating](#4-updating)
5. [Settings](#5-settings)
6. [E-mail](#6-e-mail)
7. [Public links](#7-public-links)
8. [Containers, files and folders on the server](#8-containers-files-and-folders-on-the-server)
9. [Odoo Enterprise add-ons](#9-odoo-enterprise-add-ons)
10. [Backups](#10-backups)
11. [Accounts from the command line](#11-accounts-from-the-command-line)
12. [Checking that everything works](#12-checking-that-everything-works)
13. [Troubleshooting](#13-troubleshooting)
14. [Moving to permanent links](#14-moving-to-permanent-links)

---

## 1. The server

| Need | Why |
| :--- | :--- |
| Linux, Ubuntu or Debian recommended | Every part runs in Docker containers ([Docker](concepts/DOCKER.md)). Docker Engine and the Compose plugin are installed by the installer when missing. |
| 2 GB of memory or more; about 1 to 1.5 GB per running instance | Odoo and PostgreSQL. Paused instances use none. |
| 20 GB of free disk recommended (the installer stops below 5 GB) | Odoo images (about 1.5 GB per version), databases, attachments, backups. |
| Outgoing access to GitHub (443 and 22), Docker Hub and Cloudflare (443, 7844) | Code, images, public links. |
| **No incoming port** | Public access goes through connections the server opens itself ([Tunneling](concepts/TUNNELING.md)). Only SSH is needed, for administration. |

`check-server.sh` (in the kit, see below) checks all of this and changes nothing:

```bash
sudo /opt/sudoo.sh-kit/check-server.sh
```

It prints one line per check, marked `[ OK ]`, `[WARN]` or `[FAIL]`, with a `Fix:` line saying what to do. If Docker
Compose is missing on Ubuntu with the `docker.io` package, install `docker-compose-v2`.

---

## 2. Installing

On a fresh server, one command installs everything:

```bash
curl -fsSL https://raw.githubusercontent.com/Yosef-AlSabbah/sudoo.sh/main/get.sh | sudo bash
```

What `get.sh` does, in order:

1. **Docker.** If Docker or its Compose plugin is missing, installs them with Docker's official installer
   (`get.docker.com`) and starts Docker.
2. **Checking the server.** Prints the memory and the free disk, and stops when less than 5 GB of disk is free.
3. **Downloading.** Downloads the latest release of Sudoo.sh into `/opt/sudoo.sh-kit` (replacing an earlier copy).
   Choose another version with `SUDOO_VERSION`:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/Yosef-AlSabbah/sudoo.sh/main/get.sh | sudo SUDOO_VERSION=v1.0.0 bash
   curl -fsSL https://raw.githubusercontent.com/Yosef-AlSabbah/sudoo.sh/main/get.sh | sudo SUDOO_VERSION=main bash
   ```

4. **Installing.** Runs `install.sh` from the downloaded kit.

What `install.sh` does, in order:

1. Creates `/opt/odoo/{manager,instances,enterprise}` (root only).
2. Replaces the panel's code in `/opt/odoo/manager` (`src/`, `web/` and the build files) and copies the
   documentation to `/opt/odoo/manager/documentation/`.
3. Writes `/opt/odoo/manager/.env` from `.env.example`: new settings are added with their defaults, and **every
   value already set on this server is kept**.
4. Builds the two images and starts the containers `sudoo-panel` (the backend) and `sudoo-web` (the web app, on
   the panel's port).
5. Waits for the panel's public address and prints it.
6. On a new server, prints the **setup code** for the first account ([section 3](#3-the-first-account)). On a server
   that already has accounts, it says that accounts were not changed.

You can also install from a copy of the kit (a release archive or a clone of the repository) without `get.sh`:

```bash
scp -r sudoo.sh <user>@<server>:~
ssh <user>@<server>
cd ~/sudoo.sh
sudo ./install.sh
```

`install.sh` expects Docker with the Compose plugin to be installed already.

---

## 3. The first account

A new server has **no accounts and no default passwords**. The installer prints a setup code, for example:

```
Open the panel's address above and create your account with this setup code:

    ABCD-EFGH-JKLM
```

Open the panel's address in a browser. The **Set up Sudoo.sh** page asks for the setup code, first name, last name,
e-mail (required), phone (optional) and a password twice (12 to 128 characters, following the
[password rules](USER_MANUAL.md#password-rules)). There is no user name field: the user name is made from the
e-mail address (`yousef.sabbah@example.com` becomes `yousef.sabbah`, then `-2`, `-3` when taken) and stays the
account's internal key. It creates the first account, a **Super admin**, and signs it in. Everyone signs in with
their e-mail address; the user name works too. The other accounts are then created on the **Accounts** page
([User manual](USER_MANUAL.md#8-accounts-and-roles)).

| Good to know | |
| :--- | :--- |
| Show the code again | `docker exec sudoo-panel sudoo.sh setup-code` |
| The code works once | After the first account exists, the setup page is gone and the command says the panel is already set up. |
| Wrong codes | 5 wrong codes within 15 minutes block the address they came from for 15 minutes. |
| Second factor | By default super admins and administrators must set up a second factor right after signing in. Without e-mail set up, only the authenticator app is offered, so set up e-mail first ([section 6](#6-e-mail)) if you want e-mail codes. |

Keep the setup code private until the first account exists: whoever uses it first becomes super admin.

---

## 4. Updating

Run the same command again:

```bash
curl -fsSL https://raw.githubusercontent.com/Yosef-AlSabbah/sudoo.sh/main/get.sh | sudo bash
```

or copy a new kit to the server and run `sudo ./install.sh` in it.

| Kept | Replaced or restarted |
| :--- | :--- |
| Instances, their databases, files and links | The panel's code, web app and documentation |
| Accounts, API tokens, audit log (`sudoo.db`) | `sudoo-panel` and `sudoo-web` are rebuilt and restarted (a few seconds) |
| Deploy keys, the vault key, the secret key | New settings are added to `.env` with their defaults |
| Settings in `.env` and `settings.json` | |
| The connector containers, so every public link stays the same | |

Running instances are not restarted. Accounts of earlier versions are renamed once: "admin" accounts become
**Super admin**, "sync operator" accounts become **Developer**.

Read `CHANGELOG.md` in the kit before updating to a new major version.

---

## 5. Settings

Settings come from two places:

| Where | What |
| :--- | :--- |
| `/opt/odoo/manager/.env` (mode 600) | The base value of every setting, written by the installer. |
| `/opt/odoo/manager/settings.json` | The changes made on the panel's **Settings** page, applied on top of `.env`. Passwords in it are encrypted with the vault key. |

A super admin changes most settings in the browser (**Settings** in the sidebar; [User manual](USER_MANUAL.md#11-settings)).
Every setting belongs to one of three classes:

| Class | Settings | How a change applies |
| :--- | :--- | :--- |
| **Now** | Almost everything: second factor requirement, session times, e-mail (including the mail password), limits, defaults for new instances, daily backups (`DAILY_BACKUPS`, `DAILY_BACKUP_TIME`, `TIMEZONE`), Odoo versions and images, Enterprise paths, update checks, webhook secret, contact, accent colour, `METRICS_INTERVAL_SECONDS`. | At once, when saved on the Settings page. |
| **Restart** | The settings that choose plugins: `MAILER`, `SECOND_FACTORS`, `UPDATE_TRIGGERS`, `ENTERPRISE_SOURCE`, `AUTH`, `RATE_LIMITER`. | After **Restart the panel** (Settings, Plugins). The restart stops only the backend for a few seconds and is refused while jobs run. |
| **.env only** | `ODOO_ROOT`, `DOCS_DIR`, `MEMORY_DIR`, `EXPOSURE`, `STORAGE`, `PUBLIC_HOST`, `PUBLIC_SCHEME`, all ports (`PANEL_PORT`, `API_PORT`, `EVENTS_PORT`, `GATEWAY_PORT_START`, `ODOO_PORT_START`, connector ports), `CONNECTOR_PREFIX`. | Edit `.env`, then `cd /opt/odoo/manager && docker compose up -d`. The Settings page lists them under **Server**, read only. |

A value changed on the Settings page wins over `.env`. **Use .env** on that page removes the change and brings back
the `.env` value. Editing `.env` by hand therefore has no effect on a setting that was changed in the browser until
someone clicks **Use .env**.

Every setting, with its type, default and meaning, is in [Configuration](CONFIGURATION.md); the template with
comments is `.env.example`. To see what the panel is actually using, and which plugins are installed:

```bash
docker exec sudoo-panel sudoo.sh config
```

A wrong value in `.env` (for example `IDLE_HOURS=soon`) or an unknown plugin name stops the panel at start-up, and
`docker logs sudoo-panel` names the setting. The Settings page checks every value before saving it.

The two choices that shape a server are plugins ([Plugins](PLUGINS.md)):

| Setting | Options |
| :--- | :--- |
| `EXPOSURE` | `cloudflare-quick` (free tunnel per instance, no open port; default) or `host-ports` (open ports, fixed addresses) |
| `ENTERPRISE_SOURCE` | `folder` (default), `git` (a branch per version) or `none` |

Per instance, the **Settings** tab of the instance's page changes automation and lifetime (stored as
`AUTO_UPDATE`, `AUTO_UPGRADE`, `AUTO_PAUSE`, `DELETE_AFTER_DAYS` in the instance's `.env`).

---

## 6. E-mail

The panel sends e-mail for sign-in codes and test messages. Without e-mail the panel works, but the **E-mail code**
second factor is not offered. E-mails are HTML in the style of Odoo's own, with a plain-text part; the code in them
can be selected with one tap, and Gmail's apps add their own **Copy code** button.

### Gmail, step by step

1. On the Google account that will send the e-mails, turn on **2-Step Verification** (myaccount.google.com,
   Security). Google offers app passwords only with it.
2. Open **myaccount.google.com/apppasswords**, create an app password named `Sudoo.sh` and copy the 16 letters
   Google shows once.
3. In the panel, as a super admin, open **Settings, E-mail** and fill in:

   | Field | Value |
   | :--- | :--- |
   | Mail server | `smtp.gmail.com` |
   | Port | `587` |
   | Encryption | STARTTLS |
   | User name | the Gmail address |
   | Password | the app password (not the account's normal password) |
   | Sender | `Sudoo.sh <your.address@gmail.com>` |

4. Click **Save**, then **Send a test** and enter an address. The test e-mail should arrive within a minute (look in
   the spam folder too).

The same in `/opt/odoo/manager/.env` instead of the browser:

```
MAILER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=your.address@gmail.com
SMTP_PASSWORD=the16letterapppassword
MAIL_FROM=Sudoo.sh <your.address@gmail.com>
```

Apply and test:

```bash
cd /opt/odoo/manager && docker compose up -d
docker exec sudoo-panel sudoo.sh test-email you@example.com
```

Gmail sends from the signed-in address (or an alias added under Gmail's **Send mail as**), at most about 500
messages a day for a personal account. Other mail servers are described in
[Configuration: Gmail](CONFIGURATION.md#gmail) and the sections after it.

---

## 7. Public links

The panel and every instance have their own link, `https://<words>.trycloudflare.com` (with the default
`EXPOSURE=cloudflare-quick`).

**These Cloudflare quick tunnel links change when the server restarts, or when a connector container restarts.**
The panel always shows the current ones, and tells every Odoo its new address within about 15 seconds (so links
inside Odoo, such as those in e-mails, follow). Links you shared earlier, and webhook addresses registered at other
services, must be updated by hand.

Updating Sudoo.sh does not touch the connector containers, so the links stay the same through an update.

To see the current links on the server:

```bash
docker exec sudoo-panel sudoo.sh urls
```

Administrators can also reach the panel without Cloudflare, through SSH (9000 is the default `PANEL_PORT`):

```bash
ssh -N -L 9000:localhost:9000 <user>@<server>
```

then open `http://localhost:9000`. Why the server needs tunnels at all is explained in
[Tunneling](concepts/TUNNELING.md).

---

## 8. Containers, files and folders on the server

### Containers

| Container | What it is |
| :--- | :--- |
| `sudoo-web` | The web app (React, served by Caddy) on `PANEL_PORT` (9000). Forwards `/api`, `/hooks` and `/docs` to the backend and `/api/v1/events` to the live events. |
| `sudoo-panel` | The backend: the API on `127.0.0.1:API_PORT` (9002), live events on `127.0.0.1:EVENTS_PORT` (9001), the gateways in front of the instances, the monitor and the jobs. |
| `sudoo-connector-panel` | The panel's Cloudflare connector. |
| `sudoo-connector-<name>` | One connector per instance. |
| `odoo-<name>-odoo-1`, `odoo-<name>-db-1` | The instance's Odoo and PostgreSQL. |
| volumes `odoo-<name>_db-data`, `odoo-<name>_odoo-data` | The instance's database and its attachments (filestore). |

Do not restart or recreate the connector containers by hand: their links would change.

### Files and folders

```
/opt/sudoo.sh-kit/                           the downloaded kit (get.sh); install.sh and check-server.sh are here
/opt/odoo/                                   root only
├── manager/
│   ├── .env                                 settings (mode 600)
│   ├── settings.json                        settings changed on the Settings page, on top of .env;
│   │                                        passwords in it are ENCRYPTED
│   ├── sudoo.db                             SQLite: accounts (password HASHES), second factors, API tokens,
│   │                                        audit log, instance state, creation times (mode 600)
│   ├── vault.key                            encrypts stored secrets, peppers password hashes (mode 600)
│   ├── secret_key                           signs session cookies (mode 600)
│   ├── keys/known_hosts                     GitHub's SSH host key, recorded on first use
│   ├── keys/deploy/<owner>__<repo>.key      deploy key, ENCRYPTED (mode 600)
│   ├── keys/deploy/<owner>__<repo>.pub      its public part (what is added on GitHub)
│   ├── keys/deploy/<owner>__<repo>.name     the repository name as written on GitHub
│   ├── documentation/                       README.md and docs/, shown under Help in the panel
│   ├── src/, pyproject.toml, uv.lock        the backend's code (from the kit)
│   ├── web/                                 the web app's code and Caddyfile (from the kit)
│   └── Dockerfile, compose.yaml, .env.example, README.md, LICENSE.md
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

### Useful commands

| Task | Command |
| :--- | :--- |
| Everything running | `docker ps` |
| Backend log | `docker logs -f sudoo-panel` |
| Web app log | `docker logs -f sudoo-web` |
| Odoo log of an instance | `cd /opt/odoo/instances/<name> && docker compose logs -f odoo` |
| Current links | `docker exec sudoo-panel sudoo.sh urls` |
| Effective settings | `docker exec sudoo-panel sudoo.sh config` |
| Memory | `free -m` |
| Disk per container and volume | `docker system df -v` |

Do not run `docker compose up` or `down` for an instance by hand: use the panel, which also keeps its gateway,
connector and state in step.

---

## 9. Odoo Enterprise add-ons

**The Enterprise add-ons are not part of this project** and never go into its repository: they are Odoo's
proprietary code, available to Odoo partners and subscribers. Each server needs its own copy. Where the panel finds
it is chosen by the add-on source plugin (`ENTERPRISE_SOURCE`, see [Plugins](PLUGINS.md#4-add-on-sources-where-enterprise-comes-from)).
The paths and the git address can be changed on the Settings page (**Enterprise add-ons**); changing the source
itself needs **Restart the panel**.

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
image of that version is published (`ODOO_VERSIONS=auto`) or listed in `ODOO_VERSIONS`. After updating a version,
restart its Enterprise instances and upgrade modules where needed. Enterprise databases must be registered with an
Odoo subscription or partner code, otherwise Odoo shows its expiry notice after the trial period.

---

## 10. Backups

### Instances

**Instances are backed up by the panel.** Every backup writes one zip per database to `/opt/odoo/backups/`
(`BACKUPS_DIR`):

```
/opt/odoo/backups/<instance>__<database>__<YYYYMMDD-HHMMSS>__<kind>.zip      mode 600
├── dump.sql          the database (pg_dump --no-owner)
├── filestore/        attachments and images
└── manifest.json     database name, Odoo version
```

The time in the name is when the backup started. Files from earlier versions have no `__<kind>`
part and count as `manual`.

| Kind | Taken | Kept |
| :--- | :--- | :--- |
| `manual` | **Back up now** on an instance's page. | Until someone deletes it. |
| `daily` | Once a day, for each instance used since its last daily backup (below). | Only the latest per instance; the new one replaces the previous. Removed when the instance is deleted. |
| `deletion` | Before every deletion, automatic or by hand. If it fails, the instance is not deleted. | Until someone deletes it. |
| `before-restore` | At the start of every restore: the state just before it. | Only the latest per instance. Removed when the instance is deleted. |

This is Odoo's own backup format, with both the database and the filestore: restore it with **Restore Database**
in any Odoo's database manager of the same version, or by hand (`createdb`, `psql -f dump.sql`, copy `filestore/`
into the new Odoo's `filestore/<database>`). The files are listed (with their kind), downloaded and deleted under
**Backups** in the panel; an instance's own backups are also on its **Overview** tab. Copy the ones you want to keep
off the server.

**Daily backups.** Every minute the panel checks whether today's backup time has passed (`DAILY_BACKUP_TIME`,
default `00:00`, in `TIMEZONE`, default `UTC`). When it has, it backs up each instance that was **used** since its
last daily backup: opened through its link, or started or restarted. The panel records the first use after each
daily backup (`used_since` in the instance state) and clears it when the daily backup starts.

* An instance nobody used gets no new daily backup, so its last one stays.
* A running instance keeps running during the backup; a paused one stays paused (only its database container is
  started for the backup, then stopped again).
* An instance with a job running is tried again a minute later. A daily backup that fails waits for the next day.
* If the panel was down at the backup time, the backups run as soon as it is back.
* `DAILY_BACKUPS=false` turns them off. All three settings are on the Settings page under **Backups** and apply at
  once. `TIMEZONE` takes a name of the time zone database, such as `UTC` or `Africa/Cairo` (the panel image includes
  the time zone data).

**Restoring.** Roles with the `instances.restore` permission (maintainer, administrator, super admin) restore an
instance from its **Overview** tab: **Restore the latest** (the newest `manual`, `daily` or `deletion` backup of a
database the instance has) or **Restore** on any backup in the list. The job "Restore a backup":

1. saves the current state as a `before-restore` backup, so the restore can be undone,
2. stops Odoo,
3. drops the database, creates it again and loads `dump.sql`, replaces the filestore with the backup's
   `filestore/`, and sets the instance's current public address in the database again,
4. starts Odoo and waits until it answers.

It takes about a minute or two, depending on the size. The instance is running afterwards. A backup can only be
restored into the instance it was taken from, and only when that instance still has the database.

**Disk use.** Backups by hand and before deletions add up until someone deletes them. Daily and before-restore
backups stay bounded: at most one of each per instance. Plan for about two zips per instance in use on top of the
ones kept by hand, and check `du -sh /opt/odoo/backups` from time to time; the server's disk use is also on the
Instances page.

**Lifetime.** Each instance has `DELETE_AFTER_DAYS` in its `.env` (0: kept). Every 10 minutes the panel deletes the
instances whose lifetime is over, backup first; if the backup fails, the instance is not deleted. New instances get
the default lifetime (30 days; Settings, New instances) unless "Keep until deleted by hand" is chosen; it can be
changed on the instance's Settings tab.

**By hand**, for an instance (database and attachments):

```bash
NAME=sales-main; DB=sales_main
docker exec odoo-$NAME-db-1 pg_dump -U odoo -Fc $DB > $NAME.dump
docker run --rm -v odoo-${NAME}_odoo-data:/data -v "$PWD":/backup alpine tar czf /backup/$NAME-files.tgz -C /data .
```

### The panel itself

Back up `/opt/odoo/manager`. The parts that cannot be recreated:

| File | Holds | Without it |
| :--- | :--- | :--- |
| `sudoo.db` | Accounts, second factors, API tokens, audit log, instance state | Every account and token must be created again. |
| `vault.key` | The key of every encrypted secret | Encrypted deploy keys, master passwords and passwords in `settings.json` cannot be read; account passwords must be reset. |
| `settings.json` | Settings changed in the browser | The `.env` values apply. |
| `.env` | The base settings | `install.sh` writes the defaults. |
| `secret_key` | Signs session cookies | A new one is made; everyone signs in again. |
| `keys/` | Deploy keys (encrypted) | Deploy keys must be created and added on GitHub again. |

`src/`, `web/` and `documentation/` come with the kit and need no backup.

**The vault key** deserves its own place:

* Store it **separately** from the other backups (for example in the team's password manager, as a file
  attachment). A backup that contains both the encrypted secrets and their key protects nothing.
* Without it, encrypted deploy keys and master passwords cannot be read: deploy keys must be created again and
  added on GitHub again, and the panel's account passwords must be reset (the key also peppers their hashes).

See [Security](SECURITY.md) for why it works this way.

---

## 11. Accounts from the command line

For the first account, and for when nobody can sign in. Every command runs inside the panel's container:

| Command | What it does |
| :--- | :--- |
| `docker exec sudoo-panel sudoo.sh setup-code` | Shows the setup code for the first account, or says the panel is already set up. |
| `docker exec sudoo-panel sudoo.sh create-user NAME ROLE` | Creates an account and prints its password once. `ROLE` is `super_admin`, `admin`, `maintainer`, `developer` or `viewer`. The account has no e-mail address, so it signs in with the user name `NAME`; add an e-mail address on the Accounts page (or in **My account**) to sign in with it. |
| `docker exec sudoo-panel sudoo.sh reset-password NAME` | Sets a new password, prints it once and signs the account out everywhere. The person chooses their own at the next sign-in. |
| `docker exec sudoo-panel sudoo.sh list-users` | Lists the accounts with their role and last sign-in. |
| `docker exec sudoo-panel sudoo.sh create-token NAME SCOPE` | Creates an API token (`SCOPE` is `read` or `write`) and prints the token and signing secret once. |
| `docker exec sudoo-panel sudoo.sh test-email ADDRESS` | Sends a test e-mail with the current mail settings, and says what went wrong if it cannot. |
| `docker exec sudoo-panel sudoo.sh urls` | Prints the panel's and every instance's current link. |
| `docker exec sudoo-panel sudoo.sh config` | Prints the effective settings (secrets hidden) and the installed plugins. |

`sudoo` works as a shorter name for `sudoo.sh`. Commands that change something are recorded in the audit log as
"command line".
Every other account task is done in the panel ([User manual](USER_MANUAL.md#8-accounts-and-roles)): new accounts
need an e-mail address that no other account uses; the password is generated or typed by the administrator
(checked against the [password rules](USER_MANUAL.md#password-rules)); super admins, including your own account,
can be edited; and the last super admin cannot be demoted or deleted.

A person who is locked out by a lost second factor does not need the command line: an administrator uses **Remove
second factors** on the Accounts page. If no administrator can sign in, create a new super admin with
`create-user NAME super_admin`.

---

## 12. Checking that everything works

After installing or updating:

1. `docker ps` shows `sudoo-web`, `sudoo-panel`, `sudoo-connector-panel`, and for each instance its connector, Odoo
   and database (paused and switched-off instances show only their connector).
2. `docker exec sudoo-panel sudoo.sh urls` prints the panel's link and every instance's link.
3. Open the panel's link and sign in (on a new server: the setup page, [section 3](#3-the-first-account)).
4. **Help** in the sidebar opens these pages.
5. As a super admin, **Settings, E-mail, Send a test** delivers a test e-mail (when e-mail is set up).
6. Create a small test instance (no repository, name `check`, the newest version, Community). It should be ready in
   about 2 minutes; open its link and sign in with the login on its **Access** tab.
7. On its page: **Restart**, then **Pause** (menu), then open its link: "Starting up" appears and Odoo opens by
   itself.
8. Delete it (menu, **Delete**). A backup appears under **Backups**, and `docker ps` no longer lists the instance.
9. The logs show no errors: `docker logs sudoo-panel 2>&1 | grep -i error` and `docker logs sudoo-web`.

---

## 13. Troubleshooting

| Symptom | Likely cause | What to do |
| :--- | :--- | :--- |
| The panel's link does not open | The link changed after a restart | `docker exec sudoo-panel sudoo.sh urls` |
| No link at all | `sudoo-panel` or the connector is not running | `docker ps -a`, `docker logs sudoo-panel`, then `cd /opt/odoo/manager && docker compose up -d` |
| The link opens but shows an error, or nothing | `sudoo-web` is not running or not starting | `docker logs sudoo-web`, then `cd /opt/odoo/manager && docker compose up -d --build` |
| `sudoo-panel` keeps restarting | A wrong value in `.env`, or an unknown plugin name | `docker logs sudoo-panel` names the setting; fix it in `.env` and run `docker compose up -d` in `/opt/odoo/manager` |
| The setup code was lost | It is only printed once by the installer | `docker exec sudoo-panel sudoo.sh setup-code` |
| The setup page refuses even the right code | 5 wrong setup codes within 15 minutes blocked the address | Wait 15 minutes; check the code with `sudoo.sh setup-code` |
| Nobody can sign in (password lost, last super admin locked out) | | `sudoo.sh reset-password NAME`, or `sudoo.sh create-user NAME super_admin` ([section 11](#11-accounts-from-the-command-line)) |
| Lost phone or mailbox with the second factor | | An administrator uses **Remove second factors** on the Accounts page |
| "Too many failed attempts" at sign-in | Sign-in lockout (15 minutes) | Wait, or reset the password |
| "Wrong e-mail address or password" for an account created with `create-user` | The account has no e-mail address | Sign in with the user name, then add the e-mail address in **My account** |
| No daily backup of an instance | Nobody used it since its last daily backup, `DAILY_BACKUPS` is off, or the backup time has not come yet in `TIMEZONE` | Check **Settings, Backups**; the instance's **Activity** tab shows "Daily backup" jobs and why one failed |
| **Restore the latest** is greyed out | The instance has no `manual`, `daily` or `deletion` backup of a database it has | Click **Back up now** to take one; older backups of another instance cannot be restored here |
| A restore failed | The backup file is damaged, or the database did not start | The **Activity** tab shows the step and the reason. The state before the restore is kept as a `before-restore` backup: restore it to go back |
| E-mail codes or the test e-mail do not arrive | Wrong mail settings, or Gmail refuses the normal password | **Send a test** (or `sudoo.sh test-email ADDRESS`) shows the reason. For Gmail, `SMTP_PASSWORD` must be an app password and 2-Step Verification must be on ([section 6](#6-e-mail)). Look in the spam folder. |
| "E-mail code" is not offered | No mail server set, or `email` not in `SECOND_FACTORS` | Set up e-mail ([section 6](#6-e-mail)); after changing `SECOND_FACTORS`, **Restart the panel** |
| A plugin setting changed in the browser has no effect | Plugin settings apply after a restart | Settings, Plugins, **Restart the panel** (refused while jobs run: wait for them) |
| A value changed in `.env` has no effect | The same setting was changed on the Settings page, which wins | Click **Use .env** next to it on the Settings page |
| "Starting up" for several minutes | Odoo fails to start | The instance's **Odoo log** tab |
| An access report line is red on New instance | No deploy key, or not added on GitHub | Create the key, add it on GitHub ([User manual](USER_MANUAL.md#7-github-access-private-repositories)) |
| Update fails: "branch ... no longer exists on GitHub" (or "no such ref was fetched"); the instance page warns about it | The branch was deleted on GitHub, usually after its pull request was merged | Menu, **Switch branch**, choose the branch to follow now (for example `main`) |
| Update fails: "went in different directions" | Something was changed directly in the instance's `addons` folder | A developer resets that folder to the GitHub branch |
| Module changes not visible | The module was not upgraded | **Upgrade modules**, or turn on **Upgrade changed modules** |
| Instance "Stopped" | Odoo crashed, often out of memory | **Start**; pause other instances; `free -m` |
| "Cannot decrypt the value" in a log | `vault.key` was replaced or lost | Restore `vault.key` from its backup; otherwise recreate deploy keys, set the passwords on the Settings page again and reset account passwords |
| Deletion failed: "the database did not start, so no backup could be taken" | The instance's database container is broken | Nothing was deleted. Check `docker logs odoo-<name>-db-1`; to delete anyway, fix or remove the database by hand |
| JSON-2 API: 401 with a valid key | Odoo 20 API key without an expiry date | Create the key with an expiry date ([API](API.md)) |
| Requests over 100 MB, or over 100 seconds, fail | Cloudflare quick tunnel limits | Split uploads; run long operations in the background |

---

## 14. Moving to permanent links

Quick tunnels are free and need no account, but their addresses change. For addresses that never change:

| Option | How |
| :--- | :--- |
| The server accepts incoming connections | `EXPOSURE=host-ports`, `PUBLIC_HOST=<name or address>`, open the ports. Built in. |
| A domain behind Cloudflare | Buy a domain (about 10 USD a year), create a Cloudflare named tunnel, and write a small exposure plugin that routes `<instance>.<domain>` to each gateway port ([Plugins](PLUGINS.md#6-writing-a-plugin)). |
| A reverse proxy you already run | An exposure plugin like the example in [Plugins](PLUGINS.md#6-writing-a-plugin). |

`EXPOSURE` and `PUBLIC_HOST` are `.env`-only settings: change them in `/opt/odoo/manager/.env` and run
`cd /opt/odoo/manager && docker compose up -d`. The gateway, the instances and the panel stay as they are; only the
exposure plugin changes. The comparison with Tailscale and other options is in
[Tunneling](concepts/TUNNELING.md#7-other-ways-and-why-we-chose-this-one).
