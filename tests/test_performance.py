"""What the panel keeps in memory to answer fast (docs/PERFORMANCE.md) is never stale and never shared wrongly:
every cache notices the change it depends on, and the limits that protect memory hold."""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from sudoo import accounts, api_tokens
from sudoo.config import settings
from sudoo.core import cache, jobs
from sudoo.core.events import Event
from sudoo.instances import backups, metrics, store
from sudoo.repos import gitrepo
from sudoo.web.v1 import events as stream
from sudoo.web.v1 import resources

Client = tuple[FlaskClient, dict[str, str]]


def _age(path: Path, seconds: float = 5) -> None:
    """Make a file look as if it was last changed `seconds` ago (caches never trust a file changed just now)."""
    when = time.time() - seconds
    os.utime(path, (when, when))


def test_a_file_changed_just_now_is_read_again_every_time(tmp_path: Path) -> None:
    path = tmp_path / "value"
    reads: list[str] = []
    memo = cache.FileMemo(lambda p: reads.append(p.read_text()) or p.read_text())
    path.write_text("one")
    assert memo.get(path) == "one" and memo.get(path) == "one"
    assert len(reads) == 2  # racy: not kept
    _age(path)
    memo.get(path)
    memo.get(path)
    assert len(reads) == 3  # settled: kept
    path.write_text("two")  # same size, new time
    assert memo.get(path) == "two"
    _age(path, 10)  # an older time than when it was cached: still another signature
    assert memo.get(path) == "two"


def test_versioned_memo_follows_the_version_and_the_age() -> None:
    built: list[int] = []
    memo: cache.VersionedMemo[int] = cache.VersionedMemo(seconds=60)
    assert memo.get("k", 1, lambda: built.append(1) or 1) == 1
    assert memo.get("k", 1, lambda: built.append(2) or 2) == 1
    assert memo.get("k", 2, lambda: built.append(3) or 3) == 3
    assert built == [1, 3]
    short: cache.VersionedMemo[int] = cache.VersionedMemo(seconds=0)
    short.get("k", 1, lambda: 1)
    assert short.get("k", 1, lambda: 2) == 2


def test_instance_env_is_read_again_when_it_changes() -> None:
    name = "perf-env"
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        store.write_env(name, {"ODOO_PORT": 1})
        _age(folder / ".env")
        first = store.read_env(name)
        first["ODOO_PORT"] = "changed by a caller"  # callers get their own copy
        assert store.read_env(name)["ODOO_PORT"] == "1"
        store.update_env(name, ODOO_PORT=2)
        assert store.read_env(name)["ODOO_PORT"] == "2"
    finally:
        shutil.rmtree(folder)


def test_backup_list_follows_files_added_and_removed() -> None:
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    name = "perf-backups__perf_backups__20260101-000000__manual.zip"
    path = settings.backups_dir / name
    before = {b["file"] for b in backups.all_backups()}
    path.write_bytes(b"zip")
    try:
        assert name in {b["file"] for b in backups.all_backups()}
        _age(settings.backups_dir)
        backups.all_backups()[0]["file"] = "changed by a caller"
        assert name in {b["file"] for b in backups.all_backups()}
    finally:
        path.unlink()
    assert {b["file"] for b in backups.all_backups()} == before


def test_repository_address_follows_git_config(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "https://github.com/a/one.git"], check=True)
    _age(tmp_path / ".git" / "config")
    assert gitrepo.origin_url(tmp_path) == "https://github.com/a/one.git"
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "set-url", "origin", "https://github.com/a/two.git"], check=True
    )
    assert gitrepo.origin_url(tmp_path) == "https://github.com/a/two.git"


def test_commits_are_read_again_after_a_job(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(resources, "commits", lambda _: calls.append("commits") or [])
    monkeypatch.setattr(resources, "submodules", lambda _: [])
    resources.code("perf-code")
    resources.code("perf-code")
    assert calls == ["commits"]
    jobs._GENERATIONS["perf-code"] += 1  # a job started or ended on the instance
    resources.code("perf-code")
    assert calls == ["commits", "commits"]


def test_metrics_history_answer_changes_with_every_sample(admin: Client) -> None:
    client, _ = admin
    server = {"cpu_percent": 1.0, "memory_percent": 2.0, "disk_percent": 3.0, "load_1m": 0.1}
    metrics.sample(server, {})
    first = client.get("/api/v1/metrics/history?minutes=5").get_json()["data"]["server"]["cpu_percent"]
    metrics.sample({**server, "cpu_percent": 77.0}, {})
    second = client.get("/api/v1/metrics/history?minutes=5").get_json()["data"]["server"]["cpu_percent"]
    assert first[-1] == 1.0 and second[-1] == 77.0


def test_shared_answers_are_compressed_once_for_callers_that_accept_it(admin: Client) -> None:
    client, _ = admin
    metrics.sample({"cpu_percent": 5.0}, {f"perf-{n}": metrics.Usage(True, 1.0, 1, 2, None, None) for n in range(40)})
    plain = client.get("/api/v1/metrics/history?minutes=60")
    packed = client.get("/api/v1/metrics/history?minutes=60", headers={"Accept-Encoding": "gzip, br"})
    assert plain.headers.get("Content-Encoding") is None and "Accept-Encoding" in plain.headers["Vary"]
    assert packed.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(packed.get_data()) == plain.get_data()


def test_shared_answers_still_check_the_caller(app: Flask) -> None:
    """The cached history is the same for every allowed caller; without a session there is no answer at all."""
    assert app.test_client().get("/api/v1/metrics/history?minutes=5").status_code == 401


def test_live_event_frames_are_built_once_per_event() -> None:
    one = Event(10_001, "metrics", "", {"x": 1})
    other = Event(10_001, "metrics", "", {"x": 2})  # same number from another bus (tests): never mixed up
    assert stream._published_frame(one) is stream._published_frame(one)
    assert b'"x":2' in stream._published_frame(other)


def test_replayed_nonces_are_refused_and_old_ones_forgotten(monkeypatch: pytest.MonkeyPatch) -> None:
    api_tokens._remember_nonce("perf-nonce-0123456789")
    with pytest.raises(PermissionError):
        api_tokens._remember_nonce("perf-nonce-0123456789")
    later = time.time() + 2 * api_tokens.MAX_CLOCK_SKEW_SECONDS + 1
    monkeypatch.setattr(api_tokens.time, "time", lambda: later)
    api_tokens._remember_nonce("perf-nonce-abcdefghij")
    assert "perf-nonce-0123456789" not in api_tokens._NONCES


def test_last_used_is_written_at_most_once_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[str] = []

    class Storage:
        def change(self, collection: str, key: str, fn: object) -> None:
            writes.append(key)

    monkeypatch.setattr(api_tokens, "storage", Storage)
    api_tokens._LAST_WRITTEN.pop("perf-token", None)
    for _ in range(5):
        api_tokens._mark_used("perf-token")
    assert writes == ["perf-token"]


def test_sign_in_throttle_keeps_only_failures() -> None:
    throttle = accounts.SignInThrottle()
    for number in range(1000):
        assert throttle.seconds_to_wait(f"10.0.{number // 250}.{number % 250}", f"nobody{number}") == 0
    assert throttle._failures == {}  # asking stores nothing
    for _ in range(throttle.MAX_PER_ACCOUNT_AND_ADDRESS):
        throttle.failed("10.9.9.9", "ada")
    assert throttle.seconds_to_wait("10.9.9.9", "ada") > 0
    throttle.WINDOW_SECONDS = 0
    throttle._swept = 0
    time.sleep(0.01)
    assert throttle.seconds_to_wait("10.9.9.9", "ada") == 0
    assert throttle._failures == {}
