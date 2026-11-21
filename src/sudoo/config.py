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

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent

STAGES = ("production", "staging", "development")

MEMORY_PATTERN = r"^(0|\d+(\.\d+)?[bkmg])$"
"""A quantity of memory in Docker units (512m, 2g); 0: none."""

MIN_CONTAINER_MEMORY = 128 * 1024**2


EXPOSURE_MODES = ("direct", "proxy", "tunnel")
"""The ways the panel and the instances are reached that the Settings page offers (EXPOSURE)."""

EXPOSURE_ALIASES = {"cloudflare-quick": "tunnel"}
"""Earlier names of a mode: the same plugin under its old entry point."""


def exposure_mode(name: str) -> str:
    """The mode an EXPOSURE value stands for: direct, proxy, tunnel, or the value itself (host-ports, or a plugin
    of someone else's)."""
    return EXPOSURE_ALIASES.get(name, name)


def memory_bytes(text: str) -> int:
    """Bytes of a quantity in Docker units ("512m", "2g", "0")."""
    text = text.strip().lower()
    if not text or text == "0":
        return 0
    units = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
    return int(float(text[:-1]) * units[text[-1]])


_LIMIT_TEXT = {
    "workers": "Odoo HTTP workers (odoo.conf workers); 0: threaded mode, Odoo's default.",
    "cron_threads": "Odoo cron workers (odoo.conf max_cron_threads); 0: Odoo's default (2).",
    "memory_soft": "Memory per Odoo worker before it is recycled, in MB (limit_memory_soft); 0: Odoo's default.",
    "memory_hard": "Memory per Odoo worker before a request is stopped, in MB (limit_memory_hard); 0: Odoo's default.",
    "time_cpu": "Processor seconds one request may use (limit_time_cpu); 0: Odoo's default.",
    "time_real": "Seconds one request may take (limit_time_real); 0: Odoo's default.",
    "odoo_memory": "Memory the Odoo container may use, in Docker units (512m, 2g); 0: no limit.",
    "odoo_cpu": "Processor cores the Odoo container may use; 0: no limit.",
    "db_memory": "Memory the database container may use, in Docker units (512m, 2g); 0: no limit.",
    "db_cpu": "Processor cores the database container may use; 0: no limit.",
}


def _limit(stage: str, what: str) -> str:
    return f"Default for {stage} instances. {_LIMIT_TEXT[what]}"


def _cpu_weight(stage: str) -> str:
    return (
        f"Processor weight of {stage} instances (Docker cpu_shares; 1024 is Docker's default). Only acts when the "
        "processor is busy: a container gets processor time in proportion to its weight."
    )


def _oom_score(stage: str) -> str:
    return (
        f"Out-of-memory score adjustment of {stage} instances (-999 to 1000): when the server runs out of memory, "
        "the kernel stops processes with the highest score first. -1000 (never stopped) is not allowed."
    )


def _reservation(stage: str, container: str) -> str:
    return (
        f"Memory reservation of the {container} container of {stage} instances (Docker mem_reservation, a soft "
        "limit): under memory pressure the kernel takes memory back from other containers first. 0: none."
    )


def _plain_https(value: str, required: bool) -> str:
    """An https:// address without a user name, password or #fragment (http:// only for localhost); "" when not
    `required` and empty."""
    from urllib.parse import urlsplit

    value = value.strip()
    if not value and not required:
        return ""
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        raise ValueError("must be an https:// address") from None
    local = host in ("localhost", "127.0.0.1", "::1")
    if parts.scheme not in ("https", "http") or not host or (parts.scheme == "http" and not local):
        raise ValueError("must be an https:// address (http:// only for localhost)")
    if parts.username or parts.password or parts.fragment or len(value) > 500:
        raise ValueError("must be a plain address, without a user name, password or #fragment")
    return value.rstrip("/")


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
        "tunnel",
        description='How people reach the panel and the instances: "direct" (this server answers on ports 80 and 443 '
        'itself, with certificates from an ACME authority such as Let\'s Encrypt), "proxy" (your own reverse proxy '
        'or load balancer in front) or "tunnel" (an outgoing tunnel, for servers that cannot open ports). New '
        "installations get direct; a .env without this setting keeps tunnel, the behaviour of earlier releases. "
        'The earlier names "cloudflare-quick" (tunnel) and "host-ports" still work.',
    )
    panel_domain: str = Field(
        "",
        description="direct and proxy: the name people open the panel with (its DNS points at this server, or at "
        "your proxy). direct serves it on port 443 with a certificate from the ACME authority. Empty: "
        "PUBLIC_HOST when it is a name, else the server's public address with the panel's own certificate.",
    )
    cloudflared_image: str = Field("cloudflare/cloudflared:latest", description="tunnel: connector image.")
    connector_prefix: str = Field("sudoo-connector-", description="tunnel: container name prefix.")
    connector_status_port_start: int = Field(20300, description="tunnel: first status port of a connector.")
    panel_connector_status_port: int = Field(20242, description="tunnel: status port of the panel connector.")
    public_host: str = Field(
        "",
        description="The server's public name or address, used in addresses that have no name of their own (direct: "
        "an instance without a managed name or custom domain; proxy and host-ports: every address). Empty with "
        "direct: the server's public address (PUBLIC_IPV4, PUBLIC_IPV6, or the one it sends from).",
    )
    public_scheme: Literal["", "http", "https"] = Field(
        "",
        description='proxy and host-ports: scheme of the public addresses; empty: "https" with proxy, "http" with '
        "host-ports.",
    )
    proxy_listen: str = Field(
        "127.0.0.1",
        description="proxy: the address the panel and the gateways listen on. 127.0.0.1 when the proxy runs on this "
        "server; another address only when a firewall lets nothing but the proxy reach those ports.",
    )
    proxy_client_ip_header: str = Field(
        "X-Real-IP",
        description="proxy: the request header in which your proxy sends the visitor's address (it must set it on "
        "every request, replacing any the visitor sent). For X-Forwarded-For the last address is taken.",
    )

    @field_validator("exposure")
    @classmethod
    def _exposure(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("panel_domain")
    @classmethod
    def _panel_domain(cls, value: str) -> str:
        import re

        value = value.strip().lower().rstrip(".")
        label = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
        if value and (len(value) > 253 or not re.fullmatch(rf"{label}(\.{label})+", value)):
            raise ValueError("must be a host name such as panel.example.com")
        return value

    @field_validator("public_host")
    @classmethod
    def _public_host(cls, value: str) -> str:
        import ipaddress
        import re

        value = value.strip().lower().rstrip(".")
        if not value:
            return ""
        try:
            return str(ipaddress.ip_address(value.strip("[]")))
        except ValueError:
            pass
        label = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
        if len(value) > 253 or not re.fullmatch(rf"{label}(\.{label})*", value):
            raise ValueError("must be a host name such as server.example.com, or an IP address")
        return value

    @field_validator("proxy_listen")
    @classmethod
    def _proxy_listen(cls, value: str) -> str:
        import ipaddress

        try:
            return str(ipaddress.ip_address(value.strip().strip("[]")))
        except ValueError:
            raise ValueError("must be an IP address, such as 127.0.0.1") from None

    @field_validator("proxy_client_ip_header")
    @classmethod
    def _proxy_client_ip_header(cls, value: str) -> str:
        import re

        value = value.strip()
        if value and not re.fullmatch(r"[A-Za-z0-9-]{1,64}", value):
            raise ValueError("must be a header name such as X-Real-IP")
        return value

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
    licence_required_version: str = Field(
        "1.0",
        pattern=r"^\d{1,3}\.\d{1,3}$",
        description="Oldest version of the Sudoo.sh Licence Agreement that counts: while no super admin has accepted "
        "it (or a later one), the next super admin to sign in must accept it before anything else. Instances keep "
        "running. It cannot be newer than the version this release bundles.",
    )
    link_deadline_days: int = Field(
        7,
        ge=1,
        le=365,
        description="Days after the panel's first run to link it to a Sudoo.sh account (Settings, Marketplace). "
        "Until then super admins and administrators see a banner; after that, while it is not linked, no new "
        "instances, no stage changes into production and no updates or deploys. Instances keep serving and backups "
        "keep running. The first run is recorded once and never changes.",
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

    # ------------------------------------------------------------------ resources (instances/resources.py)
    # Priority under load, by stage: weights that only act when instances compete for the processor or memory.
    # Applied to an instance's Odoo and database containers when it next starts (or when its resources are applied).
    priority_cpu_weight_production: int = Field(4096, ge=2, le=262144, description=_cpu_weight("production"))
    priority_cpu_weight_staging: int = Field(2048, ge=2, le=262144, description=_cpu_weight("staging"))
    priority_cpu_weight_development: int = Field(1024, ge=2, le=262144, description=_cpu_weight("development"))
    priority_oom_score_production: int = Field(0, ge=-999, le=1000, description=_oom_score("production"))
    priority_oom_score_staging: int = Field(300, ge=-999, le=1000, description=_oom_score("staging"))
    priority_oom_score_development: int = Field(600, ge=-999, le=1000, description=_oom_score("development"))
    priority_memory_reservation_production: str = Field(
        "1g", pattern=MEMORY_PATTERN, description=_reservation("production", "Odoo")
    )
    priority_db_memory_reservation_production: str = Field(
        "512m", pattern=MEMORY_PATTERN, description=_reservation("production", "database")
    )
    priority_memory_reservation_staging: str = Field(
        "0", pattern=MEMORY_PATTERN, description=_reservation("staging", "Odoo")
    )
    priority_db_memory_reservation_staging: str = Field(
        "0", pattern=MEMORY_PATTERN, description=_reservation("staging", "database")
    )
    priority_memory_reservation_development: str = Field(
        "0", pattern=MEMORY_PATTERN, description=_reservation("development", "Odoo")
    )
    priority_db_memory_reservation_development: str = Field(
        "0", pattern=MEMORY_PATTERN, description=_reservation("development", "database")
    )
    # Default limits of each stage (an admin can override them per instance). 0 everywhere: not set, so no limit
    # (containers) or Odoo's own default (odoo.conf). Unlimited by default.
    production_odoo_workers: int = Field(0, ge=0, le=256, description=_limit("production", "workers"))
    production_odoo_cron_threads: int = Field(0, ge=0, le=64, description=_limit("production", "cron_threads"))
    production_odoo_limit_memory_soft_mb: int = Field(
        0, ge=0, le=1048576, description=_limit("production", "memory_soft")
    )
    production_odoo_limit_memory_hard_mb: int = Field(
        0, ge=0, le=1048576, description=_limit("production", "memory_hard")
    )
    production_odoo_limit_time_cpu: int = Field(0, ge=0, le=86400, description=_limit("production", "time_cpu"))
    production_odoo_limit_time_real: int = Field(0, ge=0, le=86400, description=_limit("production", "time_real"))
    production_odoo_memory_limit: str = Field(
        "0", pattern=MEMORY_PATTERN, description=_limit("production", "odoo_memory")
    )
    production_odoo_cpu_limit: float = Field(0, ge=0, le=1024, description=_limit("production", "odoo_cpu"))
    production_db_memory_limit: str = Field("0", pattern=MEMORY_PATTERN, description=_limit("production", "db_memory"))
    production_db_cpu_limit: float = Field(0, ge=0, le=1024, description=_limit("production", "db_cpu"))
    staging_odoo_workers: int = Field(0, ge=0, le=256, description=_limit("staging", "workers"))
    staging_odoo_cron_threads: int = Field(0, ge=0, le=64, description=_limit("staging", "cron_threads"))
    staging_odoo_limit_memory_soft_mb: int = Field(0, ge=0, le=1048576, description=_limit("staging", "memory_soft"))
    staging_odoo_limit_memory_hard_mb: int = Field(0, ge=0, le=1048576, description=_limit("staging", "memory_hard"))
    staging_odoo_limit_time_cpu: int = Field(0, ge=0, le=86400, description=_limit("staging", "time_cpu"))
    staging_odoo_limit_time_real: int = Field(0, ge=0, le=86400, description=_limit("staging", "time_real"))
    staging_odoo_memory_limit: str = Field("0", pattern=MEMORY_PATTERN, description=_limit("staging", "odoo_memory"))
    staging_odoo_cpu_limit: float = Field(0, ge=0, le=1024, description=_limit("staging", "odoo_cpu"))
    staging_db_memory_limit: str = Field("0", pattern=MEMORY_PATTERN, description=_limit("staging", "db_memory"))
    staging_db_cpu_limit: float = Field(0, ge=0, le=1024, description=_limit("staging", "db_cpu"))
    development_odoo_workers: int = Field(0, ge=0, le=256, description=_limit("development", "workers"))
    development_odoo_cron_threads: int = Field(0, ge=0, le=64, description=_limit("development", "cron_threads"))
    development_odoo_limit_memory_soft_mb: int = Field(
        0, ge=0, le=1048576, description=_limit("development", "memory_soft")
    )
    development_odoo_limit_memory_hard_mb: int = Field(
        0, ge=0, le=1048576, description=_limit("development", "memory_hard")
    )
    development_odoo_limit_time_cpu: int = Field(0, ge=0, le=86400, description=_limit("development", "time_cpu"))
    development_odoo_limit_time_real: int = Field(0, ge=0, le=86400, description=_limit("development", "time_real"))
    development_odoo_memory_limit: str = Field(
        "0", pattern=MEMORY_PATTERN, description=_limit("development", "odoo_memory")
    )
    development_odoo_cpu_limit: float = Field(0, ge=0, le=1024, description=_limit("development", "odoo_cpu"))
    development_db_memory_limit: str = Field(
        "0", pattern=MEMORY_PATTERN, description=_limit("development", "db_memory")
    )
    development_db_cpu_limit: float = Field(0, ge=0, le=1024, description=_limit("development", "db_cpu"))

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
    custom_domains: Literal["", "off", "acme", "external"] = Field(
        "",
        description='Custom domains for instances: "off", "acme" (the gateway answers on ports 80 and 443 and gets '
        'a certificate for each domain from an ACME authority such as Let\'s Encrypt) or "external" (something in '
        "front, such as your own proxy, handles https; the panel only records the domains). Empty: as EXPOSURE "
        'suggests: "acme" with direct, "external" with proxy, "off" otherwise.',
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
    managed_subdomains: bool = Field(
        True,
        description="Give each instance a name under Sudoo.sh's domain (<branch>.<stage>.<company>.<base>) when the "
        "linked account's hub offers them, served with the panel's own certificates (ports 80 and 443 must reach "
        "this server). Off: no name is asked for and none is served; names held are released with their instance.",
    )
    managed_subdomain_company: str = Field(
        "",
        max_length=63,
        description="The company label asked for with the account's first managed name; empty: Sudoo.sh makes it "
        "from the company's name. It cannot change once the account has a name.",
    )
    managed_subdomains_check_minutes: int = Field(
        15, ge=1, le=1440, description="Minutes between two comparisons of the instances with their managed names."
    )

    @field_validator("managed_subdomain_company")
    @classmethod
    def _company_label(cls, value: str) -> str:
        import re

        value = value.strip().lower()
        if value and (
            not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", value) or (len(value) >= 4 and value[2:4] == "--")
        ):
            raise ValueError("must be a DNS label: a-z, 0-9 and -, not at either end, at most 63 characters")
        return value

    @field_validator(*(f"{stage}_{part}_memory_limit" for stage in STAGES for part in ("odoo", "db")))
    @classmethod
    def _container_memory(cls, value: str) -> str:
        if value != "0" and memory_bytes(value) < MIN_CONTAINER_MEMORY:
            raise ValueError("is less than 128m: a container needs more to start (0 for no limit)")
        return value

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

    @model_validator(mode="after")
    def _follow_exposure(self) -> Settings:
        """Empty CUSTOM_DOMAINS and PUBLIC_SCHEME take the value the exposure mode suggests."""
        mode = exposure_mode(self.exposure)
        if not self.custom_domains:
            object.__setattr__(
                self, "custom_domains", {"direct": "acme", "proxy": "external"}.get(mode, "off")
            )  # frozen: set once, while the object is built
        if not self.public_scheme:
            object.__setattr__(self, "public_scheme", "https" if mode == "proxy" else "http")
        return self

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

    # ------------------------------------------------------------------ projects (projects/)
    project_production_branches: int = Field(
        1,
        ge=0,
        description="Production branches one project may have; 0: no limit (MAX_PRODUCTION_INSTANCES still limits "
        "the whole server).",
    )
    project_auto_development: bool = Field(
        True,
        description="New projects create a development instance for every new branch pushed to their repository "
        "(each project can change it).",
    )
    project_max_auto_development: int = Field(
        3,
        ge=0,
        description="Development instances a project creates by itself for new branches, at most at the same time "
        "(each project can change it); 0: no limit.",
    )
    project_development_demo_data: bool = Field(
        True, description="Development instances of projects start with Odoo's demo data (each project can change it)."
    )
    project_staging_on_push: Literal["rebuild", "keep_data"] = Field(
        "rebuild",
        description='A push to a staging branch: "rebuild" (a new neutralised copy of production, then the new '
        'code) or "keep_data" (only the new code, like a development instance). Each project can change it.',
    )
    project_branch_deleted: Literal["keep", "archive", "delete"] = Field(
        "archive",
        description='A branch deleted on GitHub: its development instance is kept ("keep"), backed up and switched '
        'off ("archive") or deleted after a backup ("delete"); staging is archived at most, production only '
        "marked. Each project can change it.",
    )
    project_upgrade_test_on_push: bool = Field(
        True,
        description="A push to a project's upgrade branch (named after the next Odoo version, such as 18.0) runs its "
        "database upgrade test again (each project can change it).",
    )

    # ------------------------------------------------------------------ database upgrades (upgrades/)
    upgrade_methods: str = Field(
        "enterprise,openupgrade",
        description='How databases may be upgraded to a newer Odoo version, comma-separated: "enterprise" (Odoo\'s '
        'upgrade service, with the customer\'s Enterprise subscription code) and "openupgrade" (OCA OpenUpgrade, '
        "Community); empty: no upgrades.",
    )
    upgrade_service_url: str = Field(
        "https://upgrade.odoo.com",
        description="Address of Odoo's upgrade service; Odoo's upgrade script is downloaded from <address>/upgrade "
        "when an upgrade runs. Only in .env: the subscription code is sent there.",
    )
    openupgrade_repository: str = Field(
        "https://github.com/OCA/OpenUpgrade",
        description="GitHub repository of OpenUpgrade (OCA, AGPL-3.0). Its sources are downloaded at the pinned commit "
        "by the temporary container that runs the upgrade, never bundled with the panel.",
    )
    openupgrade_refs: str = Field(
        "16.0=08eae463b6bc00bd38abdc141b6f6aabe7ce3d44,17.0=0eddd35be9a1ee7db76f2afb138366ed3538403d,"
        "18.0=25f74e95a0f2495b91b771ace697082ee3cd121f,19.0=bdae60ef5cece7c0a5e02ef87bc5834ddb310d35,"
        "20.0=480dce61a36974913d3d883711f4593412e554b0",
        description="The OpenUpgrade commit used to upgrade to each Odoo version, as version=commit pairs, "
        "comma-separated (the commit of the repository's branch of that version); a version without one cannot be "
        "reached with OpenUpgrade.",
    )
    openupgrade_lib: str = Field(
        "openupgradelib==3.13.7", description="The openupgradelib package OpenUpgrade runs with (a pip requirement)."
    )
    upgrade_rollback_minutes: int = Field(
        60,
        ge=0,
        le=10080,
        description="After a production upgrade, how long Cancel the upgrade (back to the backup taken before it) is "
        "offered and Odoo is watched, rolling back by itself when it stops answering; 0: neither.",
    )
    upgrade_max_hours: float = Field(
        8, gt=0, le=72, description="Longest time one database upgrade (Odoo's service or OpenUpgrade) may take."
    )

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
        "before-restore, before-rollback, before-repair, staging-copy, before-upgrade.",
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
        known = (
            "manual",
            "daily",
            "deletion",
            "before-update",
            "before-restore",
            "before-rollback",
            "before-repair",
            "staging-copy",
            "before-upgrade",
        )
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

    # ------------------------------------------------------------------ support access (support/, docs/ADMIN_GUIDE.md)
    support_access: bool = Field(
        True,
        description="Super admins and administrators may grant Sudoo.sh support temporary access to the panel "
        "(Support access); off: no access can be granted and every grant ends.",
    )
    support_access_max_hours: int = Field(
        72,
        ge=1,
        le=72,
        description="The longest support access can be granted for, in hours (the choices are 1 hour, 1 day and 3 "
        "days, up to this).",
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

    # ------------------------------------------------------------------ servers (premium/servers.py, docs/ADMIN_GUIDE.md "Servers")
    server_name: str = Field(
        "Main", min_length=1, max_length=40, description="Name of this server, the main one, where instances show it."
    )
    server_region: str = Field(
        "", max_length=40, description="Region of this server, the main one (for example Frankfurt or eu-central)."
    )
    servers_panel_url: str = Field(
        "",
        description="Address the agents of other servers reach this panel at (https://...); empty: the panel's own "
        "public address. Several servers only.",
    )
    servers_join_code_minutes: int = Field(
        30, ge=5, le=1440, description="Minutes a join code for a new server stays valid; it works once."
    )
    servers_heartbeat_seconds: int = Field(
        15, ge=5, le=300, description="Seconds between two reports of each server's agent (status, instances, jobs)."
    )
    servers_offline_seconds: int = Field(
        90, ge=15, le=3600, description="A server whose agent has not reported for this long is shown offline."
    )
    servers_request_seconds: int = Field(
        30, ge=5, le=300, description="Seconds the panel waits for a server's agent to answer a request."
    )
    servers_wait_minutes: int = Field(
        60,
        ge=1,
        le=10080,
        description="Minutes a job on another server waits for the main panel when it needs it (a GitHub token), "
        "before it fails.",
    )
    servers_instances_kept: int = Field(
        2000,
        ge=10,
        le=100000,
        description="Instances the panel keeps of what one server's agent reports; beyond that, the rest of that "
        "server's report is not taken.",
    )
    servers_report_kb: int = Field(
        64,
        ge=8,
        le=4096,
        description="Largest description of one instance or job a server's agent may report, in KB; a larger one is "
        "not kept.",
    )
    servers_log_entries_per_day: int = Field(
        5000,
        ge=100,
        le=1000000,
        description="Log entries the panel takes from one server's agent per day; beyond that they are left out until "
        "the next day, which the log says once.",
    )
    move_max_backup_gb: float = Field(
        100, ge=1, le=10000, description="Largest backup an instance may have to be moved to another server, in GB."
    )
    move_chunk_mb: int = Field(8, ge=1, le=64, description="Size of each piece of a backup copied between servers.")
    move_at_once: int = Field(1, ge=1, le=10, description="Moves between servers that may run at the same time.")
    move_health_seconds: int = Field(
        300, ge=30, le=3600, description="Seconds a moved instance has to answer on its new server before the move "
        "is rolled back.",
    )  # fmt: skip

    @field_validator("servers_panel_url")
    @classmethod
    def _servers_panel_url(cls, value: str) -> str:
        return _plain_https(value, required=False)

    # ------------------------------------------------------------------ mail catcher (mailcatcher/, docs/ADMIN_GUIDE.md "Mail catcher")
    mail_catcher_retention_days: int = Field(
        7, ge=1, le=365, description="Days the e-mails caught from staging and development instances are kept."
    )
    mail_catcher_max_messages: int = Field(
        500, ge=10, le=100000, description="E-mails kept per instance in the mail catcher; the oldest go first."
    )
    mail_catcher_max_mb: int = Field(
        200,
        ge=10,
        le=100000,
        description="Space the caught e-mails of one instance may take, in MB; the oldest go first.",
    )
    mail_catcher_max_message_mb: int = Field(
        25, ge=1, le=100, description="Largest e-mail the mail catcher accepts, in MB; a larger one is refused."
    )
    mail_catcher_release: bool = Field(
        True,
        description="Administrators may release a caught e-mail: send a copy of it, from the panel's own address, "
        "to an address they type (audit-logged); off: nobody can.",
    )

    # ------------------------------------------------------------------ help (the "?" button)
    docs_url: str = Field(
        "https://docs.sudoo.sh", description="Where the documentation links of the help panel go (https)."
    )
    support_url: str = Field(
        "",
        description="Where Contact support goes when the panel is not linked to a Sudoo.sh account: an https:// "
        "address or a mailto: address; empty: an e-mail to ADMIN_CONTACT_EMAIL.",
    )
    hub_support_url: str = Field(
        "https://app.sudoo.sh/support",
        description="The help desk Contact support opens when the panel is linked to a Sudoo.sh account (https).",
    )
    plan_pricing_url: str = Field(
        "https://sudoo.sh/pricing",
        description="Where the panel sends people to compare the Sudoo.sh plans (Settings, Plan, and every message "
        "about a plan's limit) (https).",
    )
    plan_account_url: str = Field(
        "https://app.sudoo.sh",
        description="The account site where the plan of the linked account is managed and, after a downgrade, the "
        "production branches to keep are chosen (https).",
    )

    @field_validator("docs_url", "hub_support_url", "plan_pricing_url", "plan_account_url")
    @classmethod
    def _help_url(cls, value: str) -> str:
        return _plain_https(value, required=True)

    @field_validator("support_url")
    @classmethod
    def _support_url(cls, value: str) -> str:
        import re

        value = value.strip()
        if value.lower().startswith("mailto:"):
            if not re.fullmatch(r"mailto:[^@\s,?]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}", value, re.IGNORECASE):
                raise ValueError("must be mailto: followed by one e-mail address")
            return value
        return _plain_https(value, required=False)

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

    # ------------------------------------------------------------------ premium features (premium/, docs/PREMIUM.md)
    premium_public_keys: str = Field(
        "",
        description="The premium signing keys the panel trusts (Ed25519, base64url, comma-separated); empty: the keys "
        "this release ships. Only for a test panel.",
    )
    premium_binding_public_keys: str = Field(
        "",
        description="The premium binding keys the panel trusts (Ed25519, base64url, comma-separated): they sign the "
        "bindings of downloads to a server, never a package; empty: the keys this release ships. Only for a test "
        "panel.",
    )
    public_ipv4: str = Field(
        "",
        description="The server's public IPv4 address, sent to Sudoo.sh for managed subdomains; empty: the address "
        "the server sends from, when it is public.",
    )
    public_ipv6: str = Field(
        "",
        description="The server's public IPv6 address, sent to Sudoo.sh for managed subdomains; empty: the address "
        "the server sends from, when it is public.",
    )

    @field_validator("public_ipv4", "public_ipv6")
    @classmethod
    def _public_address(cls, value: str, info: Any) -> str:
        import ipaddress

        value = value.strip()
        if not value:
            return ""
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ValueError("must be an IP address") from None
        if address.version != (4 if info.field_name == "public_ipv4" else 6) or not address.is_global:
            raise ValueError("must be a public address of that family")
        return str(address)

    premium_auto_install: bool = Field(
        True,
        description="Install and update the premium features the plan includes automatically, after each signed "
        "lease. Off: they install only when an administrator presses Check now in Settings.",
    )
    premium_check_hours: float = Field(
        6, ge=0.25, le=168, description="Hours between checks for newer versions of the installed premium features."
    )
    premium_retry_minutes: float = Field(
        15, ge=1, le=1440, description="Minutes the panel waits before asking Sudoo.sh again after a check of the "
        "premium features (when one failed, or a feature was not ready yet).",
    )  # fmt: skip
    premium_watch_minutes: float = Field(
        10, ge=1, le=1440, description="Minutes between integrity passes over the installed premium features "
        "(their files, signature and binding to this server).",
    )  # fmt: skip
    premium_binding_renew_days: float = Field(
        7, ge=1, le=60, description="Days before a premium feature's binding to this server ends that the panel "
        "asks Sudoo.sh for a new one.",
    )  # fmt: skip
    premium_max_package_mb: int = Field(
        60, ge=5, le=500, description="Largest premium package the panel downloads, in MB."
    )

    @field_validator("hub_public_keys", "premium_public_keys", "premium_binding_public_keys")
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

    @field_validator("upgrade_methods")
    @classmethod
    def _known_upgrade_methods(cls, value: str) -> str:
        methods = [part.strip() for part in value.split(",") if part.strip()]
        unknown = [m for m in methods if m not in ("enterprise", "openupgrade")]
        if unknown:
            raise ValueError(f"unknown method {unknown[0]!r}; use enterprise, openupgrade or both")
        return ",".join(dict.fromkeys(methods))

    @field_validator("openupgrade_refs")
    @classmethod
    def _openupgrade_refs(cls, value: str) -> str:
        import re

        pairs = [part.strip() for part in value.split(",") if part.strip()]
        for pair in pairs:
            if not re.fullmatch(r"\d{1,3}\.\d=[0-9a-f]{40}", pair):
                raise ValueError(f"{pair[:60]!r} is not version=commit (a full 40-character commit)")
        return ",".join(pairs)

    @field_validator("openupgrade_lib")
    @classmethod
    def _openupgrade_lib(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"openupgradelib(==[0-9][0-9A-Za-z.]*)?", value.strip()):
            raise ValueError("must be openupgradelib or openupgradelib==<version>")
        return value.strip()

    @field_validator("openupgrade_repository")
    @classmethod
    def _openupgrade_repository(cls, value: str) -> str:
        import re

        value = value.strip().rstrip("/").removesuffix(".git")
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
            raise ValueError("must be a GitHub repository address, https://github.com/<owner>/<repository>")
        return value

    @field_validator("upgrade_service_url")
    @classmethod
    def _upgrade_service_url(cls, value: str) -> str:
        if not value.startswith("https://") and not value.startswith("http://127.0.0.1"):
            raise ValueError("must be an https:// address")
        return value.rstrip("/")

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
    def mail_catcher_dir(self) -> Path:
        """Shared by the panel and the mail catcher service (mailcatcher/): the caught e-mails of every staging and
        development instance and the catcher's settings, readable by root only."""
        return self.odoo_root / "mail"

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

RESOURCE_KNOBS = (
    "odoo_workers",
    "odoo_cron_threads",
    "odoo_limit_memory_soft_mb",
    "odoo_limit_memory_hard_mb",
    "odoo_limit_time_cpu",
    "odoo_limit_time_real",
    "odoo_memory_limit",
    "odoo_cpu_limit",
    "db_memory_limit",
    "db_cpu_limit",
)
"""The limits an admin can set per stage (setting <stage>_<knob>) and per instance (instances/resources.py)."""

RESOURCE_SETTINGS = tuple(
    name
    for name in Settings.model_fields
    if name.startswith("priority_") or any(name == f"{stage}_{knob}" for stage in STAGES for knob in RESOURCE_KNOBS)
)
"""Priority weights and default limits of each stage (instances/resources.py)."""

OFFSITE_SETTINGS = tuple(name for name in Settings.model_fields if name.startswith("offsite_"))
"""Every setting of off-site copies (offsite/); changing them also needs the permission offsite.configure."""

SHELL_SETTINGS = tuple(name for name in Settings.model_fields if name.startswith("shell_"))
"""Every web shell setting (webshell/); all of them can be changed in the panel."""

SERVER_SETTINGS = tuple(name for name in Settings.model_fields if name.startswith(("server_", "servers_", "move_")))
"""Every setting of servers and moves between them (premium/servers.py); all of them can be changed in the panel."""

PROJECT_SETTINGS = tuple(name for name in Settings.model_fields if name.startswith("project_"))
"""Every setting of projects (projects/); all of them can be changed in the panel."""

MAIL_CATCHER_SETTINGS = tuple(name for name in Settings.model_fields if name.startswith("mail_catcher_"))
"""Every setting of the mail catcher (mailcatcher/); all of them can be changed in the panel."""

EDITABLE = (
    # take effect at once
    "brand_color",
    "default_language",
    "licence_required_version",
    "link_deadline_days",
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
    *PROJECT_SETTINGS,
    "poll_minutes",
    "metrics_interval_seconds",
    "daily_backups",
    "daily_backup_time",
    "timezone",
    "rollback_hours",
    "safe_updates",
    "upgrade_methods",
    "openupgrade_repository",
    "openupgrade_refs",
    "openupgrade_lib",
    "upgrade_rollback_minutes",
    "upgrade_max_hours",
    "odoo_versions",
    "odoo_min_version",
    "prepull_images",
    "odoo_image",
    "postgres_image",
    "odoo_admin_password",
    *RESOURCE_SETTINGS,
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
    "premium_auto_install",
    "public_ipv4",
    "public_ipv6",
    "premium_check_hours",
    "premium_retry_minutes",
    "premium_watch_minutes",
    "premium_binding_renew_days",
    "premium_max_package_mb",
    "custom_domains",
    "panel_domain",
    "public_host",
    "acme_directory",
    "acme_email",
    "certificate_renew_days",
    "certificate_retry_max_hours",
    "domains_per_instance",
    "domain_dns_target",
    "domain_dns_timeout_seconds",
    "domain_http_probe",
    "managed_subdomains",
    "managed_subdomain_company",
    "managed_subdomains_check_minutes",
    *OFFSITE_SETTINGS,
    *ALERT_SETTINGS,
    *MAIL_CATCHER_SETTINGS,
    "docs_url",
    "support_url",
    "hub_support_url",
    "plan_pricing_url",
    "plan_account_url",
    *SHELL_SETTINGS,
    "support_access",
    "support_access_max_hours",
    *SERVER_SETTINGS,
)
"""Settings that can be changed in the panel, taking effect at once."""

RESTART = ("mailer", "second_factors", "update_triggers", "enterprise_source", "auth", "rate_limiter", "exposure")
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


_REPLACED_LIMITS = {
    "odoo_memory_limit": "odoo_memory_limit",
    "odoo_cpu_limit": "odoo_cpu_limit",
    "db_memory_limit": "db_memory_limit",
    "db_cpu_limit": "db_cpu_limit",
}
"""Container limits for every instance, before 3.0 (ODOO_MEMORY_LIMIT...) -> the per-stage defaults that replaced
them (<stage>_odoo_memory_limit...)."""


def _migrated(saved: dict[str, Any]) -> dict[str, Any]:
    """settings.json of an earlier panel: a container limit an admin set in the panel for every instance becomes the
    default of every stage (unless one is set already). The values of the template .env are not carried over: the
    limits are off by default since 3.0."""
    saved = dict(saved)
    for old, knob in _REPLACED_LIMITS.items():
        if old not in saved:
            continue
        value = saved.pop(old)
        for stage in STAGES:
            saved.setdefault(f"{stage}_{knob}", value)
    return saved


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
            saved = _migrated(json.loads(self._file.read_text()))
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

    def notify(self, changed: Mapping[str, Any]) -> None:
        """Call the listeners as after a change (a server's agent took new values from the main panel: use())."""
        for listener in self._listeners:
            try:
                listener(dict(changed))
            except Exception as error:  # noqa: BLE001 - one listener never stops the others
                print(f"settings listener: {error!r}", flush=True)

    def on_change(self, listener: Callable[[dict[str, Any]], None]) -> None:
        """Call `listener(changed values)` after every change."""
        self._listeners.append(listener)


live = LiveSettings()
settings = cast(Settings, live)
"""The current settings. Typed as Settings: everything else in the panel reads it like a Settings object."""
