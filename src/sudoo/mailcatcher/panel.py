"""The panel's side of the mail catcher (see __init__.py): which instances it serves, their SMTP passwords and
odoo.conf settings, the catcher's settings file, its network connections, the instances' own mail servers switched
off, retention, and releasing a caught e-mail.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections.abc import Mapping
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Any

from sudoo.config import settings
from sudoo.core.shell import capture, succeeds
from sudoo.core.vault import write_private
from sudoo.instances import stages, store

from . import FORMAT, HOST, PORT, SETTINGS_FILE, mailbox
from .server import limits_for

PASSWORD_KEY = "MAIL_CATCHER_PASSWORD"
CONF_KEYS = ("smtp_server", "smtp_port", "smtp_user", "smtp_password", "smtp_ssl")
MAINTAIN_SECONDS = 30.0  # settings file and network connections
CLEAN_SECONDS = 300.0  # retention, mailboxes of deleted instances, mail servers switched off again

_LOCK = threading.Lock()


def catches(env: Mapping[str, str]) -> bool:
    """Whether the instance's e-mail goes to the catcher: every stage but production."""
    return not stages.is_production(env)


def password(name: str) -> str:
    """The instance's SMTP password for the catcher; made the first time it is needed."""
    with _LOCK:
        found = store.read_env(name).get(PASSWORD_KEY, "")
        if not found:
            found = secrets.token_urlsafe(24)
            store.update_env(name, **{PASSWORD_KEY: found})
        return found


def conf_options(name: str) -> dict[str, str | None]:
    """odoo.conf's SMTP options for the instance: the catcher for staging and development, none for production
    (None: the option is removed)."""
    if not catches(store.read_env(name)):
        return dict.fromkeys(CONF_KEYS)
    return {
        "smtp_server": HOST,
        "smtp_port": str(PORT),
        "smtp_user": name,
        "smtp_password": password(name),
        "smtp_ssl": "False",
    }


def write_conf(name: str) -> bool:
    """Write the SMTP options into the instance's odoo.conf (a hand-edited odoo.conf is replaced by the panel's own
    first). Production loses only options that point at the catcher. Returns whether anything changed."""
    from sudoo.instances import integrity, odoo

    wanted = conf_options(name)
    with integrity.writing(name, integrity.CONF):
        integrity.rewrite_if_changed(name)
        conf = odoo.read_config(name)
        if not conf.has_section("options"):
            return False
        ours = conf.get("options", "smtp_server", fallback=None) == HOST
        changed = False
        for option, value in wanted.items():
            current = conf.get("options", option, fallback=None)
            if value is None:
                if current is not None and ours:
                    conf.remove_option("options", option)
                    changed = True
            elif current != value:
                conf.set("options", option, value)
                changed = True
        if changed:
            with open(store.config_file(name), "w") as fh:
                conf.write(fh)
    return changed


# --------------------------------------------------------------------------- the catcher's settings file


def mailboxes() -> dict[str, str]:
    """Instance -> SHA-256 of its password, for every staging and development instance."""
    found = {}
    for name in store.names():
        try:
            env = store.read_env(name)
        except OSError:
            continue
        if catches(env) and env.get(PASSWORD_KEY):
            found[name] = hashlib.sha256(env[PASSWORD_KEY].encode()).hexdigest()
    return found


def write_settings() -> bool:
    """Write catcher.json when what it should say changed. Returns whether it was written."""
    folder = settings.mail_catcher_dir
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    from sudoo.config import MAIL_CATCHER_SETTINGS

    values = {key: getattr(settings, key) for key in MAIL_CATCHER_SETTINGS}
    text = json.dumps(
        {"format": FORMAT, "limits": limits_for(values), "mailboxes": mailboxes()}, indent=1, sort_keys=True
    )
    path = folder / SETTINGS_FILE
    try:
        if path.read_text() == text:
            return False
    except OSError:
        pass
    write_private(path, text)
    return True


# --------------------------------------------------------------------------- the catcher's container


def network(name: str) -> str:
    """The instance's own Compose network, where its Odoo container (not its database) is."""
    return f"odoo-{name}_default"


def state() -> dict[str, Any]:
    """Whether the catcher's container exists and runs, and the networks it is connected to."""
    found = capture(["docker", "inspect", "--format", "{{json .}}", HOST], timeout=10)
    try:
        info = json.loads(found) if found else None
    except ValueError:
        info = None
    if not isinstance(info, dict):
        return {"exists": False, "running": False, "networks": []}
    return {
        "exists": True,
        "running": bool(info.get("State", {}).get("Running")),
        "networks": sorted((info.get("NetworkSettings", {}).get("Networks") or {}).keys()),
    }


def attach(name: str) -> bool:
    """Connect the catcher to the instance's network (staging and development). False when it could not be done
    (no catcher, the network is not there yet): the next round of maintain() tries again."""
    return succeeds(["docker", "network", "connect", "--alias", HOST, network(name), HOST], timeout=20)


def detach(name: str) -> bool:
    """Disconnect the catcher from the instance's network: before the instance's containers and networks are
    removed (Docker refuses to remove a network something is still connected to), or when it moves to production."""
    return succeeds(["docker", "network", "disconnect", "--force", network(name), HOST], timeout=20)


def reconcile() -> dict[str, list[str]]:
    """Connect the catcher to the network of every staging and development instance and disconnect it from the
    others. Returns what was connected and disconnected."""
    current = state()
    done: dict[str, list[str]] = {"connected": [], "disconnected": []}
    if not current["exists"]:
        return done
    wanted = set()
    for name in store.names():
        try:
            if catches(store.read_env(name)):
                wanted.add(network(name))
        except OSError:
            continue
    present = set(current["networks"])
    for name in store.names():
        net = network(name)
        if net in wanted and net not in present and attach(name):
            done["connected"].append(name)
        elif net in present and net not in wanted and detach(name):
            done["disconnected"].append(name)
    for net in present:  # networks of instances that are gone
        if net.startswith("odoo-") and net.endswith("_default") and net not in wanted:
            name = net.removeprefix("odoo-").removesuffix("_default")
            if not store.exists(name) and detach(name):
                done["disconnected"].append(name)
    return done


# --------------------------------------------------------------------------- the instances' own mail servers

SWITCH_OFF_SQL = (
    "DO $$ BEGIN IF to_regclass('ir_mail_server') IS NOT NULL THEN "
    "UPDATE ir_mail_server SET active = false WHERE active; END IF; END $$;"
    "SELECT 'switched-off';"
)
COUNT_SQL = (
    "SELECT CASE WHEN to_regclass('ir_mail_server') IS NULL THEN 0 ELSE "
    "(SELECT count(*) FROM ir_mail_server WHERE active) END;"
)


def switch_off_mail_servers(name: str) -> int:
    """Switch off every outgoing mail server (ir.mail_server) in the databases of a staging or development
    instance, so Odoo sends through odoo.conf's server: the catcher. Returns how many were active. Its database
    container must be running; production instances are never touched."""
    from sudoo.instances import odoo

    if not catches(store.read_env(name)):
        return 0
    container = store.container(name, "db")
    count = 0
    for db in odoo.databases(name):
        psql = ["docker", "exec", container, "psql", "-q", "-X", "-U", "odoo", "-d", db, "-tAc"]
        active = capture([*psql, COUNT_SQL], timeout=30).strip()
        if active.isdigit() and int(active):
            capture([*psql, SWITCH_OFF_SQL], timeout=30)
            count += int(active)
    return count


# --------------------------------------------------------------------------- keeping it all in order


def clean(now: float | None = None) -> dict[str, int]:
    """Remove e-mails past the retention or beyond the limits, and the mailboxes of instances that are gone or in
    production. Returns how many e-mails were removed per instance."""
    now = time.time() if now is None else now
    older_than = now - settings.mail_catcher_retention_days * 86400
    removed: dict[str, int] = {}
    keep = set(mailboxes())
    for name in mailbox.boxes():
        if name not in keep:
            if not store.exists(name) or not catches(store.read_env(name)):
                removed[name] = mailbox.usage(name)["messages"]
                mailbox.remove_box(name)
            continue
        count = mailbox.prune(
            name,
            max_messages=settings.mail_catcher_max_messages,
            max_bytes=settings.mail_catcher_max_mb * 1024**2,
            older_than=older_than,
        )
        if count:
            removed[name] = count
    return removed


def maintain_forever() -> None:
    """Every 30 seconds: catcher.json and the network connections; every 5 minutes: retention, and the mail servers
    of running staging and development instances switched off again (one turned on meanwhile sends nothing more)."""
    from sudoo.core import jobs
    from sudoo.instances import monitor

    last_clean = 0.0
    while True:
        try:
            write_settings()
            reconcile()
            if time.time() - last_clean >= CLEAN_SECONDS:
                last_clean = time.time()
                clean()
                for name in store.names():
                    running = monitor.STATUS.get(name, {}).get("running")
                    skip = not running or jobs.busy(name) or not catches(store.read_env(name))
                    if not skip and switch_off_mail_servers(name):
                        from sudoo.core import audit

                        audit.record(
                            "system",
                            None,
                            "outgoing mail servers switched off (staging or development instance)",
                            instance=name,
                        )
        except Exception as error:  # noqa: BLE001 - one bad round must not end the loop
            print(f"mailcatcher: maintenance failed: {error!r}", flush=True)
        time.sleep(MAINTAIN_SECONDS)


def prepare(name: str) -> None:
    """Before the instance's containers start: its odoo.conf options and the catcher's settings."""
    write_conf(name)
    write_settings()


def after_start(name: str) -> int:
    """After the instance's containers started: the catcher on its network, its own mail servers switched off.
    Returns how many mail servers were switched off."""
    if not catches(store.read_env(name)):
        detach(name)
        return 0
    attach(name)
    return switch_off_mail_servers(name)


def forget(name: str) -> None:
    """The instance is being deleted: off its network first, then its mailbox."""
    detach(name)
    mailbox.remove_box(name)


# --------------------------------------------------------------------------- releasing


class ReleaseError(RuntimeError):
    pass


_DROPPED = ("From", "To", "Cc", "Bcc", "Sender", "Reply-To", "Return-Path", "Message-ID", "Date", "DKIM-Signature",
            "ARC-Seal", "ARC-Message-Signature", "ARC-Authentication-Results", "Delivered-To")  # fmt: skip


def released(name: str, message_id: str, to: str) -> EmailMessage:
    """The copy of a caught e-mail that releasing sends: the same content and attachments, from the panel's own
    address (sending as the original sender would fail SPF and DKIM), to `to` alone, its original sender as
    Reply-To, and headers that say where it comes from."""
    data = mailbox.raw(name, message_id)
    if data is None:
        raise LookupError(message_id)
    message = mailbox.parse(data)
    original_from = str(message.get("From", "") or "")
    subject = str(message.get("Subject", "") or "")
    for key in _DROPPED:
        del message[key]
    del message["Subject"]
    sender = settings.mail_from or settings.smtp_username
    message["From"] = sender
    message["To"] = to
    message["Subject"] = f"[{name}] {subject}".strip()
    if "@" in parseaddr(original_from)[1]:
        message["Reply-To"] = original_from
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=(sender.split("@")[-1].rstrip(">") or "sudoo.local"))
    message["X-Sudoo-Released-From"] = f"{name} {message_id}"
    return message


def release(name: str, message_id: str, to: str) -> None:
    """Send a copy of a caught e-mail to `to` through the panel's mailer. Raises LookupError (no such e-mail),
    ReleaseError (switched off, or the mailer cannot send) or mail.MailError."""
    from sudoo import mail

    if not settings.mail_catcher_release:
        raise ReleaseError("Releasing caught e-mails is switched off on this panel (Settings, E-mail).")
    sender = mail.mailer()
    if not sender.ready:
        raise ReleaseError("The panel cannot send e-mail: set up its e-mail first (Settings, E-mail).")
    message = released(name, message_id, to)
    send_message = getattr(sender, "send_message", None)
    if callable(send_message):
        send_message(message)
        return
    # a mailer plugin that only sends text and HTML: the copy without its attachments
    text, html = "", None
    body = message.get_body(("plain",))
    if body is not None:
        text = body.get_content()
    html_part = message.get_body(("html",))
    if html_part is not None:
        html = html_part.get_content()
    sender.send(to, str(message["Subject"]), text or "(this e-mail has no text part)", html)


def stage_changed(name: str) -> None:
    """The instance moved to another stage: odoo.conf and the catcher's settings now, the network and its own mail
    servers in the background (Odoo reads odoo.conf when it starts: until its next start, an instance that left
    production has its mail servers switched off, so it sends nothing at all)."""
    write_conf(name)
    write_settings()

    def network_and_servers() -> None:
        try:
            after_start(name)
        except Exception as error:  # noqa: BLE001 - maintain_forever catches up
            print(f"mailcatcher: {name}: {error!r}", flush=True)

    threading.Thread(target=network_and_servers, daemon=True, name=f"mail-catcher-{name}").start()
