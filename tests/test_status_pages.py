"""The gateway's pages while an instance is not available, and its public status address (status_pages.py): English
and Arabic, the same 503 + Retry-After for API clients as before, live progress that shows nothing but the step."""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from sudoo import exposure
from sudoo.core import jobs
from sudoo.core.state import STATE
from sudoo.instances import gateway, status_pages, store

KINDS = ("waking", "waiting", "busy", "upgrading", "off", "locked", "gone", "full")
ALLOWED_KEYS = {"state", "step", "progress", "percent", "overall", "steps", "eta_seconds"}


def _request(accept: str = "text/html", language: str = "", method: str = "GET") -> web.Request:
    return make_mocked_request(method, "/odoo", headers={"Accept": accept, "Accept-Language": language})


@pytest.mark.parametrize("kind", KINDS)
def test_api_clients_get_the_same_plain_503_as_before(kind: str) -> None:
    answer = status_pages.render(_request("application/json"), "acme", kind, since=time.time())
    title, text = status_pages._PAGES[kind]
    assert answer.status == 503 and answer.headers["Retry-After"] == "4"
    assert answer.text == f"{title}. {text}" and answer.content_type == "text/plain"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("language", ["en-GB,en;q=0.9", "ar-PS,ar;q=0.9,en;q=0.5"])
def test_browsers_get_a_page_in_their_language(kind: str, language: str) -> None:
    answer = status_pages.render(_request(language=language), "acme-main", kind, since=time.time() - 12)
    page = answer.text or ""
    arabic = language.startswith("ar")
    assert answer.status == 503 and answer.headers["Retry-After"] == "4"
    assert ('dir="rtl"' in page) is arabic and f'lang="{"ar" if arabic else "en"}"' in page
    title = (status_pages._PAGES_AR if arabic else status_pages._PAGES)[kind][0]
    assert f"<h1>{title}</h1>" in page and "acme-main" in page
    assert "<svg" in page and "prefers-reduced-motion" in page and "prefers-color-scheme:dark" in page
    assert "#714b67" in page
    assert not re.search(r"(src|href)=\"https?://", page)  # nothing loaded from anywhere
    assert len(page.encode()) < 20_000
    nonce = re.search(r"'nonce-([^']+)'", answer.headers["Content-Security-Policy"])
    assert nonce and f'nonce="{nonce.group(1)}"' in page
    waiting = kind in status_pages.WAITING_KINDS
    assert ("<script" in page) is waiting and ('http-equiv="refresh"' in page) is waiting


def test_the_language_follows_accept_language() -> None:
    assert status_pages.language("") == "en"
    assert status_pages.language("fr-FR,fr;q=0.9") == "en"
    assert status_pages.language("fr,ar;q=0.8,en;q=0.7") == "ar"
    assert status_pages.language("en;q=0.9,ar;q=0.8") == "en"
    assert status_pages.language("ar;q=0,en") == "en"


def test_the_page_shows_the_jobs_progress() -> None:
    progress = {
        "step": "Downloading Odoo 19.0", "progress": "2 of 5 parts", "percent": 40, "overall": 30,
        "steps": {"current": 2, "total": 6}, "eta_seconds": 150,
    }  # fmt: skip
    page = status_pages.render(_request(), "acme", "busy", since=time.time() - 30, progress=progress).text or ""
    assert "Downloading Odoo 19.0" in page and "2 of 5 parts" in page and "--w:30%" in page
    assert "Step 2 of 6" in page and "About 2 min left" in page and "Waiting for 30 s" in page
    arabic = status_pages.render(_request(language="ar"), "acme", "busy", progress=progress).text or ""
    assert "الخطوة 2 من 6" in arabic and "يتبقى نحو 2 د" in arabic


def test_a_long_wait_names_the_administrator() -> None:
    page = status_pages.render(_request(), "acme", "waking", since=time.time() - 400).text or ""
    assert "This is taking longer than usual" in page and "Please contact the administrator" in page


def test_the_blocked_page() -> None:
    page = status_pages.render_blocked(
        _request(language="ar"), "acme-main", country="PS", address=None, reference="1A2B3C4D"
    )
    text = page.text or ""
    assert page.status == 403 and 'dir="rtl"' in text and "هذا الموقع غير متاح من موقعك أو شبكتك" in text
    assert "🇵🇸 PS" in text and "1A2B3C4D" in text and "<svg" in text
    english = status_pages.render_blocked(_request(), "acme-main", country=None, address="203.0.113.9", reference="X")
    body = english.text or ""
    assert "This site isn&#x27;t available from your location / network" in body
    assert "203.0.113.9" in body and "Unknown" in body
    plain = status_pages.render_blocked(_request("*/*"), "acme", country="JO", address=None, reference="R1")
    assert plain.status == 403 and (plain.text or "").endswith("Reference: R1.")
    custom = status_pages.render_blocked(
        _request(), "acme", country="JO", address=None, reference="R", contact="Call Huda"
    )
    assert "Call Huda" in (custom.text or "")


class _Job:
    """The parts of a job the public progress may read; the rest must never come out."""

    def __init__(self) -> None:
        self.kind, self.instance, self.status = "update", "acme", "running"
        self.started, self.finished = time.time() - 20, None
        self.expected_steps = 4
        self.user = "secret-person"
        self.log = ["password=hunter2"]
        self.notes = [{"text": "private note"}]
        self.steps = [
            {"title": "Getting the code", "status": "done"},
            {
                "title": "Installing modules",
                "status": "running",
                "progress": "Setting up the module sale",
                "percent": 55,
            },
        ]

    def progress_percent(self) -> int:
        return 25


def test_public_progress_shows_only_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _Job()
    finished = jobs.Job("acme", "Update", "update", "someone")
    finished.status, finished.started, finished.finished = "success", 100.0, 190.0
    monkeypatch.setattr(jobs, "JOBS", {"old": finished})
    found = status_pages.public_progress(job)
    assert found == {
        "step": "Installing modules", "progress": "Setting up the module sale", "percent": 55, "overall": 25,
        "steps": {"current": 2, "total": 4}, "eta_seconds": found["eta_seconds"] if found else None,
    }  # fmt: skip
    assert found and 60 < found["eta_seconds"] <= 70  # 90 s last time, 20 s gone
    text = repr(found)
    assert "secret-person" not in text and "hunter2" not in text and "private note" not in text
    assert status_pages.status_json(status_pages.Situation("busy", 0, found)).keys() <= ALLOWED_KEYS


def test_progress_from_the_panel_is_cleaned() -> None:
    raw = {
        "step": "x" * 500, "progress": 7, "percent": 140, "overall": 50, "steps": {"current": 1, "total": 3},
        "eta_seconds": -1, "log": "secret",
    }  # fmt: skip
    cleaned = status_pages.clean_progress(raw)
    assert cleaned == {
        "step": "x" * 120, "progress": None, "percent": None, "overall": 50,
        "steps": {"current": 1, "total": 3}, "eta_seconds": None,
    }  # fmt: skip
    assert status_pages.clean_progress({"progress": "no step"}) is None


# ---------------------------------------------------------------- the public status address, through the gateway

NAME = "sp-test"


def _run(scenario: Callable[[TestClient[web.Request, web.Application]], Awaitable[None]], odoo_up: bool) -> None:
    async def main() -> None:
        odoo = TestServer(web.Application())
        await odoo.start_server()
        port = odoo.port
        if not odoo_up:
            await odoo.close()
        folder = store.folder(NAME)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "docker-compose.yml").write_text("services: {}\n")
        store.write_env(NAME, {"ODOO_PORT": port, "GATEWAY_PORT": 1})
        await gateway._create_session()
        app = web.Application()
        app[gateway.INSTANCE] = NAME
        app.router.add_route("*", "/{tail:.*}", gateway._handle)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            await scenario(client)
        finally:
            await client.close()
            assert gateway._SESSION is not None
            await gateway._SESSION.close()
            if odoo_up:
                await odoo.close()
            shutil.rmtree(folder)
            STATE.remove(NAME)
            gateway._STATUS_ASKS.clear()

    asyncio.run(main())


@pytest.fixture
def edge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exposure, "provider", lambda: type("Edge", (), {"https": False, "client_ip_header": None})())


def test_the_status_address_tells_the_state_and_progress_only(monkeypatch: pytest.MonkeyPatch, edge: None) -> None:
    job = _Job()

    def decision(name: str) -> tuple[str, float]:
        return "busy", time.time() - 5

    monkeypatch.setattr(gateway, "wake_decision", decision)
    monkeypatch.setattr(jobs, "ACTIVE_JOB", {NAME: job})

    async def scenario(client: TestClient[web.Request, web.Application]) -> None:
        answer = await client.get("/__sudoo/status")
        data = await answer.json()
        assert answer.status == 200 and answer.headers["Cache-Control"] == "no-store"
        assert set(data) <= ALLOWED_KEYS and data["state"] == "busy" and data["step"] == "Installing modules"
        assert "secret-person" not in repr(data) and "hunter2" not in repr(data)
        assert (await client.post("/__sudoo/status")).status == 405
        page = await client.get("/odoo", headers={"Accept": "text/html"})
        assert page.status == 503 and "Installing modules" in await page.text()

    _run(scenario, odoo_up=False)


def test_the_status_address_says_ready_and_is_rate_limited(monkeypatch: pytest.MonkeyPatch, edge: None) -> None:
    monkeypatch.setattr(gateway, "STATUS_ASKS_PER_MINUTE", 3)

    async def scenario(client: TestClient[web.Request, web.Application]) -> None:
        answers = [await client.get("/__sudoo/status") for _ in range(4)]
        assert [a.status for a in answers] == [200, 200, 200, 429]
        assert await answers[0].json() == {"state": "ready"}

    _run(scenario, odoo_up=True)


def test_the_gateway_service_passes_on_the_panels_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo.instances import gateway_service

    host = gateway_service.ServiceHost()

    class Answer:
        async def __aenter__(self) -> Answer:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        async def json(self) -> dict[str, Any]:
            return {"page": "busy", "since": 5.0, "progress": {"step": "Restarting Odoo", "log": "no", "percent": 9}}

    class Session:
        def post(self, url: str) -> Answer:
            return Answer()

    host._panel = Session()  # type: ignore[assignment]
    found = asyncio.run(host.situation("acme"))
    assert found.page == "busy" and found.progress and found.progress["step"] == "Restarting Odoo"
    assert "log" not in found.progress


def test_step_and_progress_are_shown_in_arabic_to_an_arabic_visitor() -> None:
    progress = {
        "step": "Starting Odoo", "progress": "Downloading Odoo 19.0: 2 of 5 parts", "percent": 40, "overall": 30,
        "steps": {"current": 2, "total": 6}, "eta_seconds": 100,
    }  # fmt: skip
    arabic = status_pages.render(_request(language="ar"), "acme", "busy", progress=progress).text or ""
    assert "تشغيل Odoo" in arabic and "تنزيل \u2066Odoo 19.0\u2069: 2 من 5 أجزاء" in arabic
    assert "Starting Odoo" not in arabic and "Downloading Odoo 19.0: 2 of 5 parts" not in arabic
    english = status_pages.render(_request(language="en"), "acme", "busy", progress=progress).text or ""
    assert "Starting Odoo" in english and "Downloading Odoo 19.0: 2 of 5 parts" in english
    situation = status_pages.Situation("busy", 0, progress)
    assert status_pages.status_json(situation)["step"] == "Starting Odoo"  # what the page polls: English as written
    shown = status_pages.status_json(situation, "ar")
    assert shown["step"] == "تشغيل Odoo" and shown["progress"] == "تنزيل \u2066Odoo 19.0\u2069: 2 من 5 أجزاء"
    assert shown.keys() <= ALLOWED_KEYS
