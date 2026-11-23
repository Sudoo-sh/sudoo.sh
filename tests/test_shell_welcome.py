"""The Terminal's welcome (webshell/welcome.py, welcome.bash): run as the panel runs it (sh -c START, the start file
in SUDOO_RC) in a pseudo-terminal, for each stage, in a narrow terminal and with NO_COLOR; and the process the panel
creates carries it."""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import termios
import time
from pathlib import Path
from typing import Any

import pytest

from sudoo.instances import odoo, store
from sudoo.webshell import policy, welcome

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

CONTAINER_ID = "09e95bd2d9df"
DATABASE = {"host": "db", "port": "5432", "user": "odoo"}
ESCAPES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _environment(stage: str, code: Path, **extra: str) -> dict[str, str]:
    env = {"ODOO_VERSION": "18.0", "EDITION": "enterprise", "DB_NAME": "shop", "GIT_BRANCH": "main", "STAGE": stage}
    found = welcome.environment(
        "shop-" + stage, env, code=str(code), odoo_conf="/nowhere/odoo.conf", database=DATABASE, project="Shop"
    )
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(code.parent / "home"), "LANG": "C.UTF-8"}
    return base | {"HOSTNAME": CONTAINER_ID, "TERM": "xterm-256color", "COLORTERM": "truecolor"} | found | extra


def _terminal(env: dict[str, str], cwd: Path, typed: list[str], cols: int = 100) -> str:
    """What the Terminal shows: sh -c START in a pseudo-terminal `cols` wide, with `typed` lines typed into it."""
    fd, child = pty.openpty()
    fcntl.ioctl(child, termios.TIOCSWINSZ, struct.pack("HHHH", 40, cols, 0, 0))
    # what docker exec runs, as the panel asks for it (subprocess, not pty.fork: the test process has threads)
    process = subprocess.Popen(
        ["sh", "-c", welcome.START], stdin=child, stdout=child, stderr=child, cwd=cwd, env=env, start_new_session=True
    )
    os.close(child)
    shown = b""

    def prompts() -> int:
        return ESCAPES.sub("", shown.decode(errors="replace")).count("$ ")

    def read_until_prompt(count: int, seconds: float = 10) -> None:
        """Read what the terminal shows until it has shown `count` prompts (or the shell ended)."""
        nonlocal shown
        end = time.time() + seconds
        while prompts() < count and time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                shown += data

    read_until_prompt(1)
    for line in typed:
        os.write(fd, line.encode() + b"\r")
        read_until_prompt(prompts() + 1)
    os.write(fd, b"exit\r")
    try:
        process.wait(5)
    finally:
        process.kill()
        os.close(fd)
    return shown.decode(errors="replace").replace("\r", "")


@pytest.fixture
def code(tmp_path: Path) -> Path:
    (tmp_path / "code" / "module_a").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    return tmp_path / "code"


def test_the_banner_is_our_own_compact_and_plum() -> None:
    cells = welcome.banner_cells()
    assert len(cells) == 6 and all(len(row) == len(cells[0]) < 60 for row in cells)
    coloured = "\n".join(welcome.banner("24"))
    assert "\x1b[38;2;113;75;103m" in coloured and "\x1b[38;2;199;155;189m" in coloured
    assert "\x1b[38;5;96m" in "\n".join(welcome.banner("256"))
    plain = welcome.banner("plain")
    assert all("\x1b" not in line for line in plain) and "░" in "".join(plain)
    assert "odoosh" not in welcome.rc().lower().replace(".", "")


def test_the_environment_names_the_instance_never_the_container() -> None:
    env = {"ODOO_VERSION": "18.0", "EDITION": "community", "DB_NAME": "shop", "GIT_BRANCH": "fix\x1b[2Jit"}
    found = welcome.environment(
        "shop", env, code="/mnt/extra-addons", odoo_conf="/etc/odoo/odoo.conf", database=DATABASE
    )
    assert (
        found["HOSTNAME"] == "shop" and found["SUDOO_STAGE"] == "development" and found["SUDOO_EDITION"] == "Community"
    )
    assert found["SUDOO_BRANCH"] == "fix[2Jit"  # no control characters reach the terminal
    assert found["PGDATABASE"] == "shop" and found["PGHOST"] == "db" and "PGPASSWORD" not in found
    assert found["SUDOO_RC"] == welcome.rc()


@pytest.mark.parametrize(
    ("stage", "colour", "note"),
    [
        ("production", "\x1b[1;31m", "This is production: changes affect live users."),
        ("staging", "\x1b[1;33m", "goes to the mail catcher"),
        ("development", "\x1b[1;32m", "goes to the mail catcher"),
    ],
)
def test_each_stage_has_its_welcome_and_prompt_colour(code: Path, stage: str, colour: str, note: str) -> None:
    shown = _terminal(_environment(stage, code), code, ["cd module_a", "cd /", "cd", "echo host=$HOSTNAME"])
    plain = ESCAPES.sub("", shown)
    assert "█" in shown and "\x1b[38;2;113;75;103m" in shown  # the banner, in plum
    assert f"You are connected to the {stage} instance shop-{stage}" in plain
    assert "running Odoo 18.0 (Enterprise) · project Shop · branch main" in plain
    assert note in plain
    assert (stage == "production") == ("live users" in plain)
    assert f"{colour}shop-{stage}" in shown  # the stage word and the name in the stage's colour
    prompt = f"shop-{stage} [{stage}/18.0]:"
    assert f"{colour}shop-{stage} [{stage}/18.0]" in shown  # the prompt, in bold and the stage's colour
    assert f"{prompt}~$ cd module_a" in plain  # the shell starts in the code, shown as ~
    assert f"{prompt}~/module_a$ cd /" in plain
    assert f"{prompt}/$ cd\n" in plain and f"{prompt}~$ echo" in plain  # `cd` alone goes back to the code
    assert f"host=shop-{stage}" in plain
    assert CONTAINER_ID not in shown and str(code) not in plain  # never the container's id, never the server's path


def test_only_commands_that_work_here_are_listed(code: Path) -> None:
    plain = ESCAPES.sub("", _terminal(_environment("development", code), code, ["sudoo-help"]))
    commands = re.findall(r"^  (\S+)", plain, re.MULTILINE)
    assert "cd" in commands and "sudoo-help" in commands
    assert ("psql" in commands) == (shutil.which("psql") is not None)
    assert "odoo-shell" not in commands and "odoo-update" not in commands  # no Odoo and no odoo.conf here


def test_a_narrow_terminal_gets_no_banner(code: Path) -> None:
    shown = _terminal(_environment("staging", code), code, [], cols=40)
    assert "█" not in shown and "You are connected to the" in ESCAPES.sub("", shown)


def test_no_color_turns_every_colour_off(code: Path) -> None:
    shown = _terminal(_environment("production", code, NO_COLOR="1"), code, ["cd module_a"])
    assert not re.search(r"\x1b\[[0-9;]*m", shown)  # no colour (bash may still switch bracketed paste on and off)
    assert "░" in shown and "shop-production [production/18.0]:~/module_a$" in shown


def test_the_users_own_bashrc_is_read_first(code: Path) -> None:
    (code.parent / "home" / ".bashrc").write_text("alias hello='echo hi-from-bashrc'\nPS1='mine$ '\n")
    plain = ESCAPES.sub("", _terminal(_environment("development", code), code, ["hello"]))
    assert "hi-from-bashrc" in plain and "shop-development [development/18.0]:~$ hello" in plain


def test_a_shell_that_is_not_interactive_prints_nothing(tmp_path: Path, code: Path) -> None:
    rc = tmp_path / "rc.bash"
    rc.write_text(welcome.rc())
    env = _environment("production", code)
    done = subprocess.run(["bash", "-c", f". {rc}; type -t sudoo-help"], env=env, capture_output=True, text=True)
    assert done.stdout == "function\n" and done.stderr == ""


# ---------------------------------------------------------------- the process the panel creates


def _instance(name: str, stage: str) -> None:
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {"ODOO_VERSION": "19.0", "EDITION": "community", "DB_NAME": f"{name}_db", "GIT_BRANCH": "dev", "STAGE": stage},
    )


def test_the_terminal_carries_the_start_file_and_the_instances_details() -> None:
    _instance("welcomebox", "staging")
    try:
        user: dict[str, Any] = {"name": "admin1", "role": "super_admin"}
        shell = policy.plan(user, "welcomebox", "shell")
        assert shell.argv[:3] == ("sh", "-c", policy.NICE) and shell.argv[-3:] == ("sh", "-c", welcome.START)
        assert shell.stage == "staging" and shell.workdir == odoo.CUSTOM_ADDONS_MOUNT
        env = dict(shell.env)
        assert env["SUDOO_RC"] == welcome.rc() and env["SUDOO_STAGE"] == "staging"
        assert env["SUDOO_INSTANCE"] == env["HOSTNAME"] == "welcomebox" and env["SUDOO_ODOO_VERSION"] == "19.0"
        assert env["SUDOO_CODE"] == odoo.CUSTOM_ADDONS_MOUNT and env["PGDATABASE"] == "welcomebox_db"
        assert policy.plan(user, "welcomebox", "odoo-shell").env == ()  # only the Terminal gets the welcome
        assert policy.plan(user, "welcomebox", "psql").env == ()
    finally:
        shutil.rmtree(store.folder("welcomebox"), ignore_errors=True)
