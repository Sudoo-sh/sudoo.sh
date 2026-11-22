"""Checking what people give the panel to pass to Odoo's command line: module names and --test-tags.

Both end up as arguments of `odoo` (never through a shell), but a value Odoo would misread (an option instead of a
module, a tag it cannot parse) must be refused when it is typed, not discovered in a failed build. Nothing here
imports the rest of the panel, so the settings (config.py) can use it.
"""

from __future__ import annotations

import re

MODULE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{0,63}$")
MAX_MODULES = 200

# one --test-tags spec (Odoo 12 and later): [-][tag][/module][:class][.method]; at least one part besides the "-"
TAG_SPEC_RE = re.compile(
    r"^-?(?P<tag>\*|[A-Za-z0-9_]+)?(?:/(?P<module>[A-Za-z0-9_]+))?(?::(?P<cls>[A-Za-z0-9_]+))?"
    r"(?:\.(?P<method>[A-Za-z0-9_]+))?$"
)
MAX_TAG_SPECS = 50


def parse_modules(value: str | list[str] | tuple[str, ...]) -> list[str]:
    """Module names from "a, b,c" (or a list): each checked, duplicates removed, order kept. Raises ValueError with
    a sentence for people."""
    items = value.split(",") if isinstance(value, str) else list(value)
    found: list[str] = []
    for raw in items:
        if not isinstance(raw, str):
            raise ValueError("Module names are text, such as sale_custom.")
        name = raw.strip()
        if not name:
            continue
        if not MODULE_RE.match(name):
            raise ValueError(f"{name[:40]!r} is not a module name (letters, digits and _, such as sale_custom).")
        if name not in found:
            found.append(name)
    if len(found) > MAX_MODULES:
        raise ValueError(f"At most {MAX_MODULES} modules.")
    return found


def parse_test_tags(value: str) -> str:
    """A --test-tags value, checked and without spaces ("" for none). Raises ValueError with a sentence."""
    if not isinstance(value, str):
        raise ValueError("Test tags are text, such as /sale_custom,-slow.")
    specs = [spec.strip() for spec in value.split(",") if spec.strip()]
    if len(specs) > MAX_TAG_SPECS:
        raise ValueError(f"At most {MAX_TAG_SPECS} test tags.")
    for spec in specs:
        if spec in ("-", "") or not TAG_SPEC_RE.match(spec):
            raise ValueError(
                f"{spec[:40]!r} is not a test tag Odoo understands: [-][tag][/module][:class][.method], such as "
                "/sale_custom, post_install, -slow or /sale_custom:TestOrder.test_total."
            )
    return ",".join(specs)


def modules_of_tags(tags: str) -> list[str]:
    """The modules a --test-tags value names (/module parts of the specs that include tests)."""
    found: list[str] = []
    for spec in (s for s in tags.split(",") if s and not s.startswith("-")):
        match = TAG_SPEC_RE.match(spec)
        if match and match["module"] and match["module"] not in found:
            found.append(match["module"])
    return found


DEMO_FLAG_FROM = 19  # Odoo 19 installs no demo data unless --with-demo; older versions install it unless told not to


def demo_args(version: str, demo: bool) -> list[str]:
    """The arguments of `odoo -i base` that give a new database Odoo's demo data (`demo`) or not. Odoo 19 and later
    load none by default and take --with-demo; Odoo 18 and older load it by default, refuse --with-demo ("no such
    option", exit code 2) and take --without-demo=all."""
    try:
        major = int(str(version).split(".")[0].removeprefix("saas~").removeprefix("saas-"))
    except ValueError:
        major = DEMO_FLAG_FROM
    if major >= DEMO_FLAG_FROM:
        return ["--with-demo"] if demo else []
    return [] if demo else ["--without-demo=all"]
