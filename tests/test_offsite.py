"""Off-site copies (offsite/), against a fake S3 server in this process (fake_s3.py): uploading, the signed manifest,
the checks that fail closed (a changed file, a changed or moved manifest, a wrong key, no signature), retention,
restoring through the check, alerts and permissions. No real storage is ever contacted."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from fake_s3 import ACCESS_KEY, BUCKET, SECRET_KEY, FakeS3
from sudoo import accounts, alerts, offsite
from sudoo.config import live, settings
from sudoo.core import jobs
from sudoo.core.jobs import JobError
from sudoo.instances import odoo, operations, store
from sudoo.offsite import keys, s3
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]


@pytest.fixture(scope="module")
def fake() -> Iterator[FakeS3]:
    server = FakeS3()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def configured(fake: FakeS3, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The storage set up and switched on; jobs run at once, in the test's thread."""

    def run_now(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any) -> jobs.Job:
        job = jobs.Job(instance, action, kind, user)
        try:
            fn(job, *args)
            job.status = "success"
        except JobError as error:
            job.status = "failed"
            job.hint = str(error)
        return job

    monkeypatch.setattr(jobs, "start", run_now)
    monkeypatch.setattr(s3, "time", type("NoSleep", (), {"sleep": staticmethod(lambda s: None)}))
    fake.objects.clear()
    fake.refuse = None
    live.change(
        {
            "offsite_backups": True,
            "offsite_endpoint": fake.endpoint,
            "offsite_bucket": BUCKET,
            "offsite_prefix": "panel",
            "offsite_region": "us-east-1",
            "offsite_access_key_id": ACCESS_KEY,
            "offsite_secret_access_key": SECRET_KEY,
        }
    )
    yield
    live.change(dict.fromkeys(live.overrides()))
    for record in offsite.copies():
        storage().delete(offsite.COLLECTION, record["id"])
    for key in storage().all(offsite.META):
        storage().delete(offsite.META, key)
    shutil.rmtree(settings.backups_dir, ignore_errors=True)
    for name in ("shop", "lab"):
        shutil.rmtree(store.folder(name), ignore_errors=True)


def _instance(name: str, stage: str = "production") -> None:
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {"ODOO_VERSION": "18.0", "EDITION": "community", "STAGE": stage, "CREATED_AT": int(time.time() - 86400 * 60),
         "INSTANCE_ID": f"id-{name}"},
    )  # fmt: skip


def _backup(instance: str, kind: str = "daily", stamp: str = "20261117-000000", payload: bytes = b"") -> Any:
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    path = settings.backups_dir / f"{instance}__{instance}__{stamp}__{kind}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("dump.sql", b"-- dump\n" + payload)
        archive.writestr(
            "manifest.json", json.dumps({"db_name": instance, "version": "18.0", "instance_id": f"id-{instance}"})
        )
    taken = datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=UTC).timestamp()
    os.utime(path, (taken, taken))
    return path


def _copied(instance: str = "shop", kind: str = "daily", stamp: str = "20261117-000000") -> str:
    path = _backup(instance, kind, stamp)
    offsite.backup_created(instance, kind, [path])
    return offsite.copy_id_of(instance, stamp, kind)


# ---------------------------------------------------------------- signing requests


def test_signature_v4_matches_the_aws_example() -> None:
    """The GET Object example of the AWS documentation ("Signature Calculations for the Authorization Header")."""
    headers = s3.sign(
        "GET", "examplebucket.s3.amazonaws.com", "/test.txt", {}, {"Range": "bytes=0-9"}, s3.EMPTY_SHA256,
        "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
        datetime(2013, 5, 24, tzinfo=UTC),
    )  # fmt: skip
    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_connection_test_writes_reads_and_removes(fake: FakeS3) -> None:
    assert offsite.test_connection() == ["wrote a test file", "read it back", "listed the folder", "deleted it"]
    assert fake.objects == {}
    fake.refuse = "AccessDenied"
    with pytest.raises(s3.S3Error, match="may not do this"):
        offsite.test_connection()


# ---------------------------------------------------------------- copying


def test_a_production_backup_is_copied_with_a_signed_manifest(fake: FakeS3) -> None:
    _instance("shop")
    copy_id = _copied()
    record = offsite.get(copy_id)
    assert record and record["status"] == "stored" and record["location"] == f"{BUCKET}/panel"
    folder = "panel/shop/20261117-000000__daily/"
    zip_name = "shop__shop__20261117-000000__daily.zip"
    assert set(fake.objects) == {folder + zip_name, folder + "manifest.json", folder + "manifest.json.sig"}
    manifest = fake.objects[folder + "manifest.json"]
    content = json.loads(manifest)
    data = (settings.backups_dir / zip_name).read_bytes()
    assert content["files"] == [
        {"name": zip_name, "db": "shop", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    ]
    assert content["instance"] == "shop" and content["instance_id"] == "id-shop" and content["stage"] == "production"
    assert content["format"] == offsite.FORMAT and content["kind"] == "daily"
    pem = keys.public_pem()
    assert pem and content["signed_by"] == keys.fingerprint(pem)
    assert keys.verify(manifest, fake.objects[folder + "manifest.json.sig"], pem)
    # the manifest is written last: a copy without it is not complete
    puts = [path for method, path in fake.requests if method == "PUT"]
    assert puts[-1].endswith("manifest.json")


def test_which_backups_are_copied(fake: FakeS3) -> None:
    _instance("shop")
    _instance("lab", stage="staging")
    _copied("lab")
    assert offsite.copies() == []  # other stages only with OFFSITE_OTHER_STAGES
    _copied("shop", kind="before-update")
    assert offsite.copies() == []  # not in OFFSITE_KINDS
    live.change({"offsite_other_stages": True, "offsite_kinds": "daily,before-update"})
    _copied("lab")
    _copied("shop", kind="before-update")
    assert {c["id"] for c in offsite.copies()} == {
        "lab__20261117-000000__daily",
        "shop__20261117-000000__before-update",
    }
    live.change({"offsite_backups": False})
    _copied("shop", stamp="20261118-000000")
    assert len(offsite.copies()) == 2


def test_large_files_go_in_parts(fake: FakeS3, monkeypatch: pytest.MonkeyPatch) -> None:
    _instance("shop")
    monkeypatch.setattr(s3, "PART_SIZE", 1024)
    path = _backup("shop", payload=os.urandom(5000))
    offsite.backup_created("shop", "daily", [path])
    stored = fake.objects["panel/shop/20261117-000000__daily/" + path.name]
    assert stored == path.read_bytes() and not fake.uploads
    assert sum(1 for method, p in fake.requests if method == "PUT" and p.endswith(path.name)) >= 5


def test_a_failed_copy_is_recorded_alerted_and_leaves_nothing(fake: FakeS3) -> None:
    _instance("shop")
    fake.refuse = "AccessDenied"
    copy_id = _copied()
    record = offsite.get(copy_id)
    assert record and record["status"] == "failed" and "may not do this" in record["reason"]
    alerts.drain()
    sent = [a for a in alerts.recent() if a["type"] == "offsite_failed" and a["instance"] == "shop"]
    assert sent and sent[0]["severity"] == "problem"
    assert alerts.template(sent[0]) == "offsite_failed"
    fake.refuse = None
    assert fake.objects == {}
    offsite.retry(copy_id, "admin1")
    assert offsite.get(copy_id)["status"] == "stored"  # type: ignore[index]
    alerts.drain()
    assert any(a["type"] == "offsite_failed" and a["severity"] == "recovered" for a in alerts.recent())


# ---------------------------------------------------------------- checking: fails closed


def _check(copy_id: str, public_pem: str | None = None) -> dict[str, Any]:
    offsite.start_verify(copy_id, public_pem, "admin1")
    return offsite.get(copy_id) or {}


def test_an_intact_copy_verifies() -> None:
    _instance("shop")
    copy_id = _copied()
    record = _check(copy_id)
    assert record["status"] == "verified" and record["verified_with"] == keys.fingerprint(keys.public_pem() or "")
    assert not list((settings.backups_dir / ".offsite").iterdir())  # the download is removed


def test_a_changed_file_fails_the_check(fake: FakeS3) -> None:
    _instance("shop")
    copy_id = _copied()
    key = "panel/shop/20261117-000000__daily/shop__shop__20261117-000000__daily.zip"
    fake.objects[key] = fake.objects[key][:-1] + b"X"
    record = _check(copy_id)
    assert record["status"] == "failed" and "SHA-256" in record["reason"]


def test_a_changed_manifest_fails_the_check(fake: FakeS3) -> None:
    _instance("shop")
    copy_id = _copied()
    key = "panel/shop/20261117-000000__daily/manifest.json"
    fake.objects[key] = fake.objects[key].replace(b'"daily"', b'"manual"')
    record = _check(copy_id)
    assert record["status"] == "failed" and "signature" in record["reason"]


def test_a_missing_signature_fails_the_check(fake: FakeS3) -> None:
    _instance("shop")
    copy_id = _copied()
    del fake.objects["panel/shop/20261117-000000__daily/manifest.json.sig"]
    record = _check(copy_id)
    assert record["status"] == "failed" and "no signature" in record["reason"]


def test_another_key_fails_the_check_and_the_right_one_passes() -> None:
    _instance("shop")
    copy_id = _copied()
    other = keys.public_pem_of(Ed25519PrivateKey.generate().public_key())
    record = _check(copy_id, other)
    assert record["status"] == "failed" and keys.fingerprint(other) in record["reason"]
    # the key kept off the server (the same as the panel's here) passes
    record = _check(copy_id, keys.public_pem())
    assert record["status"] == "verified"


def test_a_signed_copy_moved_to_another_place_fails(fake: FakeS3) -> None:
    _instance("shop")
    _copied()
    for key in list(fake.objects):
        fake.objects[key.replace("20261117-000000", "20261118-000000")] = fake.objects[key]
    offsite.scan()
    moved = "shop__20261118-000000__daily"
    record = _check(moved)
    assert record["status"] == "failed" and "another copy" in record["reason"]


def test_the_bucket_list_brings_back_unknown_copies_and_notices_missing_ones(fake: FakeS3) -> None:
    _instance("shop")
    first = _copied()
    second = _copied(stamp="20261118-000000")
    storage().delete(offsite.COLLECTION, first)  # as on a rebuilt server
    for key in [k for k in fake.objects if "20261118" in k]:
        del fake.objects[key]
    assert offsite.scan() == {"found": 1, "missing": 1}
    assert offsite.get(first)["found_in_bucket"] and offsite.get(first)["status"] == "stored"  # type: ignore[index]
    assert offsite.get(second)["status"] == "failed"  # type: ignore[index]
    assert _check(first)["status"] == "verified"


# ---------------------------------------------------------------- restoring


def test_restore_checks_first_then_uses_the_panel_restore(fake: FakeS3, monkeypatch: pytest.MonkeyPatch) -> None:
    _instance("shop")
    copy_id = _copied()
    local = settings.backups_dir / "shop__shop__20261117-000000__daily.zip"
    original = local.read_bytes()
    local.unlink()  # the server lost it
    done: list[tuple[str, str]] = []
    monkeypatch.setattr(odoo, "databases", lambda name: ["shop"])
    monkeypatch.setattr(operations, "restore", lambda job, name, file_name: done.append((name, file_name)))
    job = jobs.Job("shop", "Restore an off-site copy", "restore", "admin1")
    offsite.restore(job, "shop", copy_id)
    assert done == [("shop", local.name)]
    assert local.read_bytes() == original
    assert abs(local.stat().st_mtime - datetime(2026, 11, 17, tzinfo=UTC).timestamp()) < 1  # its own time
    assert offsite.get(copy_id)["status"] == "verified"  # type: ignore[index]

    # a changed copy restores nothing
    done.clear()
    local.unlink()
    key = f"panel/shop/20261117-000000__daily/{local.name}"
    fake.objects[key] = b"not the backup"
    with pytest.raises(JobError, match="Nothing was restored"):
        offsite.restore(jobs.Job("shop", "Restore", "restore", "admin1"), "shop", copy_id)
    assert done == [] and not local.exists()
    assert offsite.get(copy_id)["status"] == "failed"  # type: ignore[index]


# ---------------------------------------------------------------- retention


def _record(copy_id: str, created: float, status: str = "stored", stage: str = "production") -> dict[str, Any]:
    instance, stamp, kind = copy_id.split("__")
    record = {"id": copy_id, "instance": instance, "kind": kind, "stamp": stamp, "created": created,
              "status": status, "stage": stage, "files": [], "location": offsite.location()}  # fmt: skip
    storage().put(offsite.COLLECTION, copy_id, record)
    return record


def test_retention_keeps_days_and_months_and_the_newest_verified() -> None:
    live.change({"offsite_keep_daily_production": 3, "offsite_keep_monthly_production": 2})
    day = 86400
    start = datetime(2026, 9, 1, 12, tzinfo=UTC).timestamp()
    records = [_record(f"old__2026{i:04d}-000000__daily", start + i * day) for i in range(0, 60)]
    records.append(_record("old__20261231-000000__manual", start + 59 * day + 3600))  # same day as the newest
    kept = {r["id"] for r in records} - {r["id"] for r in offsite.to_prune(records)}
    # 3 days: the newest of 30 October (the manual one), 29 and 28 October; 2 months: October (the same manual one)
    # and September (its last day, 30 September)
    assert kept == {"old__20261231-000000__manual", records[58]["id"], records[57]["id"], records[29]["id"]}
    # never the newest verified copy, however old
    records[0]["status"] = "verified"
    assert records[0]["id"] not in {r["id"] for r in offsite.to_prune(records)}
    # failed copies and those being uploaded are not touched
    records[1]["status"] = "failed"
    records[2]["status"] = "uploading"
    assert not {records[1]["id"], records[2]["id"]} & {r["id"] for r in offsite.to_prune(records)}


def test_pruning_removes_from_the_bucket_and_audits(fake: FakeS3) -> None:
    _instance("lab", stage="staging")
    live.change({"offsite_other_stages": True, "offsite_keep_daily_other": 1})
    first = _copied("lab", stamp="20261116-000000")
    second = _copied("lab", stamp="20261117-000000")  # its upload applies the rules
    assert offsite.get(first) is None and offsite.get(second)
    assert not [k for k in fake.objects if "20261116" in k]
    entries = storage().logs(limit=20)
    assert any("retention" in str(e.get("event")) and first in str(e.get("detail")) for e in entries)


# ---------------------------------------------------------------- alerts


def test_production_without_a_recent_copy_is_alerted() -> None:
    _instance("shop")
    now = time.time()
    storage().put(offsite.META, "enabled", {"at": now - 100 * 3600})
    assert offsite.check_missing(now) == ["shop"]
    alerts.drain()
    alert = next(a for a in alerts.recent() if a["type"] == "offsite_missing")
    assert alerts.template(alert) == "offsite_missing" and "26" in alert["title"]
    _record("shop__20261118-000000__daily", now - 3600)
    storage().put(
        offsite.COLLECTION,
        "shop__20261118-000000__daily",
        {**offsite.get("shop__20261118-000000__daily"), "uploaded_at": now - 3600},
    )  # type: ignore[dict-item]
    assert offsite.check_missing(now) == []


def test_the_emails_are_on_the_templates_page(admin: Client) -> None:
    client, _ = admin
    listed = {t["key"] for t in client.get("/api/v1/mail-templates").get_json()["data"]}
    assert {"offsite_failed", "offsite_missing"} <= listed


# ---------------------------------------------------------------- the API and permissions


@pytest.fixture(scope="module")
def administrator(app: Flask) -> Client:
    password = "granite-orchard-signal-73"
    accounts.create("offadmin", "admin", password)
    return sign_in(app, "offadmin", password)


@pytest.fixture(scope="module")
def maintainer(app: Flask) -> Client:
    password = "saffron-tunnel-meadow-26"
    accounts.create("offmaint", "maintainer", password)
    return sign_in(app, "offmaint", password)


def test_permissions(admin: Client, administrator: Client, maintainer: Client) -> None:
    _instance("shop")
    copy_id = _copied()
    m_client, m_headers = maintainer
    assert m_client.get("/api/v1/offsite/copies").status_code == 403
    assert m_client.post(f"/api/v1/offsite/copies/{copy_id}/restore", json={}, headers=m_headers).status_code == 403

    a_client, a_headers = administrator
    listed = a_client.get("/api/v1/offsite/copies").get_json()["data"]
    assert listed[0]["id"] == copy_id and listed[0]["status"] == "stored" and listed[0]["size"] > 0
    assert a_client.post("/api/v1/offsite/test", headers=a_headers).status_code == 403
    assert a_client.patch("/api/v1/settings", json={"offsite_bucket": "other"}, headers=a_headers).status_code == 403
    state = a_client.get("/api/v1/offsite").get_json()["data"]
    assert state["active"] and state["fingerprint"].startswith("SHA256:") and "PUBLIC KEY" in state["public_key"]
    assert SECRET_KEY not in json.dumps(state)
    answer = a_client.get("/api/v1/offsite/public-key")
    assert answer.status_code == 200 and b"BEGIN PUBLIC KEY" in answer.data

    s_client, s_headers = admin
    test = s_client.post("/api/v1/offsite/test", headers=s_headers)
    assert test.status_code == 200 and test.get_json()["data"]["location"] == f"{BUCKET}/panel"
    settings_list = {s["key"]: s for s in s_client.get("/api/v1/settings").get_json()["data"]}
    assert settings_list["offsite_secret_access_key"]["value"] is True  # set, never shown


def test_restore_and_delete_through_the_api(admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _instance("shop")
    first = _copied(stamp="20261116-000000")
    copy_id = _copied()
    client, headers = admin
    answer = client.post(f"/api/v1/offsite/copies/{copy_id}/restore", json={}, headers=headers)
    assert answer.status_code == 400 and "production" in answer.get_json()["detail"]
    answer = client.post(f"/api/v1/offsite/copies/{copy_id}/restore", json={"public_key": "nope"}, headers=headers)
    assert answer.status_code == 400
    answer = client.post(f"/api/v1/offsite/copies/{copy_id}/restore", json={"instance": "nothere"}, headers=headers)
    assert answer.status_code == 404

    called: list[Any] = []
    monkeypatch.setattr(offsite, "restore", lambda job, name, cid, pem: called.append((name, cid, pem)))
    answer = client.post(
        f"/api/v1/offsite/copies/{copy_id}/restore", json={"confirm_production": True}, headers=headers
    )
    assert answer.status_code == 202 and called == [("shop", copy_id, None)]

    # the newest good copy is kept; an older one can go
    assert client.delete(f"/api/v1/offsite/copies/{copy_id}", headers=headers).status_code == 409
    assert client.delete(f"/api/v1/offsite/copies/{first}", headers=headers).status_code == 204
    assert offsite.get(first) is None
    answer = client.post(f"/api/v1/offsite/copies/{copy_id}/verify", json={}, headers=headers)
    assert answer.status_code == 202 and offsite.get(copy_id)["status"] == "verified"  # type: ignore[index]
    assert client.get("/api/v1/offsite/copies/..%2F/verify").status_code in (404, 405)


def test_settings_are_checked() -> None:
    for values in (
        {"offsite_endpoint": "http://s3.example.com"},
        {"offsite_endpoint": "https://s3.example.com/path"},
        {"offsite_bucket": "Bad_Bucket"},
        {"offsite_prefix": "../up"},
        {"offsite_kinds": "daily,weekly"},
    ):
        with pytest.raises(ValueError):
            live.change(values)
