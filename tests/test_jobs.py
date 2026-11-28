"""Running a job's commands: output is streamed into the log, exit codes are returned or raised as before, and a
step's timeout stops a command even when it writes nothing (with everything it started), so the instance's lock is
released. Addresses with a user name and password never reach a job's log. Docker is never run."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from sudoo.core import jobs
from sudoo.core.jobs import Job, JobError
from sudoo.core.shell import redact

NAME = "job-runner"


def _job() -> Job:
    return Job(NAME, "Test", "test", "admin1")


def _alive(pid: int) -> bool:
    """True while `pid` runs (a finished process waiting to be reaped does not count)."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def test_output_is_streamed_and_exit_codes_are_kept() -> None:
    job = _job()
    assert job.run_unchecked(["sh", "-c", "echo one; echo two; exit 3"], timeout=10) == 3
    assert "one\n" in job.log and "two\n" in job.log
    assert job.log[0].startswith("$ sh -c")
    assert job.run_unchecked(["cat"], input="given\n") == 0
    assert "given\n" in job.log
    job.run(["true"], timeout=10)
    with pytest.raises(JobError, match="command failed"):
        job.run(["false"])


def test_silent_command_is_stopped_at_its_timeout() -> None:
    begun = time.monotonic()
    with pytest.raises(JobError, match="took too long"):
        _job().run_unchecked(["sh", "-c", "sleep 5"], timeout=1)
    assert time.monotonic() - begun < 2.5


def test_timeout_holds_when_the_command_never_reads_its_input() -> None:
    begun = time.monotonic()
    with pytest.raises(JobError, match="took too long"):
        _job().run_unchecked(["sh", "-c", "sleep 5"], timeout=1, input="x" * (4 << 20))
    assert time.monotonic() - begun < 2.5


def test_timeout_stops_what_the_command_started(tmp_path: Path) -> None:
    pid_file = tmp_path / "child"
    with pytest.raises(JobError):
        _job().run_unchecked(["sh", "-c", f"sleep 30 & echo $! > {pid_file}; wait"], timeout=1)
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 2
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(child)


def test_silent_step_releases_the_instance_lock() -> None:
    def work(job: Job) -> None:
        job.run(["sh", "-c", "sleep 5"], timeout=1)

    job = jobs.start(NAME, "Upgrade", "update", work)
    deadline = time.monotonic() + 2.5
    while job.status in ("queued", "running"):
        assert time.monotonic() < deadline, "the step outlived its timeout"
        time.sleep(0.02)
    assert job.status == "failed"
    released = time.monotonic() + 2  # the end is recorded, then the lock is given back
    while jobs.busy(NAME) and time.monotonic() < released:
        time.sleep(0.02)
    assert not jobs.busy(NAME)
    assert NAME not in jobs.ACTIVE_JOB


def test_compose_run_gets_a_name_it_can_be_removed_by() -> None:
    cmd = ["docker", "compose", "--project-directory", "/x", "-f", "/x/c.yml", "-p", "odoo-a", "--env-file",
           "/dev/null", "run", "--rm", "-T", "odoo", "odoo", "-u", "all"]  # fmt: skip
    named, container = jobs._named_compose_run(cmd, "abc")
    assert container and container.startswith("sudoo-run-abc-")
    assert named[: cmd.index("run") + 3] == [*cmd[: cmd.index("run") + 1], "--name", container]
    assert named[cmd.index("run") + 3 :] == cmd[cmd.index("run") + 1 :]
    for other in (["docker", "compose", "-f", "/x/c.yml", "up", "-d"], ["git", "run"], ["docker", "run", "x"]):
        assert jobs._named_compose_run(other, "abc") == (other, None)


def test_addresses_with_credentials_are_masked_in_the_log() -> None:
    assert (
        redact("git clone -- https://user:secret@example.com/r.git /x")
        == "git clone -- https://***@example.com/r.git /x"
    )
    assert redact("https://token@example.com/r.git") == "https://***@example.com/r.git"
    assert (
        redact("git@github.com:acme/r.git and https://example.com/a@b")
        == "git@github.com:acme/r.git and https://example.com/a@b"
    )
    job = _job()
    job.run_unchecked(["echo", "https://user:secret@example.com/r.git"])
    assert "secret" not in "".join(job.log)
