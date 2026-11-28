"""Several worker processes of the panel (PANEL_WORKERS) and the command line, with real processes and the real
storage: what decides correctness is shared, so it holds whichever process a request reaches.

Each test starts child processes on the same ODOO_ROOT (the test session's), waits until every child is ready, then
lets them go at the same moment. Single host only (as core/slots.py and core/shared.py)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from sudoo import accounts, invitations
from sudoo.config import live
from sudoo.core import jobs
from sudoo.core.state import COLLECTION as STATE_COLLECTION
from sudoo.core.state import STATE
from sudoo.instances import integrity, store
from sudoo.storage import storage

SRC = str(Path(jobs.__file__).resolve().parents[2])

APP = """
import json, sys, threading, time
from sudoo import background
from sudoo.web.v1 import stream
background.start = lambda: None
stream.start = lambda app: None
from sudoo.web import create_app
app = create_app()
app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
ORIGIN = {"Origin": "http://localhost"}

def signed_in(name, password):
    client = app.test_client()
    answer = client.post("/api/v1/session", json={"username": name, "password": password}, headers=ORIGIN)
    assert answer.status_code == 201, answer.get_json()
    return client, {**ORIGIN, "X-CSRF-Token": answer.get_json()["data"]["csrf_token"]}
"""


def child(code: str) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": SRC}
    return subprocess.Popen([sys.executable, "-u", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, env=env)  # fmt: skip


def together(code: str, count: int = 2, timeout: float = 120) -> list[list[str]]:
    """Start `count` children running `code`; each prints "ready" when it is, then waits for a line on its standard
    input; all are released at the same moment. Returns each child's further output lines."""
    procs = [child(code) for _ in range(count)]
    try:
        for proc in procs:
            assert proc.stdout is not None
            line = proc.stdout.readline().strip()
            assert line == "ready", line
        for proc in procs:
            assert proc.stdin is not None
            proc.stdin.write("go\n")
            proc.stdin.flush()
        outputs = []
        for proc in procs:
            out, _ = proc.communicate(timeout=timeout)
            assert proc.returncode == 0, out
            outputs.append([line for line in out.splitlines() if line.strip()])
        return outputs
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()


# ---------------------------------------------------------------- the sign-in limit


def test_wrong_passwords_sent_to_two_workers_are_limited_together() -> None:
    name = f"mw{uuid.uuid4().hex[:8]}"
    accounts.create(name, "viewer", "quartz-harbor-meadow-63")
    accounts.THROTTLE.clear()
    code = APP + f"""
print("ready", flush=True)
sys.stdin.readline()
codes = []
def guess(n):
    client = app.test_client()
    codes.append(client.post("/api/v1/session", json={{"username": "{name}", "password": "wrong-" + str(n)}},
                             headers=ORIGIN).status_code)
threads = [threading.Thread(target=guess, args=(n,)) for n in range(6)]
for t in threads: t.start()
for t in threads: t.join()
print(json.dumps(codes), flush=True)
"""
    codes = [c for out in together(code) for c in json.loads(out[-1])]
    accounts.THROTTLE.clear()
    assert len(codes) == 12
    assert codes.count(401) <= accounts.SignInThrottle.MAX_PER_ACCOUNT_AND_ADDRESS, codes
    assert codes.count(429) == 12 - codes.count(401)


# ---------------------------------------------------------------- jobs: one per request, one at a time, seen by all

INSTANCE = "mw-shop"


@pytest.fixture
def instance() -> Iterator[str]:
    store.folder(INSTANCE).mkdir(parents=True, exist_ok=True)
    (store.folder(INSTANCE) / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(INSTANCE, {"ODOO_VERSION": "18.0", "DB_NAME": "mw_shop", "ODOO_PORT": "18997",
                               "STAGE": "development", "CREATED_BY": "admin1", "GIT_BRANCH": "main",
                               "AUTO_UPDATE": "false", "AUTO_PAUSE": "false", "DELETE_AFTER_DAYS": "0"})  # fmt: skip
    yield INSTANCE
    shutil.rmtree(store.folder(INSTANCE), ignore_errors=True)
    STATE.remove(INSTANCE)
    integrity.forget(INSTANCE)


def test_the_same_action_sent_to_two_workers_is_one_job_seen_by_both(
    instance: str, passwords: dict[str, str], tmp_path: Path
) -> None:
    ran = tmp_path / "ran"
    code = APP + f"""
from sudoo.instances import operations
def backup(job, name, kind, comment):
    with open({str(ran)!r}, "a") as fh:
        fh.write(job.id + "\\n")
    time.sleep(3)
operations.backup = backup
client, headers = signed_in("admin1", {passwords["admin"]!r})
print("ready", flush=True)
sys.stdin.readline()
answer = client.post("/api/v1/instances/{instance}/actions/backup", json={{"comment": "go-live"}}, headers=headers)
job_id = answer.get_json()["data"]["id"]
time.sleep(0.5)
seen = client.get("/api/v1/jobs/" + job_id).status_code
listed = [j["id"] for j in client.get("/api/v1/jobs?instance={instance}").get_json()["data"]]
print(json.dumps([answer.status_code, job_id, seen, job_id in listed]), flush=True)
from sudoo.core import jobs
while jobs.unfinished("{instance}"):
    time.sleep(0.2)
"""
    results = [json.loads(out[-1]) for out in together(code)]
    assert [r[0] for r in results] == [202, 202]
    assert results[0][1] == results[1][1], results  # one job, whichever worker got the request
    assert all(r[2] == 200 and r[3] for r in results)  # each worker shows it
    assert len(ran.read_text().split()) == 1


def test_jobs_of_two_workers_on_one_instance_run_one_after_the_other(instance: str, tmp_path: Path) -> None:
    times = tmp_path / "times"
    code = f"""
import sys, time
from sudoo.core import jobs
def work(job):
    start = time.time()
    time.sleep(1)
    with open({str(times)!r}, "a") as fh:
        fh.write(f"{{start}} {{time.time()}}\\n")
print("ready", flush=True)
sys.stdin.readline()
job = jobs.start("{instance}", "Work", "restart", work)
time.sleep(0.2)
while job.status in ("queued", "running"):
    time.sleep(0.1)
"""
    together(code)
    spans = sorted(tuple(map(float, line.split())) for line in times.read_text().splitlines())
    assert len(spans) == 2
    assert spans[0][1] <= spans[1][0], spans  # never at the same time


def test_a_job_of_another_worker_counts_as_unfinished_and_one_of_a_dead_worker_does_not(instance: str) -> None:
    code = f"""
import sys, time
from sudoo.core import jobs
job = jobs.start("{instance}", "Work", "restart", lambda job: time.sleep(30))
time.sleep(0.3)
print(job.id, flush=True)
sys.stdin.readline()
"""
    proc = child(code)
    try:
        assert proc.stdout is not None
        job_id = proc.stdout.readline().strip()
        assert jobs.unfinished(instance) and jobs.busy(instance)
        assert jobs.find(job_id) is not None and jobs.find(job_id).status == "running"  # type: ignore[union-attr]
    finally:
        proc.kill()
        proc.wait()
    assert not jobs.busy(instance)  # its flock went with it
    assert not jobs.unfinished(instance)
    ended = jobs.find(job_id)
    assert ended is not None and ended.status == "failed"


# ---------------------------------------------------------------- shared records


def test_an_instances_env_changed_by_two_processes_keeps_both(instance: str) -> None:
    code = f"""
import sys
from sudoo.instances import store
print("ready", flush=True)
tag = sys.stdin.readline().strip()
import os
for n in range(30):
    store.update_env("{instance}", **{{f"P{{os.getpid()}}_{{n}}": "1"}})
print(os.getpid(), flush=True)
"""
    pids = [out[-1] for out in together(code)]
    env = store.read_env(instance)
    for pid in pids:
        assert all(env.get(f"P{pid}_{n}") == "1" for n in range(30)), pid


def test_instance_state_changed_by_two_processes_keeps_both(instance: str) -> None:
    code = f"""
import os, sys
from sudoo.core.state import STATE
print("ready", flush=True)
sys.stdin.readline()
for n in range(20):
    STATE.update("{instance}", **{{f"p{{os.getpid()}}_{{n}}": n}})
print(os.getpid(), flush=True)
"""
    pids = [out[-1] for out in together(code)]
    stored = storage().get(STATE_COLLECTION, instance) or {}
    for pid in pids:
        assert all(f"p{pid}_{n}" in stored for n in range(20)), pid
    assert all(f"p{pids[0]}_{n}" in STATE.get(instance) for n in range(20))  # this process sees them too


def test_settings_changed_in_two_processes_are_all_kept_and_seen_everywhere() -> None:
    code = """
import os, sys
from sudoo.config import live
print("ready", flush=True)
which = sys.stdin.readline() and os.getpid() % 2
for n in range(5):
    live.change({"max_instances": 100 + n} if which else {"max_instances_per_user": 200 + n})
print(os.getpid(), flush=True)
"""
    try:
        procs = [child(code.replace("os.getpid() % 2", str(n))) for n in (0, 1)]
        for proc in procs:
            assert proc.stdout is not None and proc.stdout.readline().strip() == "ready"
        for proc in procs:
            assert proc.stdin is not None
            proc.stdin.write("go\n")
            proc.stdin.flush()
        for proc in procs:
            proc.communicate(timeout=60)
            assert proc.returncode == 0
        saved = json.loads((live.env.manager_dir / "settings.json").read_text())
        assert saved["max_instances"] == 104 and saved["max_instances_per_user"] == 204
        live._reload()  # what this process does within a second of the file changing
        assert live.max_instances == 104 and live.max_instances_per_user == 204
    finally:
        live.change({"max_instances": None, "max_instances_per_user": None})


def test_ports_reserved_by_two_processes_at_once_never_overlap() -> None:
    code = """
import json, sys
from sudoo.instances import store
print("ready", flush=True)
sys.stdin.readline()
got = [store.reserve_ports({}) for _ in range(5)]
print(json.dumps([p for ports in got for p in ports.values()]), flush=True)
"""
    outputs = together(code)
    ports = [p for out in outputs for p in json.loads(out[-1])]
    try:
        assert len(ports) == len(set(ports)) == 20
    finally:
        store.release_ports({str(p): p for p in ports})


def test_one_time_records_are_shared_and_used_once() -> None:
    from sudoo.auth import codes
    from sudoo.webshell import tickets

    sent = codes.new(("mw-user", "sign-in"))
    token, _ = tickets.issue(user="mw-user", sid="s", instance="i", target="odoo", as_root=False, confirmed=False,
                             ip="127.0.0.1")  # fmt: skip
    code = f"""
import json, sys
from sudoo.auth import codes
from sudoo.webshell import tickets
print("ready", flush=True)
sys.stdin.readline()
right = codes.check(("mw-user", "sign-in"), {sent!r})[0]
taken = tickets.take({token!r}) is not None
print(json.dumps([right, taken]), flush=True)
"""
    results = [json.loads(out[-1]) for out in together(code, count=3)]
    assert sum(r[0] for r in results) == 1 and sum(r[1] for r in results) == 1, results


# ---------------------------------------------------------------- background work and the command line


def test_one_process_runs_the_background_work_and_another_takes_it_over(tmp_path: Path) -> None:
    ran = tmp_path / "leader"
    lead_file = f"leader-{uuid.uuid4().hex[:6]}.lock"
    code = f"""
import os, sys, time
from sudoo.core import shared
shared.LEADER_RETRY_SECONDS = 0.2
shared.LEADER_FILE = {lead_file!r}  # not the test session's own leadership
def work():
    with open({str(ran)!r}, "a") as fh:
        fh.write(str(os.getpid()) + "\\n")
print("ready", flush=True)
sys.stdin.readline()
shared.when_leader(work)
print(os.getpid(), flush=True)
time.sleep(1.5)
"""
    first, second = child(code), child(code.replace("time.sleep(1.5)", "time.sleep(4)"))
    try:
        for proc in (first, second):
            assert proc.stdout is not None and proc.stdout.readline().strip() == "ready"
        for proc in (first, second):
            assert proc.stdin is not None
            proc.stdin.write("go\n")
            proc.stdin.flush()
        first.wait(30)
        second.wait(30)
    finally:
        for proc in (first, second):
            if proc.poll() is None:
                proc.kill()
    leaders = ran.read_text().split()
    assert len(leaders) == 2 and leaders[0] != leaders[1]  # one at a time; the second after the first ended


def test_the_panel_and_the_command_line_never_send_a_heartbeat_at_the_same_time(tmp_path: Path) -> None:
    spans = tmp_path / "spans"
    code = f"""
import sys, time
from sudoo.hub import marketplace
def once():
    start = time.time()
    time.sleep(0.5)
    with open({str(spans)!r}, "a") as fh:
        fh.write(f"{{start}} {{time.time()}}\\n")
    return {{}}
marketplace._heartbeat_once = once
print("ready", flush=True)
sys.stdin.readline()
marketplace.heartbeat()
"""
    together(code, count=3)
    found = sorted(tuple(map(float, line.split())) for line in spans.read_text().splitlines())
    assert len(found) == 3
    assert all(a[1] <= b[0] for a, b in zip(found, found[1:], strict=False)), found


def test_the_command_line_leaves_commands_that_start_jobs_to_the_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo import plugin_manager
    from sudoo.hub import marketplace

    started: list[str] = []
    monkeypatch.setattr(plugin_manager, "start_command", lambda command_id, payload: started.append(command_id))
    monkeypatch.setattr(marketplace, "verify_command", lambda command, me, signed: True)
    monkeypatch.setattr(marketplace, "HANDS_JOBS_OVER", True)  # what the command line sets
    marketplace.carry_out([{"id": "cmd-mw", "kind": "remove_paid_plugins", "payload": {}}])
    assert started == []
    assert "cmd-mw" not in marketplace.commands_done()  # not acknowledged: the hub sends it again to the panel


# ---------------------------------------------------------------- invitations


def test_the_same_address_invited_by_two_processes_at_once_gets_one_invitation() -> None:
    address = f"mw{uuid.uuid4().hex[:6]}@example.com"
    code = f"""
import json, sys
from sudoo import invitations
print("ready", flush=True)
sys.stdin.readline()
try:
    found, _ = invitations.create({address!r}, "viewer", "admin1")
except invitations.AlreadyInvited as error:
    found = error.invitation_id
print(json.dumps(found), flush=True)
"""
    ids = [json.loads(out[-1]) for out in together(code, count=3)]
    try:
        assert len(set(ids)) == 1, ids
        assert len([i for i in invitations.all_invitations().values() if i["email"] == address]) == 1
    finally:
        for key, found in invitations.all_invitations().items():
            if found["email"] == address:
                invitations.revoke(key)


def test_shared_lock_excludes_another_process(tmp_path: Path) -> None:
    from sudoo.core import shared

    lock = shared.SharedLock(f"mw-{uuid.uuid4().hex[:6]}")
    code = f"""
from sudoo.core import shared
lock = shared.SharedLock({lock.name!r})
print(lock.acquire(blocking=False), flush=True)
"""
    with lock:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             env={**os.environ, "PYTHONPATH": SRC}, timeout=60, check=True)  # fmt: skip
        assert out.stdout.strip() == "False"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": SRC}, timeout=60, check=True)  # fmt: skip
    assert out.stdout.strip() == "True"
    assert time.time() > 0


def test_an_event_of_another_worker_reaches_the_stream_and_not_the_listeners_twice() -> None:
    from sudoo.core import event_bridge, events

    key = f"mw-{uuid.uuid4().hex[:6]}"
    heard: list[str] = []
    events.BUS.listen(frozenset({"instance"}), lambda event: heard.append(event.key))
    code = f"""
from sudoo.core import event_bridge, events
event_bridge.install()
events.publish("instance", {key!r}, {{"name": {key!r}}})
"""
    subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": SRC}, timeout=60, check=True)
    event_bridge.deliver_once()
    deadline = time.time() + 5
    while time.time() < deadline and not any(e.key == key for e in events.BUS._recent):
        time.sleep(0.05)
    found = [e for e in events.BUS._recent if e.key == key]
    assert found and found[-1].data == {"name": key}  # delivered to the stream's clients here
    assert key not in heard  # the listeners ran where it was published, not again here


def test_the_request_limit_counts_the_requests_of_every_worker() -> None:
    key = f"mw-rate-{uuid.uuid4().hex[:6]}"
    code = f"""
import json, sys
from sudoo.config import Settings
from sudoo.limits.memory import MemoryRateLimiter
limiter = MemoryRateLimiter(Settings(panel_workers=2))
print("ready", flush=True)
sys.stdin.readline()
print(json.dumps([limiter.hit({key!r}, 10).allowed for _ in range(8)]), flush=True)
"""
    allowed = [a for out in together(code) for a in json.loads(out[-1])]
    assert allowed.count(True) == 10, allowed  # 16 requests to two workers, 10 allowed in all
