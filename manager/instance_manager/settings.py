"""Settings, read once from the environment (the panel's .env file, passed in by docker compose).

Only values an administrator may want to change are read from the environment; everything else is a constant
here, so there is exactly one place to look.
"""
import os
from pathlib import Path


def _text(name, default):
    return os.environ.get(name, default).strip()


def _number(name, default):
    return float(os.environ.get(name, "") or default)


# --------------------------------------------------------------------------- locations
ROOT = Path(_text("ODOO_ROOT", "/opt/odoo"))
INSTANCES_DIR = ROOT / "instances"
BACKUPS_DIR = ROOT / "backups"                # Odoo-format backup zips, taken before every deletion
ENTERPRISE_DIR = ROOT / "enterprise"          # one folder per version: enterprise/19.0, enterprise/20.0, ...
MANAGER_DIR = ROOT / "manager"
KEYS_DIR = MANAGER_DIR / "keys"
DEPLOY_KEYS_DIR = KEYS_DIR / "deploy"         # one read-only GitHub deploy key per repository
STATE_FILE = MANAGER_DIR / "state.json"
USERS_FILE = MANAGER_DIR / "users.json"
AUDIT_FILE = MANAGER_DIR / "audit.log"
SECRET_FILE = MANAGER_DIR / "secret_key"           # signs session cookies
VAULT_KEY_FILE = MANAGER_DIR / "vault.key"        # encrypts stored secrets and peppers password hashes (vault.py)

DOCS_DIR = Path(__file__).resolve().parent.parent / "documentation"   # README.md and docs/, copied by install.sh
MEMORY_DIR = "/dev/shm"                       # memory-backed folder for secrets needed briefly as files

# paths inside the Odoo containers
CUSTOM_ADDONS_MOUNT = "/mnt/extra-addons"
ENTERPRISE_ADDONS_MOUNT = "/mnt/enterprise-addons"

# --------------------------------------------------------------------------- ports (all on 127.0.0.1 only)
MANAGER_PORT = 9000                 # the panel
GATEWAY_PORT_START = 8069           # one gateway port per instance, the target of its connector
ODOO_PORT_START = 28069             # one Odoo port per instance
CONNECTOR_METRICS_PORT_START = 20300   # one status port per instance connector
PANEL_CONNECTOR_METRICS_PORT = 20242

# --------------------------------------------------------------------------- images
ODOO_IMAGE = "odoo"                 # official image, tag = Odoo version
POSTGRES_IMAGE = "postgres:16"
CLOUDFLARED_IMAGE = "cloudflare/cloudflared:latest"

# --------------------------------------------------------------------------- behaviour (from .env)
ODOO_VERSIONS = [v.strip() for v in _text("ODOO_VERSIONS", "18.0,19.0,20.0").split(",") if v.strip()]
ODOO_ADMIN_PASSWORD = _text("ODOO_ADMIN_PASSWORD", "admin")
IDLE_HOURS = _number("IDLE_HOURS", 6)                  # pause an instance after this long without visits
DELETE_AFTER_DAYS = int(_number("DELETE_AFTER_DAYS", 30))   # lifetime offered for new instances (0: keep them)
POLL_MINUTES = _number("POLL_MINUTES", 2)              # check GitHub for new commits this often
SESSION_IDLE_MINUTES = _number("SESSION_IDLE_MINUTES", 60)
SESSION_MAX_HOURS = _number("SESSION_MAX_HOURS", 12)
ADMIN_CONTACT = {
    "name": _text("ADMIN_CONTACT_NAME", "Yousef M. Y. Al Sabbah"),
    "phone": _text("ADMIN_CONTACT_PHONE", ""),
    "email": _text("ADMIN_CONTACT_EMAIL", ""),
}

# --------------------------------------------------------------------------- intervals
MONITOR_INTERVAL_SECONDS = 15
PAUSE_CHECK_INTERVAL_SECONDS = 120
EXPIRY_CHECK_INTERVAL_SECONDS = 600
HISTORY_SAMPLES = 120               # 30 minutes at one sample every 15 seconds


def contact_sentence():
    """'Please contact the administrator, <name> (<phone>, <email>).' for messages shown to sync operators."""
    details = ", ".join(v for v in (ADMIN_CONTACT["phone"], ADMIN_CONTACT["email"]) if v)
    return f"Please contact the administrator, {ADMIN_CONTACT['name']}" + (f" ({details})." if details else ".")
