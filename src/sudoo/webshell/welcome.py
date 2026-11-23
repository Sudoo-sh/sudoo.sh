"""The welcome of a web shell's Terminal: a banner, where the person is, the commands that work there, and a prompt
in the colour of the instance's stage. Written by the shell itself (bash), so it shows in any terminal the panel
offers, not only in the web app.

    welcome.bash   the start file bash reads instead of ~/.bashrc (it reads ~/.bashrc itself first)
    rc()           that file with the banner drawn in (truecolour, 256 colours and no colour)
    environment()  what the panel adds to the process's environment: the start file (SUDOO_RC), what it shows
                   (SUDOO_*), the database for psql (PG*) and the instance's name as HOSTNAME
    START          the command that runs bash with the start file, or sh where there is no bash

The start file travels in the environment of the process the panel starts (docker exec) and reaches bash on a file
descriptor (/dev/fd/9, a here-document): nothing is written in the container, nothing is mounted, and instances made
before this existed get it too, without being recreated. The container's id, the server's paths, addresses and ports
are never shown: the prompt names the instance and shows the code's folder as ~.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from pathlib import Path

from sudoo.instances import stages

# Runs bash with the start file read from descriptor 9 (filled from $SUDOO_RC by a here-document: its expanded text
# is taken as it is), interactive; images without bash get a plain login shell, as before.
START = """\
if command -v bash >/dev/null 2>&1; then
exec bash --rcfile /dev/fd/9 -i 9<<SUDOO_RC_END
$SUDOO_RC
SUDOO_RC_END
fi
exec sh -l"""

# The banner: block letters of our own drawing, 5 rows high, with a shadow one row down and one column right.
GLYPHS: dict[str, tuple[str, ...]] = {
    "S": (" █████", "██    ", " ████ ", "    ██", "█████ "),
    "U": ("██  ██", "██  ██", "██  ██", "██  ██", " ████ "),
    "D": ("█████ ", "██  ██", "██  ██", "██  ██", "█████ "),
    "O": (" ████ ", "██  ██", "██  ██", "██  ██", " ████ "),
    ".": ("  ", "  ", "  ", "  ", "██"),
    "H": ("██  ██", "██  ██", "██████", "██  ██", "██  ██"),
}
WORD = "SUDOO.SH"
# the letters in a light plum, their shadow in the brand's plum (#714b67), as 24-bit colours and their nearest
# 256-colour codes; without colour the shadow is drawn with a lighter block so it still reads
FACE_24, SHADOW_24 = "38;2;199;155;189", "38;2;113;75;103"
FACE_256, SHADOW_256 = "38;5;182", "38;5;96"
SHADE = "░"


@functools.cache
def banner_cells() -> tuple[str, ...]:
    """The banner as rows of cells: "F" a letter, "S" its shadow, " " nothing (all rows the same width)."""
    rows = [" ".join(GLYPHS[letter][row] for letter in WORD) for row in range(5)]
    width = max(len(row) for row in rows) + 1
    grid = [row.ljust(width) for row in rows] + [" " * width]
    cells = []
    for r, row in enumerate(grid):
        line = ""
        for c, char in enumerate(row):
            if char != " ":
                line += "F"
            elif r and c and grid[r - 1][c - 1] != " ":
                line += "S"
            else:
                line += " "
        cells.append(line)
    return tuple(cells)


def banner(mode: str) -> list[str]:
    """The banner's lines: mode "24" (truecolour), "256" or "plain"."""
    colours = {"24": (FACE_24, SHADOW_24), "256": (FACE_256, SHADOW_256)}.get(mode)
    lines = []
    for row in banner_cells():
        line, current = "", None
        for cell in row.rstrip():
            kind = None if cell == " " else cell
            if colours and kind != current:
                line += "\033[0m" if kind is None else f"\033[{colours[0] if kind == 'F' else colours[1]}m"
                current = kind
            line += " " if kind is None else "█" if kind == "F" or colours else SHADE
        if colours and current is not None:
            line += "\033[0m"
        lines.append(line)
    return lines


def _ansi_c(text: str) -> str:
    """`text` as a bash $'...' word."""
    return "$'" + text.replace("\\", "\\\\").replace("'", "\\'").replace("\033", "\\e") + "'"


@functools.cache
def rc() -> str:
    """The start file, with the banner in each of its three forms."""
    drawn = [f"_sudoo_banner_width={len(banner_cells()[0])}"]
    for mode, name in (("24", "_sudoo_banner_24"), ("256", "_sudoo_banner_256"), ("plain", "_sudoo_banner_plain")):
        drawn.append(f"{name}=(\n" + "".join(f"    {_ansi_c(line)}\n" for line in banner(mode)) + ")")
    template = (Path(__file__).resolve().parent / "welcome.bash").read_text()
    return template.replace("# @BANNER@", "\n".join(drawn))


def _plain(value: str) -> str:
    """No control characters (a branch or project name must not move the cursor or change colours)."""
    return "".join(char for char in value if char.isprintable())[:120]


def environment(
    name: str, env: Mapping[str, str], *, code: str, odoo_conf: str, database: Mapping[str, str], project: str = ""
) -> dict[str, str]:
    """The variables a Terminal in the Odoo container of instance `name` (its .env: `env`) gets from the panel;
    `database`: the host, port and user of its database, as its odoo.conf names them."""
    stage = stages.of(env)
    edition = env.get("EDITION", "")
    return {
        "SUDOO_RC": rc(),
        "SUDOO_INSTANCE": name,
        "SUDOO_STAGE": stage,
        "SUDOO_ODOO_VERSION": _plain(env.get("ODOO_VERSION", "")),
        "SUDOO_EDITION": edition.capitalize() if edition in ("community", "enterprise") else "",
        "SUDOO_PROJECT": _plain(project),
        "SUDOO_BRANCH": _plain(env.get("GIT_BRANCH", "")),
        "SUDOO_DB": env.get("DB_NAME", ""),
        "SUDOO_CODE": code,
        "SUDOO_ODOO_CONF": odoo_conf,
        "HOSTNAME": name,
        # psql reaches the instance's database as Odoo does; its password stays out of the environment and is
        # given to psql alone (welcome.bash)
        "PGHOST": database.get("host", ""),
        "PGPORT": database.get("port", ""),
        "PGUSER": database.get("user", ""),
        "PGDATABASE": env.get("DB_NAME", ""),
    }
