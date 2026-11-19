"""A server's agent (`sudoo.sh agent`, container sudoo-agent, the panel's image): the core's part.

A panel whose plan includes several servers (the premium package "servers") manages other servers through an agent
on each of them. The agent's work (joining, its signed channel to the panel, answering the panel's requests) is the
package's; the core only brings that package to the new server and starts it:

    sudoo.sh agent join <code>   fetch the panel's premium packages with the join code, verify them, install them
                                 for this agent, then join (the package's "agent" hook); prints this server's
                                 fingerprint ("Server fingerprint: ...", read by the installer)
    sudoo.sh agent status        joined or not, approved or not, connected or not (--json)
    sudoo.sh agent leave         forget the panel, this server's key and the packages (the instances stay)
    sudoo.sh agent [run]         run (the container's command)

Fetching packages. The join code is "sudoo-join-v1." followed by base64url JSON whose "u" is the panel's address.
The agent asks the panel GET <u>/api/v1/servers/agent-packages?python_tag=&platform= (the packages to run, built for
this server's Python and platform), then each GET .../agent-packages/<id>?..., with the join code in
X-Sudoo-Join-Code (later, the package signs these requests with the agent's key). Each answer carries the same
headers as a download from Sudoo.sh: the SHA-256, the premium key's signature and the binding to the main panel.
The agent installs a package only when that signature verifies with a trusted premium key and its binding names
the panel it joined (premium/loader.py, agent mode): a package copied to a server without that panel is refused.
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from sudoo.config import settings
from sudoo.hub.crypto import from_b64url

from . import SERVERS, hook, servers

JOIN_PREFIX = "sudoo-join-v1."
PACKAGES_PATH = "/api/v1/servers/agent-packages"
JOIN_HEADER = "X-Sudoo-Join-Code"


class AgentError(Exception):
    """Something people must fix before the agent can join or run (the message says what)."""


def panel_url_of(code: str) -> str:
    """The panel's address in a join code (the package checks the rest of the code)."""
    code = (code or "").strip()
    if not code.startswith(JOIN_PREFIX):
        raise AgentError("This is not a Sudoo.sh join code: it starts with sudoo-join-v1.")
    try:
        body = json.loads(from_b64url(code[len(JOIN_PREFIX) :]))
    except ValueError:
        raise AgentError("The join code is damaged: copy it again from the panel.") from None
    url = body.get("u") if isinstance(body, dict) else None
    if not isinstance(url, str) or not url.startswith(("https://", "http://")) or any(c.isspace() for c in url):
        raise AgentError("The join code is damaged: copy it again from the panel.")
    return url.rstrip("/")


def _get(url: str, headers: Mapping[str, str], timeout: float = 120) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:  # noqa: S310 - the panel's address
            limit = int(settings.premium_max_package_mb) * 1024 * 1024
            data = answer.read(limit + 1)
            if len(data) > limit:
                raise AgentError("The panel sent a package larger than PREMIUM_MAX_PACKAGE_MB.")
            return data, {k.lower(): v for k, v in answer.headers.items()}
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        raise AgentError(str(detail or f"The panel refused (HTTP {error.code}).")) from None
    except (OSError, ValueError) as error:
        raise AgentError(f"The panel at {url.split('/api/', 1)[0]} could not be reached: {error}") from None


def fetch_packages(panel_url: str, headers: Callable[[], Mapping[str, str]]) -> list[str]:
    """Fetch and install the packages the panel runs, for this agent; the ids installed. `headers()` gives the
    request's authentication (the join code, or the package's signature) for each request."""
    from . import loader

    query = urlencode({"python_tag": loader.python_tag(), "platform": loader.platform_tag()})
    data, _ = _get(f"{panel_url}{PACKAGES_PATH}?{query}", headers())
    try:
        listed = json.loads(data).get("data")
    except (ValueError, AttributeError):
        listed = None
    if not isinstance(listed, list):
        raise AgentError("The panel's answer is not a list of packages.")
    installed = []
    for item in listed:
        package_id = str((item or {}).get("id") or "") if isinstance(item, dict) else ""
        if package_id not in loader.PACKAGES:
            continue
        body, found = _get(f"{panel_url}{PACKAGES_PATH}/{quote(package_id)}?{query}", headers())
        with tempfile.TemporaryDirectory(prefix="sudoo-agent-") as work:
            package = Path(work) / f"{package_id}.zip"
            package.write_bytes(body)
            try:
                loader.install_for_agent(package, found)
            except loader.PremiumRefused as error:
                raise AgentError(f"{package_id}: {error}") from None
        installed.append(package_id)
    if "servers" not in installed:
        raise AgentError("The panel does not run several servers (its plan, or the package is not installed).")
    return installed


def _agent() -> Any:
    """The servers package's agent hooks (join, status, leave, run), once its package is installed here."""
    from . import loader

    servers.become_agent()
    loader.activate()
    found = hook(SERVERS, "agent") if "servers" in (loader.LOADED or set()) else None
    if found is None:
        raise AgentError(
            "This server has no verified package for several servers: run sudoo-install agent --join with a join "
            "code from the panel (Servers, Add a server)."
        )
    return found


def join(code: str) -> dict[str, Any]:
    from . import loader

    url = panel_url_of(code)
    servers.become_agent()
    if loader.agent_identity().get("server_id") and _status().get("joined"):
        raise AgentError("This server joined a panel already. Run `sudoo.sh agent leave` first.")
    fetch_packages(url, lambda: {JOIN_HEADER: code.strip()})
    try:
        return dict(_agent().join(code.strip()))
    except ValueError as error:
        raise AgentError(str(error)) from None


def _status() -> dict[str, Any]:
    try:
        return dict(_agent().status())
    except AgentError:
        return {"joined": False}


def leave() -> bool:
    from . import loader

    had = False
    with contextlib.suppress(AgentError):
        had = bool(_agent().leave())
    return loader.forget_agent_packages() or had


def run() -> None:
    _agent().run()


def main(action: str, code: str, as_json: bool) -> None:
    """`sudoo.sh agent ...` (cli.py)."""
    try:
        if action == "join":
            if not code:
                sys.exit("Give the join code: sudoo.sh agent join <code> (Servers, Add a server, in the panel).")
            found = join(code)
            print(
                f"Joined the panel at {found.get('panel_url')} as {found.get('name') or found.get('server_id')}.\n"
                f"Server fingerprint: {found.get('fingerprint')}\n"
                "In the panel (Servers), approve this server by typing this fingerprint. Panel fingerprint: "
                f"{found.get('panel_fingerprint')}"
            )
        elif action == "status":
            servers.become_agent()
            found = _status()
            if as_json:
                print(json.dumps(found))
            elif not found.get("joined"):
                print("This server has not joined a panel: sudoo.sh agent join <code>")
            else:
                for key, value in found.items():
                    print(f"{key:<18} {value}")
        elif action == "leave":
            servers.become_agent()
            print("The panel and this server's key are forgotten." if leave() else "Nothing to forget.")
        else:
            run()
    except AgentError as error:
        sys.exit(str(error))
