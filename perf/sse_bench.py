"""Live events under load: many browsers on GET /api/v1/events, and how fast one change reaches all of them.

    uv run python perf/sse_bench.py <run folder> [--clients 300] [--rounds 20] [--base http://127.0.0.1:19100]

Signs in, opens --clients event streams through the web service, then changes a harmless setting --rounds times
(PATCH /api/v1/settings) and measures, for every client, the time from sending the change to receiving its
"settings" event: the fan-out latency (p50/p95/p99), plus the time to open a stream. Prints JSON and writes it to
<run folder>/sse.json. k6 has no Server-Sent Events client, hence this script (aiohttp is a panel dependency).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import aiohttp


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(share * len(ordered)) - 1))] if ordered else float("nan")


async def client(session: aiohttp.ClientSession, url: str, opened: list[float], arrivals: asyncio.Queue) -> None:
    began = time.perf_counter()
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=None, sock_read=None)) as response:
        response.raise_for_status()
        first = True
        event = ""
        async for raw in response.content:
            if first:
                opened.append((time.perf_counter() - began) * 1000)
                first = False
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event == "settings":
                arrivals.put_nowait(time.perf_counter())


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--clients", type=int, default=300)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--base", default="http://127.0.0.1:19100")
    args = parser.parse_args()
    secrets = json.loads((args.run / "secrets.json").read_text())
    connector = aiohttp.TCPConnector(limit=0)
    # unsafe: the panel is reached by its IP address here, whose cookies aiohttp ignores otherwise
    async with aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        async with session.post(
            f"{args.base}/api/v1/session", json={"username": secrets["username"], "password": secrets["password"]}
        ) as response:
            response.raise_for_status()
            csrf = (await response.json())["data"]["csrf_token"]
        opened: list[float] = []
        arrivals: asyncio.Queue[float] = asyncio.Queue()
        tasks = [
            asyncio.create_task(client(session, f"{args.base}/api/v1/events?topics=settings", opened, arrivals))
            for _ in range(args.clients)
        ]
        while len(opened) < args.clients:
            await asyncio.sleep(0.05)
            if any(task.done() for task in tasks):
                [task.result() for task in tasks if task.done()]
        latencies: list[float] = []
        missing = 0
        for number in range(args.rounds):
            sent = time.perf_counter()
            async with session.patch(
                f"{args.base}/api/v1/settings",
                json={"admin_contact_phone": f"+1 555 {number:04d}"},
                headers={"X-CSRF-Token": csrf},
            ) as response:
                response.raise_for_status()
            got = 0
            deadline = time.perf_counter() + 10
            while got < args.clients and time.perf_counter() < deadline:
                try:
                    arrived = await asyncio.wait_for(arrivals.get(), deadline - time.perf_counter())
                except TimeoutError:
                    break
                latencies.append((arrived - sent) * 1000)
                got += 1
            missing += args.clients - got
            await asyncio.sleep(0.2)
        async with session.patch(
            f"{args.base}/api/v1/settings", json={"admin_contact_phone": None}, headers={"X-CSRF-Token": csrf}
        ):
            pass
        for task in tasks:
            task.cancel()
    result = {
        "clients": args.clients,
        "rounds": args.rounds,
        "open_p50_ms": round(statistics.median(opened), 1),
        "open_p99_ms": round(percentile(opened, 0.99), 1),
        "fanout_p50_ms": round(percentile(latencies, 0.5), 1),
        "fanout_p95_ms": round(percentile(latencies, 0.95), 1),
        "fanout_p99_ms": round(percentile(latencies, 0.99), 1),
        "events_missed": missing,
    }
    text = json.dumps(result, indent=1)
    (args.run / "sse.json").write_text(text)
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
