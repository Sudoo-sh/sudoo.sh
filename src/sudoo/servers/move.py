"""Moving an instance to another server: a job of the panel (kind "move"), with downtime from the second step on.

    1. Check the target: active, connected, room for one more, no instance of that name there
    2. Switch the instance off on its server (visitors see the "switched off" page from here on)
    3. Back it up there (an Odoo-format backup, as for any backup)
    4. Copy the backup to the target, piece by piece (MOVE_CHUNK_MB), and check its size and SHA-256
    5. Create the instance on the target from that backup: the same name, stage, project, branch, version and
       switches; the same INSTANCE_ID and admin password (the data is not neutralised: it is the same instance);
       it must answer there within MOVE_HEALTH_SECONDS
    6. Delete it on its old server (which keeps the deletion backup, as every deletion does)

When step 2 to 5 fails, everything is put back: the copy and a half-made instance on the target are removed and the
instance is started again on its old server; the job fails and says so. Moves at the same time: MOVE_AT_ONCE.
What does not move: custom domains (add them again on the new server and point their DNS at it), the instance's
own resource limits and its caught e-mails. Its public address changes with the server.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from typing import Any

from sudoo import roles, servers
from sudoo.config import settings
from sudoo.core.jobs import Job, JobError

from . import inventory, links, registry

POLL_SECONDS = 2.0
SAVED_RE = re.compile(r"Backup saved: (\S+\.zip)")


def _system(user: str) -> dict[str, Any]:
    """Who the agents act for during a move: the person who asked, with every instance permission (the panel
    checked instances.move before the job started)."""
    return {"name": f"{user} (move)", "role": "super_admin", "permissions": sorted(roles.PERMISSIONS), "via": "panel"}


class Side:
    """One server of the move: the main one (operations run here, inside the move job) or another one (through
    its agent, waiting for its jobs)."""

    def __init__(self, server_id: str, job: Job) -> None:
        self.id = server_id
        self.job = job
        self.local = server_id == servers.MAIN
        self.name = registry.name_of(server_id)

    # ------------------------------------------------ remote helpers

    def _http(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        import json

        answer = links.call(
            self.id,
            "http",
            {"method": method, "path": path, "query": "", "body": json.dumps(body) if body is not None else "",
             "user": _system(self.job.user)},  # fmt: skip
        )
        raw = base64.b64decode(answer.get("body") or "")
        try:
            document = json.loads(raw) if raw else {}
        except ValueError:
            document = {}
        return int(answer.get("status") or 500), document

    def _follow(self, job: dict[str, Any], what: str) -> dict[str, Any]:
        """Wait for a job of the other server; its steps are written into the move's log."""
        seen = 0
        while True:
            status, document = self._http("GET", f"/api/v1/jobs/{job['id']}")
            found = document.get("data") or {} if status == 200 else {}
            steps = found.get("steps") or []
            for step in steps[seen:]:
                self.job.write(f"  {self.name}: {step.get('title')}\n")
            seen = max(seen, len(steps))
            if found.get("status") in ("success", "failed"):
                if found["status"] == "failed":
                    raise JobError(f"{what} failed on {self.name}: {found.get('error') or 'see its job'}")
                return found
            time.sleep(POLL_SECONDS)

    def _action(self, instance: str, action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        status, document = self._http("POST", f"/api/v1/instances/{instance}/actions/{action}", body or {})
        if status != 202:
            detail = (document or {}).get("detail") or f"HTTP {status}"
            raise JobError(f"{action} was refused on {self.name}: {detail}")
        return self._follow(document["data"], action)

    # ------------------------------------------------ what a move does on a server

    def switch_off(self, instance: str) -> None:
        if self.local:
            from sudoo.instances import operations

            operations.switch_off(self.job, instance)
        else:
            self._action(instance, "switch-off", {"confirm_production": True})

    def start(self, instance: str) -> None:
        if self.local:
            from sudoo.instances import operations

            operations.wake(self.job, instance)
        else:
            self._action(instance, "start")

    def backup(self, instance: str) -> str:
        """Back up the instance; the backup's file name."""
        if self.local:
            from sudoo.instances import operations

            before = len(self.job.notes)
            operations.backup(self.job, instance)
            notes = [n["text"] for n in self.job.notes[before:]]
        else:
            notes = [n.get("text", "") for n in self._action(instance, "backup").get("notes") or []]
        for text in notes:
            found = SAVED_RE.search(text)
            if found:
                return found.group(1).rstrip(",")
        raise JobError(f"The backup on {self.name} did not say which file it saved.")

    def env(self, instance: str) -> dict[str, str]:
        from sudoo.instances import store

        if self.local:
            return store.read_env(instance)
        return inventory.env(instance)

    def admin_password(self, instance: str) -> str:
        if self.local:
            return self.env(instance).get("ODOO_ADMIN_PASSWORD", "")
        status, document = self._http("GET", f"/api/v1/instances/{instance}/credentials")
        return str((document.get("data") or {}).get("admin_password") or "") if status == 200 else ""

    def repository(self, instance: str) -> str:
        if self.local:
            from sudoo.instances import monitor, store
            from sudoo.repos.gitrepo import origin_url

            return str(monitor.STATUS.get(instance, {}).get("repo") or origin_url(store.addons_dir(instance)) or "")
        found = inventory.remote(instance) or {}
        return str(((found.get("resource") or {}).get("git") or {}).get("repository") or "")

    def size(self, file_name: str) -> int:
        if self.local:
            path = settings.backups_dir / file_name
            if not path.is_file():
                raise JobError(f"The backup {file_name} is not on {self.name}.")
            return path.stat().st_size
        return int(links.call(self.id, "file.stat", {"area": "backups", "name": file_name})["size"])

    def read(self, file_name: str, offset: int, length: int) -> bytes:
        if self.local:
            with (settings.backups_dir / file_name).open("rb") as fh:
                fh.seek(offset)
                return fh.read(length)
        answer = links.call(self.id, "file.read", {"area": "backups", "name": file_name, "offset": offset,
                                                   "length": length})  # fmt: skip
        return base64.b64decode(answer.get("data") or "")

    def write(self, file_name: str, offset: int, data: bytes, last: bool) -> None:
        if self.local:
            part = settings.backups_dir / f"{file_name}.part"
            settings.backups_dir.mkdir(parents=True, exist_ok=True)
            with part.open("r+b" if offset and part.exists() else "wb") as fh:
                fh.seek(offset)
                fh.write(data)
            if last:
                part.replace(settings.backups_dir / file_name)
            return
        links.call(self.id, "file.write", {"area": "backups", "name": file_name, "offset": offset,
                                           "data": base64.b64encode(data).decode(), "last": last},
                   timeout=max(60, settings.servers_request_seconds))  # fmt: skip

    def remove(self, file_name: str) -> None:
        if self.local:
            (settings.backups_dir / file_name).unlink(missing_ok=True)
            (settings.backups_dir / f"{file_name}.part").unlink(missing_ok=True)
        else:
            links.call(self.id, "file.remove", {"area": "backups", "name": file_name})

    def exists(self, instance: str) -> bool:
        from sudoo.instances import store

        if self.local:
            return store.exists(instance)
        status, _ = self._http("GET", f"/api/v1/instances/{instance}")
        return status == 200

    def create(self, plan: dict[str, Any]) -> None:
        if self.local:
            from sudoo import exposure
            from sudoo.instances import operations, store

            operations.create(self.job, {**plan, "ports": store.reserve_ports(exposure.provider().instance_ports())})
            return
        links.expect_owner(self.id, plan.get("repo") or "")
        job = links.call(self.id, "create", {"plan": plan, "user": self.job.user})
        self._follow(job, "Creating the instance")

    def answers(self, instance: str) -> bool:
        """Whether the instance's Odoo answers on this server, within MOVE_HEALTH_SECONDS."""
        deadline = time.monotonic() + settings.move_health_seconds
        if self.local:
            from sudoo.instances import odoo, store

            port = store.read_env(instance).get("ODOO_PORT", "")
            return bool(port) and odoo.wait_until_ready(port, timeout=settings.move_health_seconds)
        while time.monotonic() < deadline:
            status, document = self._http("GET", f"/api/v1/instances/{instance}")
            if status == 200 and (document.get("data") or {}).get("status") == "online":
                return True
            time.sleep(max(POLL_SECONDS, 1.0))
        return False

    def delete(self, instance: str, production: bool, without_backup: bool = False) -> None:
        if self.local:
            from sudoo.instances import operations

            operations.delete(self.job, instance, without_backup)
            return
        body: dict[str, Any] = {"confirm": instance, "without_backup": without_backup}
        if production:
            body["confirm_stage"] = "production"
        self._action(instance, "delete", body)


def copy(job: Job, source: Side, target: Side, file_name: str) -> None:
    """Copy a backup between two servers through the panel; checked by size and SHA-256 on both ends."""
    size = source.size(file_name)
    if size > settings.move_max_backup_gb * 1024**3:
        raise JobError(
            f"The backup is {size / 1024**3:.1f} GB, more than Largest backup to move ({settings.move_max_backup_gb:g} GB)."
        )
    chunk = int(settings.move_chunk_mb) * 1024 * 1024
    digest = hashlib.sha256()
    offset, reported = 0, 0
    while offset < size or size == 0:
        data = source.read(file_name, offset, chunk)
        if not data and offset < size:
            raise JobError(f"The backup on {source.name} ended early, at {offset} of {size} bytes.")
        digest.update(data)
        target.write(file_name, offset, data, last=offset + len(data) >= size)
        offset += len(data)
        if offset * 10 // max(size, 1) > reported:
            reported = offset * 10 // max(size, 1)
            job.write(f"  copied {offset * 100 // max(size, 1)}% ({offset // 1024**2} MB)\n")
        if size == 0:
            break
    if target.size(file_name) != size:
        raise JobError(f"The copy on {target.name} does not have the size of the backup.")
    check = hashlib.sha256()
    offset = 0
    while offset < size:  # read back from the target: what it stored is what was sent
        data = target.read(file_name, offset, chunk)
        if not data:
            break
        check.update(data)
        offset += len(data)
    if check.hexdigest() != digest.hexdigest():
        raise JobError(f"The copy on {target.name} differs from the backup (SHA-256): nothing was changed.")
    job.note(f"Backup copied to {target.name}: {size // 1024**2} MB, SHA-256 {digest.hexdigest()[:16]}...")


def plan_for(
    name: str, env: dict[str, str], repo: str, file_name: str, admin_password: str, user: str
) -> dict[str, Any]:
    """The creation's plan on the target: the instance as it is, started from its backup."""
    return {
        "name": name,
        "db_name": env.get("DB_NAME") or name.replace("-", "_"),
        "repo": repo if env.get("SOURCE") != "plain" else "",
        "branch": env.get("GIT_BRANCH", ""),
        "version": env.get("ODOO_VERSION", ""),
        "edition": env.get("EDITION", "community"),
        "demo": False,
        "install_modules": False,
        "delete_after_days": int(env.get("DELETE_AFTER_DAYS") or 0),
        "auto_update": env.get("AUTO_UPDATE") == "true",
        "auto_upgrade": env.get("AUTO_UPGRADE") == "true",
        "auto_pause": env.get("AUTO_PAUSE") == "true",
        "stage": env.get("STAGE") or "development",
        "created_by": env.get("CREATED_BY") or user,
        "from_backup": file_name,
        "from_production": None,
        "project": env.get("PROJECT") or "",
        "moved": True,
        "admin_password": admin_password,
    }


def run(job: Job, name: str, source_id: str, target_id: str) -> None:
    """The move job (see the module's description)."""
    from sudoo.servers import placement

    source, target = Side(source_id, job), Side(target_id, job)
    job.expect(6)
    job.step(f"Checking {target.name}")
    try:
        placement.choose(target_id)
    except placement.Refused as error:
        raise JobError(error.message) from None
    if target.exists(name):
        raise JobError(f"{target.name} has an instance called {name} already.")
    env = dict(source.env(name))
    production = env.get("STAGE") == "production"
    repo = source.repository(name)
    admin_password = source.admin_password(name)
    copied = created = False
    file_name = ""
    job.step(f"Switching {name} off on {source.name} (the downtime starts)")
    source.switch_off(name)
    try:
        job.step(f"Backing up on {source.name}")
        file_name = source.backup(name)
        job.step(f"Copying the backup to {target.name}")
        copy(job, source, target, file_name)
        copied = True
        job.step(f"Creating {name} on {target.name} from the backup")
        created = True
        target.create(plan_for(name, env, repo, file_name, admin_password, job.user))
        if not target.answers(name):
            raise JobError(f"{name} did not answer on {target.name} within {settings.move_health_seconds} seconds.")
    except Exception as error:
        job.write(f"\nThe move failed ({error}); putting everything back.\n")
        _roll_back(job, source, target, name, file_name if copied else "", created)
        raise JobError(f"The move failed and was rolled back: {name} runs on {source.name} again. {error}") from None
    job.step(f"Deleting {name} on {source.name}")
    try:
        source.delete(name, production)
    except Exception as error:  # noqa: BLE001 - the instance runs on the target: the old copy is left switched off
        job.note(
            f"{name} runs on {target.name}, but its old copy on {source.name} could not be deleted ({error}); it stays "
            "switched off there. Delete it on that server.",
            "warning",
        )
    job.note(
        f"{name} now runs on {target.name}. Its public address changed; custom domains must be added again on "
        f"{target.name} and their DNS pointed at it."
    )


def _roll_back(job: Job, source: Side, target: Side, name: str, file_name: str, created: bool) -> None:
    def remove_instance() -> None:
        if target.exists(name):
            target.delete(name, production=True, without_backup=True)

    steps: list[tuple[str, Any]] = []
    if created:
        steps.append((f"removing the half-made instance on {target.name}", remove_instance))
    if file_name:
        steps.append((f"removing the copied backup on {target.name}", lambda: target.remove(file_name)))
    steps.append((f"starting {name} again on {source.name}", lambda: source.start(name)))
    for title, step in steps:
        job.write(f"Rollback: {title}\n")
        try:
            step()
        except Exception as error:  # noqa: BLE001 - every step of the rollback is tried
            job.write(f"Rollback: {title} failed: {error}\n")


def running_moves() -> int:
    from sudoo.core import jobs

    return sum(1 for j in list(jobs.JOBS.values()) if j.kind == "move" and j.status in ("queued", "running"))
