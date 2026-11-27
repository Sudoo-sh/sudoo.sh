"""Rules that name the cause of a failed step: an id, what was found in the log (a module, a file, a line, a package),
and a plain explanation with what to do. They add to core/hints.py, they do not replace it: hints.py keeps giving the
short sentence people already see; a rule only adds an id and the captured details on top.

Matching runs over the lines of the failed step after core.failure.clean() (secrets masked, server paths removed), so
what a rule captures is already safe to show. The first rule that matches wins, in the order of RULES.
The text is English; the panel shows the Arabic text of the same id from its own locale files (failure.rules.<id>).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    title: str
    explanation: str
    fix: str
    # names of the groups of `pattern` to report: module, file, line, package, port
    captures: tuple[str, ...] = ()


def _rule(id: str, pattern: str, title: str, explanation: str, fix: str, *captures: str) -> Rule:
    return Rule(id, re.compile(pattern, re.I), title, explanation, fix, captures)


RULES: tuple[Rule, ...] = (
    _rule(
        "odoo_unmet_dependency",
        r"module (?P<module>[\w.]+): Unmet dependencies?: (?P<package>[\w., ]+)",
        "A module needs another module that is missing",
        "A module lists another module in its dependencies, but that module is not in the code the instance uses.",
        "Add the missing module to the repository (or to the instance's repositories), or remove it from the "
        "module's dependencies.",
        "module",
        "package",
    ),
    _rule(
        "odoo_xml_parse_error",
        r"ParseError: while parsing (?P<file>\S+?):(?P<line>\d+)",
        "A module has an error in an XML file",
        "Odoo stopped while reading a view or data file of a module. The file and line are shown.",
        "Open that file at that line and fix the XML (a field that does not exist, a bad inheritance, a missing tag).",
        "file",
        "line",
    ),
    _rule(
        "odoo_external_id_missing",
        r"External ID not found in the system: (?P<module>[\w]+)\.(?P<file>[\w.]+)",
        "A file refers to a record that does not exist",
        "A view, menu or data file refers to an external id that was not loaded before it.",
        "Check the module's dependencies, and that the file defining the record comes earlier in the manifest "
        "'data' list.",
        "module",
        "file",
    ),
    _rule(
        "python_syntax_error",
        r'File "(?P<file>[^"]+\.py)", line (?P<line>\d+)\n(?:[^\n]*\n){0,3}?\s*(?:Indentation|Tab|Syntax)Error',
        "A Python file has a syntax error",
        "Python could not read a file of a module.",
        "Open that file at that line and fix the syntax.",
        "file",
        "line",
    ),
    _rule(
        "pip_no_matching_distribution",
        r"No matching distribution found for (?P<package>[\w.\-]+)|Could not find a version that satisfies the "
        r"requirement (?P<package2>[\w.\-]+)",
        "A Python package could not be found",
        "pip could not find the package (or the version) in a module's requirements.",
        "Check the name and the version in requirements.txt, and that it supports the Python of this Odoo version.",
        "package",
    ),
    _rule(
        "pip_conflict",
        r"ResolutionImpossible|Cannot install (?P<package>[\w.\-]+).* conflict",
        "Two Python requirements conflict",
        "pip cannot find versions of the requirements that work together.",
        "Loosen or align the versions in requirements.txt files, then try again.",
        "package",
    ),
    _rule(
        "python_module_missing",
        r"ModuleNotFoundError: No module named '(?P<package>[\w.]+)'",
        "A Python library is missing",
        "Code imports a Python library that is not installed in this Odoo.",
        "Add the library to requirements.txt of the module's repository.",
        "package",
    ),
    _rule(
        "port_in_use",
        r"port is already allocated|address already in use.*?(?P<port>\d{2,5})|Bind for \S*?:(?P<port2>\d+) failed",
        "A port is already in use",
        "Another program on the server already uses a port this step needs.",
        "Ask the administrator which program uses it, or choose another port.",
        "port",
    ),
    _rule(
        "access_csv_error",
        r"ir\.model\.access\.csv|ir_model_access",
        "The access rights file has a problem",
        "Odoo could not load the access rights of a module (ir.model.access.csv).",
        "Check the model names and the group ids in that file.",
    ),
)

_GROUP_ALIASES = {"package2": "package", "port2": "port"}


def match(lines: list[str]) -> dict[str, object] | None:
    """{"id", "title", "explanation", "fix", "captured": {module/file/line/package/port}} of the
    first rule that matches the lines of the failed step (already cleaned), else None. Rules see the lines joined,
    so one may span a few of them (a Python traceback). The panel never sends the whole log, so there is no line
    number to jump to: the file and line a rule captured are the ones named in the log itself."""
    text = "\n".join(lines)
    for rule in RULES:
        found = rule.pattern.search(text)
        if not found:
            continue
        captured: dict[str, str] = {}
        for name, value in found.groupdict().items():
            key = _GROUP_ALIASES.get(name, name)
            if value and key in rule.captures and key not in captured:
                captured[key] = value[:200]
        return {
            "id": rule.id,
            "title": rule.title,
            "explanation": rule.explanation,
            "fix": rule.fix,
            "captured": captured,
            "matched": found.group(0).splitlines()[0][:400],
        }
    return None
