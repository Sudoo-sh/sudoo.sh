"""Temporary access (projects/elevation.py) and the client portal (projects/clients.py) each keep their requests in
their own storage collection, and the one-time move of the old shared collection "access_requests"
(storage/migrate.py: separate_access_requests)."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from sudoo.config import live
from sudoo.projects import clients, elevation
from sudoo.storage import migrate, storage

OLD = migrate.OLD_REQUESTS
BACKUP = migrate.OLD_REQUESTS_BACKUP
NOW = time.time()


def _grant(key: str, *, age_days: float = 0) -> dict[str, object]:
    """An approved temporary-access grant, as elevation.py writes it."""
    now = NOW
    return {"id": key, "project": "p1", "user": "dev1", "stage": "staging", "level": "developer", "hours": 4.0,
            "reason": "Look at the failed import", "status": "approved", "asked": now - age_days * 86400,
            "decided_by": "admin1", "decided": now - age_days * 86400 + 5,
            "expires": now + 3600}  # fmt: skip


def _portal(key: str, *, status: str = "pending", at: float | None = None) -> dict[str, object]:
    """A client portal access request, as clients.py writes it."""
    return {"id": key, "project": "p1", "branch": "main", "by": "staff1", "reason": "Fix the invoice report",
            "hours": 2, "status": status, "at": int(at if at is not None else NOW)}  # fmt: skip


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    names = (OLD, BACKUP, elevation.COLLECTION, clients.REQUESTS)
    for name in names:
        for key in storage().all(name):
            storage().delete(name, key)
    elevation.forget_cache()
    yield
    for name in names:
        for key in storage().all(name):
            storage().delete(name, key)
    live.change(dict.fromkeys(live.overrides()))
    elevation.forget_cache()


def test_each_feature_has_its_own_collection() -> None:
    assert elevation.COLLECTION == "role_requests"
    assert clients.REQUESTS == "client_access_requests"
    assert len({elevation.COLLECTION, clients.REQUESTS, OLD}) == 3


def test_a_client_lapse_leaves_temporary_grants_alone() -> None:
    storage().put(elevation.COLLECTION, "grant", _grant("grant", age_days=180))
    storage().put(elevation.COLLECTION, "waiting", {**_grant("waiting"), "status": "pending"})
    long_ago = NOW - 40 * 86400
    storage().put(clients.REQUESTS, "old-pending", _portal("old-pending", at=long_ago))
    storage().put(clients.REQUESTS, "old-answered", {**_portal("old-answered", status="refused", at=long_ago),
                                                      "decided_at": int(long_ago)})  # fmt: skip
    elevation.forget_cache()

    clients.lapse(now=NOW + 400 * 86400)

    assert storage().get(elevation.COLLECTION, "grant") == _grant("grant", age_days=180)
    assert (storage().get(elevation.COLLECTION, "waiting") or {})["status"] == "pending"
    assert storage().get(clients.REQUESTS, "old-answered") is None  # the portal's own cleanup still works
    assert (storage().get(clients.REQUESTS, "old-pending") or {})["status"] == "lapsed"


def test_temporary_access_works_while_a_client_request_exists() -> None:
    storage().put(clients.REQUESTS, "c1", _portal("c1"))
    storage().put(elevation.COLLECTION, "g1", _grant("g1"))
    elevation.forget_cache()
    assert [r["id"] for r in elevation.of_project("p1")] == ["g1"]
    assert [r["id"] for r in clients.requests("p1")] == ["c1"]


def test_the_move_sorts_mixed_old_data_by_shape() -> None:
    storage().put(OLD, "g1", _grant("g1"))
    storage().put(OLD, "g2", {**_grant("g2"), "status": "pending", "expires": None})
    storage().put(OLD, "c1", _portal("c1"))
    storage().put(OLD, "c2", {**_portal("c2", status="approved"), "users": ["staff1"], "until": 1, "decided_at": 1})
    storage().put(OLD, "odd", {"id": "odd", "project": "p1", "status": "pending"})
    both = {**_grant("both"), **_portal("both")}
    storage().put(OLD, "both", both)

    moved = migrate.separate_access_requests(storage())

    assert moved == {"moved": 4, "left": 2, "backed_up": 6}
    assert set(storage().all(elevation.COLLECTION)) == {"g1", "g2"}
    assert set(storage().all(clients.REQUESTS)) == {"c1", "c2"}
    assert storage().get(elevation.COLLECTION, "g1") == _grant("g1")
    assert storage().get(clients.REQUESTS, "c2") == {**_portal("c2", status="approved"), "users": ["staff1"],
                                                      "until": 1, "decided_at": 1}  # fmt: skip
    assert set(storage().all(OLD)) == {"odd", "both"}  # a document that fits neither (or both) stays for a person
    assert set(storage().all(BACKUP)) == {"g1", "g2", "c1", "c2", "odd", "both"}  # the copy as it was


def test_the_move_twice_changes_nothing() -> None:
    storage().put(OLD, "g1", _grant("g1"))
    storage().put(OLD, "c1", _portal("c1"))
    migrate.separate_access_requests(storage())
    roles = storage().all(elevation.COLLECTION)
    portal = storage().all(clients.REQUESTS)
    assert (roles, portal) == ({"g1": roles["g1"]}, {"c1": portal["c1"]})

    again = migrate.separate_access_requests(storage())

    assert again == {"moved": 0, "left": 0, "backed_up": 0}
    assert storage().all(elevation.COLLECTION) == roles
    assert storage().all(clients.REQUESTS) == portal
    assert storage().all(OLD) == {}
    assert storage().all(BACKUP) == {}  # the copy lasts until the next start-up


def test_the_move_never_overwrites_a_document_already_in_place() -> None:
    newer = {**_grant("g1"), "status": "withdrawn"}
    storage().put(elevation.COLLECTION, "g1", newer)
    storage().put(OLD, "g1", _grant("g1"))
    migrate.separate_access_requests(storage())
    assert storage().get(elevation.COLLECTION, "g1") == newer
    assert storage().all(OLD) == {}


def test_nothing_to_move_means_nothing_written() -> None:
    assert migrate.separate_access_requests(storage()) == {"moved": 0, "left": 0, "backed_up": 0}
    assert storage().all(BACKUP) == {} and storage().all(OLD) == {}


def test_the_move_logs_what_it_moved(caplog: pytest.LogCaptureFixture) -> None:
    storage().put(OLD, "g1", _grant("g1"))
    with caplog.at_level("INFO", logger=migrate.log.name):
        migrate.separate_access_requests(storage())
    assert "Moved access request g1 from access_requests to role_requests" in caplog.text
    assert "1 moved, 0 left" in caplog.text
