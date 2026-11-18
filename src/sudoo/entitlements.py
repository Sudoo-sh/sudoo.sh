"""What this panel's plan allows: the entitlements the hub signs into each lease (hub.sudoo.sh), how long they are
trusted when the hub cannot be reached, and the plan's limits the panel enforces (Sudoo.sh 3.0, step D).

Where they come from
    Each heartbeat (hub/marketplace.py) answers with a lease signed by the hub's key. Its claim "entitlements" says
    what the account the server is linked to may run: the plan, max_production_branches (0 while
    `production_withheld`), max_servers, max_projects, teams, support level, feature flags (custom_domains,
    offsite_backups, per_project_access), add-ons, a ban, and `valid_until`. The panel keeps the last verified ones
    (state "entitlements"), with when they arrived and until when they are valid.

    linked, heard     the hub's entitlements apply (source "hub")
    hub unreachable   the last ones keep applying until their `valid_until` (the hub's ENTITLEMENTS_VALID_HOURS,
                      24 hours by default, never past the lease): source "grace"
    after that        "expired": the last ones still decide the features, but no new production branch is made until
                      the panel hears from the hub again. Nothing that runs is ever stopped
    not linked        the marketplace is off, or the panel never heard from the hub: the Free plan of the licence
    (or never heard)  (FREE below: one production branch on one server, the paid features off), source "unlinked"
                      or "default", with a banner that says how to link the panel

Production branches
    The panel counts its production branches: every production branch of every project, and every production
    instance outside a project. One more is refused beyond max_production_branches, while production is withheld
    (company verification on the account site), while the account is banned, and once the entitlements expired.
    The production that exists keeps serving in every case: only making a new one waits. The project's own limit
    (projects.production_limit, PROJECT_PRODUCTION_BRANCHES) and the server's (MAX_PRODUCTION_INSTANCES) apply too.

Features
    teams (Teams), per_project_access (a project's grants, restricted projects and branches), custom_domains (adding
    a custom domain) and offsite_backups (turning off-site copies on, testing the storage): refused with a sentence
    that names the plans that have them, the pricing page and the account site. What was set up before keeps working:
    existing domains keep serving and renewing, off-site copies already on keep going, existing restrictions and
    locks stay enforced (removing them is always possible).

Usage
    The heartbeat reports what the panel counts (usage_report): servers (this one), projects, production branches.
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sudoo.config import settings

FREE: dict[str, Any] = {
    "plan": "free",
    "max_servers": 1,
    "max_projects": None,
    "max_production_branches": 1,
    "production_withheld": None,
    "teams": False,
    "priority_support": False,
    "support_level": "community",
    "features": {"per_project_access": False, "custom_domains": False, "offsite_backups": False},
    "addons": {},
    "banned": False,
}
"""The Free plan of the Sudoo.sh Licence Agreement: what an unlinked panel may run."""

FEATURES = ("teams", "per_project_access", "custom_domains", "offsite_backups")
FEATURE_NAMES = {
    "teams": "Teams",
    "per_project_access": "Access per project and per branch",
    "custom_domains": "Custom domains",
    "offsite_backups": "Off-site backups",
}
SOURCES = ("hub", "grace", "expired", "unlinked", "default")


@dataclass(frozen=True)
class Current:
    """The entitlements that apply now, where they come from and until when."""

    values: dict[str, Any]
    source: str
    linked: bool
    received_at: float | None = None
    valid_until: float | None = None
    account_name: str | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def plan(self) -> str:
        return str(self.values.get("plan") or "free")

    def limit(self, name: str) -> int | None:
        """max_<name>; None: no limit."""
        value = self.values.get(f"max_{name}")
        return None if value is None else max(0, int(value))

    def has(self, feature: str) -> bool:
        if feature == "teams":
            return bool(self.values.get("teams"))
        return bool((self.values.get("features") or {}).get(feature))

    @property
    def withheld(self) -> str | None:
        found = self.values.get("production_withheld")
        return str(found) if found else None

    @property
    def banned(self) -> bool:
        return bool(self.values.get("banned"))


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("not a number")
    return int(value)


def normalize(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The lease's entitlements as the panel keeps them (unknown claims are dropped; a wrong type raises ValueError)."""
    features = raw.get("features") or {}
    addons = raw.get("addons") or {}
    if not isinstance(features, dict) or not isinstance(addons, dict):
        raise ValueError("features and addons are objects")
    keep: dict[str, Any] = {
        "plan": str(raw.get("plan") or "free")[:40],
        "max_servers": _int_or_none(raw.get("max_servers")),
        "max_projects": _int_or_none(raw.get("max_projects")),
        "max_production_branches": _int_or_none(raw.get("max_production_branches")) or 0,
        "production_withheld": str(raw["production_withheld"])[:60] if raw.get("production_withheld") else None,
        "teams": raw.get("teams") is True,
        "priority_support": raw.get("priority_support") is True,
        "support_level": str(raw.get("support_level") or ("priority" if raw.get("priority_support") else "community"))[
            :20
        ],
        "features": {str(k)[:60]: v for k, v in features.items() if isinstance(v, bool | int | str)},
        "addons": {str(k)[:60]: int(v) for k, v in addons.items() if isinstance(v, int) and not isinstance(v, bool)},
        "banned": raw.get("banned") is True,
        "over_limit": [str(x)[:40] for x in raw.get("over_limit") or [] if isinstance(x, str)][:10],
    }
    kept = raw.get("production_keep")
    keep["production_keep"] = [str(k)[:200] for k in kept][:1000] if isinstance(kept, list) else None
    for key in ("period", "renews_at", "trial_plan", "api_requests_per_day"):  # newer hubs say more; kept as given
        if key in raw and isinstance(raw[key], str | int | float) and not isinstance(raw[key], bool):
            keep[key] = raw[key]
    privileges = raw.get("privileges")
    if isinstance(privileges, list):
        keep["privileges"] = [p for p in privileges if isinstance(p, dict)][:50]
    return keep


def remember(claims: Mapping[str, Any], lease_expires_at: float, now: float) -> dict[str, Any] | None:
    """The values marketplace.py stores from a verified lease ("entitlements" and their dates), or None when the
    lease has none (an older hub) or they cannot be read."""
    raw = claims.get("entitlements")
    if not isinstance(raw, dict):
        return None
    try:
        values = normalize(raw)
    except (ValueError, TypeError):
        return None
    valid_until = raw.get("valid_until")
    until = float(valid_until) if isinstance(valid_until, int | float) and not isinstance(valid_until, bool) else None
    return {
        "entitlements": values,
        "entitlements_received_at": now,
        "entitlements_valid_until": min(until, lease_expires_at) if until else lease_expires_at,
    }


def current() -> Current:
    """The entitlements that apply now (see the module's description)."""
    from sudoo.hub import marketplace

    if not settings.marketplace:
        return Current(dict(FREE), "unlinked", linked=False)
    state = marketplace.state()
    linked = state.get("linked") or None
    account = (linked or {}).get("account_name") if isinstance(linked, dict) else None
    stored = state.get("entitlements")
    flags = [str(f) for f in state.get("flags") or []]
    if not isinstance(stored, dict):
        return Current(dict(FREE), "default" if linked else "unlinked", linked=bool(linked), account_name=account)
    values = {**FREE, **stored}
    received = state.get("entitlements_received_at")
    until = state.get("entitlements_valid_until")
    now = marketplace.trusted_now()
    if until and now > float(until):
        source = "expired"
    elif state.get("error_code") in ("unreachable", "unverified"):
        source = "grace"
    else:
        source = "hub"
    return Current(
        values,
        source,
        linked=bool(linked),
        received_at=float(received) if received else None,
        valid_until=float(until) if until else None,
        account_name=account,
        flags=flags,
    )


# ---------------------------------------------------------------- production branches


def production_keys() -> list[str]:
    """What the plan counts as production branches, named as the heartbeat reports them (the hub's
    usage.production_branch_names, and its lease's production_keep): "<project>/<branch>" for every production branch
    of a project, and "<instance>" for every production instance outside a project (being created ones included)."""
    from sudoo import projects
    from sudoo.instances import stages

    keys = {f"{p['id']}/{branch}" for p in projects.all_projects() for branch in projects.production_branches(p)}
    for name in stages.production_instances():
        if not projects.project_of(name) and not _creating_in_project(name):
            keys.add(name)
    return sorted(keys)


def _creating_in_project(name: str) -> bool:
    from sudoo.core import jobs
    from sudoo.instances import store

    if store.exists(name):
        return False
    job = jobs.creating(name)
    return bool(job and job.details.get("project"))


def upsell(what: str) -> str:
    """The sentence that says where to get more."""
    return f"{what} See the plans at {settings.plan_pricing_url}, or manage yours at {settings.plan_account_url}."


WITHHELD = {
    "declaration_required": "the account has not yet said how it uses Sudoo.sh (its declaration on the account site)",
    "verification_required": "the company of the account is not verified yet",
    "licence_acceptance_required": "the account has not accepted the current Sudoo.sh Licence Agreement",
}


def production_refusal(key: str) -> str | None:
    """Why `key` ("<project>/<branch>", or "<instance>" outside a project) may not become one more production
    branch under the plan, or None. A branch already in production is never refused."""
    found = current()
    existing = production_keys()
    if key in existing:
        return None
    if found.banned:
        return "The Sudoo.sh account of this panel is suspended: no new production branch can be made. What runs keeps serving."
    if found.withheld:
        reason = WITHHELD.get(found.withheld, found.withheld.replace("_", " "))
        return (
            f"New production branches are withheld because {reason}. Finish it on {settings.plan_account_url}; the "
            "production branches you have keep serving."
        )
    if found.source == "expired":
        return (
            "The panel could not confirm its plan with Sudoo.sh for too long (the hub cannot be reached), so new "
            "production branches wait until it can. Everything that runs keeps serving."
        )
    limit = found.limit("production_branches")
    if limit is None or len(existing) < limit:
        return None
    plan = found.plan.capitalize()
    count = f"{limit} production {'branch' if limit == 1 else 'branches'}"
    linked = "" if found.linked else " This panel is not linked to a Sudoo.sh account, so it has the Free plan."
    return upsell(
        f"The {plan} plan allows {count}, and this panel has {len(existing)} ({', '.join(existing[:5])}).{linked} "
        "Move one out of production, or get more production branches."
    )


# ---------------------------------------------------------------- features


def feature_refusal(feature: str) -> str | None:
    """Why `feature` (FEATURES) is not available on the plan, or None."""
    found = current()
    if found.has(feature):
        return None
    linked = "" if found.linked else " This panel is not linked to a Sudoo.sh account, so it has the Free plan."
    return upsell(f"{FEATURE_NAMES[feature]} {'is' if feature == 'per_project_access' else 'are'} available on Pro and "
                  f"Business.{linked}")  # fmt: skip


# ---------------------------------------------------------------- usage


def usage() -> dict[str, Any]:
    """What the panel counts: servers, projects, production branches, instances by stage."""
    from sudoo import projects
    from sudoo.instances import stages, store

    by_stage = dict.fromkeys(stages.STAGES, 0)
    for name in store.names():
        by_stage[stages.of(store.read_env(name))] += 1
    keys = production_keys()
    return {
        "servers": 1,
        "projects": len(projects.all_projects()),
        "production_branches": len(keys),
        "production_branch_names": keys,
        "instances_by_stage": by_stage,
    }


REPORTED_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")  # the hub's BranchName


def usage_report() -> dict[str, Any]:
    """The heartbeat's "usage", as the hub's UsageIn names it: servers, projects, production_branches,
    production_branch_names (at most 1,000; a name the hub would not take is left out, its count stays) and
    instances_by_stage ({production, staging, development}: counts, as Settings › Plan shows them)."""
    found = usage()
    report: dict[str, Any] = {
        key: min(int(found[key]), 100_000) for key in ("servers", "projects", "production_branches")
    }
    report["production_branch_names"] = [n for n in found["production_branch_names"] if REPORTED_NAME.match(n)][:1000]
    report["instances_by_stage"] = {stage: min(int(n), 100_000) for stage, n in found["instances_by_stage"].items()}
    return report


# ---------------------------------------------------------------- after a downgrade: the production branches kept


def production_keep() -> list[str] | None:
    """The lease's production_keep: the production branches of this server the account keeps after a downgrade
    left it with more than its plan allows (None: no choice to apply)."""
    keep = current().values.get("production_keep")
    return [str(k) for k in keep] if isinstance(keep, list) else None


def leaving_production() -> list[str]:
    """The production branches production_keep does not keep: they become development branches."""
    keep = production_keep()
    if keep is None:
        return []
    return [key for key in production_keys() if key not in keep]


def apply_production_keep() -> list[str]:
    """Turn the production branches the account does not keep (production_keep) into development branches: each
    instance's stage changes (it keeps its data, its lifetime "keep" and its automation; nothing is deleted), the
    branch's recorded stage too, with an audit entry and an alert. A busy instance waits for the next heartbeat.
    Returns the instances changed."""
    from sudoo import alerts, projects
    from sudoo.core import audit, events, jobs
    from sudoo.instances import stages, store

    changed: list[str] = []
    for key in leaving_production():
        project_id, _, branch = key.partition("/")
        if branch:
            names = projects.by_branch(project_id).get(branch, [])
        else:
            names, project_id = [key], ""
        names = [n for n in names if store.exists(n) and stages.is_production(store.read_env(n))]
        if any(jobs.busy(n) for n in names):
            continue
        for name in names:
            store.update_env(name, STAGE=stages.DEVELOPMENT)
            _mail_catcher_changed(name)
            events.publish("instance", name)
            changed.append(name)
        if branch and projects.get(project_id):
            projects.record_branch(project_id, branch, stage=stages.DEVELOPMENT, changed_by="system",
                                   changed_at=int(time.time()))  # fmt: skip
            events.publish("project", project_id)
        audit.record("system", None, "production branch left production (plan)", names[0] if names else None,
                     detail=f"{key}: not kept after the plan's downgrade ({', '.join(names) or 'no instance'})")  # fmt: skip
        with contextlib.suppress(Exception):  # an alert that cannot be sent never stops the change
            alerts.send(
                alerts.Alert(
                    type="plan",
                    title=f"{key} is no longer a production branch",
                    text=f"The Sudoo.sh plan of this panel allows fewer production branches since its downgrade, and "
                    f"{key} is not one of those kept, so it is now a development branch. Its instances keep their "
                    "data and keep running; nothing was deleted.",
                    instance=names[0] if names else None,
                    hint=f"Choose the branches to keep, or get more production branches, on {settings.plan_account_url}.",
                    key=f"plan-production:{key}",
                    severity="info",
                )
            )
    return changed


def _mail_catcher_changed(name: str) -> None:
    try:
        from sudoo.mailcatcher import panel as mail_catcher

        mail_catcher.stage_changed(name)
    except Exception:  # noqa: BLE001 - the mail catcher catches up when the instance starts
        pass


def status() -> dict[str, Any]:
    """Settings › Plan: the plan, its entitlements, what the panel uses, and where to manage it."""
    found = current()
    counted = usage()
    return {
        "plan": found.plan,
        "source": found.source,
        "linked": found.linked,
        "account_name": found.account_name,
        "received_at": found.received_at,
        "valid_until": found.valid_until,
        "entitlements": {
            "max_production_branches": found.limit("production_branches"),
            "max_servers": found.limit("servers"),
            "max_projects": found.limit("projects"),
            "teams": found.has("teams"),
            "features": {feature: found.has(feature) for feature in FEATURES if feature != "teams"},
            "support_level": found.values.get("support_level") or "community",
            "addons": found.values.get("addons") or {},
            **{k: found.values[k] for k in ("period", "renews_at", "trial_plan") if k in found.values},
        },
        "production_withheld": found.withheld,
        "banned": found.banned,
        "over_limit": found.values.get("over_limit") or [],
        "usage": counted,
        "production_refusal": production_refusal("\0new"),
        "production_keep": production_keep(),
        "leaving_production": leaving_production(),
        "links": {
            "pricing": settings.plan_pricing_url,
            "account": settings.plan_account_url,
            "keep": f"{settings.plan_account_url.rstrip('/')}/plan",
        },
        "checked_at": time.time(),
    }
