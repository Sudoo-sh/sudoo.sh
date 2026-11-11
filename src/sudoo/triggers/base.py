"""The interface every update-trigger plugin implements: what tells the panel that an instance has new code.

A trigger only decides *when*; the panel itself decides whether the instance takes updates (its "Update
automatically" switch), whether it is busy, and runs the update. Several triggers can be enabled together, for
example polling as a safety net next to a webhook.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from flask import Blueprint

RequestUpdate = Callable[[str, str], None]
"""request_update(instance name, reason): ask the panel to update that instance."""


@runtime_checkable
class UpdateTrigger(Protocol):
    """Notices new code and asks the panel to update the instances concerned."""

    def start(self, request_update: RequestUpdate) -> None:
        """Begin watching (for example start a background thread); called once when the panel starts."""
        ...

    def blueprint(self) -> Blueprint | None:
        """HTTP endpoints the trigger needs (webhooks), served under /hooks/; None when it needs none."""
        ...
