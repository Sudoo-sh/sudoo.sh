"""How long the backend takes from `sudoo.sh serve` to answering /healthz, and its memory once started.

    uv run python perf/cold_start.py <run folder> [--times 5]

Starts the panel with <run folder>/panel.env (perf/seed.py) the way the container does, waits for /healthz,
records the time and the resident memory of the gunicorn worker, stops it, and repeats. Prints JSON and writes it
to <run folder>/cold_start.json.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path


def env_of(run: Path) -> dict[str, str]:
    values = dict(os.environ)
    for line in (run / "panel.env").read_text().splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def rss_kb(pid: int) -> int:
    """Resident memory of a process and its children (gunicorn's worker), in KB."""
    total = 0
    pids = [pid] + [
        int(p) for p in subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    ]
    for one in pids:
        try:
            for line in Path(f"/proc/{one}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
        except OSError:
            pass
    return total


def once(run: Path) -> tuple[float, int]:
    env = env_of(run)
    url = f"http://127.0.0.1:{env['API_PORT']}/healthz"
    began = time.perf_counter()
    proc = subprocess.Popen(["sudoo.sh", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while True:
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.02)
            if time.perf_counter() - began > 60:
                raise TimeoutError("the panel did not answer within 60 s")
        seconds = time.perf_counter() - began
        time.sleep(2)  # let the background work settle before reading memory
        return seconds, rss_kb(proc.pid)
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(30)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--times", type=int, default=5)
    args = parser.parse_args()
    results = [once(args.run.resolve()) for _ in range(args.times)]
    seconds = [r[0] for r in results]
    result = {
        "runs": args.times,
        "start_median_s": round(statistics.median(seconds), 3),
        "start_min_s": round(min(seconds), 3),
        "start_max_s": round(max(seconds), 3),
        "rss_mb": round(statistics.median(r[1] for r in results) / 1024, 1),
    }
    text = json.dumps(result, indent=1)
    (args.run / "cold_start.json").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
