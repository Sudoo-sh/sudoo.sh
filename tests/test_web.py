"""The API's request rules, input checks, roles, first-run setup and the documentation."""

from __future__ import annotations

import re

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import ORIGIN, sign_in

Client = tuple[FlaskClient, dict[str, str]]


def test_changes_must_be_json(app: Flask) -> None:
    response = app.test_client().post("/api/v1/session", data={"username": "a", "password": "b"}, headers=ORIGIN)
    assert response.status_code == 415


def test_changes_from_another_site_are_refused(app: Flask) -> None:
    response = app.test_client().post("/api/v1/session", json={}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_csrf_token_is_required(admin: Client) -> None:
    client, _ = admin
    assert client.post("/api/v1/users", json={"name": "bob", "role": "viewer"}, headers=ORIGIN).status_code == 403


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
    response = client.post("/api/v1/instances", json={**valid, **body}, headers=headers)
    assert response.status_code == 400 and message in response.get_json()["detail"]


def test_versions_and_editions(admin: Client) -> None:
    client, _ = admin
    assert client.get("/api/v1/versions").get_json()["data"] == [
        {"version": v, "enterprise": False} for v in ("18.0", "19.0", "20.0")  # ENTERPRISE_SOURCE=none
    ]


def test_roles_limit_what_each_account_can_do(operator: Client) -> None:
    """sync1 is a developer: sees and updates instances, nothing more."""
    client, headers = operator
    assert client.get("/api/v1/versions").status_code == 200
    assert client.get("/api/v1/backups").status_code == 403
    assert client.get("/api/v1/settings").status_code == 403
    assert client.post("/api/v1/users", json={"name": "bob", "role": "admin"}, headers=headers).status_code == 403
    me = client.get("/api/v1/session").get_json()["data"]
    assert me["role"] == "developer" and "instances.update" in me["permissions"]
    assert "instances.delete" not in me["permissions"]


def test_only_a_super_admin_manages_super_admins(app: Flask, admin: Client) -> None:
    from sudoo import accounts

    accounts.create("plainadmin", "admin", "maple-harbor-quartz-88")
    client, headers = sign_in(app, "plainadmin", "maple-harbor-quartz-88")
    made = client.post("/api/v1/users", json={"name": "sneaky", "role": "super_admin"}, headers=headers)
    assert made.status_code == 403
    assert client.delete("/api/v1/users/admin1", headers=headers).status_code == 403
    assert client.get("/api/v1/settings").status_code == 403  # settings are for super admins
    roles = client.get("/api/v1/roles").get_json()["data"]["roles"]
    assert [r["name"] for r in roles] == ["super_admin", "admin", "maintainer", "developer", "viewer"]


def test_generated_passwords_must_be_replaced(app: Flask, admin: Client) -> None:
    client, headers = admin
    made = client.post(
        "/api/v1/users", json={"name": "newbie", "role": "viewer", "first_name": "Noor"}, headers=headers
    )
    assert made.status_code == 201
    password = made.get_json()["data"]["password"]
    newbie, newbie_headers = sign_in(app, "newbie", password)
    assert newbie.get("/api/v1/session").get_json()["data"]["restricted"] == "password"
    assert newbie.get("/api/v1/instances").status_code == 403
    changed = newbie.put(
        "/api/v1/session/password", json={"current": password, "new": "harbor-violet-candle-59"}, headers=newbie_headers
    )
    assert changed.status_code == 204
    after = newbie.get("/api/v1/instances")
    assert after.status_code == 200, after.get_json()


def test_security_headers(admin: Client) -> None:
    client, _ = admin
    headers = client.get("/api/v1/session").headers
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Frame-Options"] == "DENY"


def test_documentation_requires_sign_in(app: Flask) -> None:
    assert app.test_client().get("/api/v1/docs/README.md").status_code == 401
    assert app.test_client().get("/docs/docs/images/architecture.svg").status_code == 302


def test_every_documentation_link_and_image_works(admin: Client) -> None:
    from sudoo.web import docs

    client, _ = admin
    contents = client.get("/api/v1/docs").get_json()["data"]
    assert contents["index"] in docs.PAGES and contents["sections"]
    broken = []
    for path in docs.PAGES:
        page = client.get(f"/api/v1/docs/{path}").get_json()["data"]
        for kind, href in re.findall(r'(?:href|src)="/(help|docs)/([^"]+)"', page["html"]):
            target, _, anchor = href.partition("#")
            if kind == "docs":
                ok = client.get(f"/docs/{target}").status_code == 200
            else:
                linked = client.get(f"/api/v1/docs/{target}")
                ok = linked.status_code == 200 and (not anchor or f'id="{anchor}"' in linked.get_json()["data"]["html"])
            if not ok:
                broken.append(f"{path} -> /{kind}/{href}")
    assert not broken
    assert client.get("/api/v1/docs/../README.md").status_code == 404


# ---------------------------------------------------------------- first run


def test_first_run_needs_the_setup_code(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo import accounts

    monkeypatch.setattr(accounts, "setup_needed", lambda: True)
    monkeypatch.setattr(accounts, "_setup_file", lambda: accounts.settings.manager_dir / "setup_code_test")
    code = accounts.setup_code()
    assert code and re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code)
    client = app.test_client()
    assert client.get("/api/v1/setup").get_json()["data"]["needed"] is True
    body = {
        "username": "owner",
        "password": "lantern-orchard-river-31",
        "first_name": "Yousef",
        "email": "y@example.com",
    }
    wrong = client.post("/api/v1/setup", json={**body, "setup_code": "AAAA-BBBB-CCCC"}, headers=ORIGIN)
    assert wrong.status_code == 403
    done = client.post("/api/v1/setup", json={**body, "setup_code": code.lower()}, headers=ORIGIN)
    assert done.status_code == 201
    me = done.get_json()["data"]
    assert me["role"] == "super_admin" and me["display_name"] == "Yousef" and me["restricted"] is None
    assert not (accounts.settings.manager_dir / "setup_code_test").exists()


def test_visitor_address_behind_the_web_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """X-Forwarded-For is trusted only from the web service on 127.0.0.1; CF-Connecting-IP only with Cloudflare."""
    from sudoo.exposure.ports import HostPorts
    from sudoo.web.security import client_ip_of

    forwarded = {"X-Forwarded-For": "203.0.113.7", "CF-Connecting-IP": "198.51.100.1"}
    assert client_ip_of(forwarded, "127.0.0.1") == "198.51.100.1"  # default exposure: Cloudflare
    assert client_ip_of({"X-Forwarded-For": "203.0.113.7"}, "127.0.0.1") == "203.0.113.7"
    assert client_ip_of({"X-Forwarded-For": "203.0.113.7"}, "192.0.2.50") == "192.0.2.50"  # not from the web service
    monkeypatch.setattr("sudoo.exposure.provider", lambda: HostPorts.__new__(HostPorts))
    assert client_ip_of(forwarded, "127.0.0.1") == "203.0.113.7"  # a visitor cannot pretend to be Cloudflare
