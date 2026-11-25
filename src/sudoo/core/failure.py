"""Why a job failed, as people may read it: the step, a plain-language reason and only the lines of the technical log
that explain the failure ("Error details"), never the whole log.

The technical log of a job stays on the server (core/jobs.py). When a job fails, describe() reads the part of it
written by the failed step and keeps what a developer needs, by kind of failure, in this order:

    repository     git could not read a repository: the main one or a submodule ("Repository not found",
                   "Permission denied (publickey)", "could not read Username", a submodule's failed clone); the
                   repository it could not read is named (owner/name), so the panel can offer the fix next to it
    git            other git errors (fatal: / error: lines, a branch that does not exist, diverging histories)
    requirements   pip could not install a module's Python requirements (which package, why)
    tests          the failing tests and their messages (projects/testing.py already picked them)
    odoo           Odoo's ERROR and CRITICAL lines while installing or upgrading modules, with the traceback that
                   follows them (the tail of it: the module, the file inside the repository, the exception)
    other          nothing recognisable: no details, only the step and the reason

Every line kept goes through clean(): addresses' passwords and tokens masked (redact() and GitHub token patterns),
the server's own paths shortened to the path inside the repository or replaced by "<server path>", IP addresses
and the panel's container names and ids replaced; at most MAX_LINES lines, each at most MAX_LINE characters.
Infrastructure noise (the commands the panel runs, image downloads, step markers) is never kept.
"""

from __future__ import annotations

import re
from typing import Any

from sudoo.core import failure_rules, hints
from sudoo.core.shell import redact

MAX_LINES = 60
MAX_LINE = 400
KINDS = ("repository", "git", "requirements", "tests", "odoo", "other")

# lines the panel itself writes, or Docker writes while it works: never error details
_NOISE = re.compile(
    r"^(\$ |== |\[info\]|\[warning\]|Sudoo\.sh: |ERROR: (command failed|this step took too long)|Still downloading "
    r"|[0-9a-f]{12}: |Digest: |Status: |Pulling |Images ready|Container |Network |Volume |#\d+ )"
)
_ODOO_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ \d+ (INFO|WARNING|ERROR|CRITICAL) (\S+) (\S+): ?(.*)$")

_REPOSITORY = re.compile(
    r"Repository not found|Permission denied \(publickey\)|could not read Username|terminal prompts disabled"
    r"|does not appear to be a git repository|Could not read from remote repository|Authentication failed"
    r"|no deploy key for|clone of '[^']+' into submodule path|Write access to repository not granted"
    r"|The requested URL returned error: 40[34]",
    re.I,
)
_GIT = re.compile(r"^(fatal|error|remote|hint): |Remote branch .* not found|couldn't find remote ref|Not possible to "
                  r"fast-forward|diverging branches|would be overwritten by merge|Failed to clone|Unable to fetch",
                  re.I)  # fmt: skip
_PIP = re.compile(
    r"ERROR: (Could not find a version|No matching distribution|Could not build wheels|Failed building|"
    r"Cannot install|ResolutionImpossible|Invalid requirement|pip's dependency resolver)|error: subprocess-exited-"
    r"with-error|Failed building wheel|ModuleNotFoundError|No module named|× |╰─>|Collecting \S+",
)
_URL = re.compile(r"(?:https?://|ssh://|git@)[^\s'\"]+")
_TOKENS = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sudoo_[A-Za-z0-9_-]{16,})\b")
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
_LONG_ID = re.compile(r"\b(?:sha256:)?[0-9a-f]{64}\b")
_PANEL_NAMES = re.compile(r"\bsudoo-run-[\w-]+|\bsudoo-(?:panel|web|gateway|connector)[\w-]*")
# where a repository's files are: the instance's clone on the server, or its mount inside Odoo's container
_REPO_ROOTS = re.compile(r"(?:/[^\s'\":]*?/instances/[A-Za-z0-9_-]+/addons/|/mnt/extra-addons/|/mnt/repository/)")
_ODOO_SOURCES = re.compile(r"/usr/lib/python3[^\s'\":]*/(?:site|dist)-packages/(odoo/)")
_ENTERPRISE = re.compile(r"/[^\s'\":]*?/enterprise[^/\s'\":]*/")
_SERVER_PATH = re.compile(r"(?<![\w.~])/(?:opt|home|root|srv|var|tmp|etc|usr|mnt|data|dev|run|proc)(?:/[^\s'\":,)]*)?")


def clean(line: str) -> str:
    """One line as people may read it: secrets masked, the server's paths, addresses and container names removed."""
    line = redact(line.rstrip())
    line = _TOKENS.sub("********", line)
    line = _REPO_ROOTS.sub("", line)
    line = _ODOO_SOURCES.sub(r"\1", line)
    line = _ENTERPRISE.sub("enterprise/", line)
    line = _SERVER_PATH.sub("<server path>", line)
    line = _IP.sub("<address>", line)
    line = _LONG_ID.sub("<id>", line)
    line = _PANEL_NAMES.sub("<container>", line)
    return line[:MAX_LINE]


def repository_name(address: str) -> str:
    """ "owner/name" of a GitHub address (https or ssh), else the address without its credentials."""
    found = re.search(r"github\.com[:/]+([\w.-]+/[\w.-]+?)(?:\.git)?/?$", address.strip())
    return found[1] if found else redact(address.strip())


def failed_step_text(log: str) -> str:
    """The part of a technical log written by its last step ("== <title>" starts each step)."""
    marker = log.rfind("\n== ")
    return log[marker:] if marker >= 0 else log


def _odoo_errors(lines: list[str]) -> list[str]:
    """Odoo's ERROR/CRITICAL lines and the traceback after each: the last ones."""
    kept: list[str] = []
    in_trace = False
    for line in lines:
        found = _ODOO_LINE.match(line)
        if found:
            level, _db, logger, text = found.groups()
            in_trace = level in ("ERROR", "CRITICAL")
            if in_trace:
                kept.append(f"{level} {logger}: {text}")
            continue
        if line.startswith("Traceback (most recent call last)"):
            in_trace = True
        if in_trace and line.strip():
            kept.append(line)
    return kept


def _tail(lines: list[str]) -> list[str]:
    found = [clean(line) for line in lines if line.strip()]
    deduped: list[str] = []
    for line in found:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return deduped[-MAX_LINES:]


def extract(log: str, tests: dict[str, Any] | None = None) -> dict[str, Any]:
    """{"kind", "details", "repository"} of a failure from the technical log of its job (the failed step is read)."""
    text = failed_step_text(log)
    lines = [line for line in text.splitlines() if line.strip() and not _NOISE.match(line.strip())]
    access = [line for line in lines if _REPOSITORY.search(line)]
    if access:
        repository = None
        for line in lines:
            found = re.search(r"clone of '([^']+)' into submodule path", line) or re.search(
                r"(?:fatal: repository|Cloning into .*?from|remote: Repository) '?((?:https?://|ssh://|git@)[^\s']+)",
                line,
            )
            if found:
                repository = repository_name(found[1])
        if repository is None:
            urls = [match for line in access for match in _URL.findall(line)]
            repository = repository_name(urls[-1]) if urls else None
        related = [line for line in lines if _REPOSITORY.search(line) or _GIT.search(line)]
        return {"kind": "repository", "details": _tail(related), "repository": repository}
    if tests and tests.get("status") == "failed" and tests.get("messages"):
        return {"kind": "tests", "details": _tail(list(tests["messages"])), "repository": None}
    pip = [line for line in lines if _PIP.search(line)]
    if any(not line.startswith("Collecting") for line in pip):
        return {"kind": "requirements", "details": _tail(pip), "repository": None}
    odoo = _odoo_errors(lines)
    if odoo:
        return {"kind": "odoo", "details": _tail(odoo), "repository": None}
    git = [line for line in lines if _GIT.search(line)]
    if git:
        return {"kind": "git", "details": _tail(git), "repository": None}
    return {"kind": "other", "details": [], "repository": None}


def describe(step: str | None, reason: str, log: str, tests: dict[str, Any] | None = None) -> dict[str, Any]:
    """The "Why it failed" of a job: {"step", "reason", "kind", "details", "repository"} and, when a rule of
    core/failure_rules.py recognised the cause, "rule"."""
    found = extract(log, tests)
    lines = [
        clean(line) for line in failed_step_text(log).splitlines() if line.strip() and not _NOISE.match(line.strip())
    ]
    # a step's own explanation ("Sudoo.sh: ..." in the log) wins over every pattern, as it always has: no rule then
    rule = None if hints.has_panel_hint(log) else failure_rules.match(lines)
    if rule is None:  # an older entry, or nothing recognisable: no rule, everything else as before
        return {"step": step, "reason": reason} | found
    # where the matched line is within the error details shown (1-based; the first one), so the card can point at it.
    # It counts lines of the excerpt, never lines of the technical log, which the panel does not send.
    matched = str(rule.pop("matched", ""))
    shown = next((i + 1 for i, line in enumerate(found["details"]) if matched and matched in line), None)
    if shown is not None:
        rule["excerpt_line"] = shown
    # one authoritative cause: a sentence of the older pattern hints (what `reason` is for most failed jobs) gives way
    # to the rule's; any other reason (what happened, for example an update that was put back) stays
    if hints.is_pattern_hint(reason):
        reason = f"{rule['title']}. {rule['explanation']}"
    return {"step": step, "reason": reason} | found | {"rule": rule}
