"""The names a new instance and its database get."""

from __future__ import annotations

import re
import secrets

from sudoo.instances import store
from sudoo.repos import github


def slug(text: str) -> str:
    """Lower-case letters, digits and single hyphens: "Feature/New UI" -> "feature-new-ui"."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


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
    base = base[:28].strip("-") or "odoo"
    name, counter = base, 2
    while store.folder(name).exists() or name in _being_created():
        name, counter = f"{base[:26].strip('-')}-{counter}", counter + 1
    return name, name.replace("-", "_")


def _being_created() -> set[str]:
    from sudoo.core import jobs  # late: core/jobs.py is imported by most of the panel

    return set(jobs.being_created())
