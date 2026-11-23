"""The setup wizard of the first-run page: the server check (read only; the setup code before the first account, a
signed-in admin after), the draft (resumable, validated, nothing applied), the test e-mail, the final confirm that
applies the draft, and the engine recommendation, the same rule as the installer's."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import ORIGIN
from sudoo import accounts, config, licence, server_check, setup_wizard
from sudoo.config import live, settings
from sudoo.storage import storage
from sudoo.web.v1 import setup_wizard as endpoints
from test_licence import licence_acceptance

Client = tuple[FlaskClient, dict[str, str]]


@pytest.fixture
def first_run(app: Flask, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FlaskClient, str]]:
    """A panel without accounts (as the setup page sees it), its setup code, and nothing installed by the installer."""
    monkeypatch.setattr(accounts, "setup_needed", lambda: True)
    monkeypatch.setattr(accounts, "_setup_file", lambda: settings.manager_dir / "setup_code_wizard")
    monkeypatch.setattr(licence, "COLLECTION", f"licence_wizard_{time.monotonic_ns()}")
    monkeypatch.setattr(server_check, "_docker_system", lambda: "Ubuntu 24.04.3 LTS")
    monkeypatch.setattr(server_check, "_cache", None)
    accounts.THROTTLE._failures.clear()
    endpoints.TEST_EMAILS._failures.clear()
    setup_wizard.finish()
    (settings.manager_dir / "installer").mkdir(exist_ok=True)
    yield app.test_client(), accounts.setup_code() or ""
    setup_wizard.finish()
    (settings.manager_dir / "installer" / "server-check").unlink(missing_ok=True)
    (settings.manager_dir / "setup_code_wizard").unlink(missing_ok=True)
    accounts.THROTTLE._failures.clear()


def code_headers(code: str) -> dict[str, str]:
    return {**ORIGIN, endpoints.SETUP_CODE_HEADER: code}


def facts(
    memory_gb: float, virt: str = "kvm", kind: str = "vm", system: str = "Ubuntu 24.04.3 LTS", cpus: int = 4
) -> dict[str, Any]:
    os_id, version = server_check.parse_system(system)
    return {
        "memory_mb": int(memory_gb * 1024),
        "processors": cpus,
        "virtualisation": {"name": virt, "kind": kind},
        "system": {"name": system, "id": os_id, "version": version},
    }


NO_ANSWER: dict[str, bool | None] = {"grow": None, "separation": None, "team": None}


def answers(grow: bool = False, separation: bool = False, team: bool = False) -> dict[str, bool | None]:
    return {"grow": grow, "separation": separation, "team": team}


# ---------------------------------------------------------------- the recommendation (the installer's rule)


def test_the_figures_come_from_one_file_and_are_to_be_measured() -> None:
    figure = server_check.figures()
    assert figure["min_cpus"] == 2 and figure["k3s_min_memory_mb"] == 2048  # k3s's own documented minimum
    assert figure["min_memory_mb"] == 2048 + figure["odoo_room_memory_mb"]
    assert figure["measured"] is False
    advice = server_check.recommend(facts(2), NO_ANSWER, False)
    assert "(to be measured)" in advice["reason"]


def test_by_need_not_by_size_docker_unless_the_answers_point_to_kubernetes() -> None:
    big = facts(64, cpus=32)
    assert server_check.recommend(big, NO_ANSWER, False)["recommended"] == "docker"
    assert server_check.recommend(big, answers(team=True), False)["reason"].startswith("one server run by a team")
    assert server_check.recommend(big, answers(), False)["reason"].startswith("one server run by one person")
    grow = server_check.recommend(facts(4), answers(grow=True), False)
    assert grow["recommended"] == "kubernetes" and grow["fits"] is True
    assert grow["reason"] == "you plan to add servers, and Kubernetes makes growing to more servers easier"
    separation = server_check.recommend(facts(4), answers(separation=True), False)
    assert separation["recommended"] == "kubernetes" and "stronger separation" in separation["reason"]


def test_docker_stays_pre_selected_while_kubernetes_is_beta() -> None:
    grow = server_check.recommend(facts(4), answers(grow=True), False)
    assert grow["default"] == "docker" and grow["beta"] is True
    assert server_check.recommend(facts(4), answers(grow=True), True)["default"] == "kubernetes"  # ENGINE_K8S_DEFAULT


def test_below_the_minimum_the_answers_do_not_matter_and_every_reason_is_given() -> None:
    small = server_check.recommend(facts(2), answers(grow=True), True)
    assert small["recommended"] == "docker" and small["fits"] is False and small["default"] == "docker"
    assert small["reason"] == (
        "Kubernetes needs at least 2 cores and 3 GB of memory (to be measured); this server has 4 cores and 2 GB"
    )
    assert server_check.recommend(facts(16, cpus=1), answers(grow=True), False)["fits"] is False
    advice = server_check.recommend(facts(16, "openvz", "container", "Rocky Linux 9.4"), answers(grow=True), True)
    assert advice["reasons"] == [
        "Kubernetes (k3s) cannot run properly in a container, and this server is one (openvz)",
        "Kubernetes is prepared on Ubuntu 22.04+ and Debian 12+; this server runs Rocky Linux 9.4",
    ]
    # a server sold as 3 GB reports a little less, and still counts
    assert server_check.recommend(facts(2.8), answers(grow=True), False)["fits"] is True


def test_no_promise_of_high_availability() -> None:
    import re

    text = Path(server_check.__file__).read_text() + (Path(__file__).parents[1] / "web/src/i18n/setup.ts").read_text()
    assert not re.search(r"failover|highly available|high-availability", text, re.IGNORECASE)


def test_systems() -> None:
    assert server_check.parse_system("Debian GNU/Linux 12 (bookworm)") == ("debian", "12")
    assert server_check.system_supported("ubuntu", "22.04") and not server_check.system_supported("ubuntu", "20.04")
    assert server_check.system_supported("debian", "12") and not server_check.system_supported("debian", "11")
    assert not server_check.system_supported("", "")


def test_the_beta_switch_is_one_constant() -> None:
    assert server_check.K8S_STABLE is False
    assert server_check.engines_available() == ["docker"]  # no Kubernetes engine package in this release


# ---------------------------------------------------------------- the server check


def test_the_server_check_needs_the_setup_code(first_run: tuple[FlaskClient, str]) -> None:
    client, code = first_run
    assert client.get("/api/v1/setup/server-check", headers=ORIGIN).status_code == 403
    assert client.get("/api/v1/setup/server-check", headers=code_headers("AAAA-BBBB-CCCC")).status_code == 403
    answer = client.get("/api/v1/setup/server-check", headers=code_headers(code.lower()))
    assert answer.status_code == 200, answer.get_json()
    data = answer.get_json()["data"]
    assert data["memory_mb"] > 0 and data["processors"] >= 1 and data["disk_free_gb"] >= 0
    assert data["system"] == {"name": "Ubuntu 24.04.3 LTS", "id": "ubuntu", "version": "24.04"}
    assert data["virtualisation"] == {"name": "unknown", "kind": "unknown", "from_installer": False}
    assert set(data["ports"]) == {"80", "443"} and "free" in data["ports"]["80"]
    assert data["recommendation"]["recommended"] == "docker" and data["needs"] == NO_ANSWER
    assert data["figures"]["measured"] is False and data["figures"]["min_cpus"] == 2
    assert data["engine"]["current"] == "docker" and data["engine"]["available"] == ["docker"]
    assert data["engine"]["switch_command"] == "sudo sudoo-install engine {engine}"


def test_the_server_check_reads_what_the_installer_found(first_run: tuple[FlaskClient, str]) -> None:
    client, code = first_run
    record = settings.manager_dir / "installer" / "server-check"
    record.write_text("VIRT=lxc\nVIRT_KIND=container\nOS_NAME=Ubuntu 24.04.3 LTS\n$(reboot)=x\nnot a line\n")
    data = client.get("/api/v1/setup/server-check?fresh=1", headers=code_headers(code)).get_json()["data"]
    assert data["virtualisation"] == {"name": "lxc", "kind": "container", "from_installer": True}
    assert data["recommendation"]["recommended"] == "docker" and data["recommendation"]["fits"] is False
    assert any("container" in reason for reason in data["recommendation"]["reasons"])


def test_the_server_check_takes_the_answers(first_run: tuple[FlaskClient, str]) -> None:
    client, code = first_run
    answer = client.get("/api/v1/setup/server-check?grow=yes&separation=no&team=maybe", headers=code_headers(code))
    data = answer.get_json()["data"]
    assert data["needs"] == {"grow": True, "separation": False, "team": None}
    if data["recommendation"]["fits"]:
        assert data["recommendation"]["recommended"] == "kubernetes"


def test_the_server_check_changes_nothing_and_has_no_other_method(first_run: tuple[FlaskClient, str]) -> None:
    client, code = first_run
    before = sorted(path.name for path in settings.manager_dir.iterdir())
    client.get("/api/v1/setup/server-check", headers=code_headers(code))
    assert sorted(path.name for path in settings.manager_dir.iterdir()) == before
    assert client.post("/api/v1/setup/server-check", headers=code_headers(code), json={}).status_code in (401, 405)


def test_after_setup_the_server_check_is_for_admins_only(app: Flask, admin: Client, operator: Client) -> None:
    server_check._cache = None
    assert app.test_client().get("/api/v1/setup/server-check", headers=ORIGIN).status_code == 401
    assert operator[0].get("/api/v1/setup/server-check", headers=ORIGIN).status_code == 403
    answer = admin[0].get("/api/v1/setup/server-check", headers=ORIGIN)
    assert answer.status_code == 200 and "recommendation" in answer.get_json()["data"]
    assert admin[0].get("/api/v1/setup/draft", headers=ORIGIN).status_code == 409  # the wizard is over


def test_wrong_setup_codes_are_limited(first_run: tuple[FlaskClient, str]) -> None:
    client, code = first_run
    for _ in range(accounts.SignInThrottle.MAX_PER_ACCOUNT_AND_ADDRESS):
        assert client.get("/api/v1/setup/draft", headers=code_headers("WRONG")).status_code == 403
    assert client.get("/api/v1/setup/draft", headers=code_headers(code)).status_code == 429


# ---------------------------------------------------------------- the draft


def test_the_draft_starts_from_the_settings_and_the_install_code(first_run: tuple[FlaskClient, str]) -> None:
    client, code = first_run
    (settings.manager_dir / "installer" / "install-code").write_text(
        "LINK_CODE=LINK-7Q2X-K9\nCOMPANY_LABEL=acme\nENGINE=kubernetes\nLANGUAGE=ar\nTIMEZONE=Asia/Gaza\n"
    )
    draft = client.get("/api/v1/setup/draft", headers=code_headers(code)).get_json()["data"]
    assert draft["step"] == "welcome" and draft["language"] == "ar" and draft["engine"] == "kubernetes"
    assert draft["account"] == {
        "link": True,
        "link_code": "LINK-7Q2X-K9",
        "company_label": "acme",
        "from_install_code": True,
    }
    assert draft["mail"]["password_set"] is False and "password" not in draft["mail"]


def test_the_draft_is_saved_validated_and_resumable(first_run: tuple[FlaskClient, str]) -> None:
    client, code = first_run
    headers = code_headers(code)
    saved = client.put(
        "/api/v1/setup/draft",
        json={
            "step": "email",
            "exposure": "proxy",
            "name": {"kind": "domain", "domain": "Panel.Example.com."},
            "mail": {"enabled": True, "host": "smtp.example.com", "port": 465, "security": "ssl",
                     "username": "no-reply@example.com", "sender": "", "password": "app-password-1"},
        },  # fmt: skip
        headers=headers,
    )
    assert saved.status_code == 200, saved.get_json()
    data = saved.get_json()["data"]
    assert data["step"] == "email" and data["name"] == {"kind": "domain", "domain": "panel.example.com"}
    assert data["mail"]["password_set"] is True and "app-password-1" not in saved.get_data(as_text=True)
    stored = (settings.manager_dir / "setup-draft.json").read_text()
    assert "app-password-1" not in stored  # encrypted
    assert oct((settings.manager_dir / "setup-draft.json").stat().st_mode & 0o777) == "0o600"
    # another browser, later: the same choices
    again = first_run[0].application.test_client().get("/api/v1/setup/draft", headers=headers).get_json()["data"]
    assert again["exposure"] == "proxy" and again["mail"]["port"] == 465
    # nothing was applied
    assert settings.smtp_host == "" and "panel_domain" not in live.overrides()

    for wrong, why in (
        ({"exposure": "everywhere"}, "exposure"),
        ({"name": {"kind": "domain", "domain": "not a name"}}, "name.domain"),
        ({"mail": {"enabled": True, "host": "smtp.example.com", "port": 70000}}, "mail.port"),
        ({"account": {"link": True, "link_code": "<script>"}}, "account.link_code"),
        ({"engine": "nomad"}, "engine"),
        ({"admin": {"first_name": "a\nb"}}, "admin.first_name"),
        ({"post_install": "x"}, "Unknown part"),
    ):
        refused = client.put("/api/v1/setup/draft", json=wrong, headers=headers)
        assert refused.status_code == 400 and why in refused.get_json()["detail"], (wrong, refused.get_json())


def test_the_draft_needs_the_setup_code(first_run: tuple[FlaskClient, str]) -> None:
    client, _ = first_run
    assert client.put("/api/v1/setup/draft", json={"step": "address"}, headers=ORIGIN).status_code == 403
    assert not (settings.manager_dir / "setup-draft.json").exists()


def test_the_test_email_uses_the_draft_mail_server(
    first_run: tuple[FlaskClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo.mail import smtp

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        smtp.Smtp,
        "send",
        lambda self, to, subject, text, html=None: sent.append((to, self.settings.smtp_host, subject)),
    )
    client, code = first_run
    headers = code_headers(code)
    client.put(
        "/api/v1/setup/draft",
        json={"mail": {"enabled": True, "host": "smtp.example.com", "port": 587, "security": "starttls",
                       "username": "no-reply@example.com", "sender": "Sudoo.sh <no-reply@example.com>"}},  # fmt: skip
        headers=headers,
    )
    assert client.post("/api/v1/setup/test-email", json={"to": "nope"}, headers=headers).status_code == 400
    answer = client.post("/api/v1/setup/test-email", json={"to": "ada@example.com"}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    assert sent == [("ada@example.com", "smtp.example.com", sent[0][2])]
    assert settings.smtp_host == ""  # the panel's own mail settings are unchanged
    for _ in range(4):
        client.post("/api/v1/setup/test-email", json={"to": "ada@example.com"}, headers=headers)
    assert client.post("/api/v1/setup/test-email", json={"to": "ada@example.com"}, headers=headers).status_code == 429


# ---------------------------------------------------------------- the final confirm


def test_the_final_confirm_creates_the_super_admin_then_applies_the_draft(
    first_run: tuple[FlaskClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, code = first_run
    headers = code_headers(code)
    client.put(
        "/api/v1/setup/draft",
        json={
            "language": "ar",
            "engine": "kubernetes",
            "name": {"kind": "domain", "domain": "panel.example.com"},
            "mail": {"enabled": True, "host": "smtp.example.com", "port": 587, "security": "starttls",
                     "username": "no-reply@example.com", "sender": "", "password": "app-password-2"},
            "github": {"connect": True},
        },  # fmt: skip
        headers=headers,
    )
    body = {
        "setup_code": code,
        "password": "violet-harbour-candle-58",
        "first_name": "Noor",
        "email": "noor@example.com",
        "licence": licence_acceptance(),
    }
    done = client.post("/api/v1/setup", json=body, headers=ORIGIN)
    try:
        assert done.status_code == 201, done.get_json()
        result = done.get_json()["data"]["setup"]
        assert set(result["settings"]) >= {"default_language", "panel_domain", "smtp_host", "smtp_password"}
        assert result["engine"] == {
            "chosen": "kubernetes",
            "current": "docker",
            "command": "sudo sudoo-install engine kubernetes",
        }
        assert result["github"] is True and result["linked"] is None
        assert settings.panel_domain == "panel.example.com" and settings.smtp_host == "smtp.example.com"
        assert settings.smtp_password == "app-password-2" and settings.default_language == "ar"
        assert not (settings.manager_dir / "setup-draft.json").exists()  # the draft is gone
    finally:
        live.change(
            dict.fromkeys(("default_language", "panel_domain", "smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_password", "mail_from"))  # fmt: skip
        )
        storage().delete(accounts.USERS, "noor")


def test_a_draft_the_panel_would_refuse_stops_the_confirm_before_the_account(
    first_run: tuple[FlaskClient, str],
) -> None:
    client, code = first_run
    client.put(
        "/api/v1/setup/draft", json={"exposure": "proxy", "name": {"kind": "server"}}, headers=code_headers(code)
    )
    body = {
        "setup_code": code,
        "password": "violet-harbour-candle-58",
        "first_name": "Sami",
        "email": "sami@example.com",
        "licence": licence_acceptance(),
    }
    refused = client.post("/api/v1/setup", json=body, headers=ORIGIN)
    assert refused.status_code == 400 and "Proxy needs" in refused.get_json()["detail"]
    assert accounts.get("sami") is None


def test_the_link_code_is_used_at_the_confirm_and_a_failure_is_reported(
    first_run: tuple[FlaskClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from sudoo.hub import marketplace

    def refuse(code: str) -> dict[str, Any]:
        raise RuntimeError("The link code has expired.")

    monkeypatch.setattr(marketplace, "link", refuse)
    client, code = first_run
    (settings.manager_dir / "installer" / "install-code").write_text("LINK_CODE=LINK-1234\n")
    body = {
        "setup_code": code,
        "password": "violet-harbour-candle-58",
        "first_name": "Rami",
        "email": "rami@example.com",
        "licence": licence_acceptance(),
    }
    done = client.post("/api/v1/setup", json=body, headers=ORIGIN)
    try:
        assert done.status_code == 201, done.get_json()
        assert done.get_json()["data"]["setup"]["link_error"] == "The link code has expired."
        assert not (settings.manager_dir / "installer" / "install-code").exists()  # used once
    finally:
        live.change({"marketplace": None, "default_language": None})
        storage().delete(accounts.USERS, "rami")


def test_every_setting_the_wizard_changes_can_be_changed_in_the_panel() -> None:
    draft = setup_wizard.defaults()
    draft.update(
        exposure="tunnel" if config.exposure_mode(settings.exposure) != "tunnel" else "direct",
        name={"kind": "managed", "domain": ""},
        account={"link": True, "link_code": "LINK-1234", "company_label": "acme"},
        mail={**draft["mail"], "enabled": True, "host": "smtp.example.com", "sender": "a@example.com"},
    )
    changes = setup_wizard.setting_changes(draft)
    assert set(changes) <= set(config.EDITABLE) | set(config.RESTART)


def test_the_setup_page_is_served(app: Flask, tmp_path: Path) -> None:
    """The routes are public only as far as the code check goes: listed in security.PUBLIC_API."""
    from sudoo.web import security

    for route in ("/api/v1/setup/server-check", "/api/v1/setup/draft", "/api/v1/setup/test-email"):
        assert any(path == route for _, path in security.PUBLIC_API)
