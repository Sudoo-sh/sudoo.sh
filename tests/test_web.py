"""The panel's HTTP API: request rules, input checks, roles and the documentation pages."""

from __future__ import annotations

import re

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import ORIGIN

Client = tuple[FlaskClient, dict[str, str]]


def test_changes_must_be_json(app: Flask) -> None:
    response = app.test_client().post("/api/login", data={"username": "a", "password": "b"}, headers=ORIGIN)
    assert response.status_code == 415


def test_changes_from_another_site_are_refused(app: Flask) -> None:
    response = app.test_client().post("/api/login", json={}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_csrf_token_is_required(admin: Client) -> None:
    client, _ = admin
    assert client.post("/api/users", json={"name": "bob", "role": "operator"}, headers=ORIGIN).status_code == 403


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"branch": "--upload-pack=x"}, "branch name is not valid"),
        ({"repo": "https://evil.example/a/b"}, "not a GitHub repository"),
        ({"version": "99.0"}, "Odoo version"),
        ({"demo": "yes"}, "true or false"),
        ({"name": "x\u0000y"}, "not allowed"),
        ({"delete_after_days": "30"}, "whole number of days"),
    ],
)
def test_create_refuses_bad_input(admin: Client, body: dict[str, object], message: str) -> None:
    client, headers = admin
    valid = {"name": "x", "version": "20.0", "edition": "community", "delete_after_days": 30}
    response = client.post("/api/instances", json={**valid, **body}, headers=headers)
    assert response.status_code == 400 and message in response.get_json()["error"]


def test_versions_and_editions(admin: Client) -> None:
    client, _ = admin
    assert client.get("/api/versions").get_json() == [
        {"version": v, "enterprise": False} for v in ("18.0", "19.0", "20.0")  # ENTERPRISE_SOURCE=none
    ]


def test_operator_cannot_use_administrator_endpoints(operator: Client) -> None:
    client, headers = operator
    assert client.get("/api/backups").status_code == 403
    assert client.get("/api/versions").status_code == 403
    assert client.post("/api/users", json={"name": "bob", "role": "admin"}, headers=headers).status_code == 403


def test_security_headers(admin: Client) -> None:
    client, _ = admin
    headers = client.get("/api/me").headers
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Frame-Options"] == "DENY"


def test_documentation_requires_sign_in(app: Flask) -> None:
    assert app.test_client().get("/docs/README.md").status_code == 302


def test_every_documentation_link_and_image_works(admin: Client) -> None:
    from instance_manager.web import docs

    client, _ = admin
    broken = []
    for path in docs.PAGES:
        html = client.get(f"/docs/{path}").get_data(as_text=True)
        for href in re.findall(r'(?:href|src)="(/docs/[^"]+)"', html):
            target, _, anchor = href.partition("#")
            page = client.get(target)
            if page.status_code != 200 or (anchor and f'id="{anchor}"' not in page.get_data(as_text=True)):
                broken.append(f"{path} -> {href}")
    assert not broken
    assert client.get("/docs/../README.md").status_code == 404
