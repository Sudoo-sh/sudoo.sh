"""The audit log (core/audit.py, web/v1/users.py): the project and branch of each entry, filters, sorting, paging,
and exports as CSV and XLSX (core/xlsx.py) with their limits, each export itself audit-logged."""

from __future__ import annotations

import csv
import io
import shutil
import time
import zipfile
from collections.abc import Iterator
from typing import Any
from xml.etree import ElementTree

import pytest

from sudoo import projects
from sudoo.config import live
from sudoo.core import audit, xlsx
from sudoo.instances import store
from sudoo.storage import storage

PROJECT, OTHER = "audit-erp", "audit-other"
MAIN, FEATURE = "auditmain", "auditfeat"
Client = Any


@pytest.fixture(autouse=True)
def project() -> Iterator[None]:
    for project_id in (PROJECT, OTHER):
        storage().put(projects.COLLECTION, project_id, {"id": project_id, "name": project_id.title(),
                                                        "repository": f"acme/{project_id}"})  # fmt: skip
    for name, branch in ((MAIN, "main"), (FEATURE, "feature/x")):
        store.folder(name).mkdir(parents=True, exist_ok=True)
        (store.folder(name) / "docker-compose.yml").write_text("services: {}\n")
        store.write_env(name, {"PROJECT": PROJECT, "GIT_BRANCH": branch, "STAGE": "development"})
    yield
    for project_id in (PROJECT, OTHER):
        storage().delete(projects.COLLECTION, project_id)
    for name in (MAIN, FEATURE):
        shutil.rmtree(store.folder(name), ignore_errors=True)
    live.change(dict.fromkeys(live.overrides()))


def _get(client: Client, path: str) -> Any:
    return client[0].get(f"/api/v1{path}")


def test_entries_name_their_project_and_branch() -> None:
    audit.record("ada", "10.0.0.1", "instance restarted", instance=MAIN)
    audit.record("ada", "10.0.0.1", "branch stage changed", detail=f"{PROJECT} feature/x: development -> staging")
    audit.record("ada", "10.0.0.1", "project changed", detail=f"{PROJECT}: name")
    audit.record("ada", "10.0.0.1", "branch unlocked", detail=f"{PROJECT} main (locked by bob)")
    audit.record("ada", "10.0.0.1", "account deleted", detail="nobody: not a project")
    first, second, third, fourth, fifth = reversed(audit.latest(5))
    assert (first["project"], first["branch"]) == (PROJECT, "main")
    assert (second["project"], second["branch"]) == (PROJECT, "feature/x")
    assert third["project"] == PROJECT and "branch" not in third
    assert (fourth["project"], fourth["branch"]) == (PROJECT, "main")
    assert "project" not in fifth


def test_the_creation_of_an_instance_names_its_project_and_branch() -> None:
    """The creation is recorded before the instance's .env exists: its job's plan names the project and branch."""
    from sudoo.core import jobs

    job = jobs.Job("auditnew", "Create instance", "create", "ada")
    job.details = {"project": PROJECT, "branch": "main"}
    jobs.JOBS[job.id] = job
    try:
        audit.record("ada", "10.0.0.1", "Create instance", instance="auditnew", detail="Odoo 18.0 community")
        created = audit.latest(1)[0]
        assert (created["project"], created["branch"]) == (PROJECT, "main")
    finally:
        jobs.JOBS.pop(job.id, None)


def test_filters_sorting_and_paging(admin: Client) -> None:
    since = int(time.time()) - 1
    for number in range(12):
        audit.record(f"user{number % 3}", "10.0.0.2", "paging test", ok=number % 4 != 0, detail=f"n{number:02d}")
    found = _get(admin, f"/audit?event=paging+test&since={since}&limit=5").get_json()
    assert found["meta"] == {"total": 12, "limit": 5, "offset": 0} and len(found["data"]) == 5
    assert found["data"][0]["detail"] == "n11"  # newest first
    page = _get(admin, f"/audit?event=paging+test&since={since}&limit=5&offset=10").get_json()["data"]
    assert [e["detail"] for e in page] == ["n01", "n00"]
    oldest = _get(admin, f"/audit?event=paging+test&since={since}&sort=ts&limit=1").get_json()["data"]
    assert oldest[0]["detail"] == "n00"
    by_user = _get(admin, f"/audit?event=paging+test&since={since}&sort=-user&limit=1").get_json()["data"]
    assert by_user[0]["user"] == "user2"
    refused = _get(admin, f"/audit?event=paging+test&since={since}&ok=refused").get_json()["meta"]["total"]
    assert refused == 3
    assert _get(admin, f"/audit?user=user1&event=paging+test&since={since}").get_json()["meta"]["total"] == 4
    assert _get(admin, f"/audit?q=N07&since={since}").get_json()["data"][0]["detail"] == "n07"
    assert _get(admin, "/audit?q=50%25").get_json()["meta"]["total"] == 0  # % is not a wildcard
    assert _get(admin, "/audit?sort=detail").status_code == 400
    assert _get(admin, "/audit?ok=maybe").status_code == 400
    assert _get(admin, "/audit?since=2026-13-01").status_code == 400
    assert _get(admin, "/audit?branch=main").status_code == 400  # a branch needs its project


def test_project_and_branch_filters(admin: Client) -> None:
    since = int(time.time()) - 1
    audit.record("ada", None, "scoped", instance=MAIN)
    audit.record("ada", None, "scoped", instance=FEATURE)
    audit.record("ada", None, "scoped", detail=f"{OTHER}: something")
    storage().log({"ts": int(time.time()), "user": "ada", "ip": None, "event": "scoped", "instance": FEATURE,
                   "ok": True})  # an entry of an earlier version, without project or branch  # fmt: skip
    assert _get(admin, f"/audit?event=scoped&since={since}&project={PROJECT}").get_json()["meta"]["total"] == 3
    branch = _get(admin, f"/audit?event=scoped&since={since}&project={PROJECT}&branch=feature/x").get_json()
    assert branch["meta"]["total"] == 2 and {e["instance"] for e in branch["data"]} == {FEATURE}
    assert _get(admin, f"/audit?event=scoped&since={since}&project={OTHER}").get_json()["meta"]["total"] == 1
    facets = _get(admin, "/audit/facets").get_json()["data"]
    assert "scoped" in facets["events"] and "ada" in facets["users"]
    assert {"id": PROJECT, "name": "Audit-Erp"} in facets["projects"]


def test_reading_needs_the_audit_permission(operator: Client) -> None:
    assert _get(operator, "/audit").status_code == 403
    assert _get(operator, "/audit/export").status_code == 403


def test_export_csv(admin: Client) -> None:
    since = int(time.time()) - 1
    audit.record("ada", "10.0.0.3", "csv test", instance=MAIN, detail='=HYPERLINK("http://x")')
    audit.record("ada", "10.0.0.3", "csv test", ok=False, detail="plain, with a comma")
    answer = admin[0].get(f"/api/v1/audit/export?format=csv&event=csv+test&since={since}")
    assert answer.status_code == 200 and answer.mimetype == "text/csv"
    assert answer.headers["X-Total-Count"] == "2" and answer.headers["X-Exported-Count"] == "2"
    assert "attachment" in answer.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(answer.get_data(as_text=True).lstrip("﻿"))))
    assert rows[0] == list(audit.COLUMNS)
    assert rows[1][2] == "csv test" and rows[1][3] == "no" and rows[1][8] == "plain, with a comma"
    assert rows[2][4:7] == [PROJECT, "main", MAIN] and rows[2][8].startswith("'=")  # never a formula
    exported = audit.latest(1)[0]
    assert exported["event"] == "audit log exported" and exported["detail"].startswith("csv, 2 of 2 entries")


def test_export_xlsx(admin: Client) -> None:
    since = int(time.time()) - 1
    audit.record("ada", "10.0.0.4", "xlsx test", detail="<b>&amp; \x01 العربية</b>")
    answer = admin[0].get(f"/api/v1/audit/export?format=xlsx&event=xlsx+test&since={since}")
    assert answer.status_code == 200 and answer.mimetype == audit_type()
    with zipfile.ZipFile(io.BytesIO(answer.get_data())) as archive:
        assert {"[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml"} <= set(archive.namelist())
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                ElementTree.fromstring(archive.read(name))  # every part is well-formed XML
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "<b>&amp;amp;  العربية</b>" not in sheet and "&lt;b&gt;&amp;amp;  العربية&lt;/b&gt;" in sheet
    assert 'r="I2"' in sheet and "xlsx test" in sheet


def audit_type() -> str:
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_export_limits(admin: Client) -> None:
    now = int(time.time())
    too_early = admin[0].get(f"/api/v1/audit/export?since={now - 400 * 86400}&until={now}")
    assert too_early.status_code == 400 and "366 days" in too_early.get_json()["detail"]
    for number in range(150):
        audit.record("ada", None, "limit test", detail=str(number))
    live.change({"audit_export_max_rows": 100})
    answer = admin[0].get(f"/api/v1/audit/export?event=limit+test&since={now - 5}")
    assert answer.headers["X-Total-Count"] == "150" and answer.headers["X-Exported-Count"] == "100"
    assert len(answer.get_data(as_text=True).strip().splitlines()) == 101


def test_xlsx_column_names() -> None:
    assert [xlsx.column_name(i) for i in (0, 25, 26, 51, 52, 701, 702)] == ["A", "Z", "AA", "AZ", "BA", "ZZ", "AAA"]
