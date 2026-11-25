"""The e-mails about the end of an instance's lifetime (instances/expiry.py), added to catalog.MESSAGES:

instance_expiry_warning   an instance expires in a few days (EXPIRY_WARNING_DAYS)
instance_stopped          an instance expired and was stopped; it is deleted after EXPIRY_GRACE_DAYS
..._ar                    the same in Arabic, for accounts that use Arabic
"""

from __future__ import annotations

from .catalog import LINK, Message, Placeholder

_BASE = (
    Placeholder("name", "The person's first name, or their user name", "Huda"),
    Placeholder("instance", "The instance", "acme-dev-3"),
    Placeholder("project", "Its project (empty for an instance without one)", "Acme Retail"),
    Placeholder("expires", "When its lifetime ends", "Tuesday 02 December 2026, 09:41 UTC"),
    Placeholder("link", "The instance in the panel", "https://panel.example.com/i/acme-dev-3", LINK),
)
_WARNING = (*_BASE, Placeholder("days", "Days left, rounded", 3))
_STOPPED = (
    *_BASE,
    Placeholder("delete_on", "When it is deleted unless it is extended", "Tuesday 09 December 2026, 09:41 UTC"),
)
_WHO = "The person who made the instance, and the project's administrators (each may turn it off on their account page)"

MESSAGES = (
    Message(
        key="instance_expiry_warning",
        name="Instance expires soon",
        description="An instance's lifetime ends in a few days (EXPIRY_WARNING_DAYS). Sent once for each expiry date.",
        recipients=_WHO,
        kicker="{{ instance }}",
        preheader="Expires {{ expires }}",
        subject="{{ instance }} expires in {{ days }} days",
        title="This instance expires soon",
        body=(
            "Hello {{ name }},\n\n"
            "The instance **{{ instance }}** expires on {{ expires }}. It is then stopped, and deleted a few days "
            "later, with a backup.\n\n"
            "[Open {{ instance }}]({{ link }}) and choose Extend to keep it longer."
        ),
        placeholders=_WARNING,
        required=("instance",),
    ),
    Message(
        key="instance_expiry_warning_ar",
        name="Instance expires soon (Arabic)",
        description="The same e-mail in Arabic.",
        recipients=_WHO,
        kicker="{{ instance }}",
        preheader="ينتهي في {{ expires }}",
        subject="تنتهي مدة {{ instance }} بعد {{ days }} أيام",
        title="تنتهي مدة هذه البيئة قريبًا",
        body=(
            "مرحبًا {{ name }}،\n\n"
            "تنتهي مدة البيئة **{{ instance }}** في {{ expires }}. بعدها تُوقَف، ثم تُحذف بعد بضعة أيام مع نسخة "
            "احتياطية.\n\n"
            "[افتح {{ instance }}]({{ link }}) واختر تمديد لإبقائها مدة أطول."
        ),
        placeholders=_WARNING,
        required=("instance",),
    ),
    Message(
        key="instance_stopped",
        name="Instance stopped, will be deleted",
        description="An instance's lifetime ended: it was stopped and will be deleted after EXPIRY_GRACE_DAYS.",
        recipients=_WHO,
        kicker="{{ instance }}",
        preheader="Will be deleted on {{ delete_on }}",
        subject="{{ instance }} was stopped and will be deleted on {{ delete_on }}",
        title="This instance was stopped",
        body=(
            "Hello {{ name }},\n\n"
            "The lifetime of **{{ instance }}** ended on {{ expires }}, so it was stopped. Nothing was deleted yet.\n\n"
            "It will be deleted on {{ delete_on }}, with a backup, unless an administrator extends it: that starts "
            "it again.\n\n"
            "[Open {{ instance }}]({{ link }})"
        ),
        placeholders=_STOPPED,
        required=("instance",),
    ),
    Message(
        key="instance_stopped_ar",
        name="Instance stopped, will be deleted (Arabic)",
        description="The same e-mail in Arabic.",
        recipients=_WHO,
        kicker="{{ instance }}",
        preheader="ستُحذف في {{ delete_on }}",
        subject="أُوقفت {{ instance }} وستُحذف في {{ delete_on }}",
        title="أُوقفت هذه البيئة",
        body=(
            "مرحبًا {{ name }}،\n\n"
            "انتهت مدة البيئة **{{ instance }}** في {{ expires }} فأُوقفت. لم يُحذف شيء بعد.\n\n"
            "ستُحذف في {{ delete_on }} مع نسخة احتياطية، إلا إذا مدّدها مدير: عندها تعمل من جديد.\n\n"
            "[افتح {{ instance }}]({{ link }})"
        ),
        placeholders=_STOPPED,
        required=("instance",),
    ),
)
