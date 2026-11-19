"""Installing, binding, loading and watching the premium packages (see premium/__init__.py and docs/PREMIUM.md).

Install (a job, started on its own after a lease whose plan includes a package, or by Check now in Settings)
    1. The hub lists the packages the plan includes, built for this panel's Python and platform
       (GET /api/v1/installations/premium, a signed request).
    2. Each package that is missing or older is downloaded with a signed request. Its SHA-256 must be the one stated,
       and the premium key's Ed25519 signature over
       "sudoo-premium-v1\\n<id>\\n<version>\\n<sha256>\\n<python tag>\\n<platform>" must verify with a trusted premium
       key (keys.py; never a hub key).
    3. The download carries the hub's binding to this server: {server_id, hardware_fingerprint, package_id, version,
       sha256, exp}, signed by the premium key. It must name this installation, this machine and this package.
    4. The package is checked like a marketplace plugin (paths, links, sizes, metadata), except that compiled
       modules (.so) are allowed here, and only those built for this interpreter (its EXT_SUFFIX); its entry points
       may only use the premium extension points, and its modules are named sudoo_premium_*.
    5. It is unpacked into <ODOO_ROOT>/manager/premium/<id>/<version>/ (folders 700, files 600), the package file is
       deleted, its entry points are written as a small dist-info, and a hash of every file is recorded.
    6. The backend restarts (once no job runs) and loads it.

Loading and the integrity pass
    At start, and every PREMIUM_WATCH_MINUTES, each installed package is checked again: its files' hash, the
    signature of its recorded SHA-256, its binding (this installation id and hardware fingerprint, not expired) and
    the plan. A package copied to another server (another fingerprint) is never loaded. A binding that ends within
    PREMIUM_BINDING_RENEW_DAYS is renewed from the hub.

When the plan no longer includes a package
    The "licence ended" path of paid plugins: the panel's data is backed up, the package is switched off (its files
    and all the data it made are kept), the super admins get an e-mail, and the panel restarts. What was set up keeps
    working through the core (premium/__init__.py). A plan that includes it again switches it back on.

Records are kept in the panel's storage, collection "premium", one document per package id.

In a server's agent (premium/agent.py, premium/servers.py)
    The agent has no link to Sudoo.sh: it runs the packages the main panel runs, fetched from the panel
    (`build_for_agent` there, `install_for_agent` here). Each is checked as above, except that its binding must name
    the main panel (its installation id and hardware fingerprint, recorded in <ODOO_ROOT>/agent/premium.json when the
    agent joined) instead of this server, and that the panel, not the plan, decides what runs: the panel renews the
    bindings and sends them along (`update_agent_binding`). Over its signed channel the joined panel states its
    identity (`confirm_agent_identity`): a package bound to any other panel does not load. Bindings are checked
    against this server's clock, never earlier than the latest time the panel signed (`agent_time_seen`): setting
    the clock back does not keep an ended binding running.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import re
import shutil
import stat
import sys
import sysconfig
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from sudoo import plugin_manager
from sudoo.config import settings
from sudoo.core import audit, events, jobs
from sudoo.core.jobs import Job, JobError
from sudoo.hub import HubRefused, HubUnavailable, client, crypto, marketplace
from sudoo.storage import storage

from . import GROUPS, PACKAGES, included_ids, keys

log = logging.getLogger("sudoo.premium")

COLLECTION = "premium"
STATE_COLLECTION = "premium_state"
JOB_INSTANCE = "panel:premium"  # one premium job at a time, never a real instance name
HUB_PATH = "/api/v1/installations/premium"
TOPIC = "premium"
MAX_FILES = 5000
MAX_UNPACKED_BYTES = 400 * 1024 * 1024
MAX_RATIO = 100
REFUSED_SUFFIXES = (".pyd", ".dll", ".dylib", ".exe", ".pyc", ".pyo")
MODULE_PREFIX = "sudoo_premium_"
UI_PATH_RE = re.compile(r"^ui/[A-Za-z0-9][A-Za-z0-9._/-]{0,150}\.(js|css|json)$")
ROUTE_RE = re.compile(r"^/[a-z0-9][a-z0-9/-]{0,60}$")
SLOT_RE = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*){1,3}$")
EXPORT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$")

_LOCK = threading.RLock()
LOADED: set[str] | None = None  # ids of the packages this process put on sys.path (None: not activated yet)
_WAKE = threading.Event()


class PremiumRefused(Exception):
    """A premium package that is not installed or loaded; the message says why."""


# ---------------------------------------------------------------- this panel's build


def python_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def platform_tag() -> str:
    """The interpreter's SOABI without "cpython-NNN-": x86_64-linux-musl, aarch64-linux-gnu, ..."""
    soabi = str(sysconfig.get_config_var("SOABI") or "")
    return re.sub(r"^cpython-\d+-", "", soabi) or "unknown"


def ext_suffix() -> str:
    return str(sysconfig.get_config_var("EXT_SUFFIX") or ".so")


def is_pure(tag: Any, platform: Any) -> bool:
    """A build without compiled modules: a py* tag (py3) and the platform "any"."""
    return isinstance(tag, str) and tag.startswith("py") and platform == "any"


def build_accepted(tag: Any, platform: Any) -> bool:
    return (tag, platform) == (python_tag(), platform_tag()) or is_pure(tag, platform)


# ---------------------------------------------------------------- records


def premium_dir() -> Path:
    return settings.manager_dir / "premium"


def records() -> dict[str, dict[str, Any]]:
    return {k: v for k, v in storage().all(COLLECTION).items() if k in PACKAGES}


def record(package_id: str) -> dict[str, Any] | None:
    return storage().get(COLLECTION, package_id)


def _update(package_id: str, **values: Any) -> dict[str, Any] | None:
    def change(doc: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if doc is None else {**doc, **values}

    return storage().change(COLLECTION, package_id, change)


def state() -> dict[str, Any]:
    return storage().get(STATE_COLLECTION, "state") or {}


def _save_state(**values: Any) -> None:
    storage().change(STATE_COLLECTION, "state", lambda doc: {**(doc or {}), **values})


def folder(rec: dict[str, Any]) -> Path:
    return premium_dir() / rec["id"] / rec["version"]


def dist_name(package_id: str) -> str:
    return f"sudoo-premium-{package_id}"


def _meta_folder(package_id: str, version: str) -> str:
    return f"{dist_name(package_id).replace('-', '_')}-{version}.dist-info"


# ---------------------------------------------------------------- identity of this server


def server_id() -> str:
    return client.identity().installation_id


def fingerprint() -> str:
    return client.hardware_fingerprint(client.identity().public_key)


def _agent() -> bool:
    from .servers import is_agent

    return is_agent()


def agent_identity_file() -> Path:
    return settings.odoo_root / "agent" / "premium.json"


def agent_identity() -> dict[str, Any]:
    """In an agent: {"server_id", "hardware_fingerprint", "panel_url"} of the main panel its packages are bound to."""
    try:
        found = json.loads(agent_identity_file().read_text())
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _write_agent_identity(changes: dict[str, Any]) -> None:
    from sudoo.core import vault

    agent_identity_file().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    vault.write_private(agent_identity_file(), json.dumps({**agent_identity(), **changes}))


def confirm_agent_identity(installation: str, hardware_fingerprint: str) -> bool:
    """In an agent: the identity the joined panel stated over its signed channel. False (and recorded, so no package
    loads) when the packages here are bound to another panel."""
    found = agent_identity()
    stated = [str(installation), str(hardware_fingerprint)]
    if not found.get("server_id") or not all(stated):
        return True
    if found.get("stated") != stated:
        _write_agent_identity({"stated": stated})
    return [str(found.get("server_id")), str(found.get("hardware_fingerprint"))] == stated


def agent_time_seen(at: Any) -> None:
    """In an agent: a time the panel signed (its welcome, its pushes). Bindings are never checked against a clock
    earlier than the latest one (a clock set back does not keep an ended binding running)."""
    try:
        value = float(at)
    except (TypeError, ValueError):
        return
    found = agent_identity()
    if found.get("server_id") and value > float(found.get("clock_floor") or 0):
        _write_agent_identity({"clock_floor": value})


def _expected_identity() -> tuple[str, str] | None:
    """(installation id, hardware fingerprint) a binding must name here: this panel's, or in an agent its panel's."""
    if not _agent():
        return server_id(), fingerprint()
    found = agent_identity()
    if not found.get("server_id") or not found.get("hardware_fingerprint"):
        return None
    return str(found["server_id"]), str(found["hardware_fingerprint"])


# ---------------------------------------------------------------- checks


def binding_problem(rec: dict[str, Any], now: float | None = None) -> str | None:
    """Why the package's binding does not allow it on this server now, or None."""
    trusted = keys.trusted()
    key = trusted.get(str(rec.get("binding_key_id") or ""))
    if key is None:
        return "its binding is signed with a key this panel does not trust"
    try:
        binding = crypto.read_binding(str(rec.get("binding") or ""), str(rec.get("binding_signature") or ""), key)
    except ValueError as error:
        return f"its binding is not valid ({error})"
    expected = _expected_identity()
    if expected is None:
        return "this server's agent has not joined a panel"
    stated = agent_identity().get("stated") if _agent() else None
    if stated and list(stated) != [binding["server_id"], binding["hardware_fingerprint"]]:
        return "it is bound to another panel than the one this server joined"
    if binding["server_id"] != expected[0]:
        return "it was installed for another server"
    if binding["hardware_fingerprint"] != expected[1]:
        return "it was installed on another machine (the hardware fingerprint differs)"
    if (binding["package_id"], binding["version"], binding["sha256"]) != (rec["id"], rec["version"], rec["sha256"]):
        return "its binding names another package"
    if binding["exp"] <= (_now() if now is None else now):
        return "its binding to this server has ended (the panel could not reach Sudoo.sh to renew it)"
    return None


def _now() -> float:
    """The time bindings are checked against: the hub's trusted time in the panel; in an agent its clock, never
    earlier than the latest time the panel signed (agent_time_seen)."""
    if _agent():
        return max(time.time(), float(agent_identity().get("clock_floor") or 0))
    return marketplace.trusted_now()


def signature_problem(rec: dict[str, Any]) -> str | None:
    key = keys.trusted().get(str(rec.get("signing_key_id") or ""))
    if key is None:
        return "it is signed with a key this panel does not trust"
    ok = crypto.verify_premium_package(key, rec["id"], rec["version"], rec["sha256"], rec["python_tag"],
                                       rec["platform"], str(rec.get("signature") or ""))  # fmt: skip
    return None if ok else "its signature is not valid"


def build_problem(rec: dict[str, Any]) -> str | None:
    if not build_accepted(rec.get("python_tag"), rec.get("platform")):
        return f"it was built for another Python ({rec.get('python_tag')}, {rec.get('platform')})"
    return None


def problem(rec: dict[str, Any], now: float | None = None) -> str | None:
    """Why the package may not run now (files, signature, build, binding), or None. The plan is checked apart."""
    if rec.get("tampered"):
        return "its files were changed"
    return signature_problem(rec) or build_problem(rec) or binding_problem(rec, now)


def verify_files(rec: dict[str, Any]) -> bool:
    if rec.get("tampered"):
        return False
    base = folder(rec)
    actual = plugin_manager.tree_hash(base) if base.is_dir() else "missing"
    if actual == rec.get("tree_hash"):
        return True
    _update(rec["id"], tampered=True, tampered_at=time.time())
    audit.record("system", None, "premium feature files modified; not loaded", ok=False, detail=rec["id"])
    _mail_super_admins(
        f"{rec.get('name', rec['id'])} was switched off: its files were changed",
        f"The files of {rec.get('name', rec['id'])} ({rec['version']}) are not the ones Sudoo.sh installed, so the "
        "panel no longer loads it. Press Check now in Settings, Premium features, to install it again.",
    )
    events.publish(TOPIC, rec["id"], {"changed": True})
    return False


def runs(rec: dict[str, Any], wanted: set[str] | None = None) -> tuple[bool, str | None]:
    """Whether the package should run now, and why not."""
    wanted = included_ids() if wanted is None else wanted
    if rec["id"] not in wanted:
        return False, "your plan does not include it"
    if rec.get("switched_off"):
        return False, "it is switched off"
    found = problem(rec)
    return (False, found) if found else (True, None)


# ---------------------------------------------------------------- loading


def activate() -> set[str]:
    """Once per process: check every installed package and put those that may run on sys.path."""
    global LOADED
    with _LOCK:
        if LOADED is not None:
            return LOADED
        LOADED = set()
        try:
            installed = records()
            wanted = included_ids()
        except Exception:  # noqa: BLE001 - a panel without readable storage still starts, without premium features
            log.exception("premium: cannot read the installed premium features")
            return LOADED
        for package_id, rec in installed.items():
            if not verify_files(rec):
                continue
            ok, why = runs(rec, wanted)
            _update(package_id, problem=None if ok or why == "your plan does not include it" else why)
            if not ok:
                continue
            base = folder(rec)
            for cache in base.rglob("__pycache__"):
                shutil.rmtree(cache, ignore_errors=True)
            for path in (base / "meta", base / "package" / "src"):
                if str(path) not in sys.path:
                    sys.path.append(str(path))  # last: a package never shadows the panel or its libraries
            LOADED.add(package_id)
        importlib.invalidate_caches()
        return LOADED


def entry_allowed(entry: Any, group: str) -> bool:
    """Whether an entry point may be used: one of a loaded premium package, in a premium group its verified manifest
    declares. Nothing else is ever loaded for the premium groups."""
    dist = getattr(entry, "dist", None)
    if dist is None or not group.startswith("sudoo.premium."):
        return False
    try:
        root = Path(str(dist.locate_file(""))).resolve()
    except (OSError, NotImplementedError):
        return False
    if not root.is_relative_to(premium_dir().resolve()):
        return False
    name = dist.metadata.get("Name", None)
    return any(
        name == dist_name(pid) and entry.name in ((rec.get("entry_points") or {}).get(group) or {})
        for pid, rec in records().items()
        if pid in (LOADED or set())
    )


# ---------------------------------------------------------------- package checks


def _members(archive: zipfile.ZipFile, compiled_ok: bool) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_FILES:
        raise PremiumRefused(f"The package has more than {MAX_FILES} files.")
    total = 0
    suffix = ext_suffix()
    for info in infos:
        name = info.filename
        if not plugin_manager._safe_name(name):
            raise PremiumRefused(f"The package contains a file outside its folder ({name[:80]!r}).")
        kind = stat.S_IFMT(info.external_attr >> 16)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise PremiumRefused(f"The package contains a link or a special file ({name[:80]!r}).")
        if info.flag_bits & 0x1:
            raise PremiumRefused("The package is encrypted.")
        lower = name.lower()
        if lower.endswith(REFUSED_SUFFIXES):
            raise PremiumRefused(f"The package contains a file of a kind it may not ({name[:80]!r}).")
        if lower.endswith(".so") and (not compiled_ok or not name.endswith(suffix) or not name.startswith("src/")):
            raise PremiumRefused(f"The package contains a compiled module not built for this panel ({name[:80]!r}).")
        if any(part.lower().endswith(plugin_manager.METADATA_SUFFIXES) for part in name.split("/")):
            raise PremiumRefused(f"The package contains Python package metadata ({name[:80]!r}).")
        total += info.file_size
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO and info.file_size > 1024 * 1024:
            raise PremiumRefused("The package is compressed suspiciously well.")
    if total > MAX_UNPACKED_BYTES:
        raise PremiumRefused("The package is too large once unpacked.")
    return infos


def _modules(infos: list[zipfile.ZipInfo]) -> set[str]:
    found = set()
    for info in infos:
        parts = info.filename.split("/")
        if parts[0] == "src" and len(parts) > 1 and parts[1]:
            found.add(parts[1].split(".", 1)[0])
    return found


def _check_ui(archive: zipfile.ZipFile, ui: Any) -> dict[str, Any]:
    """The manifest's "ui" (routes, sections, styles, strings, files with their SHA-256), checked."""
    if ui is None:
        return {}
    if not isinstance(ui, dict):
        raise PremiumRefused("The manifest's ui is not an object.")
    files = ui.get("files") or {}
    if not isinstance(files, dict):
        raise PremiumRefused("The manifest's ui.files is not an object.")
    names = set(archive.namelist())
    for path, digest in files.items():
        if not isinstance(path, str) or not UI_PATH_RE.match(path) or ".." in path or path not in names:
            raise PremiumRefused(f"The manifest lists a web file the package does not have ({str(path)[:80]!r}).")
        if hashlib.sha256(archive.read(path)).hexdigest() != digest:
            raise PremiumRefused(f"The web file {path} does not match its hash in the manifest.")
    routes = []
    for route in ui.get("routes") or []:
        if not isinstance(route, dict) or not ROUTE_RE.match(str(route.get("path", ""))):
            raise PremiumRefused("A route of the manifest has no valid path.")
        if route.get("module") not in files or not EXPORT_RE.match(str(route.get("export", ""))):
            raise PremiumRefused(f"The route {route['path']} names a module or export the package does not list.")
        nav = route.get("nav") if isinstance(route.get("nav"), dict) else None
        routes.append({"path": route["path"], "module": route["module"], "export": route["export"],
                       "permission": str(route["permission"])[:40] if route.get("permission") else None,
                       "nav": {k: str(v)[:60] for k, v in nav.items() if k in ("group", "icon", "label")} if nav else None})  # fmt: skip
    sections = []
    for section in ui.get("sections") or []:
        if not isinstance(section, dict) or not SLOT_RE.match(str(section.get("slot", ""))):
            raise PremiumRefused("A section of the manifest has no valid slot.")
        if section.get("module") not in files or not EXPORT_RE.match(str(section.get("export", ""))):
            raise PremiumRefused(f"The section {section['slot']} names a module or export the package does not list.")
        sections.append({"slot": section["slot"], "module": section["module"], "export": section["export"],
                         "permission": str(section["permission"])[:40] if section.get("permission") else None})  # fmt: skip
    styles = [s for s in ui.get("styles") or [] if s in files and str(s).endswith(".css")]
    strings = ui.get("strings") if ui.get("strings") in files else None
    return {"routes": routes, "sections": sections, "styles": styles, "strings": strings, "files": dict(files)}


def check_package(archive: zipfile.ZipFile, offer: dict[str, Any]) -> dict[str, Any]:
    """The manifest of a package this panel can install for `offer`; PremiumRefused otherwise."""
    compiled_ok = not is_pure(offer["python_tag"], offer["platform"])
    infos = _members(archive, compiled_ok)
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except KeyError:
        raise PremiumRefused("The package has no manifest.json.") from None
    except ValueError:
        raise PremiumRefused("The package's manifest.json is not valid JSON.") from None
    if not isinstance(manifest, dict) or manifest.get("kind") != "sudoo-premium":
        raise PremiumRefused("The package is not a premium package.")
    for name in ("id", "version", "python_tag", "platform"):
        if manifest.get(name) != offer[name]:
            raise PremiumRefused(f"The package's manifest names another {name.replace('_', ' ')} than the one signed.")
    from sudoo.hub.client import sudoo_version

    panel = sudoo_version()
    try:
        if plugin_manager.semver_key(panel) < plugin_manager.semver_key(
            str(manifest.get("min_sudoo_version") or "0.0.0")
        ):
            raise PremiumRefused(f"It needs Sudoo.sh {manifest['min_sudoo_version']} or newer (this is {panel}).")
        newest = manifest.get("max_sudoo_version")
        if newest and plugin_manager.semver_key(panel) > plugin_manager.semver_key(str(newest)):
            raise PremiumRefused(f"It works up to Sudoo.sh {newest} (this is {panel}).")
    except ValueError as error:
        raise PremiumRefused(f"The manifest has a version this panel cannot read: {error}.") from None
    groups = manifest.get("entry_points") or {}
    if not isinstance(groups, dict):
        raise PremiumRefused("The manifest's entry points are not an object.")
    for group, entries in groups.items():
        if group not in GROUPS or not isinstance(entries, dict):
            raise PremiumRefused(f"The entry point group {str(group)[:60]!r} is not a premium extension point.")
        for name, target in entries.items():
            if not isinstance(target, str) or not target.startswith(MODULE_PREFIX) or ":" not in target:
                raise PremiumRefused(f"The entry point {str(name)[:40]!r} is not sudoo_premium_<module>:<object>.")
    for module in _modules(infos):
        if not module.startswith(MODULE_PREFIX):
            raise PremiumRefused(f"The package's module {module!r} is not named sudoo_premium_*.")
    manifest["ui"] = _check_ui(archive, manifest.get("ui"))
    manifest["modules"] = sorted(_modules(infos))
    return manifest


# ---------------------------------------------------------------- the hub


def offers() -> list[dict[str, Any]]:
    """The packages the hub offers this panel (its plan, its Python and platform)."""
    from sudoo.hub.client import sudoo_version

    query = urlencode({"python_tag": python_tag(), "platform": platform_tag(), "sudoo_version": sudoo_version()})
    answer = marketplace._for_panel("GET", f"{HUB_PATH}?{query}")
    found = []
    for item in answer.data if isinstance(answer.data, list) else []:
        if not isinstance(item, dict) or item.get("id") not in PACKAGES:
            continue
        version, path = str(item.get("version") or ""), str(item.get("download_path") or "")
        if not plugin_manager.SEMVER_RE.match(version) or not crypto.SHA256_RE.match(str(item.get("package_sha256"))):
            continue
        if not path.startswith(f"{HUB_PATH}/{item['id']}/download") or "://" in path or ".." in path:
            continue
        if not build_accepted(item.get("python_tag"), item.get("platform")):
            continue
        found.append({**item, "version": version, "download_path": path})
    return found


def renew_binding(rec: dict[str, Any]) -> dict[str, Any]:
    """Ask the hub for a fresh binding of an installed package; raises HubRefused/HubUnavailable/PremiumRefused."""
    query = urlencode({"version": rec["version"], "sha256": rec["sha256"], "fingerprint": fingerprint()})
    answer = marketplace._for_panel("GET", f"{HUB_PATH}/{rec['id']}/binding?{query}")
    data = answer.data if isinstance(answer.data, dict) else {}
    values = {"binding": str(data.get("binding") or ""), "binding_signature": str(data.get("signature") or ""),
              "binding_key_id": str(data.get("key_id") or "")}  # fmt: skip
    found = binding_problem({**rec, **values})
    if found:
        raise PremiumRefused(f"Sudoo.sh sent a binding the panel cannot use: {found}.")
    return _update(rec["id"], **values, problem=None, binding_renewed_at=time.time()) or rec


def _binding_exp(rec: dict[str, Any]) -> float:
    try:
        return float(json.loads(crypto.from_b64url(str(rec.get("binding") or "")))["exp"])
    except (ValueError, KeyError, TypeError):
        return 0.0


# ---------------------------------------------------------------- install (a job)


def install(job: Job, offer: dict[str, Any]) -> dict[str, Any]:
    """Download, verify, bind, check and unpack one offered package."""
    package_id, version = offer["id"], offer["version"]
    downloads = premium_dir() / ".downloads"
    downloads.mkdir(parents=True, exist_ok=True, mode=0o700)
    premium_dir().chmod(0o700)
    package = downloads / f"{package_id}-{version}.zip"
    path = offer["download_path"]
    path += ("&" if "?" in path else "?") + urlencode({"fingerprint": fingerprint()})
    try:
        job.write(f"Downloading {package_id} {version} ({offer['python_tag']}, {offer['platform']})\n")
        headers = client.download(path, package, int(settings.premium_max_package_mb) * 1024 * 1024)
        values = _verify_download(package, offer, headers)
        job.write(f"SHA-256 {values['sha256']}, signature and binding verified\n")
        with zipfile.ZipFile(package) as archive:
            manifest = check_package(archive, offer)
            return _unpack(archive, manifest, values)
    finally:
        package.unlink(missing_ok=True)


def _verify_download(package: Path, offer: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    if (
        headers.get("x-sudoo-premium-id", offer["id"]) != offer["id"]
        or headers.get("x-sudoo-premium-version", offer["version"]) != offer["version"]
    ):
        raise PremiumRefused("Sudoo.sh sent another package or version than the one asked for.")
    stated = str(offer.get("package_sha256") or "").lower()
    if headers.get("x-sudoo-package-sha256", stated).lower() != stated:
        raise PremiumRefused("The download does not match what Sudoo.sh stated; not installed.")
    with package.open("rb") as fh:
        actual = hashlib.file_digest(fh, "sha256").hexdigest()
    if actual != stated:
        raise PremiumRefused("The package's SHA-256 does not match the one Sudoo.sh states; not installed.")
    values = {
        "id": offer["id"],
        "version": offer["version"],
        "sha256": actual,
        "python_tag": offer["python_tag"],
        "platform": offer["platform"],
        "signature": str(offer.get("signature") or headers.get("x-sudoo-signature") or ""),
        "signing_key_id": str(offer.get("signing_key_id") or headers.get("x-sudoo-signing-key") or ""),
        "binding": headers.get("x-sudoo-premium-binding", ""),
        "binding_signature": headers.get("x-sudoo-premium-binding-signature", ""),
        "binding_key_id": headers.get("x-sudoo-premium-binding-key", ""),
    }
    found = signature_problem(values) or binding_problem(values)
    if found:
        raise PremiumRefused(f"Not installed: {found}.")
    return values


def _unpack(archive: zipfile.ZipFile, manifest: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    package_id, version = values["id"], values["version"]
    base = premium_dir() / package_id
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = base / f".{version}.partial"
    shutil.rmtree(partial, ignore_errors=True)
    (partial / "package").mkdir(parents=True, mode=0o700)
    plugin_manager._extract(archive, partial / "package")
    info = partial / "meta" / _meta_folder(package_id, version)
    info.mkdir(parents=True, mode=0o700)
    lines = []
    for group, entries in (manifest.get("entry_points") or {}).items():
        lines += [f"[{group}]", *(f"{k} = {v}" for k, v in entries.items()), ""]
    (info / "entry_points.txt").write_text("\n".join(lines))
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist_name(package_id)}\nVersion: {version}\n")
    plugin_manager._lock_down(partial)
    final = base / version
    previous = record(package_id)
    shutil.rmtree(final, ignore_errors=True)
    partial.rename(final)
    now = time.time()
    rec = {
        **values,
        "name": str(manifest.get("name") or PACKAGES[package_id].name)[:80],
        "features": [str(f) for f in manifest.get("features") or []][:10],
        "entry_points": manifest.get("entry_points") or {},
        "ui": manifest.get("ui") or {},
        "modules": manifest.get("modules") or [],
        "tree_hash": plugin_manager.tree_hash(final),
        "tampered": False,
        "switched_off": None,
        "problem": None,
        "installed_at": (previous or {}).get("installed_at") or now,
        "updated_at": now,
    }
    storage().put(COLLECTION, package_id, rec)
    if previous and previous.get("version") != version:
        shutil.rmtree(base / previous["version"], ignore_errors=True)
    audit.record("system", None, "premium feature installed" if not previous else "premium feature updated",
                 detail=f"{package_id} {version}")  # fmt: skip
    events.publish(TOPIC, package_id, {"changed": True})
    return rec


def sync(job: Job, user: str = "system") -> None:
    """Bring the installed premium packages in line with the plan: install or update what it includes, renew
    bindings, switch off what it no longer includes (a job)."""
    wanted = included_ids()
    installed = records()
    job.expect(3)
    job.step("Asking Sudoo.sh which premium features the plan includes")
    changed = False
    problems: list[str] = []
    if wanted and settings.marketplace:
        try:
            keys.refresh()  # rotations and revocations of the premium keys
        except (HubRefused, HubUnavailable) as error:
            job.note(
                f"The premium keys could not be checked ({getattr(error, 'detail', error)}); the known ones apply."
            )
    try:
        offered = {o["id"]: o for o in offers()} if wanted and settings.marketplace else {}
    except (HubRefused, HubUnavailable) as error:
        _save_state(last_error=str(getattr(error, "detail", error)), last_checked_at=time.time())
        raise JobError(f"Sudoo.sh could not be asked: {getattr(error, 'detail', error)}") from None
    _save_state(last_checked_at=time.time(), last_error=None, offered=sorted(offered))
    job.step("Installing and updating")
    for package_id in sorted(wanted):
        offer = offered.get(package_id)
        rec = installed.get(package_id)
        if offer is None:
            if rec is None:
                problems.append(f"{PACKAGES[package_id].name}: Sudoo.sh has no build for this panel yet")
            continue
        same = rec and rec["version"] == offer["version"] and rec["sha256"] == offer["package_sha256"]
        if same and rec and not rec.get("tampered") and not build_problem(rec):
            if rec.get("switched_off"):
                _update(package_id, switched_off=None)
                audit.record(user, None, "premium feature switched back on (plan)", detail=package_id)
                changed = True
            exp = _binding_exp(rec)
            renew = exp - marketplace.trusted_now() < float(settings.premium_binding_renew_days) * 86400
            if renew or binding_problem(rec):
                try:
                    renew_binding(rec)
                    job.write(f"{package_id}: binding renewed\n")
                    changed = changed or package_id not in (LOADED or set())
                except (HubRefused, HubUnavailable, PremiumRefused) as error:
                    problems.append(f"{rec['name']}: {getattr(error, 'detail', error)}")
                    _update(package_id, problem=str(getattr(error, "detail", error))[:300])
            continue
        try:
            install(job, offer)
            changed = True
        except (PremiumRefused, zipfile.BadZipFile) as error:
            text = str(error) if isinstance(error, PremiumRefused) else "The package is not a valid archive."
            problems.append(f"{PACKAGES[package_id].name}: {text}")
            _record_failure(package_id, text)
        except (HubRefused, HubUnavailable) as error:
            text = str(getattr(error, "detail", error))
            problems.append(f"{PACKAGES[package_id].name}: {text}")
            _record_failure(package_id, text)
    job.step("Switching off what the plan no longer includes")
    for package_id, rec in installed.items():
        if package_id not in wanted and not rec.get("switched_off"):
            _plan_dropped(job, rec)
            changed = True
    _save_state(problems=problems)
    events.publish(TOPIC, "state", {"changed": True})
    if changed:
        job.note("The panel restarts in a few seconds to load the change; instances keep running.")
        plugin_manager.schedule_restart("premium features changed")
    if problems:
        raise JobError("; ".join(problems))


def _record_failure(package_id: str, text: str) -> None:
    failures = dict(state().get("failures") or {})
    failures[package_id] = {"at": time.time(), "error": text[:300]}
    _save_state(failures=failures)
    if record(package_id):
        _update(package_id, problem=text[:300])


def _plan_dropped(job: Job, rec: dict[str, Any]) -> None:
    """The plan no longer includes the package: back up the panel's data, switch it off (keeping everything), tell
    the super admins."""
    name = rec.get("name", rec["id"])
    note = ""
    try:
        saved = plugin_manager.panel_backup(f"before-{rec['id']}-stops")
        job.note(f"Panel data saved: {saved.name}")
    except Exception as error:  # noqa: BLE001 - the plan changed: the feature stops even if the backup fails
        note = f" The panel's data could not be backed up first ({error})."
        job.note(f"The panel's data could not be backed up: {error}", "warning")
    _update(rec["id"], switched_off={"at": time.time(), "reason": "plan"})
    audit.record("system", None, "premium feature switched off (plan)", detail=rec["id"])
    _mail_super_admins(
        f"{name} was switched off",
        f"The Sudoo.sh plan of this panel no longer includes {name}, so the panel switched it off. Its data and "
        "settings are kept, and what it set up keeps working where the panel can do so on its own (restrictions "
        "stay enforced, custom domains keep serving and renewing their certificates). When the plan includes it again it comes back "
        f"on by itself.{note}",
    )
    events.publish(TOPIC, rec["id"], {"changed": True})


def start_sync(user: str = "system") -> Job | None:
    """Start the sync job unless one runs."""
    if jobs.busy(JOB_INSTANCE):
        return None
    return jobs.start(JOB_INSTANCE, "Install premium features", "premium-sync", sync, user, user=user)


def needs_sync(now: float | None = None) -> bool:
    """Whether a sync is due: the plan includes a package that is not installed (or is switched off, or has a
    problem), the plan dropped one that runs, a binding is due for renewal, or the check interval passed."""
    now = time.time() if now is None else now
    wanted = included_ids()
    installed = records()
    last = float(state().get("last_checked_at") or 0)
    if now - last < float(settings.premium_retry_minutes) * 60:
        return False  # asked a moment ago (it failed, or there was nothing new): not again so soon
    if any(p not in installed or installed[p].get("switched_off") or installed[p].get("problem") for p in wanted):
        return True
    if any(p not in wanted and not rec.get("switched_off") for p, rec in installed.items()):
        return True
    renew = float(settings.premium_binding_renew_days) * 86400
    if any(_binding_exp(rec) - now < renew for p, rec in installed.items() if p in wanted):
        return True
    return bool(wanted) and now - last > float(settings.premium_check_hours) * 3600


def after_lease() -> None:
    """Called after each verified lease (marketplace.py): start the sync when one is due and automatic installs are
    on. Never raises."""
    try:
        if settings.premium_auto_install and needs_sync():
            start_sync()
    except Exception:  # noqa: BLE001 - tried again after the next lease
        log.exception("premium: could not start the sync")


# ---------------------------------------------------------------- packages for the agents of other servers


AGENT_HEADERS = {
    "sha256": "X-Sudoo-Package-SHA256",
    "id": "X-Sudoo-Premium-Id",
    "version": "X-Sudoo-Premium-Version",
    "python_tag": "X-Sudoo-Premium-Python-Tag",
    "platform": "X-Sudoo-Premium-Platform",
    "signature": "X-Sudoo-Signature",
    "signing_key_id": "X-Sudoo-Signing-Key",
    "binding": "X-Sudoo-Premium-Binding",
    "binding_signature": "X-Sudoo-Premium-Binding-Signature",
    "binding_key_id": "X-Sudoo-Premium-Binding-Key",
}
"""How a package travels from the panel to an agent: the values of its record, as headers (download names)."""


def _agents_dir() -> Path:
    found = premium_dir() / ".agents"
    found.mkdir(parents=True, exist_ok=True, mode=0o700)
    return found


def _fresh_binding(package_id: str, version: str, sha256: str) -> dict[str, str]:
    """A new binding of a published build (any platform's) to this panel, from the hub."""
    query = urlencode({"version": version, "sha256": sha256, "fingerprint": fingerprint()})
    answer = marketplace._for_panel("GET", f"{HUB_PATH}/{package_id}/binding?{query}")
    data = answer.data if isinstance(answer.data, dict) else {}
    return {"binding": str(data.get("binding") or ""), "binding_signature": str(data.get("signature") or ""),
            "binding_key_id": str(data.get("key_id") or "")}  # fmt: skip


def build_for_agent(package_id: str, tag: str, platform: str) -> tuple[Path, dict[str, str]]:
    """In the panel: the build of a running package for an agent's Python and platform (a pure build when the
    panel runs one), downloaded from the hub and kept, with a binding to this panel that is renewed when it nears
    its end; (package file, headers to send). Raises PremiumRefused, HubRefused or HubUnavailable."""
    rec = record(package_id)
    if rec is None or package_id not in (LOADED or set()):
        raise PremiumRefused(f"{package_id} does not run in this panel.")
    if is_pure(rec.get("python_tag"), rec.get("platform")):
        tag, platform = str(rec["python_tag"]), str(rec["platform"])
    if not re.fullmatch(r"[a-z0-9]{2,12}", tag) or not re.fullmatch(r"[a-z0-9_-]{2,40}", platform):
        raise PremiumRefused("Not a Python tag and platform.")
    stem = f"{package_id}-{rec['version']}-{tag}-{platform}"
    package, meta_file = _agents_dir() / f"{stem}.zip", _agents_dir() / f"{stem}.json"
    try:
        meta = stored = json.loads(meta_file.read_text())
        seen = package.stat()
        same = (seen.st_size, seen.st_mtime_ns) == (meta.get("file_size"), meta.get("file_mtime_ns"))
        if not same:  # checked once more after any change, then known by its size and time
            with package.open("rb") as fh:
                same = hashlib.file_digest(fh, "sha256").hexdigest() == meta.get("sha256")
    except (OSError, ValueError):
        meta, stored, same = {}, {}, False
    if not same:
        for old in _agents_dir().glob(f"{package_id}-*"):
            old.unlink(missing_ok=True)
        query = urlencode({"version": rec["version"], "python_tag": tag, "platform": platform,
                           "fingerprint": fingerprint()})  # fmt: skip
        headers = client.download(f"{HUB_PATH}/{package_id}/download?{query}", package,
                                  int(settings.premium_max_package_mb) * 1024 * 1024)  # fmt: skip
        offer = {"id": package_id, "version": rec["version"], "python_tag": tag, "platform": platform,
                 "package_sha256": headers.get("x-sudoo-package-sha256", "")}  # fmt: skip
        try:
            meta = _verify_download(package, offer, headers)
        except PremiumRefused:
            package.unlink(missing_ok=True)
            raise
    renew = float(settings.premium_binding_renew_days) * 86400
    if _binding_exp(meta) - marketplace.trusted_now() < renew or binding_problem(meta):
        fresh = _fresh_binding(package_id, meta["version"], meta["sha256"])
        found = binding_problem({**meta, **fresh})
        if found:
            raise PremiumRefused(f"Sudoo.sh sent a binding the panel cannot use: {found}.")
        meta = {**meta, **fresh}
    from sudoo.core import vault

    seen = package.stat()
    meta = {**meta, "file_size": seen.st_size, "file_mtime_ns": seen.st_mtime_ns}
    if meta != stored:
        vault.write_private(meta_file, json.dumps(meta))
    return package, {header: str(meta[key]) for key, header in AGENT_HEADERS.items()}


def install_for_agent(package: Path, headers: dict[str, str]) -> dict[str, Any]:
    """In an agent: install a package the main panel sent (headers as AGENT_HEADERS names them). The first one names
    the panel the agent's packages are bound to; every later one must name the same. Raises PremiumRefused."""
    got = {key: str(headers.get(header.lower()) or "") for key, header in AGENT_HEADERS.items()}
    if got["id"] not in PACKAGES or not plugin_manager.SEMVER_RE.match(got["version"]):
        raise PremiumRefused("The panel sent a package this agent does not know.")
    if not build_accepted(got["python_tag"], got["platform"]):
        raise PremiumRefused(f"The panel sent a build for another Python ({got['python_tag']}, {got['platform']}).")
    with package.open("rb") as fh:
        if hashlib.file_digest(fh, "sha256").hexdigest() != got["sha256"].lower():
            raise PremiumRefused("The package's SHA-256 does not match the one stated.")
    found = signature_problem(got)
    if found:
        raise PremiumRefused(f"Not installed: {found}.")
    if not agent_identity().get("server_id"):
        key = keys.trusted().get(got["binding_key_id"])
        if key is None:
            raise PremiumRefused("Not installed: its binding is signed with a key this panel does not trust.")
        try:
            binding = crypto.read_binding(got["binding"], got["binding_signature"], key)
        except ValueError as error:
            raise PremiumRefused(f"Not installed: its binding is not valid ({error}).") from None
        agent_identity_file().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        from sudoo.core import vault

        vault.write_private(agent_identity_file(), json.dumps(
            {"server_id": binding["server_id"], "hardware_fingerprint": binding["hardware_fingerprint"]}))  # fmt: skip
    found = binding_problem(got)
    if found:
        raise PremiumRefused(f"Not installed: {found}.")
    with zipfile.ZipFile(package) as archive:
        manifest = check_package(archive, got)
        return _unpack(archive, manifest, got)


def update_agent_binding(package_id: str, values: dict[str, str]) -> bool:
    """In an agent: a fresh binding the panel sent for an installed package; kept when it is valid here."""
    rec = record(package_id)
    if rec is None:
        return False
    fresh = {key: str(values.get(key) or "") for key in ("binding", "binding_signature", "binding_key_id")}
    if binding_problem({**rec, **fresh}):
        return False
    _update(package_id, **fresh, problem=None, binding_renewed_at=time.time())
    return True


def forget_agent_packages() -> bool:
    """In an agent leaving its panel: the packages, their records and the panel they were bound to."""
    had = agent_identity_file().exists() or bool(records())
    for package_id in list(records()):
        storage().delete(COLLECTION, package_id)
    shutil.rmtree(premium_dir(), ignore_errors=True)
    agent_identity_file().unlink(missing_ok=True)
    return had


# ---------------------------------------------------------------- the integrity pass


def watch() -> None:
    """One integrity pass: files, signature, binding and plan of every package; a restart when what should run
    differs from what runs."""
    loaded = activate()
    wanted = included_ids()
    should: set[str] = set()
    for package_id, rec in records().items():
        if package_id in loaded and not verify_files(rec):
            continue
        ok, why = runs(rec, wanted)
        if ok:
            should.add(package_id)
        elif package_id in loaded and why and why != "your plan does not include it":
            _update(package_id, problem=why)
            audit.record("system", None, "premium feature not allowed to run", ok=False, detail=f"{package_id}: {why}")
            events.publish(TOPIC, package_id, {"changed": True})
    if should != loaded:
        plugin_manager.schedule_restart("premium features changed (integrity pass)")


def watch_forever() -> None:
    while True:
        _WAKE.wait(max(60.0, float(settings.premium_watch_minutes) * 60))
        _WAKE.clear()
        try:
            watch()
        except Exception:  # noqa: BLE001 - the loop must survive anything
            log.exception("premium integrity pass failed")


def wake() -> None:
    _WAKE.set()


# ---------------------------------------------------------------- what the API shows


def file_of(package_id: str, path: str) -> tuple[bytes, str] | None:
    """A web file of a loaded package, only when its content still has the hash its signed manifest states:
    (bytes, sha256)."""
    rec = record(package_id)
    if rec is None or package_id not in (LOADED or set()):
        return None
    digest = ((rec.get("ui") or {}).get("files") or {}).get(path)
    if not digest or not UI_PATH_RE.match(path) or ".." in path:
        return None
    file = folder(rec) / "package" / path
    try:
        data = file.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != digest:
        return None
    return data, digest


def overview(details: bool) -> dict[str, Any]:
    """Every premium package: whether the plan includes it, installed, pending, failed or switched off, and the web
    parts of those that run."""
    wanted = included_ids()
    installed = records()
    current = state()
    failures = current.get("failures") or {}
    loaded = LOADED or set()
    items = []
    for package_id, package in PACKAGES.items():
        rec = installed.get(package_id)
        if package_id in loaded and rec:
            status = "active"
        elif rec and rec.get("switched_off"):
            status = "switched_off"
        elif package_id not in wanted:
            status = "not_included"
        elif rec and rec.get("problem"):
            status = "failed"
        elif rec and package_id not in loaded:
            status = "restart_pending" if plugin_manager.restart_pending() else "pending"
        elif package_id in failures:
            status = "failed"
        else:
            status = "pending"
        item: dict[str, Any] = {
            "id": package_id,
            "name": package.name,
            "features": list(package.features),
            "plans": package.plans,
            "included": package_id in wanted,
            "status": status,
            "version": rec.get("version") if rec else None,
            "installed_at": rec.get("installed_at") if rec else None,
        }
        if details:
            item["problem"] = (rec or {}).get("problem") or (failures.get(package_id) or {}).get("error")
            item["binding_until"] = _binding_exp(rec) or None if rec else None
        if status == "active" and rec:
            ui = rec.get("ui") or {}
            item["ui"] = {key: ui.get(key) for key in ("routes", "sections", "styles", "strings", "files")}
        items.append(item)
    job = jobs.ACTIVE_JOB.get(JOB_INSTANCE)
    return {
        "packages": items,
        "auto_install": bool(settings.premium_auto_install),
        "marketplace": bool(settings.marketplace),
        "last_checked_at": current.get("last_checked_at"),
        "last_error": current.get("last_error") if details else None,
        "job": job.id if job else None,
        "build": {"python_tag": python_tag(), "platform": platform_tag()},
    }


def _mail_super_admins(subject: str, text: str) -> None:
    try:
        from sudoo import accounts
        from sudoo.mail import mailer, send
        from sudoo.mail.render import panel_url

        if not mailer().ready:
            return
        panel = panel_url()
        for user in accounts.all_users().values():
            if user.get("role") == "super_admin" and user.get("email"):
                send(user["email"], "plugin_notice", notice_title=subject, notice_text=text,
                     plugins_link=f"{panel}/settings?tab=plan" if panel else "")  # fmt: skip
    except Exception:  # noqa: BLE001 - a missing e-mail never stops premium work
        log.exception("premium: could not e-mail the super admins")
