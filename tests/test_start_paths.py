"""Every place that starts a job is listed and classified, found by reading the source (not a list kept by hand
elsewhere). A new `jobs.start(...)` call fails this test until it is classified: it must hold the instance's running slot
(holds_slot, or a creation or wake, which hold it by default), or be named as a job that does not start an instance, or
be named as a known gap. The gaps are jobs that can restart or start an instance without the running-limit check or the
slot hold; the list is the open work, not a claim that they are fine."""

from __future__ import annotations

import ast
from pathlib import Path

import sudoo

ROOT = Path(sudoo.__file__).parent

HOLD = "holds the slot"  # holds_slot passed, or kind create/wake (the default)
NOT_STARTING = "does not start an instance"
GAP = "may restart or start an instance: no running-limit check, no slot hold (open)"

# (file, kind literal or "<dynamic>", holds_slot argument as written or None) -> (how many calls, class)
EXPECTED: dict[tuple[str, str, str | None], tuple[int, str]] = {
    ("background.py", "wake", None): (1, HOLD),
    ("background.py", "pause", None): (2, NOT_STARTING),
    ("background.py", "backup", None): (1, NOT_STARTING),
    ("background.py", "delete", None): (1, NOT_STARTING),
    ("instances/integrity.py", "repair", None): (1, GAP),
    ("instances/planning.py", "create", None): (1, HOLD),
    ("limits/queue.py", "wake", None): (1, HOLD),
    ("maintenance.py", "<dynamic>", None): (1, GAP),
    ("plugin_manager.py", "plugin-remove-paid", None): (1, NOT_STARTING),
    ("plugin_manager.py", "backup", None): (1, NOT_STARTING),
    ("premium/loader.py", "premium-sync", None): (1, NOT_STARTING),
    ("projects/automation.py", "stop", None): (1, NOT_STARTING),
    ("projects/automation.py", "delete", None): (1, NOT_STARTING),
    ("projects/previews.py", "delete", None): (1, NOT_STARTING),
    ("projects/refresh.py", "restore", "True"): (1, HOLD),
    ("triggers/__init__.py", "update", None): (1, GAP),
    ("triggers/__init__.py", "<dynamic>", None): (1, GAP),
    ("upgrades/run.py", "db-upgrade", None): (1, GAP),
    ("web/v1/imports.py", "restore", "True"): (1, HOLD),
    ("web/v1/instances.py", "restart", "True"): (1, HOLD),
    ("web/v1/instances.py", "<dynamic>", "True if action in STARTING_ACTIONS else None"): (1, HOLD),
    ("web/v1/instances.py", "wake", None): (1, HOLD),
    ("web/v1/plugins.py", "plugin-install", None): (1, NOT_STARTING),
    ("web/v1/plugins.py", "plugin-uninstall", None): (1, NOT_STARTING),
    ("web/v1/project_resources.py", "restart", None): (1, GAP),
    ("web/v1/projects.py", "restore", None): (1, GAP),
    ("web/v1/upgrades.py", "db-upgrade", "True"): (2, HOLD),
    ("web/v1/upgrades.py", "upgrade-test", "True"): (1, HOLD),
}


def found() -> dict[tuple[str, str, str | None], int]:
    seen: dict[tuple[str, str, str | None], int] = {}
    for path in sorted(ROOT.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "start"):
                continue
            if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "jobs"):
                continue
            kind = node.args[2].value if len(node.args) > 2 and isinstance(node.args[2], ast.Constant) else "<dynamic>"
            flags = [ast.unparse(k.value) for k in node.keywords if k.arg == "holds_slot"]
            key = (str(path.relative_to(ROOT)), str(kind), flags[0] if flags else None)
            seen[key] = seen.get(key, 0) + 1
    return seen


def test_every_job_start_is_classified() -> None:
    assert found() == {key: count for key, (count, _) in EXPECTED.items()}


def test_a_job_that_holds_the_slot_says_so_or_is_a_creation_or_wake() -> None:
    for (path, kind, flag), (_, how) in EXPECTED.items():
        if how == HOLD:
            assert flag is not None or kind in ("create", "wake"), (path, kind)
        else:
            assert flag is None, (path, kind)
