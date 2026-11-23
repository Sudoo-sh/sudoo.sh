"""The server's own figures on a server that is itself a container (LXC): /host/proc wins over /proc."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from sudoo.instances import monitor


def _write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_memory_comes_from_the_mounted_server_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "meminfo", "MemTotal:        3145728 kB\nMemFree:  100 kB\nMemAvailable:    1572864 kB\n")
    monkeypatch.setattr(monitor, "HOST_PROC", tmp_path)
    total, used = monitor._host_memory()
    assert total == 3 * 1024**3
    assert used == 1536 * 1024**2


def test_without_the_mounts_proc_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor, "HOST_PROC", tmp_path / "missing")
    assert monitor._proc("meminfo") == Path("/proc/meminfo")


def test_uptime_counts_from_the_servers_own_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hz = os.sysconf("SC_CLK_TCK")
    boot = int(time.time()) - 30 * 86400  # the provider's host booted 30 days ago
    started_ticks = 16 * 86400 * hz  # the server (its init) started 16 days after that
    _write(tmp_path, "stat", f"cpu  1 2 3 4 5 6 7 8\nbtime {boot}\n")
    fields = ["S"] + ["0"] * 18 + [str(started_ticks)] + ["0"] * 10
    _write(tmp_path, "1/stat", "1 (systemd) " + " ".join(fields) + "\n")
    monkeypatch.setattr(monitor, "HOST_PROC", tmp_path)
    assert abs(monitor._host_uptime() - 14 * 86400) <= 2


def test_uptime_falls_back_to_proc_uptime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor, "HOST_PROC", tmp_path / "missing")
    assert monitor._host_uptime() >= 0
