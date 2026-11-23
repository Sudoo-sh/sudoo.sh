"""The e-mails of production access rules (instances/access_rules.py), added to catalog.MESSAGES:

access_rules_code       the one-time code that confirms a change of an instance's access rules (by e-mail)
access_rules_changed    an instance's access rules were changed: to the other people who may change them
                        and to the project's clients
..._ar                  the same in Arabic, for accounts that use Arabic
"""

from __future__ import annotations

from .catalog import BLOCK, LINK, Message, Placeholder, code_block, details_block

_REQUEST = details_block(
    [("When", "Tuesday 18 November 2026, 09:41 UTC"), ("From", "203.0.113.9"), ("Device", "Firefox on macOS")],
    "Requested",
)

_CODE = (
    Placeholder("name", "The account's name", "Sam"),
    Placeholder("instance", "The production instance", "acme-main"),
    Placeholder("code", "The 6-digit code that confirms the change", "482913"),
    Placeholder("minutes", "How many minutes the code is valid", 10),
    Placeholder("code_box", "The code, large and easy to copy", code_block("482913", 10), BLOCK),
    Placeholder("request", "When, from where and from which device it was asked for, in a box", _REQUEST, BLOCK),
)

_CHANGED = (
    Placeholder("name", "The name of the person told", "Huda"),
    Placeholder("instance", "The production instance", "acme-main"),
    Placeholder("by", "Who changed the rules", "omar"),
    Placeholder("time", "When", "2026-11-23 09:41 UTC"),
    Placeholder(
        "rules",
        "The new rules, in a few plain sentences (English)",
        "Only visitors from JO, PS may open the back office. Always open: payments, webhooks.",
    ),
    Placeholder("link", "The instance's settings in the panel", "https://panel.example.com/i/acme-main/settings", LINK),
)

MESSAGES = (
    Message(
        key="access_rules_code",
        name="Access rules: confirmation code",
        description="The code that confirms a change of a production instance's access rules (beta).",
        recipients="The person changing the rules",
        kicker="Access rules",
        preheader="{{ code }} - valid for {{ minutes }} minutes",
        subject="{{ code }} confirms the access rules of {{ instance }}",
        title="Confirm the new access rules",
        body=(
            "Hello {{ name }},\n\n"
            "Type this code in Sudoo.sh to confirm the new access rules of **{{ instance }}**.\n\n"
            "{{ code_box }}\n\n"
            "{{ request }}\n\n"
            "> Did not ask for this? Someone with your password is trying to change who can open a production "
            "instance. Do not share this code, and change your password."
        ),
        placeholders=_CODE,
        required=("code", "code_box"),
    ),
    Message(
        key="access_rules_code_ar",
        name="Access rules: confirmation code (Arabic)",
        description="The same e-mail in Arabic, for people whose panel account (or the panel) uses Arabic.",
        recipients="The person changing the rules",
        kicker="قواعد الوصول",
        preheader="{{ code }} - صالح لمدة {{ minutes }} دقيقة",
        subject="{{ code }} يؤكّد قواعد الوصول إلى {{ instance }}",
        title="أكّد قواعد الوصول الجديدة",
        body=(
            "مرحبًا {{ name }}،\n\n"
            "اكتب هذا الرمز في Sudoo.sh لتأكيد قواعد الوصول الجديدة إلى **{{ instance }}**.\n\n"
            "{{ code_box }}\n\n"
            "{{ request }}\n\n"
            "> لم تطلب ذلك؟ هناك من يعرف كلمة مرورك ويحاول تغيير من يمكنه فتح بيئة إنتاج. لا تشارك هذا الرمز، "
            "وغيّر كلمة مرورك."
        ),
        placeholders=_CODE,
        required=("code", "code_box"),
    ),
    Message(
        key="access_rules_changed",
        name="Access rules changed",
        description="A production instance's access rules (beta) were changed.",
        recipients="The other people who may change them (super admins, the project's administrators) and the "
        "project's clients",
        kicker="{{ instance }}",
        preheader="{{ rules }}",
        subject="The access rules of {{ instance }} were changed",
        title="Access rules changed",
        body=(
            "Hello {{ name }},\n\n"
            "**{{ by }}** changed who can open the production instance **{{ instance }}** ({{ time }}).\n\n"
            "{{ rules }}\n\n"
            "[Open the instance's settings]({{ link }})\n\n"
            "You receive this because you may change these rules too. If this change was not expected, check it now."
        ),
        placeholders=_CHANGED,
        required=("instance",),
    ),
    Message(
        key="access_rules_changed_ar",
        name="Access rules changed (Arabic)",
        description="The same e-mail in Arabic, for people whose panel account (or the panel) uses Arabic.",
        recipients="The other people who may change them (super admins, the project's administrators) and the "
        "project's clients",
        kicker="{{ instance }}",
        preheader="{{ rules }}",
        subject="تغيّرت قواعد الوصول إلى {{ instance }}",
        title="تغيّرت قواعد الوصول",
        body=(
            "مرحبًا {{ name }}،\n\n"
            "غيّر **{{ by }}** من يمكنه فتح بيئة الإنتاج **{{ instance }}** ({{ time }}).\n\n"
            "{{ rules }}\n\n"
            "[افتح إعدادات البيئة]({{ link }})\n\n"
            "تصلك هذه الرسالة لأنك تستطيع تغيير هذه القواعد أيضًا. إن لم يكن هذا التغيير متوقعًا، فراجعه الآن."
        ),
        placeholders=_CHANGED,
        required=("instance",),
    ),
)
