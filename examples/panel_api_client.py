"""A small client for the panel's API (/api/v1), using only Python's standard library.

Every request carries the API token and an HMAC-SHA256 signature over a timestamp, a one-time nonce, the method,
the path and the body's SHA-256 (see docs/PANEL_API.md). Copy this file into any project, or run it:

    export SUDOO_URL=https://panel.example.com SUDOO_TOKEN=sudoo_... SUDOO_SIGNING_SECRET=...
    python panel_api_client.py                       # list instances
    python panel_api_client.py restart sales-main    # run an action and wait for it
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from typing import Any


class PanelAPI:
    """Signed calls to the panel's API."""

    def __init__(self, url: str, token: str, signing_secret: str) -> None:
        self.url, self.token, self.signing_secret = url.rstrip("/"), token, signing_secret.encode()

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else b""
        timestamp, nonce = str(int(time.time())), secrets.token_urlsafe(24)
        message = "\n".join([timestamp, nonce, method, path, hashlib.sha256(data).hexdigest()]).encode()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Sudoo-Timestamp": timestamp,
            "X-Sudoo-Nonce": nonce,
            "X-Sudoo-Signature": "sha256=" + hmac.new(self.signing_secret, message, hashlib.sha256).hexdigest(),
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        call = urllib.request.Request(self.url + path, data=data or None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(call, timeout=60) as response:
                result: dict[str, Any] = json.loads(response.read() or b"{}")
                return result
        except urllib.error.HTTPError as error:  # errors are RFC 9457 problem details
            problem = json.loads(error.read() or b"{}")
            detail = problem.get("detail", error.reason)
            raise RuntimeError(
                f"{error.code} {problem.get('title', '')}: {detail} ({problem.get('request_id')})"
            ) from None

    def instances(self) -> list[dict[str, Any]]:
        """Every instance (follows the pages of the list)."""
        found: list[dict[str, Any]] = []
        while True:
            page = self.request("GET", f"/api/v1/instances?limit=100&offset={len(found)}")
            found += page["data"]
            if len(found) >= page["meta"]["total"] or not page["data"]:
                return found

    def create(self, wait: bool = True, **fields: Any) -> dict[str, Any]:
        """Create an instance (fields as in docs/PANEL_API.md); returns the finished job, or the started one."""
        job: dict[str, Any] = self.request("POST", "/api/v1/instances", fields)["data"]
        return self.wait(job["id"]) if wait else job

    def action(self, name: str, action: str, wait: bool = True, **fields: Any) -> dict[str, Any]:
        """Run an action (restart, update, pause, start, ...) and, by default, wait until it is finished."""
        job: dict[str, Any] = self.request("POST", f"/api/v1/instances/{name}/actions/{action}", fields)["data"]
        return self.wait(job["id"]) if wait else job

    def wait(self, job_id: str, every: float = 5) -> dict[str, Any]:
        """Poll a job until it has finished; returns it (status "success" or "failed", with "error")."""
        while True:
            job: dict[str, Any] = self.request("GET", f"/api/v1/jobs/{job_id}")["data"]
            if job["status"] in ("success", "failed"):
                return job
            time.sleep(every)


if __name__ == "__main__":
    api = PanelAPI(os.environ["SUDOO_URL"], os.environ["SUDOO_TOKEN"], os.environ["SUDOO_SIGNING_SECRET"])
    if len(sys.argv) == 3:
        result = api.action(sys.argv[2], sys.argv[1])
        print(f"{sys.argv[1]} {sys.argv[2]}: {result['status']}")
    else:
        for instance in api.instances():
            print(f"{instance['name']:<32} {instance['status']:<12} {instance['url'] or ''}")
