"""Task 18c: where an AI explanation may be sent. No real network: name resolution and the connection are faked."""

from __future__ import annotations

import socket
import time
from typing import Any

import pytest
from pydantic import ValidationError

from sudoo.config import Settings, settings
from sudoo.explain import destination
from sudoo.notifications import guard
from sudoo.notifications.guard import DeliveryError, Refused

URL = "https://api.models.example:443/v1/chat"
KEY = "sk-test-KEYKEYKEYKEYKEYKEYKEY"


def _resolve(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> list[int]:
    calls: list[int] = []

    def fake(host: str, port: int, type: int = 0, **_: Any) -> list[Any]:
        calls.append(1)
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, type, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return calls


def _allow(monkeypatch: pytest.MonkeyPatch, value: str, notifications: str = "") -> None:
    monkeypatch.setattr(settings, "explain_allowed_destinations", value)
    monkeypatch.setattr(settings, "notification_allowed_hosts", notifications)


def test_nothing_can_be_sent_while_the_list_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch, "")
    _resolve(monkeypatch, "93.184.216.34")
    with pytest.raises(Refused):
        destination.check(URL)


def test_a_host_that_is_not_listed_is_refused_even_if_public(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch, "other.example:443")
    _resolve(monkeypatch, "93.184.216.34")
    with pytest.raises(Refused, match="not on the list"):
        destination.check(URL)
    _allow(monkeypatch, "api.models.example:8443")  # the port counts: exact host:port
    with pytest.raises(Refused, match="not on the list"):
        destination.check(URL)


def test_a_listed_public_host_is_allowed_and_resolved_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch, "api.models.example:443")
    calls = _resolve(monkeypatch, "93.184.216.34")
    found = destination.check(URL)
    assert (found.host, found.port, found.address) == ("api.models.example", 443, "93.184.216.34")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "bad",
    ["169.254.169.254", "169.254.0.5", "fe80::1", "::ffff:169.254.169.254", "100.100.100.200", "192.0.0.192",
     "fd00:ec2::254", "0.0.0.0", "224.0.0.1", "::", "64:ff9b::a9fe:a9fe", "64:ff9b::7f00:1",
     "::169.254.169.254", "::a9fe:a9fe", "::7f00:1", "64:ff9b::0a00:1", "::c0a8:101", "64:ff9b::ac10:1", "::10.0.0.1", "2002:a9fe:a9fe::1"],
)  # fmt: skip
@pytest.mark.parametrize("entry", ["api.models.example:443", "local:api.models.example:443"])
def test_metadata_link_local_and_the_like_are_refused_even_when_listed_or_local(
    monkeypatch: pytest.MonkeyPatch, bad: str, entry: str
) -> None:
    _allow(monkeypatch, entry, notifications="api.models.example,169.254.0.0/16,100.64.0.0/10")
    _resolve(monkeypatch, bad)
    with pytest.raises(Refused):
        destination.check(URL)


def test_one_forbidden_address_among_public_ones_refuses_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch, "api.models.example:443")
    _resolve(monkeypatch, "93.184.216.34", "169.254.169.254")
    with pytest.raises(Refused):
        destination.check(URL)


def test_notification_exceptions_do_not_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch, "api.models.example:443", notifications="api.models.example,10.0.0.0/8")
    _resolve(monkeypatch, "10.1.2.3")  # a plain entry: private is refused whatever the notification list says
    with pytest.raises(Refused, match="private or local"):
        destination.check(URL)


@pytest.mark.parametrize(
    "private", ["10.1.2.3", "127.0.0.1", "::1", "192.168.1.20", "172.16.0.9", "100.64.0.1", "fd12::1"]
)
@pytest.mark.parametrize("port", [443, 9000, 11434, 5432])
def test_no_private_or_loopback_destination_is_in_scope_whatever_the_port(
    monkeypatch: pytest.MonkeyPatch, private: str, port: int
) -> None:
    _allow(monkeypatch, f"model.lan:{port}", notifications=f"model.lan,{private}")
    _resolve(monkeypatch, private)
    with pytest.raises(Refused, match="private or local"):
        destination.check(f"https://model.lan:{port}/x")


def test_the_panels_own_public_name_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from sudoo import exposure

    _allow(monkeypatch, "panel.example.com:443")
    monkeypatch.setattr(exposure, "address", lambda name: "https://panel.example.com")
    _resolve(monkeypatch, "93.184.216.34")
    with pytest.raises(Refused, match="panel's own"):
        destination.check("https://panel.example.com/x")


@pytest.mark.parametrize("url", ["http://api.models.example/x", "https://user:pw@api.models.example/x", "ftp://x"])
def test_only_https_without_credentials(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    _allow(monkeypatch, "api.models.example:443")
    _resolve(monkeypatch, "93.184.216.34")
    with pytest.raises(Refused):
        destination.check(url)


@pytest.mark.parametrize(
    "value",
    [
        "*.example.com:443",
        "10.0.0.0/8:443",
        "host",
        "host:0",
        "host:70000",
        "local:host",
        "local:ollama.lan:11434",
        "a b:443",
    ],
)
def test_the_setting_accepts_only_exact_host_port(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(explain_allowed_destinations=value)
    assert Settings(
        explain_allowed_destinations="Api.Example.com:443, b.example:8443"
    ).explain_allowed_destinations == ("api.example.com:443,b.example:8443")


# ----------------------------------------------------------------------------------------------- the call itself


class FakeConnection:
    answer: tuple[int, bytes] = (200, b'{"ok": true}')
    raises: Exception | None = None
    seen: list[Any] = []

    delay: float = 0.0
    timeouts: list[float] = []

    def __init__(self, target: guard.Target, timeout: float, context: Any) -> None:
        FakeConnection.seen = [target, timeout]
        FakeConnection.timeouts = []
        self.status = 0
        self._data = b""
        self.timeout = timeout
        self.sock: Any = None

    def connect(self) -> None:
        FakeConnection.timeouts.append(self.timeout)
        owner = self

        class Sock:
            def settimeout(self, value: float) -> None:
                FakeConnection.timeouts.append(value)

            def shutdown(self, how: int) -> None:
                owner._data = b""

        self.sock = Sock()

    def request(self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        if FakeConnection.raises:
            raise FakeConnection.raises
        FakeConnection.seen += [method, path, body, headers]
        self.status, self._data = FakeConnection.answer

    def getresponse(self) -> FakeConnection:
        return self

    def read(self, n: int = -1) -> bytes:
        time.sleep(FakeConnection.delay)
        out, self._data = self._data[:n], self._data[n:]
        return out

    def close(self) -> None:
        pass


@pytest.fixture
def fake_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch, "api.models.example:443")
    _resolve(monkeypatch, "93.184.216.34")
    monkeypatch.setattr(guard, "_PinnedHTTPS", FakeConnection)
    FakeConnection.answer, FakeConnection.raises, FakeConnection.delay = (200, b'{"ok": true}'), None, 0.0


def test_the_call_goes_to_the_checked_address_and_returns_the_body(fake_call: None) -> None:
    body = destination.post_json(URL, {"a": 1}, {"Authorization": f"Bearer {KEY}"})
    assert body == b'{"ok": true}'
    target = FakeConnection.seen[0]
    assert target.address == "93.184.216.34" and target.host == "api.models.example"
    assert FakeConnection.seen[2] == "POST" and FakeConnection.seen[3] == "/v1/chat"


def test_a_redirect_is_not_followed(fake_call: None) -> None:
    FakeConnection.answer = (302, b"")
    with pytest.raises(DeliveryError, match="not followed"):
        destination.post_json(URL, {}, {})


def test_an_answer_over_the_cap_is_refused(fake_call: None) -> None:
    FakeConnection.answer = (200, b"x" * (destination.MAX_ANSWER + 1))
    with pytest.raises(DeliveryError, match="too large"):
        destination.post_json(URL, {}, {})


def test_a_request_over_the_cap_is_refused_before_any_connection(fake_call: None) -> None:
    FakeConnection.seen = []
    with pytest.raises(Refused, match="too large"):
        destination.post_json(URL, {"text": "x" * (destination.MAX_PAYLOAD + 1)}, {})
    assert FakeConnection.seen == []


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_errors_never_carry_the_address_the_key_or_the_answer(fake_call: None, status: int) -> None:
    FakeConnection.answer = (status, f"echo {KEY} https://api.models.example/v1/chat secret answer".encode())
    with pytest.raises(DeliveryError) as raised:
        destination.post_json(URL, {}, {"Authorization": f"Bearer {KEY}"})
    text = str(raised.value)
    assert KEY not in text and "api.models" not in text and "secret answer" not in text


@pytest.mark.parametrize("error", [ConnectionRefusedError(f"{KEY} api.models.example"), TimeoutError(), OSError(KEY)])
def test_connection_errors_are_safe(fake_call: None, error: Exception) -> None:
    FakeConnection.raises = error
    with pytest.raises(DeliveryError) as raised:
        destination.post_json(URL, {}, {"Authorization": f"Bearer {KEY}"})
    assert KEY not in str(raised.value) and "api.models" not in str(raised.value)


def test_a_refused_destination_makes_no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch, "api.models.example:443")
    _resolve(monkeypatch, "169.254.169.254")
    monkeypatch.setattr(guard, "_PinnedHTTPS", FakeConnection)
    FakeConnection.seen = []
    with pytest.raises(Refused):
        destination.post_json(URL, {}, {})
    assert FakeConnection.seen == []


def test_the_total_time_bounds_every_read_not_only_the_gaps_between_chunks(fake_call: None) -> None:
    FakeConnection.answer = (200, b"x" * 20000)  # five 4 KB reads
    FakeConnection.delay = 0.05
    started = time.monotonic()
    with pytest.raises(DeliveryError, match="no complete answer"):
        destination.post_json(URL, {}, {}, timeout=0.12)
    assert time.monotonic() - started < 1.0


def test_connect_and_each_read_get_only_the_time_left(fake_call: None) -> None:
    destination.post_json(URL, {}, {}, timeout=5.0)
    assert FakeConnection.timeouts and all(0 < t <= 5.0 for t in FakeConnection.timeouts)
    assert FakeConnection.timeouts == sorted(FakeConnection.timeouts, reverse=True)  # the budget only shrinks


def test_notifications_use_the_same_pinned_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch, "")
    monkeypatch.setattr(settings, "notification_allowed_hosts", "")
    _resolve(monkeypatch, "93.184.216.34")
    monkeypatch.setattr(guard, "_PinnedHTTPS", FakeConnection)
    FakeConnection.answer, FakeConnection.raises, FakeConnection.delay = (
        (200, b"x" * (guard.MAX_ANSWER + 10)),
        None,
        0.0,
    )
    assert guard.post("https://hooks.example/x", b"{}", {}, 5.0) == 200  # an over-long answer is cut, as before
    assert FakeConnection.seen[0].address == "93.184.216.34"


@pytest.mark.parametrize("ip", ["64:ff9b::a9fe:a9fe", "64:ff9b::0a00:0001", "::10.0.0.1", "::192.168.1.1"])
def test_an_ipv4_address_inside_nat64_or_compatible_ipv6_is_judged_as_that_address(ip: str) -> None:
    import ipaddress

    from sudoo.notifications import guard

    assert guard.public(ipaddress.ip_address(ip)) is False
    assert guard.embedded(ipaddress.ip_address(ip)).version == 4
    assert guard.public(ipaddress.ip_address("64:ff9b::5db8:d822")) is True  # NAT64 of 93.184.216.34: public
    assert guard.embedded(ipaddress.ip_address("::1")).version == 6  # the loopback itself stays as it is


@pytest.mark.parametrize(
    "url",
    [
        "https://api.models.example/v1?key=abc",
        "https://api.models.example/v1#frag",
        "https://api.models.example/v1;x=1",
    ],
)
def test_an_address_with_a_query_string_is_refused(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    _allow(monkeypatch, "api.models.example:443")
    _resolve(monkeypatch, "93.184.216.34")
    with pytest.raises(destination.Refused, match="query string"):
        destination.check(url)
