"""Gunicorn hooks for the load tests only: fill the panel's in-memory history as a panel that ran for a while has it.

Used through GUNICORN_CMD_ARGS="--config perf/gunicorn_perf.py" with `sudoo.sh serve` (perf/run.sh), so the panel
starts exactly as in production and only gets, once its worker is ready:

    - one hour of metrics history of the server and every instance (the dashboard's charts)
    - 300 finished jobs with steps and a technical log each (the activity lists)

Jobs and metrics live in memory only (core/jobs.py, instances/metrics.py), so they cannot be seeded from outside.
"""

from __future__ import annotations

import random
import time
from typing import Any


def post_worker_init(worker: Any) -> None:  # noqa: ARG001 - gunicorn's signature
    from sudoo.config import settings
    from sudoo.core import jobs
    from sudoo.instances import metrics, store

    names = store.names()
    now = int(time.time())
    step = int(settings.metrics_interval_seconds)
    for t in range(now - 3600, now, step):
        # rounded as the monitor rounds its own samples (instances/monitor.py, metrics.py)
        metrics._SERVER_HISTORY.append(
            {"t": t, "cpu_percent": round(random.uniform(5, 60), 1), "memory_percent": round(random.uniform(30, 70), 1),
             "disk_percent": 41.5, "load_1m": round(random.uniform(0.1, 3), 2), "instances_running": len(names) // 2,
             "panel_cpu_percent": round(random.uniform(0.5, 8), 2), "panel_memory_bytes": random.randint(150, 400) * 2**20}
        )  # fmt: skip
        for name in names:
            metrics._INSTANCE_HISTORY[name].append(
                {"t": t, "cpu_percent": round(random.uniform(0, 20), 2), "memory_percent": round(random.uniform(10, 60), 1),
                 "memory_bytes": random.randint(200, 900) * 2**20, "requests_per_minute": round(random.uniform(0, 90), 1),
                 "error_rate_percent": 0.0, "response_ms_avg": round(random.uniform(20, 200), 1),
                 "response_ms_p95": round(random.uniform(100, 900), 1)}
            )  # fmt: skip
    actions = (("Restart", "restart"), ("Get latest code", "update"), ("Back up", "backup"), ("Pause", "pause"))
    with jobs._JOBS_LOCK:
        for number in range(300):
            action, kind = actions[number % len(actions)]
            job = jobs.Job(names[number % len(names)], action, kind, "perfadmin")
            job.started = now - (300 - number) * 60
            job.finished = job.started + 42
            job.status = "failed" if number % 11 == 0 else "success"
            job.steps = [{"title": f"Step {s}", "status": "done"} for s in range(5)]
            job.log = [f"line {line}: docker compose did something useful for a while\n" for line in range(200)]
            jobs.JOBS[job.id] = job
    print(f"perf seed: {len(names)} instances, 1 h of metrics, {len(jobs.JOBS)} jobs", flush=True)
