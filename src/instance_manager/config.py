"""Every setting of the panel, typed and documented in one place.

Values come from environment variables (the panel's `.env` file, passed in by Docker Compose). Names are case
insensitive: the field `idle_hours` is set with `IDLE_HOURS=6`. A wrong value (for example `IDLE_HOURS=soon`) stops
the panel at start-up with a message naming the setting, instead of failing later.

The settings that choose plugins (`exposure`, `enterprise_source`) name an entry point; see plugins.py and
docs/PLUGINS.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
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
    postgres_image: str = Field("postgres:16", description="Docker image of each instance's database.")
    odoo_admin_password: str = Field("admin", description='Password of the Odoo user "admin" in new instances.')

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
    connector_prefix: str = Field("oim-connector-", description="cloudflare-quick: container name prefix.")
    connector_status_port_start: int = Field(20300, description="cloudflare-quick: first status port of a connector.")
    panel_connector_status_port: int = Field(20242, description="cloudflare-quick: status port of the panel connector.")
    public_host: str = Field("", description="host-ports: the server's public name or address.")
    public_scheme: Literal["http", "https"] = Field("http", description="host-ports: scheme of the public addresses.")

    # ------------------------------------------------------------------ update triggers (plugins)
    update_triggers: str = Field(
        "polling", description='What starts code updates, comma-separated: "polling", "github-webhook" or both.'
    )
    github_webhook_secret: str = Field(
        "", description="github-webhook: the secret entered on GitHub (20 or more random characters)."
    )

    # ------------------------------------------------------------------ ports
    panel_port: int = Field(9000, description="Port of the panel.")
    gateway_port_start: int = Field(8069, description="First gateway port (one per instance).")
    odoo_port_start: int = Field(28069, description="First Odoo port (one per instance, 127.0.0.1 only).")

    # ------------------------------------------------------------------ behaviour
    idle_hours: float = Field(6, ge=0, description="Pause an instance after this many hours without visits; 0: never.")
    delete_after_days: int = Field(30, ge=0, description="Lifetime offered for new instances, in days; 0: keep.")
    poll_minutes: float = Field(2, gt=0, description="polling: check GitHub for new commits this often.")
    session_idle_minutes: float = Field(60, gt=0, description="Panel sign-out after this long without activity.")
    session_max_hours: float = Field(12, gt=0, description="Longest panel session.")
    admin_contact_name: str = Field("the administrator", description="Who users are told to contact.")
    admin_contact_phone: str = ""
    admin_contact_email: str = ""

    # ------------------------------------------------------------------ fixed intervals (not settings)
    monitor_interval_seconds: int = 15
    pause_check_interval_seconds: int = 120
    expiry_check_interval_seconds: int = 600
    history_samples: int = 120  # 30 minutes at one sample every 15 seconds

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
    def keys_dir(self) -> Path:
        return self.manager_dir / "keys"

    @property
    def deploy_keys_dir(self) -> Path:
        """One read-only GitHub deploy key per repository."""
        return self.keys_dir / "deploy"

    @property
    def state_file(self) -> Path:
        return self.manager_dir / "state.json"

    @property
    def users_file(self) -> Path:
        return self.manager_dir / "users.json"

    @property
    def audit_file(self) -> Path:
        return self.manager_dir / "audit.log"

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
        return f"Please contact the administrator, {self.admin_contact_name}" + (f" ({details})." if details else ".")


settings = Settings()
