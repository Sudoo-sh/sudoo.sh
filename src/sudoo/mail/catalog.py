"""Every e-mail the panel sends, with its built-in text and the placeholders it may use.

The E-mail templates page (web/v1/mail_templates.py) lists these and lets a super admin change the subject, the
title and the text of each one; the changes are kept in the panel's storage (overrides.py) and the built-in texts
below stay as they are, so "Reset to default" only forgets the change. Texts are in the plain format of textformat.py.

    sign_in_code         the 6-digit code that finishes a sign-in (second factor "email")
    email_confirmation   the code that confirms an address for e-mail codes
    sign_in_notice       "new sign-in to your account", after each sign-in (SIGN_IN_EMAIL)
    alert                one alert: a failed job, an instance down, a full disk, ... or that it is over
    alert_digest         the daily summary of alerts (ALERT_DIGEST)
    certificate_failed   a custom domain's certificate could not be issued or renewed (an alert of type certificate)
    certificate_expiring a custom domain's certificate expires soon and was not renewed yet
    plugin_notice        a notice for the super admins about a plugin (licence ending, switched off, ...)
    test                 the test e-mail of Settings, E-mail and `sudoo.sh test-email`

Block placeholders (the code box, the details of a request, the list of alerts) are drawn by the functions at the
end of this file, from values the panel made; they are HTML-escaped like everything else.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

from markupsafe import Markup

from .textformat import BLOCK, LINK, Block, Placeholder


@dataclass(frozen=True, slots=True)
class Message:
    key: str
    name: str
    description: str
    recipients: str
    kicker: str  # the small line above the title; may use placeholders (not editable)
    preheader: str  # the line mail programs show after the subject; may use placeholders (not editable)
    subject: str
    title: str
    body: str
    placeholders: tuple[Placeholder, ...]
    required: tuple[str, ...] = ()  # at least one of these must stay in the text (the code of a code e-mail)
    spec: dict[str, Placeholder] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "spec", {p.name: p for p in (*self.placeholders, *COMMON)})

    def samples(self) -> dict[str, Any]:
        """Values for the preview and the test e-mail."""
        return {name: placeholder.sample for name, placeholder in self.spec.items()}


# ---------------------------------------------------------------- blocks


def _rows_table(rows: list[tuple[str, str]], heading: str = "") -> Markup:
    cells = "".join(
        f'<tr><td style="padding:2px 14px; color:#8F8F8F; width:96px; vertical-align:top;">{html.escape(label)}</td>'
        f'<td style="padding:2px 14px 2px 0; color:#212529;">{html.escape(value)}</td></tr>'
        for label, value in rows
        if value
    )
    head = (
        f'<tr><td colspan="2" style="padding:0 14px 4px 14px; font-weight:bold; color:#454748;">'
        f"{html.escape(heading)}</td></tr>"
        if heading
        else ""
    )
    return Markup(
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 16px 0; '
        'font-size:12px; color:#6C757D; background-color:#F8F9FA; border-radius:6px;">'
        f'<tr><td colspan="2" style="padding:8px 0 0 0;"></td></tr>{head}{cells}'
        '<tr><td colspan="2" style="padding:0 0 8px 0;"></td></tr></table>'
    )


def details_block(rows: list[tuple[str, str]], heading: str = "") -> Block:
    """Labelled facts in a quiet box: when, from where, which device."""
    shown = [(label, value) for label, value in rows if value]
    if not shown:
        return Block(Markup(""), "")
    text = (f"{heading}\n" if heading else "") + "\n".join(f"{label}: {value}" for label, value in shown)
    return Block(_rows_table(shown, heading), text)


def code_block(code: str, minutes: int) -> Block:
    """The one-time code, large and selectable with one tap (mail programs run no scripts, so no copy button)."""
    box = (
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 16px 0;">'
        '<tr><td align="center" style="padding:8px 0 4px 0;"><div style="display:inline-block; '
        "background-color:#F8F9FA; border:1px solid #E7E9ED; border-radius:8px; padding:14px 28px; "
        "font-family:Menlo, Consolas, 'Courier New', monospace; font-size:32px; font-weight:bold; letter-spacing:10px; "
        f'color:#212529; -webkit-user-select:all; user-select:all; cursor:text;">{html.escape(code)}</div></td></tr>'
        '<tr><td align="center" style="padding:4px 0 0 0; font-size:12px; color:#8F8F8F;">Tap the code to select it, '
        f"then copy &middot; valid for {int(minutes)} minutes &middot; works once</td></tr></table>"
    )
    return Block(Markup(box), f"    {code}\n\nValid for {int(minutes)} minutes, works once.")


def lines_block(lines: list[str]) -> Block:
    """Technical lines (an alert's details) in a monospace box."""
    if not lines:
        return Block(Markup(""), "")
    rows = "".join(
        f'<tr><td style="padding:{"10px" if i == 0 else "2px"} 14px {"10px" if i == len(lines) - 1 else "2px"} 14px; '
        "color:#212529; font-family:Menlo, Consolas, 'Courier New', monospace; font-size:12px;\">"
        f"{html.escape(str(line))}</td></tr>"
        for i, line in enumerate(lines)
    )
    box = (
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 16px 0; '
        f'font-size:13px; background-color:#F8F9FA; border-radius:6px;">{rows}</table>'
    )
    return Block(Markup(box), "\n".join(f"  - {line}" for line in lines))


def alerts_block(items: list[dict[str, Any]]) -> Block:
    """The alerts of a daily summary: title (a link to it), how often, and the text."""
    rows, text = [], []
    for item in items:
        title = ("Resolved: " if item.get("severity") == "recovered" else "") + str(item.get("title", ""))
        count = int(item.get("count") or 1)
        link = str(item.get("link") or "")
        safe_link = link if link.startswith(("https://", "http://")) else ""
        shown = (
            f'<a href="{html.escape(safe_link, quote=True)}" style="color:#212529;">{html.escape(title)}</a>'
            if safe_link
            else html.escape(title)
        )
        times = f' <span style="color:#8F8F8F;">({count} times)</span>' if count > 1 else ""
        rows.append(
            '<tr><td style="padding:8px 0; border-top:1px solid #E7E9ED; color:#212529;">'
            f'<b>{shown}</b>{times}<br><span style="color:#495057;">{html.escape(str(item.get("text", "")))}</span>'
            "</td></tr>"
        )
        text.append(
            f"- {title}{f' ({count} times)' if count > 1 else ''}\n  {item.get('text', '')}"
            + (f"\n  {safe_link}" if safe_link else "")
        )
    box = (
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 16px 0; '
        f'font-size:13px;">{"".join(rows)}</table>'
    )
    return Block(Markup(box), "\n\n".join(text))


# ---------------------------------------------------------------- placeholders and samples

COMMON = (Placeholder("panel_link", "The address of the panel", "https://panel.example.com", LINK),)

_SAMPLE_REQUEST = details_block(
    [("When", "Tuesday 18 November 2026, 09:41 UTC"), ("From", "203.0.113.9"), ("Device", "Firefox on macOS")],
    "Requested",
)
_SAMPLE_ALERTS = [
    {
        "title": "Instance shop-main is down",
        "text": "It did not answer for 5 minutes.",
        "count": 3,
        "link": "https://panel.example.com/i/shop-main",
        "severity": "problem",
    },
    {
        "title": "Backup of crm-demo failed",
        "text": "The disk is full.",
        "count": 1,
        "link": "https://panel.example.com/i/crm-demo/activity",
        "severity": "problem",
    },
]


def _code_placeholders(purpose: str) -> tuple[Placeholder, ...]:
    return (
        Placeholder("name", "The account's user name", "sam"),
        Placeholder("code", f"The 6-digit code that {purpose}", "482913"),
        Placeholder("minutes", "How many minutes the code is valid", 10),
        Placeholder("code_box", "The code, large and easy to copy", code_block("482913", 10), BLOCK),
        Placeholder("time", "When it was asked for", "Tuesday 18 November 2026, 09:41 UTC"),
        Placeholder("ip", "The address it was asked from", "203.0.113.9"),
        Placeholder("device", "Browser and system, from the browser's description", "Firefox on macOS"),
        Placeholder("request", "When, from where and from which device, in a box", _SAMPLE_REQUEST, BLOCK),
    )


MESSAGES: dict[str, Message] = {
    message.key: message
    for message in (
        Message(
            key="sign_in_code",
            name="Sign-in code",
            description="The code that finishes signing in, for accounts that use e-mail codes.",
            recipients="The person signing in",
            kicker="Sign in",
            preheader="{{ code }} - valid for {{ minutes }} minutes",
            subject="{{ code }} is your Sudoo.sh sign-in code",
            title="Your sign-in code",
            body=(
                "Hello {{ name }},\n\n"
                "Type this code in Sudoo.sh to finish signing in.\n\n"
                "{{ code_box }}\n\n"
                "[Open Sudoo.sh]({{ panel_link }})\n\n"
                "{{ request }}\n\n"
                "> Did not try to sign in? Someone knows your password. Do not share this code, and change your "
                "password in Sudoo.sh."
            ),
            placeholders=_code_placeholders("finishes signing in"),
            required=("code", "code_box"),
        ),
        Message(
            key="email_confirmation",
            name="E-mail address confirmation",
            description="The code that confirms an address when someone sets up e-mail codes.",
            recipients="The address being set up",
            kicker="Confirm your address",
            preheader="{{ code }} - valid for {{ minutes }} minutes",
            subject="{{ code }} confirms your e-mail address for Sudoo.sh",
            title="Your confirmation code",
            body=(
                "Hello {{ name }},\n\n"
                "Type this code in Sudoo.sh to use this address for sign-in codes.\n\n"
                "{{ code_box }}\n\n"
                "[Open Sudoo.sh]({{ panel_link }})\n\n"
                "{{ request }}\n\n"
                "> Did not ask for this? Someone signed in to your account: change your password in Sudoo.sh."
            ),
            placeholders=_code_placeholders("confirms the address"),
            required=("code", "code_box"),
        ),
        Message(
            key="sign_in_notice",
            name="New sign-in",
            description="Sent after every sign-in, so people notice one that was not theirs.",
            recipients="The account that signed in (Sign-in e-mail settings, and each person's own choice)",
            kicker="Security",
            preheader="{{ device }} from {{ ip }}, {{ time }}",
            subject="New sign-in to your Sudoo.sh account",
            title="New sign-in",
            body=(
                "Hello {{ name }},\n\n"
                "Your account just signed in to Sudoo.sh.\n\n"
                "{{ sign_in }}\n\n"
                "If this was you, there is nothing to do.\n\n"
                "> Not you? Change your password now: it signs out every other session. You can also sign out "
                "everywhere from your account page.\n\n"
                "[Open your account]({{ account_link }})\n\n"
                "{{ opt_out }}"
            ),
            placeholders=(
                Placeholder("name", "The account's user name", "sam"),
                Placeholder("time", "When it signed in", "Tuesday 18 November 2026, 09:41 UTC"),
                Placeholder("ip", "The address it signed in from", "203.0.113.9"),
                Placeholder("device", "Browser and system, from the browser's description", "Firefox on macOS"),
                Placeholder(
                    "method",
                    "How: password, authenticator app, e-mail code or recovery code",
                    "Password and authenticator app",
                ),
                Placeholder(
                    "sign_in",
                    "When, from where, which device and how, in a box",
                    details_block(
                        [
                            ("When", "Tuesday 18 November 2026, 09:41 UTC"),
                            ("From", "203.0.113.9"),
                            ("Device", "Firefox on macOS"),
                            ("How", "Password and authenticator app"),
                        ]
                    ),
                    BLOCK,
                ),
                Placeholder(
                    "account_link",
                    "The account page, to change the password or sign out everywhere",
                    "https://panel.example.com/account",
                    LINK,
                ),
                Placeholder(
                    "opt_out",
                    "How to turn these e-mails off; empty when people may not",
                    "You receive this e-mail at each sign-in. You can turn it off on your account page.",
                ),
            ),
        ),
        Message(
            key="alert",
            name="Alert",
            description="One alert as it happens: a failed job, an instance down, a full disk... and when it is over.",
            recipients="Alert recipients (Settings, Alerts)",
            kicker="{{ kind }}",
            preheader="{{ alert_text }}",
            subject="Sudoo.sh {{ kind }}: {{ alert_title }}",
            title="{{ alert_title }}",
            body=(
                "Hello,\n\n"
                "{{ alert_text }}\n\n"
                "{{ hint }}\n\n"
                "{{ details }}\n\n"
                "Seen {{ repeats }} times.\n\n"
                "[Open Sudoo.sh]({{ link }})\n\n"
                "You receive this because alerts are sent to you (Settings, Alerts)."
            ),
            placeholders=(
                Placeholder("kind", "Alert, Resolved or Notice", "Alert"),
                Placeholder("alert_title", "What happened, in a few words", "Instance shop-main is down"),
                Placeholder(
                    "alert_text", "What happened, in a sentence", "Instance shop-main did not answer for 5 " "minutes."
                ),
                Placeholder("hint", "What to do about it, when the panel knows", "Restart it from its page."),
                Placeholder(
                    "details",
                    "Technical details, in a monospace box",
                    lines_block(["Last answer: 09:36 UTC", "Container: odoo-shop-main"]),
                    BLOCK,
                ),
                Placeholder("repeats", "How many times it was seen; empty when once", "3"),
                Placeholder("link", "The page about it", "https://panel.example.com/i/shop-main", LINK),
            ),
        ),
        Message(
            key="alert_digest",
            name="Daily summary of alerts",
            description="With one e-mail a day (Settings, Alerts), every alert not e-mailed one by one.",
            recipients="Alert recipients (Settings, Alerts)",
            kicker="Daily summary",
            preheader="{{ count }} in the last day",
            subject="Sudoo.sh: {{ count }} in the last day",
            title="{{ count }}",
            body=(
                "Hello,\n\n"
                "What needed attention since the last summary:\n\n"
                "{{ alerts }}\n\n"
                "[Open Sudoo.sh]({{ link }})"
            ),
            placeholders=(
                Placeholder("count", 'How many alerts, as words: "3 alerts"', "2 alerts"),
                Placeholder(
                    "alerts", "The alerts, each with its title, link and text", alerts_block(_SAMPLE_ALERTS), BLOCK
                ),
                Placeholder("link", "The panel", "https://panel.example.com", LINK),
            ),
            required=("alerts",),
        ),
        Message(
            key="certificate_failed",
            name="Certificate not issued",
            description="A custom domain's certificate could not be issued or renewed (an alert).",
            recipients="Alert recipients (Settings, Alerts)",
            kicker="Custom domains",
            preheader="{{ reason }}",
            subject="Sudoo.sh alert: no certificate for {{ domain }}",
            title="No certificate for {{ domain }}",
            body=(
                "Hello,\n\n"
                "The certificate of **{{ domain }}** (instance {{ instance }}) could not be issued or renewed:\n\n"
                "{{ reason }}\n\n"
                "The panel tries again on its own, waiting longer each time. Check the domain's DNS record and that "
                "ports 80 and 443 of the server are open, then choose Check now on the instance's Domains tab.\n\n"
                "[Open the domains]({{ link }})\n\n"
                "You receive this because alerts are sent to you (Settings, Alerts)."
            ),
            placeholders=(
                Placeholder("domain", "The custom domain", "erp.client.com"),
                Placeholder("instance", "The instance it belongs to", "shop-main"),
                Placeholder(
                    "reason",
                    "Why, in a sentence",
                    "The ACME authority could not reach erp.client.com over http: connection refused.",
                ),
                Placeholder(
                    "link", "The instance's Domains tab", "https://panel.example.com/i/shop-main/domains", LINK
                ),
            ),
            required=("domain",),
        ),
        Message(
            key="certificate_expiring",
            name="Certificate expires soon",
            description="A custom domain's certificate expires soon and could not be renewed yet (an alert).",
            recipients="Alert recipients (Settings, Alerts)",
            kicker="Custom domains",
            preheader="It expires on {{ expires }}",
            subject="Sudoo.sh alert: the certificate of {{ domain }} expires on {{ expires }}",
            title="Certificate of {{ domain }} expires soon",
            body=(
                "Hello,\n\n"
                "The certificate of **{{ domain }}** (instance {{ instance }}) expires on **{{ expires }}** and was "
                "not renewed yet:\n\n"
                "{{ reason }}\n\n"
                "After that date browsers warn visitors and refuse to open the instance through this domain. The "
                "panel keeps trying; fix what the reason says, then choose Check now on the instance's Domains tab.\n\n"
                "[Open the domains]({{ link }})\n\n"
                "You receive this because alerts are sent to you (Settings, Alerts)."
            ),
            placeholders=(
                Placeholder("domain", "The custom domain", "erp.client.com"),
                Placeholder("instance", "The instance it belongs to", "shop-main"),
                Placeholder("expires", "The day the certificate expires", "02 December 2026"),
                Placeholder(
                    "reason", "Why it was not renewed", "It was not renewed yet: erp.client.com has no DNS record yet."
                ),
                Placeholder(
                    "link", "The instance's Domains tab", "https://panel.example.com/i/shop-main/domains", LINK
                ),
            ),
            required=("domain",),
        ),
        Message(
            key="plugin_notice",
            name="Plugin notice",
            description="A plugin's licence ends soon or ended, or a plugin was switched off.",
            recipients="Every super admin with an e-mail address",
            kicker="Plugins",
            preheader="{{ notice_text }}",
            subject="{{ notice_title }}",
            title="{{ notice_title }}",
            body="Hello,\n\n{{ notice_text }}\n\n[Open Plugins]({{ plugins_link }})",
            placeholders=(
                Placeholder("notice_title", "What it is about, in a few words", "Backup to S3 ends soon"),
                Placeholder(
                    "notice_text",
                    "What happens and what to do",
                    "The licence of Backup to S3 ends on 25 "
                    "November 2026. Renew it on app.sudoo.sh to keep it working.",
                ),
                Placeholder("plugins_link", "The Plugins page", "https://panel.example.com/plugins", LINK),
            ),
            required=("notice_text",),
        ),
        Message(
            key="test",
            name="Test e-mail",
            description="Proves the mail settings work (Settings, E-mail, Send a test; sudoo.sh test-email).",
            recipients="The address typed by whoever sends the test",
            kicker="E-mail settings",
            preheader="Your mail settings work.",
            subject="Sudoo.sh e-mail works",
            title="It works",
            body=(
                "Hello,\n\n"
                "This is a test message from your Sudoo.sh panel. If you are reading it, e-mail works: sign-in codes "
                "and other messages will arrive like this one.\n\n"
                "Sent through **{{ server }}**.\n\n"
                "[Open Sudoo.sh]({{ panel_link }})"
            ),
            placeholders=(
                Placeholder("server", "The mail server and its encryption", "smtp.example.com:587 " "(starttls)"),
            ),
        ),
    )
}
