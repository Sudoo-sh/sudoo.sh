"""The Kubernetes engine (beta): the cluster's nodes and the command that adds one (docs/KUBERNETES_ENGINE.md).

    GET  /api/v1/kubernetes              {"enabled", "engines", "nodes", "reachable", "problem", "join"}: whether the
                                         beta is on, the engines a new instance may use, every node (status,
                                         capacity, use) and whether a join command can be made
    POST /api/v1/kubernetes/join-command {"command", "expires", "version"}: a new k3s join token and the exact
                                         `k3s agent` command, shown once (never stored by the panel)

Both need settings.manage. Adding a node is running instances on several servers: it needs the premium package
"servers" (402 with the plans that include it otherwise); listing the nodes does not.
"""

from __future__ import annotations

from flask import Blueprint, Response, abort

from sudoo import engines
from sudoo.config import settings

from ..security import log, refuse_without_premium, require
from .responses import ok

blueprint = Blueprint("kubernetes", __name__)


def _refuse_when_off() -> None:
    if not settings.kubernetes_engine_beta:
        abort(409, "The Kubernetes engine (beta) is off: set KUBERNETES_ENGINE_BETA=true in the panel's .env.")


@blueprint.get("/kubernetes")
@require("settings.manage")
def overview() -> Response:
    found: dict[str, object] = {
        "enabled": bool(settings.kubernetes_engine_beta),
        "engines": engines.offered(),
        "nodes": [],
        "reachable": False,
        "problem": None,
        "join": {"url_set": bool(settings.kubernetes_join_url), "minutes": settings.kubernetes_join_token_minutes},
    }
    if not settings.kubernetes_engine_beta:
        return ok(found)
    from sudoo.engines.kubernetes import cluster
    from sudoo.engines.kubernetes.api import AccessError, ApiError

    try:
        found["nodes"] = cluster.nodes()
        found["reachable"] = True
    except (AccessError, ApiError) as error:
        found["problem"] = str(error)[:300]
    return ok(found)


@blueprint.post("/kubernetes/join-command")
@require("settings.manage")
def join_command() -> Response:
    _refuse_when_off()
    refuse_without_premium("servers", "adding a Kubernetes node")
    from sudoo.engines.kubernetes import cluster
    from sudoo.engines.kubernetes.api import AccessError, ApiError

    try:
        found = cluster.join_command()
    except cluster.JoinError as error:
        abort(409, str(error))
    except (AccessError, ApiError) as error:
        abort(503, f"The cluster did not make a join token: {str(error)[:300]}")
    log("Kubernetes: join command made for a new node", detail=f"token {found['token_id']}, k3s {found['version']}")
    found.pop("token_id", None)
    response = ok(found)
    response.headers["Cache-Control"] = "no-store"
    return response
