"""The documentation inside the panel: the repository's README.md and docs/**/*.md, rendered as pages.

The installer copies the Markdown files into the image (settings.docs_dir), so the panel always shows the
documentation of the version that is running. Pages are addressed by their path in the repository:

    /docs                              the documentation index (docs/README.md)
    /docs/README.md                    the project overview
    /docs/docs/concepts/TUNNELING.md   a concept explained with examples

Links between Markdown files, and the diagrams (docs/images/*.svg, built by docs/diagrams/build.py), are rewritten
to these addresses, so the same files read correctly on GitHub and in the panel. Only files found at start-up can be
shown: a path is looked up in that list, never opened directly.
"""

from __future__ import annotations

import html
import posixpath
import re

import markdown
from flask import Blueprint, abort, redirect, send_file
from flask.typing import ResponseReturnValue
from werkzeug.wrappers import Response as WerkzeugResponse

from sudoo.config import settings

blueprint = Blueprint("docs", __name__)

INDEX = "docs/README.md"
SECTIONS = (  # sidebar: (title, path prefixes or exact paths)
    ("Start here", ("README.md", "docs/README.md", "docs/USER_MANUAL.md")),
    ("Guides", ("docs/ADMIN_GUIDE.md", "docs/PLUGINS.md", "docs/PANEL_API.md", "docs/API.md")),
    ("Reference", ("docs/CONFIGURATION.md", "docs/ARCHITECTURE.md", "docs/SECURITY.md")),
    ("Concepts", ("docs/concepts/",)),
)
_LINK = re.compile(r'(href|src|srcset)="(?![a-z]+:|/|#)([^"#]+\.(?:md|svg))(#[^"]*)?"')


def _title(text: str, path: str) -> str:
    if path == "README.md":
        return "Project overview"
    match = re.search(r"^# (.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else path


def _load() -> dict[str, tuple[str, str]]:
    """{path: (title, text)} of every Markdown file shipped with the panel."""
    if not settings.docs_dir.exists():
        return {}
    pages: dict[str, tuple[str, str]] = {}
    for file in sorted(settings.docs_dir.rglob("*.md")):
        path = file.relative_to(settings.docs_dir).as_posix()
        text = file.read_text()
        pages[path] = (_title(text, path), text)
    return pages


PAGES = _load()
IMAGES = (
    {file.relative_to(settings.docs_dir).as_posix() for file in settings.docs_dir.rglob("*.svg")}
    if settings.docs_dir.exists()
    else set()
)


def github_slug(value: str, separator: str = "-") -> str:
    """Heading anchors as GitHub makes them, so `#section` links work in both places."""
    return re.sub(r"[^\w\- ]", "", value.strip().lower()).replace(" ", separator)


def _render(path: str, text: str) -> str:
    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        extension_configs={"toc": {"slugify": github_slug}},
    )

    def rewrite(match: re.Match[str]) -> str:
        attribute, relative, anchor = match.groups()
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path), html.unescape(relative)))
        if target in PAGES or target in IMAGES:
            return f'{attribute}="/docs/{target}{anchor or ""}"'
        return match.group(0)

    return _LINK.sub(rewrite, body)


def _sidebar(current: str) -> str:
    parts: list[str] = []
    for title, members in SECTIONS:
        links: list[str] = []
        for p, (t, _) in PAGES.items():
            if any(p == m or (m.endswith("/") and p.startswith(m)) for m in members):
                active = ' class="active"' if p == current else ""
                links.append(f'<a href="/docs/{p}"{active}>{html.escape(t)}</a>')
        if links:
            parts.append(f'<div class="doc-nav-title">{title}</div>{"".join(links)}')
    return "".join(parts)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{title} - Sudoo.sh</title>
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<header class="navbar">
  <a class="brand" href="/"><img class="brand-mark" src="/static/odoo-icon.svg" alt=""><span>Sudoo.sh</span></a>
  <nav class="nav"><a href="/">Instances</a><a class="active" href="/docs">Documentation</a></nav>
</header>
<div class="control-panel"><div class="crumbs"><a href="/docs">Documentation</a><span>/</span>{title}</div></div>
<main class="docs">
  <nav class="doc-nav">{sidebar}</nav>
  <article class="sheet doc">{body}</article>
</main>
<footer class="footer">Sudoo.sh. Created by Yousef M. Y. Al Sabbah.</footer>
</body>
</html>
"""


@blueprint.get("/docs")
def index() -> WerkzeugResponse:
    return redirect(f"/docs/{INDEX}")


@blueprint.get("/docs/<path:path>")
def page(path: str) -> ResponseReturnValue:
    if path in IMAGES:
        return send_file(settings.docs_dir / path, mimetype="image/svg+xml", max_age=3600)
    if path not in PAGES:
        abort(404, "This page of the documentation does not exist.")
    title, text = PAGES[path]
    return PAGE.format(title=html.escape(title), sidebar=_sidebar(path), body=_render(path, text))
