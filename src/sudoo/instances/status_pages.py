"""Small pages the gateway shows a visitor while an instance is not available (starting, updating, switched off).

Browsers get an HTML page that refreshes itself; API clients get plain text. Both use HTTP 503 with Retry-After,
which API clients and webhook senders understand as "try again shortly".
"""

from __future__ import annotations

import html
import time
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from sudoo.config import settings

_MARK = (Path(__file__).resolve().parent / "mark.svg").read_text().strip()  # the same as web/public/favicon.svg
_ICON = "data:image/svg+xml," + quote(_MARK)  # the Sudoo.sh mark

_PAGES = {
    "waking": (
        "Starting up",
        "This Odoo was paused to save server resources because nobody used it for a while. It is starting "
        "now, which usually takes 20 to 60 seconds. This page opens Odoo automatically when it is ready.",
    ),
    "busy": (
        "Update in progress",
        "This Odoo is being updated. It will be available again in a minute or two. This page refreshes "
        "automatically.",
    ),
    "off": ("Switched off", "This Odoo has been switched off by the administrator."),
    "gone": ("Not found", "This Odoo instance does not exist."),
}

_CSS = """
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Ubuntu,"Noto Sans",Arial,sans-serif;
background:#fafafa;color:#525252}
main{max-width:480px;margin:24px 16px;background:#fff;border:1px solid rgba(23,23,23,.08);border-radius:16px;padding:32px;
box-shadow:0 1px 2px rgba(0,0,0,.05)}
.name{font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#6b7280}
h1{font-size:20px;margin:8px 0 12px;font-weight:500;color:#171717}
p{margin:0 0 10px}
.contact{border-top:1px solid #e7e9ed;margin-top:18px;padding-top:14px;font-size:14px}
.spin{width:22px;height:22px;border:3px solid #e7e9ed;border-top-color:#714b67;border-radius:50%;
animation:r 1s linear infinite;margin-bottom:14px}
.time{font:13px ui-monospace,monospace;color:#6b7280}
@keyframes r{to{transform:rotate(360deg)}}
@media (prefers-color-scheme:dark){body{background:#141415;color:#a1a1a6}main{background:#19191b;
border-color:rgba(237,237,237,.08);box-shadow:none}h1{color:#ededed}.contact{border-color:rgba(237,237,237,.08)}
.spin{border-color:#232325;border-top-color:#c79bbd}}
"""


def render(request: web.Request, name: str, kind: str, since: float | None = None) -> web.Response:
    """The response for a visitor of instance `name` while it is in situation `kind` (see _PAGES)."""
    title, text = _PAGES[kind]
    if not (request.method == "GET" and "text/html" in request.headers.get("Accept", "")):
        return web.Response(status=503, text=f"{title}. {text}", headers={"Retry-After": "4"})
    waiting = kind in ("waking", "busy")
    elapsed = int(time.time() - since) if since else 0
    contact = ""
    if kind in ("off", "gone") or elapsed > 180:
        lead = "This is taking longer than usual. " if waiting else ""
        contact = f'<p class="contact">{html.escape(lead + settings.contact_sentence())}</p>'
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{'<meta http-equiv="refresh" content="4">' if waiting else ''}
<title>{html.escape(title)} - {html.escape(name)}</title><link rel="icon" href="{_ICON}"><style>{_CSS}</style></head>
<body><main>{'<div class="spin"></div>' if waiting else ''}
<div class="name">{html.escape(name)}</div><h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>
{f'<p class="time">Waiting for {elapsed} seconds</p>' if waiting else ''}{contact}
</main></body></html>"""
    return web.Response(
        status=503,
        text=page,
        content_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Retry-After": "4",
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
        },
    )
