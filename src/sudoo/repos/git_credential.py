"""git credential helper: hands git a GitHub App installation token for github.com repositories over HTTPS.

github.configure_git sets it for https://github.com (with useHttpPath, so git tells the repository path):

    git -> python -m sudoo.repos.git_credential get      (stdin: protocol=https, host=github.com, path=owner/repo.git)
        <- username=x-access-token, password=<installation token>

git asks only when GitHub refuses an anonymous request, so public repositories never need a token. The token is
handed over on standard output to git alone: it is never part of an address, never written to .git/config and never
shown in a job's log. Owners without an installation get no answer (git then reports that it cannot read the
repository), and "store"/"erase" requests are ignored: the panel keeps its own cache (github_app.py).
"""

from __future__ import annotations

import sys
from typing import TextIO


def read_request(stream: TextIO) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in stream:
        line = line.rstrip("\n")
        if not line:
            break
        key, _, value = line.partition("=")
        values[key] = value
    return values


def answer(action: str, values: dict[str, str]) -> str:
    """What to print for git: the credentials, or "" when this helper has nothing to give."""
    if action != "get" or values.get("protocol") != "https" or values.get("host", "").lower() != "github.com":
        return ""
    owner = values.get("path", "").lstrip("/").split("/")[0]
    if not owner:
        return ""
    from sudoo.repos import github_app

    try:
        token = github_app.token_for(owner)
    except github_app.GitHubError as error:
        print(f"Sudoo.sh GitHub App: {error}", file=sys.stderr)
        return ""
    except Exception as error:  # never a traceback in git's output (a job's log): only what went wrong
        print(f"Sudoo.sh GitHub App: no token ({error.__class__.__name__}).", file=sys.stderr)
        return ""
    return f"username=x-access-token\npassword={token}\n" if token else ""


def main(args: list[str]) -> int:
    action = args[0] if args else ""
    values = read_request(sys.stdin)
    sys.stdout.write(answer(action, values))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
