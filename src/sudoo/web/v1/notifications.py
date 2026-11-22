"""A project's notification channels (notifications/).

    GET    /api/v1/projects/<id>/notifications                  the channels (no secret, only its last 4
                                                                characters), the events and kinds there are
    POST   /api/v1/projects/<id>/notifications                  {"type", "name", "events", "config", "secrets"} -> 201;
                                                                a webhook's answer carries its "signing_secret",
                                                                shown this once
    PATCH  /api/v1/projects/<id>/notifications/<channel>        {"name"?, "events"?, "enabled"?, "config"?,
                                                                "secrets"?}: a secret left out stays as it is
    DELETE /api/v1/projects/<id>/notifications/<channel>        -> 204
    POST   /api/v1/projects/<id>/notifications/<channel>/test   send a test notification now; its delivery
    POST   /api/v1/projects/<id>/notifications/<channel>/signing-secret
                                                                a webhook's new signing secret, shown this once

Everything needs projects.manage in the project. The audit log names the channel and its kind, never a secret.
"""

from __future__ import annotations

import re
from typing import Any

from flask import Blueprint, Response, abort, g

from sudoo import notifications
from sudoo.config import settings
from sudoo.notifications import channels

from .. import validation as check
from ..security import log
from .projects import _project
from .responses import PREFIX, created, no_content, ok

blueprint = Blueprint("notifications", __name__)
_ID_RE = re.compile(r"^[a-f0-9]{12}$")


def _overview(project_id: str) -> dict[str, Any]:
    return {
        "project": project_id,
        "enabled": bool(settings.notifications),
        "channels": [notifications.resource(c) for c in notifications.of_project(project_id)],
        "events": list(notifications.EVENTS),
        "default_events": list(notifications.DEFAULT_EVENTS),
        "types": list(channels.TYPES),
        "limit": settings.notification_channels_per_project,
    }


def _channel(project_id: str, channel_id: str) -> dict[str, Any]:
    found = notifications.get(channel_id) if _ID_RE.match(channel_id or "") else None
    if found is None or found.get("project") != project_id:
        abort(404, "This channel does not exist.")
    return found


@blueprint.get("/projects/<project_id>/notifications")
def read_channels(project_id: str) -> Response:
    _project(project_id, "projects.manage")
    return ok(_overview(project_id))


@blueprint.post("/projects/<project_id>/notifications")
def add_channel(project_id: str) -> Response:
    _project(project_id, "projects.manage")
    try:
        channel, signing = notifications.create(project_id, check.body(), g.user["name"])
    except notifications.ChannelError as error:
        abort(400, str(error))
    log("notification channel added", detail=f"{project_id}: {channel['name']} ({channel['type']})"[:300])
    shown = notifications.resource(channel) | ({"signing_secret": signing} if signing else {})
    return created(shown, f"{PREFIX}/projects/{project_id}/notifications/{channel['id']}")


@blueprint.patch("/projects/<project_id>/notifications/<channel_id>")
def change_channel(project_id: str, channel_id: str) -> Response:
    _project(project_id, "projects.manage")
    found = _channel(project_id, channel_id)
    data = check.body()
    try:
        channel = notifications.change(found, data)
    except notifications.ChannelError as error:
        abort(400, str(error))
    changed = sorted(k for k in data if k in ("name", "events", "enabled", "config", "secrets"))
    log("notification channel changed", detail=f"{project_id}: {channel['name']} ({', '.join(changed)})"[:300])
    return ok(notifications.resource(channel))


@blueprint.delete("/projects/<project_id>/notifications/<channel_id>")
def remove_channel(project_id: str, channel_id: str) -> Response:
    _project(project_id, "projects.manage")
    found = _channel(project_id, channel_id)
    notifications.remove(channel_id)
    log("notification channel removed", detail=f"{project_id}: {found['name']} ({found['type']})"[:300])
    return no_content()


@blueprint.post("/projects/<project_id>/notifications/<channel_id>/test")
def test_channel(project_id: str, channel_id: str) -> Response:
    _project(project_id, "projects.manage")
    found = _channel(project_id, channel_id)
    last = notifications.send_test(found, g.user["name"])
    log("test notification sent", ok=bool(last.get("ok")), detail=f"{project_id}: {found['name']} ({found['type']})")
    return ok({"channel": notifications.resource(notifications.get(channel_id) or found), "delivery": last})


@blueprint.post("/projects/<project_id>/notifications/<channel_id>/signing-secret")
def new_signing_secret(project_id: str, channel_id: str) -> Response:
    _project(project_id, "projects.manage")
    found = _channel(project_id, channel_id)
    try:
        channel, signing = notifications.new_signing_secret(found)
    except notifications.ChannelError as error:
        abort(400, str(error))
    log("webhook signing secret replaced", detail=f"{project_id}: {channel['name']}")
    return ok(notifications.resource(channel) | {"signing_secret": signing})
