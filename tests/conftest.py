"""Test setup: every test session gets its own empty ODOO_ROOT and documentation folder, so nothing touches a real
server, and the panel's background work (Docker, monitor, connectors) is never started."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROOT = Path(tempfile.mkdtemp(prefix="sudoo-test-"))
(ROOT / "manager").mkdir()
DOCS = ROOT / "documentation"
DOCS.mkdir()
shutil.copy(REPO / "README.md", DOCS)
shutil.copytree(REPO / "docs", DOCS / "docs")

# settings are read when sudoo.config is first imported, so the environment is set before that
os.environ.update(ODOO_ROOT=str(ROOT), DOCS_DIR=str(DOCS), ODOO_VERSIONS="18.0,19.0,20.0", ENTERPRISE_SOURCE="none")

from flask import Flask  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402

from sudoo import accounts, background  # noqa: E402

ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture(scope="session")
def app() -> Flask:
    background.start = lambda: None  # no Docker, monitor or connectors in tests
    from sudoo.web import create_app

    application = create_app()
    application.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    return application


@pytest.fixture(scope="session")
def passwords() -> dict[str, str]:
    """One administrator and one sync operator, created once."""
    return {"admin": accounts.create("admin1", "admin"), "operator": accounts.create("sync1", "operator")}


def _signed_in(app: Flask, name: str, password: str) -> Iterator[tuple[FlaskClient, dict[str, str]]]:
    client = app.test_client()
    assert client.post("/api/login", json={"username": name, "password": password}, headers=ORIGIN).status_code == 200
    headers = {**ORIGIN, "X-CSRF-Token": client.get("/api/me").get_json()["csrf"]}
    yield client, headers


@pytest.fixture
def admin(app: Flask, passwords: dict[str, str]) -> Iterator[tuple[FlaskClient, dict[str, str]]]:
    """A client signed in as administrator, and the headers a change needs (Origin, CSRF token)."""
    yield from _signed_in(app, "admin1", passwords["admin"])


@pytest.fixture
def operator(app: Flask, passwords: dict[str, str]) -> Iterator[tuple[FlaskClient, dict[str, str]]]:
    yield from _signed_in(app, "sync1", passwords["operator"])
