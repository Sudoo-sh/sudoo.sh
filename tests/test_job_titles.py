"""The titles the server writes for jobs and their steps are English; the panel shows its own Arabic for each one
(web/src/i18n/jobs.ts). This test reads the server's source for every job title (jobs.start) and every step title
(job.step, f-strings included, a value in a title written as {}) and compares them with the list the web tests check
(web/src/i18n/job-titles.json). A title added in the server and not in the list fails here; a title in the list that
the server no longer writes fails too. The web test (web/src/lib/job-titles.test.ts) then fails for any title in the list
that has no Arabic. To refresh the list after adding a title: `uv run python tests/test_job_titles.py`."""

from __future__ import annotations

import ast
import itertools
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "sudoo"
LIST = ROOT / "web" / "src" / "i18n" / "job-titles.json"

# Job titles the server builds from a variable or a constant, not from text at the call: written here so none is
# missed; a test below checks each file still holds its text.
BY_HAND_JOBS = {
    # title: (file, text the file must still hold)
    "Scheduled refresh from production": ("projects/refresh.py", 'ACTION = "Scheduled refresh from production"'),
    "Automatic new build": ("projects/automation.py", '"Automatic new build"'),
    "Automatic new build: {}": ("projects/automation.py", '"Automatic new build"'),
    "Automatic upgrade test: {}": ("projects/automation.py", '"Automatic upgrade test"'),
    # web/v1/instances.py ACTIONS, web/v1/plugins.py (Install / Update with a name), the maintenance window's own
    # titles (maintenance.py TITLES), plugin_manager.py (Back up before ...)
    "Install {}": ("web/v1/plugins.py", 'f"Install {plugin_id}"'),
    "Update {}": ("web/v1/plugins.py", 'f"Update {rec.get'),
    "Maintenance: Update the panel to version {}": ("maintenance.py", '"Update the panel to version {version}"'),
    "Maintenance: Update the Odoo images": ("maintenance.py", '"Update the Odoo images"'),
    "Maintenance: Restart": ("maintenance.py", '"restart": ("Restart"'),
    "Maintenance: Maintenance work": ("maintenance.py", '"Maintenance work"'),
    "Back up before {} stops": ("plugin_manager.py", 'f"Back up before {name} stops"'),
    "Back up before paid plugins are removed": ("plugin_manager.py", '"Back up before paid plugins are removed"'),
    "Switch off {} (licence ended)": ("plugin_manager.py", "f\"Switch off {rec['name']} (licence ended)\""),
}


# A step whose text is made from a word the source picks ("the chosen modules" or "the repository's modules"): each
# of the titles it can become.
EXPANDED_STEPS = {
    "{} are all installed already": [
        "The chosen modules are all installed already",
        "The repository's modules are all installed already",
    ],
    "Installing {} ({#})": ["Installing the chosen modules ({#})", "Installing the repository's modules ({#})"],
    "Creating the Odoo {} database (about 1-2 minutes)": [
        "Creating the Odoo Enterprise database (about 1-2 minutes)",
        "Creating the Odoo Community database (about 1-2 minutes)",
    ],
    "Upgrading the copy to Odoo {} ({})": [  # upgrades/run.py _method_name
        "Upgrading the copy to Odoo {} (Odoo's upgrade service)",
        "Upgrading the copy to Odoo {} (OpenUpgrade)",
    ],
}


def _value(node: ast.AST) -> str:
    """{#} for a count (len(...)), {} for any other value."""
    is_count = isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len"
    return "{#}" if is_count else "{}"


def _texts(node: ast.AST) -> list[str]:
    """Every text a title expression can have; a value that is not text written in the source becomes {}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        parts = [[v.value] if isinstance(v, ast.Constant) else [_value(v.value)] for v in node.values]
        return ["".join(p) for p in itertools.product(*parts)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return [a + b for a in _texts(node.left) for b in _texts(node.right)]
    if isinstance(node, ast.IfExp):
        return _texts(node.body) + _texts(node.orelse)
    return ["{}"]


def _clean(text: str) -> str:
    return re.sub(r"(\{\})+", "{}", text).strip()  # two values side by side are one


def collect() -> dict[str, list[str]]:
    steps: set[str] = set()
    jobs: set[str] = set()
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
                continue
            if node.func.attr == "step":
                steps.update(_clean(t) for t in _texts(node.args[0]) if _clean(t))
            elif node.func.attr == "start" and isinstance(node.func.value, ast.Name) and node.func.value.id == "jobs":
                if len(node.args) > 1:
                    jobs.update(_clean(t) for t in _texts(node.args[1]) if _clean(t) not in ("", "{}", "{}: {}"))
            elif node.func.attr == "progress" and node.args and isinstance(node.args[0], ast.JoinedStr):
                steps.update(_clean(t) for t in _texts(node.args[0]))
    for node in ast.walk(ast.parse((SOURCE / "web/v1/instances.py").read_text())):  # the actions on an instance
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "ACTIONS" for t in node.targets):
            assert isinstance(node.value, ast.Dict)
            jobs.update(ast.literal_eval(value)[0] for value in node.value.values)
    for template, titles in EXPANDED_STEPS.items():
        if template in steps:
            steps.discard(template)
            steps.update(titles)
    jobs.update(BY_HAND_JOBS)
    jobs.discard("Maintenance: {}")  # the four titles of maintenance.py TITLES are listed above instead
    # a title that is only a value says nothing; the list holds texts
    steps.discard("{}")
    return {"jobs": sorted(jobs), "steps": sorted(steps)}


def test_by_hand_titles_are_still_in_the_source() -> None:
    for title, (file, text) in BY_HAND_JOBS.items():
        assert text in (SOURCE / file).read_text(), f"{title!r}: {file} no longer holds {text}"


def test_every_title_the_server_writes_is_in_the_list_with_an_arabic_check() -> None:
    found, listed = collect(), json.loads(LIST.read_text())
    for kind in ("jobs", "steps"):
        missing = sorted(set(found[kind]) - set(listed[kind]))
        extra = sorted(set(listed[kind]) - set(found[kind]))
        assert (
            not missing
        ), f"{kind} the server writes that web/src/i18n/job-titles.json lacks (add them, with Arabic in jobs.ts): {missing}"
        assert not extra, f"{kind} in web/src/i18n/job-titles.json that the server no longer writes: {extra}"


if __name__ == "__main__":
    LIST.write_text(json.dumps(collect(), ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {LIST}")
