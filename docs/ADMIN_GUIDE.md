# Administrator Guide

**Audience:** the person responsible for the server and the panel.
**Covers:** installing, updating, rolling back and uninstalling, the first account, settings, e-mail, links, files and containers, Enterprise
add-ons, backups, daily backups and restores, the command line, checks and troubleshooting, the GitHub App, custom
domains and certificates, off-site copies of backups.
**You need:** SSH access to the server as root (or with `sudo`).

---

## Contents

1. [The server](#1-the-server)
2. [Installing](#2-installing)
3. [The first account](#3-the-first-account)
4. [Updating, rolling back, uninstalling](#4-updating-rolling-back-uninstalling)
5. [Settings](#5-settings)
6. [E-mail](#6-e-mail)
7. [Public addresses](#7-public-addresses)
8. [Containers, files and folders on the server](#8-containers-files-and-folders-on-the-server)
9. [Odoo Enterprise add-ons](#9-odoo-enterprise-add-ons)
10. [Backups](#10-backups)
11. [Accounts from the command line](#11-accounts-from-the-command-line)
12. [Checking that everything works](#12-checking-that-everything-works)
13. [Troubleshooting](#13-troubleshooting)
14. [Changing how the server is reached](#14-changing-how-the-server-is-reached)
15. [The GitHub App](#15-the-github-app)
16. [Alerts](#16-alerts)
17. [E-mail templates](#17-e-mail-templates)
18. [Sign-in e-mails and recovery codes](#18-sign-in-e-mails-and-recovery-codes)
19. [Custom domains and certificates](#19-custom-domains-and-certificates)
20. [Off-site copies](#20-off-site-copies)
21. [Web shell](#21-web-shell)
22. [The licence agreement](#22-the-licence-agreement)
23. [Mail catcher](#23-mail-catcher)
24. [Projects and branches](#24-projects-and-branches)
25. [Database upgrades](#25-database-upgrades)
26. [Support access](#26-support-access)
27. [Servers](#27-servers)
28. [Code profiler](#28-code-profiler)
29. [Audit log](#29-audit-log)

---

## 1. The server

| Need | Why |
| :--- | :--- |
| Ubuntu 22.04+ or Debian 12+ (others may work, with a warning), amd64 or arm64, booted with systemd | Every part runs in Docker containers ([Docker](concepts/DOCKER.md)); the images exist for both architectures. |
| 2 CPU cores, 2 GB of memory or more (4 GB recommended); about 0.7 to 1.5 GB per running instance | Odoo and PostgreSQL. Paused instances use none; [how many instances a server runs](#how-many-instances-can-my-server-run). |
| 20 GB of free disk on `/opt` and on Docker's folder (`/var/lib/docker`) | Odoo images (about 1.5 GB per version), databases, attachments, backups. |
| Docker Engine 24+ with the Compose v2 plugin (the installer installs them after asking) | The panel and every instance are Compose projects. |
| Outgoing HTTPS to github.com, ghcr.io and registry-1.docker.io (443); GitHub SSH (22) for private repositories | Releases and code, the Sudoo.sh images, the Odoo images. |
| **Incoming TCP 80 and 443** (the default, `EXPOSURE=direct`) | The server answers the panel's and the instances' addresses itself, with certificates from Let's Encrypt; the certificate authority checks port 80 ([Public addresses](concepts/PUBLIC_ADDRESSES.md)). Nothing else on the server may use these ports. Plus SSH for administration. |
| Or: behind your own reverse proxy (`proxy`) | The proxy handles https and forwards to the panel's and the gateways' local ports; no port of the server is opened to the internet. |
| Or: no incoming port at all (`tunnel`) | For a server that cannot accept connections: public access goes through connections the server opens itself, which needs outgoing TCP and UDP 7844. Addresses change when a connector restarts. |

The installer checks all of this before it changes anything. To only check (changes nothing):

```bash
curl -fsSL https://github.com/Sudoo-sh/sudoo.sh/releases/latest/download/sudoo-install.sh | sudo bash -s -- check
```

Each line is marked ✓ (fine), ! (a warning) or ✗ (must be fixed), and every problem has a `Fix:` line;
[section 13](#installer-checks) lists them all.

### How many instances can my server run?

Memory decides. A running instance (Odoo and its database) keeps its memory while it runs; when the server runs
short, it starts swapping or stops processes, and every instance on it slows down at once. A busy processor only
makes instances take turns. Paused and switched-off instances use no memory and no processor, only disk.

The panel works the number out from what it measures (the dashboard shows it on the **Running** card, and
`GET /api/v1/server/capacity` returns it, see the [Panel API](PANEL_API.md#9-metrics)):

1. **The server's memory**, minus a **reserve** for the operating system, Docker and the file cache: 1 GB or 10 %
   of the memory, whichever is larger (`CAPACITY_RESERVE_MB`, `CAPACITY_RESERVE_PERCENT`).
2. Minus **the panel itself**: its backend, its web service and the Cloudflare connectors, at the most they used
   in the last hour. The dashboard shows it on the **Panel** card, now and at its highest; usually 150 to 400 MB.
3. Divided by **the memory of one running instance**: the most each running instance used in the last hour,
   averaged over the instances. Until one has run for a few measurements, the panel counts 700 MB
   (`CAPACITY_INSTANCE_MEMORY_MB`), what a lightly used Odoo with its database takes. The API also gives the figure
   per Odoo version.
4. Rounded down. If the processor is the tighter limit (instances that keep it busy all the time), the lower
   number is used: instances may use together up to `CAPACITY_WARNING_PERCENT` (85 %) of it.

The result is how many instances can run **at the same time, comfortably**. You can have many more instances: the
ones nobody uses pause (Settings, **Pause after, hours without visits**) and start again when someone opens them.

| | 8 GB server | 16 GB server |
| :--- | ---: | ---: |
| Memory | 8,192 MB | 16,384 MB |
| Reserve (1 GB or 10 %, the larger) | 1,024 MB | 1,638 MB |
| The panel (an example) | 300 MB | 300 MB |
| Left for instances | 6,868 MB | 14,446 MB |
| Instances at once, nothing measured yet (700 MB each) | **9** | **20** |
| Instances at once, measured 1.1 GB each (busier instances) | **6** | **12** |

**Disk.** Paused instances keep their database and files. The **Disk** card says how many more instances of the
average size fit on the free disk (for example 120 GB free and 1.5 GB per instance: about 80 more). Backups and the
Odoo images (about 1.5 GB per version) take disk too.

**Warnings.** When as many instances run as the server takes comfortably, **New instance** and **Start** say so:
"This server can run about 9 instances at once; 9 are running. Starting another may slow every instance." They do
not stop you. The dashboard warns when the server's memory use reaches `CAPACITY_WARNING_PERCENT` (85 %), or when
more instances run than the estimate. To refuse creating and starting instances beyond a number, set
`MAX_RUNNING_INSTANCES` (Settings, **Instances** tab, **Capacity**); a visitor opening a paused instance still starts it.

The figures cover the last hour and start again when the panel restarts, so right after a restart the panel counts
the default until it has measured again.

### Resources and priority

**No limits by default.** An instance has no processor or memory cap and Odoo runs with its own defaults
(threaded mode, no `workers`). It may use the whole server when nothing else needs it.

**Production first under load.** Each instance's Odoo and database containers carry its stage's weights:

| | Production | Staging | Development |
| :--- | ---: | ---: | ---: |
| Processor weight (Docker `cpu_shares`; Docker's default is 1024) | 4096 | 2048 | 1024 |
| Memory reservation, Odoo / database (Docker `mem_reservation`) | 1 GB / 512 MB | none | none |
| Out-of-memory score (`oom_score_adj`) | 0 | 300 | 600 |

What each one does:

1. **Processor weight.** When the processor is fully busy, the kernel shares it in proportion to the weights: a
   production instance gets four times the share of a development one, twice that of a staging one. A heavy import
   on a development branch then slows the development branch, not the client's live system.
2. **Memory reservation.** A soft limit: when the server runs short of memory, the kernel takes memory back from
   containers above their reservation first, so production keeps up to its reservation longest. It reserves
   nothing in advance and caps nothing.
3. **Out-of-memory score.** When memory runs out completely, the kernel stops the process with the highest score
   first: development first, then staging, production last. Docker restarts the stopped container. The panel
   never switches the out-of-memory killer off (`-1000` is refused).

Be honest with yourself about what this does: **the weights only act under contention.** On a quiet server they
change nothing. They do not make a slow instance faster, they do not stop a production instance from using all the
memory itself, and they cannot help if the server simply has too little memory: then add memory, pause instances,
or set limits.

The weights are settings (**Settings**, **Resources**, or `PRIORITY_*` in `.env`, see
[Configuration](CONFIGURATION.md#resources-and-priority)). An instance gets its stage's weights when it next starts;
after moving an instance to another stage, its **Settings** tab says when a restart is needed.

**Limits, when you want them.** On **Settings**, **Resources**, each stage has default limits, all off: Odoo
workers and cron workers, memory per worker (`limit_memory_soft`, `limit_memory_hard`), time per request
(`limit_time_cpu`, `limit_time_real`: the HTTP timeout), the scheduled actions timeout (`limit_time_real_cron`),
the database's connections (PostgreSQL `max_connections`, from which the panel shares out Odoo's `db_maxconn`
between its processes), and the processor and memory of the Odoo and database containers. They stay off, so
existing instances do not change until someone sets a value. Each layer below overrides the one before:

1. the stage's default (administrators, **Settings**, **Resources**);
2. the project's value for that stage (project **Settings**, **Resources**, permission `projects.resources`);
3. the branch's own value (the branch's **Branch settings** tab, the same permission);
4. the instance's own value (administrators, permission `instances.resources`, its **Settings** tab, **Resources**).

Project owners stay within the **maxima** (the last column of the table, `RESOURCES_MAX_*`): a higher value is
refused, and one set before a maximum was lowered is lowered to it. Administrators' stage defaults and an instance's
own limits are not bound by them. The permission `projects.resources` belongs to administrators and super admins by
default, and to a project's **admin** level in its access settings.

**Computed container limits.** With workers set by a project or a branch and no Odoo container limit set anywhere,
the container gets limits computed from them (`RESOURCES_COMPUTED_LIMITS`): memory `RESOURCES_BASE_MEMORY_MB` + (workers + cron workers) ×
memory per worker before recycling, and max(1, workers) cores. With 2 workers, 1 cron worker and 640 MB per
worker that is 2.4 GB and 2 cores. A value typed anywhere wins. Workers an administrator sets as a stage default or
for one instance get no implicit cap, as before.

**The reservation check.** Every instance counts on the server with, for each container, its memory limit, else
its stage's memory reservation; staging and development count `CAPACITY_OVERCOMMIT` of it (half by default, they
pause when unused), production fully. A change of resources that raises this total above the memory the server can
spare for instances (see Capacity above) is refused with the figures; lowering is always allowed. The project's
**Resources** section shows the line "This server: 9.1 GB of 14 GB reserved after this change" while you type.
`RESOURCES_RESERVATION_CHECK=false` turns the refusal off.

The pages show what applies, where it comes from, and warn (without refusing) when:

- the memory reserved by all instances is more than the server has, or than it can spare for instances;
- the Odoo container's memory limit looks too small for its workers;
- there are more workers than Odoo suggests for the processor (cores × 2 + 1).

**Apply** writes `odoo.conf` and the compose file and restarts Odoo (its container is recreated when its limits
change; the data stays). On a production instance it asks for confirmation, takes a backup first, and puts the
previous limits back if Odoo does not answer with the new ones. A paused instance gets them when it next starts.

**Workers.** With workers, Odoo serves WebSockets (live chat, notifications) on a second port, its gevent server
(8072 in the container). The panel reserves it the first time (`ODOO_GEVENT_PORT` in the instance's `.env`),
publishes it on `127.0.0.1` only, and the gateway sends WebSocket traffic there, and the long-polling bus of Odoo 15
and earlier (`/longpolling/`) too. A rule of thumb: one worker for about six people using Odoo at the same time.

**Applying from a project.** Saving a stage of a project (or a branch) applies it at once to the instances it
changes: a job "Apply resources" each, as above (production: confirmation, backup, the previous values put back if
Odoo does not answer). An instance with a job running, or on a locked branch you may not change, keeps its
containers and shows "Restart pending" until it next starts.

**Storage.** The monitor measures each running instance's database (`pg_database_size`) and files (its filestore)
every `STORAGE_MEASURE_MINUTES` (an hour) and keeps the result, so a paused instance still shows its size. Each
stage has a soft quota (`STORAGE_QUOTA_GB_*`: 20 GB production, 10 GB staging, 5 GB development): from
`STORAGE_WARNING_PERCENT` (80 %) an instance is shown as near its quota and an alert is sent, over it a second alert
(production: again every day). Nothing is refused or stopped. The sizes show on the instance's **Settings** tab and
on the project's **Settings**, **Resources** (production's database size first, as on Odoo.sh).

**Upgrading from 2.x.** `ODOO_MEMORY_LIMIT`, `ODOO_CPU_LIMIT`, `DB_MEMORY_LIMIT` and `DB_CPU_LIMIT` are no longer
read, so the 2 GB / 2 cores of the old template no longer cap anything. A value you had changed on the Settings page
becomes the default of every stage. Instances get the new rules at their next start or restart.

---

## 2. Installing

On a fresh server, one command does everything:

```bash
curl -fsSL https://github.com/Sudoo-sh/sudoo.sh/releases/latest/download/sudoo-install.sh | sudo bash
```

`sudoo-install.sh` is one file attached to every release; nothing else is downloaded except the release's images. To
read it before running it:

```bash
curl -fsSLO https://github.com/Sudoo-sh/sudoo.sh/releases/latest/download/sudoo-install.sh
curl -fsSLO https://github.com/Sudoo-sh/sudoo.sh/releases/latest/download/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS      # must print: sudoo-install.sh: OK
less sudoo-install.sh
sudo bash sudoo-install.sh
```

With [cosign](https://docs.sigstore.dev/cosign/system_config/installation/), also check who built it: the release
workflow signs it ([Security](SECURITY.md#13-releases-and-signatures)).

What it does, in order:

0. **Asks you to accept the licence agreement**: it prints a short summary of the
   [Sudoo.sh Licence Agreement](https://sudoo.sh/legal/license) and the address of the exact version, and you type `accept`. Without a
   terminal it stops unless `--accept-licence` is given ([section 22](#the-installer)). Nothing is checked or
   installed before.
1. **Asks how people should reach this server** (a fresh installation only): **Direct** (pre-selected and
   recommended: the server answers on ports 80 and 443 itself), **Proxy** (your own reverse proxy or load balancer
   in front) or **Tunnel** (no open port). Before it accepts Direct it checks that ports 80 and 443 are free on the
   server, that the server has a public address of its own and that a host firewall (ufw, firewalld) lets them in
   (whether they are open from the internet cannot be seen from the server itself; the panel checks port 80 again
   before each certificate); it explains what it found and suggests Proxy or Tunnel when something is not right.
   Direct and Proxy then ask for the panel's name (`PANEL_DOMAIN`). The answer is written to `.env` as `EXPOSURE`;
   an update never asks again and never changes it.
2. **Checks the server** (read only): root, operating system, architecture, systemd, processor, memory, disk, clock,
   the panel's ports, the tools it needs, internet access (port 7844 only for a tunnel), Docker. When a check fails it
   stops, with the fix, before changing anything.
3. **Installs what is missing, after asking**: curl, openssl, ca-certificates, Docker Engine (from Docker's
   repository, or the distribution's packages when Docker has none for this release), the Compose and buildx
   plugins, starting Docker. Each question is answered **[y]es**, **[n]o** or **[a]ll** (yes to every later question).
   Without a terminal it stops and prints the command to run again with `--yes` (also `--allow-all`).
4. **Downloads the images** `ghcr.io/sudoo-sh/sudoo-panel` and `ghcr.io/sudoo-sh/sudoo-web`, pinned by digest in the
   installer, and checks their signatures when `cosign` is installed ([Security](SECURITY.md#13-releases-and-signatures)).
5. **Writes the files**: `/opt/odoo/{manager,instances,enterprise,gateway,mail}` (root only), `/opt/odoo/manager/.env`
   from the release's settings template (values already there are kept), and `/opt/odoo/manager/compose.yaml`.
6. **Starts** `sudoo-panel`, `sudoo-gateway`, `sudoo-mail` and `sudoo-web` and waits until all are healthy.
7. Prints the panel's address and, on a new server, the **setup code** for the first account ([section 3](#3-the-first-account)).

It keeps a copy of itself as `/opt/odoo/manager/installer/sudoo-install.sh`, on the path as **`sudoo-install`**.

| Option | Meaning |
| :--- | :--- |
| `--version 3.1.1` | Install that release instead of the latest. `edge` follows `main` ([section 4](#following-main-edge)). |
| `--yes`, `--allow-all` | Answer yes to every question (for scripts). |
| `--accept-licence` | Accept the licence agreement without the prompt (for scripts). `--yes` does not accept it. |
| `--dry-run` | Show what would be done; change nothing. |
| `--docker-source official` or `distro` | Where a missing Docker comes from. |
| `--force` | Continue although a check failed (not recommended). |
| `--exposure direct`, `proxy` or `tunnel` | How people reach the server, without the question (for scripts). Without a terminal and without this option, a fresh installation takes `direct`; a busy port 80 or 443 then fails the check before anything is changed. `--domain NAME` sets the panel's name (`PANEL_DOMAIN`). |

Settings you want from the start (another `PANEL_PORT`, `PANEL_DOMAIN=panel.example.com`, `EXPOSURE=proxy`...) can be
written to `/opt/odoo/manager/.env` before installing; the installer keeps them (an `EXPOSURE` there is not asked
again) and adds the rest.

### Manual installation

Without the installer, on a server with Docker Engine 24+ and the Compose plugin:

```bash
sudo install -d -m 700 /opt/odoo /opt/odoo/manager /opt/odoo/instances /opt/odoo/enterprise /opt/odoo/gateway /opt/odoo/mail
cd /opt/odoo/manager
sudo curl -fsSLO https://github.com/Sudoo-sh/sudoo.sh/releases/latest/download/compose.yaml
sudo curl -fsSL -o .env https://github.com/Sudoo-sh/sudoo.sh/releases/latest/download/env.example
sudo chmod 600 .env compose.yaml          # edit .env now if needed (CONFIGURATION.md): EXPOSURE=direct is the
                                          # template's choice; set PANEL_DOMAIN, or EXPOSURE=proxy or tunnel
sudo docker compose pull
sudo docker compose up -d
sudo docker exec sudoo-panel sudoo.sh urls
sudo docker exec sudoo-panel sudoo.sh setup-code
```

The release's `compose.yaml` names both images by digest (`sudoo-panel`, `sudoo-gateway` and `sudoo-mail` run the
panel's image). `SHA256SUMS` of the same release covers `compose.yaml` and
`env.example`.

### From source

For developers, from a clone of the repository: the README's "From source" section builds the two images with
`docker compose build` and starts them with the same container names, folders and settings.

---

## 3. The first account

A new server has **no accounts and no default passwords**. The installer prints a setup code, for example:

```
Open the panel's address above and create your account with this setup code:

    ABCD-EFGH-JKLM
```

Open the panel's address in a browser. The first step shows the **licence agreement**: its summary, the full text
and a link to the published version. Choose how you will use Sudoo.sh (**Personal projects**, **My company** or
**Service provider**), tick **I have read and accept**, and for a company also **I accept it on behalf of my
company, and I am authorised to bind it**, then **Accept and continue** ([section 22](#22-the-licence-agreement)).
The **Set up Sudoo.sh** page then asks for the setup code, first name, last name,
e-mail (required), phone (optional) and a password twice (12 to 128 characters, following the
[password rules](USER_MANUAL.md#password-rules)). There is no user name field: the user name is made from the
e-mail address (`jane.doe@example.com` becomes `jane.doe`, then `-2`, `-3` when taken) and stays the
account's internal key. It creates the first account, a **Super admin**, and signs it in. Everyone signs in with
their e-mail address; the user name works too. The other accounts are then created on the **Accounts** page
([User manual](USER_MANUAL.md#9-accounts-and-roles)).

| Good to know | |
| :--- | :--- |
| Show the code again | `docker exec sudoo-panel sudoo.sh setup-code` |
| The code works once | After the first account exists, the setup page is gone and the command says the panel is already set up. |
| Wrong codes | 5 wrong codes within 15 minutes block the address they came from for 15 minutes. |
| Second factor | By default super admins and administrators must set up a second factor right after signing in. Without e-mail set up, only the authenticator app is offered, so set up e-mail first ([section 6](#6-e-mail)) if you want e-mail codes. |

Keep the setup code private until the first account exists: whoever uses it first becomes super admin.

---

## 4. Updating, rolling back, uninstalling

```bash
sudo sudoo-install update
```

(or, on a server without the `sudoo-install` command, the install command with `-s -- update`). It runs the checks,
downloads the latest release's installer and checks it against that release's `SHA256SUMS` and, when `cosign` is
installed, its signature by that release's workflow (a missing or failing signature stops the update), then:

1. backs up `/opt/odoo/manager` to `/opt/odoo/manager-backup-<date>.tgz`;
2. keeps the running images under a name, for a rollback;
3. adds the new settings to `.env` with their defaults (every value already set is kept) and writes the new
   `compose.yaml`. The one exception: an `ODOO_ADMIN_PASSWORD` still set to the well-known `admin` is replaced by a
   random value (the old `.env` is in the backup of step 1). New instances get the new password; existing instances
   keep theirs until it is changed in the panel, and a password set on the Settings page still applies;
4. recreates **only the panel's own containers** (`sudoo-panel`, `sudoo-gateway`, `sudoo-mail` and `sudoo-web`; an
   update from a release without the gateway service or the mail catcher adds them), waits until they are healthy, and
   checks that every instance and connector container is still the same container with the same start time.

| Kept | Replaced or restarted |
| :--- | :--- |
| Instances, their databases, files and links | `sudoo-panel`, `sudoo-gateway`, `sudoo-mail` and `sudoo-web` (a few seconds) |
| The connector containers (tunnel), so every public address stays the same, and `EXPOSURE` | `compose.yaml`; new settings in `.env` |
| Accounts, API tokens, audit log (`sudoo.db`), deploy keys, vault key, secret key | |
| Settings in `.env` and `settings.json` | |
| Caught e-mails (`/opt/odoo/mail`) | |

Running instances are not restarted. From version 3.0 on, the instances' gateway ports belong to their own container,
`sudoo-gateway`, so the instances keep answering while `sudoo-panel` is recreated; while `sudoo-gateway` itself is
recreated, the panel serves them ([section 8](#what-keeps-running-when-the-panel-is-down)). Read the release notes before updating to a new major version. A specific
version: `sudo sudoo-install update --version 1.2.0`.

Earlier versions wrote the value of a secret setting changed on the Settings page (mail password, webhook secret,
default Odoo admin password) into the audit log. The first start after updating replaces those values with
`(set)`. When administrators or `write` API tokens could read the audit log, change the mail password and the
webhook secret after updating.

### Rolling back

```bash
sudo sudoo-install update --rollback
```

Puts back the images and `compose.yaml` of the version that ran before the last update (the `.env` stays). Running it
again switches forward. The backup of `/opt/odoo/manager` taken by the update is there if data must go back too.
Going back to a version without the mail catcher (or the gateway service) removes `sudoo-mail` (or `sudoo-gateway`);
the caught e-mails stay in `/opt/odoo/mail` and are shown again after the next update.

### Following main (edge)

Every commit on `main` that passes the checks publishes images tagged `edge` and `sha-<commit>`. A test server can
follow them:

```bash
sudo sudoo-install update --version edge
```

The images are pinned to their digest at that moment, with the `compose.yaml` and `.env.example` of the same commit.
Later `sudo sudoo-install update` runs keep following `edge`; `--version 1.2.0` goes back to releases.

### Servers installed with the earlier install.sh

Servers installed by the earlier `get.sh` / `install.sh` (images built on the server, the kit in
`/opt/sudoo.sh-kit`) update with the same command. The containers keep their names, `/opt/odoo` and `.env` stay, the
connectors and instances are not touched. The source files in `/opt/odoo/manager` and `/opt/sudoo.sh-kit` are removed
afterwards (they are in the backup), and the images built on the server are kept for `update --rollback`.

### Updating by hand

For a [manual installation](#manual-installation): download the new release's `compose.yaml` into
`/opt/odoo/manager`, add the new keys of its `env.example` to `.env`, then
`sudo docker compose pull && sudo docker compose up -d` there (from a release without the mail catcher, first
`sudo install -d -m 700 /opt/odoo/mail`). `docker compose` only recreates `sudoo-panel`, `sudoo-gateway`, `sudoo-mail`
and `sudoo-web`; never use `--remove-orphans` or restart the connectors.

### Uninstalling

```bash
sudo sudoo-install uninstall
```

Removes `sudoo-panel`, `sudoo-gateway`, `sudoo-mail`, `sudoo-web`, their images, the tunnel connectors, if any (the public addresses stop working) and the
`sudoo-install` command, and stops the instances. `/opt/odoo` (settings, accounts, keys, backups, caught e-mails) and
the instances' databases and files are kept; installing again brings them back, with new links.

`sudo sudoo-install uninstall --purge` also deletes every instance with its volumes and all of `/opt/odoo`. It asks you
to type `DELETE` (`--yes` does not answer it). Docker stays installed.

---

## 5. Settings

Settings come from two places:

| Where | What |
| :--- | :--- |
| `/opt/odoo/manager/.env` (mode 600) | The base value of every setting, written by the installer. |
| `/opt/odoo/manager/settings.json` | The changes made on the panel's **Settings** page, applied on top of `.env`. Passwords in it are encrypted with the vault key. |

A super admin changes most settings in the browser (**Settings** in the sidebar, grouped in tabs: General, Instances,
Odoo, Backups, Sign-in and security, E-mail, Alerts, GitHub and updates, Marketplace, Advanced; [User manual](USER_MANUAL.md#12-settings)).
A tab can be linked directly, for example `/settings?tab=email`.
Every setting belongs to one of three classes:

| Class | Settings | How a change applies |
| :--- | :--- | :--- |
| **Now** | Almost everything: second factor requirement, session times, e-mail (including the mail password), limits, defaults for new instances, daily backups (`DAILY_BACKUPS`, `DAILY_BACKUP_TIME`, `TIMEZONE`), `SAFE_UPDATES`, Odoo versions and images, instance containers (limits, `DRIFT_ACTION`), Enterprise paths, update checks, webhook secret, contact, accent colour, `METRICS_INTERVAL_SECONDS`. | At once, when saved on the Settings page. Container limits reach an instance when it next starts or restarts. |
| **Restart** | The settings that choose plugins: `MAILER`, `SECOND_FACTORS`, `UPDATE_TRIGGERS`, `ENTERPRISE_SOURCE`, `AUTH`, `RATE_LIMITER`. | After **Restart the panel** (Settings, Advanced, or the notice above the tabs while a restart waits). The restart stops only the backend for a few seconds and is refused while jobs run. |
| **.env only** | `ODOO_ROOT`, `DOCS_DIR`, `MEMORY_DIR`, `EXPOSURE`, `STORAGE`, `PUBLIC_HOST`, `PUBLIC_SCHEME`, all ports (`PANEL_PORT`, `API_PORT`, `EVENTS_PORT`, `GATEWAY_PORT_START`, `ODOO_PORT_START`, connector ports), `CONNECTOR_PREFIX`. | Edit `.env`, then `cd /opt/odoo/manager && docker compose up -d`. The Settings page lists them under **Advanced**, **Server**, read only. |

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
| `EXPOSURE` | `direct` (the default of new installations: the server answers on ports 80 and 443 with automatic certificates), `proxy` (behind your own reverse proxy) or `tunnel` (no open port; addresses change when a connector restarts); the earlier `cloudflare-quick` (= `tunnel`) and `host-ports` still work ([section 14](#14-changing-how-the-server-is-reached)) |
| `ENTERPRISE_SOURCE` | `folder` (default), `git` (a branch per version) or `none` |

Per instance, the **Settings** tab of the instance's page changes the stage, automation and lifetime (stored as
`STAGE`, `AUTO_UPDATE`, `AUTO_UPGRADE`, `AUTO_PAUSE`, `DELETE_AFTER_DAYS` in the instance's `.env`).

### Language of the web app

The web app is in English and Arabic (right to left). `DEFAULT_LANGUAGE` (`en` or `ar`; **Settings, General**,
applies at once) is the language of every account that has not chosen one; each person can choose their own on
**My account** or with the switch at the bottom of the sidebar, and the choice is kept on the account, so it follows
them to every browser. Before signing in, the sign-in and setup pages use the browser's language (or a `sudoo_lang`
cookie, which the other Sudoo.sh sites set), and the default when the browser asks for neither. Messages written by
the server (problem details, job logs, Odoo's logs) and the e-mails the panel sends stay in English.

### Instance stages

Each instance is in a stage: `production`, `staging` or `development` (`STAGE` in its `.env`; an instance without
it, such as one created before stages existed, is `development` and behaves exactly as before). What the stage
changes for users is in the [User manual](USER_MANUAL.md#stages-production-staging-and-development); for the server:

| | Development | Staging | Production |
| :--- | :--- | :--- | :--- |
| Automatic pause (`IDLE_HOURS`) | when the instance's switch is on | when the instance's switch is on | never |
| Automatic deletion | after its lifetime (`DELETE_AFTER_DAYS` offered) | after its lifetime (`STAGING_DELETE_AFTER_DAYS` offered) | never; a lifetime is refused |
| Daily backup | `DAILY_BACKUPS`, when used | `DAILY_BACKUPS` and `STAGING_DAILY_BACKUPS`, when used | every day, used or not, whatever `DAILY_BACKUPS` says |
| Backup before an update, rollback when it fails | `SAFE_UPDATES` | `SAFE_UPDATES` | always |
| New code automatically, for new instances | `AUTO_UPDATE` | `AUTO_UPDATE` | `PRODUCTION_AUTO_UPDATE` (off by default) |

`MAX_PRODUCTION_INSTANCES` (1 by default; 0: no limit) caps the instances in production at the same time, those
being created as production included: creating one more, or moving one more into production, is refused. Every
setting above is on the Settings page (**Instances**, **Stages**; `STAGING_DAILY_BACKUPS` under **Backups**) and
in [Configuration](CONFIGURATION.md#stages). Changes of stage are in the audit log ("instance stage changed").

Plan the disk for production: its daily backup is taken every day, but each instance keeps only its latest daily
backup, so it needs room for about one more copy of its database and files.

---

## 6. E-mail

The panel sends e-mail for sign-in codes, sign-in notices, alerts, plugin notices and test messages. Without e-mail
the panel works, but the **E-mail code** second factor is not offered and nothing else is sent. Their texts can be
changed on the [E-mail templates](#17-e-mail-templates) page. E-mails are HTML in the style of Odoo's own, with a plain-text part; the code in them
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

## 7. Public addresses

The panel and every instance have their own public https address. How they get it is `EXPOSURE`
([Public addresses](concepts/PUBLIC_ADDRESSES.md)):

| `EXPOSURE` | The panel | An instance |
| :--- | :--- | :--- |
| `direct` (default of new installations) | `https://<PANEL_DOMAIN>`, else the server's public name, else its managed name from Sudoo.sh (`panel.<company>.<base>`, a linked panel), else `https://<server address>` with the panel's own certificate (browsers warn once) | its primary custom domain, else its managed name (`<branch>.<stage>.<company>.<base>`), else `https://<server address>:<gateway port>` |
| `proxy` | `https://<PANEL_DOMAIN or PUBLIC_HOST>`, served by your proxy | its custom domain, else `https://<PUBLIC_HOST>:<gateway port>` |
| `tunnel` | `https://<words>.trycloudflare.com` | the same, one per instance |

**Firewall with `direct`:** open TCP 80 and 443 to the internet (at the hosting provider too). The certificate
authority checks port 80; everything else is https on 443. Open the gateway ports (from `GATEWAY_PORT_START`, 8069)
only if instances without a name must be reachable. With `proxy`, open nothing: only the proxy reaches the local
ports. With `tunnel`, allow outgoing TCP and UDP 7844.

**Setting the panel's name with `direct`:** create an A (and AAAA) record for a name such as `panel.example.com`
pointing at the server, set **Panel's name** (`PANEL_DOMAIN`) on **Settings, Addresses and domains**, and the panel
gets its certificate within a minute or two (the page shows its state). Until then, and without a name, it answers
on the server's address with its own certificate. A panel linked to a Sudoo.sh account needs no name of its own:
it gets one from Sudoo.sh ([The panel's own managed name](#the-panels-own-managed-name)).

**With `tunnel`, addresses change when the server restarts, or when a connector container restarts.** The panel
always shows the current ones and tells every Odoo its new address within about 15 seconds (so links inside Odoo,
such as those in e-mails, follow). The GitHub App's webhook address follows too, within two minutes
([section 15](#15-the-github-app)). Addresses shared earlier, and webhook addresses registered at other services
(such as a per-repository `github-webhook`), must be updated by hand. Direct and proxy addresses do not change.

Updating Sudoo.sh never changes `EXPOSURE` and does not touch the connector containers, so the addresses stay the
same through an update.

To see the current addresses on the server:

```bash
docker exec sudoo-panel sudoo.sh urls
```

Administrators can also reach the panel without its public address, through SSH (9000 is the default `PANEL_PORT`):

```bash
ssh -N -L 9000:localhost:9000 <user>@<server>
```

then open `http://localhost:9000`.

---

## 8. Containers, files and folders on the server

### Containers

| Container | What it is |
| :--- | :--- |
| `sudoo-web` | The web app (React, served by Caddy) on `PANEL_PORT` (9000). Forwards `/api`, `/hooks` and `/docs` to the backend and `/api/v1/events` to the live events. |
| `sudoo-panel` | The backend: the API on `127.0.0.1:API_PORT` (9002), live events on `127.0.0.1:EVENTS_PORT` (9001), the monitor and the jobs; it starts, stops and changes the instances through the server's Docker. |
| `sudoo-gateway` | The gateways in front of the instances: every instance's `GATEWAY_PORT`, where its connector points. Same image as `sudoo-panel`, without Docker access or the panel's data ([Gateway](concepts/GATEWAY.md#5-where-it-runs-its-own-container)). |
| `sudoo-mail` | The mail catcher: the e-mail of staging and development instances ends here, never at real addresses ([Mail catcher](#23-mail-catcher)). Same image as `sudoo-panel`, without Docker access, `.env` or a published port. |
| `sudoo-connector-panel` | The panel's Cloudflare connector. |
| `sudoo-connector-<name>` | One connector per instance. |
| `odoo-<name>-odoo-1`, `odoo-<name>-db-1` | The instance's Odoo and PostgreSQL. |
| volumes `odoo-<name>_db-data`, `odoo-<name>_odoo-data` | The instance's database and its attachments (filestore). |

Do not restart or recreate the connector containers by hand: their links would change.

### Files and folders

```
/opt/odoo/                                   root only
├── manager/
│   ├── .env                                 settings (mode 600)
│   ├── compose.yaml                         sudoo-panel, sudoo-gateway, sudoo-mail and sudoo-web, images pinned (written by
│   │                                        the installer)
│   ├── installer/                           sudoo-install.sh (the sudoo-install command), the running and the
│   │                                        previous version (release, previous-release)
│   ├── settings.json                        settings changed on the Settings page, on top of .env;
│   │                                        passwords in it are ENCRYPTED
│   ├── sudoo.db                             SQLite: accounts (password HASHES), second factors, API tokens,
│   │                                        audit log, instance state, creation times (mode 600)
│   ├── vault.key                            encrypts stored secrets, peppers password hashes (mode 600)
│   ├── secret_key                           signs session cookies (mode 600)
│   ├── keys/known_hosts                     GitHub's SSH host key, recorded on first use
│   ├── keys/github-app.json                 the GitHub App: id, slug, installations; its private key,
│   │                                        webhook secret and client secret ENCRYPTED (mode 600)
│   ├── keys/deploy/<owner>__<repo>.key      deploy key, ENCRYPTED (mode 600)
│   ├── keys/deploy/<owner>__<repo>.pub      its public part (what is added on GitHub)
│   ├── keys/deploy/<owner>__<repo>.name     the repository name as written on GitHub
│   └── documentation, code                  none: they are inside the images (the documentation of the running
│                                            version is in sudoo-panel under /app/documentation)
├── gateway/                                 shared by sudoo-panel and sudoo-gateway (mode 700), nothing secret:
│   ├── routes.json                          the instances and their ports, written by the panel
│   ├── visits.json                          the last visit of every instance, written by the gateway
│   └── gateway.sock, panel.sock             how the two talk to each other
├── mail/                                    shared by sudoo-panel and sudoo-mail (mode 700):
│   ├── catcher.json                         the mailboxes (SHA-256 of each instance's SMTP password) and limits
│   └── boxes/<name>/<id>.eml, <id>.json     the caught e-mails of a staging or development instance
├── manager-backup-<date>.tgz                /opt/odoo/manager before an update or rollback (mode 600)
├── enterprise/<version>/                    Enterprise add-ons (folder source, default ENTERPRISE_PATH)
├── enterprise-git/<version>/                Enterprise add-ons downloaded by the git source
├── backups/                                 Odoo-format backup zips (mode 600)
└── instances/<name>/
    ├── .env                                 version, edition, ports, database, automation, pinned images
    │                                        (mode 600); MASTER_PASSWORD in it is ENCRYPTED
    ├── docker-compose.yml                   the instance's two containers, written by the panel (mode 600)
    ├── config/odoo.conf                     Odoo configuration; master password as a HASH only (mode 600,
    │                                        owned by the Odoo image's user)
    └── addons/                              the repository, with its submodules (empty for plain Odoo)
```

### Useful commands

| Task | Command |
| :--- | :--- |
| Everything running | `docker ps` |
| Backend log | `docker logs -f sudoo-panel` |
| Web app log | `docker logs -f sudoo-web` |
| Gateway log | `docker logs -f sudoo-gateway` |
| Is the gateway service answering? | `docker exec sudoo-gateway sudoo.sh gateway --check && echo yes` |
| Mail catcher log, and is it answering? | `docker logs -f sudoo-mail`, `docker exec sudoo-mail sudoo.sh mailcatcher --check && echo yes` |
| Odoo log of an instance | `cd /opt/odoo/instances/<name> && docker compose logs -f odoo` |
| Current links | `docker exec sudoo-panel sudoo.sh urls` |
| Effective settings | `docker exec sudoo-panel sudoo.sh config` |
| Memory | `free -m` |
| Disk per container and volume | `docker system df -v` |

Do not run `docker compose up` or `down` for an instance by hand: use the panel, which also keeps its gateway,
connector and state in step. A change made by hand to an instance's containers or files is found and, with
`DRIFT_ACTION=restore`, undone (next section).

### What keeps running when the panel is down

With `GATEWAY_MODE=service` (the default), stopping, restarting or updating `sudoo-panel` does not stop the
instances' links:

| | While `sudoo-panel` is down |
| :--- | :--- |
| Running instances | Answer as usual, through `sudoo-gateway`. Visits are recorded and reach the panel when it is back. |
| Paused instances | Answer "Starting soon" and start as soon as the panel is back (the page asks every 4 seconds). |
| Switched-off instances | Answer "Switched off". |
| The panel's own link, the API, jobs, automatic pause and backups, new instances | Wait for the panel. |

And while `sudoo-gateway` is down (stopped, being updated), the panel serves the instances itself: at once when it
was stopped normally, after `GATEWAY_TAKEOVER_SECONDS` (10) when it stopped answering. It hands the ports back when
the gateway is up again. The connectors are never restarted: their links stay the same.

### Moving to the gateway service, or back

A server that runs a version before 3.0 has the gateways inside `sudoo-panel`. Updating moves them:

1. Update as usual ([section 4](#4-updating-rolling-back-uninstalling)), so that `compose.yaml` has the `gateway`
   service. Leave `GATEWAY_MODE` unset (or set it to `service`).
2. `sudoo-panel` is recreated with the new image. While it starts, the instances do not answer for a few seconds,
   as with every update before 3.0. It writes `/opt/odoo/gateway/routes.json` and, while no gateway service
   answers yet, opens the gateway ports itself.
3. `sudoo-gateway` starts, reads `routes.json` and opens the same ports next to the panel (both use
   `SO_REUSEPORT`); the panel sees it answer and closes its own, within 2 seconds. No connector is restarted.
4. Check: `docker exec sudoo-gateway sudoo.sh gateway --check && echo yes`, every link answers
   (`docker exec sudoo-panel sudoo.sh urls`), and `docker logs sudoo-panel` says nothing about the gateway service
   not answering. From now on, `docker restart sudoo-panel` leaves the instances answering.

If `sudoo-gateway` is not started (an older `compose.yaml`, or an installer that recreates only `sudoo-panel` and
`sudoo-web`), nothing breaks: the panel keeps serving the instances, as before.

To keep the gateways in the panel, set `GATEWAY_MODE=embedded` in `.env` and recreate `sudoo-panel`
(`docker compose up -d manager`); `sudoo-gateway`, if it runs, gives the ports back and stays idle (stop it with
`docker compose stop gateway`). To move back to `service`, remove the line, recreate `sudoo-panel` and start
`sudoo-gateway` (`docker compose up -d`). Either way the ports change hands without a connector restart.

### Hardened containers and changes made by hand

Each instance's `odoo` and `db` containers run with no extra privileges, read-only system files, as few Linux
capabilities as the images need, limits, rotated logs, images pinned by digest, and the database on an internal
network only. What exactly, and what cannot be protected: [Security](SECURITY.md#10-instance-containers).

**Limits.** Processor and memory have no limit by default (see [Resources and priority](#resources-and-priority)).
The others are set on the Settings page (**Instances**, **Instance containers**) or in `.env`:

| Setting | Default | Meaning |
| :--- | :--- | :--- |
| `CONTAINER_PIDS_LIMIT` | `1024` | Processes and threads per container |
| `CONTAINER_LOG_MAX_SIZE`, `CONTAINER_LOG_MAX_FILES` | `10m`, `3` | Log rotation per container |

A change reaches an instance the next time it starts or restarts through the panel; running instances are never
restarted for it. The same is true of instances made before this version: they get the hardened containers at their
next start or restart (the database container is recreated too; its data stays in its volume).

**Images.** An instance keeps the exact Odoo and PostgreSQL images it was created with (pinned by digest in its
`.env` as `ODOO_IMAGE_REF` and `DB_IMAGE_REF`). **Update Odoo and PostgreSQL images** in the instance's menu
(`POST /api/v1/instances/<name>/actions/update-images`, permission `instances.operate`) downloads the same image names
and tags again, pins the new digests and recreates the containers; a paused instance gets them at its next start.
`ODOO_IMAGE` and `POSTGRES_IMAGE` apply to new instances only, so an instance never jumps to another PostgreSQL major
version.

**Changes made by hand (drift).** Every 20 seconds the panel compares each instance with what it applied. When
something differs twice in a row:

* the instance's page shows a red banner, **Changed outside the panel**, listing what differs, and the instance card
  a tag of the same name;
* the audit log gets "configuration changed outside the panel", with the details;
* every super admin with an e-mail address gets a message (when e-mail is set up).

What happens next is `DRIFT_ACTION`:

| `DRIFT_ACTION` | Odoo and database containers, compose file, odoo.conf | Connector, `.env` |
| :--- | :--- | :--- |
| `restore` (default) | A job **Restore the panel's configuration** runs as soon as no other job runs on the instance: database backup first if its container is affected (kind "Before a repair", one kept per instance), the panel's files written back, strange containers stopped, affected containers recreated. A paused instance stays paused. Tried once per set of differences, again after 30 minutes if they remain. | Reported only. |
| `warn` | Reported only; the banner has a **Restore the panel's configuration** button (`instances.operate`, action `repair`). | Reported only. |

The banner goes away by itself once the instance matches again. A connector is never recreated by the panel (its
link would change): undo the change by hand, or remove the connector and let the panel create a new one (new link).
A hand-edited `.env` never changes the containers; **Restore the panel's configuration** accepts it as it is, after
you have checked it.

To see what the check sees, without changing anything:

```bash
docker exec sudoo-panel python -c "from sudoo.instances import integrity; print(*integrity.findings('<name>'), sep='\n')"
```

---

## 9. Odoo Enterprise add-ons

**The Enterprise add-ons are not part of this project** and never go into its repository: they are Odoo's
proprietary code, available to Odoo partners and subscribers. Each server needs its own copy. Where the panel finds
it is chosen by the add-on source plugin (`ENTERPRISE_SOURCE`, see [Plugins](PLUGINS.md#4-add-on-sources-where-enterprise-comes-from)).
The paths and the git address can be changed on the Settings page (**Odoo**, **Enterprise add-ons**); changing the source
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
refreshed the next time. Use an SSH address (`git@github.com:my-company/odoo-enterprise.git`): an address with a user
name or password in it (`https://user:token@...`) is refused, because it would be shown in job logs and kept in the
downloaded folder, which every Enterprise instance can read. If your `.env` still has such an address, the panel does
not start until you replace it.

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
└── manifest.json     database name, Odoo version, edition (backups of this version of Sudoo.sh on), instance
```

The time in the name is when the backup started. Files from earlier versions have no `__<kind>`
part and count as `manual`.

| Kind | Taken | Kept |
| :--- | :--- | :--- |
| `manual` | **Back up now** on an instance's page. | Until someone deletes it. |
| `daily` | Once a day, for each instance used since its last daily backup (below). | Only the latest per instance; the new one replaces the previous. Removed when the instance is deleted. |
| `deletion` | Before every deletion, automatic or by hand. If it fails, the instance is not deleted. | Until someone deletes it. |
| `before-restore` | At the start of every restore: the state just before it, offered as **Rollback**. | Only the latest per instance, for `ROLLBACK_HOURS` (24 by default). Removed when the instance is deleted. |
| `before-rollback` | At the start of every rollback: the state just before it. | Only the latest per instance, for `ROLLBACK_HOURS`. Removed when the instance is deleted. |
| `before-repair` | Before the panel recreates a database container that was changed outside the panel ([drift](#hardened-containers-and-changes-made-by-hand)). | Only the latest per instance. Removed when the instance is deleted. |
| `before-update` | Before an update changes an instance's code (a push, **Get latest code**, **Update submodules**), when `SAFE_UPDATES` is on; for a paused instance only when modules will be upgraded. Put back automatically when the update fails (below). | Only the latest per instance; one is kept for a restore by hand when the automatic rollback failed. Removed when the instance is deleted. |

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
* A **production** instance is backed up every day, used or not, even with `DAILY_BACKUPS=false`
  ([Instance stages](#instance-stages)). `STAGING_DAILY_BACKUPS=false` leaves staging instances out.
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

It takes about a minute or two, depending on the size. The instance is running afterwards. A backup of the
instance itself goes back into the database it was taken from (the instance must still have it).

**Backups of other instances.** **Restore another backup…** on the Backups card (and `{"file"}` of the `restore`
action) takes a backup of any instance, and **Start from a backup** on the New instance page (`from_backup`) starts a
new instance from one. Both need `instances.restore` and `backups.manage` (administrators and super admins; a
maintainer only restores an instance's own backups), and creating also needs `instances.create`. The panel checks:

* the backup exists (only names from the backup folder are accepted, never a path);
* its Odoo series, read from `version` in its `manifest.json`, is the instance's (`17.0` goes only into `17.0`; a
  backup without a readable version is refused);
* an Enterprise backup goes only into an Enterprise instance (backups taken before this version of Sudoo.sh do not
  record the edition and are not checked for it).

The backup's database is loaded under the target instance's database name (the dump has no database name or owner
in it), and its filestore goes to the target's `filestore/<database>`. Then, because the data comes from another
instance, the job neutralises it: on Odoo 16 and later it runs `odoo neutralize -d <database>` in a temporary
container of the instance, which switches off mail servers, scheduled actions, payment providers and the other
integrations each installed app declares; on older versions, or if that command fails, it switches off
`ir_mail_server`, incoming mail servers and every scheduled action except Odoo's database cleanup directly in SQL.
In both cases the copy gets a new `database.uuid` and `database.secret`, so it is not taken for the original and
sessions of the original do not work in it. The job's notes say it was neutralised. Finally web.base.url is set to
the instance's own address, as for every restore. The instance's own backups are not neutralised. A backup of an
earlier, deleted instance with the same name is not the instance's own: each instance has a random `INSTANCE_ID`
(in its `.env`, and in the `manifest.json` of every backup taken from it), so such a backup needs `backups.manage`,
is never picked by **Restore the latest backup**, and is neutralised like any other instance's data. Instances and
backups from before `INSTANCE_ID` was recorded are told apart by time: a backup without an id is the instance's own
when it was taken after the instance was created.

A new instance started from a backup skips creating an empty database (and demo data). If **Install the
repository's modules** is on, only the repository's modules the backup does not have installed are installed;
modules it has are left at their version (upgrade them with **Upgrade modules** if the code is newer). The backup's
user 2 (the first administrator) gets `ODOO_ADMIN_PASSWORD`, so the instance's **Access** tab is right, and the job's
result shows that user's login.

**Rollback.** For `ROLLBACK_HOURS` (default 24, on the Settings page under **Backups**) after a restore, the
instance's page shows **Rollback to before the restore (until …)**, and the `rollback` action puts back the newest
`before-restore` backup of the instance. A rollback first saves the current state as `before-rollback`, never as
`before-restore`, so it cannot replace the snapshot it restores; if that backup fails (for example after a restore
that failed halfway and left no database), the rollback goes on with a warning. Restoring an instance's own
`before-restore` backup from the list is a rollback too. Every five minutes the panel removes `before-restore` and
`before-rollback` backups older than `ROLLBACK_HOURS`, but never while a job runs (a restore or a creation may be
about to read one); the Rollback offer ends with the file. After a rollback the banner goes away.

**Safe updates.** With `SAFE_UPDATES` on (the default, on the Settings page under **Backups**), an update that
changes an instance's code first saves a `before-update` backup (database and filestore) and records the commit of
the repository and of every submodule. When a later step fails (the pull cannot fast-forward, a submodule cannot be
checked out, the module upgrade fails, or Odoo does not answer within the usual wait after the restart), the panel
puts the instance back on its own: the previous commits, the `before-update` backup when an upgrade ran or started
(the database may have changed), then Odoo restarted and checked. The job fails with "The update to `<sha>` failed
at `<step>`; the instance was put back to `<previous sha>`." and the explanation of the error; the instance's page
shows it until an update works, and the audit log has an entry ("update failed, instance put back"). Automatic
updates do not try the same commit again (a new push does, and so does **Get latest code**). When putting the
instance back fails too, the message says so and names the `before-update` backup, which is then kept (a later
update does not replace it) so you can restore it by hand. A paused instance stays paused: it is backed up only when
modules are upgraded. `SAFE_UPDATES=false` updates as before, without the backup and the rollback.

**Disk use.** Backups by hand and before deletions add up until someone deletes them. Daily, before-restore,
before-rollback, before-repair and before-update backups stay bounded: at most one of each per instance, before-restore
and before-rollback only for `ROLLBACK_HOURS`. Plan for about three zips per instance in use on top of the
ones kept by hand, and check `du -sh /opt/odoo/backups` from time to time; the server's disk use is also on the
Instances page.

**Lifetime.** Each instance has `DELETE_AFTER_DAYS` in its `.env` (0: kept). Every 10 minutes the panel deletes the
instances whose lifetime is over, backup first; if the backup fails, the instance is not deleted. Code running in an
instance can make its own backup fail, so after three failed automatic deletions in a row the panel switches the
instance off instead, stops trying, and records "automatic deletion stopped" in the audit log; the failed job says
so too. Delete it by hand then: **Delete even if the backup fails** in the Delete dialog (`"without_backup": true`
in the API) removes it without a complete backup and needs `backups.manage` as well as `instances.delete`. Giving
the instance another lifetime starts the automatic attempts again. A backup's `pg_dump` waits at most five minutes
for a lock, so a lock held inside the instance makes it fail rather than hang. New instances get
the default lifetime (30 days; Settings, Instances, New instances) unless "Keep until deleted by hand" is chosen; it can be
changed on the instance's Settings tab. Making it end sooner (also from "Keep" to a number of days) needs
`instances.delete` as well, since it deletes the instance sooner: maintainers can only make it longer.

**Off the server.** Everything above stays on this server's disk: a lost server takes its backups with it. Copy
them to storage somewhere else with [off-site copies](#20-off-site-copies).

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
| `.env` | The base settings | The installer writes the defaults. |
| `secret_key` | Signs session cookies | A new one is made; everyone signs in again. |
| `keys/` | Deploy keys (encrypted) | Deploy keys must be created and added on GitHub again. |

`compose.yaml` and `installer/` are written again by the installer; the code and the documentation are in the images.

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
Every other account task is done in the panel ([User manual](USER_MANUAL.md#9-accounts-and-roles)): new accounts
need an e-mail address that no other account uses; the password is generated or typed by the administrator
(checked against the [password rules](USER_MANUAL.md#password-rules)); super admins, including your own account,
can be edited; and the last super admin cannot be demoted or deleted.

A person who is locked out by a lost second factor does not need the command line: an administrator uses **Remove
second factors** on the Accounts page. If no administrator can sign in, create a new super admin with
`create-user NAME super_admin`.

---

## 12. Checking that everything works

After installing or updating:

1. `docker ps` shows `sudoo-web`, `sudoo-panel`, `sudoo-gateway`, `sudoo-mail`, `sudoo-connector-panel`, and for each instance its connector, Odoo
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
| The link opens but shows an error, or nothing | `sudoo-web` is not running or not starting | `docker logs sudoo-web`, then `cd /opt/odoo/manager && docker compose up -d` |
| `sudoo-panel` keeps restarting | A wrong value in `.env`, or an unknown plugin name | `docker logs sudoo-panel` names the setting; fix it in `.env` and run `docker compose up -d` in `/opt/odoo/manager` |
| The setup code was lost | It is only printed once by the installer | `docker exec sudoo-panel sudoo.sh setup-code` |
| The setup page refuses even the right code | 5 wrong setup codes within 15 minutes blocked the address | Wait 15 minutes; check the code with `sudoo.sh setup-code` |
| Nobody can sign in (password lost, last super admin locked out) | | `sudoo.sh reset-password NAME`, or `sudoo.sh create-user NAME super_admin` ([section 11](#11-accounts-from-the-command-line)) |
| Lost phone or mailbox with the second factor | | An administrator uses **Remove second factors** on the Accounts page |
| "Too many failed attempts" at sign-in | Sign-in lockout (15 minutes) | Wait, or reset the password |
| "Wrong e-mail address or password" for an account created with `create-user` | The account has no e-mail address | Sign in with the user name, then add the e-mail address in **My account** |
| No daily backup of an instance | Nobody used it since its last daily backup, `DAILY_BACKUPS` is off, or the backup time has not come yet in `TIMEZONE` | Check **Settings, Backups**; the instance's **Activity** tab shows "Daily backup" jobs and why one failed |
| **Restore the latest** is greyed out | The instance has no `manual`, `daily` or `deletion` backup of a database it has | Click **Back up now** to take one, or use **Restore another backup…** for a backup of another instance |
| A backup is greyed out in the backup list | It is of another Odoo version, its version is unknown (no `manifest.json`), or it is an Enterprise backup and the instance is Community | The reason is shown next to it; use an instance of that version and edition |
| A restore failed | The backup file is damaged, or the database did not start | The **Activity** tab shows the step and the reason. Click **Rollback to before the restore** on the instance's page to go back |
| A copy of another instance sends no e-mail | It was neutralised on purpose | In Odoo, switch the outgoing mail servers and the scheduled actions you need back on |
| E-mail codes or the test e-mail do not arrive | Wrong mail settings, or Gmail refuses the normal password | **Send a test** (or `sudoo.sh test-email ADDRESS`) shows the reason. For Gmail, `SMTP_PASSWORD` must be an app password and 2-Step Verification must be on ([section 6](#6-e-mail)). Look in the spam folder. |
| "E-mail code" is not offered | No mail server set, or `email` not in `SECOND_FACTORS` | Set up e-mail ([section 6](#6-e-mail)); after changing `SECOND_FACTORS`, **Restart the panel** |
| A plugin setting changed in the browser has no effect | Plugin settings apply after a restart | Settings, Advanced, **Restart the panel** (refused while jobs run: wait for them) |
| A value changed in `.env` has no effect | The same setting was changed on the Settings page, which wins | Click **Use .env** next to it on the Settings page |
| "Starting up" for several minutes | Odoo fails to start | The instance's **Logs** tab |
| "Starting soon" on a paused instance | `sudoo-panel` is down or restarting; only it can start an instance | `docker ps -a`, `docker logs sudoo-panel`; the instance starts once the panel is back |
| An instance's page says its gateway port is used by another program | Another program holds that port | `ss -ltnp 'sport = :<port>'` shows which; stop it, and the gateway takes the port within seconds |
| `docker logs sudoo-panel` says the gateway service does not answer | `sudoo-gateway` is stopped, restarting or hangs (the panel serves the instances meanwhile) | `docker logs sudoo-gateway`, then `cd /opt/odoo/manager && docker compose up -d gateway` |
| An access report line is red on New instance | The message says which: the app belongs to another account, is not installed on the repository's owner, the repository is not among its selected ones; or no app and no deploy key | Follow the button next to the line ([When a repository cannot be read](#when-a-repository-cannot-be-read)); or create a deploy key ([User manual](USER_MANUAL.md#8-github-access-private-repositories)) |
| GitHub's page after **Create GitHub App** or **Install** does not open | The panel's public address changed since the app was created (GitHub still has the old one) | Open the panel at its current address, **GitHub access**, **Refresh**. A new app: connect again |
| Pushes do not start updates at once | `github-app` is not in `UPDATE_TRIGGERS`, or GitHub cannot reach the panel | Settings, Plugins; on GitHub, the app's **Advanced** page lists recent deliveries and their answers |
| Update fails: "branch ... no longer exists on GitHub" (or "no such ref was fetched"); the instance page warns about it | The branch was deleted on GitHub, usually after its pull request was merged | Menu, **Switch branch**, choose the branch to follow now (for example `main`) |
| Update fails: "went in different directions" | Something was changed directly in the instance's `addons` folder | A developer resets that folder to the GitHub branch |
| Module changes not visible | The module was not upgraded | **Upgrade modules**, or turn on **Upgrade changed modules** |
| Instance "Stopped" | Odoo crashed, often out of memory | **Start**; pause other instances; `free -m` |
| "Cannot decrypt the value" in a log | `vault.key` was replaced or lost | Restore `vault.key` from its backup; otherwise recreate deploy keys, set the passwords on the Settings page again and reset account passwords |
| Deletion failed: "the database did not start, so no backup could be taken" | The instance's database container is broken | Nothing was deleted. Check `docker logs odoo-<name>-db-1`; to delete anyway, fix or remove the database by hand |
| JSON-2 API: 401 with a valid key | Odoo 20 API key without an expiry date | Create the key with an expiry date ([API](API.md)) |
| Requests over 100 MB, or over 100 seconds, fail | Limits of the free tunnel (`EXPOSURE=tunnel`) | Split uploads; run long operations in the background, or switch to `direct` ([section 14](#14-changing-how-the-server-is-reached)) |
| The browser warns about the panel's certificate | `EXPOSURE=direct` without a name for the panel: it uses its own certificate | Point a name at the server and set **Panel's name** (`PANEL_DOMAIN`) |
| The panel's name has no certificate | Port 80 is closed, or the name does not point at the server | Open TCP 80 and 443; check the name's A/AAAA records; **Settings, Addresses and domains** shows the reason |


### Installer checks

Every check the installer runs, what a failure means and what to do. A ✗ stops the installer before it changes
anything; a ! is a warning. `sudo sudoo-install check` runs them all without changing anything.

| Check | ✗ or ! means | Fix |
| :--- | :--- | :--- |
| Running as root | It was run without `sudo` | Run it with `sudo` (`... \| sudo bash`) |
| Operating system | ✗ Ubuntu older than 22.04 or Debian older than 12; ! another distribution (not tested) | `sudo do-release-upgrade` (Ubuntu) or upgrade Debian; elsewhere it may work |
| Architecture | Not amd64 (x86_64) or arm64 | Use an x86_64 or arm64 server |
| Service manager: systemd | The system does not boot with systemd (a container, WSL) | Use a server or VM with Ubuntu or Debian |
| Processor | Fewer than 2 cores | A server with 2 or more cores |
| Memory | ✗ under 2 GB; ! under 4 GB | A bigger server; at least add a swap file (`fallocate -l 2G /swapfile`, `mkswap`, `swapon`) |
| Free disk for /opt and /var/lib/docker | Under 20 GB free (5 GB for an update) | Free space (`docker system df`, `docker image prune`) or enlarge the disk |
| Clock | ! The clock is not synchronised | `sudo timedatectl set-ntp true` |
| Ports 80 and 443 for Direct | ! Another program listens on port 80 or 443, the server has no public address of its own, or a host firewall (ufw, firewalld) does not let them in | Stop the other program (`sudo ss -ltnp 'sport = :443'`), open the ports at the firewall and the hosting provider, or choose Proxy or Tunnel (`--exposure proxy` or `tunnel`) |
| Port ... is free | Another program listens on `PANEL_PORT`, `API_PORT` or `EVENTS_PORT` | `sudo ss -ltnp 'sport = :9000'` shows it; stop it, or set another port in `/opt/odoo/manager/.env` and run the installer again |
| Tools | curl, tar, openssl or ca-certificates is missing | Answer yes when asked, or `sudo apt-get install -y curl openssl ca-certificates` |
| HTTPS to github.com, ghcr.io, registry-1.docker.io, download.docker.com (and api.trycloudflare.com for a tunnel) | No connection | Allow outgoing TCP 443; check DNS (`getent hosts ghcr.io`) and any proxy (`HTTPS_PROXY`, and Docker's own proxy settings) |
| Port 7844 (tunnel only) | ! Outgoing 7844 is blocked | Allow outgoing TCP and UDP 7844 to `*.argotunnel.com` |
| Docker Engine | Missing, from a snap, not running, or older than 24 | Answer yes to install or start it; a snap: `sudo snap remove docker`, then run again; old: upgrade from Docker's repository |
| Docker Compose | The Compose v2 plugin is missing | Answer yes, or `sudo apt-get install -y docker-compose-plugin` (Ubuntu's `docker.io`: `docker-compose-v2`) |
| Docker buildx | ! Missing (only needed to build images yourself) | `sudo apt-get install -y docker-buildx-plugin` (or `docker-buildx`) |
| Control groups | ! cgroup v1 | Boot with cgroup v2 (`systemd.unified_cgroup_hierarchy=1`), the default on Ubuntu 22.04+ and Debian 12+ |
| Docker starts after a reboot | ! Not enabled | `sudo systemctl enable docker` |

| Message after the checks | What to do |
| :--- | :--- |
| "There is no terminal to ask, so nothing was changed." | Run the printed command (the same, with `--yes`). |
| "The licence agreement must be accepted, and there is no terminal to ask" | Read the agreement at the printed address, then run the same command with `--accept-licence` ([section 22](#the-installer)). |
| "Could not download ghcr.io/sudoo-sh/..." | Check access to `ghcr.io`; `docker pull` the image by hand to see Docker's message. |
| "The signature of ... could not be verified" | Do not install it; report it ([Security](SECURITY.md#13-releases-and-signatures)). |
| "Sudoo.sh did not become healthy." | `docker logs sudoo-panel` names the problem (usually `.env`); fix it and run `sudo sudoo-install update`, or go back with `sudo sudoo-install update --rollback`. |
| "These containers changed meanwhile" | The installer does not touch instances or connectors; the panel itself may have started or paused an instance. Check `docker ps`. |
| "No earlier version is kept on this server." | A rollback needs an update made by this installer; install a given version with `update --version X`. |

---

## 14. Changing how the server is reached

`EXPOSURE` can be changed later on **Settings, Addresses and domains, How people reach this server**: the page shows
the current mode and the panel's address, explains what the change does, and the change applies when the panel
restarts (**Restart the panel**). It offers `direct`, `proxy` and `tunnel`; a change that would move the web service
to another address (from or to `host-ports`, or `proxy` with `PROXY_LISTEN` other than `127.0.0.1`) is refused there:
change `EXPOSURE` in `/opt/odoo/manager/.env` instead and run `cd /opt/odoo/manager && docker compose up -d`. The
gateway, the instances and their data stay as they are; only the addresses change.

| From | To | Before switching |
| :--- | :--- | :--- |
| `tunnel` | `direct` | Open TCP 80 and 443; point a name at the server and set **Panel's name**. A `.env` written by an earlier release has `CUSTOM_DOMAINS=off`: set it to `acme` (or empty) to serve custom domains. The `sudoo-connector-*` containers keep running until removed (`docker rm -f $(docker ps -aq --filter name=sudoo-connector-)`). |
| `tunnel` or `direct` | `proxy` | Set up the proxy first ([Public addresses](concepts/PUBLIC_ADDRESSES.md#4-proxy)), with `PANEL_DOMAIN` or `PUBLIC_HOST`. |
| `direct` or `proxy` | `tunnel` | Nothing; the connectors start with the panel. |

Keep the Settings page open at the current address until the new one works. If the panel cannot be reached
afterwards, set `EXPOSURE` back in `.env` and run `docker compose up -d`, or reach it through SSH
([section 7](#7-public-addresses)). The options are compared in
[Public addresses](concepts/PUBLIC_ADDRESSES.md#2-choosing).

---

## 15. The GitHub App

The panel reads private repositories through **one GitHub App of its own**, created by GitHub from a description
the panel sends (GitHub's "App Manifest" flow). Nothing is copied by hand: no keys, no secrets, no webhook per
repository. Only super admins connect, install and disconnect it.

What the app may do, and nothing more:

| | |
| :--- | :--- |
| Repository permissions | **Contents: read**, **Metadata: read** (no write to code, no issues, no pull requests); **Commit statuses: write**, to show each build's result next to its commit ([Commit statuses](#commit-statuses)); **Administration: write** only when you allow it, to create empty repositories for new projects ([below](#creating-repositories-for-new-projects)) |
| Events | **Push**, **Create**, **Delete** (branches), plus the installation events GitHub always sends |
| Visibility | Private: only the account that owns it can install it |
| Webhook | `<panel address>/hooks/github-app`, signed with a secret GitHub creates |

### Which account should own the app

A private GitHub App can be installed **only on the account that owns it**. An app created on your personal account
can never read the repositories of your company's organisation, and an app created under the organisation cannot
read your personal repositories. The panel connects one app, so choose its owner by where the instances' code is:

| The code is in | Create the app under | Other repositories |
| :--- | :--- | :--- |
| A company organisation (the usual case) | The organisation (**An organisation** in step 2 below). An owner of the organisation on GitHub has to do it | Personal or other repositories: deploy keys ([User manual](USER_MANUAL.md#8-github-access-private-repositories)), one per repository |
| Your personal account only | Your account (**My personal account**) | Company repositories: deploy keys, or reconnect the app under the organisation |

The app stays private and never writes code either way. Connecting a second app at the same time is not
supported: each app has its own private key, webhook secret and webhook, and one app per panel keeps that simple and
safe.

An app already connected under the wrong account (for example created on a personal account when the repositories
are the company's): click **Disconnect**, delete the old app on GitHub (the links the panel shows), then **Connect
GitHub** again and choose **An organisation**. Instances keep their code; they get new code again once the new app is
installed on the organisation.

### Connecting

1. Sign in as a super admin, open **GitHub access** and click **Connect GitHub**.
2. Answer **Whose repositories will the instances use?**: **An organisation** (a company's repositories) or **My
   personal account** (see [Which account should own the app](#which-account-should-own-the-app)). For an
   organisation, type its name as in `github.com/<name>`; you must be an owner of the organisation (GitHub refuses
   otherwise). Click **Continue on GitHub**.
3. GitHub shows **Create GitHub App for &lt;account&gt;** with the name filled in, for example
   `Sudoo.sh panel-3f9a`. The name must be unique on all of GitHub; change it if GitHub says it is taken (34
   characters at most). Click **Create GitHub App for &lt;account&gt;**.
4. GitHub sends you back to the panel, which exchanges GitHub's one-time code for the app's credentials (within
   an hour, and only for the connection this browser session started) and shows "GitHub App created".

The private key, the webhook secret and the client secret are stored encrypted with the vault key in
`/opt/odoo/manager/keys/github-app.json`. They are never shown again, not even to super admins.

### Installing

1. Click **Install on GitHub**. GitHub offers only the account that owns the app (the app is private).
2. Choose **All repositories** or **Only select repositories** (then pick them), and click **Install**.
3. GitHub sends you back; the panel checks the installation with GitHub and lists it under **Installed on**. In an
   organisation where you are not an owner, GitHub sends a request to the owners instead.

The panel also reads the app's installations from GitHub by itself, at most once a minute: when the GitHub access
page opens and before it checks a repository. An installation made directly on GitHub, approved later by an
organisation owner, or whose return to the panel never arrived therefore shows up without pressing **Refresh**
(which reads them at once). The panel uses, for each repository, the installation of the repository's owner.

### When a repository cannot be read

When the panel cannot read a repository (the **New instance** page's check, or the first step of creating an
instance), it says what applies, with a button to fix it:

| Message begins with | What it means | What to do |
| :--- | :--- | :--- |
| The panel's GitHub App belongs to &lt;account&gt; | The repository's owner is another account; a private app cannot be installed there | Reconnect the app under that organisation ([Which account should own the app](#which-account-should-own-the-app)), or use a deploy key |
| The GitHub App is not installed on &lt;owner&gt; | The app belongs to that account but has no installation there | **Install on &lt;owner&gt;** |
| The GitHub App's installation on &lt;owner&gt; is suspended | Someone suspended it on GitHub | Unsuspend it in the installation's settings on GitHub |
| The GitHub App is installed on &lt;owner&gt;, but &lt;repository&gt; is not among the repositories it may read | The installation has **Only select repositories** | **Choose repositories** and add it |
| The GitHub App can read the repositories of &lt;owner&gt;, but GitHub does not know &lt;repository&gt; | The address is wrong | Check the address |

Without a connected app, the message advises a deploy key instead.

### How the panel uses it

* For each installation the panel asks GitHub for an **installation access token**: read-only, valid one hour. The
  request is signed with a short JSON Web Token (RS256, valid nine minutes) made from the app's private key. Tokens
  are reused until five minutes before they expire, kept encrypted in memory (`MEMORY_DIR`).
* git receives a token only from the panel's credential helper, when GitHub asks for credentials. Tokens are never
  written into repository addresses, `.git/config` or job logs. Submodules on GitHub whose owner has an installation
  are read the same way.
* The **New instance** page lists the repositories of all installations, and branches come from GitHub's API.
* With `github-app` in `UPDATE_TRIGGERS` (the default for new servers), a push updates the instances that follow
  the branch within seconds; keep `polling` next to it as a safety net. On a server installed earlier, add
  `github-app` on the Settings page (Plugins) and restart the panel.
* Every two minutes the panel compares its public address with the app's webhook address and, when the default
  public-link extension gave the panel a new address, updates the webhook on GitHub (an audit-log entry says so).

* When a build of a project's branch starts and ends, the panel sets the commit's status on GitHub with a separate
  token that may only write statuses ([Commit statuses](#commit-statuses)).

A repository that still has a deploy key keeps using it. Delete the key to switch that repository to the app.

### Creating repositories for new projects

A new project can start from a **new, totally empty repository** that the panel creates in an organisation
([Projects and branches](#24-projects-and-branches)). That needs one more permission, **Administration: Read and
write** (creating repositories), which the app does not have unless you give it:

* **When connecting:** tick **Also allow creating repositories** on the GitHub access page before **Connect
  GitHub**; the app is created with the permission.
* **For an app connected earlier:** on GitHub open the app's settings, **Permissions and events**, **Repository
  permissions**, set **Administration** to **Read and write** and save. GitHub then asks an owner of each
  organisation to accept the new permission (an e-mail, and a banner on the installation's page). The new-project
  wizard shows both links and **Check again** once it is accepted.

The panel uses the permission only for creating a repository: it asks GitHub for a separate token with
Administration: write and Metadata: read, valid one hour, used for that one call and never kept; the tokens git uses
stay read-only. A repository is created private (unless chosen otherwise), with no README and no branch. GitHub lets
an app create repositories only in **organisations**: for a personal account, create an empty repository on GitHub
and link it. Each creation is in the audit log ("repository created on GitHub").

### Checking a project's connection

On a project's **Settings** tab, **GitHub connection, Check connection** checks every part of the project's link to
GitHub at once, like **Verify Deploy Key** and **Verify Webhook** on Odoo.sh, and changes nothing:

| Check | What it looks at |
| :--- | :--- |
| Repository | The server can read the repository (through the App, a deploy key, or as a public repository). |
| GitHub App installation | The App is installed on the repository's owner, not suspended, and covers the repository (all repositories, or among the selected ones). |
| Permissions and events | **Contents: read** and **Metadata: read** are needed; **Commit statuses: write** (builds shown on commits) and the push, create and delete events (changes arrive at once, not at the next poll) are recommended. |
| Webhook | The App's webhook goes to this panel's address, and its latest deliveries for this repository (or the App's) arrived; a failed delivery links to the App's **Advanced** page on GitHub, where it can be redelivered. |
| Submodules and deploy keys | Every submodule of the default branch can be read; a private one the App does not cover needs a deploy key. |

Each check that fails or warns says what to do and links to the page where it is done. The check needs
`projects.manage` in the project and is in the audit log (`GitHub connection checked`). API:
`POST /api/v1/projects/<id>/github/check` ([Panel API](PANEL_API.md#projects)).

### Disconnecting

**Disconnect** deletes the app's credentials, installations and tokens from the server, and shows two links: one to
uninstall the app from each account, one to the app's settings on GitHub, where **Advanced, Delete GitHub App**
removes it for good. Do both: until then the app still exists on GitHub (without its private key it can do
nothing). Instances of private repositories cannot get new code until an app is connected again or deploy keys are
added.

Every step (connection started, refused, connected, installed, refreshed, forgotten, disconnected, webhook address
changed) is recorded in the audit log.

---

## 16. Alerts

The panel tells the right people when something needs attention, so nobody has to watch the dashboard. Set it up on
the **Settings** page, **Alerts** tab (`/settings?tab=alerts`); every switch, threshold and duration there is a
setting with a default ([Configuration, Alerts](CONFIGURATION.md#alerts)).

### What is alerted

| Alert | When | Setting |
| :--- | :--- | :--- |
| A job failed | Any job on an instance fails. The alert carries the plain hint the panel shows next to the job, the step that failed and a link that opens the job's log. | `ALERT_JOB_FAILED` |
| An update failed | An update failed and the instance was put back to its previous code and database (`SAFE_UPDATES`), or putting it back failed too (the before-update backup is then kept for a restore by hand). | `ALERT_UPDATE_FAILED` |
| A backup failed | A manual or daily backup failed. | `ALERT_BACKUP_FAILED` |
| An instance is down | An instance that should run (not paused, not switched off, no job on it) is stopped, or Odoo in it does not answer, for `ALERT_INSTANCE_DOWN_MINUTES` (5). | `ALERT_INSTANCE_DOWN` |
| Disk almost full | The disk holding the instances and backups has less than `ALERT_DISK_FREE_PERCENT` (10 %) free. | `ALERT_DISK` |
| Storage near or over its quota | An instance's database and files reach `STORAGE_WARNING_PERCENT` (80 %) of its stage's storage quota, and again when they are over it (production: every day until it is back under) ([Resources and priority](#resources-and-priority)). Nothing is stopped. | `ALERT_STORAGE` |
| Memory high | Server memory stays at or above the capacity warning (`CAPACITY_WARNING_PERCENT`, 85 %) for `ALERT_MEMORY_MINUTES` (10). | `ALERT_MEMORY` |
| Changed outside the panel | An instance's containers or files were changed by hand ([Hardened containers](#hardened-containers-and-changes-made-by-hand)). | `ALERT_DRIFT` |
| No public link | The panel, or an instance that is online, has had no public address for `ALERT_PUBLIC_LINK_MINUTES` (10): its tunnel connector stopped, for example. | `ALERT_PUBLIC_LINK` |

When a problem that lasts is over (the instance answers again, the disk has room, memory is back below the warning,
the link is back), a **resolved** notice follows, unless `ALERT_RECOVERY` is off. A paused, switched-off or deleted
instance is not watched; an open alert about it is closed without a notice. While a job runs on an instance (a
restart, an update), the countdown waits.

### Who receives them

* **E-mail**: every super admin with an e-mail address (`ALERT_SUPER_ADMINS`, on by default) and the addresses in
  `ALERT_EMAILS` (an on-call mailbox, say). E-mail must be set up ([E-mail](#6-e-mail)).
* **Webhooks**: up to three (`ALERT_WEBHOOK_1_URL` to `ALERT_WEBHOOK_3_URL`), each in a format: `json` (the panel's
  own JSON, signed), `slack`, `discord` or `teams` (paste the incoming webhook address from Slack, Discord or a
  Teams workflow).
* **The bell** at the top of the panel lists the latest alerts for everyone who may read the audit log (super admins
  and administrators); new ones appear as they happen, with a dot until you open the list.

**Send a test alert** next to the recipients and next to each webhook sends a test with the saved settings, and says
what went wrong when it fails. Save your changes first.

### How often

The same alert (same kind, same instance) is sent at most once in `ALERT_QUIET_MINUTES` (60). A repeat inside that
time is not sent again; the bell counts it on the alert already listed ("3 times"). With `ALERT_DIGEST` on, e-mails
come as one summary a day at `ALERT_DIGEST_TIME` (08:00 in `TIMEZONE`) instead of one per alert; webhooks and the
bell still get each alert at once.

### Webhooks in detail

A `json` webhook receives a POST with `Content-Type: application/json`:

```json
{"event": "alert", "id": "3f9c2a1b7d4e", "type": "instance_down", "severity": "problem",
 "title": "shop-main is down", "text": "Instance shop-main is stopped, for 5 minutes now. ...",
 "hint": null, "details": [], "instance": "shop-main", "link": "https://panel.example.com/i/shop-main",
 "count": 1, "at": "2026-11-17T08:00:00+00:00", "sent_at": "2026-11-17T08:00:01+00:00"}
```

`type` is one of `job_failed`, `update_failed`, `backup_failed`, `instance_down`, `disk`, `memory`, `drift`,
`public_link` and `test`; `severity` is `problem`, `recovered` (the problem is over) or `info` (a test). The header
`X-Sudoo-Event` repeats the type.

With a secret (`ALERT_WEBHOOK_<n>_SECRET`), every request carries
`X-Sudoo-Signature: sha256=<hex HMAC-SHA256 of the raw body, keyed with the secret>`. Check it before trusting the
body, in constant time, and ignore a `sent_at` older than a few minutes:

```python
import hashlib, hmac

def genuine(body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

Rules the panel keeps for every webhook:

* Only `https://` addresses (`http://` only for `localhost`), without a user name or password in them.
* Redirects are never followed: a 3xx answer counts as a failed delivery.
* Each request may take `ALERT_WEBHOOK_TIMEOUT_SECONDS` (10); the body is at most 64 KB, and at most 64 KB of the
  answer is read.
* A webhook's address and secret are secrets: stored encrypted, never shown on the Settings page, never written to
  the audit log (a failed delivery is logged as "alert webhook failed", naming the webhook by its number).

### What an alert never contains

Alerts are written by the panel in plain words; they never copy a job's log, an instance's passwords, API tokens or
keys, and any address with a user name or password in it is masked. The link in an alert opens the panel, which asks
the reader to sign in.


### Project status and notifications

Next to the alerts for the whole panel, each project has a **Status** tab and its own **Notifications**, as Odoo.sh
has a status page and chat notifications. Settings: [Configuration, Project status and
notifications](CONFIGURATION.md#project-status-and-notifications).

**Status checks.** Every `STATUS_CHECK_INTERVAL_SECONDS` (60) the panel checks, for each project:

| What | Up when | Degraded when |
| :--- | :--- | :--- |
| Each production instance | `GET /web/health` through the instance's own gateway port answers below 500 within `STATUS_CHECK_TIMEOUT_SECONDS` (it is not counted as a visit) | It answers slower than `STATUS_SLOW_MS`, or fails while a job runs on it |
| The server | The monitor measured it recently (its heartbeat) | The disk is below `ALERT_DISK_FREE_PERCENT` free, or memory above `CAPACITY_WARNING_PERCENT` |
| The production instance's backups | Its latest backup is newer than the last `DAILY_BACKUP_TIME`, `STATUS_BACKUP_GRACE_HOURS` (2) allowed | |
| Its off-site copies (with off-site copies on) | The latest successful copy is newer than `ALERT_OFFSITE_MISSING_HOURS` | |

A switched-off production instance counts as down. Results are kept per minute for `STATUS_MINUTE_HOURS` (48) and per
hour for `STATUS_KEEP_DAYS` (90), with the incidents (from the first failed check until the next good one). Uptime is
the share of checked minutes that were up; for the server, a minute without any check (the panel was not running)
counts as down. Days are UTC days. Nothing is sent to another server: the checks run inside the panel.

**Public status page.** Someone who manages a project can turn on a read-only page for it (off by default; a link
with a 43-character random token, which can be replaced at any time). It shows the project's name, each target's
name, state and uptime, and nothing else; `STATUS_PUBLIC_PAGES` off closes every such page.

**Notifications.** A project's channels: Slack (incoming webhook), Discord (webhook), Telegram (bot token and chat
id), Microsoft Teams (Workflows webhook), a webhook of your own (JSON signed with HMAC-SHA256 in
`X-Sudoo-Signature: sha256=<hex>`, its secret made by the panel and shown once), and e-mail (the panel's mailer). Each
channel chooses its events: build failed, build with warnings, build working again, stage change, promotion to
production, production down (after two failed checks in a row) and back up, backup failed, backup restored, storage
over its quota (the last one needs `ALERTS` and `ALERT_STORAGE`). They are taken from what the panel already
publishes (a branch's history, the jobs, the alerts): nothing is watched twice.

* Addresses and tokens are stored encrypted with the vault key and never shown again, logged or audited: the page
  shows their last 4 characters.
* Every request is checked first: `https://` only, no user name or password in the address, and the host name must
  resolve to public addresses only (no private, loopback, link-local, shared or multicast address, IPv4 inside IPv6
  included). The connection goes to the address that was checked, with the certificate checked for the host name,
  and redirects are never followed. A receiver inside your own network must be listed in
  `NOTIFICATION_ALLOWED_HOSTS`.
* Delivery runs in the background. No answer, HTTP 429 or a 5xx is tried again `NOTIFICATION_RETRIES` (5) times, 30
  seconds after the first try and twice as long each time after; other answers are not retried. Each channel shows
  how its last delivery went, and **Send test** sends one at once. Deliveries still waiting when the panel restarts
  are not sent.

---

## 17. E-mail templates

**E-mail templates** (sidebar, Administration; super admins only, permission `mail.manage`) lists every e-mail the
panel sends. For each one you can change the **subject**, the **title** (the large line at the top) and the
**text**. The built-in texts stay in the panel: **Reset to default** brings one back, and a panel update never
overwrites a text you changed.

| E-mail | When it is sent | To |
| :--- | :--- | :--- |
| Sign-in code | Signing in with the e-mail code | The person signing in |
| E-mail address confirmation | Setting up the e-mail code | The address being set up |
| New sign-in | After every sign-in ([section 18](#18-sign-in-e-mails-and-recovery-codes)) | The account that signed in |
| Invitation | **Invite** and **Send again** on the Accounts page: the link that accepts it (works once, `INVITATION_HOURS`) | The person invited |
| Alert | An alert as it happens, and its recovery ([Alerts](#16-alerts)) | Alert recipients |
| Daily summary of alerts | Once a day with `ALERT_DIGEST` | Alert recipients |
| Plugin notice | A plugin's licence ends soon or ended, a plugin was switched off | Every super admin with an address |
| Test e-mail | **Send a test** on Settings, E-mail, and `sudoo.sh test-email` | The address typed |

The panel sends no invitation or password-reset e-mails: new accounts and new passwords are handed over by the
administrator who makes them ([Accounts from the command line](#11-accounts-from-the-command-line)).

### Writing a text

Texts are plain text with a few marks. There is no template code: nothing you type is run, and every value the panel
fills in is escaped, so HTML you type shows as text.

| You type | You get |
| :--- | :--- |
| `{{ name }}` | The value of a placeholder. Each e-mail lists its own placeholders with what they hold; click one to insert it where the cursor is |
| A blank line | A new paragraph (a single line break stays a line break) |
| `**words**` | Bold |
| `- item` | A bulleted list, one item per line |
| `> words` | A highlighted note, for warnings |
| `[label](https://...)` or `[label]({{ link }})` | A link; alone on its line, a button. Only `https://`, `http://`, `mailto:` and link placeholders are accepted |

A few placeholders are blocks drawn by the panel (the code box, the details of a sign-in, an alert's details, the
list of alerts): they stand alone on their line. A line whose placeholders are all empty is left out, so
`Seen {{ repeats }} times.` disappears when an alert was seen once.

**Save** refuses a text with an unknown placeholder, a placeholder not written like `{{ name }}`, a block that is
not alone on its line, a link that is not allowed, or, for the code e-mails, a text without the code. The message
lists every problem. The preview shows the e-mail with sample values as you type (**E-mail** and **Text only**, the
part mail programs show when they show no HTML). **Send me a test** sends the preview, unsaved changes included, to
your own address with `[Test]` before the subject. Every change and reset is in the audit log.

The frame around every e-mail (the header with the logo, the footer with the contact) is
`src/sudoo/mail/templates/layout.html`. To change it, put your own `layout.html` in a folder and set
`MAIL_TEMPLATES_DIR` to that folder ([Configuration](CONFIGURATION.md)). The accent colour is `BRAND_COLOR`.

---

## 18. Sign-in e-mails and recovery codes

### The sign-in e-mail

After every successful sign-in the panel e-mails the account: when, from which address, from which browser and
system (read from the browser's description, so approximate), and how it signed in (password, authenticator app,
e-mail code or recovery code), with a link to the account page to change the password or sign out everywhere. It is
sent in the background: a slow or failing mail server never delays a sign-in. Nothing is sent while e-mail is not
set up, or to an account without an address.

| Setting | Default | What it does |
| :--- | :--- | :--- |
| `SIGN_IN_EMAIL` | on | Whether accounts get the e-mail, unless their owner chose otherwise |
| `SIGN_IN_EMAIL_OPT_OUT` | on | Whether people may turn it off on their account page. Off: everyone gets it while `SIGN_IN_EMAIL` is on |

Both are on **Settings, Sign-in and security**. Anyone may turn the e-mail on for their own account, even when
`SIGN_IN_EMAIL` is off.

### Recovery codes

When someone sets up the authenticator app, the panel makes `RECOVERY_CODES_COUNT` recovery codes (10 by default,
4 to 20) and shows them once. Each code works once at sign-in in place of the app's code. They protect against a
lost phone without asking an administrator.

* The panel keeps only a keyed hash of each code (HMAC-SHA256 with a key derived from the vault key), never the
  codes. A code is 10 characters from 31 that are hard to confuse (about 49 bits).
* Wrong codes count like wrong passwords: 5 per account and address, 20 per address, in 15 minutes. Every use is in
  the audit log ("recovery code used", with how many are left), and the sign-in e-mail names it.
* New codes need the account's password and replace the old ones. Removing the authenticator app removes its codes.
* Accounts that used the app before recovery codes existed see a notice on every page until they make them.

If someone has lost both the phone and the codes, a super admin or administrator removes their second factors on
the **Accounts** page; that removes the recovery codes too, and the person sets the app up again at the next
sign-in.

---

## 19. Custom domains and certificates

> Adding custom domains is a premium feature: it installs automatically from Sudoo.sh when your plan includes it.
> Domains set up before keep being served and renewed by the panel itself, whatever the plan. See
> [Premium features](PREMIUM.md).

An instance (usually a production instance) can answer on your client's own names, such as `erp.client.com`, over
https. Administrators add the domains on the instance's **Domains** tab ([User Manual](USER_MANUAL.md#custom-domains)); this
section is about what the server needs. How https is handled is `CUSTOM_DOMAINS` on **Settings, Addresses and
domains** ([Configuration](CONFIGURATION.md#custom-domains)); left empty it follows `EXPOSURE`: `acme` with
`direct`, `external` with `proxy`, off with `tunnel`.

### Which mode

| Your server | `CUSTOM_DOMAINS` | What happens |
| :--- | :--- | :--- |
| Accepts connections from the internet on ports 80 and 443 (`EXPOSURE=direct`, the default) | `acme` | The gateway answers the domains on ports 80 and 443, gets a certificate for each from Let's Encrypt (or the authority in `ACME_DIRECTORY`) and renews it. |
| Behind a proxy or load balancer you run (`EXPOSURE=proxy`) | `external` | The proxy handles https and forwards to `http://<PROXY_LISTEN>:<the instance's gateway port>` (shown under **Credentials**); the panel records the domain, checks that it reaches the instance, and uses it as the instance's address. |
| Reached only through a tunnel (`EXPOSURE=tunnel`, no open port) | `external`, or none | The tunnel's random addresses cannot carry your own domain. A tunnel of your own account, or another proxy, that sends the domain to the instance's gateway port works as `external`. Otherwise custom domains are not available on this server, and the Domains tab says so. |

### Setting up `acme`

1. Open ports 80 and 443 (TCP) to the internet in the server's firewall and at your hosting provider. Nothing else on
   the server may use them (the panel's own web service does not).
2. Set `DOMAIN_DNS_TARGET` to the server's public name, such as `server.yourcompany.com` (a name you control, with
   an A/AAAA record pointing at the server), or leave it empty to use `PUBLIC_HOST`. Users are told to create a
   CNAME record pointing at it; for a bare domain (`client.com`) they get A/AAAA records with its addresses. An IP
   address works too (users then create A or AAAA records), but a name lets you move the server later without
   asking every client to change their records.
3. Optionally set `ACME_EMAIL` (the contact the authority has for the account; empty uses `ADMIN_CONTACT_EMAIL`).
4. Set `CUSTOM_DOMAINS=acme`. Choosing it means you agree to the authority's terms of service (for Let's Encrypt,
   its subscriber agreement). To try the setup without using up Let's Encrypt's limits, set `ACME_DIRECTORY=staging`
   first: certificates then come from its test authority, browsers do not trust them, and the Domains tab says so.
5. Make sure the `sudoo-gateway` service runs with the current `compose.yaml` (it needs the `NET_BIND_SERVICE`
   capability to open ports below 1024): `cd /opt/odoo/manager && docker compose up -d gateway`.

If the gateway cannot open port 80 or 443 (another program holds it, or the capability is missing), the Domains tab
of every instance shows the error.

### What happens to a domain

1. **Waiting for DNS.** The panel looks the domain up from the server (with `DOMAIN_DNS_TIMEOUT_SECONDS` per lookup)
   every minute at first, then less often (up to once an hour). It passes when every address of the domain is one of
   the target's. A domain proxied by Cloudflare (orange cloud) resolves to Cloudflare's addresses: switch the record
   to DNS only, or use `external`.
2. **Checking over http.** With `DOMAIN_HTTP_PROBE` on, the panel fetches a random answer through
   `http://<domain>/.well-known/acme-challenge/...`, as the authority will. A closed port 80 is reported here instead
   of costing a failed attempt at the authority. Some networks do not let a server reach its own public address:
   turn the probe off then.
3. **Issuing.** The panel asks the authority for a certificate (HTTP-01 challenge, answered by the gateway on port
   80), with a new P-256 key for each certificate. Usually under a minute.
4. **Active.** The gateway serves the domain over https (TLS 1.2 or later, the certificate chosen by the name the
   browser asks for) and redirects http to https. If it is the instance's primary domain, it becomes the instance's
   address in the panel, and Odoo's `web.base.url` follows within a few seconds, so links in e-mails and the portal
   use it.
5. **Renewing.** `CERTIFICATE_RENEW_DAYS` (30) before it expires, the certificate is renewed; the old one is served
   until the new one is in place, and the gateway picks the new one up at the next connection, without a restart.

A failed issuance or renewal is tried again after 5 minutes, then 10, 20 and so on, at most
`CERTIFICATE_RETRY_MAX_HOURS` apart (the authority allows only a few failed checks per domain per hour). Each
failure is an alert (type certificate, e-mail "Certificate not issued"), and a certificate that expires within
`ALERT_CERTIFICATE_DAYS` without being renewed is alerted once a day (e-mail "Certificate expires soon"). Both
e-mails can be changed on the **E-mail templates** page. **Check now** on the Domains tab skips the wait.

### Where things are kept

| What | Where |
| :--- | :--- |
| Certificates and their private keys | `<ODOO_ROOT>/gateway/certs/<domain>.pem`, mode 600, in a folder only root can open; the gateway reads them |
| Challenge answers while a check runs | `<ODOO_ROOT>/gateway/acme-challenge/`, removed after each check |
| The ACME account key | `<ODOO_ROOT>/manager/keys/acme-accounts.json`, encrypted with the vault key |
| The domains and their state | the panel's storage (collection `domains`) |

Removing a domain deletes its certificate and key at once. Deleting an instance removes its domains.

### Permissions

Administrators and super admins add and remove domains and choose the primary one (permission `domains.manage`);
every change is in the audit log. Everyone who can see the instance can see its domains and their state. On a
production instance every domain change must be confirmed, since it moves where the instance's users reach it.

### Managed subdomains

On every plan, while the panel is linked to a Sudoo.sh account that offers them, each instance gets a name under
Sudoo.sh's domain: `<branch>.<stage>.<company>.<base>`, for example `main.production.acme.sudoo.cloud`. Nothing has
to be set up on your side but the network:

* Sudoo.sh creates and keeps the DNS records (A and AAAA) from the server's public addresses, which the panel sends
  with its heartbeat. Behind NAT, set `PUBLIC_IPV4` / `PUBLIC_IPV6` (Settings, Plan).
* Ports 80 and 443 of the server must reach the gateway: the panel gets each name's certificate itself (ACME,
  HTTP-01) and renews it like a custom domain's, whatever `CUSTOM_DOMAINS` says.
* The branch is the instance's branch (its name for plain Odoo), made a DNS label; the stage is its stage. The
  company label is chosen with the account's first name (`MANAGED_SUBDOMAIN_COMPANY`, else Sudoo.sh makes it from the
  company's name) and does not change unless Sudoo.sh support changes it on request; the names then move to the new
  label at the next check (each gets a new certificate, the old names are released). Two instances of the same branch
  and stage share one name: the first keeps it.

Once its certificate is there, the managed name is the instance's address (shown on its **Overview**), unless it has a
primary custom domain. When the instance switches branch or stage it gets the new name and the old one is released;
when it is deleted its name is released. A name Sudoo.sh could not release at once is tried again at the next check.
The plan sets how many names the account may hold; a lower limit after a change of plan removes none, it only refuses
new ones. **Settings, Custom domains, Managed subdomains** shows whether Sudoo.sh offers them, the end of the names,
the company label, how many are used and the addresses sent, with **Check now**. `MANAGED_SUBDOMAINS` turns them off
on this panel ([Configuration](CONFIGURATION.md#custom-domains)).

With several servers ([Servers](#27-servers)), instances on the other servers get their names too. Only the main panel
talks to Sudoo.sh, for every server: it asks for the name with the public addresses of the server the instance runs
on (each agent reports its own, from that server's `PUBLIC_IPV4` / `PUBLIC_IPV6` or the address it sends from), and
Sudoo.sh points the DNS records there. No server ever holds a DNS token. The panel tells each agent the names its
server serves: that server's gateway answers them on its ports 80 and 443, and its agent gets and renews their
certificates (ACME, HTTP-01) on that server. When an instance moves, its name stays the same: it is pointed at the new
server, its certificate goes with it (so it is served at once, without asking for a new one), and the old server stops
serving it. When a server's addresses change, the names of its instances follow at the next check. An instance whose
server reported no public address gets no name until it does (the state says so).

### The panel's own managed name

With `EXPOSURE=direct`, a panel linked to a Sudoo.sh account that offers managed subdomains also gets a name for
itself: `panel.<company>.<base>`, for example `panel.acme.sudoo.cloud`. Instance names always have a branch and a stage
in front of the company label, so the two can never be the same name. It is used only when you have not given the
panel a name yourself:

1. **Panel's name** (`PANEL_DOMAIN`) always wins;
2. else `PUBLIC_HOST`, when it is a name;
3. else the managed name, once its certificate is issued;
4. else `https://<server address>` with the panel's own certificate.

Sudoo.sh creates its DNS records (A and AAAA, from the server's public addresses, following them when they change),
the gateway answers it on ports 80 and 443, and the panel gets and renews its certificate (ACME, HTTP-01) like its
other names. It does not count toward the plan's managed subdomains unless Sudoo.sh says otherwise. **Settings,
Addresses and domains** shows the name and the state of its certificate.

When you set a name of your own, turn `PANEL_MANAGED_NAME` off, turn `MANAGED_SUBDOMAINS` off, log out of the
account, or Sudoo.sh stops offering it, the panel goes back to the previous address (your name, or the server's
address) and the managed name is released. It is checked with the managed subdomains (every
`MANAGED_SUBDOMAINS_CHECK_MINUTES`, and with **Check now**).

---

## 20. Off-site copies

> Off-site copies is a premium feature: it installs automatically from Sudoo.sh when your plan includes it. See
> [Premium features](PREMIUM.md).

Backups on the server's own disk are lost with the server. Off-site copies send them to S3-compatible storage
somewhere else (Cloudflare R2, AWS S3, Backblaze B2, MinIO on another machine), each with a signed list of
checksums, and check them before they are ever used.

### What is copied

While **Off-site copies** is on, after each backup that finished:

* **Production instances:** always, every backup of a kind in `OFFSITE_KINDS` (manual, daily and deletion by
  default; production instances get the daily backup every day).
* **Staging and development instances:** only when `OFFSITE_OTHER_STAGES` is on.

One copy is one backup: its zip (one per database), a `manifest.json` and its signature `manifest.json.sig`, in the
bucket under:

```
<OFFSITE_PREFIX>/<instance>/<YYYYMMDD-HHMMSS>__<kind>/
├── <instance>__<database>__<YYYYMMDD-HHMMSS>__<kind>.zip    the backup, exactly as on the server
├── manifest.json        instance (name and id), stage, kind, time, and each file's name, size and SHA-256
└── manifest.json.sig    Ed25519 signature of manifest.json (64 raw bytes)
```

The zips go first, the signature next, and the manifest last: a folder without `manifest.json` is an unfinished copy.
Every request to the storage carries the SHA-256 of its data, signed, so the storage refuses data changed on the
way. Files above 64 MB go in 64 MB parts. Copies run one at a time, as jobs of the panel itself, so a long upload never
holds up an instance's own actions.

### Setting it up

1. **Create a bucket** at your storage provider, private (no public access). If the provider offers it, turn on
   object versioning or an object lock with a retention of a few days: then even someone holding the access key
   cannot destroy the copies at once (see the trust model below).
2. **Create an access key** for that bucket only, allowed to read, write, list and delete objects. On Cloudflare R2:
   R2, **Manage API tokens**, **Object Read & Write**, limited to the bucket. On AWS: an IAM user with
   `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:ListBucket`, `s3:AbortMultipartUpload` on the bucket.
3. In the panel, **Settings**, **Backups**, **Off-site copies**: fill in the address of the storage's S3 API, the
   region (`auto` for R2), the bucket, a folder (`OFFSITE_PREFIX`), the access key ID and its secret, and save. The
   secret is stored encrypted and never shown again.
4. Choose **Test connection**: the panel writes a small file, reads it back, lists the folder and deletes it.
5. Switch **Off-site copies** on and save. The panel makes its **signing key** now.
6. **Download the public key** (same tab, **Signing key**) and keep it **off the server**: in your password manager
   or next to your other recovery documents. Write down its fingerprint too.

Only super admins change these settings and test the storage (permission `offsite.configure`); every change and
every test is in the audit log.

Typical addresses:

| Storage | `OFFSITE_ENDPOINT` | `OFFSITE_REGION` |
| :--- | :--- | :--- |
| Cloudflare R2 | `https://<account-id>.r2.cloudflarestorage.com` | `auto` |
| AWS S3 | `https://s3.<region>.amazonaws.com` | the bucket's region |
| Backblaze B2 | `https://s3.<region>.backblazeb2.com` | `us-west-004` and so on |
| MinIO | `https://minio.example.com` | `us-east-1` |

### Statuses

The **Backups** page lists, per instance, the backups on this server and the off-site copies, each with a location
badge (**This server** or **Off-site**):

| Status | Meaning |
| :--- | :--- |
| Uploading | Being copied, or waiting for the copy before it. |
| Stored | Complete in the bucket. The storage accepted every file with its checksum. |
| Verifying | Being downloaded and checked. |
| Verified | Downloaded again; the signature and every checksum were checked (when, and with which key). |
| Failed | The copy or its check failed; the reason is shown. The backup on the server is not affected. |

**Verify now** downloads a copy and checks it. The panel also checks the newest copy of each instance by itself when
none was verified for `OFFSITE_VERIFY_DAYS` (7). **Copy again** retries a failed copy from the backup on the server,
if it is still there. **Read the bucket** lists the bucket again: copies the panel does not know (after the server
was rebuilt) are added as stored, and known copies whose manifest is gone are marked failed. The panel reads the
bucket once a day by itself too.

### Retention

Per instance, the panel keeps the newest copy of each of the most recent days that have a copy and of each of the
most recent months, as `restic --keep-daily --keep-monthly` count them:

| | Days | Months |
| :--- | :--- | :--- |
| Production | `OFFSITE_KEEP_DAILY_PRODUCTION` (30) | `OFFSITE_KEEP_MONTHLY_PRODUCTION` (12) |
| Staging and development | `OFFSITE_KEEP_DAILY_OTHER` (7) | `OFFSITE_KEEP_MONTHLY_OTHER` (0) |

The rules run after each copy and once a day. They never remove the newest verified copy of an instance (before any
check: its newest stored copy), copies being uploaded or checked, or failed copies (delete those by hand). Days are
counted only when they have a copy, so the copies of a deleted instance stay until removed by hand. Every removal is
in the audit log ("off-site copy removed by the retention rules").

### Restoring from an off-site copy

On the Backups page, **Restore** on an off-site copy:

1. Choose the instance to restore into (the copy's own by default; another instance of the same Odoo series works
   too, and its data is then neutralised as with any backup of another instance). To restore a deleted or lost
   instance, create an instance of the same Odoo version first, then restore into it.
2. Optionally paste or load a **public key kept off the server**. Use it after the server was rebuilt: the new
   panel has a new signing key, and the copies were signed with the old one.
3. Confirm. The job downloads the copy into `/opt/odoo/backups/.offsite/` (mode 700), checks the signature of the
   manifest, that the manifest is the one of this copy (instance, time and kind), and the size and SHA-256 of every
   file. **Anything missing or different stops the restore**, nothing is changed, and the copy is marked failed with
   the reason. When everything matches, the zip joins the backups on the server (with its original time) and the
   panel's own restore runs: the current state is saved first and **Rollback** is offered for `ROLLBACK_HOURS`.

Restoring needs `offsite.restore` and `instances.restore` (administrators and super admins); a production instance
must be confirmed. Deleting a copy by hand needs `offsite.delete`; the newest good copy of an instance that still
exists cannot be deleted. Both are in the audit log.

**Checking a copy without the panel**, for example on your laptop with the public key you kept:

```bash
openssl pkeyutl -verify -pubin -inkey sudoo-offsite-signing.pub.pem -rawin \
  -in manifest.json -sigfile manifest.json.sig       # "Signature Verified Successfully"
openssl pkey -pubin -in sudoo-offsite-signing.pub.pem -outform DER | sha256sum    # the fingerprint
sha256sum *.zip                                       # compare with "sha256" in manifest.json
```

### Alerts

* **Off-site copy failed** (`ALERT_OFFSITE_FAILED`): a backup could not be copied (the storage refused it, the
  connection failed, the file was removed first) or a copy failed its check. A notice follows when copies of that
  instance work again.
* **No recent off-site copy** (`ALERT_OFFSITE_MISSING`): a production instance has had no successful copy for
  `ALERT_OFFSITE_MISSING_HOURS` (26) while off-site copies are on.

Both e-mails can be changed on the **E-mail templates** page.

### The trust model

The signing key is made on the server and kept like the panel's other secrets: encrypted with the vault key, in
`/opt/odoo/manager/offsite/signing.key` (mode 600); the public key is next to it in `signing.pub.pem`. So:

* **Protected:** someone who can write to the bucket (a leaked access key, a compromised storage account) but not
  to the server cannot change a copy, replace it, or pass a copy off as another instance's or another day's: the
  check fails and nothing is restored.
* **Not protected:** someone with root on the server can read the vault key and sign anything; and someone holding
  the access key can delete copies (versioning or an object lock at the provider limits this). The copies are not
  encrypted by the panel: choose a provider you trust with the data, or one that encrypts at rest.
* **Why the public key off the server:** after the server is lost, the copies are only as trustworthy as the key
  you check them with. A key kept elsewhere, with its fingerprint, is what proves they are yours.

Back up `/opt/odoo/manager/offsite/` with the rest of the panel's data ([The panel itself](#the-panel-itself)); a new
key can always be made, but copies signed with the old one then need its public key to be checked.

---

## 21. Web shell

The **Shell** tab of an instance opens a terminal in the browser inside that instance's containers
([User manual](USER_MANUAL.md#the-shell-tab)): bash in the Odoo container as the container's user `odoo`,
`odoo shell` on the instance's database, or `psql` in its database container. It is on by default for super admins
and administrators. How it is protected is in [Security](SECURITY.md#15-web-shell).

### Who may open one

| Setting (**Settings, Sign-in and security, Web shell**) | Default | What it does |
| :--- | :--- | :--- |
| `SHELL_ENABLED` | `true` | Off: nobody can open a shell, the tab disappears and open shells close within 15 seconds. |
| `SHELL_ROLES` | `admins` | Who may open the Odoo container's terminal and `odoo shell`: `admins` (super admins and administrators), `maintainers` (and maintainers) or `developers` (and developers). Viewers never can. `psql` stays with super admins and administrators. |
| `SHELL_PRODUCTION` | `super_admins` | Shells in production instances: `off` (nobody), `super_admins` or `allowed` (whoever may open shells elsewhere). Each production shell is confirmed when it is opened. |
| `SHELL_ROOT` | `false` | Super admins may choose **As root** for the Odoo container's terminal. The container keeps every limit (capabilities dropped, no new privileges, read-only system files): root there cannot reach the server. |

No API token can open a shell, whatever its scope: a shell is opened by a person in the panel.

### Limits

| Setting | Default | What it does |
| :--- | :--- | :--- |
| `SHELL_IDLE_MINUTES` | `15` | A shell nobody types in for this long closes (a notice a minute before). Output alone, such as `tail -f`, does not keep it open. |
| `SHELL_MAX_MINUTES` | `60` | The longest a shell stays open, typing or not. |
| `SHELL_MAX_PER_USER` | `2` | Shells one person may have open at once. |
| `SHELL_MAX_PER_INSTANCE` | `3` | Shells open in one instance at once. |
| `SHELL_TICKET_SECONDS` | `15` | How long the one-time ticket that connects a new shell stays valid. |

At most 100 shells are open on the panel at once. A shell's programs run inside the instance's container, so the
container's memory, processor and process limits (`ODOO_MEMORY_LIMIT`, `ODOO_CPU_LIMIT`, `CONTAINER_PIDS_LIMIT`,
and the `DB_` ones) apply to them, and they run with a lower priority than Odoo itself (`nice 10`). A heavy command
can still use the instance's memory up to its limit: that slows or restarts that instance, never the others.

When a shell closes, for whatever reason (the person closed it, left the page, lost the connection, the idle or
longest time, an administrator ended it, the panel stopped), every program started in it is ended, also those
started in the background or with `nohup`: each carries the shell's id in its environment, and the panel sends them
SIGHUP, then SIGKILL a second later. A shell left open when the panel crashed is ended the same way when it starts
again.

### Recordings

| Setting | Default | What it does |
| :--- | :--- | :--- |
| `SHELL_RECORDING` | `production` | What is recorded: `off`, `production` (shells in production instances and root shells) or `all`. |
| `SHELL_RECORDING_DAYS` | `30` | Days recordings, and the list of past shells, are kept. |
| `SHELL_RECORDING_MAX_MB` | `5` | Largest recording of one shell. Beyond it only typing is recorded, and the shell closes when typing alone reaches the size again, so nobody escapes the recording by printing a lot. |
| `SHELL_RECORDINGS_TOTAL_MB` | `500` | Space all recordings may take together; the oldest are deleted first. |

A recording holds what was shown and what was typed (passwords typed into programs included), in the asciicast v2
format, each line encrypted with the vault key, in `/opt/odoo/manager/shell-recordings/` (mode 600). Only super
admins see the list of shells (**Shells in this instance** on the Shell tab) and play recordings; each playing is
in the audit log. **Download (.cast)** saves a recording for `asciinema play`.

### The audit log

| Entry | Detail |
| :--- | :--- |
| `shell opened` | What was opened, as which container user, recorded or not, and the shell's id. |
| `shell closed` | How long it was open and why it closed (`exit`, `closed`, `idle`, `max-length`, `not-allowed`, `ended`, `stopping`, `recording-full`, `error`). |
| `shell refused` | A refused ticket or connection, with the reason (no permission, production rules, limits, wrong origin, expired ticket). |
| `shell ended by a super admin`, `shell recording played` | Supervision by super admins. |

### How it reaches the browser

The terminal is a WebSocket on the same server as the live events (`127.0.0.1:EVENTS_PORT`); the web service
forwards `/api/v1/instances/<name>/shell/socket` there. A proxy of your own in front of the panel must pass
WebSocket upgrades (`Upgrade` and `Connection` headers) on that path; Cloudflare tunnels do.

---

## 22. The licence agreement

Sudoo.sh is licensed under the [Sudoo.sh Licence Agreement](https://sudoo.sh/legal/license) (published at
<https://sudoo.sh/legal/license>; each version keeps its own address, such as <https://sudoo.sh/legal/license/v1.0>).
Each release bundles the text of the version current when it was made, and asks for it to be accepted in two places:
the installer, and the panel.

### In the panel

- **First-run setup:** accepting is the first step, before the first account is created ([section 3](#3-the-first-account)).
- **A server installed before 3.0:** after the update, the first super admin to sign in sees **Accept the licence
  agreement** before anything else. Until a super admin accepts, super admins can open only that step, their own
  account and sign out. Other people, API tokens and every instance carry on: instances keep running and serving,
  backups and updates keep their schedule.
- **A new version:** `LICENCE_REQUIRED_VERSION` (**Settings, About**, default `1.0`) is the oldest version that
  counts. When it names a newer version than the last one accepted, super admins see the same step again. It cannot
  name a version newer than the one the running release bundles (update the panel first); one set that way in `.env`
  stops nobody and **Settings, About** says to update.
- **Accepting again** is possible at any time from **Settings, About, Review and accept again**, for example when the
  way you use Sudoo.sh changed. Every acceptance is added; none is replaced.

**Settings, About** shows the version this release bundles, the required version, who accepted, when and the declared
use, every acceptance (the installer's too) with the address and browser it came from, and the full text.

### What is recorded

Each acceptance in the panel is kept in the panel's database (collection `licence_acceptances`), append-only: nothing
in the panel changes or deletes one. It holds the version, the SHA-256 of the exact text shown, the time (UTC), the
address and browser (user agent), the account (user name, e-mail, name, role), the declared use (`personal`,
`company` or `service_provider`), for a company the statement that the person may bind it, the sentences ticked, the
language of the screen, where it was given (`panel_setup` or `panel`) and the panel's version. The audit log also
says **accepted the Sudoo.sh Licence Agreement**. `GET /api/v1/licence` reads them ([Panel API](PANEL_API.md)).

### The installer

Before a fresh installation, and before an update to a release whose bundled agreement differs from the last one
accepted on this server (a different SHA-256), the installer prints a short summary and the address of the exact
version, then asks you to type `accept`. A run without a terminal (a script, CI, `curl ... | sudo bash` without a
terminal) stops with a message unless it is given `--accept-licence` (or `SUDOO_ACCEPT_LICENCE=<version>`, for
example `1.0`). `--yes` never accepts the agreement for you. A rollback asks nothing.

Each acceptance is appended to `/opt/odoo/manager/licence-acceptance.log` (mode 600, root only), one line each:

```
2026-11-18T10:00:00Z version=1.0 sha256=<64 hex> host=<host name> user=<who ran sudo> via=prompt installer=3.0.0 command=install
```

`via` is `prompt`, `flag` or `env`. The panel reads this file and lists its lines under **Settings, About**.

### Sent to your Sudoo.sh account

When the panel is linked to a Sudoo.sh account (**Plugins**, marketplace), it sends every acceptance, the
installer's too, to the hub once: right after linking, after each acceptance and with each heartbeat until they are
taken. Which ones were sent is kept apart (collection `licence_sync`), so the records themselves never change.

**To do:** the hub does not take acceptances yet (`POST /api/v1/installations/licence-acceptances` answers 404).
Until it does, the panel keeps them and tries again with every heartbeat; **Settings, About** shows them as
**Waiting**. Nothing else depends on it.

### Linking the panel within 7 days

A panel is linked to a Sudoo.sh account within `LINK_DEADLINE_DAYS` (**Settings, About**, default `7`) of its first
run. The first run is recorded once in the panel's database (collection `installation`), the first time the panel
starts (for a panel installed before this rule, the first start of the release that has it); no setting, restart or
update changes it. The clock used never goes back, so changing the server's time gives no more days.

- **Until it is linked,** super admins and administrators see a banner on every page that cannot be closed: **Link
  this panel to your Sudoo.sh account**, with the days left. Super admins have the **Link this panel** button
  (**Settings, Marketplace**: turn the marketplace on, then link with the code from the account site); administrators
  are told that a super admin links it. Other roles see nothing.
- **After the deadline, while it is not linked,** managing is limited: no new instances (by hand, through the API, or
  for a new branch), no stage change into production (an instance or a branch), and no updates or deploys (get
  latest code, submodules, switch branch, connect a repository, upgrade modules, new images, rebuild staging;
  pushes do not update instances either). Each refusal says why and is in the audit log. Nothing stops serving:
  instances keep running and serving, and start, pause, restart, backups (daily and by hand), restores, monitoring and
  certificates go on.
- **Linking lifts the limits at once.** Logging out of the account, or turning the marketplace off, makes the panel
  unlinked again; past the deadline the limits are then back.

The licence acceptance and the link flow itself are unchanged.

---

## 23. Mail catcher

Like Odoo.sh, staging and development instances never send real e-mail: a test that confirms an order, resets a
password or runs a mass mailing must not reach customers. Their e-mail goes to the **mail catcher**, a small SMTP
service in its own container, `sudoo-mail`, and each instance's **Mail** tab shows what it sent
([User manual](USER_MANUAL.md#the-mail-tab)). Production instances keep their own mail settings and are never
touched. How it is protected: [Security](SECURITY.md#16-mail-catcher).

### How e-mail reaches it

* **The service.** `sudoo-mail` runs the panel's own image (`sudoo.sh mailcatcher`), started with the others by
  `docker compose up -d`. It has no Docker access, no `.env`, no published port, read-only system files and every
  capability dropped, and its own network has no way out. It writes only `/opt/odoo/mail`.
* **The network.** The panel connects `sudoo-mail` to the network of each staging and development instance's Odoo
  container (`odoo-<name>_default`; the database is on another network) and disconnects it from production
  instances, within 30 seconds of a change and at once when an instance starts.
* **odoo.conf.** For staging and development the panel writes `smtp_server = sudoo-mail`, `smtp_port = 2525`,
  `smtp_user = <instance>` and a password of the instance's own (`.env`, `MAIL_CATCHER_PASSWORD`); production's
  `odoo.conf` has none of these.
* **Odoo's own mail servers.** Odoo uses an outgoing mail server set up in its database (Settings, Technical,
  Outgoing Mail Servers) before `odoo.conf`'s, so the panel switches every one of them off in staging and
  development instances when they start, when they leave production, and every 5 minutes while they run. The
  instance's job notes say when it did. Restoring another instance's data still neutralises it (no scheduled
  actions, no mail servers), as before.

An instance knows only its own SMTP password, and the catcher files each e-mail in the mailbox of the instance whose
password was given: no instance can put e-mail in another's mailbox, and the panel shows each instance only its own.

**Changing the stage.** Out of production: the instance's mail servers are switched off at once, so nothing goes
out; Odoo sends to the catcher after its next start (Restart). Into production: `odoo.conf` loses the catcher's
settings and its caught e-mails are deleted; switch its own mail server back on in Odoo (Settings, Technical,
Outgoing Mail Servers) and restart it.

### Settings

**Settings, E-mail, Mail catcher** ([Configuration](CONFIGURATION.md#mail-catcher)):

| Setting | Default | What it does |
| :--- | :--- | :--- |
| `MAIL_CATCHER_RETENTION_DAYS` | `7` | Caught e-mails older than this are deleted (checked every 5 minutes). |
| `MAIL_CATCHER_MAX_MESSAGES` | `500` | E-mails kept per instance; the oldest go first. |
| `MAIL_CATCHER_MAX_MB` | `200` | Space per instance; the oldest go first. |
| `MAIL_CATCHER_MAX_MESSAGE_MB` | `25` | A larger e-mail is refused: Odoo shows it as not sent, with the catcher's answer. |
| `MAIL_CATCHER_RELEASE` | `true` | Administrators may **Release** a caught e-mail (below). |

### Who sees what

| Permission | Roles | What it allows |
| :--- | :--- | :--- |
| `caught_mail.view` | developers, maintainers, administrators, super admins | The Mail tab: list, search, read, download the `.eml` and attachments. |
| `caught_mail.manage` | administrators, super admins | Delete caught e-mails (one or all) and release them. |

**Releasing** sends a copy of a caught e-mail to one address typed in the panel, for example to check how an
invoice looks in a real mailbox. The copy keeps the content and attachments; it is sent through the panel's own
e-mail settings (**Settings, E-mail**), from `MAIL_FROM` (sending as the original sender would fail SPF and DKIM),
with the original sender as Reply-To, `[<instance>]` before the subject and an `X-Sudoo-Released-From` header.
Every release, and every refused one, is in the audit log with the address. Set `MAIL_CATCHER_RELEASE=false` to
make sure nothing caught can ever leave the server.

### When the Mail tab stays empty

| Check | How |
| :--- | :--- |
| The catcher runs | `docker ps` lists `sudoo-mail` as healthy; else `cd /opt/odoo/manager && docker compose up -d mail`. A `compose.yaml` from before 3.0 has no `mail` service: take the release's one. The Mail tab says when the catcher is not running. |
| It is on the instance's network | `docker inspect sudoo-mail --format '{{json .NetworkSettings.Networks}}'` lists `odoo-<name>_default` (the panel adds it within 30 seconds). |
| Odoo sends to it | The instance was restarted since it left production; in Odoo, Settings, Technical, Emails, the e-mail's state and its failure reason. |
| Code that opens its own SMTP connection | Not caught: only Odoo's own mail sending goes through `odoo.conf` and `ir.mail_server`. A module that connects to an SMTP server by itself is not stopped (the instances can reach the Internet). |

---

## 24. Projects and branches

A **project** is one GitHub repository, as on Odoo.sh: it links an existing repository, or the panel creates a
new, totally empty one in an organisation ([Creating repositories for new projects](#creating-repositories-for-new-projects)).
Its page lists every branch of the repository with the
branch's stage (production, staging or development), its instances and its latest commit. Every instance of a
repository belongs to that repository's project. What people do with projects is in the
[User manual](USER_MANUAL.md#2-projects); this section is for the server.

### Existing instances

When the panel starts, every instance that is in no project joins its repository's project, and a project is
created for each repository that has none ("adopted"; its Odoo version and edition are those of its production
instance, else its staging instance, else its newest instance). Only `PROJECT=<id>` is added to the instance's
`.env`: its name, stage, address, backups, containers and settings stay as they are, and **nothing restarts**. The
stage of each branch is recorded from its instances (the highest when several instances follow one branch; each
instance keeps its own stage). Running it again changes nothing. The audit log has "project created from existing
instances" and "instance joined a project" for each.

**Plain Odoo stays standalone.** An instance created without a repository has no branch and no code, and a project
is one repository, so it joins no project: the Projects page lists such instances under **Without a project**, and
the All instances page shows them as before. When someone gives one a repository (**Connect a repository**), it joins
that repository's project. A made-up "Plain Odoo" project was not chosen: it would have branches that do not exist,
and its rules (one production branch, staging from production) would mean nothing.

### What each stage does

The rules of [Instance stages](#instance-stages) apply to every instance; projects add:

| | Development | Staging | Production |
| :--- | :--- | :--- | :--- |
| New instance of the branch | a fresh database (with demo data when `PROJECT_DEVELOPMENT_DEMO_DATA`) | a neutralised copy of the project's production instance | a fresh database, without demo data |
| A push to the branch | by the branch's **Behaviour upon new commits** ([Build options and tests](#build-options-and-tests)): new code (`AUTO_UPDATE`) by default | with `PROJECT_STAGING_ON_PUSH=rebuild`: a new neutralised copy of production, then the new code; `keep_data`: new code only (a branch can choose its own behaviour) | new code only when asked, or with its automatic updates switched on; always backup + automatic rollback |
| The branch deleted on GitHub | `PROJECT_BRANCH_DELETED` (keep, archive, delete) | archived at most | marked only |
| How many per project | any | any | `PROJECT_PRODUCTION_BRANCHES` (1) |

Changing a branch's stage moves every instance of the branch to that stage, with the same checks as changing one
instance's stage: making a branch production needs a confirmation and is refused beyond `PROJECT_PRODUCTION_BRANCHES`
production branches in the project and beyond `MAX_PRODUCTION_INSTANCES` production instances on the server; leaving production needs a confirmation too. Changing one instance's stage on its
own page records that stage for its branch.

### Staging from production

A staging instance never starts from an empty database: creating it, and rebuilding it, start from a new backup of
the project's production instance (kind `staging-copy`; each production instance keeps only its latest one, and
the production instance keeps serving while it is taken; no update or restore runs on it meanwhile). The copy is then
neutralised:

* **E-mail:** Odoo's mail servers are switched off and the instance sends to the [mail catcher](#23-mail-catcher)
  (every instance that is not production does).
* **Scheduled actions:** switched off (except Odoo's own database clean-up).
* **Payment providers, IAP and other services that act on the outside world:** Odoo's `neutralize` (Odoo 16 and
  later) puts them in test mode or switches them off and marks the database as neutralised; older versions get the
  same for mail servers, scheduled actions and payment providers in SQL.
* **Identity and address:** a new database identity (`database.uuid`, `database.secret`), the instance's own admin
  password, and `web.base.url` set to the staging instance's address.

On a rebuild the branch's latest code is downloaded first, then the repository's modules that are installed in the
copy are upgraded, so staging shows production's data with the branch's code. A rebuild replaces the staging
instance's database and files without a backup of them: staging data is meant to be thrown away. Use
`PROJECT_STAGING_ON_PUSH=keep_data` (or a project's own option) when testers keep work in staging.

### New and deleted branches

A project learns that a branch was created or deleted from the GitHub App's webhook (create, delete and push events)
or the `github-webhook` trigger (push events) within seconds, and, as a safety net that needs nothing from GitHub,
from a look at every project's branches every `POLL_MINUTES` (`git ls-remote`, through the app, a deploy key or
anonymously). Each branch is handled once, whichever tells first. A project made from existing instances records its
branches at its first look without creating anything, so turning projects on never creates an instance for every
existing branch.

* **New branch:** with `PROJECT_AUTO_DEVELOPMENT` (or the project's own option) a development instance is created,
  as the user `system`, unless the branch has an instance or another stage recorded, the project has
  `PROJECT_MAX_AUTO_DEVELOPMENT` instances it created this way already, or the server is at `MAX_INSTANCES` or
  `MAX_RUNNING_INSTANCES`. A refusal is in the audit log ("no instance for a new branch") with the reason; the
  project page still offers **Create instance**.
* **Deleted branch:** its instances show that the branch is gone. With `archive` a development instance is backed
  up and switched off (it can be started again, and its lifetime still ends it); with `delete` it is deleted after a
  backup. Staging is archived at most and production is never touched. A busy instance is left as it is.

### Build history

Every branch of a project keeps a history, as on Odoo.sh. A **build** is recorded each time an instance of the
branch gets code from it: when the instance is created, when a push updates it (or rebuilds staging from production),
when someone uses **Get latest code** or **Update submodules**, and when someone rebuilds staging by hand. A build
shows the commit (sha, message, author), who or what started it, when it was queued, started and finished, how long
it took, the instance, the job (whose log you can open) and a short result:

* **building** while its job runs;
* **success** when the job succeeded;
* **warning** when the job succeeded but Odoo's log has `WARNING` or `ERROR` lines while loading the modules (the
  first ten are kept with the build), or the job left a warning, such as modules that could not be upgraded;
* **failed** when the job failed; an update that was put back keeps the instance on its previous build, which stays
  live;
* **dropped** when the instance moved on: a newer build of it succeeded, it follows another branch now, or it was
  deleted. Only the latest build of an instance is live. A build cut off by a restart of the panel is dropped too.

An update that finds nothing new on GitHub is not a build. Between the builds, the history shows each change of the
branch's stage (who moved it, from which stage to which, and what the new stage does), including the moves made by a
database upgrade and by a plan that no longer allows a production branch, and each restore of a backup into one of
its instances.

Each branch keeps its last `PROJECT_BUILDS_KEPT` builds (200 by default; **Settings**, **Projects**). The history is
kept in the panel's storage (collection `build_history`) and removed with its project. Reading it needs what
reading the project needs; branches hidden from someone keep their history from them too
([API](PANEL_API.md#projects): `GET /projects/<id>/builds`, `GET /projects/<id>/branches/<branch>/history`).

### Build options and tests

Each project has **build options**, as Odoo.sh's "New build options", and each branch can change them for itself
(project **Settings**, **Development branches defaults**; a branch's **Branch settings** tab; [API](PANEL_API.md#projects):
`GET/PUT /projects/<id>/branches/<branch>/options`). A branch's value wins over the project's, and the project's over
the panel's settings ([Configuration](CONFIGURATION.md#projects)):

| Option | Choices | Default |
| :--- | :--- | :--- |
| Behaviour upon new commits (`on_new_commit`) | `update`: the new code in the same database, the changed modules upgraded; `new_build`: development builds a new database from scratch (the old one and its files are replaced), staging is rebuilt from a new neutralised copy of production; `nothing`: the commit is only recorded in the branch's history | development: `PROJECT_DEVELOPMENT_ON_NEW_COMMIT` (`update`); staging: `PROJECT_STAGING_ON_PUSH` (`rebuild` is `new_build`) |
| Module installation (`module_install`) | `repository`: every module of the repository; `all`: every module Odoo has, with the repository's (localisations, `test_*`, `hw_*` and `theme_*` modules are left out, and modules Odoo marks as not installable; tests are off); `list`: only `modules` | `PROJECT_MODULE_INSTALL` (`repository`) |
| Test suite (`test_suite`) | `off`; `selection`: the tests of the repository's installed modules; `custom`: `test_tags` in Odoo's `--test-tags` syntax | `PROJECT_TEST_SUITE` (`off`) |
| Demo data (`demo_data`) | new development databases get Odoo's demo data | `PROJECT_DEVELOPMENT_DEMO_DATA` |

Module installation and demo data apply to new development databases (a new instance, or a `new_build`). Demo data
uses the flag each version knows: Odoo 19 and later load it with `--with-demo`; Odoo 18 and earlier load it unless
told `--without-demo=all`. Production
is never built by these options: its new code always comes through the safe update with a backup and automatic
rollback, and its tests never run on its database.

**How the tests run.** When a build creates or updates an instance of a development branch whose test suite is not
`off`, the panel stops Odoo and runs, in a one-off container of the instance's own image,
`odoo -d <database> --test-enable --stop-after-init --no-http -u <the repository's installed modules>` with
`--test-tags=/<module>,...` (`selection`) or `--test-tags=<test_tags>` (`custom`); Odoo 15 and earlier run the tests
of the upgraded modules instead (no `--test-tags`). Only then is the instance started. Staging runs them too when the
project's **Tests on staging** (`PROJECT_STAGING_TESTS`) is on or the branch sets its own test suite, on a
**throw-away copy** of the staging database and its files (a PostgreSQL template copy, removed afterwards), so the
tests never touch the data testers work with. A template copy needs the source database to itself: the panel stops
Odoo and ends any other session on it first. Tests that take longer than `PROJECT_TEST_TIMEOUT_MINUTES` (30) are
stopped.

**The result.** The panel reads Odoo's log of the run (checked against real runs of Odoo 17, 18 and 19): the
"N failed, N error(s) of M tests" summary of `odoo.tests.result`, which counts the post-install tests too, the
"Module x: N failures, N errors of M tests" lines, the failing tests (`ERROR` lines of `odoo.addons.<module>.tests`,
with the exception that ended them), `ERROR` lines of `odoo.modules.loading` and `unittest`, and warnings (each
distinct line once). Odoo exits with code 1 when a test fails. The build is then **failed** with "At least one test failed when loading the modules (module_x)" and the
first ten error lines, **warning** when the tests passed with warnings in the log, or **success** with the number of
tests that passed. A failed test does not stop the instance: it starts with the new code, and the build shows that it
failed, as on Odoo.sh.

### Commit statuses

The result of each build is shown on GitHub next to its commit (pull requests show it in their checks), when the
GitHub App may write statuses and the project's **Commit statuses** (`PROJECT_COMMIT_STATUSES`) is on:

* `pending` ("Building") when the build starts, `success` when it ends with success or warning, `failure` when a test
  failed, `error` when the build failed otherwise;
* the context is `sudoo/<stage>` (`sudoo/development`, `sudoo/staging`, `sudoo/production`), and **Details** opens
  the branch's history in the panel (`<panel address>/projects/<id>/branches/<branch>`);
* GitHub is told in the background: a slow or refusing GitHub never holds or fails a build (the panel's log says
  when a status could not be set).

**The permission.** Apps connected from this version on ask for **Commit statuses: Read and write**. For an app
connected earlier: on GitHub open the app's settings, **Permissions and events**, **Repository permissions**, set
**Commit statuses** to **Read and write** and save; then an owner of each account it is installed on accepts the new
permission on the installation's page (GitHub sends them an e-mail). The panel sees the accepted permission at its
next look at the installations (within a minute of opening GitHub access, or **Refresh**). Until then no status is
set, and the project's branch options say so (`commit_statuses.available`).

### Many branches at once

Every running instance takes memory and processor time, so many branches running in parallel slow every instance.
The project page shows the server's capacity estimate ([How many instances can my server run?](#how-many-instances-can-my-server-run))
and warns when one more running instance would go beyond it; development instances pause when idle, which keeps
the number down. `MAX_RUNNING_INSTANCES` turns the warning into a limit.

### Permissions and data

Seeing projects needs `instances.view`; creating and linking projects, changing their options and the stages of
their branches need `projects.manage` (maintainers, administrators, super admins); creating an instance of a branch
needs `instances.create`, a staging one and a rebuild also `instances.restore` (they replace data with production's).
Projects are documents in the panel's storage (collection `projects`); each records its access (below).

### Teams, project access and locked branches

> Teams and access per project and branch (branch locks are on every plan) is a premium feature: it installs automatically from Sudoo.sh when your plan includes it. See
> [Premium features](PREMIUM.md).

Teams (**Teams** in the sidebar, `teams.manage`), each project's **Access** tab and branch locks (`projects.access`)
are for administrators and super admins. What people see is in the
[User manual](USER_MANUAL.md#project-access-and-locked-branches); the rules are in
[Security](SECURITY.md#teams-project-access-and-locked-branches). For the server:

* **Upgrading locks nobody out.** Every project made before teams existed has the mode "panel roles": everyone keeps
  the access their role gives, until an administrator adds grants, restricts the project, restricts a branch or
  locks one.
* **Super admins** see every project and branch whatever its grants, so a project nobody else can reach can always be
  repaired. A lock applies to them too, until they unlock the branch (always allowed, audited).
* **Production.** A locked production branch keeps serving, never pauses and keeps its daily backup; only people
  are kept out. Restricting the production branch to a team ("production for the leads") hides it from everyone
  else, its instances included.
* **Automatic work on a locked branch stops:** no update or staging rebuild on a push (the audit log has "automatic
  update skipped (branch locked)"), no deletion when its lifetime ends, nothing when it is deleted on GitHub, no new
  instance for it, and a visit to a paused instance shows a "Locked" page instead of starting it. Data of a locked
  production branch is not copied into staging, by hand or on a push (a push then only brings new code).
* **API tokens** act for the account that created them; tokens made with `sudoo.sh create-token` belong to no
  account and are kept out of restricted projects and locked branches. Give a CI pipeline a token created by an
  account that has the access it needs.
* **Data.** Teams are documents in the collection `teams`; a project's grants, branch rules and locks are in its
  `access`. Deleting an account or a team removes it from every team, grant and lock.

### The plan's limits

Teams and access per project are features of the Pro and Business plans; locked branches are on every plan. The
plan comes from Sudoo.sh in the signed licence (`entitlements.py`); what people see is in the
[User manual](USER_MANUAL.md#plans-and-limits). For the server:

* **Production branches** are counted on the whole panel: each project's production branches (`<project>/<branch>`)
  and each production instance outside a project (`<instance>`), the names the heartbeat reports to Sudoo.sh
  (`usage.production_branch_names`). The plan's number applies on top of `PROJECT_PRODUCTION_BRANCHES` (per project)
  and `MAX_PRODUCTION_INSTANCES` (per server).
* **Not linked** (or `MARKETPLACE` off): the Free plan of the licence. **Linked:** the licence's entitlements, trusted
  until their `valid_until` (24 hours after the last heartbeat by default) while the hub cannot be reached; after
  that the features stay as they were and only new production branches wait.
* **After a downgrade** the licence's `production_keep` names the production branches this server keeps; at the
  heartbeat that brings it, the panel moves every instance of the other production branches to development (its
  `STAGE`, the branch's recorded stage; lifetime "keep", automation and data unchanged; a busy instance waits for the
  next heartbeat), with an audit entry "production branch left production (plan)" and an alert of kind `plan`.
* **The hub's daily API quota.** Calls the panel makes for the account (the catalogue, installs, checkout) count
  toward it; past it the hub answers 429 until midnight UTC, and the panel stops sending them until then (Plugins
  says when they work again). Heartbeats, registration, licence checks, licence acceptances and command
  acknowledgements never count, so licences and plans keep working.

---

## 25. Database upgrades

People's side: [User Manual](USER_MANUAL.md#upgrading-to-a-newer-odoo-version). Settings:
[Configuration](CONFIGURATION.md#database-upgrades). Permission: `instances.upgrade` (administrators and super
admins); upgrading or cancelling on a production instance also needs `"confirm_production": true` in the API.

### How it works

* **Targets.** The next major version this panel offers (`ODOO_VERSIONS`), for example 18.0 to 19.0. Odoo's service
  can skip versions, so an Enterprise database may also go further; OpenUpgrade goes one version at a time.
* **The test** creates a staging instance named `<instance>-odoo<version>` from a new backup of the instance (kind
  `staging-copy`), on the target version, with the repository's upgrade branch (named after the target) or plain
  Odoo when there is none. It belongs to the project and follows that branch. **Run the test again** reuses it.
* **The promotion** runs as one job on the instance itself (kind `db-upgrade`): Odoo stopped (the gateway shows
  **Upgrading**), backup kind `before-upgrade` (the instance keeps its latest one), the method on the live data, the
  code switched to the upgrade branch, the image switched to the target version (pinned by digest; Enterprise
  add-ons of the target prepared by the Enterprise source), the database's version checked, Odoo started and waited
  for. Any failure puts back the image, the add-ons, the commit and the backup, and starts Odoo again. The project
  records the upgrade branch as its production branch.
* **The rollback window.** For `UPGRADE_ROLLBACK_MINUTES` the instance offers **Cancel the upgrade**, and the panel
  checks Odoo every 30 seconds: after two minutes without an answer (not while a job runs, not when paused or
  switched off) it rolls back by itself and audit-logs it. A cancel saves the upgraded state first (`before-rollback`).
* **Audit.** Every pre-check, test, promotion, failure, cancel and automatic rollback is in the audit log
  (`database upgrade: ...`) and in the upgrade's own history on the Upgrade tab.

### The methods and what the server needs

* **Odoo's upgrade service (Enterprise databases).** A temporary container of the instance's PostgreSQL image, on the
  instance's database network and with Internet access, installs python3, rsync and ssh, downloads Odoo's upgrade
  script from `UPGRADE_SERVICE_URL/upgrade` at that moment and runs it as Odoo documents it (`test` or `production`,
  `--no-restore`). The script dumps the database, sends it to Odoo's platform with the customer's subscription code
  and downloads the upgraded dump; the panel loads it and merges the new filestore files. The server must reach
  Odoo's platform on port 443 and on a TCP port between 32768 and 60999. The code is passed in a file the container
  deletes when it has read it, masked in the job's log, and never stored. Using the service needs a valid Odoo
  Enterprise subscription for the database, and it is subject to Odoo S.A.'s terms (Enterprise Subscription
  Agreement, Upgrade Services); the panel asks people to accept that each time.
* **OpenUpgrade (Community databases).** A temporary container of the instance's own Odoo image of the target version
  downloads OpenUpgrade's sources from `OPENUPGRADE_REPOSITORY` at the commit pinned in `OPENUPGRADE_REFS`, installs
  `OPENUPGRADE_LIB` in its own `/tmp`, and runs Odoo with OpenUpgrade's framework and migration scripts (`-u all`).
  It needs Internet access to GitHub and PyPI. Move a pinned commit forward only after reading OpenUpgrade's changes
  for that version.
* **Disk.** An upgrade needs room for a backup and, with Odoo's service, a dump and the upgraded dump at once (in
  `<ODOO_ROOT>/upgrades/work`, removed after the job).

### Honest limits

* Custom modules must be ported by developers, in the upgrade branch. The pre-check only reads manifests: a module
  marked **Ready** may still fail its upgrade.
* Enterprise upgrades need a valid subscription and follow Odoo's terms; Odoo's platform decides how long it takes,
  and a failed request is explained on Odoo's side (its report is in the job's log).
* OpenUpgrade's coverage varies by version and module (see its documentation); Enterprise modules are not covered,
  which is why Enterprise databases use Odoo's service.
* Data changed in production after the final backup is part of the upgrade; data changed after the upgrade is lost
  by cancelling it (the upgraded state is kept as a `before-rollback` backup).
* With Odoo's service, a push to the upgrade branch does not run the test again by itself (the code is not kept).

### Third-party software (notice)

The panel bundles, copies and imports none of the following; it starts them as separate, unmodified programs in
temporary containers that download them when an upgrade runs:

* **OpenUpgrade** by the Odoo Community Association (OCA), licensed under the GNU Affero General Public License
  v3.0 (AGPL-3.0); source: https://github.com/OCA/OpenUpgrade. Run unmodified, at the commit pinned in
  `OPENUPGRADE_REFS`.
* **openupgradelib** by the OCA, AGPL-3.0; source: https://github.com/OCA/openupgradelib (installed from PyPI).
* **Odoo's upgrade script and upgrade service** by Odoo S.A., downloaded from Odoo's own address at run time and
  used under the customer's Odoo Enterprise subscription and Odoo S.A.'s terms.

---

## 26. Support access

> Support access is a premium feature: it installs automatically from Sudoo.sh when your plan includes it. See
> [Premium features](PREMIUM.md).

A super admin or administrator can let Sudoo.sh support into the panel for a while, to look into a problem with
them: **Settings › Support access** (super admins) or **Support access** in the side bar (administrators; the
permission `support_access.manage`). It needs the panel connected to Sudoo.sh (`MARKETPLACE` on) and
`SUPPORT_ACCESS` on (the default).

### Granting

A grant says:

| Choice | What it means |
| :--- | :--- |
| **View** | See instances, their activity and logs (the viewer role). |
| **Panel admin** | What an administrator does to instances: restart, update, back up, change, restore, delete. Never accounts, API tokens, teams, project access and locks, GitHub, the panel's settings, plugins or support access itself, and no shells. |
| **Panel admin + shell** | The same with terminals in instances' containers (never as root). Every support shell is recorded, whatever `SHELL_RECORDING` says; production shells follow `SHELL_PRODUCTION`. |
| **For how long** | 1 hour, 1 day or 3 days, up to `SUPPORT_ACCESS_MAX_HOURS`. It ends on its own then. |
| **Where** | All projects and instances (restricted projects and branches stay closed), or chosen projects and instances (a chosen project's restricted branches stay closed; a chosen instance is open whatever restricts it). **Locked branches stay locked** in every case: support is never allowed by a lock and cannot unlock one. |
| **Ticket** | Optional: the Sudoo.sh support ticket it is for. |

**Withdraw** ends one grant at once; **End now** (on the banner every page shows while support is connected) ends
every grant and every support session. Switching `SUPPORT_ACCESS` off ends them all too. At most 20 grants are active
at once.

### How support reaches the panel

Nothing on the server is opened. While a grant is active, the panel keeps asking the Sudoo.sh hub for work over the
connection it already has (the same signed link as the heartbeat: every call signed with the installation key, every
answer checked against the hub's keys), about every 20 seconds while nothing happens. A support person at Sudoo.sh
opens a **session** on the grant with a fresh code from their authenticator app; their requests come to the panel
through that call, and the panel carries each out against its own API as **"Sudoo.sh support (their name)"** with
exactly the grant's level and scope, then sends the answer back. Before each request it checks again that the grant
is active and the session open; withdrawing works at once on the panel, and the hub hears of it within a second or
two. When no grant is active the panel does not call for support at all.

### What you see

* **The banner** "Sudoo.sh support is connected (name)" on every page while a session is open, with **End now** for
  those who manage support access.
* **The activity log**: every request support makes that changes something or is refused, sessions starting and
  ending, shells opened and closed, all under "Sudoo.sh support (name)" with a **Support** tag; granting, withdrawing
  and "End now" under the admin's name; expiry under `system`.
* **Shells**: support's are in the list of past shells and recorded like any other (super admins play them).

Support access runs inside the panel: when the panel itself is down, it cannot help.

---

## 27. Servers

> Several servers is a premium feature of Business plans: it installs automatically from Sudoo.sh when your plan
> includes it. See [Premium features](PREMIUM.md).

One panel can manage instances on several servers: the **main server** (where the panel runs) and **other
servers**, for example one in another region, closer to a customer. Each other server runs a small agent
(`sudoo-agent`, the panel's own image) with its own gateway and mail catcher. How many servers you may have comes
from your plan (the main server counts), shown on **Servers**. Without several servers, every instance runs on this
server, shown under its name (`SERVER_NAME`, `SERVER_REGION`).

### Adding a server

1. On the new server: Ubuntu or Debian with ports 80 and 443 open for its visitors. It needs no other open port: the
   agent connects out to the panel.
2. In the panel, **Servers**, **Add a server**: give it a name, a region (free text, for example `Riyadh` or
   `me-central`) and, if you want, the most instances it may take. The panel shows a **join code** and the exact
   command to run, once. The code works once and for `SERVERS_JOIN_CODE_MINUTES` (30); **New join code** makes another.
3. On the new server, run the command as root:

   ```bash
   curl -fsSL https://github.com/Sudoo-sh/sudoo.sh/releases/latest/download/sudoo-install.sh \
     | sudo bash -s -- agent --join '<join code>'
   ```

   The installer checks the server, installs Docker if you allow it, asks you to accept the licence agreement and
   starts the agent. The agent fetches the panel's premium packages with the join code (each checked like on the
   panel: the premium key's signature, and a binding to your panel), joins, and prints the server's
   **fingerprint** (for example `3F2A-91C0-7D4E-B812`).
4. In the panel the server shows **Waiting for approval** with **Approve**. Type the fingerprint the server printed.
   It must match the key that used the join code: if it does not, someone else may have used the code; revoke the
   server and add it again. Once approved, the server is **Online** within seconds.

The agents reach the panel at its public address. If they must use another address (a private network, a fixed
domain), set **Address for other servers** (`SERVERS_PANEL_URL`) before you create the join code.

### Where new instances go

**New instance** asks which server the instance goes to; without a choice it goes to the project's **Default
server** (project settings), else to the main server. A server must be online and have room. Each instance shows its
server on its page. Automatic development instances for new branches go to the project's default server.

Good to know:

* Starting an instance from a backup works on the main server; to start on another server, create it on the main
  server and move it.
* A staging copy of production is made on the server of the production instance.
* Each server needs its own Enterprise add-ons (`ENTERPRISE_SOURCE` in its `.env`) and its own exposure. Private
  repositories are read through the panel's GitHub App: the server asks the panel for a short-lived token when it
  needs one. Deploy keys stay on the main server.
* The panel's instance settings (pausing, backups, time zone, stages, resources, images, shells, mail catcher)
  apply on every server; its own secrets never leave it.

### Moving an instance

On the instance, **Move to another server** (permission `instances.move`): choose the server and confirm (production
asks again). The move is a job:

1. the instance is switched off (its visitors see the "switched off" page: **the downtime starts**);
2. it is backed up on its server;
3. the backup is copied to the other server through the panel and checked (size and SHA-256);
4. the instance is created there from the backup: same name, stage, project, branch, version, database and admin
   password; the data is not neutralised;
5. its custom domains go to the new server, each with the certificate it is served with (over the servers' signed
   channel): the new server's gateway serves them at once and the old one stops;
6. its managed name (if it has one, [Managed subdomains](#managed-subdomains)) is pointed at the new server, with its
   certificate, and it is deleted on its old server (which keeps the deletion backup).

The downtime lasts about as long as a backup, the copy and a restore of that instance. When a step fails, the move is
rolled back: what was made on the target is removed and the instance starts again where it was. Its managed name stays
its address; an address given by the exposure (a tunnel link or a port of the server) changes with the server.

Its custom domains stay its domains (the primary one stays its address), but their DNS records still point at the old
server: whoever manages each domain's DNS must point it at the new server. The move's job says so for each domain, and
on the instance's **Domains** tab each moved domain shows "moved here from <server>", the reason (for example "still
points at the old server ... Update its DNS to A 203.0.113.7") and the record to create. The new server checks the
domain on the DNS back-off (and on **Check now**); once it points there the note goes, and a domain that came without
a valid certificate gets its own. A domain the new server already has for another instance is not taken (the job
warns). Each server keeps its own `DOMAIN_DNS_TARGET` (the main panel does not pass it on to other servers); a server
without one (nor `PUBLIC_HOST`) tells people to point domains at its public address. Limits: `MOVE_MAX_BACKUP_GB`,
`MOVE_AT_ONCE`, `MOVE_CHUNK_MB` ([Configuration](CONFIGURATION.md#servers)).

### When the main panel is down

Every other server keeps working on its own: its instances keep serving, paused instances wake when someone opens
them, daily backups run, certificates are renewed, idle instances pause and expired ones are deleted, with the
panel's last settings. Nothing can be changed on them meanwhile, and a job that needs the panel (reading private
code from GitHub) waits up to `SERVERS_WAIT_MINUTES`, then fails with a clear message. When the panel is back, the
agents reconnect within a minute and the Servers page catches up.

When another server is down, its instances stay listed with the status **Unreachable**; the panel refuses actions on
them until the server is back.

### Revoking and removing

**Revoke** (type the server's name) closes its connection at once and refuses its key from then on; its instances
keep running there but the panel can no longer reach them. On the server, `sudo sudoo-install agent leave` stops and
removes the agent and the premium packages it fetched (its instances are kept unless you ask to delete them).
**Remove** takes a revoked server, or one that never joined, off the list; when the panel still knows instances on
it, move or delete them first, or remove it with **Forget its instances**.

On a server: `sudo sudoo-install agent status` shows whether it joined, whether it is approved and connected, its
fingerprint and the panel's.

### When the plan no longer includes several servers

The other servers keep running their instances on their own, but the panel no longer reaches them: new instances go
to the main server, and their instances are not listed. When the plan includes several servers again, everything
comes back as it was.

---

## 28. Code profiler

The **Tools** tab of an instance records, for 1 to `PROFILER_MAX_MINUTES` minutes, what its Odoo processes spend
their time on, and shows the result as a flame graph ([User manual](USER_MANUAL.md#the-tools-tab)), as the Tools
tab of Odoo.sh does. Nothing in the instance is restarted or changed.

### How it records

1. The panel makes sure the profiler image is on the server. With `PROFILER_IMAGE` empty (the default) it builds
   `sudoo-profiler:py-spy-<version>` the first time, from `python:3.12-slim` and the py-spy wheel from PyPI, pinned
   to one version and checked against its sha256 (`pip install --require-hashes`); this needs the server to reach
   Docker Hub and PyPI once. Set `PROFILER_IMAGE` to an image of your own (with `sh` and `py-spy`) to avoid that.
2. It checks that the instance's Odoo container is running and is the one the panel made for it (name and labels).
3. It starts a one-off sidecar container, `sudoo-profiler-<instance>`, that joins the PID namespace of the Odoo
   container (`--pid=container:odoo-<instance>-odoo-1`) with only the `SYS_PTRACE` capability, no network, no new
   privileges, 256 MB of memory, one processor and 64 processes. It finds the Odoo main process and runs
   `py-spy record --format speedscope --subprocesses --duration <seconds> --rate <PROFILER_RATE>`: the main process,
   its HTTP and cron workers and the gevent process are all sampled. **Stop now** sends py-spy SIGINT; it then writes
   what it has.
4. The file is copied out (`docker cp`), the sidecar removed, the processes' command lines are taken out of the file
   (they may hold passwords), and a compact call tree for the flame graph is computed once.

Recordings are kept in `/opt/odoo/manager/profiles/<instance>/` (mode 600): the speedscope file, the call tree and a
short description; the last `PROFILER_KEEP` per instance, and all of them go when the instance is deleted. One
recording per instance runs at a time.

If the recording fails with a ptrace error, the server's Docker does not let containers trace processes: check
that the Docker seccomp profile is the default one (Docker 20.10 or later allows `ptrace` with `SYS_PTRACE`) and,
on hosts with AppArmor or SELinux policies of their own, that they allow it.

### Who may record

| Setting (**Settings, Sign-in and security, Code profiler and audit exports**) | Default | What it does |
| :--- | :--- | :--- |
| `PROFILER_ENABLED` | `true` | Off: nobody can record; the Tools tab disappears. |
| `PROFILER_ROLES` | `maintainers` | Besides super admins and administrators: `maintainers`, or `developers` (maintainers and developers), or `admins` only. Viewers never can. The permission is `instances.profile`; project access and locked branches apply. |
| `PROFILER_PRODUCTION` | `true` | Production instances may be profiled; each recording is confirmed when it starts. Off: staging and development only. |
| `PROFILER_MAX_MINUTES` | `5` | Longest recording (people choose from 1 minute up to this). |
| `PROFILER_KEEP` | `5` | Recordings kept per instance. |
| `PROFILER_RATE` | `100` | Samples per second. Each sample pauses the Odoo processes for a fraction of a millisecond. |
| `PROFILER_IMAGE` | (empty) | The image with py-spy; empty: built here as described above. |

Audit log entries: `profiler started` (with the minutes, and `production, confirmed`), `profiler stopped`,
`profile recorded`, `profile failed` (with the reason), `profile downloaded`, `profile deleted`.

---

## 29. Audit log

**Audit log** in the sidebar lists who did what, from where, and whether it was allowed. Each entry also names the
project and branch it is about, when there is one: those of its instance, or of the project it changed. Filter by
project and branch, instance, user, action, result (allowed or refused), dates and any text; sort by a column; page
through it. A project's page links to its own entries (**Audit log**, next to its version), which opens the page
filtered to the project (`/activity?project=<id>`).

**Export** downloads what the filters show as CSV or Excel (XLSX): a year at most and `AUDIT_EXPORT_MAX_ROWS`
entries at most (100,000 by default). Each export is itself an entry (`audit log exported`, with the format, how many
entries and the filters). The panel keeps the newest 200,000 entries. API: `GET /api/v1/audit`,
`GET /api/v1/audit/facets` and `GET /api/v1/audit/export` ([Panel API](PANEL_API.md#accounts-roles-and-the-audit-log)).
