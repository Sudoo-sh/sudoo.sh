# Configuration

Every setting of the panel, with its type, default and meaning. Settings are environment variables, kept in
`/opt/odoo/manager/.env` on the server ([.env.example](../.env.example) is the template). Apply a change with
`cd /opt/odoo/manager && docker compose up -d`.

* Names are not case sensitive. A wrong value (for example `IDLE_HOURS=soon`) stops the panel at start-up with a
  message naming the setting.
* See what the panel actually uses: `docker exec oim-panel instance-manager config`.
* The settings are defined in `src/instance_manager/config.py`; a test fails when one of them is missing here.

---

## Contact

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `ADMIN_CONTACT_NAME` | str | `the administrator` | Who users are told to contact. |
| `ADMIN_CONTACT_PHONE` | str | empty | Phone number added to that message. |
| `ADMIN_CONTACT_EMAIL` | str | empty | E-mail address added to that message. |

---

## Odoo

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `ODOO_VERSIONS` | str | `auto` | Versions offered: "auto" (every published version of the official image from odoo_min_version) or a comma-separated list such as 17.0,18.0,saas-18.2. |
| `ODOO_MIN_VERSION` | str | `16.0` | Oldest version offered when odoo_versions is "auto". |
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
| `CONNECTOR_PREFIX` | str | `oim-connector-` | cloudflare-quick: container name prefix. |
| `CONNECTOR_STATUS_PORT_START` | int | `20300` | cloudflare-quick: first status port of a connector. |
| `PANEL_CONNECTOR_STATUS_PORT` | int | `20242` | cloudflare-quick: status port of the panel connector. |

---

## Ports

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `PANEL_PORT` | int | `9000` | Port of the panel. |
| `GATEWAY_PORT_START` | int | `8069` | First gateway port (one per instance). |
| `ODOO_PORT_START` | int | `28069` | First Odoo port (one per instance, 127.0.0.1 only). |

---

## Behaviour

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `IDLE_HOURS` | float | `6` | Pause an instance after this many hours without visits; 0: never. |
| `DELETE_AFTER_DAYS` | int | `30` | Lifetime offered for new instances, in days; 0: keep. |
| `POLL_MINUTES` | float | `2` | Check GitHub for new commits this often. |
| `SESSION_IDLE_MINUTES` | float | `60` | Panel sign-out after this long without activity. |
| `SESSION_MAX_HOURS` | float | `12` | Longest panel session. |

---

## Locations

| Setting | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `ODOO_ROOT` | Path | `/opt/odoo` | Folder that holds instances, backups and panel data. |
| `DOCS_DIR` | Path | `/home/yousef-al-sabbah/odoo-instance-manager/documentation` | README.md and docs/ shown in the panel. |
| `MEMORY_DIR` | Path | `/dev/shm` | Memory-backed folder for secrets needed briefly as files. |
