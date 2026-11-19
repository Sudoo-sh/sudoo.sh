"""Premium features (premium/): what the plan includes, what is installed, and the web parts of those that run.

    GET  /api/v1/premium          every premium package: included, status (active, pending, restart_pending,
                                  failed, switched_off, not_included), version; for those that run, their web
                                  routes, sections, styles, strings and files (each with its SHA-256)
    POST /api/v1/premium/sync     check with Sudoo.sh now and install or update what the plan includes -> 202 (job)
    GET  /premium/ui/<id>/<file>  a web file of a running package, served only while its content has the hash its
                                  signed manifest states (signed-in sessions; the web app loads it as an ES module)

Reading needs a signed-in session (the web app builds its menu from it; problems and dates only for settings.manage);
checking now needs settings.manage. When a premium package is absent, the core answers its API paths with 402 and a
sentence that names the plans that include it (stubs below), so nothing fails in a confusing way.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Flask, Response, abort, g

from sudoo import premium
from sudoo.premium import loader

from ..security import PremiumRequired, can, log, require
from .resources import job_resource
from .responses import PREFIX, ok

blueprint = Blueprint("premium", __name__)
files = Blueprint("premium_files", __name__)

CONTENT_TYPES = {"js": "text/javascript", "css": "text/css", "json": "application/json"}

# the API paths of each premium package that the core answers with 402 while the package is absent
STUB_PATHS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "domains": (
        ("/instances/<name>/domains", ("POST",)),
        ("/instances/<name>/domains/<domain>", ("PATCH",)),
        ("/instances/<name>/domains/<domain>/check", ("POST",)),
    ),
    "offsite": (("/offsite", ("GET", "POST", "PUT", "PATCH", "DELETE")),
                ("/offsite/<path:rest>", ("GET", "POST", "PUT", "PATCH", "DELETE"))),  # fmt: skip
    "support": (("/support-access", ("GET", "POST", "PUT", "PATCH", "DELETE")),
                ("/support-access/<path:rest>", ("GET", "POST", "PUT", "PATCH", "DELETE"))),  # fmt: skip
    "servers": (("/servers", ("GET", "POST", "PUT", "PATCH", "DELETE")),
                ("/servers/<path:rest>", ("GET", "POST", "PUT", "PATCH", "DELETE"))),  # fmt: skip
}


@blueprint.get("/premium")
def overview() -> Response:
    return ok(loader.overview(details=can("settings.manage")))


@blueprint.post("/premium/sync")
@require("settings.manage")
def sync_now() -> Response:
    job = loader.start_sync(g.user["name"])
    if job is None:
        abort(409, "Premium features are being installed already; wait for that to finish.")
    log("premium features: check now")
    response = ok(job_resource(job), status=202)
    response.headers["Location"] = f"{PREFIX}/jobs/{job.id}"
    return response


@files.get("/premium/ui/<package_id>/<path:file>")
def ui_file(package_id: str, file: str) -> Response:
    if not g.get("user"):
        abort(401)
    found = loader.file_of(package_id, file)
    if found is None:
        abort(404, "No such file.")
    data, digest = found
    response = Response(data, mimetype=CONTENT_TYPES[file.rsplit(".", 1)[1]])
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"  # the address carries the hash
    response.headers["ETag"] = f'"{digest}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _stub(package_id: str) -> Any:
    def answer(**_: Any) -> Response:
        raise PremiumRequired(premium.refusal(package_id))

    answer.__name__ = f"premium_stub_{package_id}"
    return answer


def stubs() -> Blueprint:
    """402 answers for the API paths of the premium packages not loaded in this process."""
    found = Blueprint("premium_stubs", __name__)
    for package_id, paths in STUB_PATHS.items():
        if premium.loaded(package_id):
            continue
        for index, (rule, methods) in enumerate(paths):
            found.add_url_rule(rule, f"{package_id}_{index}", _stub(package_id), methods=list(methods))
    return found


def register_packages(api: Blueprint, app: Flask) -> list[str]:
    """Add the API of every loaded premium package (sudoo.premium.blueprints) and the stubs of the others."""
    added = []
    try:
        provided = premium.hooks(premium.BLUEPRINTS)
    except Exception as error:  # noqa: BLE001 - a broken package never stops the panel
        app.logger.error("premium: blueprints not loaded: %r", error)
        provided = []
    for name, extra in provided:
        if isinstance(extra, Blueprint):
            api.register_blueprint(extra)
            added.append(name)
    api.register_blueprint(stubs())
    return added
