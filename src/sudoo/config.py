"""Every setting of the panel, typed and documented in one place.

Values come from environment variables (the panel's `.env` file, passed in by Docker Compose). Names are case
insensitive: the field `idle_hours` is set with `IDLE_HOURS=6`. A wrong value (for example `IDLE_HOURS=soon`) stops
the panel at start-up with a message naming the setting, instead of failing later.

The settings that choose plugins (`exposure`, `enterprise_source`) name an entry point; see plugins.py and
docs/PLUGINS.md.

Almost every setting can also be changed in the panel (Settings page, or PATCH /api/v1/settings): those in
EDITABLE take effect at once, those in RESTART (they choose plugins) when the panel restarts. The changes are kept in
<ODOO_ROOT>/manager/settings.json on top of `.env`, secrets encrypted; removing a change brings back the `.env`
value. Where things are and how they are reached (folders, ports, exposure, storage) is set in `.env` only, because
the web service and the containers read it too.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """The panel's configuration. See .env.example for every setting with an explanation."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    # ------------------------------------------------------------------ locations
    odoo_root: Path = Field(Path("/opt/odoo"), description="Folder that holds instances, backups and panel data.")
    docs_dir: Path = Field(
        PACKAGE_DIR.parent.parent / "documentation", description="README.md and docs/ shown in the panel."
    )
    memory_dir: Path = Field(Path("/dev/shm"), description="Memory-backed folder for secrets needed briefly as files.")

    # ------------------------------------------------------------------ Odoo
    odoo_image: str = Field("odoo", description="Docker image of Odoo; the tag is the Odoo version.")
    odoo_versions: str = Field(
        "auto",
        description='Versions offered: "auto" (every published version of the official image from odoo_min_version) '
        "or a comma-separated list such as 17.0,18.0,saas-18.2.",
    )
    odoo_min_version: str = Field("16.0", description='Oldest version offered when odoo_versions is "auto".')
    prepull_images: bool = Field(
        False,
        description="Download the Odoo image of every offered version, and the PostgreSQL image, in the background, "
        "so the first instance of a version does not wait for the download (each Odoo image takes 1-3 GB of disk).",
    )
    postgres_image: str = Field("postgres:16", description="Docker image of each instance's database.")
    odoo_admin_password: str = Field("admin", description='Password of the Odoo user "admin" in new instances.')

    # ------------------------------------------------------------------ instance containers (applied at their next start)
    odoo_memory_limit: str = Field(
        "2g",
        pattern=r"^(0|\d+(\.\d+)?[bkmg])$",
        description="Memory one instance's Odoo container may use, in Docker units (512m, 2g); 0: no limit.",
    )
    db_memory_limit: str = Field(
        "1g",
        pattern=r"^(0|\d+(\.\d+)?[bkmg])$",
        description="Memory one instance's database container may use, in Docker units (512m, 1g); 0: no limit.",
    )
    odoo_cpu_limit: float = Field(2, ge=0, description="Processor cores one instance's Odoo may use; 0: no limit.")
    db_cpu_limit: float = Field(2, ge=0, description="Processor cores one instance's database may use; 0: no limit.")
    container_pids_limit: int = Field(
        1024, ge=64, description="Processes and threads one instance container may run at the same time."
    )
    container_log_max_size: str = Field(
        "10m", pattern=r"^\d+[kmg]$", description="Size of a container log file before it is rotated (10m)."
    )
    container_log_max_files: int = Field(3, ge=1, le=20, description="Rotated log files kept per container.")
    drift_action: Literal["warn", "restore"] = Field(
        "restore",
        description='What happens when an instance\'s containers or files were changed outside the panel: "warn" '
        '(report it) or "restore" (report it, then recreate Odoo and its database from the panel\'s own '
        "configuration). Connectors are only ever reported.",
    )

    # ------------------------------------------------------------------ Enterprise add-ons (plugin: enterprise_source)
    enterprise_source: str = Field(
        "folder", description='Where Enterprise add-ons come from: "folder", "git" or "none".'
    )
    enterprise_path: str = Field(
        "/opt/odoo/enterprise/{version}",
        description="folder source: where the add-ons of a version are; {version} (19.0) and {major} (19) are replaced.",
    )
    enterprise_git_url: str = Field("", description="git source: repository holding one branch per Odoo version.")
    enterprise_git_branch: str = Field("{version}", description="git source: branch name of a version, e.g. {version}.")
    enterprise_marker: str = Field("web_enterprise", description="Module whose presence proves the add-ons are there.")

    # ------------------------------------------------------------------ exposure (plugin: exposure)
    exposure: str = Field(
        "cloudflare-quick", description='How instances are reached: "cloudflare-quick" or "host-ports".'
    )
    cloudflared_image: str = Field("cloudflare/cloudflared:latest", description="cloudflare-quick: connector image.")
    connector_prefix: str = Field("sudoo-connector-", description="cloudflare-quick: container name prefix.")
    connector_status_port_start: int = Field(20300, description="cloudflare-quick: first status port of a connector.")
    panel_connector_status_port: int = Field(20242, description="cloudflare-quick: status port of the panel connector.")
    public_host: str = Field("", description="host-ports: the server's public name or address.")
    public_scheme: Literal["http", "https"] = Field("http", description="host-ports: scheme of the public addresses.")

    # ------------------------------------------------------------------ update triggers (plugins)
    update_triggers: str = Field(
        "polling,github-app",
        description='What starts code updates, comma-separated: "polling", "github-app" (the GitHub App\'s webhook), '
        '"github-webhook" (one webhook per repository).',
    )
    github_webhook_secret: str = Field(
        "", description="github-webhook: the secret entered on GitHub (20 or more random characters)."
    )

    # ------------------------------------------------------------------ storage, sign-in, mail (plugins)
    storage: str = Field("sqlite", description='Where the panel keeps its own data: "sqlite".')
    auth: str = Field("local", description='How user names and passwords are checked: "local" (the panel\'s accounts).')
    second_factors: str = Field(
        "email,totp", description='Second factors offered at sign-in, comma-separated: "email", "totp" or both.'
    )
    require_second_factor: Literal["none", "admins", "everyone"] = Field(
        "admins", description='Who must use a second factor: "none", "admins" or "everyone".'
    )
    mailer: str = Field("smtp", description='How the panel sends e-mail: "smtp" or "none".')
    mail_from: str = Field("", description="Sender of the panel's e-mails, e.g. Sudoo.sh <no-reply@example.com>.")
    smtp_host: str = Field("", description="smtp: the mail server; empty: no e-mail (and no e-mail codes).")
    smtp_port: int = Field(587, description="smtp: port (587 for STARTTLS, 465 for SSL).")
    smtp_security: Literal["starttls", "ssl", "none"] = Field("starttls", description="smtp: encryption.")
    smtp_username: str = Field("", description="smtp: user name, usually the full e-mail address.")
    smtp_password: str = Field("", description="smtp: password or app password.")
    mail_templates_dir: str = Field(
        "", description="Folder with a layout.html that replaces the built-in frame of every e-mail."
    )
    invitation_hours: int = Field(
        72, ge=1, le=720, description="Hours an invitation link works before it expires (it works once)."
    )
    invitation_password_fallback: Literal["no_mail", "always", "never"] = Field(
        "no_mail",
        description="When administrators may set a new account's password instead of inviting the person by e-mail: "
        '"no_mail" (only while e-mail is not set up), "always" or "never".',
    )
    invitations_per_hour: int = Field(
        20, ge=1, description="Invitations one administrator may send (new or again) per hour."
    )
    recovery_codes_count: int = Field(
        10, ge=4, le=20, description="Recovery codes made for an account that uses an authenticator app."
    )
    sign_in_email: bool = Field(
        True, description="E-mail people each time their account signs in (when e-mail is set up)."
    )
    sign_in_email_opt_out: bool = Field(
        True, description="People may turn the sign-in e-mail off for their own account."
    )
    brand_color: str = Field(
        "#714B67", pattern=r"^#[0-9A-Fa-f]{6}$", description="Accent colour of e-mails, as #RRGGBB."
    )
    default_language: Literal["en", "ar"] = Field(
        "en", description='Language of the web app for accounts that have not chosen one: "en" or "ar".'
    )

    # ------------------------------------------------------------------ limits
    rate_limiter: str = Field("memory", description='Where request counts are kept: "memory".')
    rate_limit_reads_per_minute: int = Field(
        600, ge=0, description="Requests that read, per minute, per signed-in user or address; 0: no limit."
    )
    rate_limit_changes_per_minute: int = Field(
        60, ge=0, description="Requests that change something, per minute, per user or address; 0: no limit."
    )
    max_instances: int = Field(0, ge=0, description="Instances on this server at most; 0: no limit.")
    max_instances_per_user: int = Field(0, ge=0, description="Instances one user (or API token) may own; 0: no limit.")
    create_cooldown_minutes: float = Field(
        0, ge=0, description="Minutes a user must wait between two creations; 0: no wait."
    )

    # ------------------------------------------------------------------ capacity (instances/capacity.py)
    capacity_reserve_mb: int = Field(
        1024,
        ge=0,
        description="Memory kept free for the operating system and Docker, in MB (the larger of this and "
        "capacity_reserve_percent is used).",
    )
    capacity_reserve_percent: float = Field(
        10, ge=0, le=90, description="Memory kept free for the operating system and Docker, in % of the server's."
    )
    capacity_instance_memory_mb: int = Field(
        700,
        ge=50,
        description="Memory one running instance is counted with while none has been measured yet, in MB.",
    )
    capacity_warning_percent: float = Field(
        85,
        ge=10,
        le=100,
        description="Server memory use, in %, from which the dashboard warns that instances may slow down.",
    )
    max_running_instances: int = Field(
        0,
        ge=0,
        description="Instances running at once at most: creating or starting one more is refused; 0: never refused, "
        "only warned.",
    )

    # ------------------------------------------------------------------ gateway (instances/gateway.py)
    gateway_mode: Literal["service", "embedded"] = Field(
        "service",
        description='Where the gateways run: "service" (their own container, sudoo-gateway: instances keep '
        "answering while the panel restarts; the panel serves them itself while that container is not running) or "
        '"embedded" (inside the panel process, as before 3.0).',
    )
    gateway_takeover_seconds: float = Field(
        10,
        ge=2,
        le=600,
        description="service mode: seconds the gateway service may not answer the panel before the panel serves "
        "the instances itself (at once when the service stopped normally).",
    )
    gateway_max_upload_mb: int = Field(
        2048, ge=1, description="Largest request body a visitor may send to an instance (uploads, imports), in MB."
    )
    gateway_idle_timeout_seconds: float = Field(
        120,
        ge=5,
        le=3600,
        description="Seconds a visitor may send nothing while sending a request body before the gateway gives up.",
    )
    gateway_websocket_max_message_mb: int = Field(
        16, ge=1, le=1024, description="Largest single WebSocket message passed to or from an instance, in MB."
    )
    gateway_header_timeout_seconds: float = Field(
        30,
        ge=1,
        le=3600,
        description="Seconds a visitor's connection has to send a request's headers (and is kept open while idle).",
    )
    gateway_max_connections: int = Field(
        1000, ge=1, description="Visitors' connections open at once to one instance's gateway; more are answered 503."
    )
    gateway_max_connections_total: int = Field(
        4000, ge=1, description="Visitors' connections open at once to all gateways together; more are answered 503."
    )

    # ------------------------------------------------------------------ custom domains (domains/)
    custom_domains: Literal["off", "acme", "external"] = Field(
        "off",
        description='Custom domains for instances: "off", "acme" (the gateway answers on ports 80 and 443 and gets '
        'a certificate for each domain from an ACME authority such as Let\'s Encrypt) or "external" (something in '
        "front, such as your own Cloudflare tunnel or proxy, handles https; the panel only records the domains).",
    )
    acme_directory: str = Field(
        "production",
        description='The ACME authority: "production" (Let\'s Encrypt), "staging" (Let\'s Encrypt\'s test '
        "authority: certificates browsers do not trust, for trying things out) or the https address of another "
        "ACME directory.",
    )
    acme_email: str = Field(
        "", description="Contact e-mail given to the ACME authority; empty: ADMIN_CONTACT_EMAIL, or none."
    )
    certificate_renew_days: int = Field(
        30, ge=1, le=90, description="Renew a certificate this many days before it expires."
    )
    certificate_retry_max_hours: float = Field(
        24, ge=1, le=168, description="Longest wait between two attempts after a certificate could not be issued."
    )
    domains_per_instance: int = Field(5, ge=0, le=100, description="Custom domains one instance may have; 0: none.")
    domain_dns_target: str = Field(
        "",
        description="The name (or address) custom domains point to; empty: PUBLIC_HOST. A name is shown as a CNAME "
        "record, an address as an A or AAAA record.",
    )
    domain_dns_timeout_seconds: float = Field(
        5, ge=1, le=60, description="Seconds a DNS lookup of a custom domain may take."
    )
    domain_http_probe: bool = Field(
        True,
        description="Before asking for a certificate, check that the domain reaches this server over http (port "
        "80), as the ACME authority will.",
    )
    domain_http_port: int = Field(80, description="Port the gateway answers custom domains on over http.")
    domain_https_port: int = Field(443, description="Port the gateway answers custom domains on over https.")

    @field_validator("acme_directory")
    @classmethod
    def _acme_directory(cls, value: str) -> str:
        from urllib.parse import urlsplit

        value = value.strip()
        if value in ("production", "staging"):
            return value
        parts = urlsplit(value)
        local = parts.hostname in ("localhost", "127.0.0.1", "::1")
        if parts.scheme not in ("https", "http") or not parts.hostname or (parts.scheme == "http" and not local):
            raise ValueError('must be "production", "staging" or an https:// address')
        if parts.username or parts.password or parts.fragment or len(value) > 500:
            raise ValueError("must be a plain address, without a user name, password or #fragment")
        return value

    @field_validator("acme_email")
    @classmethod
    def _acme_email(cls, value: str) -> str:
        import re

        value = value.strip()
        if value and not re.fullmatch(r"[^@\s,:]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}", value):
            raise ValueError("is not an e-mail address")
        return value

    @field_validator("domain_dns_target")
    @classmethod
    def _domain_dns_target(cls, value: str) -> str:
        import ipaddress
        import re

        value = value.strip().lower().rstrip(".")
        if not value:
            return ""
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            pass
        label = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
        if len(value) > 253 or not re.fullmatch(rf"{label}(\.{label})+", value):
            raise ValueError("must be a host name such as server.example.com, or an IP address")
        return value

    # ------------------------------------------------------------------ ports
    panel_port: int = Field(9000, description="Port of the panel: the web service (sudoo-web) listens here.")
    api_port: int = Field(9002, description="Port of the backend (127.0.0.1 only); the web service forwards to it.")
    events_port: int = Field(9001, description="Port of the live event stream (127.0.0.1 only).")
    gateway_port_start: int = Field(8069, description="First gateway port (one per instance).")
    odoo_port_start: int = Field(28069, description="First Odoo port (one per instance, 127.0.0.1 only).")

    # ------------------------------------------------------------------ behaviour
    idle_hours: float = Field(6, ge=0, description="Pause an instance after this many hours without visits; 0: never.")
    auto_pause: bool = Field(True, description="New instances pause when idle (each instance can change it).")
    auto_update: bool = Field(True, description="New instances get new code automatically.")
    auto_upgrade: bool = Field(True, description="New instances upgrade changed modules after an update.")
    delete_after_days: int = Field(30, ge=0, description="Lifetime offered for new instances, in days; 0: keep.")

    # ------------------------------------------------------------------ stages (instances/stages.py)
    max_production_instances: int = Field(
        1, ge=0, description="Instances that may be in the production stage at the same time; 0: no limit."
    )
    production_auto_update: bool = Field(
        False,
        description="New production instances get new code automatically (off: updates only when someone asks, or "
        "once switched on for the instance).",
    )
    staging_delete_after_days: int = Field(
        90, ge=0, description="Lifetime offered for new staging instances, in days; 0: keep."
    )
    staging_daily_backups: bool = Field(
        True, description="Staging instances get the daily backup too (when daily_backups is on)."
    )
    poll_minutes: float = Field(2, gt=0, description="polling: check GitHub for new commits this often.")
    session_idle_minutes: float = Field(60, gt=0, description="Panel sign-out after this long without activity.")
    session_max_hours: float = Field(12, gt=0, description="Longest panel session.")
    admin_contact_name: str = Field("the administrator", description="Who users are told to contact.")
    admin_contact_phone: str = ""
    admin_contact_email: str = ""

    metrics_interval_seconds: float = Field(
        5, ge=2, le=60, description="Seconds between two measurements of the server and the instances."
    )
    daily_backups: bool = Field(
        True,
        description="Back up every instance that was used since its last daily backup, once a day; each keeps only "
        "its latest daily backup.",
    )
    daily_backup_time: str = Field(
        "00:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="When the daily backups run (HH:MM, 24 hours)."
    )
    timezone: str = Field("UTC", description="Time zone of the daily backup time, for example Africa/Cairo.")
    rollback_hours: float = Field(
        24,
        gt=0,
        le=720,
        description="How long the state before a restore is kept, and Rollback offered, after a restore (hours).",
    )
    safe_updates: bool = Field(
        True,
        description="Back up an instance before an update changes its code, and put it back (code, and the database "
        "when it may have changed) when the update fails.",
    )

    # ------------------------------------------------------------------ off-site copies (offsite/, docs/ADMIN_GUIDE.md "Off-site copies")
    offsite_backups: bool = Field(
        False,
        description="Copy backups to S3-compatible storage off this server (Cloudflare R2, AWS S3, Backblaze B2, "
        "MinIO...), each copy with a manifest of SHA-256 checksums signed with the panel's Ed25519 key.",
    )
    offsite_endpoint: str = Field(
        "",
        description="Address of the storage's S3 API, e.g. https://<account>.r2.cloudflarestorage.com or "
        "https://s3.eu-central-1.amazonaws.com (https; http only for localhost).",
    )
    offsite_region: str = Field(
        "auto", description='Region of the bucket ("auto" for Cloudflare R2; us-east-1, eu-central-1, ... otherwise).'
    )
    offsite_bucket: str = Field("", description="The bucket the copies go to (create it first; keep it private).")
    offsite_prefix: str = Field(
        "sudoo", description="Folder inside the bucket for this panel's copies (several panels can share a bucket)."
    )
    offsite_addressing: Literal["path", "virtual"] = Field(
        "path",
        description='How the bucket is addressed: "path" (endpoint/bucket/file: R2, MinIO, B2, S3) or "virtual" '
        "(bucket.endpoint/file).",
    )
    offsite_access_key_id: str = Field("", description="Access key ID of a key that may read and write the bucket.")
    offsite_secret_access_key: str = Field("", description="The secret of that access key (stored encrypted).")
    offsite_other_stages: bool = Field(
        False,
        description="Copy the backups of staging and development instances off-site too (production instances' "
        "backups are always copied while off-site copies are on).",
    )
    offsite_kinds: str = Field(
        "manual,daily,deletion",
        description="Which backups are copied, comma-separated: manual, daily, deletion, before-update, "
        "before-restore, before-rollback, before-repair.",
    )
    offsite_keep_daily_production: int = Field(
        30, ge=1, le=3650, description="Production: keep the newest copy of each of this many most recent days."
    )
    offsite_keep_monthly_production: int = Field(
        12, ge=0, le=600, description="Production: keep the newest copy of each of this many most recent months too."
    )
    offsite_keep_daily_other: int = Field(
        7, ge=1, le=3650, description="Staging and development: keep the newest copy of each of this many days."
    )
    offsite_keep_monthly_other: int = Field(
        0, ge=0, le=600, description="Staging and development: keep the newest copy of each of this many months too."
    )
    offsite_verify_days: float = Field(
        7,
        ge=0,
        le=365,
        description="Download and check the newest copy of each instance again when none was verified for this many "
        "days (signature and every checksum); 0: only when someone asks.",
    )
    offsite_timeout_seconds: float = Field(
        60, ge=5, le=600, description="Seconds one request to the storage may wait for an answer."
    )

    @field_validator("offsite_endpoint")
    @classmethod
    def _offsite_endpoint(cls, value: str) -> str:
        from urllib.parse import urlsplit

        value = value.strip().rstrip("/")
        if not value:
            return ""
        try:
            parts = urlsplit(value)
            host = parts.hostname
        except ValueError:
            raise ValueError("must be an https:// address") from None
        local = host in ("localhost", "127.0.0.1", "::1")
        if parts.scheme not in ("https", "http") or not host or (parts.scheme == "http" and not local):
            raise ValueError("must be an https:// address (http:// only for localhost)")
        if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
            raise ValueError("must be a plain address such as https://s3.eu-central-1.amazonaws.com, without a path")
        return value

    @field_validator("offsite_region")
    @classmethod
    def _offsite_region(cls, value: str) -> str:
        import re

        value = value.strip() or "auto"
        if not re.fullmatch(r"[a-z0-9-]{1,40}", value):
            raise ValueError("must be a region name such as auto, us-east-1 or eu-central-1")
        return value

    @field_validator("offsite_bucket")
    @classmethod
    def _offsite_bucket(cls, value: str) -> str:
        import re

        value = value.strip()
        if value and not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", value):
            raise ValueError("must be a bucket name: 3-63 lowercase letters, digits, dots and hyphens")
        return value

    @field_validator("offsite_prefix")
    @classmethod
    def _offsite_prefix(cls, value: str) -> str:
        import re

        value = value.strip().strip("/")
        if value and (not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", value) or ".." in value or "//" in value):
            raise ValueError("must be a folder name of letters, digits, dots, hyphens, underscores and slashes")
        return value

    @field_validator("offsite_access_key_id")
    @classmethod
    def _offsite_access_key_id(cls, value: str) -> str:
        import re

        value = value.strip()
        if value and not re.fullmatch(r"[A-Za-z0-9._+/=-]{1,256}", value):
            raise ValueError("is not an access key ID")
        return value

    @field_validator("offsite_kinds")
    @classmethod
    def _offsite_kinds(cls, value: str) -> str:
        known = ("manual", "daily", "deletion", "before-update", "before-restore", "before-rollback", "before-repair")
        kinds = [part.strip() for part in value.split(",") if part.strip()]
        unknown = [kind for kind in kinds if kind not in known]
        if unknown:
            raise ValueError(f"unknown kind {unknown[0][:40]!r}; use {', '.join(known)}")
        return ",".join(dict.fromkeys(kinds))

    # ------------------------------------------------------------------ alerts (alerts/, docs/ADMIN_GUIDE.md "Alerts")
    alerts: bool = Field(
        True,
        description="Tell people when something needs attention (a failed job, an instance down, a full disk), by "
        "e-mail and webhooks; off: no alert is sent or listed.",
    )
    alert_super_admins: bool = Field(True, description="Send alert e-mails to every super admin with an address.")
    alert_emails: str = Field(
        "", description="More addresses that receive alert e-mails, comma-separated (an on-call mailbox, say)."
    )
    alert_job_failed: bool = Field(True, description="Alert when a job fails (with its plain hint and a link).")
    alert_update_failed: bool = Field(
        True, description="Alert when an update failed and was rolled back, or its rollback failed too."
    )
    alert_backup_failed: bool = Field(True, description="Alert when a backup fails (manual, daily or before a change).")
    alert_instance_down: bool = Field(
        True, description="Alert when an instance that should run does not answer for alert_instance_down_minutes."
    )
    alert_instance_down_minutes: float = Field(
        5, ge=1, le=1440, description="Minutes an instance must stay down before the alert."
    )
    alert_disk: bool = Field(True, description="Alert when the free disk falls below alert_disk_free_percent.")
    alert_disk_free_percent: float = Field(
        10, ge=1, le=90, description="Free disk, in % of the disk holding ODOO_ROOT, below which the alert is sent."
    )
    alert_memory: bool = Field(
        True,
        description="Alert when server memory stays above capacity_warning_percent for alert_memory_minutes.",
    )
    alert_memory_minutes: float = Field(
        10, ge=1, le=1440, description="Minutes memory must stay above the warning before the alert."
    )
    alert_drift: bool = Field(True, description="Alert when an instance was changed outside the panel (drift).")
    alert_offsite_failed: bool = Field(
        True, description="Alert when a backup could not be copied off-site, or a copy failed its check."
    )
    alert_offsite_missing: bool = Field(
        True,
        description="Alert when a production instance has had no successful off-site copy for "
        "alert_offsite_missing_hours (while off-site copies are on).",
    )
    alert_offsite_missing_hours: float = Field(
        26,
        ge=1,
        le=720,
        description="Hours without a successful off-site copy of a production instance before the alert.",
    )
    alert_public_link: bool = Field(
        True,
        description="Alert when the panel or a running instance has no public link for alert_public_link_minutes.",
    )
    alert_public_link_minutes: float = Field(
        10, ge=1, le=1440, description="Minutes without a public link before the alert."
    )
    alert_certificate: bool = Field(
        True,
        description="Alert when a custom domain's certificate cannot be issued or renewed, or expires within "
        "alert_certificate_days.",
    )
    alert_certificate_days: float = Field(
        14, ge=1, le=60, description="Days before a certificate expires from which it is shown and alerted as expiring."
    )
    alert_recovery: bool = Field(
        True, description="Send a notice when a problem that was alerted is over (back online, disk freed)."
    )
    alert_quiet_minutes: float = Field(
        60, ge=0, le=10080, description="The same alert is sent at most once in this many minutes; 0: every time."
    )
    alert_digest: bool = Field(
        False,
        description="E-mail one summary a day instead of each alert as it happens (webhooks and the bell still "
        "get each alert at once).",
    )
    alert_digest_time: str = Field(
        "08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="When the daily summary is sent (HH:MM, TIMEZONE)."
    )
    alert_webhook_timeout_seconds: float = Field(
        10, ge=1, le=60, description="Seconds a webhook may take to answer before the delivery counts as failed."
    )
    alert_webhook_1_url: str = Field("", description="Webhook 1: address alerts are posted to (https).")
    alert_webhook_1_secret: str = Field("", description="Webhook 1: key of the X-Sudoo-Signature HMAC-SHA256.")
    alert_webhook_1_format: Literal["json", "slack", "discord", "teams"] = Field(
        "json", description='Webhook 1: "json" (signed JSON), "slack", "discord" or "teams" (incoming webhooks).'
    )
    alert_webhook_2_url: str = Field("", description="Webhook 2: address alerts are posted to (https).")
    alert_webhook_2_secret: str = Field("", description="Webhook 2: key of the X-Sudoo-Signature HMAC-SHA256.")
    alert_webhook_2_format: Literal["json", "slack", "discord", "teams"] = Field(
        "json", description='Webhook 2: "json", "slack", "discord" or "teams".'
    )
    alert_webhook_3_url: str = Field("", description="Webhook 3: address alerts are posted to (https).")
    alert_webhook_3_secret: str = Field("", description="Webhook 3: key of the X-Sudoo-Signature HMAC-SHA256.")
    alert_webhook_3_format: Literal["json", "slack", "discord", "teams"] = Field(
        "json", description='Webhook 3: "json", "slack", "discord" or "teams".'
    )

    # ------------------------------------------------------------------ web shell (webshell/, docs/ADMIN_GUIDE.md "Web shell")
    shell_enabled: bool = Field(
        True,
        description="People whose role allows it can open a terminal in an instance's containers from the panel "
        "(the Shell tab); off: nobody can, and open shells close.",
    )
    shell_roles: Literal["admins", "maintainers", "developers"] = Field(
        "admins",
        description='Who may open a shell in an instance\'s Odoo container: "admins" (super admins and '
        'administrators), "maintainers" (and maintainers) or "developers" (and developers). Viewers never can; psql '
        "in the database container stays with super admins and administrators.",
    )
    shell_production: Literal["off", "super_admins", "allowed"] = Field(
        "super_admins",
        description='Shells in production instances: "off" (nobody), "super_admins" (super admins only) or "allowed" '
        "(whoever may open shells in other instances). Each one is confirmed when it is opened.",
    )
    shell_root: bool = Field(
        False,
        description="Super admins may open the Odoo container's shell as root (the container's capabilities stay "
        "dropped); off: every shell runs as the container's own user.",
    )
    shell_idle_minutes: float = Field(15, ge=1, le=1440, description="A shell nobody types in for this long closes.")
    shell_max_minutes: float = Field(
        60, ge=1, le=1440, description="Longest a shell stays open; it then closes (a new one can be opened)."
    )
    shell_max_per_user: int = Field(2, ge=1, le=20, description="Shells one person may have open at the same time.")
    shell_max_per_instance: int = Field(3, ge=1, le=50, description="Shells open in one instance at the same time.")
    shell_ticket_seconds: int = Field(
        15, ge=5, le=60, description="Seconds the one-time ticket that connects a new shell stays valid."
    )
    shell_recording: Literal["off", "production", "all"] = Field(
        "production",
        description='Record what is typed and shown in shells: "off", "production" (shells in production instances '
        'and root shells) or "all". Recordings are encrypted; only super admins can play them.',
    )
    shell_recording_days: int = Field(
        30, ge=1, le=3650, description="Days recordings, and the list of past shells, are kept."
    )
    shell_recording_max_mb: float = Field(
        5,
        ge=0.1,
        le=50,
        description="Largest recording of one shell, in MB; beyond it only what is typed is recorded, and the shell "
        "closes when that alone reaches the size again.",
    )
    shell_recordings_total_mb: int = Field(
        500, ge=10, le=100000, description="Space all shell recordings may take together, in MB; the oldest go first."
    )

    @field_validator("alert_emails")
    @classmethod
    def _alert_emails(cls, value: str) -> str:
        import re

        addresses = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
        for address in addresses:
            if not re.fullmatch(r"[^@\s,]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}", address):
                raise ValueError(f"{address[:80]} is not an e-mail address")
        if len(addresses) > 50:
            raise ValueError("at most 50 addresses")
        return ",".join(dict.fromkeys(addresses))

    @field_validator("alert_webhook_1_url", "alert_webhook_2_url", "alert_webhook_3_url")
    @classmethod
    def _webhook_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        value = value.strip()
        if not value:
            return ""
        try:
            parts = urlsplit(value)
            host = parts.hostname
        except ValueError:
            raise ValueError("must be an https:// address") from None
        local = host in ("localhost", "127.0.0.1", "::1")
        if parts.scheme not in ("https", "http") or not host or (parts.scheme == "http" and not local):
            raise ValueError("must be an https:// address (http:// only for localhost)")
        if parts.username or parts.password or parts.fragment or len(value) > 2000:
            raise ValueError("must be a plain address, without a user name, password or #fragment")
        return value

    # ------------------------------------------------------------------ marketplace (docs/MARKETPLACE.md)
    marketplace: bool = Field(
        False,
        description="Use the Sudoo.sh marketplace: register with the hub, send a signed heartbeat every 15 minutes, "
        "install plugins. Off: the panel never contacts the hub; installed free plugins keep working.",
    )
    hub_url: str = Field(
        "https://hub.sudoo.sh",
        description="Address of the Sudoo.sh hub (https; http only for localhost while testing).",
    )
    hub_public_keys: str = Field(
        "",
        description="The hub's root public keys (Ed25519, base64url, comma-separated); empty: the keys this release "
        "ships for hub.sudoo.sh, or the hub's keys as first seen for another hub.",
    )

    @field_validator("hub_public_keys")
    @classmethod
    def _hub_public_keys(cls, value: str) -> str:
        import base64
        import binascii
        import re

        keys = [part.strip() for part in value.split(",") if part.strip()]
        for key in keys:
            try:
                raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
            except (binascii.Error, ValueError):
                raw = b""
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", key) or len(raw) != 32:
                raise ValueError("each key must be an Ed25519 public key in base64url (43 characters)")
        return ",".join(keys)

    @field_validator("hub_url")
    @classmethod
    def _hub_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parts = urlsplit(value.strip())
        local = parts.hostname in ("localhost", "127.0.0.1", "::1")
        if parts.scheme not in ("https", "http") or not parts.hostname or (parts.scheme == "http" and not local):
            raise ValueError("must be an https:// address (http:// only for localhost)")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("must be a plain address such as https://hub.sudoo.sh")
        return value.strip().rstrip("/")

    @field_validator("enterprise_git_url")
    @classmethod
    def _no_credentials_in_git_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        value = value.strip()
        if "://" in value:
            parts = urlsplit(value)
            if parts.password is not None or (parts.username and parts.scheme not in ("ssh", "git+ssh")):
                raise ValueError(
                    "must not contain a user name or password (they would end up in job logs and in the add-ons' "
                    ".git/config); use an SSH address and give the server a deploy key under GitHub access"
                )
        return value

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("unknown time zone; use a name such as UTC or Africa/Cairo") from error
        return value

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    # ------------------------------------------------------------------ fixed intervals (not settings)
    pause_check_interval_seconds: int = 120
    integrity_check_interval_seconds: int = 20  # instances/integrity.py
    expiry_check_interval_seconds: int = 600
    history_samples: int = 120  # the previous pages' short history (instances/monitor.py)

    # ------------------------------------------------------------------ derived locations
    @property
    def instances_dir(self) -> Path:
        return self.odoo_root / "instances"

    @property
    def backups_dir(self) -> Path:
        return self.odoo_root / "backups"

    @property
    def manager_dir(self) -> Path:
        return self.odoo_root / "manager"

    @property
    def gateway_dir(self) -> Path:
        """Shared by the panel and the gateway service (instances/gateway_service.py), readable by root only. The
        only secrets in it are the custom domains' certificate keys (certs/, domains/certificates.py)."""
        return self.odoo_root / "gateway"

    @property
    def keys_dir(self) -> Path:
        return self.manager_dir / "keys"

    @property
    def deploy_keys_dir(self) -> Path:
        """One read-only GitHub deploy key per repository."""
        return self.keys_dir / "deploy"

    @property
    def secret_file(self) -> Path:
        """Signs session cookies."""
        return self.manager_dir / "secret_key"

    @property
    def vault_key_file(self) -> Path:
        """Encrypts stored secrets and peppers password hashes (core/vault.py)."""
        return self.manager_dir / "vault.key"

    @property
    def admin_contact(self) -> dict[str, str]:
        return {"name": self.admin_contact_name, "phone": self.admin_contact_phone, "email": self.admin_contact_email}

    def contact_sentence(self) -> str:
        """'Please contact the administrator, <name> (<phone>, <email>).' for messages shown to sync operators."""
        details = ", ".join(value for value in (self.admin_contact_phone, self.admin_contact_email) if value)
        who = "the administrator"
        if self.admin_contact_name and self.admin_contact_name.lower() != who:
            who += f", {self.admin_contact_name}"
        return f"Please contact {who}" + (f" ({details})." if details else ".")


# ---------------------------------------------------------------------- changes made in the panel

ALERT_SETTINGS = tuple(name for name in Settings.model_fields if name == "alerts" or name.startswith("alert_"))
"""Every alert setting (alerts/); all of them can be changed in the panel."""

OFFSITE_SETTINGS = tuple(name for name in Settings.model_fields if name.startswith("offsite_"))
"""Every setting of off-site copies (offsite/); changing them also needs the permission offsite.configure."""

SHELL_SETTINGS = tuple(name for name in Settings.model_fields if name.startswith("shell_"))
"""Every web shell setting (webshell/); all of them can be changed in the panel."""

EDITABLE = (
    # take effect at once
    "brand_color",
    "default_language",
    "require_second_factor",
    "session_idle_minutes",
    "session_max_hours",
    "rate_limit_reads_per_minute",
    "rate_limit_changes_per_minute",
    "max_instances",
    "max_instances_per_user",
    "create_cooldown_minutes",
    "capacity_reserve_mb",
    "capacity_reserve_percent",
    "capacity_instance_memory_mb",
    "capacity_warning_percent",
    "max_running_instances",
    "gateway_max_upload_mb",
    "gateway_idle_timeout_seconds",
    "gateway_websocket_max_message_mb",
    "idle_hours",
    "auto_pause",
    "auto_update",
    "auto_upgrade",
    "delete_after_days",
    "max_production_instances",
    "production_auto_update",
    "staging_delete_after_days",
    "staging_daily_backups",
    "poll_minutes",
    "metrics_interval_seconds",
    "daily_backups",
    "daily_backup_time",
    "timezone",
    "rollback_hours",
    "safe_updates",
    "odoo_versions",
    "odoo_min_version",
    "prepull_images",
    "odoo_image",
    "postgres_image",
    "odoo_admin_password",
    "odoo_memory_limit",
    "db_memory_limit",
    "odoo_cpu_limit",
    "db_cpu_limit",
    "container_pids_limit",
    "container_log_max_size",
    "container_log_max_files",
    "drift_action",
    "enterprise_path",
    "enterprise_git_url",
    "enterprise_git_branch",
    "enterprise_marker",
    "github_webhook_secret",
    "mail_from",
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "smtp_username",
    "smtp_password",
    "mail_templates_dir",
    "recovery_codes_count",
    "invitation_hours",
    "invitation_password_fallback",
    "invitations_per_hour",
    "sign_in_email",
    "sign_in_email_opt_out",
    "cloudflared_image",
    "admin_contact_name",
    "admin_contact_phone",
    "admin_contact_email",
    "marketplace",
    "custom_domains",
    "acme_directory",
    "acme_email",
    "certificate_renew_days",
    "certificate_retry_max_hours",
    "domains_per_instance",
    "domain_dns_target",
    "domain_dns_timeout_seconds",
    "domain_http_probe",
    *OFFSITE_SETTINGS,
    *ALERT_SETTINGS,
    *SHELL_SETTINGS,
)
"""Settings that can be changed in the panel, taking effect at once."""

RESTART = ("mailer", "second_factors", "update_triggers", "enterprise_source", "auth", "rate_limiter")
"""Settings that can be changed in the panel and take effect when the panel restarts (they choose plugins)."""

SECRET = (
    "odoo_admin_password",
    "github_webhook_secret",
    "smtp_password",
    "offsite_secret_access_key",
    *(f"alert_webhook_{n}_{part}" for n in (1, 2, 3) for part in ("url", "secret")),
)
"""Settings whose value is never shown, only whether they are set; kept encrypted in settings.json."""

_CHANGEABLE = frozenset(EDITABLE + RESTART)


class LiveSettings:
    """The current settings: `.env`, with the changes made in the panel on top.

    Reading an attribute gives the current value, so code that keeps a reference (plugins get it in their
    constructor) sees changes at once. Each change builds a new, validated, frozen Settings object. Secrets are
    stored encrypted with the vault key and decrypted when first read (the vault itself needs these settings to
    find its key, so it cannot be used while they load).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._env = Settings()
        self._overrides: dict[str, Any] = {}
        self._encrypted: dict[str, str] = {}
        self._current = self._env
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        try:
            saved = json.loads(self._file.read_text())
            self._encrypted = {
                k: v["encrypted"] for k, v in saved.items() if k in SECRET and isinstance(v, dict) and "encrypted" in v
            }
            plain = {k: v for k, v in saved.items() if k in _CHANGEABLE and k not in self._encrypted}
            self._current = Settings(**plain)
            self._overrides = {k: getattr(self._current, k) for k in plain}
        except (OSError, ValueError):
            pass  # no changes yet, or an unreadable file: .env alone
        self._started = self._current  # what the plugins were created with

    def __getattr__(self, name: str) -> Any:
        if name in SECRET and self._encrypted:
            self._decrypt_secrets()
        return getattr(self._current, name)

    def _decrypt_secrets(self) -> None:
        from sudoo.core import vault

        with self._lock:
            if not self._encrypted:
                return
            plain = {k: vault.decrypt(v, f"setting:{k}") for k, v in self._encrypted.items()}
            self._encrypted = {}
            self._overrides.update(plain)
            self._current = Settings(**self._overrides)

    @property
    def _file(self) -> Path:
        return self._env.manager_dir / "settings.json"

    @property
    def env(self) -> Settings:
        """The values from `.env` alone."""
        return self._env

    def overrides(self) -> dict[str, Any]:
        """The settings changed in the panel, with their values."""
        self._decrypt_secrets()
        return dict(self._overrides)

    def pending_restart(self, name: str) -> bool:
        """True when a setting that applies at restart was changed since the panel started."""
        return name in RESTART and getattr(self._current, name) != getattr(self._started, name)

    def change(self, values: Mapping[str, Any]) -> None:
        """Change settings (None removes a change). Raises ValueError, naming the setting, when a value is not
        valid or the setting can only be set in .env."""
        refused = sorted(set(values) - _CHANGEABLE)
        if refused:
            raise ValueError(f"These settings can only be changed in .env: {', '.join(refused)}.")
        self._decrypt_secrets()
        from sudoo.core import vault

        with self._lock:
            overrides = {**self._overrides, **values}
            overrides = {k: v for k, v in overrides.items() if v is not None}
            current = Settings(**overrides)  # pydantic.ValidationError is a ValueError
            self._overrides = {k: getattr(current, k) for k in overrides}
            self._current = current
            stored = {
                k: {"encrypted": vault.encrypt(str(v), f"setting:{k}")} if k in SECRET else v
                for k, v in self._overrides.items()
            }
            vault.write_private(self._file, json.dumps(stored, indent=1))
        for listener in self._listeners:
            listener(dict(values))

    def use(self, values: Mapping[str, Any]) -> None:
        """Take these values on top of `.env`, in memory only: the gateway service gets the panel's current values
        from the panel (instances/gateway_service.py) and has no settings.json of its own. Raises ValueError when
        a value is not valid; nothing changes then."""
        current = Settings(**dict(values))
        with self._lock:
            self._overrides = {k: getattr(current, k) for k in values}
            self._encrypted = {}
            self._current = current

    def on_change(self, listener: Callable[[dict[str, Any]], None]) -> None:
        """Call `listener(changed values)` after every change."""
        self._listeners.append(listener)


live = LiveSettings()
settings = cast(Settings, live)
"""The current settings. Typed as Settings: everything else in the panel reads it like a Settings object."""
