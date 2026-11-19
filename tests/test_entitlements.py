"""The plan's entitlements from the hub's signed licence (entitlements.py), against the fake hub (fake_hub.py) speaking
the hub's contract (sudoo-hub docs/PLANS.md): an unlinked panel has the Free plan; production branches are counted and
limited (extra branches and privileges are already in the hub's number), withheld production makes none, the
entitlements are kept through a short outage and then only stop new production; feature gates with the upsell; the
heartbeat reports the usage in the hub's exact shape; production_keep after a downgrade; the hub's 429 API quota."""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import sign_in
from fake_hub import FakeHub
from sudoo import accounts, alerts, entitlements, projects, teams
from sudoo.config import live
from sudoo.core import audit
from sudoo.core.state import STATE
from sudoo.hub import HubRefused, HubUnavailable, client, marketplace
from sudoo.instances import monitor, store
from sudoo.storage import storage

Client = tuple[FlaskClient, dict[str, str]]
NAMES = ("pl-main", "pl-second", "pl-plain")
PRO = {
    "plan": "pro",
    "max_servers": 2,
    "max_projects": None,
    "max_production_branches": 2,
    "production_withheld": None,
    "teams": True,
    "priority_support": False,
    "support_level": "email",
    "features": {"per_project_access": True, "custom_domains": True, "offsite_backups": True},
    "addons": {"extra_production_branch": 1},
    "api_requests_per_day": 100000,
    "privileges": [],
    "trial_plan": None,
    "production_keep": None,
    "banned": False,
    "ban_scope": None,
    "over_limit": [],
    "over_limit_grace_until": None,
}


def _instance(name: str, project: str, branch: str, stage: str) -> None:
    folder = store.folder(name)
    (folder / "addons").mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text("services: {}\n")
    store.write_env(
        name,
        {
            "ODOO_VERSION": "18.0",
            "EDITION": "community",
            "DB_NAME": name.replace("-", "_"),
            "GATEWAY_PORT": 39800 + 2 * NAMES.index(name),
            "ODOO_PORT": 39801 + 2 * NAMES.index(name),
            "GIT_BRANCH": branch,
            "PROJECT": project,
            "STAGE": stage,
            "AUTO_UPDATE": "false",
            "AUTO_UPGRADE": "true",
            "AUTO_PAUSE": "false",
            "DELETE_AFTER_DAYS": "0",
            "CREATED_AT": int(time.time() - 3600),
            **({"SOURCE": "plain"} if not project else {}),
        },
    )


@pytest.fixture(autouse=True)
def world() -> Iterator[str]:
    erp = projects.create(repository="Acme/Plan", odoo_version="18.0", edition="community", origin="linked", user="t")
    projects.record_branch(erp["id"], "main", stage="production")
    _instance("pl-main", erp["id"], "main", "production")
    for collection in (client.COLLECTION,):
        for key in storage().all(collection):
            storage().delete(collection, key)
    yield erp["id"]
    for name in NAMES:
        shutil.rmtree(store.folder(name), ignore_errors=True)
        STATE.remove(name)
        monitor.STATUS.pop(name, None)
    for key in list(storage().all(projects.COLLECTION)):
        projects.remove(key)
    for collection in (client.COLLECTION, teams.COLLECTION):
        for key in list(storage().all(collection)):
            storage().delete(collection, key)
    live.change(dict.fromkeys(live.overrides()))


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHub]:
    fake = FakeHub().start()
    monkeypatch.setattr(client, "hub_url", lambda: fake.url)
    live.change({"marketplace": True})
    yield fake
    live.change({"marketplace": None})
    fake.stop()


@pytest.fixture
def plain_admin(app: Flask) -> Client:
    if accounts.get("pl-admin") is None:
        accounts.create("pl-admin", "admin", "orchid-ledger-violet-77")
    return sign_in(app, "pl-admin", "orchid-ledger-violet-77")


# ---------------------------------------------------------------- an unlinked panel


def test_an_unlinked_panel_has_the_free_plan(world: str, admin: Client) -> None:
    found = entitlements.current()
    assert (found.plan, found.source, found.linked) == ("free", "unlinked", False)
    assert found.limit("production_branches") == 1 and not found.has("custom_domains")
    # the production branch it has stays; one more is refused with where to get more
    assert entitlements.production_refusal(f"{world}/main") is None
    refused = entitlements.production_refusal(f"{world}/release")
    assert refused and "Free plan allows 1 production branch" in refused and "https://sudoo.sh/pricing" in refused
    assert "not linked" in refused
    # through the API (the project's own limit lifted: the plan's is what refuses)
    live.change({"project_production_branches": 0})
    client_, headers = admin
    answer = client_.put(f"/api/v1/projects/{world}/branches/release/stage",
                         json={"stage": "production", "confirm_production": True}, headers=headers)  # fmt: skip
    assert answer.status_code == 409 and "sudoo.sh/pricing" in answer.get_json()["detail"]
    shown = client_.get("/api/v1/plan").get_json()["data"]
    assert shown["plan"] == "free" and shown["source"] == "unlinked" and shown["linked"] is False
    assert shown["usage"]["production_branches"] == 1 and shown["usage"]["production_branch_names"] == [f"{world}/main"]
    assert (
        shown["entitlements"]["max_production_branches"] == 1
        and shown["links"]["pricing"] == "https://sudoo.sh/pricing"
    )
    assert shown["production_refusal"]


def test_paid_features_are_refused_with_the_upsell(world: str, admin: Client, plain_admin: Client) -> None:
    client_, headers = admin
    # without the premium package (it installs automatically once the plan includes it): 402 with the plans
    refused = client_.post("/api/v1/teams", json={"name": "Leads"}, headers=headers)
    assert refused.status_code == 402 and "Pro and Business" in refused.get_json()["detail"]
    assert "installs automatically" in refused.get_json()["detail"]
    grants = {"mode": "panel_roles", "grants": [{"type": "user", "id": "admin1", "level": "view"}]}
    assert client_.put(f"/api/v1/projects/{world}/access", json=grants, headers=headers).status_code == 402
    # clearing a project's access is always possible
    assert (
        client_.put(f"/api/v1/projects/{world}/access", json={"mode": "panel_roles"}, headers=headers).status_code
        == 200
    )
    # locks are on every plan
    lock = client_.put(f"/api/v1/projects/{world}/branches/main/lock", json={"allow": []}, headers=headers)
    assert lock.status_code == 200
    answer = client_.post("/api/v1/instances/pl-main/domains", json={"domain": "erp.example.com"}, headers=headers)
    assert answer.status_code == 402 and "Custom domains" in answer.get_json()["detail"]
    off = client_.patch("/api/v1/settings", json={"offsite_backups": True}, headers=headers)
    assert off.status_code == 403 and "Off-site backups" in off.get_json()["detail"]
    assert any(e["event"].endswith("(plan)") for e in audit.latest(50))
    assert any(e["event"].endswith("(premium feature not installed)") for e in audit.latest(50))


# ---------------------------------------------------------------- from the hub


def test_the_hubs_entitlements_apply_and_the_usage_is_reported(world: str, hub: FakeHub, admin: Client) -> None:
    hub.entitlements = dict(PRO)
    _instance("pl-plain", "", "", "production")
    marketplace.heartbeat()
    usage = hub.heartbeats[-1]["usage"]
    assert usage == {
        "servers": 1,
        "projects": 1,
        "production_branches": 2,
        "production_branch_names": ["pl-plain", f"{world}/main"],
        "instances_by_stage": {"production": 2, "staging": 0, "development": 0},
    }
    found = entitlements.current()
    assert (found.plan, found.source) == ("pro", "hub") and found.has("teams") and found.has("custom_domains")
    # 2 production branches (the plan's 1 + an extra branch bought): both used, a third is refused
    assert entitlements.production_refusal(f"{world}/release") is not None
    hub.entitlements = {**PRO, "max_production_branches": 3, "privileges": [{"kind": "extra_production_branches",
                                                                             "value": {"count": 1}, "until": None}]}  # fmt: skip
    marketplace.heartbeat()
    assert entitlements.production_refusal(f"{world}/release") is None
    client_, headers = admin
    shown = client_.get("/api/v1/plan").get_json()["data"]
    assert shown["plan"] == "pro" and shown["entitlements"]["max_production_branches"] == 3
    assert shown["entitlements"]["features"]["custom_domains"] is True and shown["valid_until"]
    pending = client_.post("/api/v1/teams", json={"name": "Pro team"}, headers=headers)
    assert pending.status_code == 402 and "included in your plan" in pending.get_json()["detail"]


def test_withheld_production_makes_none_and_keeps_what_runs(world: str, hub: FakeHub) -> None:
    hub.entitlements = {**PRO, "max_production_branches": 0, "production_withheld": "verification_required"}
    marketplace.heartbeat()
    refused = entitlements.production_refusal(f"{world}/release")
    assert refused and "not verified" in refused and "keep serving" in refused
    assert entitlements.production_refusal(f"{world}/main") is None  # the one that runs stays
    assert store.read_env("pl-main")["STAGE"] == "production"


def test_an_offline_panel_keeps_its_plan_then_only_stops_new_production(
    world: str, hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub.entitlements = dict(PRO)
    marketplace.heartbeat()
    hub.stop()
    with pytest.raises(HubUnavailable):
        marketplace.heartbeat()
    found = entitlements.current()
    assert (found.plan, found.source) == ("pro", "grace")
    assert entitlements.production_refusal(f"{world}/release") is None
    later = time.time() + 30 * 3600  # past valid_until (24 hours)
    monkeypatch.setattr(marketplace, "trusted_now", lambda: later)
    found = entitlements.current()
    assert found.source == "expired" and found.has("custom_domains")  # features stay as they were
    refused = entitlements.production_refusal(f"{world}/release")
    assert refused and "keeps serving" in refused
    assert entitlements.production_refusal(f"{world}/main") is None
    hub.start = lambda: hub  # type: ignore[method-assign]  # stopped already; the fixture stops it again
    hub.stop = lambda: None  # type: ignore[method-assign]


def test_production_branches_not_kept_after_a_downgrade_become_development(world: str, hub: FakeHub) -> None:
    projects.record_branch(world, "second", stage="production")
    _instance("pl-second", world, "second", "production")
    hub.entitlements = {**PRO, "plan": "free", "max_production_branches": 1, "production_keep": [f"{world}/main"],
                        "over_limit": ["production_branches"]}  # fmt: skip
    marketplace.heartbeat()
    assert store.read_env("pl-second")["STAGE"] == "development"
    assert store.read_env("pl-second")["DELETE_AFTER_DAYS"] == "0"  # kept: nothing is deleted
    assert store.read_env("pl-main")["STAGE"] == "production"
    found = projects.get(world)
    assert found is not None and found["branches"]["second"]["stage"] == "development"
    assert any(e["event"] == "production branch left production (plan)" for e in audit.latest(50))
    assert any(a["type"] == "plan" for a in alerts.recent())
    assert entitlements.leaving_production() == []  # done


# ---------------------------------------------------------------- the hub's daily API quota


def test_the_hubs_api_quota_is_respected(hub: FakeHub, admin: Client) -> None:
    marketplace.heartbeat()
    hub.quota_retry_after = 3600
    with pytest.raises(HubRefused) as refused:
        marketplace.catalog({})
    assert refused.value.status == 429 and refused.value.retry_after == 3600
    assert "midnight UTC" in refused.value.detail and "keep working" in refused.value.detail
    sent = len(hub.requests)
    with pytest.raises(HubRefused):
        marketplace.catalog({})  # not sent again before Retry-After
    assert len(hub.requests) == sent
    marketplace.heartbeat()  # heartbeats never count: the licence never stops on the quota
    assert len(hub.requests) > sent
    client_, headers = admin
    answer = client_.get("/api/v1/marketplace/catalog")
    assert answer.status_code == 429 and int(answer.headers["Retry-After"]) > 3000
    assert marketplace.status()["api_quota_wait"] > 3000
