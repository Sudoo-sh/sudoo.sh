"""The documentation, for the web app's Help pages: the same Markdown files as in the repository's docs/.

GET /api/v1/docs           the table of contents and the index page's path
GET /api/v1/docs/<path>    one page: title, rendered HTML and its headings
"""

from __future__ import annotations

from flask import Blueprint, Response, abort

from .. import docs
from ..security import require
from .responses import ok

blueprint = Blueprint("docs_api", __name__)


@blueprint.get("/docs")
@require("instances.view")
def contents() -> Response:
    return ok({"index": docs.INDEX, "sections": docs.sections()})


@blueprint.get("/docs/<path:path>")
@require("instances.view")
def page(path: str) -> Response:
    found = docs.page_resource(path) or abort(404, "This page of the documentation does not exist.")
    return ok(found)
