"""The caught e-mails on disk: one mailbox (folder) per instance, used by the catcher (writing) and the panel
(reading, deleting, retention). No Docker, no settings of its own: limits are passed in.

    <MAIL_CATCHER_DIR>/boxes/<instance>/<id>.eml    the message as received
    <MAIL_CATCHER_DIR>/boxes/<instance>/<id>.json   envelope and summary (see summary())

Ids sort by arrival: milliseconds since 1970 (13 digits), "-", 8 hexadecimal digits.
"""

from __future__ import annotations

import base64
import email
import email.policy
import json
import os
import re
import secrets
import shutil
import time
from collections.abc import Iterator
from email.message import EmailMessage, Message
from email.utils import getaddresses
from pathlib import Path
from typing import Any

from sudoo.config import settings
from sudoo.instances.store import NAME_RE

from . import BOXES

ID_RE = re.compile(r"^\d{13}-[0-9a-f]{8}$")
SNIPPET = 240  # characters of the text kept in the summary, for the list and the search
INLINE_IMAGE_BYTES = 2 * 1024**2  # images shown inside the HTML (cid:) up to this size; larger ones stay attachments


def root() -> Path:
    return settings.mail_catcher_dir / BOXES


def box(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise ValueError("not an instance name")
    return root() / name


def boxes() -> list[str]:
    """Instances that have a mailbox."""
    try:
        return sorted(entry.name for entry in root().iterdir() if entry.is_dir() and NAME_RE.match(entry.name))
    except OSError:
        return []


def new_id(now: float | None = None) -> str:
    return f"{int((now if now is not None else time.time()) * 1000):013d}-{secrets.token_hex(4)}"


def _write_private(path: Path, data: bytes) -> None:
    """Write `data` next to `path` and move it into place: a reader never sees half a file."""
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(temporary, path)


# --------------------------------------------------------------------------- reading a message


def parse(raw: bytes) -> EmailMessage:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(message, EmailMessage)
    return message


def _header(message: Message, key: str) -> str:
    try:
        return str(message.get(key, "") or "")[:2000]
    except (ValueError, TypeError, IndexError, AttributeError):  # a malformed header: show it as it was sent
        value = message.get_all(key, failobj=[""])
        return str(value[0] if value else "")[:2000]


def _addresses(message: Message, key: str) -> list[str]:
    try:
        values = [str(value) for value in message.get_all(key, failobj=[])]
    except (ValueError, TypeError, IndexError, AttributeError):
        return []
    return [address for _, address in getaddresses(values) if address][:200]


def _parts(message: EmailMessage) -> tuple[str, str, list[dict[str, Any]]]:
    """(text, html, attachments) of a message; attachments: index, file name, type, size, content id."""
    text = html = ""
    attachments: list[dict[str, Any]] = []
    for index, part in enumerate(message.walk()):
        if part.is_multipart():
            continue
        kind = part.get_content_type()
        disposition = part.get_content_disposition()
        try:
            payload = part.get_payload(decode=True) or b""
        except (ValueError, TypeError, AssertionError):
            payload = b""
        if disposition != "attachment" and kind == "text/plain" and not text:
            text = _decode(part, payload)
        elif disposition != "attachment" and kind == "text/html" and not html:
            html = _decode(part, payload)
        else:
            attachments.append(
                {
                    "index": index,
                    "filename": _filename(part, index),
                    "content_type": kind,
                    "size": len(payload),
                    "content_id": _header(part, "Content-ID").strip().strip("<>"),
                }
            )
    return text, html, attachments


def _decode(part: Message, payload: bytes) -> str:
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:  # an unknown charset
        return payload.decode("utf-8", errors="replace")


def _filename(part: Message, index: int) -> str:
    try:
        name = part.get_filename() or ""
    except (ValueError, TypeError, IndexError):
        name = ""
    name = re.sub(r'[\x00-\x1f"\\/]', "_", name).strip()[:200]
    return name or f"part-{index}"


def summary(raw: bytes, envelope: dict[str, Any]) -> dict[str, Any]:
    """What the list shows and the search looks at, worked out once when the e-mail arrives."""
    message = parse(raw)
    text, html, attachments = _parts(message)
    snippet = re.sub(r"\s+", " ", text or re.sub(r"<[^>]+>", " ", html)).strip()[:SNIPPET]
    return {
        **envelope,
        "subject": _header(message, "Subject"),
        "from": _header(message, "From"),
        "to": _addresses(message, "To"),
        "cc": _addresses(message, "Cc"),
        "date": _header(message, "Date"),
        "size": len(raw),
        "has_html": bool(html),
        "has_text": bool(text),
        "attachments": len([a for a in attachments if not a["content_id"]]),
        "snippet": snippet,
    }


# --------------------------------------------------------------------------- the catcher's side


def deliver(name: str, raw: bytes, envelope: dict[str, Any], limits: dict[str, int]) -> str:
    """File a received e-mail in the instance's mailbox; keep the mailbox within `limits`. Returns its id."""
    folder = box(name)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    message_id = new_id(envelope.get("received_at"))
    _write_private(folder / f"{message_id}.eml", raw)
    info = {"id": message_id, **summary(raw, envelope)}
    _write_private(folder / f"{message_id}.json", json.dumps(info).encode())
    prune(name, max_messages=limits.get("max_messages", 0), max_bytes=limits.get("max_bytes", 0))
    return message_id


# --------------------------------------------------------------------------- the panel's side


def _ids(name: str) -> list[str]:
    """Ids in the mailbox, newest first."""
    try:
        found = [entry.stem for entry in box(name).iterdir() if entry.suffix == ".json" and ID_RE.match(entry.stem)]
    except OSError:
        return []
    return sorted(found, reverse=True)


def summaries(name: str) -> Iterator[dict[str, Any]]:
    """Every caught e-mail of the instance, newest first."""
    for message_id in _ids(name):
        try:
            found = json.loads((box(name) / f"{message_id}.json").read_text())
        except (OSError, ValueError):
            continue  # removed meanwhile, or written by someone else
        if isinstance(found, dict):
            yield found | {"id": message_id}


def matches(found: dict[str, Any], query: str) -> bool:
    words = query.lower().split()
    haystack = " ".join(
        str(found.get(key, "")) for key in ("subject", "from", "to", "cc", "rcpt_to", "mail_from", "snippet")
    ).lower()
    return all(word in haystack for word in words)


def search(name: str, query: str = "") -> list[dict[str, Any]]:
    return [found for found in summaries(name) if not query.strip() or matches(found, query)]


def raw(name: str, message_id: str) -> bytes | None:
    if not ID_RE.match(message_id or ""):
        return None
    try:
        return (box(name) / f"{message_id}.eml").read_bytes()
    except OSError:
        return None


def get(name: str, message_id: str) -> dict[str, Any] | None:
    """The summary of one e-mail, or None."""
    if not ID_RE.match(message_id or ""):
        return None
    try:
        found = json.loads((box(name) / f"{message_id}.json").read_text())
    except (OSError, ValueError):
        return None
    return found | {"id": message_id} if isinstance(found, dict) else None


def detail(name: str, message_id: str) -> dict[str, Any] | None:
    """One e-mail for the viewer: summary, every header, the text, whether it has HTML, the attachments."""
    found, data = get(name, message_id), raw(name, message_id)
    if found is None or data is None:
        return None
    message = parse(data)
    text, html, attachments = _parts(message)
    headers = [[str(key), _header_value(value)] for key, value in message.raw_items()][:300]
    return found | {
        "headers": headers,
        "text": text,
        "has_html": bool(html),
        "attachments": [a for a in attachments if not a["content_id"] or not a["content_type"].startswith("image/")],
    }


def _header_value(value: object) -> str:
    return re.sub(r"\r?\n[ \t]+", " ", str(value))[:4000]


def html(name: str, message_id: str) -> str | None:
    """The HTML of an e-mail, its inline images (cid:) turned into data: addresses so the viewer needs nothing from
    elsewhere; None when it has none."""
    data = raw(name, message_id)
    if data is None:
        return None
    message = parse(data)
    _, body, _ = _parts(message)
    if not body:
        return None
    images: dict[str, str] = {}
    for part in message.walk():
        content_id = _header(part, "Content-ID").strip().strip("<>")
        if not content_id or not part.get_content_type().startswith("image/") or part.is_multipart():
            continue
        payload = part.get_payload(decode=True) or b""
        if isinstance(payload, bytes) and len(payload) <= INLINE_IMAGE_BYTES:
            kind = part.get_content_type()
            images[content_id] = f"data:{kind};base64,{base64.b64encode(payload).decode()}"
    if images:
        body = re.sub(
            r"cid:([^\"'\s)>]+)", lambda match: images.get(match.group(1), match.group(0)), body, flags=re.IGNORECASE
        )
    return body


def attachment(name: str, message_id: str, index: int) -> tuple[str, str, bytes] | None:
    """(file name, type, content) of part `index` of an e-mail, when that part is an attachment."""
    data = raw(name, message_id)
    if data is None:
        return None
    message = parse(data)
    for position, part in enumerate(message.walk()):
        if position != index or part.is_multipart():
            continue
        _, _, attachments = _parts(message)
        if not any(found["index"] == index for found in attachments):
            return None
        payload = part.get_payload(decode=True) or b""
        return _filename(part, index), part.get_content_type(), payload if isinstance(payload, bytes) else b""
    return None


def delete(name: str, message_id: str) -> bool:
    if not ID_RE.match(message_id or ""):
        return False
    found = False
    for suffix in (".json", ".eml"):
        try:
            (box(name) / f"{message_id}{suffix}").unlink()
            found = True
        except OSError:
            pass
    return found


def delete_all(name: str) -> int:
    count = 0
    for message_id in _ids(name):
        count += delete(name, message_id)
    return count


def remove_box(name: str) -> None:
    """Remove the instance's mailbox and everything in it (the instance was deleted)."""
    shutil.rmtree(box(name), ignore_errors=True)


def usage(name: str) -> dict[str, int]:
    """How many e-mails the mailbox holds, and their size in bytes."""
    count = size = 0
    for message_id in _ids(name):
        try:
            size += (box(name) / f"{message_id}.eml").stat().st_size
            count += 1
        except OSError:
            continue
    return {"messages": count, "bytes": size}


def prune(name: str, *, max_messages: int = 0, max_bytes: int = 0, older_than: float | None = None) -> int:
    """Remove e-mails beyond `max_messages`, beyond `max_bytes` in all, and received before `older_than`, the
    oldest first; 0 or None: no such limit. Returns how many were removed. Also removes files left half-written."""
    folder = box(name)
    removed = 0
    kept = size = 0
    for message_id in _ids(name):  # newest first
        try:
            length = (folder / f"{message_id}.eml").stat().st_size
        except OSError:
            length = 0
        received = int(message_id[:13]) / 1000
        too_old = older_than is not None and received < older_than
        too_many = bool(max_messages) and kept >= max_messages
        too_big = bool(max_bytes) and size + length > max_bytes and kept > 0
        if too_old or too_many or too_big:
            removed += delete(name, message_id)
            continue
        kept += 1
        size += length
    try:
        for entry in folder.iterdir():
            stem = entry.name.split(".")[0]
            orphan = entry.suffix == ".eml" and not (folder / f"{stem}.json").exists()
            stale = entry.name.startswith(".") and entry.stat().st_mtime < time.time() - 3600
            if orphan and entry.stat().st_mtime < time.time() - 60 or stale:
                entry.unlink(missing_ok=True)
    except OSError:
        pass
    return removed
