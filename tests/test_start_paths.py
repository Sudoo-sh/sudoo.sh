"""Every place that starts a job is listed and classified, found by reading the source (not a list kept by hand
elsewhere). A new `jobs.start(...)` call fails this test until it is classified: it must hold the instance's running slot
(holds_slot, or a creation or wake, which hold it by default), or be named as a job that does not start an instance, or
be named as a known gap (GAP: none is left now; a new one can be added there while it is open work).
This is an inventory of one spelling, `jobs.start(...)`, not proof of coverage: a start that goes around jobs.start
(a direct docker or compose call, a wrapper) is not seen. The second test below fails on the spellings that would hide
a call from the first (an alias, `from ... import start`, `core.jobs.start`, `jobs.start` passed as a value); it does
not follow a wrapper function. It also does not check that the running-limit check ran before the start, only that the
slot hold is asked for."""

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
    ("instances/planning.py", "create", None): (1, HOLD),
    ("instances/integrity.py", "repair", "True"): (1, HOLD),
    ("limits/queue.py", "wake", None): (1, HOLD),
    ("maintenance.py", "<dynamic>", "True"): (1, HOLD),
    ("plugin_manager.py", "plugin-remove-paid", None): (1, NOT_STARTING),
    ("plugin_manager.py", "backup", None): (1, NOT_STARTING),
    ("premium/loader.py", "premium-sync", None): (1, NOT_STARTING),
    ("projects/automation.py", "stop", None): (1, NOT_STARTING),
    ("projects/automation.py", "delete", None): (1, NOT_STARTING),
    ("projects/previews.py", "delete", None): (1, NOT_STARTING),
    ("projects/refresh.py", "restore", "True"): (1, HOLD),
    ("triggers/__init__.py", "<dynamic>", "True"): (1, HOLD),
    ("upgrades/run.py", "db-upgrade", "True"): (1, HOLD),
    ("web/v1/imports.py", "restore", "True"): (1, HOLD),
    ("web/v1/instances.py", "restart", "True"): (1, HOLD),
    ("web/v1/instances.py", "<dynamic>", "True if action in STARTING_ACTIONS else None"): (1, HOLD),
    ("web/v1/instances.py", "wake", None): (1, HOLD),
    ("web/v1/plugins.py", "plugin-install", None): (1, NOT_STARTING),
    ("web/v1/plugins.py", "plugin-uninstall", None): (1, NOT_STARTING),
    ("web/v1/project_resources.py", "restart", "True"): (1, HOLD),
    ("web/v1/projects.py", "restore", "True"): (1, HOLD),
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


def hidden_spellings() -> list[str]:
    """Places that could start a job without being a literal `jobs.start(...)` call."""
    out: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "jobs":
                out += [f"{rel}:{node.lineno} from jobs import {a.name}" for a in node.names if a.name == "start"]
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                for a in node.names:
                    imports_jobs = a.name.split(".")[-1] == "jobs" if isinstance(node, ast.Import) else a.name == "jobs"
                    if imports_jobs and a.asname not in (None, "jobs"):
                        out.append(f"{rel}:{node.lineno} jobs imported as {a.asname}")
            if isinstance(node, ast.Attribute) and node.attr == "start":
                owner = ast.unparse(node.value)
                if owner != "jobs" and owner.split(".")[-1] == "jobs":
                    out.append(f"{rel}:{node.lineno} {owner}.start")
    for path in sorted(ROOT.rglob("*.py")):  # `jobs.start` used as a value (partial, callback), not called
        tree = ast.parse(path.read_text())
        called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "start"
                and isinstance(node.value, ast.Name)
                and node.value.id == "jobs"
                and id(node) not in called
            ):
                out.append(f"{path.relative_to(ROOT)}:{node.lineno} jobs.start used as a value")
    return out


# `jobs.start` used as a value, classified by reading it: plugin_manager.watch(start_job=jobs.start) starts only the
# "plugin-expired" job on JOB_INSTANCE (the panel-wide plugin job, not an instance), so it does not start an instance.
VALUE_USES = {"plugin_manager.py": NOT_STARTING}


def test_no_spelling_hides_a_job_start_from_the_inventory() -> None:
    unclassified = [
        h for h in hidden_spellings() if not (h.endswith("used as a value") and h.split(":")[0] in VALUE_USES)
    ]
    assert unclassified == []


def test_the_alias_check_sees_the_spellings_it_claims_to(monkeypatch) -> None:
    sample = (
        "from sudoo.core.jobs import start\nfrom sudoo.core import jobs as j\nimport sudoo.core.jobs\n"
        "sudoo.core.jobs.start(1)\nf = jobs.start\n"
    )
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "x.py").write_text(sample)
        monkeypatch.setitem(globals(), "ROOT", Path(tmp))
        found_here = hidden_spellings()
    assert len(found_here) >= 4, found_here


ADMITTED = ("start_admitted(", "quotas.LOCK", "admit_start(")


def test_a_row_that_holds_the_slot_through_a_flag_also_checks_the_limit_in_its_file() -> None:
    """Weak by design: the file mentions the check (capacity.start_admitted, or quotas.LOCK with admit_start). It does
    not prove the check runs before the start on every path; the behavioural tests of each row do (test_slot_rows)."""
    for (path, kind, flag), (_, how) in EXPECTED.items():
        if how == HOLD and flag == "True" and kind not in ("create", "wake"):
            text = (ROOT / path).read_text()
            assert any(word in text for word in ADMITTED), (path, kind)


def test_no_row_is_left_as_a_known_gap() -> None:
    assert [key for key, (_, how) in EXPECTED.items() if how == GAP] == []
