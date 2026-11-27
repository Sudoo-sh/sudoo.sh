"""Tests that simulate an instance running: the strict limit counts the shared record alone, so publish into it."""

from sudoo.core import slots
from sudoo.instances import metrics


def run(name: str, running: bool = True, at: int | None = None) -> None:
    metrics.INSTANCES[name] = {"running": running}

    seq = at if at is not None else (slots.next_seq() or 1)

    def put(doc: dict) -> None:
        doc["observed"][name] = {"seq": seq, "running": running}

    slots._change(put)


def reset() -> None:
    """Forget every simulated observation and hold in the shared record."""

    def clear(doc: dict) -> None:
        doc["observed"].clear()
        doc["held"].clear()

    slots._change(clear)


def passes_then_full(limit: int = 1):  # type: ignore[no-untyped-def]
    """An admission_refusal stand-in for the race: the check passes (and arms its Admit as the real one does) and the
    record then holds `limit` running instances other than the target, so only the judgment inside the hold's own
    write can refuse."""
    from sudoo.instances import store

    def check(name: str) -> None:
        slots.arm(
            slots.Admit(existing=frozenset(store.names()), production=frozenset(), owned=frozenset(), user=None,
                        exempt=False, max_running=limit, user_limit=0, own_voice=True),
            name,
        )  # fmt: skip
        fill_record(limit)
        return None

    return check


def fill_record(limit: int = 1) -> None:
    """Other processes' holds fill the record to `limit` running instances (they count whether or not the name exists)."""

    def fill(doc: dict) -> None:  # holds of jobs of other processes (they count whether or not the name exists)
        for i in range(limit):
            doc["held"][f"docrun-job-{i}"] = {"instance": f"docrun-other-{i}", "owner": "other", "user": "x",
                                              "end_seq": None}  # fmt: skip

    slots._change(fill)
