"""The files the repository guard adds to a repository: what they say follows the project's check choices.

    .github/workflows/sudoo-checks.yml   GitHub Actions: lint (ruff or flake8), pylint-odoo, manifests, XML and CSV,
                                         and optionally the modules' tests in Odoo's official image with PostgreSQL;
                                         one job per Odoo series of the branch (the matrix), and a last job named
                                         "sudoo-checks" that fails when any other did (the check branch rules require)
    .github/sudoo/check_addons.py        the manifest, XML and CSV checks (repo_guard/addon_checks.py, copied as is)
    .pre-commit-config.yaml              the same linters on developers' machines, every hook pinned to a commit
    docs/CHECKS.md                       what the checks are and how to run them locally

Every action and every hook is pinned to a full commit hash (a tag can be moved, a commit cannot); the comment next
to it says which release that commit is. Python tools are pinned to exact versions. The files start with MARKER, so
the panel recognises its own files and never overwrites or removes a file someone else wrote.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MARKER = "Managed by Sudoo.sh (repository guard)"
REQUIRED_CHECK = "sudoo-checks"  # the name of the last job: the check the branch rules require
WORKFLOW = ".github/workflows/sudoo-checks.yml"
SCRIPT = ".github/sudoo/check_addons.py"
PRE_COMMIT = ".pre-commit-config.yaml"
DOC = "docs/CHECKS.md"
CODEOWNERS = ".github/CODEOWNERS"
PATHS = (WORKFLOW, SCRIPT, PRE_COMMIT, DOC, CODEOWNERS)
ACTIONS_APP_ID = 15368  # GitHub Actions: the only source the required check may come from

# (repository, commit, release): GitHub Actions, pinned by full commit hash
ACTIONS = {
    "checkout": ("actions/checkout", "3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"),
    "setup-python": ("actions/setup-python", "6d4fd22b20465ccc0f7354476588e8df7c9ca0e0", "v7.1.0"),
}
# pre-commit hook repositories, pinned the same way ("frozen" revisions)
HOOKS = {
    "pre-commit-hooks": ("https://github.com/pre-commit/pre-commit-hooks", "3e8a8703264a2f4a69428a0aa4dcb512790b2c8c",
                         "v6.0.0"),
    "ruff": ("https://github.com/astral-sh/ruff-pre-commit", "9026c9127e153371d7a78dfcb0c45ae2046a3d10", "v0.17.6"),
    "flake8": ("https://github.com/PyCQA/flake8", "1c7fec1973ed3a7c192dbe1e96906fbbd1dbe49e", "7.5.0"),
    "pylint-odoo": ("https://github.com/OCA/pylint-odoo", "30224d0a2ae20645b197776358a734d81197aafc", "v10.0.12"),
}  # fmt: skip
TOOLS = {"ruff": "0.17.6", "flake8": "7.5.0", "pylint-odoo": "10.0.12"}
PYTHON = "3.12"
POSTGRES_IMAGE = "postgres:16"

STRICTNESS = ("relaxed", "standard", "strict")
LINTERS = ("ruff", "flake8")

# pylint-odoo messages enabled at each strictness (each level adds to the one before)
PYLINT_RELAXED = (
    "sql-injection",
    "invalid-commit",
    "method-required-super",
    "no-write-in-compute",
    "external-request-timeout",
    "resource-not-exist",
    "manifest-data-duplicated",
)
PYLINT_STANDARD = PYLINT_RELAXED + (
    "license-allowed",
    "manifest-required-author",
    "manifest-required-key",
    "manifest-version-format",
    "attribute-deprecated",
    "method-compute",
    "method-inverse",
    "method-search",
    "context-overridden",
    "missing-return",
    "print-used",
    "test-folder-imported",
    "translation-required",
)
PYLINT_STRICT = PYLINT_STANDARD + (
    "translation-field",
    "translation-positional-used",
    "translation-not-lazy",
    "manifest-deprecated-key",
    "development-status-allowed",
    "missing-readme",
    "consider-merging-classes-inherited",
    "prefer-env-translation",
    "deprecated-odoo-model-method",
)
PYLINT = {"relaxed": PYLINT_RELAXED, "standard": PYLINT_STANDARD, "strict": PYLINT_STRICT}
RUFF_SELECT = {"relaxed": "E9,F63,F7,F82", "standard": "E,W,F", "strict": "E,W,F,B,UP,C4,SIM"}
FLAKE8_SELECT = {"relaxed": "E9,F63,F7,F82", "standard": "E,W,F", "strict": "E,W,F,C90"}

DEFAULT_CHECKS: dict[str, Any] = {
    "enabled": False,
    "lint": True,
    "linter": "ruff",
    "pylint_odoo": True,
    "manifest": True,
    "xml_csv": True,
    "tests": False,
    "strictness": "standard",
}


def _action(key: str) -> str:
    repository, commit, release = ACTIONS[key]
    return f"{repository}@{commit} # {release}"


def _lint_command(linter: str, strictness: str) -> str:
    if linter == "flake8":
        return f"flake8 --max-line-length 120 --select {FLAKE8_SELECT[strictness]} --extend-ignore E501,W503 ."
    return f"ruff check --no-cache --line-length 120 --select {RUFF_SELECT[strictness]} --ignore E501 ."


def _pylint_args(strictness: str, series: str) -> list[str]:
    return [
        "--load-plugins=pylint_odoo",
        "--disable=all",
        f"--enable={','.join(PYLINT[strictness])}",
        f"--valid-odoo-versions={series}",
        "--score=n",
    ]


def workflow(checks: dict[str, Any], versions: dict[str, Any]) -> str:
    """The workflow file. `versions`: {"default": ["18.0"], "branches": {"17.0": ["17.0"]}}, the Odoo series a
    branch's checks run with (a branch with a version of its own, else the project's)."""
    strictness = checks["strictness"]
    table = json.dumps(versions, sort_keys=True, separators=(",", ": "))
    jobs: list[str] = []
    needs: list[str] = []
    checkout = f"""      - uses: {_action("checkout")}
        with:
          persist-credentials: false"""
    python = f"""      - uses: {_action("setup-python")}
        with:
          python-version: "{PYTHON}\""""
    if checks["lint"]:
        linter = checks["linter"]
        needs.append("lint")
        jobs.append(f"""  lint:
    name: Lint ({linter})
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
{checkout}
{python}
      - run: python -m pip install "{linter}=={TOOLS[linter]}"
      - run: {_lint_command(linter, strictness)}""")
    if checks["pylint_odoo"]:
        needs.append("pylint-odoo")
        args = " ".join(_pylint_args(strictness, "${{ matrix.odoo }}"))
        jobs.append(f"""  pylint-odoo:
    name: pylint-odoo (${{{{ matrix.odoo }}}})
    needs: versions
    runs-on: ubuntu-latest
    timeout-minutes: 20
    strategy:
      fail-fast: false
      matrix:
        odoo: ${{{{ fromJSON(needs.versions.outputs.odoo) }}}}
    steps:
{checkout}
{python}
      - run: python -m pip install "pylint-odoo=={TOOLS["pylint-odoo"]}"
      - name: Run pylint-odoo on every module
        run: |
          mapfile -t modules < <(find . -name __manifest__.py -not -path "./.git/*" -printf "%h\\n" | sort)
          if [ "${{#modules[@]}}" -eq 0 ]; then echo "No Odoo modules found."; exit 0; fi
          pylint {args} "${{modules[@]}}\"""")
    if checks["manifest"] or checks["xml_csv"]:
        needs.append("addons")
        what = " ".join(f for f, on in (("--manifests", checks["manifest"]), ("--xml-csv", checks["xml_csv"])) if on)
        title = "Manifests, XML and CSV" if checks["manifest"] and checks["xml_csv"] else (
            "Manifests" if checks["manifest"] else "XML and CSV")  # fmt: skip
        jobs.append(f"""  addons:
    name: {title} (${{{{ matrix.odoo }}}})
    needs: versions
    runs-on: ubuntu-latest
    timeout-minutes: 10
    strategy:
      fail-fast: false
      matrix:
        odoo: ${{{{ fromJSON(needs.versions.outputs.odoo) }}}}
    steps:
{checkout}
{python}
      - run: python {SCRIPT} --series "${{{{ matrix.odoo }}}}" --strictness {strictness} {what}""")
    if checks["tests"]:
        needs.append("tests")
        jobs.append(f"""  tests:
    name: Odoo tests (${{{{ matrix.odoo }}}})
    needs: versions
    runs-on: ubuntu-latest
    timeout-minutes: 60
    strategy:
      fail-fast: false
      matrix:
        odoo: ${{{{ fromJSON(needs.versions.outputs.odoo) }}}}
    container:
      image: odoo:${{{{ matrix.odoo }}}}
      options: --user root
    services:
      db:
        image: {POSTGRES_IMAGE}
        env:
          POSTGRES_USER: odoo
          POSTGRES_PASSWORD: odoo
          POSTGRES_DB: postgres
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-timeout 5s --health-retries 20
    steps:
{checkout}
      - name: Install the modules and run their tests
        run: |
          modules=$(python3 {SCRIPT} --list-modules)
          if [ -z "$modules" ]; then echo "No Odoo modules found."; exit 0; fi
          paths=$(python3 {SCRIPT} --addons-paths)
          odoo --db_host db --db_port 5432 --db_user odoo --db_password odoo -d sudoo_checks \\
            --addons-path "/usr/lib/python3/dist-packages/odoo/addons,$paths" \\
            -i "$modules" --test-enable --stop-after-init --log-level test --http-port 8070 2>&1 | tee odoo.log
          if grep -qE "^[0-9-]+ [0-9:,]+ [0-9]+ (ERROR|CRITICAL) " odoo.log; then
            echo "Odoo logged errors while installing or testing the modules (see above)."
            exit 1
          fi""")
    if not jobs:  # every check off: a workflow that only says so (the files are removed instead, see plan_files)
        needs = []
    result = f"""  {REQUIRED_CHECK}:
    name: {REQUIRED_CHECK}
    if: always()
    needs: [{", ".join(needs)}]
    runs-on: ubuntu-latest
    steps:
      - name: Every check passed
        if: ${{{{ !(contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled')) }}}}
        run: echo "Every check passed."
      - name: A check failed
        if: ${{{{ contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled') }}}}
        run: |
          echo "At least one check failed: open it above to see why."
          exit 1"""
    plan = f"""  versions:
    name: Odoo series of this branch
    runs-on: ubuntu-latest
    outputs:
      odoo: ${{{{ steps.pick.outputs.odoo }}}}
    steps:
      - id: pick
        env:
          BRANCH: ${{{{ github.base_ref || github.ref_name }}}}
          VERSIONS: '{table}'
        run: |
          python3 - <<'EOF'
          import json, os
          table = json.loads(os.environ["VERSIONS"])
          chosen = table["branches"].get(os.environ["BRANCH"], table["default"])
          with open(os.environ["GITHUB_OUTPUT"], "a") as out:
              out.write("odoo=" + json.dumps(chosen) + "\\n")
          EOF"""
    body = "\n\n".join([plan, *jobs, result])
    return f"""# {MARKER}.
# Changes made here are replaced the next time the panel updates the checks: change them in the panel
# (the project's Settings tab, Repository guard). docs/CHECKS.md explains each check.
name: Sudoo.sh checks

on:
  pull_request:
  push:
    branches: ["**"]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: sudoo-checks-${{{{ github.workflow }}}}-${{{{ github.event_name }}}}-${{{{ github.ref }}}}
  cancel-in-progress: true

jobs:
{body}
"""


def pre_commit(checks: dict[str, Any], series: str) -> str:
    """The pre-commit configuration: the workflow's linters, on the developer's machine, for the project's series."""
    strictness = checks["strictness"]

    def repo(key: str) -> str:
        url, commit, release = HOOKS[key]
        return f"  - repo: {url}\n    rev: {commit}  # frozen: {release}"

    blocks = [
        repo("pre-commit-hooks")
        + "\n    hooks:\n      - id: check-merge-conflict\n      - id: check-added-large-files\n        args: [--maxkb=500]"
        + ("\n      - id: check-xml" if checks["xml_csv"] else "")
    ]
    if checks["lint"] and checks["linter"] == "ruff":
        blocks.append(repo("ruff") + "\n    hooks:\n      - id: ruff-check\n        args: [--line-length=120, "
                      f"--select={RUFF_SELECT[strictness]}, --ignore=E501]")  # fmt: skip
    if checks["lint"] and checks["linter"] == "flake8":
        blocks.append(repo("flake8") + "\n    hooks:\n      - id: flake8\n        args: [--max-line-length=120, "
                      f"--select={FLAKE8_SELECT[strictness]}, '--extend-ignore=E501,W503']")  # fmt: skip
    if checks["pylint_odoo"]:
        args = ", ".join(_pylint_args(strictness, series))
        blocks.append(repo("pylint-odoo") + f"\n    hooks:\n      - id: pylint_odoo\n        args: [{args}]")
    if checks["manifest"] or checks["xml_csv"]:
        what = " ".join(f for f, on in (("--manifests", checks["manifest"]), ("--xml-csv", checks["xml_csv"])) if on)
        blocks.append(
            "  - repo: local\n    hooks:\n      - id: sudoo-addons\n        name: Odoo manifests, XML and CSV\n"
            f"        entry: python3 {SCRIPT} --series {series} --strictness {strictness} {what}\n"
            "        language: system\n        pass_filenames: false\n"
            "        files: (__manifest__\\.py|\\.xml|\\.csv)$"
        )
    return (
        f"# {MARKER}.\n# The same checks as .github/workflows/sudoo-checks.yml, before each commit. Once per clone:\n"
        "#   pip install pre-commit && pre-commit install\n# docs/CHECKS.md explains each check.\n"
        "repos:\n" + "\n\n".join(blocks) + "\n"
    )


def document(checks: dict[str, Any], versions: dict[str, Any]) -> str:
    strictness = checks["strictness"]
    series = ", ".join(sorted({v for vs in [versions["default"], *versions["branches"].values()] for v in vs}))
    rows = []
    if checks["lint"]:
        rows.append(f"| Lint ({checks['linter']}) | Python errors and style ({checks['linter']} {TOOLS[checks['linter']]}"
                    f", rules {(RUFF_SELECT if checks['linter'] == 'ruff' else FLAKE8_SELECT)[strictness]}) |")  # fmt: skip
    if checks["pylint_odoo"]:
        rows.append(f"| pylint-odoo | The Odoo community's checks for Odoo code ({len(PYLINT[strictness])} rules) |")
    if checks["manifest"]:
        rows.append("| Manifests | Required keys, a licence Odoo knows, a version that starts with the branch's Odoo "
                    "series, listed data files that exist |")  # fmt: skip
    if checks["xml_csv"]:
        rows.append("| XML and CSV | Every XML file parses; every CSV row has as many columns as its header |")
    if checks["tests"]:
        rows.append("| Odoo tests | The modules are installed in Odoo's official image with PostgreSQL and their "
                    "tests run; any ERROR line in Odoo's log fails it |")  # fmt: skip
    table = "\n".join(rows) or "| (none) | Every check is off. |"
    return f"""<!-- {MARKER}. Replaced when the checks change in the panel. -->
# Checks

**Audience:** everyone who changes this repository.
**Covers:** the checks that run on every push and pull request, and how to run them before you commit.

The Sudoo.sh panel added these checks. They run in GitHub Actions (`{WORKFLOW}`) for Odoo {series}, at the
**{strictness}** level. The last job, `{REQUIRED_CHECK}`, fails when any other check failed: branch rules can require
it before a pull request is merged.

| Check | What it looks at |
| --- | --- |
{table}

## Run them on your machine

```bash
pip install pre-commit
pre-commit install          # once per clone: the checks now run before every commit
pre-commit run --all-files  # check everything now
```

## Change or turn off a check

Change the checks in the panel (the project's Settings tab, **Repository guard**). The panel opens a pull request
that updates or removes these files; nothing changes until it is merged. Edits made directly to these files are
replaced the next time the panel updates them.
"""


def script() -> str:
    """check_addons.py as it goes into the repository, marked as the panel's."""
    source = (Path(__file__).parent / "addon_checks.py").read_text()
    return f"# {MARKER}. Replaced when the checks change in the panel.\n{source}"


def codeowners(owners: list[str]) -> str:
    """The CODEOWNERS file that makes a change to .github/ (the workflow that makes the required check) need a
    review by one of `owners` (GitHub teams as @organisation/team, people as @login)."""
    return (
        f"# {MARKER}. Replaced when the guard's rules change in the panel.\n"
        "# A change to .github/ needs a review by one of these owners (branch rule: code-owner review).\n"
        f"/.github/ {' '.join(owners)}\n"
    )


def generate(checks: dict[str, Any], versions: dict[str, Any], owners: list[str] | None = None) -> dict[str, str]:
    """{path: content} of every file the checks need (empty when the checks are off: the files are removed).
    `owners`: the code owners of .github/ (the CODEOWNERS file is written whether or not the checks are on)."""
    extra = {CODEOWNERS: codeowners(owners)} if owners else {}
    if not checks.get("enabled") or not any(checks[k] for k in ("lint", "pylint_odoo", "manifest", "xml_csv", "tests")):
        return extra
    series = versions["default"][0]
    found = {
        WORKFLOW: workflow(checks, versions),
        PRE_COMMIT: pre_commit(checks, series),
        DOC: document(checks, versions),
        **extra,
    }
    if checks["manifest"] or checks["xml_csv"] or checks["tests"]:
        found[SCRIPT] = script()
    return found


def is_ours(content: str | None) -> bool:
    """Whether a file in the repository was written by the panel (it carries MARKER in its first lines)."""
    return content is not None and MARKER in "\n".join(content.splitlines()[:3])
