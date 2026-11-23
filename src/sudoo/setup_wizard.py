"""The setup wizard of the first-run page: the choices made in the browser before the first account exists.

Steps (web/src/pages/Setup.tsx): 1 welcome and licence, 2 server check and engine, 3 address, 4 e-mail (optional,
with a test), 5 GitHub App (optional), 6 Sudoo.sh account (optional), 7 the first super admin, 8 done.

Until the final confirm (POST /api/v1/setup, web/v1/session.py) nothing is applied: the choices are a draft, kept in
<ODOO_ROOT>/manager/setup-draft.json (mode 600, the SMTP password encrypted with the vault key) so the setup can be
left and resumed from any browser with the setup code. The confirm creates the super admin, then applies the draft
(apply()): settings (address, e-mail, language), the link to the Sudoo.sh account with its link code. Connecting the
GitHub App goes through GitHub's pages, which need a signed-in super admin, so it is offered on the last step.

The engine is never changed from the browser: when the choice differs from what the installer prepared, the page
shows the command to run on the server (`sudo sudoo-install engine kubernetes`).

An install code (sudoo-install.sh --code) may have filled in installer/install-code: its engine, company label,
time zone, language and link code are the draft's starting values.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

from sudoo import accounts, config, server_check
from sudoo.config import live, settings
from sudoo.core import vault

STEPS = ("welcome", "server", "address", "email", "github", "account", "admin", "done")
EXPOSURES = config.EXPOSURE_MODES
NAME_KINDS = ("server", "domain", "managed")
SECURITY = ("starttls", "ssl", "none")
_LOCK = threading.Lock()
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)((?!-)[a-z0-9-]{1,63}(?<!-)\.)+(?!-)[a-z0-9-]{1,63}(?<!-)$")
LINK_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,31}$")


class DraftError(ValueError):
    """A value of the draft that cannot be taken; the message says which and why."""


def _file() -> Any:
    return settings.manager_dir / "setup-draft.json"


def install_code() -> dict[str, str]:
    """What an install code filled in (sudoo-install.sh --code), {} without one."""
    return server_check.key_values(settings.manager_dir / "installer" / "install-code")


def defaults() -> dict[str, Any]:
    """The draft before anything is chosen: the panel's current settings, the install code's values on top."""
    code = install_code()
    check_engine = settings.engine_wanted or settings.engine
    domain = settings.panel_domain
    return {
        "step": "welcome",
        "language": code.get("LANGUAGE") or settings.default_language,
        "engine": code.get("ENGINE") or check_engine,
        "needs": {"grow": None, "separation": None, "team": None},
        "exposure": (
            config.exposure_mode(settings.exposure)
            if config.exposure_mode(settings.exposure) in EXPOSURES
            else "direct"
        ),
        "name": {"kind": "domain" if domain else "server", "domain": domain},
        "mail": {
            "enabled": bool(settings.smtp_host),
            "host": settings.smtp_host,
            "port": settings.smtp_port,
            "security": settings.smtp_security,
            "username": settings.smtp_username,
            "sender": settings.mail_from,
            "password_set": bool(settings.smtp_password),
        },
        "github": {"connect": False},
        "account": {
            "link": bool(code.get("LINK_CODE")),
            "link_code": code.get("LINK_CODE", ""),
            "company_label": code.get("COMPANY_LABEL") or settings.managed_subdomain_company,
            "from_install_code": bool(code),
        },
        "admin": {"first_name": "", "last_name": "", "email": "", "phone": ""},
    }


def _stored() -> dict[str, Any]:
    try:
        data = json.loads(_file().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read() -> dict[str, Any]:
    """The draft as the page shows it: defaults, then what was saved; the SMTP password only as set or not."""
    draft = defaults()
    saved = _stored()
    for key, value in saved.items():
        if key == "secrets":
            continue
        if isinstance(draft.get(key), dict) and isinstance(value, dict):
            draft[key] = {**draft[key], **value}
        elif key in draft:
            draft[key] = value
    draft["mail"]["password_set"] = bool(saved.get("secrets", {}).get("smtp_password")) or draft["mail"]["password_set"]
    return draft


def _text(value: Any, name: str, limit: int = 254) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise DraftError(f"{name}: not a valid text.")
    return value.strip()


def _choice(value: Any, name: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise DraftError(f"{name}: choose one of {', '.join(choices)}.")
    return str(value)


def clean(changes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """The validated parts of a draft change and its secrets; raises DraftError for anything else."""
    unknown = set(changes) - {
        "step",
        "language",
        "engine",
        "needs",
        "exposure",
        "name",
        "mail",
        "github",
        "account",
        "admin",
    }
    if unknown:
        raise DraftError(f"Unknown part of the setup: {', '.join(sorted(unknown))}.")
    out: dict[str, Any] = {}
    secrets: dict[str, str] = {}
    if "step" in changes:
        out["step"] = _choice(changes["step"], "step", STEPS)
    if "language" in changes:
        out["language"] = _choice(changes["language"], "language", ("en", "ar"))
    if "engine" in changes:
        out["engine"] = _choice(changes["engine"], "engine", server_check.ENGINES)
    if "needs" in changes:
        needs = changes["needs"] if isinstance(changes["needs"], dict) else {}
        if set(needs) - set(server_check.NEEDS) or any(v not in (True, False, None) for v in needs.values()):
            raise DraftError("needs: grow, separation and team, each true, false or null.")
        out["needs"] = {key: needs.get(key) for key in server_check.NEEDS}
    if "exposure" in changes:
        out["exposure"] = _choice(changes["exposure"], "exposure", EXPOSURES)
    if "name" in changes:
        name = changes["name"] if isinstance(changes["name"], dict) else {}
        kind = _choice(name.get("kind"), "name.kind", NAME_KINDS)
        domain = _text(name.get("domain"), "name.domain").lower().rstrip(".")
        if kind == "domain" and not DOMAIN_RE.match(domain):
            raise DraftError("name.domain: enter a name such as panel.example.com.")
        out["name"] = {"kind": kind, "domain": domain if kind == "domain" else ""}
    if "mail" in changes:
        mail = changes["mail"] if isinstance(changes["mail"], dict) else {}
        enabled = bool(mail.get("enabled"))
        port = mail.get("port", 587)
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise DraftError("mail.port: a port from 1 to 65535.")
        host = _text(mail.get("host"), "mail.host").lower()
        if enabled and not re.fullmatch(r"[a-z0-9.-]{1,253}", host):
            raise DraftError("mail.host: the mail server's name, such as smtp.example.com.")
        sender = _text(mail.get("sender"), "mail.sender")
        username = _text(mail.get("username"), "mail.username")
        if enabled and "@" not in (sender or username):
            raise DraftError("mail.sender: an e-mail address, such as Sudoo.sh <no-reply@example.com>.")
        out["mail"] = {
            "enabled": enabled,
            "host": host,
            "port": port,
            "security": _choice(mail.get("security", "starttls"), "mail.security", SECURITY),
            "username": username,
            "sender": sender,
        }
        if isinstance(mail.get("password"), str) and mail["password"]:
            if len(mail["password"]) > 256:
                raise DraftError("mail.password: too long.")
            secrets["smtp_password"] = mail["password"]
    if "github" in changes:
        github = changes["github"] if isinstance(changes["github"], dict) else {}
        out["github"] = {"connect": bool(github.get("connect"))}
    if "account" in changes:
        account = changes["account"] if isinstance(changes["account"], dict) else {}
        link = bool(account.get("link"))
        code = _text(account.get("link_code"), "account.link_code", 40).upper().replace(" ", "")
        if link and not LINK_CODE_RE.match(code):
            raise DraftError("account.link_code: the link code from your Sudoo.sh account (letters, digits, dashes).")
        label = _text(account.get("company_label"), "account.company_label", 63).lower()
        if label and not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", label):
            raise DraftError("account.company_label: lower-case letters, digits and dashes.")
        out["account"] = {"link": link, "link_code": code if link else "", "company_label": label}
    if "admin" in changes:
        admin = changes["admin"] if isinstance(changes["admin"], dict) else {}
        out["admin"] = {key: _text(admin.get(key), f"admin.{key}", 120) for key in accounts.PROFILE_FIELDS}
    return out, secrets


def save(changes: dict[str, Any]) -> dict[str, Any]:
    """Merge validated changes into the stored draft; returns the draft as read() shows it. Nothing is applied."""
    cleaned, secrets = clean(changes)
    with _LOCK:
        stored = _stored()
        for key, value in cleaned.items():
            if isinstance(value, dict) and isinstance(stored.get(key), dict):
                stored[key] = {**stored[key], **value}
            else:
                stored[key] = value
        if secrets:
            stored["secrets"] = {
                **stored.get("secrets", {}),
                **{key: vault.encrypt(value, f"setup:{key}") for key, value in secrets.items()},
            }
        vault.write_private(_file(), json.dumps(stored, indent=1))
    return read()


def secret(key: str) -> str:
    encrypted = _stored().get("secrets", {}).get(key)
    return vault.decrypt(encrypted, f"setup:{key}") if encrypted else ""


def setting_changes(draft: dict[str, Any]) -> dict[str, Any]:
    """The settings the draft changes (only those that differ from the current ones)."""
    wanted: dict[str, Any] = {"default_language": draft["language"]}
    exposure = draft["exposure"]
    if exposure != config.exposure_mode(settings.exposure):
        wanted["exposure"] = exposure
    name = draft["name"]
    if name["kind"] == "domain":
        wanted["panel_domain"] = name["domain"]
    elif name["kind"] == "managed":
        wanted["panel_managed_name"] = True
        wanted["managed_subdomains"] = True
    label = draft["account"].get("company_label")
    if label and label != settings.managed_subdomain_company:
        wanted["managed_subdomain_company"] = label
    mail = draft["mail"]
    if mail["enabled"]:
        wanted.update(
            smtp_host=mail["host"],
            smtp_port=mail["port"],
            smtp_security=mail["security"],
            smtp_username=mail["username"],
            mail_from=mail["sender"],
        )
        password = secret("smtp_password")
        if password:
            wanted["smtp_password"] = password
    if draft["account"]["link"] or name["kind"] == "managed":
        wanted["marketplace"] = True
    return {key: value for key, value in wanted.items() if getattr(settings, key) != value}


def validate(draft: dict[str, Any]) -> dict[str, Any]:
    """The settings changes, checked as the panel would check them, before anything exists; raises DraftError."""
    from pydantic import ValidationError

    changes = setting_changes(draft)
    refused = sorted(set(changes) - set(config.EDITABLE) - set(config.RESTART))
    if refused:
        raise DraftError(f"These settings can only be changed in .env: {', '.join(refused)}.")
    if draft["exposure"] == "proxy" and draft["name"]["kind"] != "domain" and not settings.public_host:
        raise DraftError("Proxy needs the name your proxy serves the panel under: choose My own name.")
    try:
        config.Settings(**{**live.overrides(), **changes})
    except ValidationError as error:
        first = error.errors()[0]
        raise DraftError(f"{str(first['loc'][0]).upper()}: {first['msg']}.") from error
    return changes


def mail_settings(draft: dict[str, Any], password: str | None = None) -> config.Settings:
    """Settings with the draft's mail server, for a test before anything is saved."""
    mail = draft["mail"]
    return config.Settings(
        mailer="smtp",
        smtp_host=mail["host"],
        smtp_port=mail["port"],
        smtp_security=mail["security"],
        smtp_username=mail["username"],
        smtp_password=password if password is not None else secret("smtp_password"),
        mail_from=mail["sender"],
    )


def apply(changes: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    """After the first account exists: the settings, then the link to the account. Returns what happened."""
    result: dict[str, Any] = {
        "settings": sorted(changes),
        "restart_needed": False,
        "linked": None,
        "link_error": None,
        "github": bool(draft["github"]["connect"]),
        "engine": {
            "chosen": draft["engine"],
            "current": settings.engine,
            "command": None if draft["engine"] == settings.engine else f"sudo sudoo-install engine {draft['engine']}",
        },
    }
    if changes:
        live.change(changes)
        result["restart_needed"] = any(live.pending_restart(key) for key in config.RESTART)
    if draft["account"]["link"] and draft["account"]["link_code"]:
        try:
            from sudoo.hub import marketplace

            linked = marketplace.link(draft["account"]["link_code"])
            result["linked"] = {"account_name": linked.get("account_name")}
        except Exception as error:  # noqa: BLE001 - shown on the last step; linking again is on Settings
            result["link_error"] = str(error) or error.__class__.__name__
    finish()
    return result


def finish() -> None:
    """Forget the draft and the install code's one-time link code."""
    _file().unlink(missing_ok=True)
    (settings.manager_dir / "installer" / "install-code").unlink(missing_ok=True)
