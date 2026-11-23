"""The client portal (projects/clients.py, web/v1/clients.py): a client's switch closes its project or branches to
everyone else, super admins included; time-limited opening; access requests; emergency access; a client sees only
its own projects; the portal's own address and sessions that never cross between the two addresses."""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import ORIGIN, sign_in
from sudoo import accounts, projects
from sudoo.config import live
from sudoo.core import audit
from sudoo.instances import store
from sudoo.projects import access, clients
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
PASSWORD = "harbour-violet-compass-{}"
PORTAL = "portal.example.com"
NAMES = ("cp-main", "cp-dev", "cp-other")


def _instance(name: str, project_id: str, branch: str, stage: str) -> None:
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39700 + 2 * NAMES.index(name),
            "ODOO_PORT": 39701 + 2 * NAMES.index(name),
            "GIT_BRANCH": branch,
            "AUTO_UPDATE": "true",
            "AUTO_UPGRADE": "true",
            "AUTO_PAUSE": "true",
            "DELETE_AFTER_DAYS": "30",
            "CREATED_AT": int(time.time() - 3600),
            "STAGE": stage,
            "PROJECT": project_id,
        },
    )


def _account(name: str, role: str) -> None:
    if accounts.get(name) is None:
        accounts.create(name, role, PASSWORD.format(name), first_name=name, email=f"{name}@example.com")
        if role == "client":  # clients sign in with e-mail codes by default (CLIENT_SIGN_IN_METHODS); passwords here
            accounts.update(name, sign_in_methods=["password", "email_code"])


def _sign_in(app: Flask, name: str, host: str = "localhost") -> Client:
    if host == "localhost":
        return sign_in(app, name, PASSWORD.format(name))
    client = app.test_client()
    origin = {"Origin": f"http://{host}"}
    answer = client.post(
        "/api/v1/session", json={"username": name, "password": PASSWORD.format(name)}, headers=origin,
        base_url=f"http://{host}",
    )  # fmt: skip
    assert answer.status_code == 201, answer.get_json()
    return client, {**origin, "X-CSRF-Token": answer.get_json()["data"]["csrf_token"]}


@pytest.fixture
def project() -> Iterator[str]:
    found = projects.create(repository="Acme/Portal", odoo_version="18.0", edition="community", origin="linked",
                            user="t")  # fmt: skip
    other = projects.create(repository="Acme/Other", odoo_version="18.0", edition="community", origin="linked",
                            user="t")  # fmt: skip
    _instance("cp-main", found["id"], "main", "production")
    _instance("cp-dev", found["id"], "dev", "development")
    _instance("cp-other", other["id"], "main", "production")
    _account("cp-client", "client")
    _account("cp-client2", "client")
    _account("cp-admin", "admin")
    _account("cp-dev1", "developer")
    clients.add_client(found["id"], "cp-client", "admin1", backups=True)
    clients.add_client(other["id"], "cp-client2", "admin1")
    access.forget_restrictions()
    yield str(found["id"])
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
    projects.remove(found["id"])
    projects.remove(other["id"])
    for key in list(storage().all(clients.REQUESTS)):
        storage().delete(clients.REQUESTS, key)
    access.forget_restrictions()


def _other(project_id: str) -> str:
    return next(p["id"] for p in projects.all_projects() if p["id"] != project_id and p["repository"] == "Acme/Other")


SUPER = {"name": "admin1", "role": "super_admin"}
ADMIN = {"name": "cp-admin", "role": "admin"}
CLIENT = {"name": "cp-client", "role": "client"}


# ---------------------------------------------------------------- a closed resource


def test_a_closed_project_refuses_staff_and_super_admins_every_protected_action(app: Flask, project: str) -> None:
    clients.set_switch(project, None, "closed", "cp-client")
    found = projects.get(project)
    token_of_super = {"name": "api:t", "role": "admin", "token_id": "x", "owner": "admin1"}
    for user in (SUPER, ADMIN, token_of_super):
        for permission in sorted(access.LOCKED_PERMISSIONS):
            if permission not in access.role_permissions(user):
                continue
            assert not access.allowed(user, permission, found), (user["name"], permission)
            assert not access.allowed_instance(user, permission, "cp-main"), (user["name"], permission)
            refused = access.instance_refusal(user, permission, "cp-main")
            assert refused is not None and refused[0] == 403 and "closed by its client" in refused[1]
        assert access.allowed_instance(user, "instances.view", "cp-main")  # they still see it, closed
        assert not access.may_unlock(user, found, "main")  # no lock changes inside a closure
    assert access.allowed_instance(CLIENT, "instances.view", "cp-main")
    assert access.allowed_instance(CLIENT, "backups.manage", "cp-main")  # given to this client
    assert access.held(found, "main") is not None  # automatic changes stop, as for a lock
    assert access.instance_lock("cp-main", closures=False) is None  # a visit still starts a paused instance


def test_the_api_refuses_a_super_admin_inside_a_closed_branch(app: Flask, project: str, admin: Client) -> None:
    clients.set_switch(project, "main", "closed", "cp-client")
    web, headers = admin
    assert web.get("/api/v1/instances/cp-main/credentials").status_code == 403
    assert web.post("/api/v1/instances/cp-main/shell/tickets", json={}, headers=headers).status_code == 403
    assert web.put(f"/api/v1/projects/{project}/branches/main/lock", json={}, headers=headers).status_code == 403
    assert web.get("/api/v1/instances/cp-main").status_code == 200  # seen, with its closure
    shown = web.get("/api/v1/instances/cp-main").get_json()["data"]["client_access"]
    assert shown["state"] == "closed" and shown["scope"] == "branch"
    # the other branch keeps following the project, which is open
    assert access.allowed_instance(SUPER, "instances.credentials", "cp-dev")
    refused = [e for e in audit.latest(20) if e["event"].startswith("refused:") and e.get("instance") == "cp-main"]
    assert refused and refused[0].get("project") == project  # in the client's activity log


def test_a_client_named_person_is_let_through(app: Flask, project: str) -> None:
    clients.set_switch(project, None, "closed", "cp-client")
    assert not access.allowed_instance(ADMIN, "instances.credentials", "cp-main")
    clients.set_allow(project, ["cp-admin"], "cp-client")
    assert access.allowed_instance(ADMIN, "instances.credentials", "cp-main")
    assert not access.allowed_instance(SUPER, "instances.credentials", "cp-main")


def test_staff_cannot_take_a_clients_place_while_it_is_closed(app: Flask, project: str, admin: Client) -> None:
    web, headers = admin
    clients.set_switch(project, None, "closed", "cp-client")
    assert web.post("/api/v1/users/cp-client/password", json={}, headers=headers).status_code == 409
    assert web.patch("/api/v1/users/cp-client", json={"email": "x@example.com"}, headers=headers).status_code == 409
    assert web.delete("/api/v1/users/cp-client", headers=headers).status_code == 409
    assert web.delete(f"/api/v1/projects/{project}/clients/cp-client", headers=headers).status_code == 409
    clients.set_switch(project, None, "open", "cp-client")
    answer = web.patch("/api/v1/users/cp-client", json={"role": "admin"}, headers=headers)
    assert answer.status_code == 409  # a client stays a client


# ---------------------------------------------------------------- open for a time


def test_open_for_a_time_closes_again(app: Flask, project: str, monkeypatch: pytest.MonkeyPatch) -> None:
    clients.set_switch(project, None, "timed", "cp-client", hours=1)
    assert access.allowed_instance(SUPER, "instances.credentials", "cp-main")
    later = time.time() + 3601
    monkeypatch.setattr(clients, "_now", lambda: later)
    assert not access.allowed_instance(SUPER, "instances.credentials", "cp-main")  # closed without waiting
    assert clients.describe(projects.get(project))["state"] == "closed"  # type: ignore[index]
    assert clients.sweep(later) == [project]
    assert clients.part(projects.get(project))["state"] == "closed"
    assert any(e["event"] == "client access closed again (time over)" for e in audit.latest(20))
    with pytest.raises(clients.ClientError):
        clients.set_switch(project, None, "timed", "cp-client", hours=100000)


# ---------------------------------------------------------------- requests


def test_request_then_approve_lets_the_person_in_for_the_time_given(
    app: Flask, project: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr("sudoo.mail.send", lambda to, key, **values: sent.append((to, key, values)))
    clients.set_switch(project, "main", "closed", "cp-client")
    staff = _sign_in(app, "cp-admin")
    web, headers = staff
    body = {"branch": "main", "reason": "The invoice report fails since this morning.", "hours": 2}
    answer = web.post(f"/api/v1/projects/{project}/access-requests", json=body, headers=headers)
    assert answer.status_code == 201, answer.get_json()
    request_id = answer.get_json()["data"]["id"]
    assert sent and sent[0][0] == "cp-client@example.com" and sent[0][1] == "client_access_request"
    assert web.post(f"/api/v1/projects/{project}/access-requests", json=body, headers=headers).status_code == 409
    assert web.get("/api/v1/instances/cp-main/credentials").status_code == 403  # nothing opens before the answer
    client, client_headers = _sign_in(app, "cp-client")
    shown = client.get(f"/api/v1/client/projects/{project}").get_json()["data"]
    assert [r["id"] for r in shown["requests"] if r["status"] == "pending"] == [request_id]
    approved = client.post(f"/api/v1/client/requests/{request_id}/approve", json={"hours": 1}, headers=client_headers)
    assert approved.status_code == 200, approved.get_json()
    assert approved.get_json()["data"]["people"][0]["name"] == "cp-admin"
    assert access.allowed_instance(ADMIN, "instances.credentials", "cp-main")
    assert not access.allowed_instance(SUPER, "instances.credentials", "cp-main")  # only those named
    later = time.time() + 3601
    monkeypatch.setattr(clients, "_now", lambda: later)
    assert not access.allowed_instance(ADMIN, "instances.credentials", "cp-main")  # the time given is over
    events = [e["event"] for e in audit.search({"project": project}, "ts", True, 50, 0)[0]]
    assert "access requested" in events and "access request approved" in events


def test_a_refused_request_opens_nothing(app: Flask, project: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sudoo.mail.send", lambda *a, **k: None)
    clients.set_switch(project, None, "closed", "cp-client")
    made = clients.ask(projects.get(project) or {}, None, "cp-admin", "Need to check the logs please.", 1)
    client, headers = _sign_in(app, "cp-client")
    answer = client.post(f"/api/v1/client/requests/{made['id']}/refuse", json={"note": "Not today"}, headers=headers)
    assert answer.status_code == 200 and answer.get_json()["data"]["status"] == "refused"
    assert not access.allowed_instance(ADMIN, "instances.logs", "cp-main")
    other, other_headers = _sign_in(app, "cp-client2")  # another project's client cannot answer it
    assert (
        other.post(f"/api/v1/client/requests/{made['id']}/approve", json={}, headers=other_headers).status_code == 404
    )


# ---------------------------------------------------------------- emergency access


def test_emergency_access_when_allowed(
    app: Flask, project: str, admin: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []
    monkeypatch.setattr("sudoo.mail.send", lambda to, key, **values: sent.append(key))
    clients.set_switch(project, None, "closed", "cp-client")
    web, headers = admin
    path = f"/api/v1/projects/{project}/emergency-access"
    assert web.post(path, json={"reason": "Production is down for everyone", "confirm": "nope"},
                    headers=headers).status_code == 400  # fmt: skip
    answer = web.post(path, json={"reason": "Production is down for everyone", "confirm": project}, headers=headers)
    assert answer.status_code == 201, answer.get_json()
    assert sent == ["client_emergency_access"]  # the client is told at once
    assert access.allowed_instance(SUPER, "instances.credentials", "cp-main")
    assert not access.allowed_instance(ADMIN, "instances.credentials", "cp-main")  # only the super admin
    until = time.time() + live.client_emergency_minutes * 60 + 1
    monkeypatch.setattr(clients, "_now", lambda: until)
    assert not access.allowed_instance(SUPER, "instances.credentials", "cp-main")
    other = _sign_in(app, "cp-admin")  # an administrator never forces access
    assert other[0].post(path, json={"reason": "Production is down for everyone", "confirm": project},
                         headers=other[1]).status_code == 403  # fmt: skip
    client, client_headers = _sign_in(app, "cp-client")
    notices = client.get(f"/api/v1/client/projects/{project}").get_json()["data"]["notices"]
    assert notices and notices[0]["kind"] == "emergency"


def test_emergency_access_turned_off_by_the_client(app: Flask, project: str, admin: Client) -> None:
    clients.set_switch(project, None, "closed", "cp-client")
    client, client_headers = _sign_in(app, "cp-client")
    answer = client.put(f"/api/v1/client/projects/{project}/emergency", json={"allowed": False}, headers=client_headers)
    assert answer.status_code == 200 and answer.get_json()["data"]["emergency_allowed"] is False
    web, headers = admin
    refused = web.post(f"/api/v1/projects/{project}/emergency-access",
                       json={"reason": "Production is down for everyone", "confirm": project}, headers=headers)  # fmt: skip
    assert refused.status_code == 403
    assert not access.allowed_instance(SUPER, "instances.credentials", "cp-main")
    assert any(e["event"] == "emergency access refused (turned off by the client)" for e in audit.latest(10))


# ---------------------------------------------------------------- isolation


def test_a_client_sees_only_its_own_projects(app: Flask, project: str) -> None:
    client, headers = _sign_in(app, "cp-client")
    listed = client.get("/api/v1/client/projects").get_json()["data"]
    assert [p["id"] for p in listed] == [project]
    assert client.get(f"/api/v1/client/projects/{_other(project)}").status_code == 404
    for path in ("/api/v1/projects", "/api/v1/instances", "/api/v1/users", "/api/v1/audit", "/api/v1/settings",
                 "/api/v1/instances/cp-other", "/api/v1/backups", "/api/v1/server"):  # fmt: skip
        assert client.get(path).status_code == 403, path
    assert not access.sees_instance(CLIENT, "cp-other")
    assert access.visible_instances(CLIENT, ["cp-main", "cp-dev", "cp-other"]) == ["cp-main", "cp-dev"]
    assert not access.allowed(CLIENT, "instances.view", None)  # nothing outside its projects
    shown = client.get(f"/api/v1/client/projects/{project}").get_json()["data"]
    assert {i["name"] for b in shown["branches"] for i in b["instances"]} == {"cp-main", "cp-dev"}
    activity = client.get(f"/api/v1/client/projects/{project}/activity").get_json()
    assert all(e["address"] is None for e in activity["data"] if e["user"] != "cp-client")
    exported = client.get(f"/api/v1/client/projects/{project}/activity/export")
    assert exported.status_code == 200 and exported.mimetype == "text/csv"


def test_adding_a_client_is_premium(app: Flask, project: str, admin: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    web, headers = admin
    body = {"email": "cp-client2@example.com"}
    assert web.post(f"/api/v1/projects/{project}/clients", json=body, headers=headers).status_code == 402
    from sudoo import premium

    monkeypatch.setattr(premium, "loaded", lambda package_id: package_id == "teams")
    answer = web.post(f"/api/v1/projects/{project}/clients", json=body, headers=headers)
    assert answer.status_code == 201, answer.get_json()
    assert clients.entry(projects.get(project), "cp-client2") is not None
    staff = {"email": "cp-admin@example.com"}
    assert web.post(f"/api/v1/projects/{project}/clients", json=staff, headers=headers).status_code == 409
    clients.set_switch(project, None, "closed", "cp-client")
    assert web.post(f"/api/v1/projects/{project}/clients", json=body, headers=headers).status_code == 403


# ---------------------------------------------------------------- the portal's address


@pytest.fixture
def portal() -> Iterator[None]:
    live.change({"portal_domain": PORTAL, "portal_name": "Acme Integrators"})
    yield
    live.change({"portal_domain": None, "portal_name": None})


def test_the_portal_address_serves_the_client_layout(app: Flask, project: str, portal: None) -> None:
    web = app.test_client()
    shown = web.get("/api/v1/portal", base_url=f"http://{PORTAL}").get_json()["data"]
    assert shown["portal"] is True and shown["name"] == "Acme Integrators"
    assert web.get("/api/v1/portal").get_json()["data"]["portal"] is False
    client, _ = _sign_in(app, "cp-client", PORTAL)
    me = client.get("/api/v1/session", base_url=f"http://{PORTAL}").get_json()["data"]
    assert me["address"] == "portal" and me["role"] == "client"
    # staff are sent to the panel's address; nothing is started at the portal's
    staff = app.test_client()
    answer = staff.post("/api/v1/session", json={"username": "cp-admin", "password": PASSWORD.format("cp-admin")},
                        headers={"Origin": f"http://{PORTAL}"}, base_url=f"http://{PORTAL}")  # fmt: skip
    assert answer.status_code == 200 and answer.get_json()["data"]["redirect"]["address"] == "panel"
    assert staff.get("/api/v1/session", base_url=f"http://{PORTAL}").status_code == 401
    # and a client at the panel's address is sent to the portal
    elsewhere = app.test_client().post(
        "/api/v1/session", json={"username": "cp-client", "password": PASSWORD.format("cp-client")}, headers=ORIGIN
    )
    assert elsewhere.get_json()["data"]["redirect"] == {"address": "portal", "url": f"https://{PORTAL}"}


def test_a_session_never_crosses_between_the_two_addresses(app: Flask, project: str, portal: None) -> None:
    web, _ = _sign_in(app, "cp-admin")
    assert web.get("/api/v1/session").status_code == 200
    cookie = web.get_cookie("sudoo_session")
    assert cookie is not None
    web.set_cookie("sudoo_session", cookie.value, domain=PORTAL)
    assert web.get("/api/v1/session", base_url=f"http://{PORTAL}").status_code == 401  # the panel's cookie, elsewhere
    client, _ = _sign_in(app, "cp-client", PORTAL)
    found = client.get_cookie("sudoo_session", domain=PORTAL)
    assert found is not None
    other = app.test_client()
    other.set_cookie("sudoo_session", found.value, domain="localhost")
    assert other.get("/api/v1/session").status_code == 401  # the portal's cookie at the panel's address
