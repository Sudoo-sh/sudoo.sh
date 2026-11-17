"""An instance's stage: production, staging or development (STAGE in its .env).

    development   what every instance was before stages existed, and the default: pauses when idle, lifetime,
                  daily backup when used, the panel's update settings
    staging       like development, with a longer lifetime offered (STAGING_DELETE_AFTER_DAYS) and a daily backup
                  that can be turned off for staging alone (STAGING_DAILY_BACKUPS)
    production    never paused automatically, never deleted automatically (its lifetime is "keep"), backed up every
                  day whatever DAILY_BACKUPS says and whether it was used or not, always updated safely (SAFE_UPDATES
                  does not apply), new code only on demand unless switched on (PRODUCTION_AUTO_UPDATE for new ones);
                  how many there may be: production_limit()

An instance made before stages existed has no STAGE and is a development instance: it behaves exactly as before.
"""

from __future__ import annotations

from collections.abc import Mapping

from sudoo.config import settings
from sudoo.core import jobs
from sudoo.instances import backups, store

PRODUCTION, STAGING, DEVELOPMENT = "production", "staging", "development"
STAGES = (PRODUCTION, STAGING, DEVELOPMENT)  # also the order lists show them in
DEFAULT = DEVELOPMENT


def of(env: Mapping[str, str]) -> str:
    """The stage of the instance whose .env is `env` (development when none is recorded)."""
    value = env.get("STAGE", "")
    return value if value in STAGES else DEFAULT


def is_production(env: Mapping[str, str]) -> bool:
    return of(env) == PRODUCTION


# ---------------------------------------------------------------- how many production instances


def production_limit() -> int:
    """How many instances may be in production at the same time; 0: no limit.

    The one place the limit comes from: today the MAX_PRODUCTION_INSTANCES setting, later the server's licence."""
    return int(settings.max_production_instances)


def production_instances(exclude: str | None = None) -> list[str]:
    """Instances in production, those whose creation as production is queued or running included."""
    found = {name for name in store.names() if is_production(store.read_env(name))}
    for name in jobs.being_created():
        job = jobs.creating(name)
        if job is not None and job.details.get("stage") == PRODUCTION:
            found.add(name)
    found.discard(exclude or "")
    return sorted(found)


def production_refusal(exclude: str | None = None) -> str | None:
    """Why one more production instance is not allowed (`exclude`: the instance being promoted), or None."""
    limit = production_limit()
    current = production_instances(exclude)
    if not limit or len(current) < limit:
        return None
    return (
        f"This server allows {limit} production {'instance' if limit == 1 else 'instances'}, and "
        f"{', '.join(current)} {'is' if len(current) == 1 else 'are'} in production already. Move one to staging or "
        "development first, or raise Production instances at most (Settings, Instances)."
    )


# ---------------------------------------------------------------- what a stage changes


def defaults(stage: str) -> dict[str, object]:
    """What a new instance of `stage` starts with, unless the request says otherwise."""
    values: dict[str, object] = {
        "delete_after_days": settings.delete_after_days,
        "auto_update": settings.auto_update,
        "auto_upgrade": settings.auto_upgrade,
        "auto_pause": settings.auto_pause,
    }
    if stage == STAGING:
        values["delete_after_days"] = settings.staging_delete_after_days
    elif stage == PRODUCTION:
        values |= {"delete_after_days": 0, "auto_pause": False, "auto_update": settings.production_auto_update}
    return values


def pauses_when_idle(env: Mapping[str, str]) -> bool:
    """Whether the instance is paused after IDLE_HOURS without visits: never in production."""
    return env.get("AUTO_PAUSE") == "true" and not is_production(env)


def lifetime_days(env: Mapping[str, str]) -> int:
    """Days after its creation the instance is deleted automatically; 0: kept (always in production)."""
    return 0 if is_production(env) else int(env.get("DELETE_AFTER_DAYS") or 0)


def safe_updates(env: Mapping[str, str]) -> bool:
    """Whether updates back up first and put back a failed update: always in production, else SAFE_UPDATES."""
    return is_production(env) or bool(settings.safe_updates)


def daily_backup_due(name: str, env: Mapping[str, str], used_since: float | None, slot: float) -> bool:
    """Whether the instance needs its daily backup for `slot` (backups.daily_slot).

    Production: every day, used or not and whatever DAILY_BACKUPS says, unless a daily backup was taken since the
    slot. Others: when DAILY_BACKUPS is on (and, for staging, STAGING_DAILY_BACKUPS) and it was used since its last
    daily backup."""
    if is_production(env):
        latest = next(iter(backups.own_backups(name, ("daily",))), None)
        return latest is None or latest["created"] < slot
    if not settings.daily_backups or (of(env) == STAGING and not settings.staging_daily_backups):
        return False
    return backups.needs_daily(used_since, slot)
