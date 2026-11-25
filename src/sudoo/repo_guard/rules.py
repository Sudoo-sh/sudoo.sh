"""Branch rules on GitHub: what the panel asks for, what GitHub has now, the difference, and applying or removing it.

Rulesets first (GitHub's newer branch rules; on organisation and personal repositories alike), classic branch
protection as the fallback (rulesets not available, or named people allowed to push: rulesets cannot name people).
The panel's rulesets are named with PREFIX: that name is the marker, and the panel changes or removes only rulesets
that carry it. Classic protection has no name, so the panel records the branches it protected itself and refuses to
change a branch that was protected before (it would replace someone else's rules).

    production          a pull request with N approvals (default 1), the checks' "sudoo-checks" passing (when
                        asked), no force-push, no deletion; optionally a linear history and signed commits
    production merges   only when pushes are restricted: an "update" rule that only its bypass list may pass:
                        "roles" (GitHub's Admin and Maintain repository roles, the counterparts of the project's
                        Admin and Maintainer levels) or named teams; named people need classic protection
                        (organisation repositories only)
    staging             the same as production with lighter defaults (no approvals required)

GitHub's plan limits: private repositories on GitHub Free have neither rulesets nor branch protection; GitHub
answers 403 "Upgrade to GitHub Pro or make this repository public". The plan says so instead of failing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from sudoo import projects
from sudoo.instances import stages
from sudoo.repo_guard import files
from sudoo.repo_guard import github as gh
from sudoo.repos.github_app import GitHubError

PREFIX = "Sudoo.sh guard: "
NAMES = {"production": PREFIX + "production", "production_merges": PREFIX + "production merges",
         "staging": PREFIX + "staging"}  # fmt: skip
ROLE_ACTORS = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"},  # Admin
               {"actor_id": 2, "actor_type": "RepositoryRole", "bypass_mode": "always"}]  # Maintain  # fmt: skip
METHODS = ("auto", "rulesets", "branch_protection")
PUSHERS = ("anyone", "roles", "named")
MAX_APPROVALS = 10  # GitHub's limit
log = logging.getLogger(__name__)

DEFAULT_RULES: dict[str, Any] = {
    "enabled": False,
    "method": "auto",
    "production": {"approvals": 1, "require_checks": True, "linear_history": False, "signed_commits": False,
                   "code_owner_review": True},
    "staging": {"enabled": True, "approvals": 0, "require_checks": True, "linear_history": False,
                "signed_commits": False},
    "pushers": {"mode": "roles", "teams": [], "users": []},
}  # fmt: skip

LABELS = {
    "branches": "Branches",
    "pull_request": "Pull request required",
    "approvals": "Approvals required",
    "code_owner_review": "Review of .github/ by its owners",
    "checks": "Checks that must pass",
    "force_push": "Force-pushes",
    "deletion": "Deleting the branch",
    "linear_history": "Linear history",
    "signed_commits": "Signed commits",
    "pushers": "Who may push or merge",
}


# ---------------------------------------------------------------- which branches


def production_branches(project: Mapping[str, Any], default_branch: str) -> list[str]:
    return projects.production_branches(project) or [default_branch]


def staging_branches(project: Mapping[str, Any]) -> list[str]:
    grouped = projects.by_branch(str(project["id"]))
    names = set(project.get("branches") or {}) | set(grouped)
    return sorted(b for b in names if b and projects.branch_stage(project, b, grouped.get(b, [])) == stages.STAGING)


# ---------------------------------------------------------------- the rules wanted, in one shape


def check_label(context: str, app_id: Any) -> str:
    """A required check as shown: with the source GitHub will accept it from."""
    return f"{context} (GitHub Actions)" if app_id == files.ACTIONS_APP_ID else f"{context} (any source)"


def code_owners(project: Mapping[str, Any], rules: Mapping[str, Any], owner_type: str | None) -> list[str]:
    """Who owns .github/ in CODEOWNERS when production asks for "changes to .github/ need review": the teams and
    people named in the pushers (teams only on an organisation's repository), else the owner of a personal
    repository. Empty when nobody can be named (an organisation without a team): GitHub cannot name a role."""
    if not rules["production"].get("code_owner_review"):
        return []
    owner = str(project["repository"]).split("/")[0]
    pushers = rules["pushers"]
    named: list[str] = []
    if pushers["mode"] == "named":
        if owner_type == "Organization":
            named += [f"@{owner}/{t}" for t in sorted(pushers["teams"])]
        named += [f"@{u}" for u in sorted(pushers["users"])]
    if not named and owner_type == "User":
        named = [f"@{owner}"]
    return named


def _wanted(
    kind: str, branches: list[str], rules: Mapping[str, Any], owners: list[str] | None = None
) -> dict[str, Any]:
    """The normalised rules of one target (the shape `describe_*` reads back from GitHub)."""
    if kind == "production_merges":
        return {"branches": sorted(branches), "pushers": _pushers_text(rules["pushers"])}
    level = rules["production" if kind == "production" else "staging"]
    return {
        "branches": sorted(branches),
        "pull_request": True,
        "approvals": int(level["approvals"]),
        "code_owner_review": bool(level.get("code_owner_review")) and bool(owners),
        "checks": [check_label(files.REQUIRED_CHECK, files.ACTIONS_APP_ID)] if level["require_checks"] else [],
        "force_push": "blocked",
        "deletion": "blocked",
        "linear_history": bool(level["linear_history"]),
        "signed_commits": bool(level["signed_commits"]),
    }


def _pushers_text(pushers: Mapping[str, Any]) -> str:
    if pushers["mode"] == "roles":
        return "Admin and Maintain roles"
    if pushers["mode"] == "named":  # rulesets name teams only (people need classic protection)
        return ", ".join(f"team {t}" for t in sorted(pushers["teams"])) or "nobody"
    return "anyone with write access"


def ruleset_body(
    kind: str, branches: list[str], rules: Mapping[str, Any], team_ids: Mapping[str, int],
    owners: list[str] | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The ruleset GitHub is sent for `kind` ("production", "production_merges" or "staging")."""
    conditions = {"ref_name": {"include": [f"refs/heads/{b}" for b in sorted(branches)], "exclude": []}}
    if kind == "production_merges":
        pushers = rules["pushers"]
        actors = list(ROLE_ACTORS) if pushers["mode"] == "roles" else [
            {"actor_id": team_ids[t], "actor_type": "Team", "bypass_mode": "always"} for t in pushers["teams"]
        ]  # fmt: skip
        return {"name": NAMES[kind], "target": "branch", "enforcement": "active", "conditions": conditions,
                "bypass_actors": actors,
                "rules": [{"type": "update", "parameters": {"update_allows_fetch_and_merge": False}}]}  # fmt: skip
    level = rules["production" if kind == "production" else "staging"]
    found: list[dict[str, Any]] = [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "pull_request", "parameters": {
            "required_approving_review_count": int(level["approvals"]), "dismiss_stale_reviews_on_push": True,
            "require_code_owner_review": bool(level.get("code_owner_review")) and bool(owners),
            "require_last_push_approval": False,
            "required_review_thread_resolution": False}},
    ]  # fmt: skip
    if level["require_checks"]:
        found.append({"type": "required_status_checks", "parameters": {
            "strict_required_status_checks_policy": False,
            "required_status_checks": [{"context": files.REQUIRED_CHECK,
                                        "integration_id": files.ACTIONS_APP_ID}]}})  # fmt: skip
    if level["linear_history"]:
        found.append({"type": "required_linear_history"})
    if level["signed_commits"]:
        found.append({"type": "required_signatures"})
    return {"name": NAMES[kind], "target": "branch", "enforcement": "active", "conditions": conditions,
            "bypass_actors": [], "rules": found}  # fmt: skip


def describe_ruleset(found: Mapping[str, Any], teams: Mapping[int, str] | None = None) -> dict[str, Any]:
    """A ruleset as GitHub has it, in the normalised shape (`teams`: team ids and their names)."""
    include = ((found.get("conditions") or {}).get("ref_name") or {}).get("include") or []
    by_type = {str(r.get("type")): r.get("parameters") or {} for r in found.get("rules") or []}
    shown: dict[str, Any] = {"branches": sorted(str(b).removeprefix("refs/heads/") for b in include)}
    if "update" in by_type:
        actors = found.get("bypass_actors") or []
        ids = sorted((a.get("actor_type"), a.get("actor_id")) for a in actors)
        roles = sorted((a["actor_type"], a["actor_id"]) for a in ROLE_ACTORS)
        if ids == roles:
            shown["pushers"] = "Admin and Maintain roles"
        else:
            names = sorted(f"team {(teams or {}).get(int(a.get('actor_id') or 0), a.get('actor_id'))}" if
                           a.get("actor_type") == "Team" else str(a.get("actor_type")) for a in actors)  # fmt: skip
            shown["pushers"] = ", ".join(names) or "nobody"
        return shown
    shown.update(
        pull_request="pull_request" in by_type,
        approvals=int(by_type.get("pull_request", {}).get("required_approving_review_count", 0)),
        code_owner_review=bool(by_type.get("pull_request", {}).get("require_code_owner_review")),
        checks=sorted(check_label(str(c.get("context")), c.get("integration_id")) for c in by_type.get(
            "required_status_checks", {}).get("required_status_checks", [])),
        force_push="blocked" if "non_fast_forward" in by_type else "allowed",
        deletion="blocked" if "deletion" in by_type else "allowed",
        linear_history="required_linear_history" in by_type,
        signed_commits="required_signatures" in by_type,
    )  # fmt: skip
    return shown


def protection_body(kind: str, rules: Mapping[str, Any], org_repository: bool,
                    owners: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    level = rules["production" if kind == "production" else "staging"]
    pushers = rules["pushers"]
    restrictions = None
    if kind == "production" and pushers["mode"] == "named" and org_repository:
        restrictions = {"users": list(pushers["users"]), "teams": list(pushers["teams"]), "apps": []}
    return {
        "required_status_checks": {"strict": False, "checks": [{"context": files.REQUIRED_CHECK,
                                                                "app_id": files.ACTIONS_APP_ID}]}
        if level["require_checks"] else None,
        "enforce_admins": False,
        "required_pull_request_reviews": {"required_approving_review_count": int(level["approvals"]),
                                          "dismiss_stale_reviews": True,
                                          "require_code_owner_reviews": bool(level.get("code_owner_review"))
                                          and bool(owners)},
        "restrictions": restrictions,
        "required_linear_history": bool(level["linear_history"]),
        "allow_force_pushes": False,
        "allow_deletions": False,
    }  # fmt: skip


def describe_protection(branch: str, found: Mapping[str, Any]) -> dict[str, Any]:
    reviews = found.get("required_pull_request_reviews")
    required = found.get("required_status_checks") or {}
    sources = {str(c.get("context")): c.get("app_id") for c in required.get("checks") or []}
    checks = [check_label(str(c), sources.get(str(c))) for c in required.get("contexts") or sources]
    restrictions = found.get("restrictions")
    if restrictions:
        names = [f"team {t.get('slug')}" for t in restrictions.get("teams", [])] + [
            f"@{u.get('login')}" for u in restrictions.get("users", [])]  # fmt: skip
        pushers = ", ".join(names) or "nobody"
    else:
        pushers = "anyone with write access"
    return {
        "branches": [branch],
        "pull_request": reviews is not None,
        "approvals": int((reviews or {}).get("required_approving_review_count", 0)),
        "code_owner_review": bool((reviews or {}).get("require_code_owner_reviews")),
        "checks": sorted(checks),
        "force_push": "allowed" if (found.get("allow_force_pushes") or {}).get("enabled") else "blocked",
        "deletion": "allowed" if (found.get("allow_deletions") or {}).get("enabled") else "blocked",
        "linear_history": bool((found.get("required_linear_history") or {}).get("enabled")),
        "signed_commits": bool((found.get("required_signatures") or {}).get("enabled")),
        "pushers": pushers,
    }


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "none"
    return str(value)


def diff(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Field by field: [{"field", "label", "before", "after"}] for what differs ("" where a side has nothing)."""
    keys = [k for k in LABELS if k in (before or {}) or k in (after or {})]
    found = []
    for key in keys:
        old = _show((before or {})[key]) if key in (before or {}) else ""
        new = _show((after or {})[key]) if key in (after or {}) else ""
        if old != new:
            found.append({"field": key, "label": LABELS[key], "before": old, "after": new})
    return found


# ---------------------------------------------------------------- the plan


def _target(key: str, kind: str, action: str, before: Any, after: Any, **extra: Any) -> dict[str, Any]:
    return {"key": key, "kind": kind, "action": action, "before": before, "after": after,
            "changes": diff(before, after), **extra}  # fmt: skip


def plan(project: Mapping[str, Any], rules: Mapping[str, Any], applied: Mapping[str, Any] | None,
         bearer: str, turn_off: bool = False) -> dict[str, Any]:  # fmt: skip
    """What applying `rules` (or turning them off) would change on GitHub, without changing anything:
    {"method", "available", "limitations", "targets" (each with "action": create, update, delete, unchanged or
    blocked, and its field changes), "untouched" (rules the panel did not create, never changed),
    "affects_production", "repository"}."""
    full = str(project["repository"])
    repo = gh.repository(full, bearer)
    org = repo["owner_type"] == "Organization"
    owners = [] if turn_off else code_owners(project, rules, repo["owner_type"])
    production = production_branches(project, repo["default_branch"])
    staging = [b for b in staging_branches(project) if b not in production]
    limitations: list[dict[str, str]] = []
    pushers = rules["pushers"]
    method = str(rules["method"])
    if turn_off and (applied or {}).get("method") in METHODS:
        method = str((applied or {})["method"])  # turned off the way it was applied
    elif method == "auto":
        method = "branch_protection" if pushers["mode"] == "named" and pushers["users"] else "rulesets"
    if not turn_off and pushers["mode"] == "named" and pushers["users"] and method == "rulesets":
        limitations.append({"reason": "people_in_rulesets", "message": "Rulesets cannot name people, only roles "
                            "and teams: the people listed are left out. Choose classic branch protection to name "
                            "them (organisation repositories only)."})  # fmt: skip
    if not turn_off and rules["production"].get("code_owner_review") and not owners:
        limitations.append({"reason": "no_code_owners", "message": "\"Changes to .github/ need review\" is on, but "
                            "nobody can be named as the owner of .github/: GitHub's CODEOWNERS file names teams and "
                            "people, not roles. Name a team (or people) in the pushers, then try again; until then "
                            "the rule does not ask for the review."})  # fmt: skip
    if not turn_off and pushers["mode"] == "named" and not org:
        limitations.append({"reason": "personal_repository", "message": "Teams and named people exist only in "
                            "organisations. On a personal repository, whoever has write access can push; remove "
                            "collaborators on GitHub to narrow it."})  # fmt: skip
    if not turn_off and pushers["mode"] == "roles" and method == "branch_protection":
        limitations.append({"reason": "roles_in_protection", "message": "Classic branch protection restricts pushes "
                            "only to named people and teams, not to roles: everyone with write access who passes "
                            "the pull-request rules may merge."})  # fmt: skip

    existing: list[dict[str, Any]] | None = None
    available = True
    if method == "rulesets":
        try:
            existing = gh.rulesets(full, bearer)
        except GitHubError as error:
            if not gh.plan_limited(error) and error.status != 404:
                raise
            if rules["method"] == "auto":
                method = "branch_protection"
            else:
                available = False
                limitations.append(_plan_limit(repo, "rulesets"))
    if method == "branch_protection" and available:
        try:
            gh.protection(full, production[0], bearer)
        except GitHubError as error:
            if not gh.plan_limited(error):
                raise
            available = False
            limitations.append(_plan_limit(repo, "branch protection"))

    targets: list[dict[str, Any]] = []
    untouched: list[str] = []
    applied = applied or {}
    if available and method == "rulesets":
        existing = existing if existing is not None else gh.rulesets(full, bearer)
        ours = {str(r.get("name")): r for r in existing if str(r.get("name", "")).startswith(PREFIX)}
        untouched = sorted(str(r.get("name")) for r in existing if not str(r.get("name", "")).startswith(PREFIX))
        wanted: dict[str, list[str]] = {}
        if not turn_off:
            wanted["production"] = production
            if pushers["mode"] == "roles" or (pushers["mode"] == "named" and pushers["teams"] and org):
                wanted["production_merges"] = production
            if rules["staging"]["enabled"] and staging:
                wanted["staging"] = staging
        slugs: dict[int, str] = {}
        if "production_merges" in wanted and pushers["mode"] == "named":
            slugs = {gh.team_id(full.split("/")[0], slug, bearer): slug for slug in pushers["teams"]}
        for kind in NAMES:
            current = ours.pop(NAMES[kind], None)
            before = (describe_ruleset(gh.ruleset(full, int(current["id"]), bearer) or current, slugs)
                      if current else None)  # fmt: skip
            after = None
            if kind in wanted:
                after = _wanted(kind, wanted[kind], rules, owners)
            action = ("create" if after and not before else "delete" if before and not after
                      else "unchanged" if not diff(before, after) else "update")  # fmt: skip
            if before or after:
                targets.append(_target(kind, "ruleset", action, before, after, name=NAMES[kind],
                                       id=int(current["id"]) if current else None))  # fmt: skip
        for name, left in sorted(ours.items()):  # a ruleset of the panel's it no longer makes (renamed kinds)
            targets.append(_target(name, "ruleset", "delete", {"branches": []}, None, name=name, id=int(left["id"])))
    if available and method == "branch_protection":
        mine = set(applied.get("protections") or [])
        wanted_branches = {} if turn_off else {**dict.fromkeys(production, "production"),
                                               **(dict.fromkeys(staging, "staging") if rules["staging"]["enabled"] else {})}  # fmt: skip
        for branch in sorted(set(wanted_branches) | mine):
            kind = wanted_branches.get(branch, "production")
            current = gh.protection(full, branch, bearer)
            before = describe_protection(branch, current) if current else None
            after = None
            if branch in wanted_branches:
                body = protection_body(kind, rules, org, owners)
                after = describe_protection(branch, _as_read(body, rules[kind if kind == "production" else "staging"]))
            if current and branch not in mine:
                if after:  # protected by someone else: never replaced
                    targets.append(_target(branch, "protection", "blocked", before, after, name=branch,
                                           reason="This branch already has branch protection the panel did not "
                                           "create. The panel leaves it alone; remove it on GitHub first if the "
                                           "panel should manage it."))  # fmt: skip
                    untouched.append(f"branch protection of {branch}")
                continue
            action = ("create" if after and not before else "delete" if before and not after
                      else "unchanged" if not diff(before, after) else "update")  # fmt: skip
            if before or after:
                targets.append(_target(branch, "protection", action, before, after, name=branch, kind_of=kind))
    if available and applied.get("method") and applied.get("method") != method:
        limitations.append({"reason": "method_changed", "message": f"The rules were applied with "
                            f"{_method_name(str(applied['method']))} before; those stay until you turn the rules "
                            "off with that method or remove them on GitHub."})  # fmt: skip
    affects_production = any(
        t["action"] in ("create", "update", "delete") and (t["key"].startswith("production") or t["key"] in production)
        for t in targets
    )
    return {
        "repository": repo,
        "method": method,
        "available": available,
        "limitations": limitations,
        "targets": targets,
        "untouched": untouched,
        "branches": {"production": production, "staging": staging},
        "owners": owners,
        "affects_production": affects_production,
        "changes": sum(t["action"] in ("create", "update", "delete") for t in targets),
    }


def _as_read(body: Mapping[str, Any], level: Mapping[str, Any]) -> dict[str, Any]:
    """A protection body as GitHub reads it back (to compare it with the current protection)."""
    restrictions = body.get("restrictions")
    return {
        "required_pull_request_reviews": body.get("required_pull_request_reviews"),
        "required_status_checks": None if not body.get("required_status_checks") else {
            "contexts": [c["context"] for c in body["required_status_checks"]["checks"]],
            "checks": body["required_status_checks"]["checks"]},
        "restrictions": {"users": [{"login": u} for u in restrictions["users"]],
                         "teams": [{"slug": t} for t in restrictions["teams"]]} if restrictions else None,
        "allow_force_pushes": {"enabled": body["allow_force_pushes"]},
        "allow_deletions": {"enabled": body["allow_deletions"]},
        "required_linear_history": {"enabled": body["required_linear_history"]},
        "required_signatures": {"enabled": bool(level["signed_commits"])},
    }  # fmt: skip


def _method_name(method: str) -> str:
    return "rulesets" if method == "rulesets" else "classic branch protection"


def _plan_limit(repo: Mapping[str, Any], what: str) -> dict[str, str]:
    return {
        "reason": "plan",
        "message": f"GitHub does not allow {what} on {repo['full_name']} with its current plan: private "
        "repositories need GitHub Pro, Team or Enterprise for branch rules. Make the repository public or upgrade "
        "the account's plan on GitHub, then try again.",
    }


# ---------------------------------------------------------------- applying


def apply(project: Mapping[str, Any], rules: Mapping[str, Any], applied: Mapping[str, Any] | None, bearer: str,
          turn_off: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:  # fmt: skip
    """Carry out the plan; returns (the plan, what the panel now owns on GitHub: {"method", "rulesets": {kind: id},
    "protections": [branches]}). Raises GitHubError."""
    found = plan(project, rules, applied, bearer, turn_off=turn_off)
    full = str(project["repository"])
    owned: dict[str, Any] = {"method": found["method"], "rulesets": dict((applied or {}).get("rulesets") or {}),
                             "protections": list((applied or {}).get("protections") or [])}  # fmt: skip
    if not found["available"]:
        return found, owned
    pushers = rules["pushers"]
    team_ids: dict[str, int] = {}
    org = found["repository"]["owner_type"] == "Organization"
    for target in found["targets"]:
        action = target["action"]
        if action in ("unchanged", "blocked"):
            continue
        if target["kind"] == "ruleset":
            if action == "delete":
                current = gh.ruleset(full, int(target["id"]), bearer)
                if current is not None and not str(current.get("name", "")).startswith(PREFIX):
                    raise GitHubError(f"The ruleset {current.get('name')} is not the panel's: left alone.", 409)
                gh.delete_ruleset(full, int(target["id"]), bearer)
                owned["rulesets"].pop(target["key"], None)
                continue
            kind = target["key"]
            if kind == "production_merges" and pushers["mode"] == "named":
                owner = full.split("/")[0]
                for slug in pushers["teams"]:
                    team_ids[slug] = gh.team_id(owner, slug, bearer)
            body = ruleset_body(kind, target["after"]["branches"], rules, team_ids, found["owners"])
            owned["rulesets"][kind] = gh.save_ruleset(full, body, bearer, target["id"] if action == "update" else None)
        else:
            branch = target["key"]
            if action == "delete":
                gh.delete_protection(full, branch, bearer)
                owned["protections"] = [b for b in owned["protections"] if b != branch]
                continue
            kind = target.get("kind_of", "production")
            level = rules["production" if kind == "production" else "staging"]
            gh.save_protection(
                full, branch, protection_body(kind, rules, org, found["owners"]), bool(level["signed_commits"]), bearer
            )
            if branch not in owned["protections"]:
                owned["protections"].append(branch)
    changed = [str(t["name"]) for t in found["targets"] if t["action"] in ("create", "update", "delete")]
    if changed:
        log.info(
            "repository guard: %s: branch rules %s (%s)", full, "removed" if turn_off else "applied", ", ".join(changed)
        )
    return found, owned
