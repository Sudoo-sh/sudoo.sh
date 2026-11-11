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
        r"manifest unknown|manifest for odoo:.* not found|pull access denied for odoo",
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
        "technical log shows which file.",
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
        "This step took much longer than expected and was stopped. Try again; if it keeps happening, check the "
        "technical log.",
    ),
]


def friendly_hint(log: str) -> str:
    """The first explanation whose pattern appears in the end of `log`, or a general one."""
    tail = log[-20000:]
    for pattern, hint in _HINTS:
        if re.search(pattern, tail, re.IGNORECASE):
            return hint
    return "Something went wrong. The technical log shows exactly what happened; send it to a developer if needed."
