"""The mail catcher service (`sudoo.sh mailcatcher`, container sudoo-mail): a small SMTP server that files every
e-mail it is given in the sending instance's mailbox and sends nothing anywhere (see __init__.py).

Built in rather than a third-party catcher (Mailpit, MailHog): it is the panel's own image (nothing more to
download, pin or keep up to date), it files each e-mail by the instance that authenticated, which is what keeps the
instances' mail apart, and the panel reads the mailboxes as plain files instead of through another API.

SMTP as Odoo speaks it (RFC 5321): EHLO/HELO, AUTH PLAIN and LOGIN (required before MAIL), MAIL FROM (with SIZE),
RCPT TO, DATA, RSET, NOOP, VRFY, QUIT. No TLS: the catcher is reached only over the instances' own Docker networks.
Limits: the largest e-mail (MAIL_CATCHER_MAX_MESSAGE_MB, from catcher.json), 1000 recipients, 64 KB per line, 64
connections, 5 minutes without a command, 3 wrong passwords per connection, 20 wrong commands.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import hmac
import json
import os
import re
import signal
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from sudoo.config import settings
from sudoo.instances.store import NAME_RE

from . import FORMAT, HOST, PORT, SETTINGS_FILE, mailbox

MAX_LINE = 65536
MAX_RECIPIENTS = 1000
MAX_CONNECTIONS = 64
IDLE_SECONDS = 300.0
MAX_AUTH_FAILURES = 3
MAX_ERRORS = 20
DEFAULT_LIMITS = {"max_message_bytes": 25 * 1024**2, "max_messages": 500, "max_bytes": 200 * 1024**2}
_ADDRESS_RE = re.compile(r"^\s*(FROM|TO):\s*<([^<>]{0,320})>(.*)$", re.IGNORECASE)


@dataclass
class Config:
    """catcher.json as last read: the limits and the mailboxes (instance -> SHA-256 of its password)."""

    limits: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    mailboxes: dict[str, str] = field(default_factory=dict)
    mtime: float = -1.0

    def refresh(self) -> None:
        path = settings.mail_catcher_dir / SETTINGS_FILE
        try:
            mtime = path.stat().st_mtime
            if mtime == self.mtime:
                return
            found = json.loads(path.read_text())
        except (OSError, ValueError):
            return  # not written yet, or being written: keep the last valid one
        if not isinstance(found, dict) or found.get("format") != FORMAT:
            return
        limits = {key: int(found.get("limits", {}).get(key, value)) for key, value in DEFAULT_LIMITS.items()}
        boxes = found.get("mailboxes", {})
        if not isinstance(boxes, dict):
            return
        self.limits = limits
        self.mailboxes = {str(k): str(v) for k, v in boxes.items() if NAME_RE.match(str(k)) and isinstance(v, str)}
        self.mtime = mtime

    def check(self, user: str, password: str) -> str | None:
        """The instance whose mailbox `user` and `password` open, or None."""
        self.refresh()
        expected = self.mailboxes.get(user)
        digest = hashlib.sha256(password.encode(errors="surrogateescape")).hexdigest()
        if expected is None or not hmac.compare_digest(expected, digest):
            return None
        return user


CONFIG = Config()


class TooLong(Exception):
    pass


class Session:
    """One SMTP connection."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer
        self.peer = str((writer.get_extra_info("peername") or ("?",))[0])
        self.instance: str | None = None
        self.auth_failures = self.errors = 0
        self.reset()

    def reset(self) -> None:
        self.mail_from: str | None = None
        self.rcpt_to: list[str] = []

    async def reply(self, text: str) -> None:
        self.writer.write(text.encode() + b"\r\n")
        await self.writer.drain()

    async def line(self) -> bytes:
        try:
            data = await asyncio.wait_for(self.reader.readuntil(b"\n"), IDLE_SECONDS)
        except asyncio.LimitOverrunError as error:
            raise TooLong from error
        except asyncio.IncompleteReadError as error:
            raise ConnectionResetError from error
        return data

    async def run(self) -> None:
        await self.reply(f"220 {HOST} ESMTP Sudoo.sh mail catcher: nothing is sent on")
        while True:
            raw = (await self.line()).rstrip(b"\r\n")
            text = raw.decode("utf-8", errors="replace")
            verb, _, argument = text.partition(" ")
            verb = verb.upper()
            handler = getattr(self, f"do_{verb.lower()}", None) if verb.isalpha() else None
            if handler is None:
                await self.error("500 5.5.2 Command not recognised")
            elif await handler(argument.strip()) is False:
                return
            if self.errors >= MAX_ERRORS:
                await self.reply("421 4.7.0 Too many errors, closing")
                return

    async def error(self, text: str) -> None:
        self.errors += 1
        await self.reply(text)

    # ------------------------------------------------------------------ commands

    async def do_helo(self, argument: str) -> None:
        self.reset()
        await self.reply(f"250 {HOST}")

    async def do_ehlo(self, argument: str) -> None:
        self.reset()
        size = CONFIG.limits["max_message_bytes"]
        for text in (f"250-{HOST}", f"250-SIZE {size}", "250-8BITMIME", "250-SMTPUTF8", "250 AUTH PLAIN LOGIN"):
            await self.reply(text)

    async def do_auth(self, argument: str) -> bool | None:
        if self.instance:
            await self.error("503 5.5.1 Already authenticated")
            return None
        mechanism, _, initial = argument.partition(" ")
        mechanism = mechanism.upper()
        try:
            if mechanism == "PLAIN":
                if not initial:
                    await self.reply("334 ")
                    initial = (await self.line()).decode(errors="replace").strip()
                parts = _b64(initial).split(b"\0")
                if len(parts) != 3:
                    raise ValueError
                user, password = parts[1].decode(), parts[2].decode()
            elif mechanism == "LOGIN":
                if initial:
                    user = _b64(initial).decode()
                else:
                    await self.reply("334 VXNlcm5hbWU6")
                    user = _b64((await self.line()).decode(errors="replace").strip()).decode()
                await self.reply("334 UGFzc3dvcmQ6")
                password = _b64((await self.line()).decode(errors="replace").strip()).decode()
            else:
                await self.error("504 5.5.4 Use AUTH PLAIN or AUTH LOGIN")
                return None
        except (ValueError, UnicodeDecodeError):
            await self.error("501 5.5.2 Cannot decode the answer")
            return None
        self.instance = CONFIG.check(user, password)
        if self.instance:
            await self.reply("235 2.7.0 Authentication successful")
            return None
        self.auth_failures += 1
        if self.auth_failures >= MAX_AUTH_FAILURES:
            await self.reply("421 4.7.0 Too many failed authentications, closing")
            return False
        await self.error("535 5.7.8 Authentication credentials invalid")
        return None

    async def do_mail(self, argument: str) -> None:
        if not self.instance:
            await self.error("530 5.7.0 Authentication required")
            return
        if self.mail_from is not None:
            await self.error("503 5.5.1 Sender already given")
            return
        match = _ADDRESS_RE.match(argument)
        if not match or match.group(1).upper() != "FROM":
            await self.error("501 5.5.4 Syntax: MAIL FROM:<address>")
            return
        size = re.search(r"\bSIZE=(\d+)", match.group(3), re.IGNORECASE)
        if size and int(size.group(1)) > CONFIG.limits["max_message_bytes"]:
            await self.error("552 5.3.4 Message too big for the mail catcher")
            return
        self.mail_from = match.group(2)
        await self.reply("250 2.1.0 OK")

    async def do_rcpt(self, argument: str) -> None:
        if self.mail_from is None:
            await self.error("503 5.5.1 Need MAIL first")
            return
        match = _ADDRESS_RE.match(argument)
        if not match or match.group(1).upper() != "TO" or not match.group(2):
            await self.error("501 5.5.4 Syntax: RCPT TO:<address>")
            return
        if len(self.rcpt_to) >= MAX_RECIPIENTS:
            await self.error("452 4.5.3 Too many recipients")
            return
        self.rcpt_to.append(match.group(2))
        await self.reply("250 2.1.5 OK")

    async def do_data(self, argument: str) -> None:
        if not self.rcpt_to or self.instance is None:
            await self.error("503 5.5.1 Need RCPT first")
            return
        await self.reply("354 End data with <CR><LF>.<CR><LF>")
        limit = CONFIG.limits["max_message_bytes"]
        chunks: list[bytes] = []
        size, too_big = 0, False
        while True:
            line = await self.line()
            if line in (b".\r\n", b".\n"):
                break
            if line.startswith(b"."):
                line = line[1:]  # dot-stuffing (RFC 5321 4.5.2)
            size += len(line)
            if size > limit:
                too_big, chunks = True, []
            elif not too_big:
                chunks.append(line)
        instance, envelope = self.instance, {
            "received_at": time.time(),
            "mail_from": self.mail_from,
            "rcpt_to": list(self.rcpt_to),
        }
        self.reset()
        if too_big:
            await self.reply("552 5.3.4 Message too big for the mail catcher")
            return
        data = b"".join(chunks)
        try:
            message_id = await asyncio.get_running_loop().run_in_executor(
                None, mailbox.deliver, instance, data, envelope, CONFIG.limits
            )
        except (OSError, ValueError) as error:
            print(f"mailcatcher: could not file an e-mail for {instance}: {error!r}", flush=True)
            await self.reply("451 4.3.0 Could not store the message, try again later")
            return
        print(f"mailcatcher: caught {message_id} for {instance} ({len(data)} bytes)", flush=True)
        await self.reply(f"250 2.0.0 OK: caught as {message_id}")

    async def do_rset(self, argument: str) -> None:
        self.reset()
        await self.reply("250 2.0.0 OK")

    async def do_noop(self, argument: str) -> None:
        await self.reply("250 2.0.0 OK")

    async def do_vrfy(self, argument: str) -> None:
        await self.reply("252 2.1.5 Cannot verify, but will take it")

    async def do_starttls(self, argument: str) -> None:
        await self.error("454 4.7.0 TLS not available")

    async def do_quit(self, argument: str) -> bool:
        await self.reply("221 2.0.0 Bye")
        return False


def _b64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError from error


_connections = 0


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    global _connections
    _connections += 1
    session = Session(reader, writer)
    try:
        if _connections > MAX_CONNECTIONS:
            await session.reply("421 4.3.2 Too many connections, try again later")
            return
        CONFIG.refresh()
        await session.run()
    except TooLong:
        with contextlib.suppress(OSError):
            await session.reply("500 5.5.6 Line too long, closing")
    except (TimeoutError, asyncio.TimeoutError):
        with contextlib.suppress(OSError):
            await session.reply("421 4.4.2 Timeout, closing")
    except (ConnectionError, OSError):
        pass
    finally:
        _connections -= 1
        writer.close()
        with contextlib.suppress(OSError, ConnectionError):
            await writer.wait_closed()


async def serve(host: str = "0.0.0.0", port: int = PORT) -> None:  # noqa: S104 - inside its own container only
    settings.mail_catcher_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    CONFIG.refresh()
    server = await asyncio.start_server(handle, host, port, limit=MAX_LINE, reuse_address=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    print(f"mailcatcher: listening on port {port}, mailboxes in {settings.mail_catcher_dir}", flush=True)
    async with server:
        await stop.wait()


def health(port: int = PORT) -> bool:
    """True when the catcher answers on its port (the container's health check)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            banner = sock.recv(512)
            sock.sendall(b"QUIT\r\n")
            return banner.startswith(b"220 ")
    except OSError:
        return False


def main(health_check: bool = False) -> None:
    if health_check:
        sys.exit(0 if health() else 1)
    os.umask(0o077)
    asyncio.run(serve())


def limits_for(values: dict[str, Any]) -> dict[str, int]:
    """catcher.json's limits from the panel's settings."""
    return {
        "max_message_bytes": int(values["mail_catcher_max_message_mb"]) * 1024**2,
        "max_messages": int(values["mail_catcher_max_messages"]),
        "max_bytes": int(values["mail_catcher_max_mb"]) * 1024**2,
    }
