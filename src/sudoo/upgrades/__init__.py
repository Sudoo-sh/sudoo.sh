"""Database upgrades: an instance's database moved to a newer Odoo version, the way Odoo.sh does it.

    1. Pre-check (precheck.py): the target version, the instance's installed modules and, for the modules of its
       repository, whether the upgrade branch (named after the target, such as "19.0") has them ported (their
       manifest version starts with the target's series). Shown before anything starts.
    2. Test (run.test / run.rerun): a new backup of the instance, upgraded with the method (methods.py), loaded into
       a NEW staging instance on the target version (the upgrade branch's code when the repository has one, else
       plain Odoo with standard modules only), neutralised like any staging copy. "Re-run" repeats it from a fresh
       backup; with the project's upgrade_test_on_push option, a push to the upgrade branch does too (OpenUpgrade;
       with Odoo's service the push only updates the code: the subscription code is never kept).
    3. Promotion (run.promote): production goes into maintenance (the gateway shows "Upgrading"), a final backup
       (kind before-upgrade), the same method on the latest data, the instance switched to the target version and
       branch, Odoo started. When Odoo does not come up, everything is put back from that backup at once; during
       UPGRADE_ROLLBACK_MINUTES the upgrade can be cancelled (run.cancel) and is put back by itself when Odoo stops
       answering (watch_forever).

Methods: "enterprise" (an Enterprise database: Odoo S.A.'s upgrade service, through Odoo's own upgrade script
downloaded when the upgrade runs, with the customer's own subscription code, under Odoo's terms) and "openupgrade"
(a Community database: OCA OpenUpgrade, AGPL-3.0, whose unmodified sources a temporary container downloads at the
commit pinned in OPENUPGRADE_REFS). Neither is bundled with or imported by the panel; both run as separate programs
in temporary containers. UPGRADE_METHODS says which are allowed.

The state of an instance's upgrade is a document in the storage (collection "upgrades", key: the instance upgraded),
see resource(). The subscription code is never stored: it lives in the job's memory only, and the job's log masks it.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

from sudoo.config import settings
from sudoo.instances import odoo, stages, store
from sudoo.storage import Document, storage

COLLECTION = "upgrades"
METHODS = ("enterprise", "openupgrade")
TERMS_URL = "https://www.odoo.com/documentation/18.0/legal/terms/enterprise.html#upgrade-services"
"""Odoo's Enterprise Subscription Agreement, Upgrade Services: shown (and accepted) before Odoo's service is used."""
STATES = ("checked", "testing", "tested", "test_failed", "promoting", "promoted", "promotion_failed", "rolled_back")
HISTORY_KEPT = 50
LOCK = threading.RLock()


class Secret:
    """A value that must never be written anywhere (the subscription code): its repr hides it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(********)"

    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._value)


# ---------------------------------------------------------------- versions and methods


def major(version: str) -> int:
    match = re.match(r"^(\d+)\.", version or "")
    return int(match[1]) if match else 0


def method_for(env: dict[str, str]) -> str:
    """The method that upgrades the instance: Odoo's service for Enterprise, OpenUpgrade for Community."""
    return "enterprise" if env.get("EDITION") == "enterprise" else "openupgrade"


def allowed_methods() -> list[str]:
    return [part for part in settings.upgrade_methods.split(",") if part in METHODS]


def openupgrade_refs() -> dict[str, str]:
    """{target version: OpenUpgrade commit} from OPENUPGRADE_REFS."""
    return dict(pair.split("=", 1) for pair in settings.openupgrade_refs.split(",") if "=" in pair)


def skips_versions(method: str) -> bool:
    """Whether the method can go over several versions at once: Odoo's service can, OpenUpgrade goes one by one."""
    return method == "enterprise"


def targets(env: dict[str, str]) -> list[str]:
    """The versions the instance may be upgraded to, the next one first: the next major version, and for a method
    that can skip versions every later one this panel offers. Only x.0 versions (never saas-)."""
    current = major(env.get("ODOO_VERSION", ""))
    offered = sorted({v for v in odoo.versions() if re.fullmatch(r"\d+\.0", v) and major(v) > current}, key=major)
    if not skips_versions(method_for(env)):
        offered = [v for v in offered if major(v) == current + 1]
    return offered


def method_problem(env: dict[str, str], target: str) -> str | None:
    """Why the instance's method cannot take it to `target`, or None."""
    from sudoo import addons

    method = method_for(env)
    if method not in allowed_methods():
        name = "Odoo's upgrade service (Enterprise)" if method == "enterprise" else "OpenUpgrade (Community)"
        return f"{name} is not allowed on this server (Settings, Odoo, Database upgrades)."
    if target not in targets(env):
        return f"Odoo {target} is not a version this instance can be upgraded to."
    if method == "enterprise" and not addons.available(target):
        return f"The Odoo {target} Enterprise add-ons are not available on this server."
    if method == "openupgrade" and target not in openupgrade_refs():
        return f"No OpenUpgrade commit is set for Odoo {target} (Settings, Odoo, OpenUpgrade commits)."
    return None


def upgrade_branch(target: str) -> str:
    """The branch of the repository that holds the code ported to `target`: named after it, like Odoo.sh."""
    return target


# ---------------------------------------------------------------- the stored state


def get(source: str) -> Document | None:
    return storage().get(COLLECTION, source) if source else None


def all_upgrades() -> list[Document]:
    return list(storage().all(COLLECTION).values())


def change(source: str, fn: Callable[[Document], Document | None]) -> Document | None:
    """Atomically change the upgrade of `source` (created empty when there is none); fn returns the new document."""
    with LOCK:

        def apply(found: Document | None) -> Document | None:
            return fn(dict(found or {"source": source, "history": []}))

        return storage().change(COLLECTION, source, apply)


def update(source: str, **values: Any) -> Document | None:
    return change(source, lambda doc: {**doc, **values})


def remember(source: str, user: str, event: str, detail: str = "", **values: Any) -> None:
    """Record an event in the upgrade's history (and change `values`); the audit log gets it too."""
    from sudoo.core import audit

    def apply(doc: Document) -> Document:
        history = [*(doc.get("history") or []), {"at": round(time.time()), "user": user, "event": event,
                                                  "detail": detail}][-HISTORY_KEPT:]  # fmt: skip
        return {**doc, **values, "history": history}

    change(source, apply)
    audit.record(user, None, f"database upgrade: {event}", source, detail=detail or None)


def forget(source: str) -> None:
    storage().delete(COLLECTION, source)


def staging_of(source: str) -> str | None:
    """The staging instance the upgrade of `source` is tested on, when it still exists."""
    found = get(source)
    name = (found or {}).get("staging")
    return str(name) if name and store.exists(str(name)) else None


def source_of(staging: str) -> str | None:
    """The instance whose upgrade `staging` tests, or None."""
    return next((str(u["source"]) for u in all_upgrades() if u.get("staging") == staging), None)


def rollback_open(doc: Document | None, now: float | None = None) -> bool:
    """Whether a promoted upgrade can still be cancelled (UPGRADE_ROLLBACK_MINUTES after it)."""
    if not doc or doc.get("state") != "promoted":
        return False
    until = (doc.get("promotion") or {}).get("rollback_until")
    return until is not None and (time.time() if now is None else now) < float(until)


def refusal(name: str) -> str | None:
    """Why instance `name` cannot be upgraded at all now, or None (checked before every step)."""
    env = store.read_env(name)
    if not targets(env):
        return f"There is no newer Odoo version this server offers for {name} (Odoo {env.get('ODOO_VERSION')})."
    if stages.of(env) == stages.STAGING and source_of(name):
        return f"{name} is the test instance of another upgrade."
    return None


def resource(name: str) -> dict[str, Any]:
    """The upgrade of instance `name` as the API shows it: what can be done and how it went."""
    env = store.read_env(name)
    doc = get(name) or {}
    promotion = dict(doc.get("promotion") or {})
    promotion.pop("previous", None)  # the instance's earlier image and folders: of no use to people
    offered = targets(env)
    method = method_for(env)
    return {
        "instance": name,
        "from_version": env.get("ODOO_VERSION"),
        "edition": env.get("EDITION", "community"),
        "method": method,
        "allowed": method in allowed_methods(),
        "targets": offered,
        "next": offered[0] if offered else None,
        "skips_versions": skips_versions(method),
        "terms_url": TERMS_URL if method == "enterprise" else None,
        "openupgrade_source": settings.openupgrade_repository if method == "openupgrade" else None,
        "refusal": refusal(name),
        "state": doc.get("state"),
        "target": doc.get("target"),
        "branch": doc.get("branch"),
        "report": doc.get("report"),
        "checked_at": doc.get("checked_at"),
        "staging": staging_of(name),
        "tested_at": doc.get("tested_at"),
        "test_job": doc.get("test_job"),
        "promotion": promotion or None,
        "rollback_open": rollback_open(doc),
        "rollback_minutes": settings.upgrade_rollback_minutes,
        "history": list(reversed(doc.get("history") or []))[:20],
    }
