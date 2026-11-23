"""The repository guard of a project: CI checks and pre-commit hooks added to its GitHub repository, their results in
the panel, and branch rules on GitHub. Everything is off until a project Admin turns it on, and everything can be
turned off again.

    checks      the panel opens a pull request that adds (or updates, or removes) the check files
                (repo_guard/files.py): never a push to a branch people work on. Its link and state are kept here.
    results     the CI results of a commit (check runs and commit statuses), shown on builds and in the history;
                with "block_promotion" (off by default) a failing result is a red item of the pre-promotion
                checklist (projects/promotion.py)
    rules       rulesets (or classic branch protection) for the production and staging branches
                (repo_guard/rules.py): previewed as a difference first, applied after the person types the
                repository's name, removed again by "turn off" (only the rules the panel made)

It needs more of the GitHub App than reading code (repo_guard/github.PERMISSIONS); the panel shows what is missing
and where to grant it, and never asks for it before the feature is used.

Storage: collection "repo_guard", one document per project (key: its id):
    {"checks": {...}, "block_promotion": bool, "rules": {...}, "pull": {...} | None, "applied": {...} | None,
     "changed_by", "changed_at"}
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Mapping
from typing import Any

from sudoo.instances import backups
from sudoo.repo_guard import files, rules
from sudoo.repo_guard import github as gh
from sudoo.repos import github_app
from sudoo.repos.github_app import GitHubError
from sudoo.storage import Document, storage

COLLECTION = "repo_guard"
RESULTS_SECONDS = 60  # a commit's CI results are read from GitHub at most once a minute
TEAM_RE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"
LOGIN_RE = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"

_results: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


class GuardError(Exception):
    """A change the guard refuses; the message is meant for people."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- stored choices


def _merge(default: Mapping[str, Any], own: Mapping[str, Any] | None) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in default.items():
        mine = (own or {}).get(key)
        found[key] = _merge(value, mine) if isinstance(value, Mapping) else (value if mine is None else mine)
    return found


def stored(project_id: str) -> Document:
    return storage().get(COLLECTION, project_id) or {}


def config(project_id: str) -> dict[str, Any]:
    """The project's choices, with the defaults for what it never chose."""
    found = stored(project_id)
    return {
        "checks": _merge(files.DEFAULT_CHECKS, found.get("checks")),
        "block_promotion": bool(found.get("block_promotion", False)),
        "rules": _merge(rules.DEFAULT_RULES, found.get("rules")),
    }


def _save(project_id: str, user: str, **values: Any) -> Document:
    def update(found: Document | None) -> Document:
        return {**(found or {}), **values, "changed_by": user, "changed_at": int(time.time())}

    return storage().change(COLLECTION, project_id, update) or {}


def forget(project_id: str) -> None:
    storage().delete(COLLECTION, project_id)


def _flag(data: Mapping[str, Any], key: str, current: bool) -> bool:
    value = data.get(key, current)
    if not isinstance(value, bool):
        raise GuardError(f"{key}: true or false.")
    return value


def clean_checks(data: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """The checks' choices from a request (missing keys keep their value). Raises GuardError."""
    if not isinstance(data, Mapping):
        raise GuardError("checks: an object.")
    unknown = set(data) - set(files.DEFAULT_CHECKS)
    if unknown:
        raise GuardError(f"Unknown check option {sorted(unknown)[0][:40]!r}.")
    found = {k: _flag(data, k, bool(current[k])) for k in ("enabled", "lint", "pylint_odoo", "manifest", "xml_csv",
                                                           "tests")}  # fmt: skip
    found["linter"] = data.get("linter", current["linter"])
    if found["linter"] not in files.LINTERS:
        raise GuardError(f"linter: {' or '.join(files.LINTERS)}.")
    found["strictness"] = data.get("strictness", current["strictness"])
    if found["strictness"] not in files.STRICTNESS:
        raise GuardError(f"strictness: {', '.join(files.STRICTNESS)}.")
    return found


def _names(value: Any, pattern: str, what: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 50:
        raise GuardError(f"{what}: a list of at most 50 names.")
    found = []
    for item in value:
        if not isinstance(item, str) or not re.match(pattern, item.strip().lstrip("@")):
            raise GuardError(f"{what}: {str(item)[:40]!r} is not a GitHub name.")
        found.append(item.strip().lstrip("@"))
    return sorted(set(found), key=str.lower)


def clean_rules(data: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """The branch rules' choices from a request (missing keys keep their value). Raises GuardError."""
    if not isinstance(data, Mapping):
        raise GuardError("rules: an object.")
    unknown = set(data) - set(rules.DEFAULT_RULES)
    if unknown:
        raise GuardError(f"Unknown rule option {sorted(unknown)[0][:40]!r}.")
    found: dict[str, Any] = {"enabled": _flag(data, "enabled", bool(current["enabled"]))}
    found["method"] = data.get("method", current["method"])
    if found["method"] not in rules.METHODS:
        raise GuardError(f"method: {', '.join(rules.METHODS)}.")
    for stage in ("production", "staging"):
        given = data.get(stage, {})
        if not isinstance(given, Mapping):
            raise GuardError(f"{stage}: an object.")
        mine = dict(current[stage])
        for key in (
            "require_checks",
            "linear_history",
            "signed_commits",
            *(("enabled",) if stage == "staging" else ()),
        ):
            mine[key] = _flag(given, key, bool(mine[key]))
        approvals = given.get("approvals", mine["approvals"])
        if not isinstance(approvals, int) or isinstance(approvals, bool) or not 0 <= approvals <= rules.MAX_APPROVALS:
            raise GuardError(f"{stage}.approvals: a whole number from 0 to {rules.MAX_APPROVALS}.")
        mine["approvals"] = approvals
        found[stage] = mine
    pushers = data.get("pushers", {})
    if not isinstance(pushers, Mapping):
        raise GuardError("pushers: an object.")
    mode = pushers.get("mode", current["pushers"]["mode"])
    if mode not in rules.PUSHERS:
        raise GuardError(f"pushers.mode: {', '.join(rules.PUSHERS)}.")
    found["pushers"] = {
        "mode": mode,
        "teams": _names(pushers.get("teams", current["pushers"]["teams"]), TEAM_RE, "pushers.teams"),
        "users": _names(pushers.get("users", current["pushers"]["users"]), LOGIN_RE, "pushers.users"),
    }
    if mode == "named" and not (found["pushers"]["teams"] or found["pushers"]["users"]):
        raise GuardError("pushers: name at least one team or person, or choose the roles.")
    return found


# ---------------------------------------------------------------- what the GitHub App may do


def owner_of(project: Mapping[str, Any]) -> str:
    return str(project["repository"]).split("/")[0]


def permissions_view(project: Mapping[str, Any]) -> dict[str, Any]:
    """Which of the guard's permissions the installation on the repository's owner granted, and where to fix it."""
    owner = owner_of(project)
    app = github_app.describe() if github_app.connected() else None
    found = gh.installation(owner) if app else None
    have = gh.granted(owner) if found else {}
    listed = [
        {**p, "granted": have.get(p["key"]) or None,
         "ok": gh.LEVEL_RANK.get(have.get(p["key"]), 0) >= gh.LEVEL_RANK[p["level"]]}
        for p in gh.PERMISSIONS
    ]  # fmt: skip
    return {
        "app_connected": app is not None,
        "installed": found is not None and not found.get("suspended"),
        "permissions": listed,
        "missing": {feature: (gh.missing(owner, feature) if found else sorted(gh.FEATURE_PERMISSIONS[feature]))
                    for feature in gh.FEATURE_PERMISSIONS},  # fmt: skip
        "app_permissions_url": (app["settings_url"] + "/permissions") if app else None,
        "installation_url": str((found or {}).get("settings_url") or "") or None,
        "install_url": app["install_url"] if app else None,
    }


def describe(project: Mapping[str, Any]) -> dict[str, Any]:
    """Everything the guard's card shows; nothing is read from GitHub here (fast, always works)."""
    found = stored(str(project["id"]))
    return {
        **config(str(project["id"])),
        "pull": found.get("pull"),
        "applied": found.get("applied"),
        "changed_by": found.get("changed_by"),
        "changed_at": found.get("changed_at"),
        "repository": str(project["repository"]),
        "versions": versions_of(project),
        "required_check": files.REQUIRED_CHECK,
        "files": list(files.PATHS),
        "github": permissions_view(project),
    }


# ---------------------------------------------------------------- the checks' pull request


def versions_of(project: Mapping[str, Any]) -> dict[str, Any]:
    """The Odoo series the checks run with: the project's, and each branch's own when it has one."""
    default = backups.series(str(project.get("odoo_version") or "")) or "18.0"
    own = {
        branch: [found]
        for branch, entry in sorted((project.get("branches") or {}).items())
        if (found := backups.series(str((entry or {}).get("odoo_version") or ""))) and found != default
    }
    return {"default": [default], "branches": own}


def _plan_files(project: Mapping[str, Any], checks: Mapping[str, Any], bearer: str | None,
                base: str | None) -> list[dict[str, Any]]:  # fmt: skip
    """Each file: {"path", "action": add | update | remove | unchanged | kept, "content", "reason"?}. Without a
    token the repository is not read and the actions are what would happen to an empty repository."""
    wanted = files.generate(dict(checks), versions_of(project))
    found: list[dict[str, Any]] = []
    for path in files.PATHS:
        current = gh.read_file(str(project["repository"]), path, base, bearer) if bearer and base else None
        content = wanted.get(path)
        if current is not None and not files.is_ours(current):
            if content is not None:
                found.append({"path": path, "action": "kept", "content": content, "reason": "The repository has this "
                              "file already, written by someone else: the panel leaves it alone."})  # fmt: skip
            continue
        if content is None:
            if current is not None:
                found.append({"path": path, "action": "remove", "content": None})
            continue
        action = "add" if current is None else "unchanged" if current == content else "update"
        found.append({"path": path, "action": action, "content": content})
    return found


def preview_checks(project: Mapping[str, Any], checks: Mapping[str, Any]) -> dict[str, Any]:
    """What the pull request would contain, without changing anything (the repository is read when the app may)."""
    owner = owner_of(project)
    bearer = base = None
    note = None
    if not gh.missing(owner, "checks") and gh.installation(owner):
        bearer = gh.token(owner, {"contents": "write"})
        base = gh.repository(str(project["repository"]), bearer)["default_branch"]
    else:
        note = (
            "The GitHub App cannot read the repository's files with the permissions it has: this shows the files as "
            "they would be added to a repository without them."
        )
    planned = _plan_files(project, checks, bearer, base)
    return {"files": planned, "base": base, "note": note,
            "changes": sum(f["action"] in ("add", "update", "remove") for f in planned)}  # fmt: skip


def _pull_body(checks: Mapping[str, Any], planned: list[dict[str, Any]]) -> str:
    lines = [
        "The Sudoo.sh panel proposes these changes to the repository's checks (Repository guard, in the project's "
        "Settings).",
        "",
        *[f"- `{f['path']}`: {f['action']}" for f in planned if f["action"] in ("add", "update", "remove")],
        "",
    ]
    if checks["enabled"]:
        lines.append(f"Strictness: **{checks['strictness']}**. `docs/CHECKS.md` explains each check and how to run "
                     "them locally with pre-commit.")  # fmt: skip
    else:
        lines.append("The checks were turned off in the panel: this removes the files the panel added.")
    lines += ["", "Nothing changes until this pull request is merged. Close it to keep the repository as it is."]
    return "\n".join(lines)


def sync_checks(project: Mapping[str, Any], checks: Mapping[str, Any], user: str,
                dry_run: bool = False) -> dict[str, Any]:  # fmt: skip
    """Save the checks' choices and open, update or close the panel's pull request so the repository follows them.
    With `dry_run` nothing is saved and nothing changes on GitHub. Returns {"files", "pull", "result"}; result:
    "opened", "updated", "up_to_date" (nothing to change; an open pull request of the panel is closed), "dry_run"."""
    full, owner = str(project["repository"]), owner_of(project)
    lacking = gh.missing(owner, "checks") if gh.installation(owner) else ["installation"]
    if dry_run:
        return {**preview_checks(project, checks), "pull": stored(str(project["id"])).get("pull"), "result": "dry_run"}
    if lacking:
        raise GuardError(
            "The GitHub App may not open pull requests with workflow files in this repository yet ("
            + ", ".join(lacking)
            + " missing). Grant the permissions shown, then try again.",
            409,
        )
    bearer = gh.token(owner, dict(gh.FEATURE_PERMISSIONS["checks"]))
    base = gh.repository(full, bearer)["default_branch"]
    planned = _plan_files(project, checks, bearer, base)
    changes = {f["path"]: f["content"] for f in planned if f["action"] in ("add", "update", "remove")}
    previous = stored(str(project["id"])).get("pull")
    now = int(time.time())
    if not changes:
        pull = previous
        current = gh.open_pull(full, bearer)
        if current is not None:  # the repository already follows the choices: the proposal is not needed
            pull = {**(previous or {}), **gh.close_pull(full, current["number"], bearer), "closed_at": now}
            gh.delete_head_branch(full, bearer)
        _save(str(project["id"]), user, checks=dict(checks), pull=pull)
        return {"files": planned, "pull": pull, "result": "up_to_date"}
    title = "Sudoo.sh: update the repository's checks" if checks["enabled"] else "Sudoo.sh: remove the checks"
    message = title + "\n\nProposed from the Sudoo.sh panel (Repository guard)."
    gh.commit_files(full, base, changes, message, bearer)
    found = gh.propose(full, base, title, _pull_body(checks, planned), bearer)
    pull = {**found, "base": base, "purpose": "add" if checks["enabled"] else "remove", "by": user, "at": now,
            "files": sorted(changes)}  # fmt: skip
    _save(str(project["id"]), user, checks=dict(checks), pull=pull)
    return {"files": planned, "pull": pull, "result": "updated" if found["updated"] else "opened"}


def refresh_pull(project: Mapping[str, Any]) -> dict[str, Any] | None:
    """The panel's pull request as GitHub has it now (open, closed or merged), stored again."""
    found = stored(str(project["id"]))
    pull = found.get("pull")
    if not pull or not pull.get("number"):
        return pull
    owner = owner_of(project)
    bearer = gh.token(owner, {"pull_requests": "read"})
    fresh = {**pull, **gh.pull(str(project["repository"]), int(pull["number"]), bearer), "read_at": int(time.time())}

    def update(current: Document | None) -> Document:
        return {**(current or {}), "pull": fresh}

    storage().change(COLLECTION, str(project["id"]), update)
    return fresh


# ---------------------------------------------------------------- CI results


def results_available(project: Mapping[str, Any]) -> bool:
    owner = owner_of(project)
    return github_app.connected() and gh.installation(owner) is not None and not gh.missing(owner, "results")


def results(project: Mapping[str, Any], shas: list[str], now: float | None = None) -> dict[str, Any]:
    """{sha: result (github.commit_results) or {"state": "unknown", "error"}} for each commit, read at most once a
    minute; {} when the project's checks are off or the app may not read check runs."""
    if not config(str(project["id"]))["checks"]["enabled"] or not results_available(project):
        return {}
    now = now if now is not None else time.time()
    owner = owner_of(project)
    answer: dict[str, Any] = {}
    bearer: str | None = None
    for sha in dict.fromkeys(shas):
        key = (str(project["id"]), sha)
        with _lock:
            cached = _results.get(key)
        if cached and now - cached[0] < RESULTS_SECONDS:
            answer[sha] = cached[1]
            continue
        try:
            if bearer is None:
                extra = {"statuses": "read"} if gh.granted(owner).get("statuses") in ("read", "write") else {}
                bearer = gh.token(owner, {"checks": "read", **extra})
            found = gh.commit_results(str(project["repository"]), sha, bearer)
        except GitHubError as error:
            found = {"state": "unknown", "error": str(error)}
        with _lock:
            _results[key] = (now, found)
        answer[sha] = found
    return answer


def forget_results() -> None:
    with _lock:
        _results.clear()


def promotion_item(project: Mapping[str, Any], sha: str | None, branch: str) -> dict[str, Any] | None:
    """The pre-promotion checklist's "ci" item for the commit `sha` of `branch`, or None when the project's checks
    are off. Red only when the project blocks promotion on failing checks; amber otherwise."""
    chosen = config(str(project["id"]))
    if not chosen["checks"]["enabled"]:
        return None
    block = chosen["block_promotion"]
    if not results_available(project):
        return {"key": "ci", "status": "warning", "text": "The CI checks' results cannot be read: the GitHub App "
                "lacks Checks: read.", "ci": None}  # fmt: skip
    if not sha:
        return {"key": "ci", "status": "warning", "text": f"{branch} has no built commit whose checks could be read.",
                "ci": None}  # fmt: skip
    found = results(project, [sha]).get(sha) or {"state": "unknown"}
    shown = {k: found.get(k) for k in ("state", "total", "failed", "pending", "url")}
    state = found.get("state")
    if state == "success":
        return {"key": "ci", "status": "ok", "text": f"The CI checks of {sha[:7]} passed.", "ci": shown}
    if state == "failure":
        text = f"{found.get('failed')} CI {'check' if found.get('failed') == 1 else 'checks'} failed on {sha[:7]}."
        return {"key": "ci", "status": "blocked" if block else "warning", "text": text, "ci": shown}
    if state == "pending":
        return {"key": "ci", "status": "warning", "text": f"The CI checks of {sha[:7]} are still running.", "ci": shown}
    if state == "none":
        return {"key": "ci", "status": "warning", "text": f"No CI checks ran on {sha[:7]}.", "ci": shown}
    return {"key": "ci", "status": "warning", "text": "The CI checks' results could not be read from GitHub.",
            "ci": shown}  # fmt: skip


# ---------------------------------------------------------------- branch rules


def _rules_token(project: Mapping[str, Any], chosen: Mapping[str, Any]) -> str:
    owner = owner_of(project)
    if not gh.installation(owner):
        raise GuardError(f"The GitHub App is not installed on {owner}.", 409)
    lacking = gh.missing(owner, "rules")
    if lacking:
        raise GuardError("The GitHub App may not change branch rules yet (Administration: write missing). Grant it "
                         "on GitHub, then try again.", 409)  # fmt: skip
    wanted = dict(gh.FEATURE_PERMISSIONS["rules"])
    teams = chosen["pushers"]["mode"] == "named" and chosen["pushers"]["teams"]
    if teams and gh.granted(owner).get("members") in ("read", "write"):
        wanted["members"] = "read"
    return gh.token(owner, wanted)


def preview_rules(project: Mapping[str, Any], chosen: Mapping[str, Any], turn_off: bool = False) -> dict[str, Any]:
    applied = stored(str(project["id"])).get("applied")
    return rules.plan(project, chosen, applied, _rules_token(project, chosen), turn_off=turn_off)


def apply_rules(project: Mapping[str, Any], chosen: Mapping[str, Any], user: str, confirm: str,
                turn_off: bool = False, dry_run: bool = False) -> dict[str, Any]:  # fmt: skip
    """Apply the branch rules `chosen` (or remove the panel's, `turn_off`). A change that touches production needs
    `confirm` to be the repository's name. `dry_run`: the plan only."""
    found = preview_rules(project, chosen, turn_off=turn_off)
    if dry_run:
        return {**found, "result": "dry_run"}
    if not found["available"]:
        raise GuardError(" ".join(item["message"] for item in found["limitations"] if item["reason"] == "plan")
                         or "GitHub does not allow these rules on this repository.", 409)  # fmt: skip
    if found["affects_production"] and confirm.strip().lower() != str(project["repository"]).lower():
        raise GuardError(f"These rules change production: type the repository's name ({project['repository']}) to "
                         "confirm.", 400)  # fmt: skip
    applied = stored(str(project["id"])).get("applied")
    done, owned = rules.apply(project, chosen, applied, _rules_token(project, chosen), turn_off=turn_off)
    nothing = not owned["rulesets"] and not owned["protections"]
    record = None if nothing else {**owned, "at": int(time.time()), "by": user}
    _save(str(project["id"]), user, rules={**dict(chosen), "enabled": not turn_off}, applied=record)
    return {**done, "result": "removed" if turn_off else "applied", "applied": record}


def set_block_promotion(project: Mapping[str, Any], value: bool, user: str) -> dict[str, Any]:
    _save(str(project["id"]), user, block_promotion=bool(value))
    return describe(project)
