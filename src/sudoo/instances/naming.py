"""The names a new instance and its database get."""

from __future__ import annotations

import re
import secrets

from sudoo.instances import store
from sudoo.repos import github


def slug(text: str) -> str:
    """Lower-case letters, digits and single hyphens: "Feature/New UI" -> "feature-new-ui"."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


NAME_MAX = 28
"""The longest instance name the panel makes (its containers, database and links are named after it)."""


def shorten(base: str, limit: int = NAME_MAX) -> str:
    """`base` cut to `limit` characters without leaving a piece of a cut word at its end: "odootec-demos-v19-car-rent-demo"
    becomes "odootec-demos-v19-car-rent", not "...-car-rent-d". A name with no hyphen in its second half is cut where
    the limit falls."""
    if len(base) <= limit:
        return base.strip("-")
    cut = base[:limit]
    if base[limit] != "-":  # the limit falls inside a word: drop what is left of it
        at = cut.rfind("-")
        if at >= limit // 2:
            cut = cut[:at]
    return cut.strip("-")


def plan_name(repo: str, branch: str, custom_name: str, version: str = "", edition: str = "") -> tuple[str, str]:
    """Instance name (from the custom name, repository + branch, or for plain Odoo "odoo-<version>-<edition>-<4
    random characters>"), made unique, and its database name."""
    if custom_name:
        base = slug(custom_name)
    elif not repo and version:  # plain Odoo
        base = "-".join(part for part in ("odoo", slug(version), slug(edition), secrets.token_hex(2)) if part)
    else:
        repository = (github.repo_name(repo) or "").split("/")[-1]
        base = "-".join(part for part in (slug(repository), slug(branch)) if part)
    base = shorten(base) or "odoo"
    name, counter = base, 2
    while store.folder(name).exists() or name in _being_created() or _elsewhere(name):
        suffix = f"-{counter}"
        name, counter = f"{shorten(base, NAME_MAX - len(suffix))}{suffix}", counter + 1
    return name, name.replace("-", "_")


def _elsewhere(name: str) -> bool:
    """An instance of that name on another server (premium/servers.py)."""
    from sudoo.premium import servers

    return servers.remote(name) is not None


def _being_created() -> set[str]:
    from sudoo.core import jobs  # late: core/jobs.py is imported by most of the panel

    return set(jobs.being_created())
