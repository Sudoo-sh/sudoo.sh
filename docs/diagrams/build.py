"""Builds the diagrams of the documentation as SVG files in docs/images/.

Run after changing a diagram:   python3 docs/diagrams/build.py

Each diagram is a short function below that places boxes and arrows on a canvas. The SVG files are what the
Markdown pages show, on GitHub and inside the panel (no JavaScript needed). Colours follow the reader's light or
dark setting.
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent / "images"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Ubuntu, 'Noto Sans', Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

STYLE = f"""
.bg {{ fill: #ffffff; }}
text {{ font-family: {FONT}; fill: #1f2937; }}
.t {{ font-size: 14px; font-weight: 600; }}
.s {{ font-size: 12.5px; }}
.m {{ font-size: 12px; fill: #6b7280; }}
.mono {{ font-family: {MONO}; font-size: 12px; }}
.lab {{ font-size: 12px; fill: #4b5563; }}
.labbg {{ fill: #ffffff; }}
.arrow {{ stroke: #6b7280; stroke-width: 1.6; fill: none; }}
.arrow.dash {{ stroke-dasharray: 5 4; }}
.arrow.bold {{ stroke: #017e84; stroke-width: 3; }}
.arrow.bad {{ stroke: #d23f3a; }}
.head {{ fill: #6b7280; }}
.head.bold {{ fill: #017e84; }}
.head.bad {{ fill: #d23f3a; }}
.life {{ stroke: #c4c9d0; stroke-width: 1.2; stroke-dasharray: 4 4; }}
.k-default {{ fill: #ffffff; stroke: #d0d7de; }}
.k-outside {{ fill: #fff8e6; stroke: #d9a521; }}
.k-cloud {{ fill: #e6f2f3; stroke: #017e84; }}
.k-panel {{ fill: #f3edf2; stroke: #714b67; }}
.k-instance {{ fill: #eef2f7; stroke: #8792a2; }}
.k-danger {{ fill: #fdecec; stroke: #d23f3a; }}
.k-decision {{ fill: #fff3cd; stroke: #e99d00; }}
.k-ok {{ fill: #e8f5ec; stroke: #28a745; }}
.k-group {{ fill: none; stroke: #9ca3af; stroke-dasharray: 6 5; }}
.k-ui {{ fill: #f9fafb; stroke: #d0d7de; }}
.k-button {{ fill: #e7e9ed; stroke: #e7e9ed; }}
.k-primary {{ fill: #714b67; stroke: #714b67; }}
.k-current {{ fill: #e6f2f3; stroke: #d0d7de; }}
.white {{ fill: #ffffff; }}
.teal {{ fill: #017e84; }}
.bad-text {{ fill: #d23f3a; font-size: 16px; font-weight: 700; }}
@media (prefers-color-scheme: dark) {{
  .bg, .labbg {{ fill: #1b1f27; }}
  text {{ fill: #e5e7eb; }}
  .m {{ fill: #9ca3af; }}
  .lab {{ fill: #c4c9d0; }}
  .arrow {{ stroke: #9ca3af; }} .head {{ fill: #9ca3af; }}
  .arrow.bold {{ stroke: #3fb6bc; }} .head.bold {{ fill: #3fb6bc; }}
  .life {{ stroke: #4a5060; }}
  .k-default {{ fill: #232834; stroke: #3a3f4d; }}
  .k-outside {{ fill: #3a2f12; stroke: #d9a521; }}
  .k-cloud {{ fill: #12302f; stroke: #3fb6bc; }}
  .k-panel {{ fill: #33222f; stroke: #b58aa8; }}
  .k-instance {{ fill: #222833; stroke: #6b7280; }}
  .k-danger {{ fill: #3b1c1c; stroke: #ef5350; }}
  .k-decision {{ fill: #3a3015; stroke: #f0b429; }}
  .k-ok {{ fill: #17301f; stroke: #4caf50; }}
  .k-ui {{ fill: #232834; stroke: #3a3f4d; }}
  .k-button {{ fill: #3a3f4d; stroke: #3a3f4d; }}
  .k-primary {{ fill: #875a7b; stroke: #875a7b; }}
  .k-current {{ fill: #12302f; stroke: #3a3f4d; }}
  .teal {{ fill: #3fb6bc; }}
}}
"""


PROBLEMS = []  # labels that do not fit their box; the build fails until they are fixed


def width_of(text, size):
    """Rough width of `text` in pixels; used to check that labels fit their boxes."""
    return len(text) * size * 0.56


class Canvas:
    def __init__(self, width, height, title):
        self.width, self.height, self.title, self.parts = width, height, title, []

    # ---------------------------------------------------------------- shapes

    def box(self, x, y, w, h, title=None, lines=(), kind="default", align="middle", mono=False, radius=8):
        """A rounded box with an optional bold title and lines of text, centred or left-aligned."""
        self.parts.append(f'<rect class="k-{kind}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}"/>')
        rows = ([("t", title)] if title else []) + [("mono" if mono else "s", line) for line in lines]
        step = 19
        top = y + h / 2 - (len(rows) - 1) * step / 2 + 4.5
        tx = x + w / 2 if align == "middle" else x + 14
        for i, (cls, text) in enumerate(rows):
            size = 14 if cls == "t" else 12.5
            if width_of(text, size) > w - 16:
                PROBLEMS.append(f"{self.title}: {text!r} needs {width_of(text, size) + 16:.0f}px, box is {w}px")
            self.text(tx, top + i * step, text, cls, align)
        return self

    def group(self, x, y, w, h, label):
        """A dashed frame around boxes that belong together, with its label at the top left."""
        self.parts.append(f'<rect class="k-group" x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/>')
        self.text(x + 14, y + 20, label, "m", "start")

    def text(self, x, y, text, cls="s", anchor="middle"):
        self.parts.append(f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}">{escape(text)}</text>')

    def label(self, x, y, text, anchor="middle"):
        """Small text on a background patch, so it stays readable on top of lines."""
        w = width_of(text, 12) + 10
        left = {"middle": x - w / 2, "start": x - 5, "end": x - w + 5}[anchor]
        self.parts.append(f'<rect class="labbg" x="{left:.1f}" y="{y - 13}" width="{w:.1f}" height="18" rx="3"/>')
        self.text(x, y, text, "lab", anchor)

    def arrow(self, *points, label=None, at=None, anchor="middle", style="", head=True, both=False):
        """A line through `points` with an arrowhead at the end (and at the start when `both`)."""
        path = " ".join(f"{'M' if i == 0 else 'L'}{x},{y}" for i, (x, y) in enumerate(points))
        self.parts.append(f'<path class="arrow {style}" d="{path}"/>')
        if head:
            self._head(points[-2], points[-1], style)
        if both:
            self._head(points[1], points[0], style)
        if label:
            lx, ly = at or ((points[0][0] + points[-1][0]) / 2, (points[0][1] + points[-1][1]) / 2 - 6)
            self.label(lx, ly, label, anchor)

    def _head(self, start, end, style):
        (x1, y1), (x2, y2) = start, end
        length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1)
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        size = 11 if "bold" in style else 9
        bx, by = x2 - ux * size, y2 - uy * size
        px, py = -uy * size * 0.5, ux * size * 0.5
        self.parts.append(
            f'<path class="head {style}" d="M{x2},{y2} L{bx + px:.1f},{by + py:.1f} '
            f'L{bx - px:.1f},{by - py:.1f} Z"/>'
        )

    def raw(self, svg):
        self.parts.append(svg)

    # ---------------------------------------------------------------- output

    def save(self, name):
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width} {self.height}" '
            f'width="{self.width}" height="{self.height}" role="img" aria-label="{escape(self.title)}">'
            f"<title>{escape(self.title)}</title><style>{STYLE}</style>"
            f'<rect class="bg" width="{self.width}" height="{self.height}" rx="12"/>' + "".join(self.parts) + "</svg>"
        )
        (OUT / f"{name}.svg").write_text(svg + "\n")


# ==================================================================== diagrams


def architecture():
    c = Canvas(800, 640, "How the parts of Sudoo fit together")
    x, w = 150, 470
    mid = x + w / 2
    c.box(x, 20, w, 58, "People and systems on the internet", ["browsers, API clients, webhook senders"], "outside")
    c.arrow((mid, 78), (mid, 124), label="https://<words>.trycloudflare.com, one link per instance", at=(mid, 104))
    c.box(x, 124, w, 58, "Cloudflare", ["one public https address per connector"], "cloud")
    c.group(100, 212, 570, 410, "Server: only SSH reaches it from outside")
    c.arrow(
        (mid, 252), (mid, 182), label="connections opened outwards by the server", at=(mid + 12, 200), anchor="start"
    )
    c.box(
        x,
        252,
        w,
        58,
        "Connectors (cloudflared)",
        ["sudoo-connector-panel  •  sudoo-connector-<name>, one each"],
        "cloud",
    )
    c.arrow((mid, 310), (mid, 344))
    c.box(
        x,
        344,
        w,
        116,
        "sudoo-panel  (the panel container)",
        [
            "Panel and JSON API on 127.0.0.1:9000",
            "Gateway: one port per instance, wakes paused instances",
            "Background: monitor, auto-pause, auto-delete, GitHub sync",
        ],
        "panel",
    )
    c.box(676, 366, 112, 70, "GitHub", ["code and", "submodules"], "outside")
    c.arrow((620, 401), (676, 401), label="fetch", at=(648, 392))
    c.arrow((mid, 460), (mid, 508), label="docker, docker compose", at=(mid, 488))
    c.box(
        x,
        508,
        w,
        96,
        "Instances  (one Docker Compose project each)",
        ["odoo (official image)  →  PostgreSQL 16", "+ Odoo Enterprise add-ons, shared read-only"],
        "instance",
    )
    c.save("architecture")


def visit_flow():
    c = Canvas(900, 250, "How a visit reaches Odoo")
    names = [
        ("Browser or", "API client", "outside"),
        ("Cloudflare", "edge", "cloud"),
        ("Connector", "per instance", "cloud"),
        ("Gateway", "port 8070", "panel"),
        ("Odoo", "port 28070", "instance"),
        ("PostgreSQL", "database", "instance"),
    ]
    labels = ["https", "tunnel", "127.0.0.1:8070", "127.0.0.1:28070", "SQL"]
    w, gap, y = 118, 34, 30
    for i, (title, line, kind) in enumerate(names):
        x = 20 + i * (w + gap)
        c.box(x, y, w, 60, title, [line], kind)
        if i < len(labels):
            c.arrow((x + w, y + 24), (x + w + gap, y + 24))
            c.arrow((x + w + gap, y + 40), (x + w, y + 40), style="dash")
            c.label(x + w + gap / 2, y - 6, labels[i])
    gx = 20 + 3 * (w + gap)
    c.arrow((gx + w / 2, 90), (gx + w / 2, 120), head=False)
    c.box(
        gx - 90,
        120,
        w + 180,
        96,
        None,
        [
            "records the visit (for automatic pause)",
            "removes the panel's cookie",
            "passes on X-Forwarded-For and https",
            "starts a paused instance",
        ],
        "panel",
        align="start",
    )
    c.text(20, 236, "Solid arrows: the request. Dashed arrows: the answer, streamed back the same way.", "m", "start")
    c.save("visit-flow")


def wake_sequence():
    c = Canvas(760, 540, "A visit to a paused instance")
    cols = {"Browser or API client": 110, "Gateway": 380, "Odoo": 650}
    for name, x in cols.items():
        c.box(
            x - 95, 20, 190, 40, name, [], "panel" if name == "Gateway" else "instance" if name == "Odoo" else "outside"
        )
        c.raw(f'<line class="life" x1="{x}" y1="60" x2="{x}" y2="525"/>')
    b, g, o = cols.values()
    steps = [
        (b, g, "GET /odoo", ""),
        (g, o, "forward", ""),
        (o, g, "connection refused: not running", "dash bad"),
        (g, o, "start the instance (once)", "bold"),
        (g, b, "503 Starting up, Retry-After: 4", "dash"),
    ]
    y = 95
    for x1, x2, text, style in steps:
        c.arrow((x1, y), (x2, y), label=text, at=((x1 + x2) / 2, y - 7), style=style)
        y += 46
    c.box(b - 95, y - 18, 270, 44, None, ["the page reloads itself every 4 s;", "API clients retry"], "decision")
    y += 58
    for x1, x2, text, style in [
        (b, g, "GET /odoo", ""),
        (g, o, "forward", ""),
        (o, g, "200", "dash"),
        (g, b, "200: Odoo opens", "dash"),
    ]:
        c.arrow((x1, y), (x2, y), label=text, at=((x1 + x2) / 2, y - 7), style=style)
        y += 40
    c.save("wake-sequence")


def lifecycle():
    c = Canvas(780, 420, "Life of an instance")
    c.raw('<circle class="teal" cx="40" cy="70" r="8"/>')
    c.arrow((48, 70), (88, 70))
    c.box(90, 44, 130, 52, "Creating", [], "default")
    c.box(300, 160, 150, 60, "Running", ["restart, updates"], "ok")
    c.box(580, 160, 150, 60, "Paused", ["uses no memory"], "instance")
    c.box(300, 320, 150, 60, "Switched off", ["does not wake up"], "instance")
    c.box(90, 320, 130, 60, "Deleted", ["backup kept"], "danger")
    c.arrow((220, 70), (375, 70), (375, 160), label="ready", at=(300, 62))
    c.arrow((155, 96), (155, 320), label="failed: all removed", at=(155, 210))
    c.arrow((450, 180), (580, 180), label="6 h unused, or Pause", at=(515, 170))
    c.arrow((580, 204), (450, 204), label="visit, Start, update", at=(515, 222))
    c.arrow((360, 220), (360, 320), label="Switch off", at=(345, 274), anchor="end")
    c.arrow((390, 320), (390, 220), label="Switch on", at=(405, 274), anchor="start")
    c.arrow((655, 220), (655, 350), (450, 350), label="Switch off", at=(600, 342))
    c.arrow((300, 350), (220, 350), label="Delete", at=(260, 342))
    c.text(
        390, 408, "Delete, or the end of the instance's lifetime, works from every state; a backup is taken first.", "m"
    )
    c.save("lifecycle")


def create_flow():
    c = Canvas(780, 700, "Creating an instance")
    steps = [
        "Repository, branch, version, edition, lifetime",
        "Access check: the repository and every submodule",
        "Name, database name and three ports reserved",
        "git clone --recurse-submodules",
        ".env, odoo.conf (master password hashed), docker-compose.yml",
        "Download the Odoo image (once per version)",
        "Start PostgreSQL, create the database (+ web_enterprise)",
        "Install the repository's own modules (if chosen)",
        "Gateway and connector, wait for the public address",
        "Start Odoo, wait until it answers",
        "Show the link and the login",
    ]
    x, w, h, gap = 40, 470, 40, 22
    for i, text in enumerate(steps):
        y = 20 + i * (h + gap)
        kind = "outside" if i == 0 else "decision" if i == 1 else "ok" if i == len(steps) - 1 else "default"
        c.box(x, y, w, h, None, [text], kind)
        if i < len(steps) - 1:
            c.arrow((x + w / 2, y + h), (x + w / 2, y + h + gap))
    y = 20 + h + gap
    c.box(570, y - 6, 190, 52, None, ["Create a deploy key,", "add it on GitHub"], "decision")
    c.arrow((x + w, y + 14), (570, y + 14), label="no access", at=(540, y + 6))
    c.arrow(
        (665, y + 46),
        (665, y + 70),
        (x + w + 40, y + 70),
        (x + w + 40, y + 30),
        (x + w, y + 30),
        label="check again",
        at=(620, y + 86),
    )
    c.box(570, 330, 190, 72, None, ["Any step fails:", "everything created", "so far is removed"], "danger")
    c.save("create-flow")


def code_update():
    c = Canvas(820, 590, "Code from GitHub")
    c.box(30, 20, 230, 50, "Developer pushes", ["the panel checks every 2 min"], "outside")
    c.box(290, 20, 210, 50, '"Get latest code"', ["clicked in the panel"], "outside")
    c.arrow((145, 70), (145, 96), (395, 96), (395, 116))
    c.arrow((395, 70), (395, 116))
    col, w = 270, 250
    c.box(col - 20, 116, w + 40, 46, None, ["Does the branch still exist on GitHub?"], "decision")
    c.box(580, 116, 210, 46, None, ['Stop: "Switch branch"'], "danger")
    c.arrow((col + w + 20, 139), (580, 139), label="no", at=(555, 131))
    c.arrow((395, 162), (395, 196), label="yes", at=(412, 184), anchor="start")
    c.box(col, 196, w, 46, None, ["git pull, git submodule update"], "default")
    c.arrow((395, 242), (395, 276))
    c.box(col, 276, w, 46, None, ["Anything new?"], "decision")
    c.box(580, 276, 210, 46, None, ["Nothing to do"], "default")
    c.arrow((col + w, 299), (580, 299), label="no", at=(550, 291))
    c.arrow((395, 322), (395, 356), label="yes", at=(412, 344), anchor="start")
    c.box(col - 30, 356, w + 60, 46, None, ["Which modules have changed files?"], "default")
    c.arrow((395, 402), (395, 436))
    c.box(col - 50, 436, w + 100, 46, None, ['"Upgrade changed modules" on, and any changed?'], "decision")
    c.box(40, 526, 300, 46, None, ["Stop Odoo, odoo -u <changed>, start"], "ok")
    c.box(470, 526, 300, 46, None, ["Restart Odoo"], "ok")
    c.arrow((320, 482), (320, 504), (190, 504), (190, 526), label="yes", at=(255, 500))
    c.arrow((470, 482), (470, 504), (620, 504), (620, 526), label="no", at=(545, 500))
    c.save("code-update")


def tunnel_problem():
    c = Canvas(860, 300, "Why the server cannot be reached directly")
    c.box(20, 70, 150, 160, "Internet", ["anyone, anywhere"], "outside")
    c.box(300, 40, 230, 220, "Provider's machine", ["public address", "203.0.113.10"], "default")
    c.box(660, 40, 180, 220, "Our server", ["a container,", "private address", "10.x.x.x"], "instance")
    c.arrow((170, 110), (660, 110), label="ssh -p 2222: forwarded to SSH", at=(415, 100), style="bold")
    c.arrow((170, 200), (380, 200), label="https on port 9000", at=(255, 190), style="bad")
    c.text(398, 206, "✗", "bad-text")
    c.text(470, 206, "dropped", "lab")
    c.text(
        430,
        284,
        "Only the SSH port is forwarded. The provider will not forward more, so a web panel "
        "on the server cannot be opened directly.",
        "m",
    )
    c.save("tunnel-problem")


def tunnel_how():
    c = Canvas(860, 330, "How a tunnel works")
    c.text(20, 30, "1. The connector on our server calls OUT to Cloudflare and keeps the line open", "t", "start")
    c.box(20, 50, 220, 60, "Our server", ["connector (cloudflared)"], "instance")
    c.box(600, 50, 240, 60, "Cloudflare", ["gives the line a public address"], "cloud")
    c.arrow((240, 80), (600, 80), label="outgoing connection: allowed", at=(420, 70), style="bold")
    c.text(20, 170, "2. Visitors use the address; Cloudflare sends them down the open line", "t", "start")
    c.box(20, 190, 140, 60, "Visitor", ["opens the link"], "outside")
    c.box(240, 190, 170, 60, "Cloudflare", [], "cloud")
    c.box(490, 190, 150, 60, "Connector", [], "instance")
    c.box(700, 190, 140, 60, "Our program", ["on 127.0.0.1"], "panel")
    c.arrow((160, 212), (240, 212))
    c.arrow((410, 212), (490, 212), style="bold")
    c.arrow((640, 212), (700, 212))
    c.arrow((700, 232), (640, 232), style="dash")
    c.arrow((490, 232), (410, 232), style="dash bold")
    c.arrow((240, 232), (160, 232), style="dash")
    c.text(
        430,
        300,
        "The visit travels inside a connection the server started, so nothing has to be opened "
        "on the server or the provider.",
        "m",
    )
    c.save("tunnel-how")


def deploy_key_flow():
    c = Canvas(820, 420, "What happens when a deploy key is created and used")
    x, w = 40, 380
    c.box(x, 20, w, 46, None, ['1. Administrator: "Create deploy key"'], "outside")
    c.arrow((x + w / 2, 66), (x + w / 2, 96))
    c.box(x, 96, w, 46, None, ["2. ssh-keygen makes a new key pair, in memory"], "default")
    c.box(480, 70, 310, 46, None, ["private key: encrypted with the vault key"], "panel")
    c.box(480, 126, 310, 46, None, ["public key: shown in the panel"], "default")
    c.arrow((x + w, 112), (480, 93))
    c.arrow((x + w, 126), (480, 149))
    c.arrow((x + w / 2, 142), (x + w / 2, 196))
    c.box(x, 196, w, 46, None, ["3. Pasted on GitHub by a repository admin"], "outside")
    c.arrow((x + w / 2, 242), (x + w / 2, 276))
    c.box(
        x,
        276,
        w,
        64,
        None,
        ["4. Each git fetch: the key is decrypted into", "memory, used for one connection, deleted"],
        "ok",
    )
    c.box(480, 276, 310, 64, "GitHub", ["checks the key; read-only access", "to this one repository"], "cloud")
    c.arrow((x + w, 308), (480, 308), label="SSH", at=(450, 300), both=True)
    c.text(410, 396, "The private key is never stored readable and never leaves the server.", "m")
    c.save("deploy-key-flow")


def docker_server():
    c = Canvas(820, 430, "Containers on the server")
    c.group(20, 20, 780, 390, "Server (Docker)")
    c.box(40, 50, 230, 52, "sudoo-panel", ["the panel, gateway, background"], "panel")
    c.box(290, 50, 230, 52, "sudoo-connector-panel", ["the panel's public link"], "cloud")
    for i, (name, version) in enumerate([("sales-main", "odoo:20.0"), ("crm-19", "odoo:19.0")]):
        y = 130 + i * 140
        c.group(40, y, 740, 120, f"instance {name}: Compose project odoo-{name}")
        c.box(60, y + 34, 200, 70, f"odoo-{name}-odoo-1", [version], "instance")
        c.box(290, y + 34, 200, 70, f"odoo-{name}-db-1", ["postgres:16"], "instance")
        c.box(520, y + 34, 240, 70, f"sudoo-connector-{name}", ["its public link"], "cloud")
        c.arrow((260, y + 69), (290, y + 69))
    c.text(410, 422, "Each instance has its own containers and volumes: none can see another's files or database.", "m")
    c.save("docker-server")


def reverse_proxy():
    c = Canvas(700, 130, "A reverse proxy")
    c.box(20, 30, 170, 60, "Visitor", [], "outside")
    c.box(265, 30, 170, 60, "Reverse proxy", ["(our gateway)"], "panel")
    c.box(510, 30, 170, 60, "Odoo", [], "instance")
    for x1, x2 in ((190, 265), (435, 510)):
        c.arrow((x1, 52), (x2, 52))
        c.arrow((x2, 70), (x1, 70), style="dash")
    c.text(350, 118, "The visitor only ever talks to the proxy; the proxy passes requests on and answers back.", "m")
    c.save("reverse-proxy")


def password_hash():
    c = Canvas(860, 190, "How a panel password is stored")
    boxes = [
        ("Password", ["typed at sign-in"], "outside"),
        ("HMAC with the pepper", ["key from vault.key"], "panel"),
        ("scrypt with a salt", ["slow and memory-hungry"], "panel"),
        ("Stored hash", ["in users.json"], "ok"),
    ]
    w, gap = 180, 40
    for i, (title, lines, kind) in enumerate(boxes):
        x = 20 + i * (w + gap)
        c.box(x, 40, w, 64, title, lines, kind)
        if i < len(boxes) - 1:
            c.arrow((x + w, 72), (x + w + gap, 72))
    c.text(430, 150, "One-way: the password cannot be calculated back from the stored hash.", "m")
    c.text(430, 172, "Without vault.key, a copy of users.json cannot even be attacked.", "m")
    c.save("password-hash")


def instance_page():
    c = Canvas(820, 400, "An instance's page in the panel")
    c.box(10, 10, 800, 380, None, [], "ui", radius=6)
    c.text(28, 40, "Instances", "lab", "start")
    c.text(98, 40, "/  sales-main", "s", "start")
    c.box(708, 22, 84, 28, None, ["Actions ▾"], "button", radius=4)
    c.raw('<line class="life" x1="10" y1="60" x2="810" y2="60"/>')
    x = 28
    for text, kind in (
        ("Open Odoo", "primary"),
        ("Restart", "button"),
        ("Get latest code", "button"),
        ("View logs", "button"),
    ):
        w = width_of(text, 12.5) + 26
        c.box(x, 72, w, 28, None, [], kind, radius=4)
        c.text(x + w / 2, 90.5, text, "s white" if kind == "primary" else "s")
        x += w + 8
    for i, (text, current) in enumerate((("Running", True), ("Paused", False), ("Switched off", False))):
        c.box(490 + i * 104, 72, 104, 28, None, [text], "current" if current else "ui", radius=0)
    c.raw('<line class="life" x1="10" y1="112" x2="810" y2="112"/>')
    c.text(40, 150, "sales-main", "t", "start")
    c.box(140, 136, 70, 20, None, ["Running"], "ok", radius=10)
    c.text(40, 176, "https://river-stone-apple.trycloudflare.com", "mono", "start")
    fields = [
        ("Status", "Running and ready"),
        ("Odoo", "20.0 Enterprise"),
        ("Branch", "main"),
        ("Database", "sales_main"),
    ]
    more = [
        ("Odoo login", "admin / hidden"),
        ("Last visit", "5 min ago"),
        ("Automatic pause", "in 5.9 h"),
        ("Lifetime", "deleted on 11/12, after a backup"),
    ]
    for i, ((k1, v1), (k2, v2)) in enumerate(zip(fields, more, strict=True)):
        y = 214 + i * 26
        c.text(40, y, k1, "t", "start"), c.text(150, y, v1, "s", "start")
        c.text(420, y, k2, "t", "start"), c.text(550, y, v2, "s", "start")
    x = 40
    for i, tab in enumerate(("Resource usage", "Actions", "Code", "Activity", "Automation", "Technical details")):
        w = width_of(tab, 12.5) + 24
        if i == 0:
            c.box(x, 330, w, 30, None, [], "default", radius=4)
        c.text(x + w / 2, 350, tab, "s")
        x += w + 4
    c.raw('<line class="life" x1="40" y1="360" x2="790" y2="360"/>')
    c.save("instance-page")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for build in (
        architecture,
        visit_flow,
        wake_sequence,
        lifecycle,
        create_flow,
        code_update,
        tunnel_problem,
        tunnel_how,
        deploy_key_flow,
        docker_server,
        reverse_proxy,
        password_hash,
        instance_page,
    ):
        build()
    if PROBLEMS:
        raise SystemExit("Labels too wide:\n  " + "\n  ".join(PROBLEMS))
    print(f"diagrams written to {OUT}")
