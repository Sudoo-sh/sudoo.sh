"""Task 18c: the real connect, TLS handshake and certificate check of guard.exchange, against a throwaway local TLS
server with a certificate made here (127.0.0.1 only, no outside network). Shows that the connection goes to the
pinned address while the certificate is checked for the host name, and that a wrong or untrusted certificate fails."""

from __future__ import annotations

import socket
import ssl
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from sudoo.notifications import guard

NAME = "api.models.example"


def _certificate(folder: Path, name: str) -> tuple[Path, Path]:
    key, cert = folder / f"{name}.key", folder / f"{name}.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
         "-days", "2", "-subj", f"/CN={name}", "-addext", f"subjectAltName=DNS:{name}"],
        check=True, capture_output=True,
    )  # fmt: skip
    return key, cert


class Server:
    def __init__(self, key: Path, cert: Path) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        self.context = context
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.errors: list[str] = []
        self.seen: list[bytes] = []
        self.sni: list[str | None] = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        def choose(_: ssl.SSLSocket, name: str | None, __: ssl.SSLContext) -> None:
            self.sni.append(name)

        self.context.sni_callback = choose
        while True:
            try:
                raw, _ = self.sock.accept()
            except OSError:
                return
            try:
                with self.context.wrap_socket(raw, server_side=True) as tls:
                    data = b""
                    while b"\r\n\r\n" not in data:
                        data += tls.recv(65536)
                    head, _, rest = data.partition(b"\r\n\r\n")
                    wanted = int(head.lower().split(b"content-length:")[1].split(b"\r\n")[0])
                    while len(rest) < wanted:
                        rest += tls.recv(65536)
                    self.seen.append(data)
                    body = b'{"choices":[]}'
                    tls.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body)
                    )
            except Exception as error:  # noqa: BLE001
                self.errors.append(repr(error))
                continue

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def certificate(tmp_path: Path) -> tuple[Path, Path]:
    return _certificate(tmp_path, NAME)


@pytest.fixture
def server(certificate: tuple[Path, Path]) -> Iterator[Server]:
    found = Server(*certificate)
    yield found
    found.close()


def _trusting(monkeypatch: pytest.MonkeyPatch, cert: Path) -> None:
    context = ssl.create_default_context()
    context.load_verify_locations(str(cert))
    monkeypatch.setattr(guard, "_CONTEXT", context)


def test_the_connection_goes_to_the_pinned_address_and_the_certificate_is_checked_for_the_host_name(
    monkeypatch: pytest.MonkeyPatch, server: Server, certificate: tuple[Path, Path]
) -> None:
    _trusting(monkeypatch, certificate[1])
    status, answer = guard.exchange(
        guard.Target(NAME, server.port, "/v1/x", "127.0.0.1"),
        "POST",
        b"{}",
        {"Content-Type": "application/json"},
        5,
        4096,
    )
    assert not server.errors, server.errors
    assert status == 200 and answer == b'{"choices":[]}'
    assert server.sni == [NAME]  # the name was sent and checked, though the socket went to 127.0.0.1
    assert server.seen[0].startswith(b"POST /v1/x HTTP/1.1") and f"Host: {NAME}:".encode() in server.seen[0]


def test_a_certificate_for_another_name_is_refused(
    monkeypatch: pytest.MonkeyPatch, server: Server, certificate: tuple[Path, Path]
) -> None:
    _trusting(monkeypatch, certificate[1])
    with pytest.raises(guard.DeliveryError) as error:
        guard.exchange(guard.Target("other.example", server.port, "/", "127.0.0.1"), "POST", b"{}", {}, 5, 4096)
    assert "127.0.0.1" not in str(error.value) and "other.example" not in str(error.value)


def test_an_untrusted_certificate_is_refused_by_the_default_context(server: Server) -> None:
    with pytest.raises(guard.DeliveryError):
        guard.exchange(guard.Target(NAME, server.port, "/", "127.0.0.1"), "POST", b"{}", {}, 5, 4096)


def test_a_server_that_never_answers_is_cut_at_the_total_deadline(
    monkeypatch: pytest.MonkeyPatch, certificate: tuple[Path, Path]
) -> None:
    import time

    _trusting(monkeypatch, certificate[1])
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)  # accepts at the TCP level, never speaks TLS
    try:
        started = time.monotonic()
        with pytest.raises(guard.DeliveryError):
            guard.exchange(guard.Target(NAME, silent.getsockname()[1], "/", "127.0.0.1"), "POST", b"{}", {}, 1.5, 4096)
        assert time.monotonic() - started < 4
    finally:
        silent.close()
