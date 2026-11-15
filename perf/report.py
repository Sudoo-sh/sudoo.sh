"""Tables of one load-test run, or a before/after comparison of two (Markdown, as in docs/PERFORMANCE.md).

uv run python perf/report.py <run folder>
uv run python perf/report.py <run folder before> <run folder after>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

FILES = (("Panel", "panel.json"), ("Gateway: direct to Odoo", "gateway-direct.json"), ("Gateway", "gateway.json"))


def load(run: Path, name: str) -> dict[str, Any]:
    try:
        loaded: dict[str, Any] = json.loads((run / name).read_text())
        return loaded
    except (OSError, ValueError):
        return {}


def cell(row: dict[str, Any] | None, key: str) -> str:
    value = (row or {}).get(key)
    return "-" if value is None else f"{value:g}" if isinstance(value, float | int) else str(value)


def main() -> None:
    runs = [Path(arg) for arg in sys.argv[1:]]
    if not 1 <= len(runs) <= 2:
        sys.exit(__doc__)
    keys = ("rps", "p50_ms", "p95_ms", "p99_ms")
    for title, name in FILES:
        results = [load(run, name) for run in runs]
        print(f"\n### {title}\n")
        if len(runs) == 1:
            print("| Path | req/s | p50 ms | p95 ms | p99 ms | failed % | KB or MB/s |")
            print("| :--- | ---: | ---: | ---: | ---: | ---: | ---: |")
            for path, row in results[0].items():
                size = row.get("kb_per_request", row.get("mb_per_second"))
                print(
                    f"| {path} | "
                    + " | ".join(cell(row, k) for k in (*keys, "failed_percent"))
                    + f" | {cell({'v': size}, 'v')} |"
                )
        else:
            print("| Path | req/s before | req/s after | p50 ms before | p50 ms after | p95 ms before | p95 ms after "
                  "| p99 ms before | p99 ms after |")  # fmt: skip
            print("| :--- |" + " ---: |" * 8)
            for path in results[1] or results[0]:
                before, after = results[0].get(path), results[1].get(path)
                print(f"| {path} | " + " | ".join(f"{cell(before, k)} | {cell(after, k)}" for k in keys) + " |")
    lighthouse = [load(run, "lighthouse.json") for run in runs]
    if any(lighthouse):
        print("\n### First load (Lighthouse, mobile)\n")
        print(
            "| Page | Measure | "
            + " | ".join(("before", "after")[: len(runs)] if len(runs) == 2 else ("value",))
            + " |"
        )
        print("| :--- | :--- |" + " ---: |" * len(runs))
        for page in lighthouse[-1] or lighthouse[0]:
            for key in (lighthouse[-1] or lighthouse[0])[page]:
                print(f"| {page} | {key} | " + " | ".join(cell(r.get(page), key) for r in lighthouse) + " |")
    for title, name in (("Live events", "sse.json"), ("Cold start", "cold_start.json")):
        results = [load(run, name) for run in runs]
        print(f"\n### {title}\n")
        print("| | " + " | ".join(("before", "after")[: len(runs)] if len(runs) == 2 else ("value",)) + " |")
        print("| :--- |" + " ---: |" * len(runs))
        for key in results[-1] or results[0]:
            print(f"| {key} | " + " | ".join(cell(r, key) for r in results) + " |")
    print("\n### Peak memory of the backend\n")
    for run in runs:
        for name in ("peak_rss_panel.txt", "peak_rss_gateway.txt"):
            path = run / name
            print(f"- {run.name} {name}: {path.read_text().strip() if path.exists() else '-'}")


if __name__ == "__main__":
    main()
