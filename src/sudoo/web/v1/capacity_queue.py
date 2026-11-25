"""The capacity queue (limits/queue.py): requests that wait for room under a running-instances limit.

GET    /api/v1/capacity-queue            the waiting requests: your own; all of them with capacity.manage
GET    /api/v1/capacity-queue/<id>       one waiting request (404 once it started or was cancelled)
DELETE /api/v1/capacity-queue/<id>       leave the line: the person who asked, or capacity.manage -> 204
POST   /api/v1/capacity-queue/<id>/front move it to the front of the line (capacity.manage)
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo.limits import queue
from sudoo.projects import access

from ..security import log, require
from .responses import no_content, ok

blueprint = Blueprint("capacity_queue", __name__)


def _manages() -> bool:
    return "capacity.manage" in access.reach_permissions(g.user)


def _visible(item: dict[str, Any]) -> bool:
    return _manages() or item["user"] == g.user["name"]


def _one(item_id: str) -> dict[str, Any]:
    found = next((i for i in queue.items() if i["id"] == item_id), None)
    if found is None or not _visible(found):  # not theirs looks like not there
        abort(404, "Nothing with that id is waiting. It may have started or been cancelled.")
    return dict(found)


@blueprint.get("/capacity-queue")
@require("instances.view")
def list_waiting() -> Response:
    return ok([queue.resource(item) for item in queue.items() if _visible(dict(item))])


@blueprint.get("/capacity-queue/<item_id>")
@require("instances.view")
def read(item_id: str) -> Response:
    return ok(queue.resource(_one(item_id)))


@blueprint.delete("/capacity-queue/<item_id>")
@require("instances.view")
def cancel(item_id: str) -> Response:
    found = _one(item_id)
    if not queue.cancel(item_id, g.user["name"], admin=_manages()):
        abort(404, "Nothing with that id is waiting. It may have started or been cancelled.")
    log("left the capacity queue", instance=str(found["instance"]))
    return no_content()


@blueprint.post("/capacity-queue/<item_id>/front")
@require("capacity.manage")
def front(item_id: str) -> Response:
    found = _one(item_id)
    queue.to_front(item_id, g.user["name"])
    log("moved to the front of the capacity queue", instance=str(found["instance"]))
    return ok(queue.resource(_one(item_id)))
