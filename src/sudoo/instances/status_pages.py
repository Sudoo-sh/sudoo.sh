"""The pages the gateway shows a visitor instead of Odoo: while an instance is not available (starting, starting soon
while the panel restarts, updating, upgrading to a newer Odoo version, switched off, paused on a locked branch, gone)
and when production access rules turn a visitor away (render_blocked).

API clients and webhook senders get plain text with HTTP 503 and Retry-After: 4 ("try again shortly"), exactly as
before; the blocked answer is a 403, as JSON when the client asks for JSON, else plain text.

Browsers get one small page per situation, in one visual family: a calm animated illustration drawn inline (SVG and
CSS, no file and no request to anywhere else), the panel's plum accent, light or dark as the visitor's system says,
English or Arabic (right to left) as the browser's Accept-Language prefers, and no motion when the visitor asked for
reduced motion. While something is under way the page shows the job's current step and progress, and asks the
gateway's public status address (STATUS_PATH, status_json) every few seconds; it opens Odoo by itself when it is
ready. That address tells only the state, the current step's title, its progress text and percentages, and an
estimate of the time left: never logs, names of people, or anything else of the job. Without JavaScript the page
refreshes itself every 4 seconds, as before.
"""

from __future__ import annotations

import html
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiohttp import web

from sudoo.config import settings

_MARK = (Path(__file__).resolve().parent / "mark.svg").read_text().strip()  # the same as web/public/favicon.svg
_ICON = "data:image/svg+xml," + quote(_MARK)  # the Sudoo.sh mark

STATUS_PATH = "/__sudoo/status"
WAITING_KINDS = ("waking", "waiting", "busy", "upgrading")
STATES = (*WAITING_KINDS, "off", "locked", "gone", "ready")

# the English texts, as API clients get them ("<title>. <text>"): unchanged
_PAGES = {
    "waking": (
        "Starting up",
        "This Odoo was paused to save server resources because nobody used it for a while. It is starting "
        "now, which usually takes 20 to 60 seconds. This page opens Odoo automatically when it is ready.",
    ),
    "waiting": (
        "Starting soon",
        "This Odoo was paused to save server resources because nobody used it for a while. The server's control "
        "panel is restarting at the moment; this Odoo starts as soon as the panel is back, usually within a minute. "
        "This page opens Odoo automatically when it is ready.",
    ),
    "busy": (
        "Update in progress",
        "This Odoo is being updated. It will be available again in a minute or two. This page refreshes "
        "automatically.",
    ),
    "upgrading": (
        "Upgrading",
        "This Odoo is being upgraded to a newer version. It is back as soon as the upgrade has finished, which can "
        "take a while for a large database. This page refreshes automatically.",
    ),
    "off": ("Switched off", "This Odoo has been switched off by the administrator."),
    "locked": (
        "Locked",
        "This Odoo is paused and its branch is locked, so it is not started by a visit. Someone the lock allows can "
        "start it from the control panel.",
    ),
    "gone": ("Not found", "This Odoo instance does not exist."),
}

_PAGES_AR = {
    "waking": (
        "جارٍ التشغيل",
        "أُوقف Odoo هذا مؤقتًا لتوفير موارد الخادم لأن أحدًا لم يستخدمه منذ مدة. إنه يبدأ الآن، ويستغرق ذلك عادةً "
        "من 20 إلى 60 ثانية. تفتح هذه الصفحة Odoo تلقائيًا عندما يصبح جاهزًا.",
    ),
    "waiting": (
        "يبدأ قريبًا",
        "أُوقف Odoo هذا مؤقتًا لتوفير موارد الخادم. لوحة التحكم تُعيد تشغيلها الآن، وسيبدأ Odoo فور عودتها، عادةً "
        "خلال دقيقة. تفتح هذه الصفحة Odoo تلقائيًا عندما يصبح جاهزًا.",
    ),
    "busy": ("جارٍ التحديث", "يجري تحديث Odoo هذا، وسيعود متاحًا خلال دقيقة أو اثنتين. تتحدّث هذه الصفحة تلقائيًا."),
    "upgrading": (
        "جارٍ الترقية",
        "تجري ترقية Odoo هذا إلى إصدار أحدث، ويعود فور انتهائها، وقد يستغرق ذلك بعض الوقت لقاعدة بيانات كبيرة. "
        "تتحدّث هذه الصفحة تلقائيًا.",
    ),
    "off": ("متوقف", "أوقف المسؤول Odoo هذا."),
    "locked": (
        "مقفل",
        "Odoo هذا متوقف مؤقتًا وفرعه مقفل، فلا تشغّله الزيارة. يستطيع من يسمح لهم القفل تشغيله من لوحة التحكم.",
    ),
    "gone": ("غير موجود", "بيئة Odoo هذه غير موجودة."),
}

_WORDS = {
    "en": {
        "refreshes": "This page refreshes by itself",
        "opens": "Odoo opens by itself when it is ready",
        "waiting": "Waiting for {n} s",
        "waiting_min": "Waiting for {n} min",
        "left": "About {n} min left",
        "left_soon": "Less than a minute left",
        "longer": "This is taking longer than usual. ",
        "step": "Step {n} of {t}",
        "now": "Now",
        "contact": "Please contact {who}",
        "the_admin": "the administrator",
        "blocked_title": "This site isn't available from your location / network",
        "blocked_text": "The owner of this Odoo lets people in only from certain places or networks. If you should "
        "have access, the administrator can help.",
        "country": "Your country",
        "unknown_country": "Unknown",
        "address": "Your address",
        "reference": "Reference",
        "reference_hint": "Give this reference to the administrator so they can find this visit.",
    },
    "ar": {
        "refreshes": "تتحدّث هذه الصفحة تلقائيًا",
        "opens": "يُفتح Odoo تلقائيًا عندما يصبح جاهزًا",
        "waiting": "الانتظار منذ {n} ث",
        "waiting_min": "الانتظار منذ {n} د",
        "left": "يتبقى نحو {n} د",
        "left_soon": "يتبقى أقل من دقيقة",
        "longer": "يستغرق هذا وقتًا أطول من المعتاد. ",
        "step": "الخطوة {n} من {t}",
        "now": "الآن",
        "contact": "يرجى التواصل مع {who}",
        "the_admin": "المسؤول",
        "blocked_title": "هذا الموقع غير متاح من موقعك أو شبكتك",
        "blocked_text": "يسمح مالك Odoo هذا بالدخول من أماكن أو شبكات معيّنة فقط. إن كان يُفترض أن يكون لديك وصول، "
        "فبإمكان المسؤول مساعدتك.",
        "country": "بلدك",
        "unknown_country": "غير معروف",
        "address": "عنوانك",
        "reference": "المرجع",
        "reference_hint": "أعطِ هذا المرجع للمسؤول ليجد هذه الزيارة.",
    },
}

# --------------------------------------------------------------------------- the illustrations (inline SVG + CSS)

_ART = {
    # a globe whose meridians turn slowly, with a shield and lock in front that breathes
    "blocked": """<svg viewBox="0 0 160 128" aria-hidden="true"><defs><clipPath id="gc"><circle cx="66" cy="62" r="42"/>
</clipPath></defs><circle cx="66" cy="62" r="42" class="fill"/><g clip-path="url(#gc)" class="ln">
<ellipse cx="66" cy="62" rx="42" ry="14"/><path d="M24 62h84M30 40h72M30 84h72"/>
<ellipse class="mer m1" cx="66" cy="62" rx="42" ry="42"/><ellipse class="mer m2" cx="66" cy="62" rx="42" ry="42"/>
<ellipse class="mer m3" cx="66" cy="62" rx="42" ry="42"/></g><circle cx="66" cy="62" r="42" class="ln"/>
<circle cx="112" cy="86" r="22" class="pulse"/><path class="shield" d="M112 64l18 7v12c0 11-8 19-18 23-10-4-18-12-18-23V71z"/>
<rect x="104" y="83" width="16" height="12" rx="3" class="lock"/><path d="M107 83v-4a5 5 0 0 1 10 0v4" class="shackle"/></svg>""",
    # a sun rising softly over a horizon
    "waking": """<svg viewBox="0 0 160 128" aria-hidden="true"><g class="rise"><circle cx="80" cy="74" r="24" class="sun"/>
<g class="rays ln"><path d="M80 34v-10M52 46l-7-7M108 46l7-7M40 74H30M120 74h10"/></g></g>
<rect x="18" y="76" width="124" height="40" class="ground"/><path d="M24 76h112" class="ln"/>
<path d="M44 90h20M92 90h28M60 102h40" class="ln soft"/></svg>""",
    # a clock whose hand turns: the panel is coming back
    "waiting": """<svg viewBox="0 0 160 128" aria-hidden="true"><circle cx="80" cy="64" r="40" class="fill"/>
<circle cx="80" cy="64" r="40" class="ln"/><path d="M80 30v6M80 92v6M46 64h6M108 64h6" class="ln"/>
<path d="M80 64V44" class="hand hour"/><path d="M80 64h16" class="hand minute"/><circle cx="80" cy="64" r="3" class="dotc"/>
<circle cx="80" cy="64" r="48" class="pulse"/></svg>""",
    # layers lifting one after the other, and a small gear turning: work in progress
    "busy": """<svg viewBox="0 0 160 128" aria-hidden="true"><g class="layers">
<path class="layer l3" d="M80 86l-44-18 44-18 44 18z"/><path class="layer l2" d="M80 72l-44-18 44-18 44 18z"/>
<path class="layer l1" d="M80 58l-44-18 44-18 44 18z"/></g><g class="gear"><circle cx="126" cy="98" r="9" class="ln"/>
<path d="M126 84v5M126 107v5M112 98h5M135 98h5M116 88l3.5 3.5M132.5 104.5L136 108M116 108l3.5-3.5M132.5 91.5L136 88" class="ln"/>
</g></svg>""",
    # an arrow carrying a box upwards, gently
    "upgrading": """<svg viewBox="0 0 160 128" aria-hidden="true"><rect x="44" y="70" width="72" height="40" rx="10" class="fill"/>
<rect x="44" y="70" width="72" height="40" rx="10" class="ln"/><g class="lift"><path d="M80 82V30M62 48l18-18 18 18" class="arrow"/></g>
<path d="M58 92h44" class="ln soft"/></svg>""",
    # a sleeping moon, letters drifting up, stars twinkling
    "off": """<svg viewBox="0 0 160 128" aria-hidden="true"><path class="moon" d="M92 26a40 40 0 1 0 22 70A34 34 0 0 1 92 26z"/>
<g class="zz"><text x="112" y="44" class="z z1">z</text><text x="124" y="30" class="z z2">z</text></g>
<circle cx="30" cy="34" r="2" class="star s1"/><circle cx="44" cy="100" r="1.6" class="star s2"/>
<circle cx="132" cy="88" r="1.8" class="star s3"/></svg>""",
    # a padlock with a soft halo
    "locked": """<svg viewBox="0 0 160 128" aria-hidden="true"><circle cx="80" cy="70" r="40" class="pulse"/>
<path d="M64 60V48a16 16 0 0 1 32 0v12" class="shackle wide"/><rect x="54" y="58" width="52" height="42" rx="10" class="lock"/>
<circle cx="80" cy="76" r="5" class="hole"/><path d="M80 80v8" class="hole-line"/></svg>""",
    # an empty page with a magnifier drifting over it: gentle, nothing alarming
    "gone": """<svg viewBox="0 0 160 128" aria-hidden="true"><rect x="46" y="18" width="62" height="84" rx="8" class="fill"/>
<rect x="46" y="18" width="62" height="84" rx="8" class="ln dash"/><path d="M58 40h38M58 52h26" class="ln soft"/>
<g class="drift"><circle cx="102" cy="80" r="16" class="glass"/><path d="M114 92l14 14" class="handle"/></g></svg>""",
}

_CSS = """
:root{--bg:#f7f6f8;--card:#fff;--text:#1d1d1f;--muted:#5f5e66;--faint:#8a8991;--line:rgba(23,23,23,.09);
--accent:#714b67;--tint:rgba(113,75,103,.10);--tint2:rgba(113,75,103,.18);color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#121214;--card:#1a1a1d;--text:#ededed;--muted:#a1a1a6;--faint:#7c7c83;
--line:rgba(237,237,237,.09);--accent:#c79bbd;--tint:rgba(199,155,189,.10);--tint2:rgba(199,155,189,.20)}}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px 16px;background:var(--bg);
color:var(--muted);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Ubuntu,"Noto Sans","Noto Sans Arabic",
Tahoma,Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{width:100%;max-width:460px;text-align:center;animation:in .5s cubic-bezier(.2,.8,.2,1) both}
.art{width:168px;height:134px;margin:0 auto 18px}
.art svg{width:100%;height:100%;overflow:visible}
.name{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--faint);margin:0 0 6px;
direction:ltr;unicode-bidi:isolate}
h1{font-size:22px;line-height:1.3;margin:0 0 10px;font-weight:600;color:var(--text);letter-spacing:-.01em}
p{margin:0 0 12px}
.lead{max-width:400px;margin-inline:auto}
.panel{margin:20px auto 0;max-width:400px;background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:14px 16px;text-align:start}
.step{display:flex;align-items:center;gap:10px;color:var(--text);font-weight:500;font-size:14px}
.step .dot{width:8px;height:8px;border-radius:50%;background:var(--accent);flex:none;animation:breathe 2s ease-in-out infinite}
.step bdi{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar{height:6px;border-radius:99px;background:var(--tint2);overflow:hidden;margin:10px 0 8px}
.bar i{display:block;height:100%;width:var(--w,0%);background:var(--accent);border-radius:inherit;
transition:width .6s cubic-bezier(.2,.8,.2,1)}
.bar.unknown i{width:35%;animation:slide 1.8s ease-in-out infinite}
.row{display:flex;justify-content:space-between;gap:12px;font-size:13px;color:var(--faint)}
.row span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.meta{margin-top:16px;font-size:13px;color:var(--faint)}
.meta .sep{margin:0 6px}
.facts{margin:18px auto 0;max-width:400px;background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:6px 16px;text-align:start;font-size:14px}
.facts div{display:flex;justify-content:space-between;gap:12px;padding:8px 0}
.facts div+div{border-top:1px solid var(--line)}
.facts dt{color:var(--faint)}.facts dd{margin:0;color:var(--text);font-weight:500}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;direction:ltr;unicode-bidi:isolate}
.hint{font-size:12px;color:var(--faint);margin-top:10px}
.contact{margin-top:18px;padding-top:14px;border-top:1px solid var(--line);font-size:14px}
.fill{fill:var(--tint)}.ln{fill:none;stroke:var(--accent);stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
.soft{opacity:.45}.dash{stroke-dasharray:5 6}
.mer{fill:none;stroke:var(--accent);stroke-width:1.6;opacity:.7;transform-box:fill-box;transform-origin:center;
animation:turn 9s linear infinite}
.m2{animation-delay:-3s}.m3{animation-delay:-6s}
.shield{fill:var(--card);stroke:var(--accent);stroke-width:2.4;stroke-linejoin:round}
.lock{fill:var(--accent)}.shackle{fill:none;stroke:var(--accent);stroke-width:2.4;stroke-linecap:round}
.wide{stroke-width:6}.hole{fill:var(--card)}.hole-line{stroke:var(--card);stroke-width:4;stroke-linecap:round}
.pulse{fill:var(--tint2);transform-box:fill-box;transform-origin:center;animation:pulse 3.2s ease-out infinite}
.sun{fill:var(--accent);opacity:.9}.rise{animation:rise 4s cubic-bezier(.4,0,.2,1) infinite alternate}
.rays{animation:fade 4s ease-in-out infinite alternate}
.ground{fill:var(--bg)}
.hand{stroke:var(--accent);stroke-width:3;stroke-linecap:round;transform-origin:80px 64px}
.minute{animation:spin 6s linear infinite}.hour{animation:spin 48s linear infinite}.dotc{fill:var(--accent)}
.layer{fill:var(--card);stroke:var(--accent);stroke-width:2.2;stroke-linejoin:round;animation:lift 3s ease-in-out infinite}
.l1{fill:var(--tint2)}.l2{animation-delay:.25s}.l3{animation-delay:.5s}
.gear{transform-origin:126px 98px;animation:spin 7s linear infinite}
.arrow{fill:none;stroke:var(--accent);stroke-width:4;stroke-linecap:round;stroke-linejoin:round}
.lift{animation:lift 2.6s ease-in-out infinite}
.moon{fill:var(--tint2);stroke:var(--accent);stroke-width:2.2}
.z{fill:var(--accent);font:600 15px/1 system-ui,sans-serif;opacity:0;animation:zz 4s ease-in-out infinite}
.z2{animation-delay:2s;font-size:11px}
.star{fill:var(--accent);animation:twinkle 3s ease-in-out infinite}.s2{animation-delay:1s}.s3{animation-delay:2s}
.glass{fill:var(--card);stroke:var(--accent);stroke-width:3}.handle{stroke:var(--accent);stroke-width:5;stroke-linecap:round}
.drift{animation:drift 6s ease-in-out infinite}
@keyframes in{from{opacity:0;transform:translateY(6px)}}
@keyframes turn{0%{transform:scaleX(1)}50%{transform:scaleX(.04)}100%{transform:scaleX(1)}}
@keyframes pulse{0%{transform:scale(.82);opacity:.9}70%{opacity:0}100%{transform:scale(1.35);opacity:0}}
@keyframes rise{from{transform:translateY(10px)}to{transform:translateY(-2px)}}
@keyframes fade{from{opacity:.15}to{opacity:.9}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes lift{0%,100%{transform:translateY(0)}50%{transform:translateY(-5px)}}
@keyframes zz{0%{opacity:0;transform:translateY(6px)}40%{opacity:.9}100%{opacity:0;transform:translateY(-10px)}}
@keyframes twinkle{0%,100%{opacity:.25}50%{opacity:1}}
@keyframes drift{0%,100%{transform:translate(0,0)}50%{transform:translate(-14px,-8px)}}
@keyframes breathe{0%,100%{opacity:1}50%{opacity:.35}}
@keyframes slide{0%{transform:translateX(-110%)}100%{transform:translateX(320%)}}
[dir=rtl] .bar.unknown i{animation-name:slide-rtl}
@keyframes slide-rtl{0%{transform:translateX(110%)}100%{transform:translateX(-320%)}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}
.z{opacity:.7}.bar.unknown i{width:100%;opacity:.35}}
"""

_SCRIPT = """(function(){var d=document,b=d.body,s=+b.dataset.elapsed||0,k=b.dataset.kind,f=0,el=d.getElementById("elapsed");
function t(n){return n<90?b.dataset.sec.replace("{n}",n):b.dataset.min.replace("{n}",Math.round(n/60))}
setInterval(function(){s++;if(el)el.textContent=t(s)},1000);
function set(id,v){var e=d.getElementById(id);if(e){e.textContent=v||"";e.hidden=!v}}
function poll(){fetch(b.dataset.status,{cache:"no-store",credentials:"same-origin",headers:{Accept:"application/json"}})
.then(function(r){return r.status==200?r.json():null}).then(function(j){if(!j)return;f=0;
if(j.state=="ready"||j.state!=k){location.reload();return}
var p=d.getElementById("progress");if(p&&j.step){p.hidden=false;set("step",j.step);set("ptext",j.progress);
var w=j.overall!=null?j.overall:j.percent;var bar=d.getElementById("bar");if(bar){bar.classList.toggle("unknown",w==null);
if(w!=null)bar.style.setProperty("--w",w+"%")}set("pct",w!=null?w+"%":"");
set("count",j.steps?b.dataset.stepof.replace("{n}",j.steps.current).replace("{t}",j.steps.total):"")}
set("eta",j.eta_seconds==null?"":j.eta_seconds<60?b.dataset.soon:b.dataset.left.replace("{n}",Math.round(j.eta_seconds/60)))})
.catch(function(){if(++f>3)location.reload()}).then(function(){setTimeout(poll,3000)})}
setTimeout(poll,2000)})();"""


def language(accept: str) -> str:
    """ "ar" or "en": the first of the two the visitor's Accept-Language prefers (q-values honoured); English when
    neither is named."""
    best: tuple[float, int, str] | None = None
    for index, part in enumerate(accept.split(",")[:20]):
        pieces = part.strip().split(";")
        tag = pieces[0].strip().lower()
        quality = 1.0
        for piece in pieces[1:]:
            if piece.strip().startswith("q="):
                try:
                    quality = float(piece.strip()[2:])
                except ValueError:
                    quality = 0.0
        code = "ar" if tag == "ar" or tag.startswith("ar-") else "en" if tag == "en" or tag.startswith("en-") else ""
        if code and quality > 0 and (best is None or (quality, -index) > (best[0], best[1])):
            best = (quality, -index, code)
    return best[2] if best else "en"


def _contact(lang: str, custom: str = "") -> str:
    """The "contact the administrator" line: the access rules' own text when set, else built from ADMIN_CONTACT_*."""
    if custom:
        return custom
    if lang == "en":
        return settings.contact_sentence()
    words = _WORDS[lang]
    details = "، ".join(v for v in (settings.admin_contact_phone, settings.admin_contact_email) if v)
    who = words["the_admin"]
    name = settings.admin_contact_name
    if name and name.lower() != "the administrator":
        who += f"، {name}"
    return words["contact"].format(who=who) + (f" ({details})." if details else ".")


def flag(country: str | None) -> str:
    """The emoji flag of a two-letter country code ("" when there is none)."""
    if not country or len(country) != 2 or not country.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country.upper())


def _elapsed_text(lang: str, seconds: int) -> str:
    words = _WORDS[lang]
    if seconds < 90:
        return words["waiting"].format(n=seconds)
    return words["waiting_min"].format(n=round(seconds / 60))


def _eta_text(lang: str, eta: int | None) -> str:
    if eta is None:
        return ""
    words = _WORDS[lang]
    return words["left_soon"] if eta < 60 else words["left"].format(n=round(eta / 60))


def _document(lang: str, title: str, name: str, body: str, *, nonce: str, data: str = "", script: bool = False) -> str:
    direction = "rtl" if lang == "ar" else "ltr"
    refresh = '<noscript><meta http-equiv="refresh" content="4"></noscript>' if script else ""
    code = f'<script nonce="{nonce}">{_SCRIPT}</script>' if script else ""
    return (
        f'<!doctype html><html lang="{lang}" dir="{direction}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
        f'<meta name="color-scheme" content="light dark">{refresh}'
        f"<title>{html.escape(title)} - {html.escape(name)}</title>"
        f'<link rel="icon" href="{_ICON}"><style nonce="{nonce}">{_CSS}</style></head>'
        f"<body{data}><main>{body}</main>{code}</body></html>"
    )


def _security_headers(nonce: str) -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; img-src data:; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        ),
    }


def _wants_html(request: web.Request) -> bool:
    return request.method == "GET" and "text/html" in request.headers.get("Accept", "")


# --------------------------------------------------------------------------- progress of the job, for visitors


def public_progress(job: Any, now: float | None = None) -> dict[str, Any] | None:
    """What visitors may see of an instance's running job: the running step's title, its progress text and
    percentage, the job's overall percentage, which step of how many, and an estimate of the time left. Nothing else
    (no log, no notes, no names). None when there is no job or no step yet."""
    if job is None or not getattr(job, "steps", None):
        return None
    running = next((step for step in reversed(job.steps) if step.get("status") == "running"), None)
    if running is None:
        return None
    total = getattr(job, "expected_steps", None) or 0
    current = len(job.steps)
    percent = running.get("percent")
    found: dict[str, Any] = {
        "step": str(running.get("title") or "")[:120],
        "progress": str(running.get("progress") or "")[:160] or None,
        "percent": percent if isinstance(percent, int) and 0 <= percent <= 100 else None,
        "overall": job.progress_percent(),
        "steps": {"current": min(current, max(total, current)), "total": max(total, current)} if total else None,
        "eta_seconds": estimate_left(job, now),
    }
    return found


def estimate_left(job: Any, now: float | None = None) -> int | None:
    """Seconds the job may still take: the duration of the last finished job of the same kind on this instance (else
    the middle one of the same kind on any instance), minus the time it has run; None when unknown or overdue."""
    from sudoo.core import jobs

    now = time.time() if now is None else now
    same: list[float] = []
    other: list[float] = []
    for done in list(jobs.JOBS.values()):
        if done is job or done.kind != job.kind or done.status != "success" or not done.finished:
            continue
        (same if done.instance == job.instance else other).append(done.finished - done.started)
    durations = same[-1:] or sorted(other)[len(other) // 2 : len(other) // 2 + 1]
    if not durations:
        return None
    left = int(durations[0] - (now - job.started))
    return left if left > 0 else None


def clean_progress(raw: Any) -> dict[str, Any] | None:
    """Progress received from the panel (gateway_service.py), reduced to the fields and sizes allowed here."""
    if not isinstance(raw, dict) or not isinstance(raw.get("step"), str) or not raw["step"]:
        return None

    def number(value: Any, top: int) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= top else None

    steps = raw.get("steps")
    counted = None
    if isinstance(steps, dict) and number(steps.get("current"), 1000) and number(steps.get("total"), 1000):
        counted = {"current": steps["current"], "total": steps["total"]}
    progress = raw.get("progress")
    return {
        "step": raw["step"][:120],
        "progress": progress[:160] if isinstance(progress, str) and progress else None,
        "percent": number(raw.get("percent"), 100),
        "overall": number(raw.get("overall"), 100),
        "steps": counted,
        "eta_seconds": number(raw.get("eta_seconds"), 7 * 86400),
    }


@dataclass(frozen=True)
class Situation:
    """Why Odoo does not answer, and what its visitors are told."""

    page: str
    since: float | None = None
    progress: Mapping[str, Any] | None = None


def status_json(situation: Situation | None) -> dict[str, Any]:
    """The answer of the public status address: the state and the job's public progress, nothing else."""
    if situation is None:
        return {"state": "ready"}
    answer: dict[str, Any] = {"state": situation.page if situation.page in STATES else "busy"}
    if situation.page in WAITING_KINDS and situation.progress:
        answer.update(clean_progress(dict(situation.progress)) or {})
    return answer


# --------------------------------------------------------------------------- the pages


def render(
    request: web.Request,
    name: str,
    kind: str,
    since: float | None = None,
    progress: Mapping[str, Any] | None = None,
) -> web.Response:
    """The response for a visitor of instance `name` while it is in situation `kind` (see _PAGES)."""
    title_en, text_en = _PAGES[kind]
    if not _wants_html(request):
        return web.Response(status=503, text=f"{title_en}. {text_en}", headers={"Retry-After": "4"})
    lang = language(request.headers.get("Accept-Language", ""))
    words = _WORDS[lang]
    title, text = (_PAGES_AR if lang == "ar" else _PAGES)[kind]
    waiting = kind in WAITING_KINDS
    elapsed = max(0, int(time.time() - since)) if since else 0
    contact = ""
    if kind in ("off", "gone", "locked") or (elapsed > 180 and kind != "upgrading"):
        lead = words["longer"] if waiting else ""
        contact = f'<p class="contact">{html.escape(lead + _contact(lang))}</p>'
    parts = [
        f'<div class="art">{_ART["waking" if kind == "waking" else kind]}</div>',
        f'<p class="name">{html.escape(name)}</p><h1>{html.escape(title)}</h1><p class="lead">{html.escape(text)}</p>',
    ]
    nonce = secrets.token_urlsafe(12)
    data = ""
    if waiting:
        info = clean_progress(dict(progress)) if progress else None
        overall = info.get("overall") if info else None
        shown = overall if overall is not None else (info.get("percent") if info else None)
        steps = info.get("steps") if info else None
        count = words["step"].format(n=steps["current"], t=steps["total"]) if steps else ""
        parts.append(
            f'<div class="panel" id="progress"{"" if info else " hidden"} aria-live="polite">'
            f'<div class="step"><span class="dot"></span><bdi id="step" dir="auto">'
            f'{html.escape(info["step"] if info else "")}</bdi></div>'
            f'<div class="bar{"" if shown is not None else " unknown"}" id="bar" style="--w:{shown or 0}%"><i></i></div>'
            f'<div class="row"><span id="ptext" dir="auto"{"" if info and info["progress"] else " hidden"}>'
            f'{html.escape((info or {}).get("progress") or "")}</span><span id="count">{html.escape(count)}</span>'
            f'<span id="pct" class="mono">{f"{shown}%" if shown is not None else ""}</span></div></div>'
        )
        eta = _eta_text(lang, info.get("eta_seconds") if info else None)
        parts.append(
            f'<p class="meta"><span id="elapsed">{html.escape(_elapsed_text(lang, elapsed))}</span>'
            f'<span class="sep">·</span><span id="eta"{"" if eta else " hidden"}>{html.escape(eta)}</span>'
            f'{"<span class=sep>·</span>" if eta else ""}'
            f'{html.escape(words["opens"] if kind in ("waking", "waiting") else words["refreshes"])}</p>'
        )
        values = {
            "kind": kind, "elapsed": str(elapsed), "status": STATUS_PATH, "sec": words["waiting"],
            "min": words["waiting_min"], "left": words["left"], "soon": words["left_soon"], "stepof": words["step"],
        }  # fmt: skip
        data = "".join(f' data-{key}="{html.escape(value)}"' for key, value in values.items())
    parts.append(contact)
    page = _document(lang, title, name, "".join(parts), nonce=nonce, data=data, script=waiting)
    return web.Response(
        status=503,
        text=page,
        content_type="text/html",
        headers={**_security_headers(nonce), "Retry-After": "4"},
    )


def render_blocked(
    request: web.Request,
    name: str,
    *,
    country: str | None,
    address: str | None,
    reference: str,
    contact: str = "",
) -> web.Response:
    """The answer when production access rules turn a visitor away: HTTP 403, a page for browsers, JSON for clients
    that ask for it, plain text otherwise. It shows the visitor's country (never the address unless the administrator
    chose to show it), a reference to quote, and how to reach the administrator."""
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Robots-Tag": "noindex"}
    en = _WORDS["en"]
    if not _wants_html(request):
        message = f"{en['blocked_title']}. {_contact('en', contact)}"
        if "json" in request.headers.get("Accept", "") or "json" in request.headers.get("Content-Type", ""):
            document = {"error": "access_denied", "message": message, "country": country, "reference": reference}
            return web.Response(status=403, text=json.dumps(document), content_type="application/json", headers=headers)
        return web.Response(status=403, text=f"{message} Reference: {reference}.", headers=headers)
    lang = language(request.headers.get("Accept-Language", ""))
    words = _WORDS[lang]
    country_text = f"{flag(country)} {country}" if country else words["unknown_country"]
    facts = [(words["country"], country_text.strip(), False)]
    if address:
        facts.append((words["address"], address, True))
    facts.append((words["reference"], reference, True))
    rows = "".join(
        f'<div><dt>{html.escape(label)}</dt><dd{" class=mono" if mono else ""}>{html.escape(value)}</dd></div>'
        for label, value, mono in facts
    )
    body = (
        f'<div class="art">{_ART["blocked"]}</div><p class="name">{html.escape(name)}</p>'
        f'<h1>{html.escape(words["blocked_title"])}</h1><p class="lead">{html.escape(words["blocked_text"])}</p>'
        f'<dl class="facts">{rows}</dl><p class="hint">{html.escape(words["reference_hint"])}</p>'
        f'<p class="contact">{html.escape(_contact(lang, contact))}</p>'
    )
    nonce = secrets.token_urlsafe(12)
    page = _document(lang, words["blocked_title"], name, body, nonce=nonce)
    return web.Response(
        status=403, text=page, content_type="text/html", headers={**headers, **_security_headers(nonce)}
    )
