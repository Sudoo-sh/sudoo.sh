"""Access e-mails: tell people when they get access to a project, when their role there changes, and when it ends;
remind each project's admins who has access (the access review); and tell admins and requesters about temporary
access.

Changes are found by comparing everyone's role in the projects concerned before and after something that may change
it (watch): a change of a project's access, a team, an account's role, GitHub permissions or GitHub teams read again.
The last role each person was told about is kept (collection "access_notices"), so nobody gets the same e-mail twice
even when GitHub is read every few minutes. Super admins and administrators are not told: they have every right in
every project anyway. Every e-mail sent is in the audit log. Nothing is sent while ACCESS_EMAILS is off, e-mail is
not set up, or the person has no address; a failing mail server never stops the change itself.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from sudoo import accounts, roles
from sudoo.config import settings
from sudoo.core import audit
from sudoo.storage import storage

from . import project_roles

log = logging.getLogger(__name__)

COLLECTION = "access_notices"
REVIEWS = "access_reviews"


def _user(name: str, account: Mapping[str, Any]) -> dict[str, str]:
    return {"name": name, "role": roles.normalize(str(account.get("role")))}


def snapshot(project: Mapping[str, Any]) -> dict[str, str]:
    """{account: its role in the project} for everyone who is not an administrator ("none" when hidden)."""
    from . import access

    found = {}
    for name, account in accounts.all_users().items():
        user = _user(name, account)
        if access.full_access(user):
            continue
        found[name] = access.level(user, project) if access.sees_project(user, project) else "none"
    return found


@contextlib.contextmanager
def watch(project_ids: Iterable[str], by: str) -> Iterator[None]:
    """Compare everyone's role in these projects before and after the block, and e-mail those whose role changed."""
    from sudoo import projects

    ids = [pid for pid in dict.fromkeys(project_ids) if pid]
    before = {}
    for pid in ids:
        project = projects.get(pid)
        if project is not None:
            with contextlib.suppress(Exception):
                before[pid] = snapshot(project)
    yield
    for pid, old in before.items():
        project = projects.get(pid)
        if project is None:
            continue
        try:
            new = snapshot(project)
        except Exception:  # noqa: BLE001 - the change is made; only the e-mails are skipped
            log.exception("access notices: could not compare %s", pid)
            continue
        for name in set(old) | set(new):
            if old.get(name, "none") != new.get(name, "none"):
                notify(project, name, old.get(name, "none"), new.get(name, "none"), by)


def _rights_block(project: Mapping[str, Any], level: str) -> Any:
    from sudoo.mail.catalog import details_block

    rows = []
    for stage in project_roles.STAGES:
        granted = project_roles.rights(project, level, stage) - {"projects.manage", "projects.access",
                                                                  "projects.resources"}  # fmt: skip
        words = [project_roles.RIGHT_LABELS[r] for r in project_roles.stage_list(granted, stage)]
        rows.append((stage.capitalize(), "; ".join(words) if words else "nothing"))
    return details_block(rows, "What you can do")


def _how(account: Mapping[str, Any]) -> str:
    from sudoo.auth import methods

    ways = [{"github": "Continue with GitHub", "email_code": "a code sent to your e-mail address",
             "password": "your e-mail address and password"}[m] for m in methods.of(account)]  # fmt: skip
    return " or ".join(ways) or "your e-mail address and password"


def _link(path: str) -> str:
    from sudoo.mail.render import panel_url

    return (panel_url() or "").rstrip("/") + path


def _send(address: str, key: str, account: Mapping[str, Any], **values: Any) -> bool:
    from sudoo.mail import mailer, send

    arabic = accounts.language(dict(account)) == "ar"
    try:
        if not mailer().ready:
            return False
        send(address, f"{key}_ar" if arabic else key, **values)
    except Exception as error:  # noqa: BLE001 - reported, never blocks
        log.warning("access e-mail %s to %s not sent: %r", key, address, error)
        return False
    return True


def notify(project: Mapping[str, Any], name: str, before: str, after: str, by: str) -> bool:
    """E-mail `name` that their role in the project went from `before` to `after` (once per change)."""
    key = f"{project.get('id')}:{name}"
    told = storage().get(COLLECTION, key) or {}
    if told.get("level", "none") == after:
        return False
    storage().put(COLLECTION, key, {"level": after, "at": time.time()})
    account = accounts.get(name) or {}
    address = str(account.get("email") or "")
    if not settings.access_emails or not address:
        return False
    template = "access_removed" if after == "none" else "access_granted" if before == "none" else "access_changed"
    values = {
        "name": account.get("first_name") or name,
        "project": project.get("name") or project.get("id"),
        "role": project_roles.LABELS.get(after, after),
        "before": project_roles.LABELS.get(before, before),
        "by": by,
        "rights": _rights_block(project, after),
        "how": _how(account),
        "link": _link(f"/projects/{project.get('id')}"),
    }

    def run() -> None:
        if _send(address, template, account, **values):
            audit.record(
                name, "", "access e-mail sent", detail=f"{project.get('id')}: {template} ({before} -> {after})"
            )

    threading.Thread(target=run, daemon=True, name="access-notice").start()
    return True


# ---------------------------------------------------------------- admins of a project


def admins(project: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The accounts that manage the project's access (projects.access in it), with an e-mail address."""
    from . import access

    found = []
    for name, account in accounts.all_users().items():
        user = _user(name, account)
        if account.get("email") and access.allowed(user, "projects.access", project):
            found.append((name, account))
    return found


def request_asked(project: Mapping[str, Any], request: Mapping[str, Any]) -> int:
    who_account = accounts.get(str(request["user"])) or {}
    values = {
        "who": accounts.display_name(str(request["user"]), who_account),
        "project": project.get("name") or project.get("id"),
        "role": project_roles.LABELS.get(str(request["level"]), str(request["level"])),
        "stage": request["stage"],
        "hours": request["hours"],
        "reason": request["reason"],
        "decision": "",
        "until": "",
        "decided_by": "",
        "link": _link(f"/projects/{project.get('id')}/access"),
    }
    sent = 0
    for name, account in admins(project):
        if name != request["user"] and _send(str(account["email"]), "access_request", account,
                                             name=account.get("first_name") or name, **values):  # fmt: skip
            sent += 1
    return sent


def request_decided(project: Mapping[str, Any], request: Mapping[str, Any]) -> bool:
    from datetime import datetime

    account = accounts.get(str(request["user"])) or {}
    if not account.get("email"):
        return False
    decided_by = accounts.display_name(
        str(request.get("decided_by")), accounts.get(str(request.get("decided_by"))) or {}
    )
    until = (
        datetime.fromtimestamp(float(request["expires"]), settings.zone).strftime("%A %d %B %Y, %H:%M")
        if request.get("expires")
        else ""
    )
    return _send(
        str(account["email"]),
        "access_request_decided",
        account,
        name=account.get("first_name") or request["user"],
        who=accounts.display_name(str(request["user"]), account),
        project=project.get("name") or project.get("id"),
        role=project_roles.LABELS.get(str(request["level"]), str(request["level"])),
        stage=request["stage"],
        hours=request["hours"],
        reason=request["reason"],
        decision=str(request["status"]),
        until=until,
        decided_by=decided_by,
        link=_link(f"/projects/{project.get('id')}"),
    )


# ---------------------------------------------------------------- the access review


def review_due(project_id: str, now: float) -> bool:
    """Whether the project's review is due; the first time, it only starts counting (no e-mail at upgrade)."""
    last = storage().get(REVIEWS, project_id)
    if last is None:
        storage().put(REVIEWS, project_id, {"at": now})
        return False
    return now - float(last.get("at") or 0) >= settings.access_review_days * 86400


def send_review(project: Mapping[str, Any]) -> int:
    from sudoo.mail.catalog import lines_block

    from . import people

    listed = [p for p in people.of_project(project) if p["level"] != "none"]
    lines = [f"{p['name']}: {project_roles.LABELS.get(p['level'], p['level'])} ({p['source_label']})" for p in listed]
    sent = 0
    for name, account in admins(project):
        if _send(str(account["email"]), "access_review", account, name=account.get("first_name") or name,
                 project=project.get("name") or project.get("id"), people=lines_block(lines), count=len(listed),
                 link=_link(f"/projects/{project.get('id')}/access")):  # fmt: skip
            sent += 1
    storage().put(REVIEWS, str(project.get("id")), {"at": time.time(), "sent": sent})
    if sent:
        audit.record(
            "panel", "", "access review sent", detail=f"{project.get('id')}: {sent} admins, {len(listed)} people"
        )
    return sent


def review_forever() -> None:
    """Every hour: the access reviews that are due (background.py)."""
    from sudoo import projects

    while True:
        time.sleep(3600)
        if not settings.access_review:
            continue
        now = time.time()
        for project in projects.all_projects():
            with contextlib.suppress(Exception):
                if review_due(str(project["id"]), now):
                    send_review(project)
