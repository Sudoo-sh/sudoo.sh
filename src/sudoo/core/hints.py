"""Plain-language explanations of technical errors, shown next to a failed job."""

from __future__ import annotations

import re

_HINTS: list[tuple[str, str]] = [
    (
        r"Permission denied \(publickey\)|no deploy key for",
        "The server cannot read this GitHub repository. Create a deploy key for it (GitHub access, or the "
        "Repository access list when creating an instance) and add the key on GitHub.",
    ),
    (
        r"could not read Username|terminal prompts disabled|Repository not found|does not appear to be a git repository",
        "GitHub says this repository does not exist, or it is private and has no deploy key yet. Check the address, "
        "and for a private repository create a deploy key and add it on GitHub.",
    ),
    (
        r"no longer exists on GitHub|no such ref was fetched",
        "The branch this instance follows was deleted on GitHub, often after its pull request was merged. Open the "
        "instance, then Actions, Switch branch, and choose the branch it should follow now (for example main).",
    ),
    (
        r"Remote branch .* not found|couldn't find remote ref",
        "That branch does not exist on GitHub. Pick another branch.",
    ),
    (
        r"manifest unknown|manifest for odoo:.* not found|pull access denied for odoo"
        r'|failed to resolve reference "[^"]*/odoo:[^"]*": .*not found',
        "This Odoo version is not published on Docker Hub yet. Choose another version.",
    ),
    (
        r"Not possible to fast-forward|diverging branches|have diverged|would be overwritten by merge",
        "The code on the server and the code on GitHub went in different directions (something was changed directly "
        "on the server). A developer needs to sort this out before updates can continue.",
    ),
    (r"No space left on device", "The server's disk is full. Delete instances that are no longer needed."),
    (r"Cannot connect to the Docker daemon", "The panel cannot reach Docker on the server. Docker may not be running."),
    (
        r"ParseError|odoo\.tools\.convert",
        "One of the modules has an error in its XML files (views or data). A developer needs to fix the module; the "
        "server's log shows which file (your administrator can read it).",
    ),
    (
        r"ModuleNotFoundError|No module named",
        "A module needs a Python library that is not installed in Odoo. A developer needs to add it.",
    ),
    (
        r"Could not resolve host|Temporary failure in name resolution|Network is unreachable",
        "The server could not reach the internet (GitHub or Docker Hub).",
    ),
    (
        r"took too long",
        "This step took much longer than expected and was stopped. Try again; if it keeps happening, ask your "
        "administrator to look at the server's log.",
    ),
]


PANEL_HINT = "Sudoo.sh: "  # a step that knows better writes "Sudoo.sh: <explanation>" into the log (panel_hint)
_PANEL_HINT_RE = re.compile(r"^Sudoo\.sh: (.+)$", re.MULTILINE)
_APP_NOT_FOUND = (
    "GitHub says this repository does not exist, or the panel's GitHub App cannot read it. Check the address, and on "
    "the GitHub access page which accounts the app is installed on and which repositories it may read."
)


def panel_hint(text: str) -> str:
    """The log line that makes `text` the hint of a failed job (it wins over the patterns below)."""
    return f"\n{PANEL_HINT}{text}\n"


def _app_connected() -> bool:
    from sudoo.repos import github_app  # late: repos/github.py imports this module

    return github_app.connected()


def friendly_hint(log: str) -> str:
    """The panel's own explanation written into the log (panel_hint), else the first explanation whose pattern
    appears in the end of `log`, else a general one. The deploy-key advice is given only when no GitHub App is
    connected."""
    return specific_hint(log) or GENERAL_HINT


GENERAL_HINT = "Something went wrong in this step. The server's log has the details; your administrator can read it."


def specific_hint(log: str) -> str | None:
    """friendly_hint without the general fallback: None when nothing in `log` has an explanation of its own."""
    tail = log[-20000:]
    own = _PANEL_HINT_RE.findall(tail)
    if own:
        return str(own[-1]).strip()
    for pattern, hint in _HINTS:
        if pattern.startswith("could not read Username") and re.search(pattern, tail, re.IGNORECASE):
            return _APP_NOT_FOUND if _app_connected() else hint
        if re.search(pattern, tail, re.IGNORECASE):
            return hint
    return None


def has_panel_hint(log: str) -> bool:
    """Whether a step wrote its own explanation into the log (panel_hint): it wins over every pattern."""
    return bool(_PANEL_HINT_RE.search(log[-20000:]))


def is_pattern_hint(text: str) -> bool:
    """Whether `text` is one of the general pattern hints (or the fallback), as friendly_hint answers them. A rule of
    core/failure_rules.py replaces such a sentence, so one failure never shows two different causes."""
    return text in {hint for _pattern, hint in _HINTS} or text in (GENERAL_HINT, _APP_NOT_FOUND)
