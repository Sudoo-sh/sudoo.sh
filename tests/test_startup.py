"""The panel starts on a new, empty ODOO_ROOT: the folders it needs are made, readable by root only."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path


def test_the_panel_starts_on_an_empty_odoo_root(tmp_path: Path) -> None:
    script = (
        "from sudoo import background\n"
        "background.start = lambda: None\n"  # no Docker, monitor or connectors
        "from sudoo.web import create_app\n"
        "create_app()\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "ODOO_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    manager = tmp_path / "manager"
    assert manager.is_dir() and stat.S_IMODE(manager.stat().st_mode) == 0o700
    assert (manager / "secret_key").exists()
