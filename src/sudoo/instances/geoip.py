"""The country of a visitor's address, for production access rules (access_gate.py, access_rules.py).

Source: the DB-IP "IP to Country Lite" database (https://db-ip.com), licensed under Creative Commons Attribution 4.0
(CC BY 4.0). It needs no account and no payment. The licence asks for attribution, which the panel shows next to
every country rule and the documentation repeats ("IP geolocation by DB-IP").

How it reaches the gateway
    1. The panel downloads the month's CSV file (ACCESS_RULES_GEO_URL, {year} and {month} filled in), at most once a
       month and only while ACCESS_RULES_BETA and ACCESS_RULES_GEO_DOWNLOAD are on (update_forever). When the new
       month's file is not published yet, it keeps the one it has and tries again later.
    2. It turns the CSV into a compact range table (build()): sorted IPv4 and IPv6 ranges with their two-letter
       country code; the gaps between ranges have no country. Implausibly small files are refused.
    3. The table and a manifest with its SHA-256 are written into GATEWAY_DIR/geo/ (country.bin, country.json),
       each atomically (a new file moved into place), table first: a reader never sees half a file, and the
       manifest's checksum always describes the table next to it or, for a moment, the previous one, which
       the reader then refuses until the manifest catches up.
    4. Every process that checks rules (the gateway service, and the panel when it serves the instances or shows a
       preview) loads the table when the manifest changes, after checking the checksum (lookup()).

When there is no usable table, lookup() answers None and ready() is False: the access rules then fail open or
closed as ACCESS_RULES_GEO_MISSING says (open by default), and the panel shows a warning.

Table format (country.bin): b"SGEO", a version byte (1), three zero bytes, the number of IPv4 records and of IPv6
records (two 32-bit big-endian numbers), then the IPv4 records (4-byte start address, 2-byte country) and the IPv6
records (16-byte start, 2-byte country), each sorted by start. A record holds from its start up to the next record's
start; the country "--" is a gap. Big-endian addresses of a fixed length compare like numbers, so a lookup is a
binary search over the raw bytes, without building any Python object per range.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import ipaddress
import json
import struct
import threading
import time
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sudoo.config import settings
from sudoo.core.vault import read_private, write_private

ATTRIBUTION = "IP geolocation by DB-IP (https://db-ip.com), licensed under CC BY 4.0"
"""Shown wherever country rules are (the licence of the DB-IP Lite database asks for it)."""

TABLE_FILE = "country.bin"
MANIFEST_FILE = "country.json"
MAGIC = b"SGEO\x01\x00\x00\x00"
GAP = b"--"
FORMAT = 1
MIN_ROWS = 50_000
"""A real DB-IP Lite country file has several hundred thousand rows; fewer means a broken or truncated download."""
MAX_TABLE_BYTES = 64 * 2**20
CHECK_SECONDS = 30.0
"""How often a process checks whether the manifest changed (a new month's table)."""
UPDATE_ROUND_SECONDS = 6 * 3600
"""How often the panel checks whether a newer month should be downloaded."""
DOWNLOAD_TIMEOUT = 120
MAX_DOWNLOAD_BYTES = 200 * 2**20

_V4, _V6 = 6, 18  # bytes per record


class GeoError(Exception):
    """The database could not be downloaded or built; the message says why."""


def folder() -> Path:
    return settings.gateway_dir / "geo"


# --------------------------------------------------------------------------- building


def _country(value: str) -> bytes:
    code = value.strip().upper()
    if len(code) == 2 and code.isascii() and code.isalpha() and code != "ZZ":
        return code.encode()
    return GAP


def _ranges(rows: Iterable[list[str]]) -> tuple[list[tuple[int, int, bytes]], list[tuple[int, int, bytes]]]:
    v4: list[tuple[int, int, bytes]] = []
    v6: list[tuple[int, int, bytes]] = []
    for row in rows:
        if len(row) < 3:
            continue
        try:
            start, end = ipaddress.ip_address(row[0].strip()), ipaddress.ip_address(row[1].strip())
        except ValueError:
            continue
        if start.version != end.version or int(end) < int(start):
            continue
        (v4 if start.version == 4 else v6).append((int(start), int(end), _country(row[2])))
    return v4, v6


def _records(ranges: list[tuple[int, int, bytes]], size: int) -> bytes:
    """Sorted, gap-filled records of one address family (`size`: 4 or 16 bytes per address)."""
    out = bytearray()
    top = 2 ** (8 * size) - 1
    following = 0  # the first address not covered yet
    last_country: bytes | None = None
    for start, end, country in sorted(ranges):
        if end < following:
            continue  # overlaps what is covered already: the earlier range wins
        start = max(start, following)
        if start > following and last_country != GAP:
            out += following.to_bytes(size, "big") + GAP
            last_country = GAP
        if country != last_country:
            out += start.to_bytes(size, "big") + country
            last_country = country
        following = end + 1
        if following > top:
            break
    if following <= top and last_country != GAP:
        out += following.to_bytes(size, "big") + GAP
    return bytes(out)


def build(rows: Iterable[list[str]], minimum_rows: int | None = None) -> bytes:
    """The table (see the module's description) from the rows of a DB-IP country CSV file (start, end, country).
    Raises GeoError when there are fewer than `minimum_rows` (default MIN_ROWS) usable rows."""
    v4, v6 = _ranges(rows)
    if len(v4) + len(v6) < (MIN_ROWS if minimum_rows is None else minimum_rows):
        raise GeoError(f"The country database has only {len(v4) + len(v6)} usable rows; it was not used.")
    four, six = _records(v4, 4), _records(v6, 16)
    return MAGIC + struct.pack(">II", len(four) // _V4, len(six) // _V6) + four + six


def install(table: bytes, source: str, month: str) -> dict[str, Any]:
    """Write the table and its manifest into the gateway's folder, atomically; returns the manifest."""
    target = folder()
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    manifest = {
        "format": FORMAT,
        "sha256": hashlib.sha256(table).hexdigest(),
        "bytes": len(table),
        "source": source,
        "month": month,
        "built_at": int(time.time()),
        "attribution": ATTRIBUTION,
    }
    write_private(target / TABLE_FILE, table, within=settings.gateway_dir)
    write_private(target / MANIFEST_FILE, json.dumps(manifest, indent=1), within=settings.gateway_dir)
    return manifest


def _csv_rows(data: bytes) -> Iterator[list[str]]:
    text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace", newline="")
    yield from csv.reader(text)


def source_url(year: int, month: int) -> str:
    return settings.access_rules_geo_url.replace("{year}", f"{year:04d}").replace("{month}", f"{month:02d}")


def _months(now: datetime) -> list[tuple[int, int]]:
    """This month, then the one before (the new file appears in the first days of a month)."""
    previous = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    return [(now.year, now.month), previous]


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Sudoo.sh panel (country database update)"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as answer:  # noqa: S310 - https setting
        data = answer.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise GeoError("The country database download is larger than expected; it was not used.")
    return data


def download(now: datetime | None = None) -> dict[str, Any]:
    """Download and install the newest month's database; returns its manifest. Raises GeoError."""
    now = now or datetime.now(UTC)
    problems = []
    for year, month in _months(now):
        url = source_url(year, month)
        if not url.startswith("https://"):
            raise GeoError("ACCESS_RULES_GEO_URL must be an https:// address.")
        try:
            packed = _fetch(url)
        except (OSError, ValueError) as error:
            problems.append(f"{url}: {error}")
            continue
        try:
            data = gzip.decompress(packed) if packed[:2] == b"\x1f\x8b" else packed
        except (OSError, EOFError) as error:
            raise GeoError(f"The country database could not be unpacked: {error}") from error
        table = build(_csv_rows(data))
        return install(table, url, f"{year:04d}-{month:02d}")
    raise GeoError("The country database could not be downloaded: " + "; ".join(problems)[:400])


# --------------------------------------------------------------------------- reading


@dataclass(frozen=True)
class Table:
    data: bytes
    count4: int
    count6: int
    manifest: dict[str, Any]

    @classmethod
    def parse(cls, data: bytes, manifest: dict[str, Any]) -> Table:
        if data[:8] != MAGIC or len(data) < 16:
            raise GeoError("not a country table")
        count4, count6 = struct.unpack(">II", data[8:16])
        if len(data) != 16 + count4 * _V4 + count6 * _V6:
            raise GeoError("the country table has the wrong size")
        return cls(data, count4, count6, manifest)

    def lookup(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
        if address.version == 4:
            base, count, size = 16, self.count4, 4
        else:
            base, count, size = 16 + self.count4 * _V4, self.count6, 16
        key = address.packed
        record = size + 2
        low, high = 0, count  # find the last record whose start <= key
        while low < high:
            middle = (low + high) // 2
            at = base + middle * record
            if self.data[at : at + size] <= key:
                low = middle + 1
            else:
                high = middle
        if low == 0:
            return None
        at = base + (low - 1) * record + size
        country = self.data[at : at + 2]
        return None if country == GAP else country.decode()


class _Loaded:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.table: Table | None = None
        self.seen: tuple[int, int] | None = None
        self.checked = 0.0
        self.problem = ""


_LOADED = _Loaded()


def _refresh(now: float) -> Table | None:
    loaded = _LOADED
    if now - loaded.checked < CHECK_SECONDS and loaded.checked:
        return loaded.table
    with loaded.lock:
        loaded.checked = now
        manifest_path = folder() / MANIFEST_FILE
        try:
            stat = manifest_path.stat()
        except OSError:
            loaded.table, loaded.seen, loaded.problem = None, None, "missing"
            return None
        seen = (stat.st_mtime_ns, stat.st_size)
        if seen == loaded.seen:
            return loaded.table
        try:
            manifest = json.loads(read_private(manifest_path, within=settings.gateway_dir))
            data = read_private(folder() / TABLE_FILE, within=settings.gateway_dir, limit=MAX_TABLE_BYTES)
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
                raise GeoError("unknown manifest")
            if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
                raise GeoError("checksum does not match")
            table = Table.parse(data, manifest)
        except (OSError, ValueError, GeoError) as error:
            # keep a table loaded before (the manifest may be one step ahead of the table for a moment)
            loaded.problem = str(error)
            loaded.seen = None
            return loaded.table
        loaded.table, loaded.seen, loaded.problem = table, seen, ""
        return table


def forget() -> None:
    """Load the table again at the next lookup (tests, a new table just installed)."""
    with _LOADED.lock:
        _LOADED.table, _LOADED.seen, _LOADED.checked, _LOADED.problem = None, None, 0.0, ""


def ready(now: float | None = None) -> bool:
    return _refresh(time.time() if now is None else now) is not None


def lookup(address: str | ipaddress.IPv4Address | ipaddress.IPv6Address | None) -> str | None:
    """The two-letter country code of `address`, or None (no table, no country known, not an address)."""
    if address is None:
        return None
    if isinstance(address, str):
        try:
            address = ipaddress.ip_address(address)
        except ValueError:
            return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    table = _refresh(time.time())
    return table.lookup(address) if table is not None else None


def status() -> dict[str, Any]:
    """What the panel shows: whether a table is loaded, its month and when it was built, and any problem."""
    table = _refresh(time.time())
    manifest = table.manifest if table is not None else {}
    return {
        "ready": table is not None,
        "month": manifest.get("month"),
        "built_at": manifest.get("built_at"),
        "ranges": (table.count4 + table.count6) if table is not None else 0,
        "problem": _LOADED.problem or None,
        "last_attempt": _UPDATES.get("attempt"),
        "last_error": _UPDATES.get("error"),
        "attribution": ATTRIBUTION,
    }


# --------------------------------------------------------------------------- monthly update (in the panel)

_UPDATES: dict[str, Any] = {}


def due(now: datetime) -> bool:
    """Whether a download should be tried: no table, or one older than this month."""
    table = _refresh(now.timestamp())
    if table is None:
        return True
    return str(table.manifest.get("month") or "") < f"{now.year:04d}-{now.month:02d}"


def update_once(now: datetime | None = None) -> bool:
    """One round: download a newer table when the feature is on and it is due. True when a table was installed."""
    now = now or datetime.now(UTC)
    if not (settings.access_rules_beta and settings.access_rules_geo_download) or not due(now):
        return False
    _UPDATES["attempt"] = int(now.timestamp())
    try:
        manifest = download(now)
    except GeoError as error:
        _UPDATES["error"] = str(error)
        print(f"Country database: {error}", flush=True)
        return False
    _UPDATES["error"] = None
    forget()
    print(f"Country database {manifest['month']} installed ({manifest['bytes']} bytes)", flush=True)
    return True


def update_forever() -> None:
    """The panel's background loop: a first round shortly after start, then every UPDATE_ROUND_SECONDS."""
    time.sleep(30)
    while True:
        try:
            update_once()
        except Exception as error:  # noqa: BLE001 - the loop must keep running
            print(f"Country database update: {error!r}", flush=True)
        time.sleep(UPDATE_ROUND_SECONDS)
