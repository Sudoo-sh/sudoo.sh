"""Import database: uploads in pieces (resumable, size limit, per person), the checks of the file (zip slip,
dump.sql, Odoo version from the manifest or from the dump itself), permissions and production confirmation. Jobs are
recorded instead of run; Docker is never called."""

from __future__ import annotations

import gzip
import io
import json
import shutil
import stat
import time
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from sudoo import accounts
from sudoo.config import live, settings
from sudoo.core import audit, jobs
from sudoo.core.state import STATE
from sudoo.instances import backups, imports, store

Client = tuple[FlaskClient, dict[str, str]]
PIECE = {"Content-Type": "application/octet-stream"}
SQL = (
    "SET client_encoding = 'UTF8';\n"
    "COPY public.ir_module_module (id, name, state, latest_version) FROM stdin;\n"
    "1\tweb\tinstalled\t18.0.1.0\n"
    "2\tbase\tinstalled\t18.0.1.3\n"
    "\\.\n"
)


def _instance(name: str, stage: str = "development", version: str = "18.0") -> None:
    folder = store.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {"ODOO_VERSION": version, "DB_NAME": name.replace("-", "_"), "STAGE": stage, "CREATED_AT": int(time.time())},
    )


def _zip(manifest: dict[str, Any] | None = None, extra: dict[str, bytes] | None = None, dump: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if dump:
            archive.writestr("dump.sql", SQL)
        archive.writestr("filestore/ab/abcdef", b"file")
        if manifest is not None:
            archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def setup() -> Iterator[None]:
    _instance("imp")
    _instance("impprod", "production")
    yield
    shutil.rmtree(settings.backups_dir, ignore_errors=True)
    for name in ("imp", "impprod"):
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, tuple[Any, ...]]]:
    calls: list[tuple[Any, tuple[Any, ...]]] = []

    def fake_start(instance: str, action: str, kind: str, fn: Any, *args: Any, user: str = "system", **_: Any) -> Any:
        calls.append((fn, args))
        return jobs.Job(instance, action, kind, user)

    monkeypatch.setattr(jobs, "start", fake_start)
    return calls


def _upload(client: FlaskClient, headers: dict[str, str], data: bytes, file_name: str, name: str = "imp") -> str:
    answer = client.post(f"/api/v1/instances/{name}/imports", json={"file_name": file_name, "size": len(data)},
                         headers=headers)  # fmt: skip
    assert answer.status_code == 201, answer.get_json()
    upload = answer.get_json()["data"]
    offset = 0
    while offset < len(data):
        piece = data[offset : offset + 7]
        put = client.put(f"/api/v1/instances/{name}/imports/{upload['id']}?offset={offset}", data=piece,
                         headers={**headers, **PIECE})  # fmt: skip
        assert put.status_code == 200, put.get_json()
        offset += len(piece)
    return str(upload["id"])


# ---------------------------------------------------------------- checking files


def test_zip_slip_and_links_are_refused(tmp_path: Any) -> None:
    for bad in ("../evil", "/etc/passwd", "filestore/../../x", "C:/x", "a\\b"):
        assert imports.unsafe_member(zipfile.ZipInfo(bad))
    link = zipfile.ZipInfo("filestore/link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert imports.unsafe_member(link)
    assert not imports.unsafe_member(zipfile.ZipInfo("filestore/ab/abcdef"))
    path = tmp_path / "slip.zip"
    path.write_bytes(_zip({"version": "18.0"}, {"../../etc/cron.d/x": b"* * * * * root sh"}))
    with pytest.raises(imports.ImportProblem, match="unsafe path"):
        imports.check_zip(path, "18.0")


def test_zip_needs_dump_and_a_valid_archive(tmp_path: Any) -> None:
    path = tmp_path / "x.zip"
    path.write_bytes(_zip({"version": "18.0"}, dump=False))
    with pytest.raises(imports.ImportProblem, match="no dump.sql"):
        imports.check_zip(path, "18.0")
    path.write_bytes(b"not a zip")
    with pytest.raises(imports.ImportProblem, match="not a valid zip"):
        imports.check_zip(path, "18.0")


def test_major_version_mismatch_is_refused(tmp_path: Any) -> None:
    path = tmp_path / "x.zip"
    path.write_bytes(_zip({"version": "17.0"}))
    with pytest.raises(imports.ImportProblem, match="Odoo 17.0.*Odoo 18.0.*upgrade"):
        imports.check_zip(path, "18.0")
    path.write_bytes(_zip({"version": "saas~18.2"}))
    assert imports.check_zip(path, "18.0") == {"version": "saas~18.2"}  # same major version
    path.write_bytes(_zip(None))
    assert imports.check_zip(path, "18.0") == {"version": None}  # read from the dump in the job


def test_version_in_sql() -> None:
    assert imports.version_in_sql(SQL.splitlines(keepends=True)) == "18.0.1.3"
    assert imports.version_in_sql(["SELECT 1;\n"]) is None
    other = SQL.replace("(id, name, state, latest_version)", "(id, state, name, latest_version)")
    other = other.replace("2\tbase\tinstalled", "2\tinstalled\tbase")
    assert imports.version_in_sql(other.splitlines(keepends=True)) == "18.0.1.3"


def test_dump_version_of_sql_files(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imports, "data_path", lambda upload_id: tmp_path / upload_id)
    (tmp_path / "a").write_bytes(gzip.compress(SQL.encode()))
    (tmp_path / "b").write_bytes(SQL.encode())
    (tmp_path / "c").write_bytes(_zip(None))
    assert imports.dump_version("imp", {"id": "a", "format": "sql.gz"}) == "18.0.1.3"
    assert imports.dump_version("imp", {"id": "b", "format": "sql"}) == "18.0.1.3"
    assert imports.dump_version("imp", {"id": "c", "format": "zip"}) == "18.0.1.3"


def test_format_of() -> None:
    assert imports.format_of("Prod.SQL.GZ") == "sql.gz" and imports.format_of("a.dump") == "dump"
    assert imports.format_of("a.zip") == "zip" and imports.format_of("a.tar") is None


# ---------------------------------------------------------------- uploading


def test_upload_in_pieces_and_import(admin: Client, started: list[tuple[Any, tuple[Any, ...]]]) -> None:
    client, headers = admin
    upload = _upload(client, headers, _zip({"version": "18.0"}), "prod.zip")
    status = client.get(f"/api/v1/instances/imp/imports/{upload}").get_json()["data"]
    assert status["complete"] and status["format"] == "zip"
    answer = client.post(f"/api/v1/instances/imp/imports/{upload}/restore", json={}, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    fn, args = started[-1]
    assert fn is imports.import_database and args == ("imp", upload, "admin1", True)  # neutralised by default
    assert audit.latest(1, instance="imp")[0]["event"] == "database import started"


def test_neutralise_can_be_unticked(admin: Client, started: list[tuple[Any, tuple[Any, ...]]]) -> None:
    client, headers = admin
    upload = _upload(client, headers, _zip({"version": "18.0"}), "prod.zip")
    client.post(f"/api/v1/instances/imp/imports/{upload}/restore", json={"neutralize": False}, headers=headers)
    assert started[-1][1][-1] is False


def test_a_piece_at_the_wrong_place_says_where_to_continue(admin: Client) -> None:
    client, headers = admin
    answer = client.post("/api/v1/instances/imp/imports", json={"file_name": "a.sql", "size": 10}, headers=headers)
    upload = answer.get_json()["data"]["id"]
    client.put(f"/api/v1/instances/imp/imports/{upload}?offset=0", data=b"12345", headers={**headers, **PIECE})
    wrong = client.put(f"/api/v1/instances/imp/imports/{upload}?offset=0", data=b"12345", headers={**headers, **PIECE})
    assert wrong.status_code == 409 and wrong.get_json()["meta"]["received"] == 5
    too_much = client.put(f"/api/v1/instances/imp/imports/{upload}?offset=5", data=b"123456",
                          headers={**headers, **PIECE})  # fmt: skip
    assert too_much.status_code == 409
    assert client.put(f"/api/v1/instances/imp/imports/{upload}?offset=5", data=b"12345",
                      headers={**headers, "Content-Type": "application/json"}).status_code == 415  # fmt: skip


def test_size_limit_and_file_types(admin: Client) -> None:
    client, headers = admin
    live.change({"import_max_gb": 0.000001})
    big = client.post("/api/v1/instances/imp/imports", json={"file_name": "a.zip", "size": 5000}, headers=headers)
    assert big.status_code == 413
    live.change({"import_max_gb": 20})
    wrong = client.post("/api/v1/instances/imp/imports", json={"file_name": "a.exe", "size": 5}, headers=headers)
    assert wrong.status_code == 400


def test_an_upload_is_its_starters_only(admin: Client, app: Flask) -> None:
    client, headers = admin
    upload = _upload(client, headers, b"PGDMP-custom", "a.dump")
    password = "sunlit-quarry-mosaic-64"
    accounts.create("admin7", "admin", password)
    other, other_headers = sign_in(app, "admin7", password)
    assert other.get(f"/api/v1/instances/imp/imports/{upload}").status_code == 404
    assert (
        other.post(f"/api/v1/instances/imp/imports/{upload}/restore", json={}, headers=other_headers).status_code == 404
    )


def test_version_mismatch_refused_before_the_job(admin: Client, started: list[tuple[Any, tuple[Any, ...]]]) -> None:
    client, headers = admin
    upload = _upload(client, headers, _zip({"version": "16.0"}), "old.zip")
    answer = client.post(f"/api/v1/instances/imp/imports/{upload}/restore", json={}, headers=headers)
    assert answer.status_code == 400 and "Odoo 16.0" in answer.get_json()["detail"]
    assert not started


def test_production_needs_typed_confirmation(admin: Client, started: list[tuple[Any, tuple[Any, ...]]]) -> None:
    client, headers = admin
    upload = _upload(client, headers, _zip({"version": "18.0"}), "prod.zip", "impprod")
    path = f"/api/v1/instances/impprod/imports/{upload}/restore"
    assert client.post(path, json={"confirm_production": True}, headers=headers).status_code == 400
    assert not started
    answer = client.post(path, json={"confirm_production": True, "confirm_stage": "production"}, headers=headers)
    assert answer.status_code == 202
    assert started[-1][1][-1] is False  # production data is not neutralised unless asked


def test_developers_cannot_import(operator: Client) -> None:
    client, headers = operator
    answer = client.post("/api/v1/instances/imp/imports", json={"file_name": "a.zip", "size": 5}, headers=headers)
    assert answer.status_code == 403


def test_pieces_are_the_only_binary_requests(admin: Client) -> None:
    client, headers = admin
    refused = client.post("/api/v1/instances/imp/imports", data=b"x", headers={**headers, **PIECE})
    assert refused.status_code == 415


def test_stale_uploads_are_removed() -> None:
    meta = imports.start("imp", "admin1", "a.sql", 10)
    imports.remove_stale(now=time.time() + imports.UPLOAD_HOURS * 3600 + 60)
    assert imports.get(meta["id"], "imp", "admin1") is None


# ---------------------------------------------------------------- backups' revision and comment


def test_backup_comment_and_revision_from_the_manifest() -> None:
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    name = "imp__imp__20261120-000000__manual.zip"
    with zipfile.ZipFile(settings.backups_dir / name, "w") as archive:
        archive.writestr("dump.sql", "")
        archive.writestr(
            "manifest.json", json.dumps({"version": "18.0", "revision": "a" * 40, "comment": "before go-live"})
        )
    found = backups.details(name)
    assert found["revision"] == "a" * 40 and found["comment"] == "before go-live"


def test_backup_action_takes_a_comment(admin: Client, started: list[tuple[Any, tuple[Any, ...]]]) -> None:
    client, headers = admin
    answer = client.post("/api/v1/instances/imp/actions/backup", json={"comment": "before go-live"}, headers=headers)
    assert answer.status_code == 202, answer.get_json()
    assert started[-1][1] == ("imp", "manual", "before go-live")
