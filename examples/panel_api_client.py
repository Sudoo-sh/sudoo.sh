"""A small client for the panel's API (/api/v1), using only Python's standard library.

Every request carries the API token and an HMAC-SHA256 signature over a timestamp, a one-time nonce, the method,
the path and the body's SHA-256 (see docs/PANEL_API.md). Copy this file into any project, or run it:

    export OIM_URL=https://panel.example.com OIM_TOKEN=oimt_... OIM_SIGNING_SECRET=...
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
            "X-OIM-Timestamp": timestamp,
            "X-OIM-Nonce": nonce,
            "X-OIM-Signature": "sha256=" + hmac.new(self.signing_secret, message, hashlib.sha256).hexdigest(),
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        call = urllib.request.Request(self.url + path, data=data or None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(call, timeout=60) as response:
                result: dict[str, Any] = json.loads(response.read() or b"{}")
                return result
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                f"{error.code}: {json.loads(error.read() or b'{}').get('error', error.reason)}"
            ) from None

    def instances(self) -> list[dict[str, Any]]:
        return list(self.request("GET", "/api/v1/instances")["instances"])

    def action(self, name: str, action: str, wait: bool = True) -> dict[str, Any]:
        """Run an action (restart, update, pause, start, ...) and, by default, wait until it is finished."""
        job = self.request("POST", f"/api/v1/instances/{name}/actions/{action}", {})
        return self.wait(job["job"]) if wait else job

    def wait(self, job_id: str, every: float = 5) -> dict[str, Any]:
        while True:
            job = self.request("GET", f"/api/v1/jobs/{job_id}")
            if job["status"] in ("success", "failed"):
                return job
            time.sleep(every)


if __name__ == "__main__":
    api = PanelAPI(os.environ["OIM_URL"], os.environ["OIM_TOKEN"], os.environ["OIM_SIGNING_SECRET"])
    if len(sys.argv) == 3:
        result = api.action(sys.argv[2], sys.argv[1])
        print(f"{sys.argv[1]} {sys.argv[2]}: {result['status']}")
    else:
        for instance in api.instances():
            print(f"{instance['name']:<32} {instance['status']:<12} {instance['url'] or ''}")
