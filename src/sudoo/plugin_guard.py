"""A plugin that fails never takes the panel down (docs/PLUGINS.md "When a plugin fails").

Every part of the panel that uses a plugin gets it from `load_safely`, which never raises:

Loading     A plugin that cannot be imported or created (an exception, even SystemExit, in its module or its
            constructor) is marked failed with the error (core/resilience.describe: no secrets), logged, written to
            the audit log and shown to the administrators; the group's built-in default is used instead (built-in
            sign-in, the panel's own storage, the default mailer and rate limiter) or, for groups that list several
            plugins (update triggers, second factors) and the Enterprise add-on source, just that plugin is left out.
Calls       A plugin that is not part of the panel is wrapped (Guarded): an exception from any of its methods is
            counted, logged and shown the same way. Sign-in and rate limits then answer with the built-in default
            for that call; the mailer's error becomes a MailError, which every sender already handles; other groups
            pass the error on to the request or job, which ends cleanly. A success resets the count. The panel's own
            plugins are not wrapped: their errors are the panel's own answers (a git server that is down).
Switching   After PLUGIN_FAILURE_LIMIT failures in a row (default 5; 0: never) the plugin is switched off: the
  off       built-in default is used from the next call on, also after a restart, until an administrator switches
            it on again (POST /api/v1/health/plugins/<group>/<name>/enable, the banner's button). Storage is never
            switched off while the panel runs: its records would go elsewhere.

The state is kept in memory, and the switched-off plugins also in storage (collection "plugin_failures").
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from sudoo import plugins
from sudoo.config import Settings, settings
from sudoo.core import resilience

log = logging.getLogger("sudoo.plugins")

COLLECTION = "plugin_failures"
LABELS = {
    plugins.AUTH_GROUP: "sign-in",
    plugins.SECOND_FACTOR_GROUP: "second factor",
    plugins.STORAGE_GROUP: "storage",
    plugins.MAILER_GROUP: "e-mail",
    plugins.RATE_LIMITER_GROUP: "rate limit",
    plugins.EXPOSURE_GROUP: "exposure",
    plugins.ADDON_SOURCE_GROUP: "Enterprise add-on source",
    plugins.UPDATE_TRIGGER_GROUP: "update trigger",
}

_LOCK = threading.Lock()
_STATE: dict[tuple[str, str], dict[str, Any]] = {}  # (group, name) -> failures, last error, switched off
_PERSISTED: dict[str, dict[str, Any]] | None = None  # the switched-off plugins kept in storage (read once)
_RESETS: dict[str, list[Callable[[], None]]] = {}  # group -> what forgets the created plugin (functools caches)


def label(group: str) -> str:
    return LABELS.get(group, group.removeprefix("sudoo."))


def on_reset(group: str, forget: Callable[[], None]) -> None:
    """Call `forget()` when a plugin of `group` is switched off or on, so the next use loads it again."""
    _RESETS.setdefault(group, []).append(forget)


def _reset(group: str) -> None:
    for forget in _RESETS.get(group, []):
        try:
            forget()
        except Exception:  # noqa: BLE001 - a cache that cannot be cleared is cleared at the next restart
            log.exception("plugins: cannot forget the %s plugin", group)


def _key(group: str, name: str) -> str:
    return f"{group}:{name}"


def _persisted() -> dict[str, dict[str, Any]]:
    """The plugins switched off in an earlier run; {} when storage cannot be read (they are retried, and fail
    again, which the panel survives)."""
    global _PERSISTED
    if _PERSISTED is None:
        try:
            from sudoo.storage import storage

            _PERSISTED = dict(storage().all(COLLECTION))
        except Exception:  # noqa: BLE001 - storage that cannot be read is listed by its own guard
            log.exception("plugins: cannot read the switched-off plugins")
            return {}
    return _PERSISTED


def _persist(group: str, name: str, state: dict[str, Any] | None) -> None:
    if group == plugins.STORAGE_GROUP:
        return  # kept in memory only: storage cannot record its own failure
    try:
        from sudoo.storage import storage

        if state is None:
            storage().delete(COLLECTION, _key(group, name))
            _persisted().pop(_key(group, name), None)
        else:
            storage().put(COLLECTION, _key(group, name), state)
            _persisted()[_key(group, name)] = state
    except Exception:  # noqa: BLE001 - the memory state still applies in this run
        log.exception("plugins: cannot record the state of %s %s", group, name)


def _audit(event: str, detail: str) -> None:
    if detail.startswith(plugins.STORAGE_GROUP + " "):
        return  # the audit log is in storage, which is being opened right now (its lock is held)
    try:
        from sudoo.core import audit

        audit.record("system", None, event, ok=False, detail=detail[:500])
    except Exception:  # noqa: BLE001 - the audit log itself may be what failed (storage)
        log.exception("plugins: cannot write to the audit log: %s", event)


def disabled(group: str, name: str) -> bool:
    """Whether the plugin was switched off after failing too often (in this run or an earlier one)."""
    with _LOCK:
        state = _STATE.get((group, name))
    if state is not None:
        return bool(state.get("disabled"))
    return group != plugins.STORAGE_GROUP and bool(_persisted().get(_key(group, name), {}).get("disabled"))


def failed(group: str, name: str, stage: str, error: BaseException) -> bool:
    """Count a failure of the plugin (stage "load" or "call"); returns True when it is switched off now."""
    detail = resilience.describe(error)
    limit = int(settings.plugin_failure_limit)
    now = time.time()
    with _LOCK:
        state = _STATE.setdefault(
            (group, name), {"group": group, "name": name, "failures": 0, "since": now, "disabled": False}
        )
        first = state["failures"] == 0
        state.update(failures=state["failures"] + 1, stage=stage, error=detail, last=now)
        switch_off = (
            not state["disabled"] and limit > 0 and state["failures"] >= limit and group != plugins.STORAGE_GROUP
        )
        if switch_off:
            state["disabled"] = True
        snapshot = dict(state)
    what = f"The {label(group)} plugin {name}"
    log.error("plugins: %s failed (%s, %d in a row): %s", what, stage, snapshot["failures"], detail)
    if switch_off:
        resilience.report("plugin", _key(group, name), f"{what} was switched off after {limit} failures in a row; "
                          "the built-in default is used until an administrator switches it on again", detail)  # fmt: skip
        _audit("plugin switched off (kept failing)", f"{group} {name}: {detail}")
        _persist(group, name, snapshot)
        _reset(group)
    else:
        verb = "could not be loaded; the built-in default is used" if stage == "load" else "failed"
        resilience.report("plugin", _key(group, name), f"{what} {verb}", detail)
        if first:
            _audit("plugin failed", f"{group} {name} ({stage}): {detail}")
    return switch_off


def succeeded(group: str, name: str) -> None:
    """A call went through: the failures in a row start again from 0 (a switched-off plugin stays off)."""
    if (group, name) not in _STATE:  # the usual case, without taking the lock
        return
    with _LOCK:
        state = _STATE.get((group, name))
        if state is None or state["disabled"] or not state["failures"]:
            return
        state["failures"] = 0
    resilience.resolve("plugin", _key(group, name))


def enable(group: str, name: str, user: str) -> bool:
    """An administrator switches a failed plugin on again: it is loaded again at its next use. False when it was
    neither failed nor switched off."""
    with _LOCK:
        state = _STATE.pop((group, name), None)
    known = state is not None or _key(group, name) in _persisted()
    _persist(group, name, None)
    resilience.resolve("plugin", _key(group, name))
    if known:
        try:
            from sudoo.core import audit

            audit.record(user, None, "plugin switched on again", detail=f"{group} {name}")
        except Exception:  # noqa: BLE001 - the switch itself is done
            log.exception("plugins: cannot write to the audit log")
        _reset(group)
    return known


def failures() -> list[dict[str, Any]]:
    """Every plugin that failed in this run or is switched off, for the administrators."""
    with _LOCK:
        found = {_key(s["group"], s["name"]): dict(s) for s in _STATE.values()}
    for key, state in _persisted().items():
        if key not in found and state.get("disabled"):
            found[key] = {**state, "earlier_run": True}
    return sorted(found.values(), key=lambda s: -float(s.get("last") or 0))


def forget_all() -> None:
    """Forget every failure (tests)."""
    global _PERSISTED
    with _LOCK:
        _STATE.clear()
    _PERSISTED = None


def _built_in(plugin: object) -> bool:
    return type(plugin).__module__.split(".", 1)[0] == "sudoo"


class Guarded:
    """A plugin from outside the panel, each attribute and call guarded (module docstring). isinstance() and
    type checks see the plugin's own class."""

    __slots__ = ("_fallback", "_group", "_name", "_passthrough", "_plugin", "_wrap")

    def __init__(
        self,
        plugin: Any,
        group: str,
        name: str,
        fallback: Callable[[], Any] | None = None,
        passthrough: tuple[type[BaseException], ...] = (),
        wrap: Callable[[str], Exception] | None = None,
    ) -> None:
        object.__setattr__(self, "_plugin", plugin)
        object.__setattr__(self, "_group", group)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_fallback", fallback)
        object.__setattr__(self, "_passthrough", passthrough)
        object.__setattr__(self, "_wrap", wrap)

    @property  # type: ignore[misc]
    def __class__(self) -> type:  # noqa: D105 - isinstance() sees the plugin's class
        return type(self._plugin)

    def __repr__(self) -> str:
        return f"Guarded({self._plugin!r})"

    def _failure(self, attribute: str, error: Exception, call: Callable[[Any], Any]) -> Any:
        failed(self._group, self._name, "call", error)
        if self._fallback is not None and (other := self._fallback()) is not None:
            return call(other)
        if self._wrap is not None:
            raise self._wrap(
                f"The {label(self._group)} plugin {self._name} failed ({attribute}): {resilience.describe(error)}"
            ) from error
        raise error

    def __getattr__(self, attribute: str) -> Any:
        try:
            value = getattr(self._plugin, attribute)
        except AttributeError:
            raise
        except self._passthrough:
            raise
        except Exception as error:  # noqa: BLE001 - a property of the plugin raised
            return self._failure(attribute, error, lambda other: getattr(other, attribute))
        if not callable(value) or isinstance(value, type):
            return value

        @functools.wraps(value)
        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                result = value(*args, **kwargs)
            except self._passthrough:
                raise
            except Exception as error:  # noqa: BLE001 - counted, shown, and answered by the fallback when there is one
                return self._failure(attribute, error, lambda other: getattr(other, attribute)(*args, **kwargs))
            succeeded(self._group, self._name)
            return result

        return call

    def __setattr__(self, attribute: str, value: Any) -> None:
        setattr(self._plugin, attribute, value)


def load_safely(
    group: str,
    name: str,
    config: Settings,
    fallback: str | None = None,
    *,
    call_fallback: bool = False,
    passthrough: tuple[type[BaseException], ...] = (),
    wrap: Callable[[str], Exception] | None = None,
) -> Any:
    """The plugin `name` of `group`, or, when it cannot be used, the built-in `fallback` (None when there is none:
    the caller leaves that feature out). Never raises.

    `call_fallback`: a call the plugin fails is answered by the fallback; `passthrough`: exceptions that are the
    plugin's normal answers; `wrap`: what other exceptions from a call become (the group's own error type)."""

    def default() -> Any:
        if fallback is None or fallback == name:
            return None
        try:
            return plugins.load(group, fallback, config)
        except Exception as error:  # noqa: BLE001 - a broken built-in is listed too; the caller copes with None
            log.exception("plugins: the built-in %s plugin %s cannot be loaded either", label(group), fallback)
            resilience.report("plugin", _key(group, fallback), f"The built-in {label(group)} plugin {fallback} "
                              "cannot be loaded", resilience.describe(error))  # fmt: skip
            return None

    if disabled(group, name):
        log.warning("plugins: the %s plugin %s is switched off; using %s", label(group), name, fallback or "none")
        state = _STATE.get((group, name)) or _persisted().get(_key(group, name)) or {}
        resilience.report("plugin", _key(group, name), f"The {label(group)} plugin {name} is switched off after "
                          "failing too often; the built-in default is used", str(state.get("error") or ""))  # fmt: skip
        return default()
    try:
        plugin = plugins.load(group, name, config)
    except (Exception, SystemExit) as error:  # noqa: BLE001 - its import, its constructor or a missing plugin
        failed(group, name, "load", error if isinstance(error, Exception) else RuntimeError(f"exit {error.code}"))
        return default()
    if _built_in(plugin):
        return plugin
    fallen = None
    if call_fallback and fallback is not None and fallback != name:
        fallen = _lazy(default)
    return Guarded(plugin, group, name, fallen, passthrough, wrap)


def _lazy(make: Callable[[], Any]) -> Callable[[], Any]:
    """`make()`, called the first time only."""
    made: list[Any] = []
    lock = threading.Lock()

    def get() -> Any:
        with lock:
            if not made:
                made.append(make())
            return made[0]

    return get


def default_of(setting: str) -> str:
    """The built-in plugin a setting names by default (AUTH=local, MAILER=smtp, ...)."""
    return str(Settings.model_fields[setting].default)
