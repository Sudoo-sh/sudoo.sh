"""Premium features: first-party packages that install automatically from Sudoo.sh when the plan includes them.

The panel is complete without them. A premium package adds one feature set (teams and access per project, custom
domains, off-site backups, support access, several servers); it is downloaded from the hub, verified, bound to this
server and loaded by loader.py. This module is what the rest of the panel uses:

    PACKAGES         the premium packages this panel knows: their names, the plan features that include them and
                     the plans that have them (for the plan card shown while a package is absent)
    included(id)     whether the plan (entitlements.py) includes the package
    loaded(id)       whether the package runs in this process
    hooks(group)     the objects the loaded packages provide for an extension point (entry-point group)
    hook(group, n)   one of them, or None
    refusal(id)      the sentence shown when the package is absent: which plans have it, and that it installs on its
                     own once the plan includes it

Extension points (entry-point groups a premium package may declare in its manifest)
    sudoo.premium.blueprints  Flask blueprints added under /api/v1
    sudoo.premium.jobs        callables started once with the background work (no arguments)
    sudoo.premium.access      access hooks (projects/access.py): "grants" (adding grants and restricted branches)
    sudoo.premium.domains     custom domains: "manager" (adding domains: DNS checks and first certificates)
    sudoo.premium.support     support access: "support" (grants of support sessions, its channel)
    sudoo.premium.servers     several servers: "servers" (the panel's side: instances of every server, placement,
                              forwarding, moves; premium/servers.py) and "agent" (a server's agent: join, status,
                              leave, run; premium/agent.py)

What stays in the core, whatever the plan
    The data of every feature (storage collections) and the enforcement of what was set up: restrictions and locks
    of projects and branches stay enforced (removing them is always possible), domains already served keep being
    served and their certificates renewed (instances/domain_worker.py), teams already named in grants keep their
    meaning. Security never depends on a premium package being present.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

GROUPS = (
    "sudoo.premium.blueprints",
    "sudoo.premium.jobs",
    "sudoo.premium.access",
    "sudoo.premium.domains",
    "sudoo.premium.support",
    "sudoo.premium.servers",
)
BLUEPRINTS, JOBS, ACCESS, DOMAINS, SUPPORT, SERVERS = GROUPS


@dataclass(frozen=True)
class Package:
    id: str
    name: str
    features: tuple[str, ...]  # entitlement features that include it (any of them)
    plans: str  # the plans that have it, for people


PACKAGES: dict[str, Package] = {
    p.id: p
    for p in (
        Package("teams", "Teams and access per project", ("teams", "per_project_access"), "Pro and Business"),
        Package("domains", "Custom domains", ("custom_domains",), "Business"),
        Package("offsite", "Off-site backups", ("offsite_backups",), "Pro and Business"),
        Package("support", "Support access", ("support_access",), "Pro and Business"),
        Package("servers", "Several servers", ("multi_server",), "Business"),
    )
}


def included_ids() -> set[str]:
    """The packages the plan includes: the lease's entitlements "premium" list, else derived from its features. In a
    server's agent: those the main panel sent (the panel decides)."""
    from sudoo import entitlements

    from .servers import is_agent

    if is_agent():
        from . import loader

        return {p for p in loader.records() if p in PACKAGES}

    found = entitlements.current()
    listed = found.values.get("premium")
    if isinstance(listed, list):
        return {str(p) for p in listed if str(p) in PACKAGES}
    ids = set()
    for package in PACKAGES.values():
        for feature in package.features:
            if feature == "multi_server":
                limit = found.limit("servers")
                on = found.linked and found.source != "unlinked" and (limit is None or limit > 1)
            else:
                on = found.has(feature)
            if on:
                ids.add(package.id)
                break
    return ids


def included(package_id: str) -> bool:
    return package_id in included_ids()


def loaded(package_id: str) -> bool:
    from . import loader

    return package_id in (loader.LOADED or set())


def hooks(group: str) -> list[tuple[str, Any]]:
    """(name, object) of every entry point of `group` that a loaded premium package declares."""
    from . import loader

    if group not in GROUPS:
        raise ValueError(f"{group} is not a premium extension point")
    loader.activate()
    found: list[tuple[str, Any]] = []
    for entry in entry_points(group=group):
        if loader.entry_allowed(entry, group):
            found.append((entry.name, entry.load()))
    return sorted(found, key=lambda item: item[0])


def hook(group: str, name: str) -> Any | None:
    """The object a loaded premium package provides as `name` in `group`, or None."""
    return next((obj for found, obj in hooks(group) if found == name), None)


def refusal(package_id: str) -> str:
    """Why a premium feature cannot be used now, in a sentence for people."""
    from sudoo import entitlements
    from sudoo.config import settings

    package = PACKAGES[package_id]
    if included(package_id):
        from . import loader

        rec = loader.record(package_id) or {}
        if rec.get("problem"):
            return (
                f"{package.name} is included in your plan but cannot run on this server: {rec['problem']} "
                "Open Settings, Premium features, to see what to do."
            )
        return (
            f"{package.name} is included in your plan and installs automatically from Sudoo.sh in a few minutes "
            "(Settings, Premium features)."
        )
    linked = "" if entitlements.current().linked else " This panel is not linked to a Sudoo.sh account."
    return (
        f"{package.name} is part of the {package.plans} plans; it installs automatically from Sudoo.sh once your "
        f"plan includes it.{linked} See the plans at {settings.plan_pricing_url}."
    )
