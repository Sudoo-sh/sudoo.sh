"""The web app's first load, as Lighthouse measures it (mobile, slow 4G and a slower processor, simulated): the
sign-in page, and the dashboard of a signed-in user.

    uv run python perf/lighthouse.py <run folder> [--base http://127.0.0.1:19100] [--runs 3]

Needs Node.js (npx downloads Lighthouse 12 once) and Chrome or Chromium: CHROME_PATH, or the Chromium of a
Playwright installation. Prints JSON and writes it to <run folder>/lighthouse.json: for each page the median of
--runs runs of the performance score, First and Largest Contentful Paint, Total Blocking Time, Speed Index, and the
bytes transferred (all, and JavaScript).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

AUDITS = {
    "fcp_ms": "first-contentful-paint",
    "lcp_ms": "largest-contentful-paint",
    "tbt_ms": "total-blocking-time",
    "speed_index_ms": "speed-index",
}


def chrome() -> str:
    found = os.environ.get("CHROME_PATH") or next(
        iter(sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")))), ""
    )
    if not found:
        raise SystemExit("Set CHROME_PATH to a Chrome or Chromium executable.")
    return found


def sign_in(base: str, secrets: dict[str, str]) -> str:
    request = urllib.request.Request(
        f"{base}/api/v1/session",
        data=json.dumps({"username": secrets["username"], "password": secrets["password"]}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        cookie = response.headers["Set-Cookie"]
    return cookie.split(";", 1)[0]


def lighthouse(url: str, headers: dict[str, str] | None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        command = [
            "npx", "--yes", "lighthouse@12", url, "--quiet", "--only-categories=performance", "--output=json",
            f"--output-path={report}", "--chrome-flags=--headless=new --no-sandbox --disable-gpu",
        ]  # fmt: skip
        if headers:
            (Path(tmp) / "headers.json").write_text(json.dumps(headers))
            command.append(f"--extra-headers={Path(tmp) / 'headers.json'}")
        subprocess.run(command, check=True, env={**os.environ, "CHROME_PATH": chrome()}, capture_output=True)
        data = json.loads(report.read_text())
    audits = data["audits"]
    items = audits["resource-summary"]["details"]["items"]
    by_type = {item["resourceType"]: item for item in items}
    return {
        "score": round(100 * data["categories"]["performance"]["score"]),
        **{key: round(audits[audit]["numericValue"]) for key, audit in AUDITS.items()},
        "transfer_kb": round(by_type["total"]["transferSize"] / 1024, 1),
        "script_kb": round(by_type.get("script", {}).get("transferSize", 0) / 1024, 1),
        "requests": by_type["total"]["requestCount"],
    }


def median(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {key: statistics.median(run[key] for run in runs) for key in runs[0]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--base", default="http://127.0.0.1:19100")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    secrets = json.loads((args.run / "secrets.json").read_text())
    cookie = sign_in(args.base, secrets)
    result = {
        "sign_in_page": median([lighthouse(f"{args.base}/login", None) for _ in range(args.runs)]),
        "dashboard": median([lighthouse(f"{args.base}/", {"Cookie": cookie}) for _ in range(args.runs)]),
    }
    text = json.dumps(result, indent=1)
    (args.run / "lighthouse.json").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
