"""Task 18c: settings, preview and confirmed send of "Explain with AI". No network: name resolution and the exchange
are faked; what the fake receives is what would have been sent."""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Iterator
from typing import Any

import pytest

from sudoo.config import settings
from sudoo.explain import service
from sudoo.explain.service import ExplainError
from sudoo.notifications import guard
from sudoo.storage import storage

ENDPOINT = "https://api.models.example/v1/chat/completions"
KEY = "sk-live-KEYKEYKEYKEYKEYKEYKEYKEY"
SECRET_LINE = "psycopg2.OperationalError: password=hunter2hunter2 for odoo@db"
ENTRY: dict[str, Any] = {
    "id": "b1",
    "branch": "feature/SECRETBRANCH",
    "status": "failed",
    "failure": {
        "step": "Loading the backup of acme-secret-instance into database db_SECRET",
        "reason": "r",
        "kind": "other",
        "details": ["line one", SECRET_LINE, "line three"],
        "rule": {"id": "port_in_use", "title": "A port is already in use"},
    },
}


class Exchange:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, bytes, dict[str, str]]] = []
        self.answer: tuple[int, bytes] = (200, json.dumps({"choices": [{"message": {"content": "Try X."}}]}).encode())

    def __call__(self, target: Any, method: str, body: bytes, headers: dict[str, str], timeout: float, max_answer: int,
                 strict_size: bool = False) -> tuple[int, bytes]:  # fmt: skip
        self.calls.append((target, body, headers))
        return self.answer


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for collection in (service.CONFIG, service.PREVIEWS, service.USAGE):
        for key in list(storage().all(collection)):
            storage().delete(collection, key)
    monkeypatch.setattr(settings, "explain_allowed_destinations", "api.models.example:443")
    monkeypatch.setattr(settings, "notification_allowed_hosts", "")
    monkeypatch.setattr(settings, "explain_user_limit_per_hour", 10)
    monkeypatch.setattr(settings, "explain_project_limit_per_hour", 30)

    def fake(host: str, port: int, type: int = 0, **_: Any) -> list[Any]:
        return [(socket.AF_INET, type, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    yield
    for collection in (service.CONFIG, service.PREVIEWS, service.USAGE):
        for key in list(storage().all(collection)):
            storage().delete(collection, key)


@pytest.fixture
def exchange(monkeypatch: pytest.MonkeyPatch) -> Exchange:
    fake = Exchange()
    monkeypatch.setattr(guard, "exchange", fake)
    return fake


def _on(project: str = "p1") -> None:
    service.configure(True, ENDPOINT, "model-1", KEY)
    service.set_project(project, True)


# --------------------------------------------------------------------------------------------------- settings


def test_off_by_default_nothing_can_be_previewed_or_sent(exchange: Exchange) -> None:
    assert service.availability("p1", ENTRY) == {"available": False, "reason": "off"}
    with pytest.raises(ExplainError):
        service.preview("u", "p1", ENTRY, "en")
    with pytest.raises(ExplainError):
        service.confirm("u", "p1", ENTRY, "0" * 32)
    assert exchange.calls == []


def test_the_endpoint_must_be_on_the_exact_list_and_the_key_is_never_shown() -> None:
    with pytest.raises(ExplainError, match="EXPLAIN_ALLOWED_DESTINATIONS"):
        service.configure(False, "https://other.example/v1", "m", None)
    with pytest.raises(ExplainError):
        service.configure(False, "http://api.models.example/v1", "m", None)
    with pytest.raises(ExplainError):
        service.configure(True, ENDPOINT, None, None)  # no model or key yet
    shown = service.configure(True, ENDPOINT, "model-1", KEY)
    assert shown["key_set"] is True and KEY not in json.dumps(shown) and "…" not in json.dumps(shown)
    stored = json.dumps(storage().get(service.CONFIG, "settings"))
    assert KEY not in stored  # encrypted at rest with the panel's key, never plain
    assert service.resource()["destination_problem"] is None


def test_every_change_makes_a_new_version() -> None:
    _on()
    first = service.resource()["version"]
    service.configure(True, None, "model-2", None)
    assert service.resource()["version"] == first + 1


def test_availability_reasons() -> None:
    assert service.availability("p1", ENTRY)["reason"] == "off"
    service.configure(True, ENDPOINT, "model-1", KEY)
    assert service.availability("p1", ENTRY)["reason"] == "project_off"
    service.set_project("p1", True)
    assert service.availability("p1", ENTRY) == {"available": True, "reason": None}
    assert service.availability("p1", {**ENTRY, "status": "success"})["reason"] == "not_failed"
    assert service.availability("p1", {**ENTRY, "failure": {"details": []}})["reason"] == "no_details"
    service.configure(False, None, None, None)
    assert service.availability("p1", ENTRY)["reason"] == "off"


# ----------------------------------------------------------------------------------------------- the payload


def test_the_payload_has_the_fixed_prompt_and_only_the_scrubbed_excerpt() -> None:
    payload, count = service.build_payload(ENTRY, "en", "model-1")
    text = json.dumps(payload, ensure_ascii=False)
    assert payload["messages"][0]["content"] == service.PROMPT["en"]
    assert "line one" in text and "line three" in text and "A port is already in use" in text
    assert "hunter2hunter2" not in text and count >= 1
    for private in ("SECRETBRANCH", "acme-secret-instance", "db_SECRET", "feature/"):
        assert private not in text  # no step title, branch, instance or database name


def test_the_excerpt_is_capped_in_lines_and_characters() -> None:
    many = {**ENTRY, "failure": {"details": [f"line {i} " + "x" * 300 for i in range(60)]}}
    payload, _ = service.build_payload(many, "en", "m")
    body = payload["messages"][1]["content"]
    assert body.count("line ") <= service.EXCERPT_LINES
    assert len(body) <= service.EXCERPT_CHARS + 200


def test_the_arabic_variant_only_changes_the_fixed_prompt() -> None:
    en, _ = service.build_payload(ENTRY, "en", "m")
    ar, _ = service.build_payload(ENTRY, "ar", "m")
    assert en["messages"][1] == ar["messages"][1] and "Arabic" in ar["messages"][0]["content"]
    with pytest.raises(ExplainError):
        service.build_payload(ENTRY, "fr", "m")


# ------------------------------------------------------------------------------------- preview and confirm


def test_the_preview_is_exactly_what_is_sent(exchange: Exchange) -> None:
    _on()
    shown = service.preview("u", "p1", ENTRY, "en")
    assert shown["host"] == "api.models.example:443" and shown["model"] == "model-1" and shown["hidden"] >= 1
    answer = service.confirm("u", "p1", ENTRY, shown["id"])
    assert len(exchange.calls) == 1
    target, body, headers = exchange.calls[0]
    assert json.loads(body) == json.loads(shown["payload"])  # byte for byte the same content
    assert len(body) == shown["bytes"]
    assert target.address == "93.184.216.34" and headers["Authorization"] == f"Bearer {KEY}"
    assert answer["text"] == "Try X." and answer["generated"] is True


def test_a_preview_works_once(exchange: Exchange) -> None:
    _on()
    shown = service.preview("u", "p1", ENTRY, "en")
    service.confirm("u", "p1", ENTRY, shown["id"])
    with pytest.raises(ExplainError, match="already used"):
        service.confirm("u", "p1", ENTRY, shown["id"])
    assert len(exchange.calls) == 1


def test_a_preview_is_for_one_person_one_project_one_build(exchange: Exchange) -> None:
    _on()
    service.set_project("p2", True)
    for who, project, entry in (("other", "p1", ENTRY), ("u", "p2", ENTRY), ("u", "p1", {**ENTRY, "id": "b2"})):
        shown = service.preview("u", "p1", ENTRY, "en")
        with pytest.raises(ExplainError, match="not for this build or person"):
            service.confirm(who, project, entry, shown["id"])
    assert exchange.calls == []


def test_a_preview_expires(exchange: Exchange, monkeypatch: pytest.MonkeyPatch) -> None:
    _on()
    shown = service.preview("u", "p1", ENTRY, "en")
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + service.PREVIEW_SECONDS + 1)
    with pytest.raises(ExplainError, match="expired"):
        service.confirm("u", "p1", ENTRY, shown["id"])
    assert exchange.calls == []


@pytest.mark.parametrize(
    "what", ["model", "endpoint", "key", "project_off", "disabled", "build_changed", "destination_removed"]
)
def test_a_change_after_the_preview_stops_the_send(
    exchange: Exchange, monkeypatch: pytest.MonkeyPatch, what: str
) -> None:
    _on()
    shown = service.preview("u", "p1", ENTRY, "en")
    entry = ENTRY
    if what == "model":
        service.configure(True, None, "model-2", None)
    elif what == "endpoint":
        monkeypatch.setattr(settings, "explain_allowed_destinations", "api.models.example:443,b.example:443")
        service.configure(True, "https://b.example/v1", None, None)
    elif what == "key":
        service.configure(True, None, None, "sk-new-NEWNEWNEWNEWNEWNEWNEW")
    elif what == "project_off":
        service.set_project("p1", False)
    elif what == "disabled":
        service.configure(False, None, None, None)
    elif what == "build_changed":
        entry = {**ENTRY, "failure": {**ENTRY["failure"], "details": ["different line"]}}
    elif what == "destination_removed":
        monkeypatch.setattr(settings, "explain_allowed_destinations", "")
    with pytest.raises(ExplainError):
        service.confirm("u", "p1", entry, shown["id"])
    assert exchange.calls == []


def test_a_destination_that_now_resolves_to_a_forbidden_address_is_refused_at_confirm(
    exchange: Exchange, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on()
    shown = service.preview("u", "p1", ENTRY, "en")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, type=0, **_: [(socket.AF_INET, type, 6, "", ("169.254.169.254", port))],
    )
    with pytest.raises(ExplainError):
        service.confirm("u", "p1", ENTRY, shown["id"])
    assert exchange.calls == []


def test_a_bad_preview_id_is_refused_without_a_lookup_of_anything_else(exchange: Exchange) -> None:
    _on()
    for bad in ("", "x", "../etc/passwd", "0" * 31, "G" * 32):
        with pytest.raises(ExplainError):
            service.confirm("u", "p1", ENTRY, bad)


# --------------------------------------------------------------------------------------------------- limits


def test_one_at_a_time_and_released_after_a_failure(exchange: Exchange) -> None:
    _on()
    service._reserve("u", "p1")
    with pytest.raises(ExplainError, match="Another explanation"):
        service._reserve("v", "p2")
    service._release()
    service._reserve("v", "p2")
    service._release()
    exchange.answer = (500, b"")
    shown = service.preview("u", "p1", ENTRY, "en")
    with pytest.raises(ExplainError):
        service.confirm("u", "p1", ENTRY, shown["id"])
    service._reserve("w", "p3")  # the failed call released its place
    service._release()


def test_per_person_and_per_project_limits(exchange: Exchange, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "explain_user_limit_per_hour", 2)
    monkeypatch.setattr(settings, "explain_project_limit_per_hour", 3)
    for _ in range(2):
        service._reserve("u", "p1")
        service._release()
    with pytest.raises(ExplainError, match="most explanations"):
        service._reserve("u", "p9")
    service._reserve("v", "p1")
    service._release()
    with pytest.raises(ExplainError, match="This project"):
        service._reserve("w", "p1")


# ----------------------------------------------------------------------------------------------- the answer


def test_the_answer_is_plain_text_capped_and_not_stored(exchange: Exchange) -> None:
    _on()
    evil = "<script>alert(1)</script> [click](http://x)\x00\x07" + "z" * 9000
    exchange.answer = (200, json.dumps({"choices": [{"message": {"content": evil}}]}).encode())
    shown = service.preview("u", "p1", ENTRY, "en")
    answer = service.confirm("u", "p1", ENTRY, shown["id"])
    assert "\x00" not in answer["text"] and "\x07" not in answer["text"] and len(answer["text"]) <= service.ANSWER_CHARS
    assert answer["text"].startswith("<script>")  # returned as text: the page shows it as text and links nothing
    for collection in (service.CONFIG, service.PREVIEWS, service.USAGE):
        assert "zzzz" not in json.dumps(storage().all(collection))


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",
        json.dumps({"choices": []}).encode(),
        json.dumps({"choices": [{"message": {"content": ""}}]}).encode(),
    ],
)
def test_an_answer_in_another_form_is_a_clear_error(exchange: Exchange, body: bytes) -> None:
    _on()
    exchange.answer = (200, body)
    shown = service.preview("u", "p1", ENTRY, "en")
    with pytest.raises(ExplainError) as raised:
        service.confirm("u", "p1", ENTRY, shown["id"])
    assert KEY not in str(raised.value) and "api.models" not in str(raised.value)


def test_errors_carry_no_key_address_or_answer(exchange: Exchange) -> None:
    _on()
    exchange.answer = (401, f"bad key {KEY} at {ENDPOINT}".encode())
    shown = service.preview("u", "p1", ENTRY, "en")
    with pytest.raises(ExplainError) as raised:
        service.confirm("u", "p1", ENTRY, shown["id"])
    assert KEY not in str(raised.value) and "api.models" not in str(raised.value) and "bad key" not in str(raised.value)


# ------------------------------------------------------------------------------------- findings of the review


@pytest.mark.parametrize("key", ["sk-\u00e9\u00e9\u00e9", "\u0645\u0641\u062a\u0627\u062d", "abc\x7fdef", "key\u2028x"])
def test_a_key_the_connection_cannot_carry_is_refused_when_set(key: str) -> None:
    with pytest.raises(ExplainError, match="key is not valid"):
        service.configure(False, None, None, key)


def test_a_header_error_at_send_is_a_clear_error_without_the_text_and_frees_the_place(
    exchange: Exchange, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on()

    def broken(*_: Any, **__: Any) -> tuple[int, bytes]:
        raise UnicodeEncodeError("latin-1", KEY, 0, 1, f"cannot encode {KEY}")

    monkeypatch.setattr(guard, "exchange", broken)
    shown = service.preview("u", "p1", ENTRY, "en")
    with pytest.raises(ExplainError) as error:
        service.confirm("u", "p1", ENTRY, shown["id"])
    assert KEY not in str(error.value) and error.value.status == 409
    service._reserve("v", "p2")  # not left busy
    service._release()


@pytest.mark.parametrize("path", ["?key=abc", "#x", ";a=b"])
def test_an_endpoint_with_a_query_is_refused_when_set(path: str) -> None:
    with pytest.raises(ExplainError, match="query string"):
        service.configure(False, ENDPOINT + path, None, None)
    assert service.config()["endpoint"] == ""


def test_previews_are_limited_per_person_and_in_all(monkeypatch: pytest.MonkeyPatch, exchange: Exchange) -> None:
    _on()
    for _ in range(service.PREVIEWS_PER_USER):
        service.preview("u", "p1", ENTRY, "en")
    with pytest.raises(ExplainError) as error:
        service.preview("u", "p1", ENTRY, "en")
    assert error.value.status == 429
    service.preview("other", "p1", ENTRY, "en")  # another person is not held up
    assert len(storage().all(service.PREVIEWS)) == service.PREVIEWS_PER_USER + 1
    monkeypatch.setattr(service, "PREVIEWS_IN_ALL", 3)
    with pytest.raises(ExplainError) as error:
        service.preview("third", "p1", ENTRY, "en")
    assert error.value.status == 429
    # used and expired ones do not count
    for key, doc in list(storage().all(service.PREVIEWS).items()):
        storage().put(service.PREVIEWS, key, {**doc, "used": True})
    service.preview("u", "p1", ENTRY, "en")


def test_a_busy_or_rate_limited_answer_does_not_use_up_the_preview(exchange: Exchange) -> None:
    _on()
    shown = service.preview("u", "p1", ENTRY, "en")
    service._reserve("v", "p2")  # somebody else is being answered
    with pytest.raises(ExplainError) as error:
        service.confirm("u", "p1", ENTRY, shown["id"])
    assert error.value.status == 429 and exchange.calls == []
    service._release()
    done = service.confirm("u", "p1", ENTRY, shown["id"])  # the same preview still works, once
    assert done["generated"] is True and len(exchange.calls) == 1


def test_many_at_once_on_one_preview_send_exactly_once(exchange: Exchange) -> None:
    import threading

    _on()
    shown = service.preview("u", "p1", ENTRY, "en")
    results: list[str] = []
    start = threading.Barrier(8)

    def go() -> None:
        start.wait()
        try:
            service.confirm("u", "p1", ENTRY, shown["id"])
            results.append("sent")
        except ExplainError as error:
            results.append(str(error.status))

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(exchange.calls) == 1 and results.count("sent") == 1, results
