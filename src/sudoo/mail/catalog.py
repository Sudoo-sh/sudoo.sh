"""Every e-mail the panel sends, with its built-in text and the placeholders it may use.

The E-mail templates page (web/v1/mail_templates.py) lists these and lets a super admin change the subject, the
title and the text of each one; the changes are kept in the panel's storage (overrides.py) and the built-in texts
below stay as they are, so "Reset to default" only forgets the change. Texts are in the plain format of textformat.py.

    sign_in_code         the 6-digit code that finishes a sign-in (second factor "email")
    email_sign_in_code   the 6-digit code that signs in without a password (sign-in method "email_code")
    email_confirmation   the code that confirms an address for e-mail codes
    sign_in_notice       "new sign-in to your account", after each sign-in (SIGN_IN_EMAIL)
    invitation           an invitation to the panel, with a link to choose a password (invitations.py)
    alert                one alert: a failed job, an instance down, a full disk, ... or that it is over
    alert_digest         the daily summary of alerts (ALERT_DIGEST)
    certificate_failed   a custom domain's certificate could not be issued or renewed (an alert of type certificate)
    certificate_expiring a custom domain's certificate expires soon and was not renewed yet
    offsite_failed       a backup could not be copied off-site, or an off-site copy failed its check (an alert)
    offsite_missing      a production instance has had no successful off-site copy for too long (an alert)
    plugin_notice        a notice for the super admins about a plugin (licence ending, switched off, ...)
    push_failed          a deployment triggered by someone's push failed: to the person who pushed
                         (projects/push_failed.py); push_failed_ar is the same in Arabic
    access_granted       project access given, changed or removed: to the person (projects/notices.py); also
    access_changed       access_*_ar in Arabic
    access_removed
    access_request       temporary access asked: to the project's admins; access_request_decided: the answer
    access_review        who has access to a project, every ACCESS_REVIEW_DAYS, to its admins
    client_access_request    someone of the panel's owner asks a client to open a closed project or branch
                         (projects/clients.py); client_access_request_ar in Arabic
    client_action_confirm    the link and code that confirm a client's major change in the portal
                         (projects/client_confirm.py); client_action_confirm_ar in Arabic
    client_emergency_access  a super admin forced access to a client's closed project or branch, at once;
                         client_emergency_access_ar in Arabic
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
        f'<td dir="auto" style="padding:2px 14px 2px 0; color:#212529;">{html.escape(value)}</td></tr>'
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
        "direction:ltr; font-family:Menlo, Consolas, 'Courier New', monospace; font-size:32px; font-weight:bold; letter-spacing:10px; "
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
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" dir="ltr" '
        'style="margin:0 0 16px 0; direction:ltr; text-align:left; font-size:13px; background-color:#F8F9FA; '
        f'border-radius:6px;">{rows}</table>'
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


_CLIENT_PLACEHOLDERS = (
    Placeholder("name", "The client's name", "Lina Haddad"),
    Placeholder("person", "Who asked, or who forced access", "Omar Saleh"),
    Placeholder("project", "The project", "Nile Logistics"),
    Placeholder("where", "The branch, or the project as a whole", "main"),
    Placeholder("reason", "Why, in their own words", "The invoice report fails since this morning's update."),
    Placeholder("link", "The client portal's page of the project", "https://portal.example.com/p/nile-logistics", LINK),
)
_REQUEST_PLACEHOLDERS = (*_CLIENT_PLACEHOLDERS, Placeholder("hours", "How many hours they ask for", 4))
_EMERGENCY_PLACEHOLDERS = (*_CLIENT_PLACEHOLDERS, Placeholder("minutes", "How many minutes the access lasts", 60))

_CONFIRM_PLACEHOLDERS = (
    Placeholder("name", "The client's name", "Lina Haddad"),
    Placeholder("project", "The project", "Nile Logistics"),
    Placeholder("action", "The change asked for", "Close the branch main"),
    Placeholder("code", "The 6-digit code (works once)", "482913"),
    Placeholder("minutes", "How many minutes the link and the code work", 15),
    Placeholder(
        "link",
        "The confirmation link (works once, in the same browser)",
        "https://portal.example.com/confirm#sample",
        LINK,
    ),
)

_PUSH_PLACEHOLDERS = (
    Placeholder("name", "The name of who pushed", "Huda"),
    Placeholder("project", "The project", "Acme ERP"),
    Placeholder("branch", "The branch pushed to", "feature-invoices"),
    Placeholder("commit", "The commit: short SHA and message", "3f2a91c Add the invoice report"),
    Placeholder("step", "The step that failed", "Downloading the code from GitHub"),
    Placeholder(
        "reason",
        "What failed, in plain words",
        "GitHub says this repository does not exist, or it is private and has no deploy key yet.",
    ),
    Placeholder(
        "facts",
        "Project, branch, commit and step, in a box",
        details_block(
            [
                ("Project", "Acme ERP"),
                ("Branch", "feature-invoices"),
                ("Commit", "3f2a91c Add the invoice report"),
                ("Step", "Downloading the code from GitHub"),
            ]
        ),
        BLOCK,
    ),
    Placeholder(
        "details",
        "Only the lines that explain the failure (never the whole log), in a monospace box",
        lines_block(
            [
                "ERROR: Repository not found.",
                "fatal: Could not read from remote repository.",
                "fatal: clone of 'git@github.com:acme/payments.git' into submodule path 'payments' failed",
            ]
        ),
        BLOCK,
    ),
    Placeholder(
        "what_to_do",
        "What to do, for this kind of failure",
        "The panel cannot read the repository acme/payments. Give the panel's GitHub App access to it, or add a "
        "read-only deploy key for it. Then retry the update from the branch page.",
    ),
    Placeholder(
        "link",
        "The branch's page in the panel",
        "https://panel.example.com/projects/acme/branches/feature-invoices/history",
        LINK,
    ),
)


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


_ACCESS_RIGHTS = details_block(
    [("Production", "See it, its history and status"), ("Staging", "See and test in Odoo"),
     ("Development", "Everything: code, logs, shell, backups")],
    "What you can do",
)  # fmt: skip
_ACCESS_PLACEHOLDERS = (
    Placeholder("name", "The person's first name, or their user name", "Huda"),
    Placeholder("project", "The project", "Acme Retail"),
    Placeholder("role", "The project role (Viewer, Tester, Developer, Maintainer, Admin)", "Developer"),
    Placeholder("before", "The role before the change", "Tester"),
    Placeholder("by", "Who or what made the change: a person, GitHub, a team", "Sara Nassar"),
    Placeholder("rights", "What the role allows in each stage, in a box", _ACCESS_RIGHTS, BLOCK),
    Placeholder("how", "How the person signs in (GitHub, a code by e-mail, a password)", "Continue with GitHub"),
    Placeholder("link", "The project's page", "https://panel.example.com/projects/acme-retail", LINK),
)
_ELEVATION_PLACEHOLDERS = (
    Placeholder("name", "The person the e-mail is for", "Sara"),
    Placeholder("who", "The person who asked", "Karim Saleh"),
    Placeholder("project", "The project", "Acme Retail"),
    Placeholder("role", "The role asked for", "Maintainer"),
    Placeholder("stage", "The stage asked for", "production"),
    Placeholder("hours", "For how many hours", 2),
    Placeholder("reason", "Why, in the person's words", "Read the production logs of the failed import"),
    Placeholder("decision", "approved or refused (decided e-mail only)", "approved"),
    Placeholder("until", "When the access ends (decided e-mail only)", "Tuesday 18 November 2026, 11:41 UTC"),
    Placeholder("decided_by", "Who decided (decided e-mail only)", "Sara Nassar"),
    Placeholder("link", "The project's Access tab", "https://panel.example.com/projects/acme-retail/access", LINK),
)
_REVIEW_PLACEHOLDERS = (
    Placeholder("name", "The admin the review is for", "Sara"),
    Placeholder("project", "The project", "Acme Retail"),
    Placeholder(
        "people",
        "Who has access, their role and where it comes from",
        lines_block(["Karim Saleh: Developer (GitHub: write)", "Lena Brandt: Maintainer (grant)"]),
        BLOCK,
    ),
    Placeholder("count", "How many people have access", 2),
    Placeholder("link", "The project's Access tab", "https://panel.example.com/projects/acme-retail/access", LINK),
)


def _access(key: str, name: str, description: str, recipients: str, kicker: str, preheader: str, subject: str,
            title: str, body: str, placeholders: tuple[Placeholder, ...]) -> Message:  # fmt: skip
    return Message(key=key, name=name, description=description, recipients=recipients, kicker=kicker,
                   preheader=preheader, subject=subject, title=title, body=body, placeholders=placeholders)  # fmt: skip


ACCESS_MESSAGES = (
    _access(
        "access_granted",
        "Project access given",
        "Someone can now work in a project, and as what.",
        "The person given the access (ACCESS_EMAILS)",
        "{{ project }}",
        "{{ role }} in {{ project }}",
        "You can now work in {{ project }}",
        "Welcome to {{ project }}",
        "Hello {{ name }},\n\nYou now have the role **{{ role }}** in **{{ project }}** ({{ by }}).\n\n"
        "{{ rights }}\n\n[Open {{ project }}]({{ link }})\n\nSign in with: {{ how }}.",
        _ACCESS_PLACEHOLDERS,
    ),
    _access(
        "access_granted_ar",
        "Project access given (Arabic)",
        "The same e-mail in Arabic.",
        "The person given the access (ACCESS_EMAILS)",
        "{{ project }}",
        "{{ role }} في {{ project }}",
        "يمكنك الآن العمل في {{ project }}",
        "أهلًا بك في {{ project }}",
        "مرحبًا {{ name }}،\n\nأصبح لك الدور **{{ role }}** في **{{ project }}** ({{ by }}).\n\n"
        "{{ rights }}\n\n[افتح {{ project }}]({{ link }})\n\nتسجّل الدخول بـ: {{ how }}.",
        _ACCESS_PLACEHOLDERS,
    ),
    _access(
        "access_changed",
        "Project role changed",
        "Someone's role in a project changed.",
        "The person whose role changed (ACCESS_EMAILS)",
        "{{ project }}",
        "{{ before }} -> {{ role }}",
        "Your role in {{ project }} is now {{ role }}",
        "Your role changed",
        "Hello {{ name }},\n\nYour role in **{{ project }}** changed from {{ before }} to **{{ role }}** "
        "({{ by }}).\n\n{{ rights }}\n\n[Open {{ project }}]({{ link }})",
        _ACCESS_PLACEHOLDERS,
    ),
    _access(
        "access_changed_ar",
        "Project role changed (Arabic)",
        "The same e-mail in Arabic.",
        "The person whose role changed (ACCESS_EMAILS)",
        "{{ project }}",
        "{{ before }} -> {{ role }}",
        "دورك في {{ project }} أصبح {{ role }}",
        "تغيّر دورك",
        "مرحبًا {{ name }}،\n\nتغيّر دورك في **{{ project }}** من {{ before }} إلى **{{ role }}** "
        "({{ by }}).\n\n{{ rights }}\n\n[افتح {{ project }}]({{ link }})",
        _ACCESS_PLACEHOLDERS,
    ),
    _access(
        "access_removed",
        "Project access removed",
        "Someone can no longer see a project.",
        "The person whose access ended (ACCESS_EMAILS)",
        "{{ project }}",
        "No more access to {{ project }}",
        "Your access to {{ project }} was removed",
        "Access removed",
        "Hello {{ name }},\n\nYou no longer have access to **{{ project }}** ({{ by }}). You had the role "
        "{{ before }}.\n\n> Not expected? Ask an admin of the project.",
        _ACCESS_PLACEHOLDERS,
    ),
    _access(
        "access_removed_ar",
        "Project access removed (Arabic)",
        "The same e-mail in Arabic.",
        "The person whose access ended (ACCESS_EMAILS)",
        "{{ project }}",
        "لم يعد لك وصول إلى {{ project }}",
        "أُزيل وصولك إلى {{ project }}",
        "أُزيل الوصول",
        "مرحبًا {{ name }}،\n\nلم يعد لك وصول إلى **{{ project }}** ({{ by }}). كان دورك {{ before }}.\n\n"
        "> لم تتوقع ذلك؟ تواصل مع أحد مديري المشروع.",
        _ACCESS_PLACEHOLDERS,
    ),
    _access(
        "access_request",
        "Temporary access asked",
        "Someone asks for a higher role for a few hours.",
        "The admins of the project",
        "{{ project }}",
        "{{ who }}: {{ role }} in {{ stage }} for {{ hours }} h",
        "{{ who }} asks for temporary access to {{ project }}",
        "Temporary access asked",
        "Hello {{ name }},\n\n**{{ who }}** asks for the role **{{ role }}** in **{{ stage }}** of "
        "**{{ project }}** for {{ hours }} hours.\n\n**Why:** {{ reason }}\n\n"
        "[Approve or refuse]({{ link }})\n\nIt ends by itself when the time is over.",
        _ELEVATION_PLACEHOLDERS,
    ),
    _access(
        "access_request_ar",
        "Temporary access asked (Arabic)",
        "The same e-mail in Arabic.",
        "The admins of the project",
        "{{ project }}",
        "{{ who }}: {{ role }} في {{ stage }} لمدة {{ hours }} س",
        "{{ who }} يطلب وصولًا مؤقتًا إلى {{ project }}",
        "طلب وصول مؤقت",
        "مرحبًا {{ name }}،\n\nيطلب **{{ who }}** الدور **{{ role }}** في **{{ stage }}** من "
        "**{{ project }}** لمدة {{ hours }} ساعات.\n\n**السبب:** {{ reason }}\n\n"
        "[وافق أو ارفض]({{ link }})\n\nينتهي تلقائيًا حين تنقضي المدة.",
        _ELEVATION_PLACEHOLDERS,
    ),
    _access(
        "access_request_decided",
        "Temporary access decided",
        "The answer to a request for temporary access.",
        "The person who asked",
        "{{ project }}",
        "{{ decision }}",
        "Your request for {{ project }} was {{ decision }}",
        "Your request was {{ decision }}",
        "Hello {{ name }},\n\n{{ decided_by }} {{ decision }} your request for the role **{{ role }}** in "
        "**{{ stage }}** of **{{ project }}**.\n\nWhen approved, it ends on {{ until }}.\n\n"
        "[Open {{ project }}]({{ link }})",
        _ELEVATION_PLACEHOLDERS,
    ),
    _access(
        "access_request_decided_ar",
        "Temporary access decided (Arabic)",
        "The same e-mail in Arabic.",
        "The person who asked",
        "{{ project }}",
        "{{ decision }}",
        "طلبك في {{ project }}: {{ decision }}",
        "نتيجة طلبك",
        "مرحبًا {{ name }}،\n\nقرار {{ decided_by }} في طلبك للدور **{{ role }}** في **{{ stage }}** من "
        "**{{ project }}**: {{ decision }}.\n\nإن وُوفق عليه فهو ينتهي في {{ until }}.\n\n"
        "[افتح {{ project }}]({{ link }})",
        _ELEVATION_PLACEHOLDERS,
    ),
    _access(
        "access_review",
        "Access review",
        "Who has access to a project, sent to its admins every " "ACCESS_REVIEW_DAYS.",
        "The admins of the project (ACCESS_REVIEW)",
        "{{ project }}",
        "{{ count }} people have access",
        "Access review: {{ project }}",
        "Who has access",
        "Hello {{ name }},\n\nThese {{ count }} people can work in **{{ project }}** now:\n\n{{ people }}\n\n"
        "Remove anyone who no longer needs it.\n\n[Review the access]({{ link }})",
        _REVIEW_PLACEHOLDERS,
    ),
    _access(
        "access_review_ar",
        "Access review (Arabic)",
        "The same e-mail in Arabic.",
        "The admins of the project (ACCESS_REVIEW)",
        "{{ project }}",
        "{{ count }} لهم وصول",
        "مراجعة الوصول: {{ project }}",
        "من له وصول",
        "مرحبًا {{ name }}،\n\nيستطيع هؤلاء ({{ count }}) العمل في **{{ project }}** الآن:\n\n{{ people }}\n\n"
        "أزل من لم يعد بحاجة إليه.\n\n[راجع الوصول]({{ link }})",
        _REVIEW_PLACEHOLDERS,
    ),
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
            key="email_sign_in_code",
            name="Sign-in code (no password)",
            description="The code that signs in without a password, for accounts that sign in with an e-mail code.",
            recipients="The person signing in",
            kicker="Sign in",
            preheader="{{ code }} - valid for {{ minutes }} minutes",
            subject="{{ code }} is your Sudoo.sh sign-in code",
            title="Your sign-in code",
            body=(
                "Hello {{ name }},\n\n"
                "Type this code in Sudoo.sh to sign in. You do not need a password.\n\n"
                "{{ code_box }}\n\n"
                "[Open Sudoo.sh]({{ panel_link }})\n\n"
                "{{ request }}\n\n"
                "> Did not ask for a code? You can ignore this e-mail: nobody signs in without it. Never share it."
            ),
            placeholders=_code_placeholders("signs in"),
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
            key="invitation",
            name="Invitation",
            description="Invites a new person: they open the link, choose their password and sign in.",
            recipients="The person invited (Accounts, Invite)",
            kicker="Invitation",
            preheader="{{ inviter }} invited you to Sudoo.sh as {{ role }}",
            subject="{{ inviter }} invited you to Sudoo.sh",
            title="You are invited to Sudoo.sh",
            body=(
                "Hello {{ name }},\n\n"
                "{{ inviter }} invited you to their Sudoo.sh panel as **{{ role }}**.\n\n"
                "Open the link to choose your password and sign in.\n\n"
                "[Accept the invitation]({{ link }})\n\n"
                "The link works once and until {{ expires }}. If it has expired, ask {{ inviter }} to send it again.\n\n"
                "> Not expecting this? You can ignore this e-mail: nothing happens unless the link is opened."
            ),
            placeholders=(
                Placeholder("name", "The person's first name, or their e-mail address", "Huda"),
                Placeholder("inviter", "Who sent the invitation", "Sara Nassar"),
                Placeholder("role", "The role the person will have", "Developer"),
                Placeholder("expires", "When the link stops working", "Friday 21 November 2026, 09:41 UTC"),
                Placeholder("hours", "How many hours the link works", 72),
                Placeholder(
                    "link",
                    "The link that accepts the invitation (works once)",
                    "https://panel.example.com/invite#sample-link",
                    LINK,
                ),
            ),
            required=("link",),
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
            key="offsite_failed",
            name="Off-site copy failed",
            description="A backup could not be copied off-site, or an off-site copy failed its check (an alert).",
            recipients="Alert recipients (Settings, Alerts)",
            kicker="Off-site copies",
            preheader="{{ reason }}",
            subject="Sudoo.sh alert: off-site copy of {{ instance }} failed",
            title="Off-site copy of {{ instance }} failed",
            body=(
                "Hello,\n\n"
                "An off-site copy of **{{ instance }}** ({{ copy }}) failed:\n\n"
                "{{ reason }}\n\n"
                "The backup on the server is not affected. If the storage refused the copy, check the off-site "
                "settings and choose Test connection (Settings, Backups); if a copy failed its check, it was changed "
                "or removed in the bucket ({{ storage }}): do not restore it, and look at who can write to the "
                "bucket.\n\n"
                "[Open the backups]({{ link }})\n\n"
                "You receive this because alerts are sent to you (Settings, Alerts)."
            ),
            placeholders=(
                Placeholder("instance", "The instance the backup is of", "shop-main"),
                Placeholder("copy", "The copy, as the Backups page names it", "shop-main__20261118-000000__daily"),
                Placeholder(
                    "reason",
                    "What went wrong, in a sentence",
                    "The storage refused PUT shop-main/20261118-000000__daily/manifest.json: the access key may not "
                    "do this in this bucket (AccessDenied).",
                ),
                Placeholder("storage", "The bucket and folder of the copies", "acme-backups/sudoo"),
                Placeholder("link", "The Backups page", "https://panel.example.com/backups?view=offsite", LINK),
            ),
            required=("instance",),
        ),
        Message(
            key="offsite_missing",
            name="No recent off-site copy",
            description="A production instance has had no successful off-site copy for too long (an alert).",
            recipients="Alert recipients (Settings, Alerts)",
            kicker="Off-site copies",
            preheader="Last copy: {{ last_copy }}",
            subject="Sudoo.sh alert: no off-site copy of {{ instance }} for {{ hours }} hours",
            title="No off-site copy of {{ instance }} for {{ hours }} hours",
            body=(
                "Hello,\n\n"
                "Production instance **{{ instance }}** has had no successful off-site copy for {{ hours }} hours "
                "(last copy: {{ last_copy }}).\n\n"
                "If the server were lost now, its newest backups would be lost with it. Check that its daily backup "
                "works and that the storage accepts copies (Settings, Backups, Test connection).\n\n"
                "[Open the backups]({{ link }})\n\n"
                "You receive this because alerts are sent to you (Settings, Alerts)."
            ),
            placeholders=(
                Placeholder("instance", "The production instance", "shop-main"),
                Placeholder("hours", "Hours without a copy that start the alert", "26"),
                Placeholder("last_copy", "When the last successful copy was made, or never", "2026-11-16 00:04"),
                Placeholder("link", "The Backups page", "https://panel.example.com/backups?view=offsite", LINK),
            ),
            required=("instance",),
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
            key="push_failed",
            name="Deployment of a push failed",
            description="A build triggered by a push failed (an automatic update, a rebuild on push): sent to the "
            "person who pushed, once per branch and commit (each project can turn it off).",
            recipients="The person who pushed (GitHub's push, or the commit's author); never a noreply address",
            kicker="{{ project }}",
            preheader="{{ reason }}",
            subject="Your push to {{ branch }} in {{ project }} could not be deployed",
            title="Your push could not be deployed",
            body=(
                "Hello {{ name }},\n\n"
                "Your push to **{{ branch }}** in **{{ project }}** could not be deployed.\n\n"
                "{{ facts }}\n\n"
                "**What failed:** {{ reason }}\n\n"
                "{{ details }}\n\n"
                "**What to do:** {{ what_to_do }}\n\n"
                "[Open the branch]({{ link }})\n\n"
                "You receive this because you pushed this commit. The project's managers can turn these e-mails off."
            ),
            placeholders=_PUSH_PLACEHOLDERS,
            required=("reason",),
        ),
        Message(
            key="push_failed_ar",
            name="Deployment of a push failed (Arabic)",
            description="The same e-mail in Arabic, for people whose panel account (or the panel) uses Arabic.",
            recipients="The person who pushed (GitHub's push, or the commit's author); never a noreply address",
            kicker="{{ project }}",
            preheader="{{ reason }}",
            subject="تعذّر نشر دفعك إلى {{ branch }} في {{ project }}",
            title="تعذّر نشر دفعك",
            body=(
                "مرحبًا {{ name }}،\n\n"
                "تعذّر نشر دفعك إلى **{{ branch }}** في **{{ project }}**.\n\n"
                "{{ facts }}\n\n"
                "**ما الذي فشل:** {{ reason }}\n\n"
                "{{ details }}\n\n"
                "**ما العمل:** {{ what_to_do }}\n\n"
                "[افتح الفرع]({{ link }})\n\n"
                "تصلك هذه الرسالة لأنك دفعت هذا الإيداع. يستطيع مديرو المشروع إيقاف هذه الرسائل."
            ),
            placeholders=_PUSH_PLACEHOLDERS,
            required=("reason",),
        ),
        Message(
            key="client_access_request",
            name="Access request (client portal)",
            description="Someone of your team asks a client to open a project or branch the client closed: sent to "
            "each client of the project, who answers in the client portal.",
            recipients="The clients of the project (client portal)",
            kicker="{{ project }}",
            preheader="{{ person }} asks to open {{ where }} for {{ hours }} hours",
            subject="{{ person }} asks for access to {{ where }} ({{ project }})",
            title="An access request is waiting for you",
            body=(
                "Hello {{ name }},\n\n"
                "**{{ person }}** asks for access to **{{ where }}** in **{{ project }}**, which you closed, for "
                "**{{ hours }} hours**.\n\n"
                "**Why:** {{ reason }}\n\n"
                "Approve it (for these people and this time, or less) or refuse it in the client portal. Nothing "
                "opens until you answer.\n\n"
                "[Answer the request]({{ link }})"
            ),
            placeholders=_REQUEST_PLACEHOLDERS,
            required=("link",),
        ),
        Message(
            key="client_access_request_ar",
            name="Access request (client portal, Arabic)",
            description="The same e-mail in Arabic, for clients whose account (or the panel) uses Arabic.",
            recipients="The clients of the project (client portal)",
            kicker="{{ project }}",
            preheader="يطلب {{ person }} فتح {{ where }} لمدة {{ hours }} ساعات",
            subject="يطلب {{ person }} الوصول إلى {{ where }} ({{ project }})",
            title="طلب وصول بانتظارك",
            body=(
                "مرحبًا {{ name }}،\n\n"
                "يطلب **{{ person }}** الوصول إلى **{{ where }}** في **{{ project }}**، الذي أغلقته، لمدة "
                "**{{ hours }} ساعات**.\n\n"
                "**السبب:** {{ reason }}\n\n"
                "وافق عليه (لهؤلاء الأشخاص ولهذه المدة، أو أقل) أو ارفضه في بوابة العملاء. لا يُفتح شيء حتى "
                "تجيب.\n\n"
                "[أجب عن الطلب]({{ link }})"
            ),
            placeholders=_REQUEST_PLACEHOLDERS,
            required=("link",),
        ),
        Message(
            key="client_emergency_access",
            name="Emergency access (client portal)",
            description="A super admin forced access to a project or branch a client closed (emergency access, "
            "which the client allows): sent at once to each client of the project.",
            recipients="The clients of the project (client portal)",
            kicker="{{ project }}",
            preheader="{{ person }} opened {{ where }} for {{ minutes }} minutes",
            subject="Emergency access to {{ where }} ({{ project }})",
            title="Emergency access was used",
            body=(
                "Hello {{ name }},\n\n"
                "**{{ person }}** used emergency access to **{{ where }}** in **{{ project }}**, which you closed. "
                "It lasts **{{ minutes }} minutes**, then it closes again by itself.\n\n"
                "**Why:** {{ reason }}\n\n"
                "Everything they do is in your activity log. You can end this access now, and turn emergency access "
                "off for this project, in the client portal.\n\n"
                "[Open the project]({{ link }})"
            ),
            placeholders=_EMERGENCY_PLACEHOLDERS,
            required=("reason",),
        ),
        Message(
            key="client_emergency_access_ar",
            name="Emergency access (client portal, Arabic)",
            description="The same e-mail in Arabic, for clients whose account (or the panel) uses Arabic.",
            recipients="The clients of the project (client portal)",
            kicker="{{ project }}",
            preheader="فتح {{ person }} {{ where }} لمدة {{ minutes }} دقيقة",
            subject="وصول طارئ إلى {{ where }} ({{ project }})",
            title="استُخدم الوصول الطارئ",
            body=(
                "مرحبًا {{ name }}،\n\n"
                "استخدم **{{ person }}** الوصول الطارئ إلى **{{ where }}** في **{{ project }}**، الذي أغلقته. "
                "يستمر **{{ minutes }} دقيقة**، ثم يُغلق من تلقاء نفسه.\n\n"
                "**السبب:** {{ reason }}\n\n"
                "كل ما يفعلونه مسجّل في سجل نشاطك. يمكنك إنهاء هذا الوصول الآن، وإيقاف الوصول الطارئ لهذا المشروع، "
                "من بوابة العملاء.\n\n"
                "[افتح المشروع]({{ link }})"
            ),
            placeholders=_EMERGENCY_PLACEHOLDERS,
            required=("reason",),
        ),
        Message(
            key="client_action_confirm",
            name="Confirm a change (client portal)",
            description="A client asked for a major change in the portal (opening or closing, a lock, emergency access, "
            "an approval, the people it lets in): it waits until the client confirms it with this link or code.",
            recipients="The client who asked (client portal)",
            kicker="{{ project }}",
            preheader="Confirm: {{ action }}",
            subject="Confirm in {{ project }}: {{ action }}",
            title="Confirm this change",
            body=(
                "Hello {{ name }},\n\n"
                "You asked for this change in **{{ project }}**:\n\n"
                "**{{ action }}**\n\n"
                "It happens only once you confirm it, in the browser where you asked, within {{ minutes }} minutes. "
                "Open the link, or type the code in the portal.\n\n"
                "[Confirm the change]({{ link }})\n\n"
                "Your code: **{{ code }}**\n\n"
                "> Did not ask for this? Do nothing: nothing changes, and the link stops working by itself."
            ),
            placeholders=_CONFIRM_PLACEHOLDERS,
            required=("code",),
        ),
        Message(
            key="client_action_confirm_ar",
            name="Confirm a change (client portal, Arabic)",
            description="The same e-mail in Arabic, for clients whose account (or the panel) uses Arabic.",
            recipients="The client who asked (client portal)",
            kicker="{{ project }}",
            preheader="أكّد: {{ action }}",
            subject="أكّد في {{ project }}: {{ action }}",
            title="أكّد هذا التغيير",
            body=(
                "مرحبًا {{ name }}،\n\n"
                "طلبت هذا التغيير في **{{ project }}**:\n\n"
                "**{{ action }}**\n\n"
                "لا يحدث إلا بعد أن تؤكّده، في المتصفح الذي طلبته منه، خلال {{ minutes }} دقيقة. "
                "افتح الرابط، أو اكتب الرمز في البوابة.\n\n"
                "[أكّد التغيير]({{ link }})\n\n"
                "رمزك: **{{ code }}**\n\n"
                "> لم تطلب هذا؟ لا تفعل شيئًا: لن يتغيّر شيء، ويتوقف الرابط عن العمل من تلقاء نفسه."
            ),
            placeholders=_CONFIRM_PLACEHOLDERS,
            required=("code",),
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
MESSAGES.update({message.key: message for message in ACCESS_MESSAGES})


# the e-mails of production access rules live in their own module (catalog_access.py)
from .catalog_access import MESSAGES as _ACCESS_MESSAGES  # noqa: E402

MESSAGES.update({message.key: message for message in _ACCESS_MESSAGES})
