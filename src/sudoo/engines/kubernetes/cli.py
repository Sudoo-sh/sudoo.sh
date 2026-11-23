"""The Kubernetes engine's commands, as the panel runs every external command (an argument list, its standard input,
output and exit code): `python -m sudoo.engines.kubernetes <command>`, the counterpart of the docker command line.

    exec <instance> <odoo|db> [--stdin] -- <argv...>       in the running pod (docker exec [-i])
    run <instance> -- <argv...>                              in a new runner Job beside Odoo, with its volumes, then
                                                             the Job is deleted (docker compose run --rm)
    copy-out <instance> <odoo> <folder>                      the folder as a tar stream whose entries start with its
                                                             own name, from a runner (docker cp <c>:<folder> -)
    logs <instance> [--tail N | --since T] [--until T] [--follow]   Odoo's log with time stamps (docker logs -t)

The exit code is the command's own (1 when the cluster refused, with the reason on standard error). Nothing secret is
printed: the access comes from the panel's settings, as in the panel.
"""

from __future__ import annotations

import argparse
import asyncio
import posixpath
import secrets
import signal
import sys
from datetime import datetime
from typing import IO, Any

from . import manifests, streams
from .api import Api, ApiError


class Failed(Exception):
    pass


def _api() -> Api:
    from .engine import api

    return api()


def _pod(client: Api, name: str, role: str) -> dict[str, Any]:
    from .engine import running_pod

    found = running_pod(client, name, role)
    if found is None:
        what = "Odoo" if role == "odoo" else "The database"
        raise Failed(f"{what} of {name} is not running on Kubernetes.")
    return found


def _exec(client: Api, namespace: str, pod: str, container: str, argv: list[str], stdin: IO[bytes] | None) -> int:
    out, err = sys.stdout.buffer, sys.stderr.buffer

    def write(stream: IO[bytes]) -> Any:
        def writer(data: bytes) -> None:
            stream.write(data)
            stream.flush()

        return writer

    return asyncio.run(streams.run(client, namespace, pod, container, argv, stdin, write(out), write(err)))


class Runner:
    """A runner Job of the instance and its pod, deleted on leaving (also when the panel stops the command)."""

    def __init__(self, client: Api, name: str) -> None:
        self.client, self.name = client, name
        self.namespace = manifests.namespace_of(name)
        self.job = f"odoo-run-{secrets.token_hex(4)}"

    def __enter__(self) -> str:
        from sudoo.config import settings
        from sudoo.instances import resources, store

        from .engine import wait

        env = store.read_env(self.name)
        image = f"{settings.odoo_image}:{env['ODOO_VERSION']}"
        self.client.create(manifests.runner_job(self.name, self.job, image, resources.effective(self.name)))
        selector = f"job-name={self.job}"
        found: dict[str, Any] = {}

        def started() -> bool:
            for pod in self.client.list("Pod", self.namespace, selector):
                phase = (pod.get("status") or {}).get("phase")
                if phase in ("Failed", "Succeeded"):
                    raise Failed(f"The runner pod ended before its command ({phase}).")
                if phase == "Running":
                    found.update(pod)
                    return True
            return False

        wait("the runner pod's start", started, float(settings.kubernetes_runner_start_seconds), 1)
        return str(found["metadata"]["name"])

    def __exit__(self, *exc: object) -> None:
        try:
            self.client.delete("Job", self.job, self.namespace)
        except ApiError as error:
            print(f"The runner Job {self.job} could not be deleted: {error}", file=sys.stderr)


def _rfc3339(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def logs(client: Api, name: str, tail: int, since: str | None, until: str | None, follow: bool) -> int:
    from .engine import server_pods

    pods = server_pods(client, name, "odoo")
    if not pods:
        return 0  # Odoo does not run: no log (docker logs of a removed container prints nothing either)
    pod = sorted(pods, key=lambda p: p["metadata"].get("creationTimestamp") or "")[-1]
    query: dict[str, Any] = {"container": "odoo", "timestamps": "true"}
    if since:
        query["sinceTime"] = since if "." not in since else since.split(".")[0] + "Z"
    else:
        query["tailLines"] = tail
    if follow:
        query["follow"] = "true"
    limit = _rfc3339(until) if until else None
    out = sys.stdout
    for line in client.log_lines(manifests.namespace_of(name), pod["metadata"]["name"], "odoo", query):
        if limit is not None:
            stamp = _rfc3339(line.split(" ", 1)[0][:26].rstrip("Z") + "+00:00")
            if stamp is not None and stamp >= limit:
                break
        out.write(line + "\n")
        out.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudoo.engines.kubernetes")
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("exec")
    one.add_argument("instance")
    one.add_argument("service", choices=("odoo", "db"))
    one.add_argument("--stdin", action="store_true")
    one.add_argument("argv", nargs=argparse.REMAINDER)
    run = commands.add_parser("run")
    run.add_argument("instance")
    run.add_argument("argv", nargs=argparse.REMAINDER)
    copy = commands.add_parser("copy-out")
    copy.add_argument("instance")
    copy.add_argument("service", choices=("odoo",))
    copy.add_argument("folder")
    log = commands.add_parser("logs")
    log.add_argument("instance")
    log.add_argument("--tail", type=int, default=100)
    log.add_argument("--since")
    log.add_argument("--until")
    log.add_argument("--follow", action="store_true")
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # the runner Job is deleted on the way out

    from sudoo.instances import store

    if not store.NAME_RE.match(args.instance):
        print("Not an instance name.", file=sys.stderr)
        return 2
    command: list[str] = list(getattr(args, "argv", None) or [])
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        client = _api()
        namespace = manifests.namespace_of(args.instance)
        if args.command == "exec":
            pod = _pod(client, args.instance, args.service)
            stdin = sys.stdin.buffer if args.stdin else None
            return _exec(client, namespace, pod["metadata"]["name"], args.service, command, stdin)
        if args.command == "run":
            with Runner(client, args.instance) as pod_name:
                return _exec(client, namespace, pod_name, "odoo", command, sys.stdin.buffer)
        if args.command == "copy-out":
            parent, folder = posixpath.split(args.folder.rstrip("/"))
            with Runner(client, args.instance) as pod_name:
                # like docker cp: entries start with the folder's name; an empty tar when it does not exist yet
                script = 'if [ -d "$1/$2" ]; then exec tar -C "$1" -cf - "$2"; else exec tar -cf - -T /dev/null; fi'
                return _exec(client, namespace, pod_name, "odoo", ["sh", "-c", script, "sh", parent, folder], None)
        return logs(client, args.instance, args.tail, args.since, args.until, args.follow)
    except (ApiError, Failed) as error:
        print(f"Kubernetes: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - a job reads this line; the panel's log has the rest
        from sudoo.core.jobs import JobError

        if isinstance(error, JobError):
            print(str(error), file=sys.stderr)
            return 1
        raise
    finally:
        sys.stdout.flush()
