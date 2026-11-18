"""The Sudoo.sh Licence Agreement: the text this release bundles, acceptances, and whether one is needed now.

The text
    agreement.md is an exact copy of LICENSE.md (a test checks it). Its version and effective date come from its
    second line ("Version 1.0, effective 2026-11-18."), its digest is the SHA-256 of the file's bytes. The panel shows
    its summary (the English one at the top, or Annex A in Arabic) and the full text, rendered from that copy.

Acceptances
    Recorded append-only in the collection `licence_acceptances`: the version, the SHA-256 of the text shown, the
    time (UTC), the address and browser it came from, the user, the declared use (personal, company or
    service_provider), for a company the statement that the person may bind it, the language of the screen and where
    it was given (`panel_setup`: first-run setup; `panel`: the step shown to a super admin, for an installation set
    up before acceptance existed or after a new version became required). Nothing here changes or deletes a record.
    The installer keeps its own acceptances in <ODOO_ROOT>/manager/licence-acceptance.log, which the panel reads.

When acceptance is needed
    LICENCE_REQUIRED_VERSION (a setting, "1.0" by default) names the oldest version that counts. While no acceptance
    of that version or a later one exists, `pending()` is true: super admins see the acceptance step before anything
    else (web/security.py), other people and API tokens are not stopped, and instances keep running and serving.
    A required version newer than the one this release bundles cannot be accepted here: the panel then says to update
    and stops nobody (`update_needed`).

The hub
    When the panel is linked to a Sudoo.sh account, every acceptance (the installer's too) is sent to the hub once
    (`sync`); what was sent is kept apart (collection `licence_sync`), so the records themselves never change. A hub
    that does not take acceptances yet keeps them waiting for a later try.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import markdown

from sudoo.config import settings
from sudoo.storage import storage

log = logging.getLogger("sudoo.licence")

COLLECTION = "licence_acceptances"
SYNC_COLLECTION = "licence_sync"
TEXT_FILE = Path(__file__).with_name("agreement.md")
INSTALLER_LOG = "licence-acceptance.log"

USES = ("personal", "company", "service_provider")
COMPANY_USES = ("company", "service_provider")
SURFACES = ("panel_setup", "panel")
VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ACCEPT_STATEMENT = "I have read and accept the Sudoo.sh Licence Agreement, version {version}."
COMPANY_STATEMENT = "I accept it on behalf of my company, and I am authorised to bind it."

_LOCK = threading.Lock()
_ACCEPTED: dict[str, tuple[int, ...] | None] = {}  # cache of the newest accepted version, by collection


@dataclass(frozen=True)
class Agreement:
    version: str
    effective: str
    sha256: str
    url: str  # the current version, wherever it moves
    version_url: str  # this version, permanently
    text: str


def version_key(version: str) -> tuple[int, ...]:
    """"1.10" -> (1, 10), so versions compare as numbers."""
    return tuple(int(part) for part in version.split("."))


@cache
def bundled() -> Agreement:
    """The agreement this release ships."""
    raw = TEXT_FILE.read_bytes()
    text = raw.decode()
    found = re.search(r"^Version (\d+\.\d+), effective (\d{4}-\d{2}-\d{2})\.", text, re.MULTILINE)
    if not found:
        raise RuntimeError(f"{TEXT_FILE}: no 'Version X.Y, effective YYYY-MM-DD.' line")
    links = re.findall(r"<(https://sudoo\.sh/legal/license[^>]*)>", text)
    version = found.group(1)
    version_url = next((link for link in links if link.endswith(f"/v{version}")), f"https://sudoo.sh/legal/license/v{version}")
    return Agreement(
        version=version,
        effective=found.group(2),
        sha256=hashlib.sha256(raw).hexdigest(),
        url="https://sudoo.sh/legal/license",
        version_url=version_url,
        text=text,
    )


def _html(text: str) -> str:
    return markdown.markdown(text, extensions=["tables", "sane_lists", "toc"])


def _summary(text: str) -> str:
    """The English summary at the top: the first block quote, without its quote marks."""
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(">"):
            lines.append(line[1:].removeprefix(" "))
        elif lines:
            break
    return "\n".join(lines)


def _annex_a(text: str) -> str:
    """Annex A (the summary in Arabic), without its heading, up to the closing rule."""
    start = text.find("\n## Annex A")
    if start < 0:
        return ""
    body = text[start + 1 :].split("\n", 1)[1]
    return body.split("\n---", 1)[0].strip()


@cache
def rendered() -> dict[str, Any]:
    """The bundled agreement for the web app: its facts, both summaries and the full text, as HTML."""
    agreement = bundled()
    return {
        "version": agreement.version,
        "effective": agreement.effective,
        "sha256": agreement.sha256,
        "url": agreement.url,
        "version_url": agreement.version_url,
        "statements": statements(agreement.version),
        "summary_html": _html(_summary(agreement.text)),
        "summary_ar_html": _html(_annex_a(agreement.text)),
        "html": _html(agreement.text),
    }


def statements(version: str) -> dict[str, str]:
    return {"accept": ACCEPT_STATEMENT.format(version=version), "company": COMPANY_STATEMENT}


# ---------------------------------------------------------------- acceptances


class LicenceError(ValueError):
    """An acceptance that cannot be recorded; the message says why, in words for the person."""


def check_acceptance(data: Any) -> dict[str, Any]:
    """The acceptance sent by the web app: {"version", "sha256", "use", "on_behalf"}, checked against the bundled
    text. Returns {"use", "on_behalf"}; raises LicenceError."""
    if not isinstance(data, dict):
        raise LicenceError("Accept the Sudoo.sh Licence Agreement to continue.")
    agreement = bundled()
    if data.get("accepted") is not True:
        raise LicenceError("Tick the box to accept the Sudoo.sh Licence Agreement.")
    if data.get("version") != agreement.version or data.get("sha256") != agreement.sha256:
        raise LicenceError("The licence text changed while it was open. Reload the page and read it again.")
    use = data.get("use")
    if use not in USES:
        raise LicenceError("Choose how you will use Sudoo.sh: personal, my company or service provider.")
    on_behalf = data.get("on_behalf") is True
    if use in COMPANY_USES and not on_behalf:
        raise LicenceError("Confirm that you accept on behalf of your company and that you are authorised to bind it.")
    language = data.get("language")
    return {"use": use, "on_behalf": on_behalf if use in COMPANY_USES else False, "language": language if language in ("en", "ar") else "en"}


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def record(
    *,
    user: str,
    account: dict[str, Any],
    role: str,
    ip: str,
    user_agent: str,
    use: str,
    on_behalf: bool,
    surface: str,
    language: str = "en",
) -> dict[str, Any]:
    """Append an acceptance of the bundled version; returns it."""
    if use not in USES or surface not in SURFACES:
        raise LicenceError("Not a declared use or surface.")
    agreement = bundled()
    now = time.time()
    key = f"{now:017.6f}-{secrets.token_hex(4)}"  # sorts by time
    shown = statements(agreement.version)
    entry = {
        "id": key,
        "version": agreement.version,
        "sha256": agreement.sha256,
        "accepted_at": _iso(now),
        "ts": now,
        "ip": ip,
        "user_agent": user_agent[:300],
        "user": user,
        "user_email": account.get("email") or "",
        "user_name": " ".join(str(account.get(k) or "") for k in ("first_name", "last_name")).strip(),
        "role": role,
        "use": use,
        "on_behalf": bool(on_behalf) and use in COMPANY_USES,
        "statements": [shown["accept"], *([shown["company"]] if use in COMPANY_USES else [])],
        "language": language,
        "surface": surface,
        "panel_version": _panel_version(),
    }

    def append(existing: dict[str, Any] | None) -> dict[str, Any]:
        if existing is not None:  # append-only: a record is never replaced
            raise LicenceError("An acceptance with this id exists already.")
        return entry

    with _LOCK:
        storage().change(COLLECTION, key, append)
        _ACCEPTED.pop(COLLECTION, None)
    sync_soon()
    return entry


def _panel_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("sudoo")
    except PackageNotFoundError:  # pragma: no cover - always installed
        return "unknown"


def history() -> list[dict[str, Any]]:
    """Every acceptance given in the panel, oldest first."""
    return [doc for _, doc in sorted(storage().all(COLLECTION).items())]


def latest() -> dict[str, Any] | None:
    """The acceptance of the newest version, the latest one given for it."""
    found = history()
    if not found:
        return None
    return max(found, key=lambda doc: (version_key(str(doc["version"])), doc["ts"]))


def _accepted_key() -> tuple[int, ...] | None:
    with _LOCK:
        if COLLECTION not in _ACCEPTED:
            newest = latest()
            _ACCEPTED[COLLECTION] = version_key(newest["version"]) if newest else None
        return _ACCEPTED[COLLECTION]


def required_version() -> str:
    return str(settings.licence_required_version)


def update_needed() -> bool:
    """The required version is newer than the one this release bundles: it cannot be accepted until an update."""
    return version_key(required_version()) > version_key(bundled().version)


def pending() -> bool:
    """Whether super admins must accept the agreement before anything else."""
    if update_needed():
        return False
    accepted = _accepted_key()
    return accepted is None or accepted < version_key(required_version())


def check_required_version(value: Any) -> None:
    """Raise ValueError when LICENCE_REQUIRED_VERSION cannot be set to `value` (it must be X.Y and not newer than
    the bundled text)."""
    if value is None:
        return
    text = str(value)
    if not VERSION_RE.match(text):
        raise ValueError('LICENCE_REQUIRED_VERSION is a version such as "1.0".')
    if version_key(text) > version_key(bundled().version):
        raise ValueError(
            f"LICENCE_REQUIRED_VERSION: this panel bundles version {bundled().version} of the agreement, so it cannot "
            f"ask for {text}. Update the panel first."
        )


# ---------------------------------------------------------------- the installer's log


_LOG_FIELD = re.compile(r"(\w+)=(\S*)")


def installer_records(path: Path | None = None) -> list[dict[str, Any]]:
    """The acceptances the installer wrote, oldest first: lines of
    `<UTC time> version=<X.Y> sha256=<hex> host=<name> user=<name> [key=value...]`. Lines that do not read so are
    skipped."""
    path = path or settings.manager_dir / INSTALLER_LOG
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    found: list[dict[str, Any]] = []
    for line in lines:
        stamp, _, rest = line.strip().partition(" ")
        fields = dict(_LOG_FIELD.findall(rest))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp):
            continue
        if not VERSION_RE.match(fields.get("version", "")) or not SHA256_RE.match(fields.get("sha256", "")):
            continue
        found.append(
            {
                "id": "installer-" + hashlib.sha256(line.strip().encode()).hexdigest()[:24],
                "accepted_at": stamp,
                "version": fields["version"],
                "sha256": fields["sha256"],
                "host": fields.get("host", "")[:255],
                "user": fields.get("user", "")[:64],
                "via": fields.get("via", "")[:32],
                "installer_version": fields.get("installer", "")[:64],
                "surface": "installer",
            }
        )
    return found


# ---------------------------------------------------------------- the hub


def synced() -> dict[str, dict[str, Any]]:
    return storage().all(SYNC_COLLECTION)


def sync() -> int:
    """Send the acceptances the hub has not had yet, when the panel is linked to an account. Returns how many were
    sent; 0 when not linked, when the hub cannot be reached or does not take them yet (they wait for the next try).
    Never raises."""
    from sudoo.hub import marketplace
    from sudoo.hub.client import HubRefused, HubUnavailable

    try:
        if not marketplace.enabled() or not marketplace.state().get("linked"):
            return 0
        done = synced()
        waiting = [doc for doc in [*history(), *installer_records()] if doc["id"] not in done]
        if not waiting:
            return 0
        try:
            marketplace.send_licence_acceptances(waiting)
        except HubRefused as error:
            if error.status in (404, 405, 501):
                log.info("licence: the hub does not take acceptances yet; %d kept to send later", len(waiting))
            else:
                log.warning("licence: the hub refused the acceptances: %s", error.detail)
            return 0
        except HubUnavailable as error:
            log.info("licence: acceptances not sent (%s); kept to send later", error)
            return 0
        now = time.time()
        for doc in waiting:
            storage().put(SYNC_COLLECTION, doc["id"], {"synced_at": now})
        return len(waiting)
    except Exception:  # noqa: BLE001 - never stops what called it
        log.exception("licence: sending acceptances to the hub failed")
        return 0


def sync_soon() -> None:
    """sync() in the background (after an acceptance or linking)."""
    if not settings.marketplace:
        return
    threading.Thread(target=sync, name="licence-sync", daemon=True).start()


# ---------------------------------------------------------------- what the web app shows


def public(doc: dict[str, Any]) -> dict[str, Any]:
    """An acceptance as every signed-in person sees it (no address or browser)."""
    keys = ("version", "sha256", "accepted_at", "user", "user_name", "use", "on_behalf", "surface")
    return {key: doc.get(key) for key in keys}


def status(full: bool) -> dict[str, Any]:
    """The licence page: the bundled version, what is required, the latest acceptance; with `full` (super admins and
    administrators) every acceptance, the installer's, and which ones the hub has."""
    newest = latest()
    result: dict[str, Any] = {
        "bundled_version": bundled().version,
        "required_version": required_version(),
        "pending": pending(),
        "update_needed": update_needed(),
        "accepted": public(newest) if newest else None,
    }
    if full:
        sent = synced()
        result["history"] = [
            {**doc, "synced_at": _iso(sent[doc["id"]]["synced_at"]) if doc["id"] in sent else None}
            for doc in [*history(), *installer_records()]
        ]
        result["history"].sort(key=lambda doc: str(doc["accepted_at"]))
    return result
