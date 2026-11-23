"""Checks of the Odoo modules of a repository: their manifests, XML files and CSV files.

This file is not used by the panel itself: the repository guard (repo_guard/) copies it into the repositories it
guards, as .github/sudoo/check_addons.py, where the workflow and the pre-commit hook run it. It needs only Python's
standard library (3.8 or newer), so it runs on a developer's machine, on GitHub's runners and inside Odoo's image.

    python3 .github/sudoo/check_addons.py --series 18.0 [--strictness relaxed|standard|strict]
                                          [--manifests] [--xml-csv]       (both when neither is given)
    python3 .github/sudoo/check_addons.py --list-modules                  comma-separated module names
    python3 .github/sudoo/check_addons.py --addons-paths                  comma-separated folders holding modules

Manifests: the file parses as a Python dict literal; the keys every module needs are present (name, version, license,
depends; author from standard; summary and website when strict); the license is one Odoo knows; the version is
"<series>.x.y.z" for the branch's Odoo series (standard and strict; relaxed only checks the series of a five-part
version); depends is a list of module names; every data file listed exists. XML files parse; CSV files parse and
every row has as many columns as the header. It prints one line per problem and exits 1 when there is one.
"""

from __future__ import annotations

import argparse
import ast
import csv
import os
import re
import sys
from xml.etree import ElementTree

SKIP = {".git", ".github", "node_modules", "__pycache__", "setup", ".venv", "venv"}
LICENSES = {
    "GPL-2",
    "GPL-2 or any later version",
    "GPL-3",
    "GPL-3 or any later version",
    "AGPL-3",
    "LGPL-3",
    "Other OSI approved licence",
    "OEEL-1",
    "OPL-1",
    "Other proprietary",
}
REQUIRED = {
    "relaxed": ("name", "version", "license", "depends"),
    "standard": ("name", "version", "license", "depends", "author"),
    "strict": ("name", "version", "license", "depends", "author", "summary", "website"),
}
SERIES_RE = re.compile(r"^\d+\.\d$")
MODULE_RE = re.compile(r"^[a-z0-9_]+$")


def modules(root: str) -> list[str]:
    """Folders under `root` (any depth, hidden and tool folders left out) that hold a __manifest__.py."""
    found: list[str] = []
    for folder, children, files in os.walk(root):
        children[:] = sorted(c for c in children if c not in SKIP and not c.startswith("."))
        if "__manifest__.py" in files:
            found.append(folder)
            children[:] = []  # a module's own folders hold no other modules
    return sorted(found)


def read_manifest(path: str) -> dict[str, object]:
    with open(path, encoding="utf-8") as handle:
        value = ast.literal_eval(handle.read())
    if not isinstance(value, dict):
        raise ValueError("the manifest is not a dict")
    return value


def check_manifest(folder: str, series: str, strictness: str) -> list[str]:
    path = os.path.join(folder, "__manifest__.py")
    try:
        manifest = read_manifest(path)
    except (OSError, ValueError, SyntaxError) as error:
        return [f"{path}: cannot be read ({error})"]
    problems = [f"{path}: '{key}' is missing" for key in REQUIRED[strictness] if not manifest.get(key)]
    license_name = manifest.get("license")
    if license_name and license_name not in LICENSES:
        problems.append(f"{path}: license {license_name!r} is not one Odoo knows ({', '.join(sorted(LICENSES))})")
    version = str(manifest.get("version") or "")
    parts = version.split(".")
    if version and series:
        if len(parts) == 5 and ".".join(parts[:2]) != series:
            problems.append(f"{path}: version {version} is for Odoo {'.'.join(parts[:2])}, this branch is {series}")
        elif len(parts) != 5 and strictness != "relaxed":
            problems.append(f"{path}: version {version} should be {series}.x.y.z (five numbers)")
        if not all(p.isdigit() for p in parts):
            problems.append(f"{path}: version {version} should contain only numbers and dots")
    depends = manifest.get("depends", [])
    if not isinstance(depends, list) or not all(isinstance(d, str) and MODULE_RE.match(d) for d in depends):
        problems.append(f"{path}: depends must be a list of module names")
    for key in ("data", "demo"):
        listed = manifest.get(key) or []
        if not isinstance(listed, list):
            problems.append(f"{path}: {key} must be a list of files")
            continue
        for name in listed:
            if isinstance(name, str) and not os.path.exists(os.path.join(folder, name)):
                problems.append(f"{path}: {key} lists {name}, which does not exist")
    return problems


def check_files(folder: str) -> list[str]:
    problems: list[str] = []
    for current, children, files in os.walk(folder):
        children[:] = sorted(c for c in children if c not in SKIP and not c.startswith("."))
        for name in sorted(files):
            path = os.path.join(current, name)
            if name.endswith(".xml"):
                try:
                    ElementTree.parse(path)
                except (ElementTree.ParseError, OSError) as error:
                    problems.append(f"{path}: not valid XML ({error})")
            elif name.endswith(".csv"):
                problems += check_csv(path)
    return problems


def check_csv(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        return [f"{path}: not a readable CSV file ({error})"]
    if not rows:
        return []
    width = len(rows[0])
    return [
        f"{path}: line {number} has {len(row)} columns, the header has {width}"
        for number, row in enumerate(rows[1:], start=2)
        if row and len(row) != width
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the Odoo modules of this repository.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--series", default="", help="the branch's Odoo series, such as 18.0")
    parser.add_argument("--strictness", choices=sorted(REQUIRED), default="standard")
    parser.add_argument("--manifests", action="store_true")
    parser.add_argument("--xml-csv", action="store_true")
    parser.add_argument("--list-modules", action="store_true")
    parser.add_argument("--addons-paths", action="store_true")
    args = parser.parse_args(argv)
    found = modules(args.root)
    if args.list_modules:
        print(",".join(os.path.basename(m) for m in found))
        return 0
    if args.addons_paths:
        print(",".join(sorted({os.path.abspath(os.path.dirname(m)) for m in found})))
        return 0
    if args.series and not SERIES_RE.match(args.series):
        parser.error("--series looks like 18.0")
    both = not args.manifests and not args.xml_csv
    problems: list[str] = []
    for folder in found:
        if args.manifests or both:
            problems += check_manifest(folder, args.series, args.strictness)
        if args.xml_csv or both:
            problems += check_files(folder)
    for line in problems:
        print(line)
    print(f"{len(found)} modules checked, {len(problems)} problems.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
