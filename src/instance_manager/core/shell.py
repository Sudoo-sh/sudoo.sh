"""Running external commands (docker, git, ssh-keygen) without a shell, so arguments are never interpreted."""

from __future__ import annotations

import subprocess


def capture(cmd: list[str], timeout: float = 30) -> str:
    """Run `cmd` and return its standard output without trailing whitespace, or "" when it fails.

    Used for questions ("which commit is checked out?"), where a failure simply means "no answer".
    Leading whitespace is kept because some outputs (git submodule status) use it as a status column.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout.rstrip() if result.returncode == 0 else ""


def succeeds(cmd: list[str], timeout: float = 30) -> bool:
    """Run `cmd` quietly and tell whether it succeeded."""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False
